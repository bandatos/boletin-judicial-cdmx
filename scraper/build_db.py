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

from normalize_tipo import normalize
import empresas as emp
from acuerdos import (descripcion, descripcion_de_sobrante, quitar_marca_publicacion, separar_expediente,
                      separar_parte, tipo_acuerdo)
import personas as pers
import agregados

# normalize() es una cascada de regex y era ~80% del tiempo de carga, pero
# hay pocos cientos de tipo_juicio distintos en millones de filas.
normalize_tipo = functools.lru_cache(maxsize=None)(normalize)

csv.field_size_limit(10**9)

RE_ANIO = re.compile(r"/(\d{4})\b")



# Memoria: en el server (7 GB, compartidos con el sitio) la corrida diaria
# baja la caché de SQLite y corre las etapas de a una (ver diario.py).
CACHE_MB = int(os.environ.get("BUILD_CACHE_MB", "2000"))
ETAPAS_A_LA_VEZ = int(os.environ.get("BUILD_ETAPAS_A_LA_VEZ", "0")) or None


def cache_sqlite(conn, fraccion=1.0):
    conn.execute(f"PRAGMA cache_size=-{int(CACHE_MB * 1000 * fraccion)}")


# Núcleos que puede usar el build: 80 % por defecto, para que la máquina
# siga respondiendo (BUILD_CPU, fracción de 0 a 1).
NUCLEOS = max(1, int((os.cpu_count() or 1) * float(os.environ.get("BUILD_CPU", "0.8"))))
# Hilos de SQLite por proceso. correr_etapas lo baja cuando hay varias
# etapas a la vez, para no pasarse de NUCLEOS entre todas.
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
I_EXPEDIENTE = FIELDS.index("expediente")
COLS_ENTERAS = {"boletin_id", "num_acdos", "pagina", "ponencia", "anio"}

MESES_LARGOS = {"ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4, "MAYO": 5, "JUNIO": 6, "JULIO": 7,
                "AGOSTO": 8, "SEPTIEMBRE": 9, "SETIEMBRE": 9, "OCTUBRE": 10, "NOVIEMBRE": 11, "DICIEMBRE": 12}
RE_FECHA_ACUERDO = re.compile(r"(\d{1,2})\s+DE\s+([A-ZÁÉÍÓÚ]+)\s+DEL?\s+(\d{4})")


def fecha_acuerdo_iso(texto):
    """'21 DE SEPTIEMBRE DEL 2026' -> '2026-09-21'."""
    m = RE_FECHA_ACUERDO.search((texto or "").upper())
    if not m or m.group(2) not in MESES_LARGOS:
        return None
    return f"{m.group(3)}-{MESES_LARGOS[m.group(2)]:02d}-{int(m.group(1)):02d}"
BLOQUE = 16 * 1024 * 1024  # bytes de CSV por tarea (con 64 MB una corrida chica daba 4 tareas y sobraban núcleos)

# Estado de cada proceso de la carga paralela (lo fija _init_worker).
_W = {}


def _init_worker(csv_path, col, tmp_dir):
    _W.update(path=csv_path, col=col, tmp_dir=tmp_dir)


def _tipo_acuerdo_fila(row, i_tacdo, i_raw, i_actuacion, i_dem):
    """tipo_acuerdo de la fila: el del CSV si lo trae, o calculado desde el
    texto del aviso. Solo para acuerdos (las sentencias tienen su tipo)."""
    if i_tacdo is not None:
        return row[i_tacdo] or None
    if i_raw is None or (i_actuacion is not None and row[i_actuacion] not in ("", "acuerdo")):
        return None
    return tipo_acuerdo(row[i_raw], row[i_dem] or None)


