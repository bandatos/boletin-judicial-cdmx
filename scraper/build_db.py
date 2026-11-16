#!/usr/bin/env python3
"""
build_db.py — Genera boletin.sqlite desde los CSVs del scraper.

Uso:
    python build_db.py --dir data/ --output boletin.sqlite
    python build_db.py --dir data/ --output boletin.sqlite --since 2026-01-01
    python build_db.py --dir data/ --output boletin.sqlite --exclude 2026-07-02
    python build_db.py --dir data/ --output boletin.sqlite --desde-fecha 2026-01-01
"""

import argparse
import csv
import functools
import itertools
import os
from collections import deque
from multiprocessing import Pool
import json
import re
import sqlite3
import subprocess
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import agregados

csv.field_size_limit(10**9)


# Memoria: en el server (7 GB, compartidos con el sitio) la corrida diaria
# baja la caché de SQLite y corre las etapas de a una (ver diario.py).
CACHE_MB = int(os.environ.get("BUILD_CACHE_MB", "2000"))
ETAPAS_A_LA_VEZ = int(os.environ.get("BUILD_ETAPAS_A_LA_VEZ", "0")) or None


def cache_sqlite(conn, fraccion=1.0):
    conn.execute(f"PRAGMA cache_size=-{int(CACHE_MB * 1000 * fraccion)}")


# Núcleos que puede usar el build: 80 % por defecto, para que la máquina
# siga respondiendo (BUILD_CPU, fracción de 0 a 1).
NUCLEOS = max(1, int((os.cpu_count() or 1) * float(os.environ.get("BUILD_CPU", "0.8"))))
# Hilos de SQLite por proceso (build_duck.py reparte NUCLEOS entre SQLite
# y DuckDB).
HILOS = min(8, NUCLEOS)


def hilos_sqlite(conn):
    """Ordenamiento en varios hilos para CREATE INDEX y GROUP BY grandes
    (PRAGMA threads, SQLite >= 3.8.7). Sin esto, indexar 34M filas usa un
    solo núcleo."""
    conn.execute(f"PRAGMA threads={HILOS}")


def create_schema(conn):
    conn.executescript("""
        -- fecha_iso, n_entradas y entrada_desde/hasta: para navegar por
        -- fecha en el frontend. Las entradas de un boletín quedan contiguas
        -- (se insertan boletín por boletín), así que traer "todo lo del día"
        -- es un rango de rowid, sin tener que indexar entradas.boletin_id.
        CREATE TABLE IF NOT EXISTS boletines (
            id            INTEGER PRIMARY KEY,
            fecha         TEXT NOT NULL,
            fecha_iso     TEXT,
            pdf_url       TEXT,
            pages         INTEGER,
            n_entradas    INTEGER NOT NULL DEFAULT 0,
            entrada_desde INTEGER,
            entrada_hasta INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_boletines_fecha
            ON boletines (fecha_iso);

        -- La fecha de publicación no se guarda acá: es 1:1 con boletin_id y
        -- ya está en boletines.fecha. Repetirla por fila (una por aviso)
        -- inflaba el archivo ~40MB sin aportar nada (se llega a ella con
        -- JOIN boletines).
        CREATE TABLE IF NOT EXISTS entradas (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            boletin_id  INTEGER REFERENCES boletines(id),
            juzgado     TEXT,
            sala        TEXT,
            secretaria  TEXT,
            actora      TEXT,
            demandada   TEXT,
            tipo_juicio TEXT,
            tipo_juicio_norm TEXT,
            expediente  TEXT,
            num_acdos   INTEGER,
            anio        INTEGER,
            pagina      INTEGER, -- página del PDF del boletín (1 = primera)
            -- acuerdo, sentencia_definitiva, sentencia_interlocutoria,
            -- sentencia (sin calificar: las de sala) o audiencia
            tipo_actuacion TEXT,
            ponencia    INTEGER, -- sentencias de sala: "Sent. Pon 2"
            fecha_acuerdo TEXT,  -- "ACUERDOS DEL ..." del órgano, ISO
            -- Qué se resolvió en un acuerdo, si el órgano lo publica:
            -- prevencion, negacion o admision (acuerdos.tipo_acuerdo). NULL si el
            -- aviso no trae descripción, o si es sentencia o audiencia.
            tipo_acuerdo TEXT,
            -- Lo que el órgano escribió sobre lo acordado ("Prevención 3
            -- Días", "Admisión Apel. Efecto Devoluctivo"), si lo escribió
            -- (acuerdos.descripcion). La ficha lo muestra en cada aviso.
            descripcion TEXT,
            -- Cuaderno o tomo que el boletín pega al número ("Tomo II",
            -- "Inc", "Principal"). expediente queda solo con el número
            -- (acuerdos.separar_expediente), que es la clave de la ficha.
            cuaderno TEXT
        );


        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS carpetas_fgj (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha_inicio     TEXT,
            fecha_hecho      TEXT,
            delito           TEXT,
            categoria_delito TEXT,
            fiscalia         TEXT,
            alcaldia         TEXT,
            colonia          TEXT,
            lat              REAL,
            lon              REAL
        );

        CREATE INDEX IF NOT EXISTS idx_fgj_alcaldia
            ON carpetas_fgj (alcaldia, fecha_hecho);
    """)


def create_indexes(conn, indices_server=False, web=False):
    # Después de la carga y no en el schema: armar el índice de una vez es
    # mucho más rápido que mantenerlo fila por fila durante 30M inserts.
    if not web:
        # (juzgado, expediente): fichas de expediente y páginas de juzgado
        # del server. El sitio estático no filtra por juzgado y este índice
        # es ~14% de la base que baja el navegador.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_expediente ON entradas (juzgado, expediente)")
    # Búsquedas filtradas solo por año del expediente.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_anio ON entradas (anio)")
    conn.executescript("""

        CREATE INDEX IF NOT EXISTS idx_tipo_juicio_norm
            ON entradas (tipo_juicio_norm);
    """)
    if indices_server:
        # Solo para la base del server (en la del browser suman MB a la
        # descarga y con 2M de filas alcanza un recorrido):
        # búsqueda por número de expediente sin saber el juzgado (el índice
        # de arriba empieza por juzgado). El conteo por año y tipo que antes
        # leía idx_anio_tipo (1.2 GB) sale de la tabla resumen_anio_tipo.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_expediente_num ON entradas (expediente)")
        conn.execute("DROP INDEX IF EXISTS idx_anio_tipo")


def create_fts(conn):
    # detail=none: no guardamos posición ni columna de cada término (solo
    # qué documentos matchean). No usamos snippet()/highlight() ni consultas
    # de columna o de frase, así que el costo es nulo y el índice pesa
    # ~1/4 de lo que pesaba con el detail (posicional) por default.
    conn.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS entradas_fts USING fts5(
            actora,
            demandada,
            tipo_juicio,
            content='entradas',
            content_rowid='id',
            detail=none,
            columnsize=0
        );
    """)


def populate_meta(conn):
    """Guarda el commit de git y la fecha de build para que el frontend
    pueda mostrar qué versión de la DB está corriendo (facilita detectar
    si ya se hizo el deploy más reciente)."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True, cwd=Path(__file__).parent,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        commit = "sin-git"

    built_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    conn.executemany(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        [("commit", commit), ("built_at", built_at)],
    )


def populate_fts(conn):
    conn.execute("""
        INSERT INTO entradas_fts(rowid, actora, demandada, tipo_juicio)
        SELECT id, actora, demandada, tipo_juicio FROM entradas
    """)


MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
         "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12}
RE_FECHA_RAW = re.compile(r"(\d{1,2})-([a-z]{3})[a-z]*\.?-(\d{4})")


def fecha_iso(fecha_raw):
    """'Consulta del Boletin Judicial de fecha  22-sep.-2026' -> '2026-09-22'."""
    m = RE_FECHA_RAW.search(fecha_raw or "")
    if not m or m.group(2) not in MESES:
        return None
    return f"{m.group(3)}-{MESES[m.group(2)]:02d}-{int(m.group(1)):02d}"


RE_URL_PDF = re.compile(r'https://(?:(?!https://)[^"#])+\.pdf')


def url_pdf_directa(url):
    """Índices guardados antes de jul. 2026 tienen la URL envuelta en el
    proxy del portal (".../pdf/boletines/https://gestordocumental..."), que
    da 404: se usa la última URL, la directa al CDN (mismo criterio que
    fetch_index en poc.py)."""
    urls = RE_URL_PDF.findall(url or "")
    return urls[-1] if urls else url


def load_index(conn, index_path):
    with open(index_path) as f:
        boletines = json.load(f)
    conn.executemany(
        "INSERT OR IGNORE INTO boletines (id, fecha, fecha_iso, pdf_url) VALUES (?, ?, ?, ?)",
        [(b["id"], b["fecha_raw"], fecha_iso(b["fecha_raw"]), url_pdf_directa(b["pdf_url"]))
         for b in boletines],
    )
    # URLs corregidas por poc.py cuando el link del portal da 404 (ver
    # docs/boletines-faltantes.md): pisan a la del portal aunque otro run
    # con el link roto se haya cargado antes.
    conn.executemany(
        "UPDATE boletines SET pdf_url = ? WHERE id = ?",
        [(b["pdf_url"], b["id"]) for b in boletines if b.get("pdf_url_portal")],
    )


def populate_boletin_ranges(conn, log):
    conn.execute("""
        CREATE TEMP TABLE agg AS
        SELECT boletin_id, COUNT(*) AS n, MIN(id) AS desde, MAX(id) AS hasta
        FROM entradas GROUP BY boletin_id
    """)
    no_contiguos = conn.execute(
        "SELECT COUNT(*) FROM agg WHERE hasta - desde + 1 != n"
    ).fetchone()[0]
    if no_contiguos:
        # El frontend filtra además por boletin_id, así que no mezcla
        # boletines; solo lee algunas filas de más.
        log(f"  Aviso: {no_contiguos} boletines con entradas no contiguas")
    conn.execute("""
        UPDATE boletines
        SET n_entradas = agg.n, entrada_desde = agg.desde, entrada_hasta = agg.hasta
        FROM agg WHERE agg.boletin_id = boletines.id
    """)
    conn.execute("DROP TABLE agg")