def _descripcion_fila(row, i_desc, i_raw, i_actuacion, i_dem):
    """descripcion de la fila: la del CSV si la trae, o calculada desde el
    texto del aviso. Solo para acuerdos."""
    if i_desc is not None:
        return row[i_desc] or None
    if i_raw is None or (i_actuacion is not None and row[i_actuacion] not in ("", "acuerdo")):
        return None
    return descripcion(row[i_raw], row[i_dem] or None)


# ── Cálculo por aviso, guardado por corrida (entradas.precalc.sqlite) ────
# Lo caro de la carga es el análisis en Python de cada aviso (nombres,
# número, tipo de acuerdo, descripción, empresas, personas): ~1 ms por
# aviso, casi 30 min para 35M. Da siempre lo mismo para los avisos viejos,
# así que la primera vez que se procesa una corrida se guarda en
# <corrida>/entradas.precalc.sqlite, ya en el formato de la base, con la
# versión del código. Los builds siguientes copian de ahí por rangos de
# filas (en C, sin Python por aviso) mientras la versión coincida.
PRECALC_COLS = COLS_CARGA + ["det(fila,tipo,rol,nucleo,nombre)", "partes(fila,otras_actora,otras_demandada)", "tipado", "fila"]
PRECALC_ARCHIVO = "entradas.precalc.sqlite"


# Port a Rust del cálculo por aviso (scraper/boletin_rs, binario precalc):
# mismo resultado que derivar() y unas 20 veces más rápido. Si está
# compilado se usa; SIN_RUST=1 fuerza Python.
DIR_RS = Path(__file__).resolve().parent / "boletin_rs"
PRECALC_RS = Path(os.environ.get("PRECALC_RS", DIR_RS / "target" / "release" / "precalc"))


def usar_rust():
    return PRECALC_RS.exists() and not os.environ.get("SIN_RUST")


def precalc_version():
    """Sello del código que produce el precálculo: si cambia, el precálculo
    se rehace. Con Rust, el código Rust; si no, los módulos de Python y la
    función derivar."""
    import hashlib
    import inspect
    h = hashlib.sha256()
    if usar_rust():
        for f in sorted((DIR_RS / "src").rglob("*.rs")) + [DIR_RS / "Cargo.lock"]:
            h.update(f.read_bytes())
        h.update(",".join(PRECALC_COLS).encode())
        return "rs" + h.hexdigest()[:14]
    base = Path(__file__).resolve().parent
    for nombre in ("acuerdos.py", "empresas.py", "personas.py", "normalize_tipo.py"):
        h.update((base / nombre).read_bytes())
    h.update(inspect.getsource(limpiar_fila).encode())
    h.update(inspect.getsource(derivar).encode())
    h.update(",".join(PRECALC_COLS).encode())
    return h.hexdigest()[:16]


def limpiar_fila(row, col):
    """Limpia actora y demandada en el lugar. Nombres que se tragaron el
    tipo de juicio o la descripción ("Guzmán García Lorena. Aud. 272-B
    ..."): se corta el nombre y lo que sobra (se devuelve) sirve de
    descripción si no hay otra (acuerdos.separar_parte)."""
    i_act, i_dem = col["actora"], col["demandada"]
    sobrante = None
    row[i_act] = quitar_marca_publicacion(row[i_act])
    row[i_dem] = quitar_marca_publicacion(row[i_dem])
    if row[i_dem]:
        row[i_dem], sobrante = separar_parte(row[i_dem])
    elif row[i_act]:
        row[i_act], sobrante = separar_parte(row[i_act])
    return sobrante


def detectar_python(campo):
    """(personas, (empresas, otras)) de un campo actora/demandada."""
    return pers.personas(campo), emp.partes(campo)


_SIN_LIMPIAR = object()