FIELDS = [
    "boletin_id", "juzgado", "sala", "secretaria",
    "actora", "demandada", "tipo_juicio", "expediente", "num_acdos", "pagina",
    "tipo_actuacion", "ponencia",
]
COLS_CARGA = FIELDS + ["tipo_juicio_norm", "anio", "fecha_acuerdo", "tipo_acuerdo", "descripcion", "cuaderno"]
COLS_ENTERAS = {"boletin_id", "num_acdos", "pagina", "ponencia", "anio"}


# ── Cálculo por aviso, guardado por corrida (entradas.precalc.sqlite) ────
# Lo caro de la carga es el análisis en Python de cada aviso (nombres,
# número, tipo de acuerdo, descripción, empresas, personas): ~1 ms por
# aviso, casi 30 min para 35M. Da siempre lo mismo para los avisos viejos,
# así que la primera vez que se procesa una corrida se guarda en
# <corrida>/entradas.precalc.sqlite, ya en el formato de la base, con la
# versión del código. Los builds siguientes copian de ahí por rangos de
# filas (en C, sin Python por aviso) mientras la versión coincida.
PRECALC_COLS = COLS_CARGA + ["det(fila,tipo,rol,nucleo,nombre)", "partes(fila,otras_actora,otras_demandada)", "tipado", "fila", "parquet ordenado"]
PRECALC_ARCHIVO = "entradas.precalc.sqlite"


# El cálculo por aviso (nombres, número, tipo de acuerdo, descripción,
# empresas, personas) lo hace el binario precalc del scraper en Rust
# (scraper/boletin_rs). Compilarlo: ver scraper/boletin_rs/README.md.
DIR_RS = Path(__file__).resolve().parent / "boletin_rs"
PRECALC_RS = Path(os.environ.get("PRECALC_RS", DIR_RS / "target" / "release" / "precalc"))


def precalc_version():
    """Sello del código que produce el precálculo (el código Rust): si
    cambia, el precálculo se rehace."""
    import hashlib
    h = hashlib.sha256()
    for f in sorted((DIR_RS / "src").rglob("*.rs")) + [DIR_RS / "Cargo.lock"]:
        h.update(f.read_bytes())
    h.update(",".join(PRECALC_COLS).encode())
    return "rs" + h.hexdigest()[:14]


def _precalc_vigente(precalc_path, csv_path):
    if not precalc_path.exists() or precalc_path.stat().st_mtime < csv_path.stat().st_mtime:
        return False
    try:
        c = sqlite3.connect(f"file:{precalc_path}?mode=ro", uri=True)
        version = c.execute("SELECT valor FROM meta WHERE clave = 'version'").fetchone()
        c.close()
    except sqlite3.Error:
        return False
    return bool(version) and version[0] == precalc_version()


def _crear_precalc(p):
    # fila es el rowid (INTEGER PRIMARY KEY), con nombre propio porque la
    # extensión sqlite de DuckDB no escribe "rowid". Los tipos, los de la
    # tabla entradas: sin tipo, los números quedaban como texto ("4050" !=
    # 4050 al comparar boletines ya cargados).
    tipadas = ", ".join(f"{c} {'INTEGER' if c in COLS_ENTERAS else 'TEXT'}" for c in COLS_CARGA)
    p.executescript(f"""
        CREATE TABLE filas (fila INTEGER PRIMARY KEY, {tipadas});
        CREATE TABLE det (
            fila INTEGER, tipo INTEGER, rol INTEGER, nucleo TEXT, nombre TEXT,
            PRIMARY KEY (fila, tipo, rol, nucleo, nombre)
        ) WITHOUT ROWID;
        CREATE TABLE partes (fila INTEGER PRIMARY KEY, otras_actora INTEGER, otras_demandada INTEGER);
        CREATE TABLE meta (clave TEXT PRIMARY KEY, valor TEXT);
        CREATE TABLE tramos (boletin_id INTEGER, desde INTEGER, hasta INTEGER);
    """)


# Columnas de los CSV que escribe el binario precalc, con su tipo.
_ENTEROS_PRECALC = COLS_ENTERAS | {"fila", "tipo", "rol", "otras_actora", "otras_demandada", "desde", "hasta"}
CSV_PRECALC = {t: [(c, "BIGINT" if c in _ENTEROS_PRECALC else "VARCHAR") for c in cols] for t, cols in (
    ("filas", ["fila"] + COLS_CARGA),
    ("det", ["fila", "tipo", "rol", "nucleo", "nombre"]),
    ("partes", ["fila", "otras_actora", "otras_demandada"]),
    ("tramos", ["boletin_id", "desde", "hasta"]),
)}


def leer_csv_precalc(carpeta, tabla):
    """read_csv de DuckDB para un CSV del binario precalc."""
    tipos = ", ".join(f"'{c}': '{t}'" for c, t in CSV_PRECALC[tabla])
    return (f"read_csv('{Path(carpeta) / (tabla + '.csv')}', header = true, nullstr = '\\N', "
            f"allow_quoted_nulls = false, columns = {{{tipos}}})")


def correr_precalc_rs(csv_path, salida, hilos=None):
    subprocess.run([str(PRECALC_RS), str(csv_path), str(salida), "--hilos", str(hilos or NUCLEOS)], check=True)


def _generar_precalc_rust(csv_path, precalc_path, tmp_dir=None, log=None, hilos=None):
    """El binario de Rust escribe filas/det/partes/tramos en CSV; de ahí se
    arma el precálculo en SQLite y, si está DuckDB, también en Parquet."""
    salida = Path(tempfile.mkdtemp(prefix="precalc_rs_", dir=tmp_dir))
    try:
        correr_precalc_rs(csv_path, salida, hilos)
        tmp_path = precalc_path.with_name(precalc_path.name + ".tmp")
        tmp_path.unlink(missing_ok=True)
        p = sqlite3.connect(tmp_path)
        p.execute("PRAGMA journal_mode=OFF")
        p.execute("PRAGMA synchronous=OFF")
        cache_sqlite(p)
        _crear_precalc(p)
        p.commit()
        try:
            import duckdb
        except ImportError:
            duckdb = None
        if duckdb:
            # DuckDB lee los CSV en paralelo y los escribe en SQLite.
            import build_duck
            p.close()
            db = build_duck.conectar_simple(NUCLEOS)
            db.execute(f"ATTACH '{tmp_path}' AS s (TYPE sqlite)")
            for tabla, cols in CSV_PRECALC.items():
                orden = "desde" if tabla == "tramos" else cols[0][0]
                db.execute(f"INSERT INTO s.{tabla} SELECT * FROM {leer_csv_precalc(salida, tabla)} ORDER BY {orden}")
            db.execute("DETACH s")
            db.close()
            p = sqlite3.connect(tmp_path)
        else:
            def filas_csv(nombre):
                with open(salida / nombre, newline="") as f:
                    r = csv.reader(f)
                    next(r)
                    for row in r:
                        yield [None if v == "\\N" else v for v in row]
            p.executemany(f"INSERT INTO filas (rowid, {', '.join(COLS_CARGA)}) VALUES "
                          f"({', '.join(['?'] * (len(COLS_CARGA) + 1))})", filas_csv("filas.csv"))
            p.executemany("INSERT INTO det VALUES (?, ?, ?, ?, ?)", filas_csv("det.csv"))
            p.executemany("INSERT INTO partes VALUES (?, ?, ?)", filas_csv("partes.csv"))
            p.executemany("INSERT INTO tramos VALUES (?, ?, ?)", filas_csv("tramos.csv"))
        p.execute("INSERT INTO meta VALUES ('version', ?)", (precalc_version(),))
        p.commit()
        p.close()
        os.replace(tmp_path, precalc_path)
    finally:
        shutil.rmtree(salida, ignore_errors=True)


def generar_precalc(csv_path, precalc_path, workers=None, tmp_dir=None, log=None):
    """Calcula todos los avisos de la corrida con el binario de Rust y los
    junta en precalc_path: filas (fila = número de fila), det (detecciones
    de empresas y personas), partes, y tramos (rango de filas de cada
    boletín)."""
    if not PRECALC_RS.exists():
        sys.exit(f"Falta {PRECALC_RS}: compilar scraper/boletin_rs (ver su README.md).")
    return _generar_precalc_rust(csv_path, precalc_path, tmp_dir, log, workers)