def derivar(row, col, detectar=detectar_python, sobrante=_SIN_LIMPIAR):
    """Todo lo que la carga calcula de un aviso del CSV del scraper.
    Devuelve (valores en el orden de COLS_CARGA, empresas [(rol, núcleo,
    nombre)], partes (otras_actora, otras_demandada) o None, personas
    [(1|2, núcleo, nombre)], fecha ISO del boletín). `detectar`: Python o
    el port a Rust, que da lo mismo. Si la fila ya pasó por
    limpiar_fila, se pasa su `sobrante`."""
    i_act, i_dem, i_exp, i_tipo = col["actora"], col["demandada"], col["expediente"], col["tipo_juicio"]
    i_facdo = col.get("fecha_acuerdo")
    i_tacdo, i_raw, i_act_t = col.get("tipo_acuerdo"), col.get("raw_text"), col.get("tipo_actuacion")
    i_desc, i_cuad, i_fecha_raw = col.get("descripcion"), col.get("cuaderno"), col.get("fecha")
    if sobrante is _SIN_LIMPIAR:
        sobrante = limpiar_fila(row, col)
    m_anio = RE_ANIO.search(row[i_exp])
    pers_a, (emp_a, otras_a) = detectar(row[i_act])
    pers_d, (emp_d, otras_d) = detectar(row[i_dem])
    empresas = [(rol, nucleo, nombre) for rol, lista in (("actora", emp_a), ("demandada", emp_d))
                for nucleo, nombre in lista]
    partes = (int(otras_a), int(otras_d)) if (emp_a or emp_d) else None
    personas = [(k, n, nombre) for k, lista in ((1, pers_a), (2, pers_d))
                for n, nombre in set(lista)]
    valores = [(row[i] or None) if i is not None else None for i in (col.get(k) for k in FIELDS)]
    # CSV anteriores a la columna cuaderno: el número viene con el resto
    # pegado y se separa acá.
    if i_cuad is not None:
        cuaderno = row[i_cuad] or None
    else:
        valores[I_EXPEDIENTE], cuaderno = separar_expediente(valores[I_EXPEDIENTE])
    valores = tuple(valores) + (
        normalize_tipo(row[i_tipo] or None), int(m_anio.group(1)) if m_anio else None,
        fecha_acuerdo_iso(row[i_facdo]) if i_facdo is not None else None,
        _tipo_acuerdo_fila(row, i_tacdo, i_raw, i_act_t, i_dem),
        _descripcion_fila(row, i_desc, i_raw, i_act_t, i_dem) or descripcion_de_sobrante(sobrante),
        cuaderno,
    )
    fecha = fecha_iso(row[i_fecha_raw]) if i_fecha_raw is not None else None
    return valores, empresas, partes, personas, fecha


def _leer_lineas(path, start, end):
    lineas = []
    with open(path, "rb") as f:
        f.seek(start - 1)
        f.readline()  # termina la línea que empezó antes de `start`
        while f.tell() < end:
            linea = f.readline()
            if not linea:
                break
            lineas.append(linea.decode("utf-8"))
    return lineas


def _parse_bloque(bounds):
    """Calcula las filas que EMPIEZAN dentro de [start, end) del CSV (cada
    fila ocupa una línea, así que se puede cortar en cualquier "\n") y las
    escribe en un SQLite propio: pasarlas serializadas al proceso principal
    costaba casi tanto como leerlas."""
    start, end = bounds
    col = _W["col"]
    filas, det, partes = [], [], []
    rows = list(csv.reader(_leer_lineas(_W["path"], start, end)))
    detectar = detectar_python
    sobrantes = [_SIN_LIMPIAR] * len(rows)
    for row, sobrante in zip(rows, sobrantes):
        valores, empresas, partes_aviso, personas, _ = derivar(row, col, detectar, sobrante)
        filas.append(valores)
        fila = len(filas)
        # Todas las detecciones, también las que aparecen una sola vez.
        for rol, nucleo, nombre in set(empresas):
            det.append((fila, TIPO_EMPRESA, ROLES[rol], nucleo, nombre))
        for k, n, nombre in set(personas):
            det.append((fila, TIPO_PERSONA, k, n, nombre))
        # Si el aviso tiene alguna empresa: si del lado de la actora o la
        # demandada quedan partes que no son empresa. Solo el indicador.
        if partes_aviso:
            partes.append((fila, *partes_aviso))
    shard = os.path.join(_W["tmp_dir"], f"bloque_{start}.sqlite")
    c = sqlite3.connect(shard)
    c.execute("PRAGMA journal_mode=OFF")
    c.execute("PRAGMA synchronous=OFF")
    c.execute(f"CREATE TABLE filas ({', '.join(COLS_CARGA)})")
    c.executemany(f"INSERT INTO filas VALUES ({', '.join(['?'] * len(COLS_CARGA))})", filas)
    c.execute("CREATE TABLE det (fila, tipo, rol, nucleo, nombre)")
    c.executemany("INSERT INTO det VALUES (?, ?, ?, ?, ?)", det)
    c.execute("CREATE TABLE partes (fila, otras_actora, otras_demandada)")
    c.executemany("INSERT INTO partes VALUES (?, ?, ?)", partes)
    c.commit()
    c.close()
    return shard, len(filas)


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