def load_entradas(conn, csv_path, loaded, log, en_rango=None, workers=None, tmp_dir=None,
                  con_empresas=False, con_personas=False):
    """Carga las entradas de un run salteando los boletines que ya cargó un
    run anterior (`loaded`, se actualiza). Los runs se pisan entre sí (el
    histórico repite boletines de los runs de 2026) y sin esto cada boletín
    repetido aparecía dos veces. `en_rango`: si se pasa, solo se cargan
    esos boletines (filtro --desde-fecha).

    Copia del precálculo de la corrida (lo genera si falta o es de otra
    versión del código) por rangos de filas, en el orden del CSV: así las
    entradas de cada boletín quedan contiguas (ver populate_boletin_ranges)."""
    # Si el run no aporta ningún boletín nuevo (todos ya cargados de un run
    # más nuevo, o fuera del rango de fechas) ni se lee. Los boletines del
    # run son los .txt que tiene en pdfs/.
    del_run = set()
    for txt in (csv_path.parent / "pdfs").glob("boletin_*.txt*"):
        numero = txt.name[len("boletin_"):].split(".")[0]
        if numero.isdigit():
            del_run.add(int(numero))
    if del_run:
        utiles = del_run - loaded
        if en_rango is not None:
            utiles &= en_rango
        if not utiles:
            log("    sin boletines nuevos, se saltea")
            return 0

    precalc_path = csv_path.parent / PRECALC_ARCHIVO
    if _precalc_vigente(precalc_path, csv_path):
        log("    precálculo guardado")
    else:
        log("    calculando (y guardando el precálculo)")
        generar_precalc(csv_path, precalc_path, workers, tmp_dir, log)

    conn.execute("ATTACH DATABASE ? AS pre", (f"file:{precalc_path}?mode=ro",))
    # Rangos de filas a copiar: tramos de boletines útiles, juntando los
    # consecutivos.
    rangos, nuevos, salteadas = [], set(), 0
    for boletin_id, desde, hasta in conn.execute("SELECT CAST(boletin_id AS INTEGER), desde, hasta FROM pre.tramos ORDER BY desde"):
        if boletin_id in loaded or (en_rango is not None and boletin_id not in en_rango):
            if boletin_id in loaded:
                salteadas += hasta - desde + 1
            continue
        nuevos.add(boletin_id)
        if rangos and rangos[-1][1] + 1 == desde:
            rangos[-1][1] = hasta
        else:
            rangos.append([desde, hasta])
    tipos = [t for t, si in ((agregados.TIPO_EMPRESA, con_empresas), (agregados.TIPO_PERSONA, con_personas)) if si]
    cols = ", ".join(COLS_CARGA)
    base = conn.execute("SELECT COALESCE(MAX(id), 0) FROM entradas").fetchone()[0]
    total = 0
    for desde, hasta in rangos:
        # ids nuevos: base+1 ... en el orden de las filas.
        corr = base - desde + 1
        conn.execute(f"INSERT INTO entradas (id, {cols}) SELECT rowid + ?, {cols} FROM pre.filas "
                     "WHERE rowid BETWEEN ? AND ? ORDER BY rowid", (corr, desde, hasta))
        if tipos:
            conn.execute(f"INSERT INTO partes_carga SELECT fila + ?, tipo, rol, nucleo, nombre FROM pre.det "
                         f"WHERE fila BETWEEN ? AND ? AND tipo IN ({', '.join(map(str, tipos))})",
                         (corr, desde, hasta))
        if con_empresas:
            conn.execute("INSERT INTO entradas_partes SELECT fila + ?, otras_actora, otras_demandada "
                         "FROM pre.partes WHERE fila BETWEEN ? AND ?", (corr, desde, hasta))
        base += hasta - desde + 1
        total += hasta - desde + 1
    conn.commit()
    conn.execute("DETACH DATABASE pre")
    loaded |= nuevos
    if salteadas:
        log(f"    {salteadas} entradas salteadas (boletines ya cargados de un run más nuevo)")
    return total


# Durante la carga, en el archivo .staging (se borra al terminar).
SQL_PARTES_CARGA = """CREATE TABLE IF NOT EXISTS {esq}.partes_carga (
    entrada_id INTEGER, tipo INTEGER, rol INTEGER, nucleo TEXT, nombre TEXT)"""


# Incremental: lo nuevo, directo en la base.
SQL_PARTES_SUMAR = """
    INSERT OR IGNORE INTO partes_nombres (tipo, nucleo, nombre)
        SELECT DISTINCT tipo, nucleo, nombre FROM st.partes_carga;
    INSERT OR IGNORE INTO partes_avisos
        SELECT n.id, c.entrada_id, c.rol
        FROM st.partes_carga c
        JOIN partes_nombres n ON n.tipo = c.tipo AND n.nucleo = c.nucleo AND n.nombre = c.nombre;
"""