def _generar_precalc_rust(csv_path, precalc_path, tmp_dir=None, log=None):
    """El binario de Rust escribe filas/det/partes/tramos en CSV; de ahí se
    arma el precálculo en SQLite y, si está DuckDB, también en Parquet."""
    salida = Path(tempfile.mkdtemp(prefix="precalc_rs_", dir=tmp_dir))
    try:
        subprocess.run([str(PRECALC_RS), str(csv_path), str(salida), "--hilos", str(NUCLEOS)], check=True)
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
            for tabla, cols in (("filas", ["fila"] + COLS_CARGA),
                                ("det", ["fila", "tipo", "rol", "nucleo", "nombre"]),
                                ("partes", ["fila", "otras_actora", "otras_demandada"]),
                                ("tramos", ["boletin_id", "desde", "hasta"])):
                tipos = ", ".join(f"'{c}': '{'BIGINT' if c in COLS_ENTERAS | {'fila', 'tipo', 'rol', 'otras_actora', 'otras_demandada', 'desde', 'hasta'} else 'VARCHAR'}'"
                                  for c in cols)
                origen = (f"read_csv('{salida / (tabla + '.csv')}', header = true, nullstr = '\\N', "
                          f"allow_quoted_nulls = false, columns = {{{tipos}}})")
                destino = tabla
                db.execute(f"INSERT INTO s.{destino} SELECT * FROM {origen} ORDER BY {cols[0] if tabla != 'tramos' else 'desde'}")
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
    """Calcula todos los avisos de la corrida y los junta en precalc_path:
    filas (rowid = número de fila), det (detecciones de empresas y
    personas), partes, y tramos (rango de filas de cada boletín). Con el
    binario de Rust si está compilado; si no, en procesos de Python por
    bloques del CSV."""
    if usar_rust():
        if log:
            log("    cálculo por aviso: Rust")
        return _generar_precalc_rust(csv_path, precalc_path, tmp_dir, log)
    with open(csv_path, newline="") as f:
        col = {name: i for i, name in enumerate(next(csv.reader(f)))}
    with open(csv_path, "rb") as f:
        inicio_datos = len(f.readline())
    total_bytes = csv_path.stat().st_size
    bloques = [(s, min(s + BLOQUE, total_bytes)) for s in range(inicio_datos, total_bytes, BLOQUE)]
    workers = workers or max(1, NUCLEOS - 1)  # más el proceso principal
    tmp_path = precalc_path.with_name(precalc_path.name + ".tmp")
    tmp_path.unlink(missing_ok=True)
    p = sqlite3.connect(tmp_path)
    p.execute("PRAGMA journal_mode=OFF")
    p.execute("PRAGMA synchronous=OFF")
    cache_sqlite(p)
    hilos_sqlite(p)
    cols = ", ".join(COLS_CARGA)
    _crear_precalc(p)
    total = 0
    with tempfile.TemporaryDirectory(prefix="build_db_", dir=tmp_dir) as tmp, \
            Pool(workers, initializer=_init_worker, initargs=(str(csv_path), col, tmp)) as pool:
        # Como mucho 2 bloques pendientes por proceso, para no llenar el
        # disco temporal si los procesos van más rápido que la copia.
        it = iter(bloques)
        pendientes = deque(pool.apply_async(_parse_bloque, (b,)) for b in itertools.islice(it, 2 * workers))
        while pendientes:
            shard, n = pendientes.popleft().get()
            siguiente = next(it, None)
            if siguiente:
                pendientes.append(pool.apply_async(_parse_bloque, (siguiente,)))
            p.execute("ATTACH DATABASE ? AS s", (shard,))
            p.execute(f"INSERT INTO filas (rowid, {cols}) SELECT rowid + ?, {cols} FROM s.filas ORDER BY rowid",
                      (total,))
            p.execute("INSERT INTO det SELECT fila + ?, tipo, rol, nucleo, nombre FROM s.det ORDER BY fila", (total,))
            p.execute("INSERT INTO partes SELECT fila + ?, otras_actora, otras_demandada FROM s.partes", (total,))
            p.commit()
            p.execute("DETACH DATABASE s")
            os.remove(shard)
            total += n
    # Tramos de filas consecutivas del mismo boletín (cada boletín suele ser
    # uno solo: el scraper escribe boletín por boletín).
    p.executescript("""
        INSERT INTO tramos
            SELECT boletin_id, MIN(fila) AS desde, MAX(fila) AS hasta
            FROM (SELECT rowid AS fila, boletin_id,
                         rowid - ROW_NUMBER() OVER (PARTITION BY boletin_id ORDER BY rowid) AS g
                  FROM filas)
            GROUP BY boletin_id, g ORDER BY desde;
    """)
    p.execute("INSERT INTO meta VALUES ('version', ?)", (precalc_version(),))
    p.commit()
    p.close()
    os.replace(tmp_path, precalc_path)
    return total


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
    tipos = [t for t, si in ((TIPO_EMPRESA, con_empresas), (TIPO_PERSONA, con_personas)) if si]
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


MIN_AVISOS_EMPRESA = 2
MIN_AVISOS_PERSONA = 2

# ── Partes detectadas (empresas y personas) ──────────────────────────────
# Todas las detecciones quedan en la base, como enteros: partes_nombres
# (cada variante de nombre, también las que aparecen una sola vez) y
# partes_avisos (qué variante aparece en qué aviso y de qué lado). De ahí
# salen empresas, entradas_empresas y personas, con sus mínimos de avisos y
# el nombre visible, y el build incremental las rearma sin otro archivo.
TIPO_EMPRESA, TIPO_PERSONA = 1, 2
ROLES = {"actora": 1, "demandada": 2}

# Durante la carga, en el archivo .staging (se borra al terminar).
SQL_PARTES_CARGA = """CREATE TABLE IF NOT EXISTS {esq}.partes_carga (
    entrada_id INTEGER, tipo INTEGER, rol INTEGER, nucleo TEXT, nombre TEXT)"""

SQL_PARTES = """
    CREATE TABLE partes_nombres (
        id     INTEGER PRIMARY KEY,
        tipo   INTEGER NOT NULL,  -- 1 empresa, 2 persona
        nucleo TEXT NOT NULL,
        nombre TEXT NOT NULL,
        UNIQUE (tipo, nucleo, nombre)
    );
    INSERT INTO partes_nombres (tipo, nucleo, nombre)
        SELECT DISTINCT tipo, nucleo, nombre FROM st.partes_carga ORDER BY 1, 2, 3;
    CREATE TABLE partes_avisos (
        nombre_id  INTEGER NOT NULL,
        entrada_id INTEGER NOT NULL,
        rol        INTEGER NOT NULL,  -- 1 actora, 2 demandada
        PRIMARY KEY (nombre_id, entrada_id, rol)
    ) WITHOUT ROWID;
    INSERT INTO partes_avisos
        SELECT DISTINCT n.id, c.entrada_id, c.rol
        FROM st.partes_carga c
        JOIN partes_nombres n ON n.tipo = c.tipo AND n.nucleo = c.nucleo AND n.nombre = c.nombre
        ORDER BY 1, 2, 3;
"""

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
def _etapa_paralela(trabajo):
    """Corre una etapa en su propio archivo SQLite. La base (`b`) y las
    tablas intermedias (`st`) se adjuntan de solo lectura: las consultas sin
    esquema las encuentran ahí, y lo que se crea queda en el archivo de la
    etapa."""
    etapa, base, staging, archivo, fuente = trabajo
    t0 = time.monotonic()
    Path(archivo).unlink(missing_ok=True)
    conn = sqlite3.connect(archivo)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    hilos_sqlite(conn)
    cache_sqlite(conn, 0.5)
    conn.execute("ATTACH DATABASE ? AS b", (f"file:{base}?mode=ro",))
    if staging and Path(staging).exists():
        conn.execute("ATTACH DATABASE ? AS st", (f"file:{staging}?mode=ro",))
    # Empresas y personas leen las partes de la etapa "partes" (build
    # completo) o de la base (incremental).
    esquema_partes = "b"
    if fuente:
        conn.execute("ATTACH DATABASE ? AS p", (f"file:{fuente}?mode=ro",))
        esquema_partes = "p"
    if etapa == "fts":
        # Sin contenido propio: en la base se define con content='entradas'
        # y se le copian estas tablas internas (_data, _idx, _docsize).
        conn.executescript("""
            CREATE VIRTUAL TABLE entradas_fts USING fts5(
                actora, demandada, tipo_juicio, content='', detail=none, columnsize=0
            );
            INSERT INTO entradas_fts(rowid, actora, demandada, tipo_juicio)
                SELECT id, actora, demandada, tipo_juicio FROM b.entradas;
            INSERT INTO entradas_fts(entradas_fts) VALUES('optimize');
        """)
    elif etapa == "partes":
        conn.execute("PRAGMA temp_store=FILE")
        conn.executescript(SQL_PARTES)
    elif etapa == "empresas":
        conn.execute("PRAGMA temp_store=FILE")
        agregados.empresas_completo(conn, esquema_partes)
    elif etapa == "resumen":
        conn.execute("PRAGMA temp_store=FILE")
        agregados.resumen_completo(conn)
    elif etapa == "estadisticas":
        agregados.estadisticas_completo(conn)
    elif etapa == "personas":
        conn.execute("PRAGMA temp_store=FILE")
        agregados.personas_completo(conn, esquema_partes)
    conn.commit()
    conn.close()
    return etapa, time.monotonic() - t0