def build_sivep(conn, log, sivep_path):
    """Enlaces a la versión pública de la sentencia en el SIVEP del PJCDMX
    (scraper/sivep_enlace.py, ver private-docs/sivep-versiones-publicas.md). Solo
    los emparejamientos únicos. El enlace se guarda en sivep.sqlite con
    claves estables (órgano, expediente, fecha del boletín) porque los id
    de entradas cambian en cada build: acá se resuelven contra esta base.
    La URL del PDF vence a los 10 minutos, así que se guarda lo necesario
    para que el server pida una nueva (materia, juzgado, ejercicio,
    trimestre y document_path)."""
    log("Enlaces a versiones públicas del SIVEP...")
    sv = sqlite3.connect(f"file:{sivep_path}?mode=ro", uri=True)
    filas = sv.execute("""
        SELECT l.organo, l.expediente, l.fecha_boletin, s.document_path, s.materia_cod, s.materia,
               s.juzgado_cod, s.ejercicio, s.trimestre, s.tipo_juicio, s.fecha_emision
        FROM enlaces l JOIN sentencias s USING (document_path)
        WHERE l.resultado = 'unico'""").fetchall()
    sv.close()
    conn.executescript("""
        DROP TABLE IF EXISTS sentencias_publicas;
        CREATE TABLE sentencias_publicas (
            entrada_id    INTEGER,  -- aviso de sentencia en esta base
            document_path TEXT,     -- id estable en el gestor documental del PJCDMX
            materia_cod   TEXT,     -- parámetros de la consulta al SIVEP
            materia       TEXT,
            juzgado_cod   TEXT,
            ejercicio     INTEGER,
            trimestre     TEXT,
            tipo_juicio   TEXT,
            fecha_emision TEXT
        );
    """)
    q = """
        SELECT e.id FROM entradas e JOIN boletines b ON b.id = e.boletin_id
        WHERE (e.expediente = ? OR e.expediente BETWEEN ? || ' ' AND ? || ' ~')
          AND COALESCE(e.sala, e.juzgado) = ? AND b.fecha_iso = ?
          AND e.tipo_actuacion IN ('sentencia_definitiva', 'sentencia')
        ORDER BY e.id LIMIT 1"""
    n = perdidos = 0
    for organo, exp, fecha, *resto in filas:
        r = conn.execute(q, (exp, exp, exp, organo, fecha)).fetchone()
        if not r:
            perdidos += 1
            continue
        conn.execute("INSERT INTO sentencias_publicas VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (r[0], *resto))
        n += 1
    conn.execute("CREATE INDEX idx_sentencias_publicas ON sentencias_publicas (entrada_id)")
    conn.commit()
    log(f"  {n} enlaces ({perdidos} sin aviso en esta base)")


def build_vocabulario(output, log):
    """Tablas vocabulario / vocabulario_tri para sugerir correcciones en el
    buscador ("santnder" -> "santander"). Palabras de los nombres de
    personas y empresas (núcleos, sin acentos), con la suma de avisos de los
    nombres que las usan como frecuencia. vocabulario_tri es un índice de
    trigramas (FTS5 tokenize=trigram) para encontrar palabras parecidas."""
    conn = sqlite3.connect(output)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    hilos_sqlite(conn)
    frecuencia = {}
    for tabla in ("empresas", "personas"):
        existe = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (tabla,)).fetchone()
        if not existe:
            continue
        for nucleo, avisos in conn.execute(f"SELECT nucleo, avisos FROM {tabla}"):
            for palabra in set(nucleo.split()):
                if len(palabra) >= 3 and not palabra.isdigit():
                    frecuencia[palabra] = frecuencia.get(palabra, 0) + avisos
    conn.executescript("""
        DROP TABLE IF EXISTS vocabulario_tri;
        DROP TABLE IF EXISTS vocabulario;
        CREATE TABLE vocabulario (id INTEGER PRIMARY KEY, palabra TEXT UNIQUE NOT NULL, n INTEGER NOT NULL);
    """)
    conn.executemany("INSERT INTO vocabulario (palabra, n) VALUES (?, ?)", sorted(frecuencia.items()))
    conn.executescript("""
        CREATE VIRTUAL TABLE vocabulario_tri USING fts5(
            palabra, content='vocabulario', content_rowid='id', tokenize='trigram', detail=none
        );
        INSERT INTO vocabulario_tri(rowid, palabra) SELECT id, palabra FROM vocabulario;
    """)
    conn.commit()
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()
    log(f"  {len(frecuencia):,} palabras en el vocabulario")


def find_runs(data_dir, since=None, exclude=()):
    """Devuelve lista de (run_dir) ordenados por fecha."""
    runs = sorted(data_dir.glob("*/entradas.csv"))
    if since:
        runs = [r for r in runs if r.parent.name >= since]
    return [r for r in runs if r.parent.name not in exclude]


# ── Etapas en paralelo ────────────────────────────────────────────────────
def armar_fts(base, archivo):
    """Índice de búsqueda por texto en su propio archivo, leyendo la base de
    solo lectura (build_duck.py lo corre en otro proceso mientras DuckDB
    escribe los agregados). Después se copia con _juntar."""
    Path(archivo).unlink(missing_ok=True)
    conn = sqlite3.connect(archivo)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    hilos_sqlite(conn)
    cache_sqlite(conn, 0.5)
    conn.execute("ATTACH DATABASE ? AS b", (f"file:{base}?mode=ro",))
    # Sin contenido propio: en la base se define con content='entradas' y se
    # le copian estas tablas internas (_data, _idx).
    conn.executescript("""
        CREATE VIRTUAL TABLE entradas_fts USING fts5(
            actora, demandada, tipo_juicio, content='', detail=none, columnsize=0
        );
        INSERT INTO entradas_fts(rowid, actora, demandada, tipo_juicio)
            SELECT id, actora, demandada, tipo_juicio FROM b.entradas;
        INSERT INTO entradas_fts(entradas_fts) VALUES('optimize');
    """)
    conn.commit()
    conn.close()


# Tablas internas de FTS5: se copian con su tabla virtual, no por separado.
RE_SOMBRA_FTS = re.compile(r"_(?:data|idx|docsize|config|content)$")


def _juntar(conn, etapa, archivo, log):
    """Copia a la base lo que armó una etapa en paralelo."""
    t0 = time.monotonic()
    conn.execute("ATTACH DATABASE ? AS w", (archivo,))
    objetos = conn.execute(
        "SELECT type, name, tbl_name, sql FROM w.sqlite_master WHERE sql IS NOT NULL ORDER BY rowid").fetchall()
    virtuales = {n for t, n, _, sql in objetos if t == "table" and sql.upper().startswith("CREATE VIRTUAL")}
    sombras = {n for t, n, _, _ in objetos for v in virtuales if t == "table" and n.startswith(v + "_")}
    for tipo, nombre, _, sql in objetos:
        if tipo == "table" and nombre not in virtuales and nombre not in sombras:
            conn.execute(f"DROP TABLE IF EXISTS main.{nombre}")
            conn.execute(sql)
            conn.execute(f"INSERT INTO main.{nombre} SELECT * FROM w.{nombre}")
    for tipo, nombre, tabla, sql in objetos:
        if tipo == "index" and tabla not in virtuales and tabla not in sombras:
            conn.execute(sql)
    if etapa == "fts":
        create_fts(conn)
        for sufijo in ("data", "idx"):
            conn.execute(f"DELETE FROM main.entradas_fts_{sufijo}")
            conn.execute(f"INSERT INTO main.entradas_fts_{sufijo} SELECT * FROM w.entradas_fts_{sufijo}")
    else:
        # empresas_fts / personas_fts apuntan a su tabla de contenido: se
        # definen en la base y se rearman (son chicas).
        for tipo, nombre, _, sql in objetos:
            if nombre in virtuales:
                conn.execute(f"DROP TABLE IF EXISTS main.{nombre}")
                conn.execute(sql)
                conn.execute(f"INSERT INTO main.{nombre}({nombre}) VALUES('rebuild')")
    conn.commit()
    conn.execute("DETACH DATABASE w")
    log(f"  {etapa} copiada a la base: {time.monotonic() - t0:.0f} s")