def correr_etapas(etapas, base, staging, log, partes_en_base=False):
    """Corre las etapas en paralelo (hasta ETAPAS_A_LA_VEZ). Empresas y
    personas necesitan las partes: en el build completo arrancan cuando
    termina la etapa "partes" y la leen de su archivo; en el incremental
    las leen de la base. Devuelve [(etapa, archivo)] en orden."""
    archivo = lambda e: str(Path(base).with_name(f"{Path(base).name}.{e}.sqlite"))
    dependen = {"empresas", "personas"}
    hechas = []
    pendientes = [e for e in etapas if e not in dependen or partes_en_base]
    esperan = [e for e in etapas if e in dependen and not partes_en_base]
    fuente = lambda e: archivo("partes") if e in dependen and not partes_en_base else None
    global HILOS
    procesos = min(len(etapas), ETAPAS_A_LA_VEZ or len(etapas), NUCLEOS)
    hilos_antes, HILOS = HILOS, max(1, min(8, NUCLEOS // procesos))
    with Pool(procesos) as pool:
        corriendo = {}
        def lanzar(e):
            corriendo[e] = pool.apply_async(_etapa_paralela, ((e, base, staging, archivo(e), fuente(e)),))
        # Primero las que habilitan a otras.
        for e in sorted(pendientes, key=lambda e: e != "partes"):
            lanzar(e)
        while corriendo:
            listas = [e for e, r in corriendo.items() if r.ready()]
            if not listas:
                time.sleep(0.5)
                continue
            for e in listas:
                _, segundos = corriendo.pop(e).get()
                log(f"  {e}: {segundos:.0f} s")
                hechas.append(e)
                if e == "partes":
                    for d in esperan:
                        lanzar(d)
    HILOS = hilos_antes
    return [(e, archivo(e)) for e in etapas]


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
    parser.add_argument("--sin-vacuum", action="store_true",
                        help="No compactar al final (más rápido; para la base del "
                             "server, donde el tamaño del archivo no importa)")
    parser.add_argument("--empresas", action="store_true",
                        help="Tablas de empresas que son parte (sección /empresas del server)")
    parser.add_argument("--personas", action="store_true",
                        help="Tabla de personas físicas que son parte (sección /personas del server, noindex)")
    parser.add_argument("--workers", type=int,
                        help="Procesos para la carga (por defecto, todos los núcleos menos uno)")
    parser.add_argument("--estadisticas", action="store_true",
                        help="Tablas de desempeño por órgano y de tipos de juicio (/salas, /juzgados, /tipos)")
    parser.add_argument("--solo-estadisticas", action="store_true",
                        help="Rearmar solo las estadísticas de una base ya armada (--output)")
    parser.add_argument("--sivep",
                        help="sivep.sqlite con la tabla enlaces (scraper/sivep_enlace.py): "
                             "agrega los enlaces a versiones públicas de sentencias")
    parser.add_argument("--sin-duckdb", action="store_true",
                        help="Build completo solo con SQLite aunque duckdb esté instalado")
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

    if args.solo_estadisticas:
        conn = sqlite3.connect(output)
        cache_sqlite(conn)
        hilos_sqlite(conn)
        conn.executescript("""
            DROP TABLE IF EXISTS organo_mes; DROP TABLE IF EXISTS ponencia_mes; DROP TABLE IF EXISTS tipo_mes;
            DROP TABLE IF EXISTS tipo_organo; DROP TABLE IF EXISTS duracion_expedientes;
            DROP TABLE IF EXISTS organo_duracion; DROP TABLE IF EXISTS tipo_duracion;
        """)
        agregados.estadisticas_completo(conn)
        conn.commit()
        conn.close()
        log(f"Listo: {output}")
        return
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
                     "--solo-estadisticas / --solo-sivep)")
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
    # Con DuckDB instalado, la carga y los agregados van por DuckDB
    # (build_duck.py): mismo resultado, mucho más rápido.
    if not args.sin_duckdb:
        try:
            import build_duck
        except ImportError:
            log("  (sin duckdb: build con SQLite)")
        else:
            build_duck.build_completo(args, output, runs, log, sys.modules[__name__])
            log(f"\nListo: {output} ({output.stat().st_size / 1e6:.1f} MB)")
            return

    output.unlink(missing_ok=True)
    conn = sqlite3.connect(output)
    # Archivo nuevo y descartable (si el build se corta se vuelve a correr):
    # sin journal ni fsync, con caché grande.
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    hilos_sqlite(conn)
    cache_sqlite(conn)
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA locking_mode=EXCLUSIVE")

    create_schema(conn)
    # Tablas intermedias (empresas y personas por aviso) en un archivo aparte:
    # en la base final dejaban páginas vacías al borrarlas, y compactarla
    # costaba ~7 min. Además, así las etapas en paralelo las leen sin tocar
    # la base.
    staging = output.with_name(output.name + ".staging.sqlite")
    staging.unlink(missing_ok=True)
    conn.execute("ATTACH DATABASE ? AS st", (str(staging),))
    if args.empresas or args.personas:
        conn.execute(SQL_PARTES_CARGA.format(esq="st"))
    if args.empresas:
        conn.executescript("""
            -- Por aviso con alguna empresa: si del lado de la actora o de la
            -- demandada hay partes que no son empresa. Solo el indicador,
            -- para mostrar "persona física" sin el nombre.
            CREATE TABLE entradas_partes (
                entrada_id      INTEGER PRIMARY KEY,
                otras_actora    INTEGER NOT NULL,
                otras_demandada INTEGER NOT NULL
            );
        """)

    # Del run más nuevo al más viejo: si un boletín está en varios, gana el
    # más reciente (parseado con la versión más nueva del parser).
    runs = list(reversed(runs))
    for csv_path in runs:
        index_path = csv_path.parent / "index.json"
        if index_path.exists():
            load_index(conn, index_path)

    en_rango = None
    if args.desde_fecha or args.hasta_fecha:
        conn.execute("DELETE FROM boletines WHERE fecha_iso IS NULL OR fecha_iso < ? OR fecha_iso > ?",
                     (args.desde_fecha or "0000-00-00", args.hasta_fecha or "9999-99-99"))
        en_rango = {r[0] for r in conn.execute("SELECT id FROM boletines")}

    total = 0
    loaded = set()
    for csv_path in runs:
        run_dir = csv_path.parent
        log(f"  {run_dir.name}...")

        n = load_entradas(conn, csv_path, loaded, log, en_rango, workers=args.workers, tmp_dir=str(output.parent),
                          con_empresas=args.empresas, con_personas=args.personas)
        total += n
        log(f"    {n} entradas")

    conn.commit()
    conn.execute("DETACH DATABASE st")

    log("Índices...")
    create_indexes(conn, args.indices_server, args.web)
    populate_boletin_ranges(conn, log)
    populate_meta(conn)
    conn.commit()
    conn.close()

    # Etapas independientes en paralelo, cada una en su propio archivo: leen
    # la base (que no se modifica mientras tanto) y escriben aparte. Después
    # se copian a la base.
    etapas = ["fts", "resumen"]
    if args.estadisticas:
        etapas.append("estadisticas")
    if args.empresas or args.personas:
        etapas.append("partes")
    if args.empresas:
        etapas.append("empresas")
    if args.personas:
        etapas.append("personas")
    log(f"Etapas en paralelo: {', '.join(etapas)}...")
    trabajos = correr_etapas(etapas, str(output), str(staging), log)

    conn = sqlite3.connect(output)
    conn.execute("PRAGMA synchronous=OFF")
    hilos_sqlite(conn)
    cache_sqlite(conn)
    for etapa, archivo in trabajos:
        _juntar(conn, etapa, archivo, log)
        os.remove(archivo)
    staging.unlink(missing_ok=True)
    if args.sivep:
        build_sivep(conn, log, args.sivep)
    conn.commit()
    log(f"Total: {total} entradas.")
    conn.close()
    if args.empresas or args.personas:
        build_vocabulario(output, log)
    # Compactar solo si quedaron muchas páginas libres (sin tablas
    # intermedias en la base casi no quedan).
    conn = sqlite3.connect(output)
    libres, paginas = conn.execute("PRAGMA freelist_count").fetchone()[0], conn.execute("PRAGMA page_count").fetchone()[0]
    if not args.sin_vacuum and paginas and libres / paginas > 0.05:
        log(f"Compactando ({100 * libres / paginas:.0f} % de páginas libres)...")
        conn.execute("VACUUM")
    conn.close()

    size_mb = output.stat().st_size / 1e6
    log(f"\nListo: {output} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