# ── Build incremental ─────────────────────────────────────────────────────
def build_incremental(args, output, runs, log):
    """Suma a una base ya armada solo los boletines que no tiene.

    Las entradas nuevas se insertan con los índices ya creados y se suman a
    la búsqueda por texto, y sus empresas y personas detectadas a
    partes_nombres / partes_avisos. Empresas, personas y estadísticas se
    rearman enteras desde la base, así el resultado es el mismo que el de un
    build completo."""
    if not output.exists():
        sys.exit(f"--incremental necesita {output}: correr antes un build completo.")
    log(f"Sumando a {output}...")
    conn = sqlite3.connect(output)
    conn.execute("PRAGMA synchronous=OFF")
    hilos_sqlite(conn)
    cache_sqlite(conn)
    conn.execute("PRAGMA temp_store=FILE")
    con_partes = args.empresas or args.personas
    if con_partes and not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'partes_avisos'").fetchone():
        sys.exit(f"{output} no tiene partes_avisos (es de una versión anterior): correr un build completo.")
    max_id_antes = conn.execute("SELECT COALESCE(MAX(id), 0) FROM entradas").fetchone()[0]
    loaded = {r[0] for r in conn.execute("SELECT id FROM boletines WHERE n_entradas > 0")}
    runs = list(reversed(runs))
    for csv_path in runs:
        index_path = csv_path.parent / "index.json"
        if index_path.exists():
            load_index(conn, index_path)
    # Las detecciones de lo nuevo pasan por un archivo aparte, como en el
    # build completo, y después se suman a la base.
    staging = output.with_name(output.name + ".staging.sqlite")
    staging.unlink(missing_ok=True)
    conn.execute("ATTACH DATABASE ? AS st", (str(staging),))
    if con_partes:
        conn.execute(SQL_PARTES_CARGA.format(esq="st"))
    total = 0
    for csv_path in runs:
        log(f"  {csv_path.parent.name}...")
        n = load_entradas(conn, csv_path, loaded, log, None, workers=args.workers, tmp_dir=str(output.parent),
                          con_empresas=args.empresas, con_personas=args.personas)
        total += n
        log(f"    {n} entradas nuevas")
    conn.commit()
    if not total:
        conn.execute("DETACH DATABASE st")
        staging.unlink(missing_ok=True)
        log("No hay boletines nuevos.")
        conn.close()
        return

    log("Rangos, búsqueda por texto y partes de lo nuevo...")
    conn.execute("""
        UPDATE boletines SET n_entradas = agg.n, entrada_desde = agg.desde, entrada_hasta = agg.hasta
        FROM (SELECT boletin_id, COUNT(*) AS n, MIN(id) AS desde, MAX(id) AS hasta
              FROM entradas WHERE id > ? GROUP BY boletin_id) AS agg
        WHERE agg.boletin_id = boletines.id
    """, (max_id_antes,))
    conn.execute("""
        INSERT INTO entradas_fts(rowid, actora, demandada, tipo_juicio)
        SELECT id, actora, demandada, tipo_juicio FROM entradas WHERE id > ?
    """, (max_id_antes,))
    if con_partes:
        conn.executescript(SQL_PARTES_SUMAR)
    populate_meta(conn)
    conn.commit()

    # Agregados: solo lo que tocan los avisos nuevos, sobre la base misma.
    pasos = [("resumen", lambda: agregados.resumen_incremental(conn, max_id_antes))]
    if args.estadisticas:
        pasos.append(("estadísticas", lambda: agregados.estadisticas_incremental(conn, log, max_id_antes)))
    if args.empresas:
        pasos.append(("empresas", lambda: agregados.empresas_incremental(conn, log)))
    if args.personas:
        pasos.append(("personas", lambda: agregados.personas_incremental(conn, log)))
    for nombre, paso in pasos:
        t0 = time.monotonic()
        paso()
        conn.commit()
        log(f"  {nombre}: {time.monotonic() - t0:.0f} s")
    conn.execute("DETACH DATABASE st")
    staging.unlink(missing_ok=True)
    if args.sivep:
        build_sivep(conn, log, args.sivep)
    conn.commit()
    conn.close()
    if args.empresas or args.personas:
        build_vocabulario(output, log)
    log(f"Total: {total} entradas nuevas.")
    # Sin compactar: las tablas rearmadas dejan páginas libres que el
    # próximo incremental reusa. Compactar 16 GB tarda ~7 min.
    conn = sqlite3.connect(output)
    libres, paginas = conn.execute("PRAGMA freelist_count").fetchone()[0], conn.execute("PRAGMA page_count").fetchone()[0]
    conn.close()
    log(f"Páginas libres: {100 * libres / max(paginas, 1):.0f} % (se compacta con VACUUM si hace falta).")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", help="Directorio raíz de datos (data/)")
    parser.add_argument("--output", default="boletin.sqlite", help="Archivo SQLite de salida")
    parser.add_argument("--since", help="Solo incluir runs desde esta fecha YYYY-MM-DD")
    parser.add_argument("--exclude", action="append", default=[],
                        help="Run a omitir (nombre del directorio). Repetible")
    parser.add_argument("--indices-server", action="store_true",
                        help="Índices extra para la base del server: por número de "
                             "expediente y por (año, tipo de juicio)")
    parser.add_argument("--empresas", action="store_true",
                        help="Tablas de empresas que son parte (sección /empresas del server)")
    parser.add_argument("--personas", action="store_true",
                        help="Tabla de personas físicas que son parte (sección /personas del server, noindex)")
    parser.add_argument("--workers", type=int,
                        help="Procesos para la carga (por defecto, todos los núcleos menos uno)")
    parser.add_argument("--estadisticas", action="store_true",
                        help="Tablas de desempeño por órgano y de tipos de juicio (/salas, /juzgados, /tipos)")
    parser.add_argument("--sivep",
                        help="sivep.sqlite con la tabla enlaces (scraper/sivep_enlace.py): "
                             "agrega los enlaces a versiones públicas de sentencias")
    parser.add_argument("--incremental", action="store_true",
                        help="Sumar a una base ya armada (--output) solo los boletines que no tiene")
    parser.add_argument("--solo-sivep", action="store_true",
                        help="Rearmar solo los enlaces al SIVEP de una base ya armada (--output, requiere --sivep)")
    parser.add_argument("--solo-vocabulario", action="store_true",
                        help="Rearmar solo el vocabulario de sugerencias de una base ya armada (--output)")
    parser.add_argument("--web", action="store_true",
                        help="Base para el sitio estático: sin el índice (juzgado, expediente)")
    parser.add_argument("--hasta-fecha",
                        help="Solo boletines publicados hasta esta fecha YYYY-MM-DD (inclusive)")
    parser.add_argument("--desde-fecha",
                        help="Solo boletines publicados desde esta fecha YYYY-MM-DD, "
                             "de cualquier run (a diferencia de --since, que filtra "
                             "por nombre del run)")
    args = parser.parse_args()
    # Prioridad baja: el build cede la CPU a lo que se esté usando.
    try:
        os.nice(10)
    except OSError:
        pass

    output = Path(args.output)
    # Cada línea con el tiempo transcurrido, para ver qué etapa pesa.
    t_inicio = time.monotonic()
    log = lambda msg: print(f"[{time.monotonic() - t_inicio:6.0f} s] {msg}", file=sys.stderr)

    if args.solo_sivep:
        if not args.sivep:
            parser.error("--solo-sivep requiere --sivep")
        conn = sqlite3.connect(output)
        build_sivep(conn, log, args.sivep)
        conn.close()
        log(f"Listo: {output}")
        return
    if args.solo_vocabulario:
        build_vocabulario(output, log)
        log(f"Listo: {output}")
        return
    if not args.dir:
        parser.error("--dir es obligatorio (salvo con --solo-vocabulario / "
                     "--solo-sivep)")
    data_dir = Path(args.dir)

    runs = find_runs(data_dir, args.since, args.exclude)
    if not runs:
        log("No se encontraron datos.")
        sys.exit(1)

    log(f"Encontrados {len(runs)} runs en {data_dir}")
    if args.incremental:
        build_incremental(args, output, runs, log)
        size_mb = output.stat().st_size / 1e6
        log(f"\nListo: {output} ({size_mb:.1f} MB)")
        return
    log(f"Generando {output}...")
    try:
        import build_duck
    except ImportError:
        sys.exit("El build completo usa DuckDB: correr con scraper/.venv/bin/python (ver scraper/boletin_rs/README.md).")
    build_duck.build_completo(args, output, runs, log, sys.modules[__name__])
    log(f"\nListo: {output} ({output.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
