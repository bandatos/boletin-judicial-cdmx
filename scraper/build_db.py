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
import shutil
import sqlite3
import subprocess
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


def hilos_sqlite(conn):
    """Ordenamiento en varios hilos para CREATE INDEX y GROUP BY grandes
    (PRAGMA threads, SQLite >= 3.8.7). Sin esto, indexar 34M filas usa un
    solo núcleo."""
    conn.execute(f"PRAGMA threads={min(8, os.cpu_count() or 1)}")


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
    if not indices_server:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_anio ON entradas (anio)")
    conn.executescript("""

        CREATE INDEX IF NOT EXISTS idx_tipo_juicio_norm
            ON entradas (tipo_juicio_norm);
    """)
    if indices_server:
        # Solo para la base del server (en la del browser suman MB a la
        # descarga y con 2M de filas alcanza un recorrido):
        # - búsqueda por número de expediente sin saber el juzgado (el
        #   índice de arriba empieza por juzgado);
        # - gráfico por año y tipo leyendo solo el índice: con idx_anio
        #   SQLite va a la tabla por cada fila y con 34M tarda minutos.
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_expediente_num ON entradas (expediente);
            CREATE INDEX IF NOT EXISTS idx_anio_tipo ON entradas (anio, tipo_juicio_norm);
        """)
        # idx_anio queda cubierto por idx_anio_tipo (empieza por anio): en la
        # base del server sobra, y armarlo es otro recorrido de 35M filas.
        conn.execute("DROP INDEX IF EXISTS idx_anio")


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
            detail=none
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


def _init_worker(csv_path, col, loaded, en_rango, tmp_dir, con_empresas=False, con_personas=False,
                 precalc=False):
    _W.update(path=csv_path, col=col, loaded=loaded, en_rango=en_rango, tmp_dir=tmp_dir,
              con_empresas=con_empresas, con_personas=con_personas, precalc=precalc)


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


# ── Cálculo por aviso, guardado por corrida (entradas.precalc.csv) ───────
# Lo caro de la carga es el análisis en Python de cada aviso (nombres,
# número, tipo de acuerdo, descripción, empresas, personas): ~1 ms por
# aviso, casi 30 min para 35M. Da siempre lo mismo para los avisos viejos,
# así que la primera vez que se procesa una corrida se guarda en
# <corrida>/entradas.precalc.csv, con un sello de la versión del código. Los
# builds siguientes lo leen mientras el sello coincida.
PRECALC_COLS = COLS_CARGA + ["empresas", "partes", "personas", "fecha_iso"]
PRECALC_ARCHIVO = "entradas.precalc.csv"


def precalc_version():
    """Sello del código que produce el precálculo: si cambia cualquiera de
    estos módulos o la función derivar, el precálculo se rehace."""
    import hashlib
    import inspect
    h = hashlib.sha256()
    base = Path(__file__).resolve().parent
    for nombre in ("acuerdos.py", "empresas.py", "personas.py", "normalize_tipo.py"):
        h.update((base / nombre).read_bytes())
    h.update(inspect.getsource(derivar).encode())
    h.update(",".join(PRECALC_COLS).encode())
    return h.hexdigest()[:16]


def derivar(row, col):
    """Todo lo que la carga calcula de un aviso del CSV del scraper.
    Devuelve (valores en el orden de COLS_CARGA, empresas [(rol, núcleo,
    nombre)], partes (otras_actora, otras_demandada) o None, personas
    [(1|2, núcleo, nombre)], fecha ISO del boletín)."""
    i_act, i_dem, i_exp, i_tipo = col["actora"], col["demandada"], col["expediente"], col["tipo_juicio"]
    i_facdo = col.get("fecha_acuerdo")
    i_tacdo, i_raw, i_act_t = col.get("tipo_acuerdo"), col.get("raw_text"), col.get("tipo_actuacion")
    i_desc, i_cuad, i_fecha_raw = col.get("descripcion"), col.get("cuaderno"), col.get("fecha")
    # Nombres que se tragaron el tipo de juicio o la descripción ("Guzmán
    # García Lorena. Aud. 272-B ..."): se corta el nombre y lo que sobra
    # sirve de descripción si no hay otra (acuerdos.separar_parte).
    sobrante = None
    row[i_act] = quitar_marca_publicacion(row[i_act])
    if row[i_dem]:
        row[i_dem], sobrante = separar_parte(row[i_dem])
    elif row[i_act]:
        row[i_act], sobrante = separar_parte(row[i_act])
    m_anio = RE_ANIO.search(row[i_exp])
    emp_a, otras_a = emp.partes(row[i_act])
    emp_d, otras_d = emp.partes(row[i_dem])
    empresas = [(rol, nucleo, nombre) for rol, lista in (("actora", emp_a), ("demandada", emp_d))
                for nucleo, nombre in lista]
    partes = (int(otras_a), int(otras_d)) if (emp_a or emp_d) else None
    personas = [(k, n, nombre) for k, campo in ((1, row[i_act]), (2, row[i_dem]))
                for n, nombre in set(pers.personas(campo))]
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
    """Lee y transforma las filas que EMPIEZAN dentro de [start, end) del CSV.
    Cada fila ocupa exactamente una línea (ningún campo lleva saltos de
    línea), así que se puede cortar el archivo en cualquier "\n".

    Con _W["precalc"] el archivo es el precálculo de la corrida y no se
    calcula nada. Si no, se calcula cada aviso (también los que se saltean,
    para que el precálculo quede completo) y el bloque se escribe además en
    una parte del precálculo."""
    start, end = bounds
    col, loaded, en_rango = _W["col"], _W["loaded"], _W["en_rango"]
    usar_precalc = _W.get("precalc", False)
    i_bol = col["boletin_id"]
    lineas = _leer_lineas(_W["path"], start, end)

    filas, ids, salteadas = [], set(), 0
    # Con --empresas: (fila, rol, núcleo, nombre) por cada empresa detectada,
    # y (fila, otras_actora, otras_demandada) si el aviso tiene alguna: si
    # del lado de la actora o la demandada quedan partes que no son empresa
    # (personas). Solo el indicador: los nombres de personas no se guardan.
    filas_empresas, filas_partes = [], []
    # Con --personas: las personas del bloque, ya agregadas por (núcleo,
    # nombre). Así no hay que releer los 35M avisos de la base después.
    acum_personas = {}
    precalc = []
    n_cols = len(COLS_CARGA)
    for row in csv.reader(lineas):
        boletin_id = int(row[i_bol])
        if usar_precalc:
            if (en_rango is not None and boletin_id not in en_rango) or boletin_id in loaded:
                salteadas += boletin_id in loaded
                continue
            valores = tuple(v or None for v in row[:n_cols])
            empresas = json.loads(row[n_cols])
            partes = json.loads(row[n_cols + 1])
            personas = json.loads(row[n_cols + 2])
            fecha = row[n_cols + 3] or None
        else:
            valores, empresas, partes, personas, fecha = derivar(row, col)
            precalc.append(valores + (json.dumps(empresas, ensure_ascii=False), json.dumps(partes),
                                      json.dumps(personas, ensure_ascii=False), fecha))
            if (en_rango is not None and boletin_id not in en_rango) or boletin_id in loaded:
                salteadas += boletin_id in loaded
                continue
        ids.add(boletin_id)
        fila = len(filas)
        if _W["con_empresas"]:
            for rol, nucleo, nombre in empresas:
                filas_empresas.append((fila, rol, nucleo, nombre))
            if partes:
                filas_partes.append((fila, partes[0], partes[1]))
        if _W["con_personas"]:
            for k, n, nombre in {(k, n, nombre) for k, n, nombre in personas}:
                a = acum_personas.get((n, nombre))
                if a is None:
                    a = acum_personas[(n, nombre)] = [0, 0, 0, fecha, fecha]
                a[0] += 1
                a[k] += 1
                a[3] = min(filter(None, (a[3], fecha)), default=None)
                a[4] = max(filter(None, (a[4], fecha)), default=None)
        filas.append(valores)

    # El bloque se escribe en un SQLite propio en vez de devolver las filas:
    # pasarlas serializadas al proceso principal costaba casi tanto como
    # leerlas. El principal las copia con INSERT ... SELECT (en C).
    shard = os.path.join(_W["tmp_dir"], f"bloque_{start}.sqlite")
    c = sqlite3.connect(shard)
    c.execute("PRAGMA journal_mode=OFF")
    c.execute("PRAGMA synchronous=OFF")
    c.execute(f"CREATE TABLE filas ({', '.join(COLS_CARGA)})")
    c.executemany(f"INSERT INTO filas VALUES ({', '.join(['?'] * len(COLS_CARGA))})", filas)
    if _W["con_empresas"]:
        c.execute("CREATE TABLE filas_empresas (fila, rol, nucleo, nombre)")
        c.executemany("INSERT INTO filas_empresas VALUES (?, ?, ?, ?)", filas_empresas)
        c.execute("CREATE TABLE filas_partes (fila, otras_actora, otras_demandada)")
        c.executemany("INSERT INTO filas_partes VALUES (?, ?, ?)", filas_partes)
    if _W["con_personas"]:
        c.execute("CREATE TABLE filas_personas (nucleo, nombre, n, act, dem, desde, hasta)")
        c.executemany("INSERT INTO filas_personas VALUES (?, ?, ?, ?, ?, ?, ?)",
                      [(n, nombre, *a) for (n, nombre), a in acum_personas.items()])
    c.commit()
    c.close()
    parte = None
    if precalc:
        parte = os.path.join(_W["tmp_dir"], f"precalc_{start}.csv")
        with open(parte, "w", newline="") as f:
            csv.writer(f).writerows(precalc)
    return shard, len(filas), ids, salteadas, parte


def load_entradas(conn, csv_path, loaded, log, en_rango=None, workers=None, tmp_dir=None,
                  con_empresas=False, con_personas=False):
    """Carga las entradas de un run salteando los boletines que ya cargó un
    run anterior (`loaded`, se actualiza). Los runs se pisan entre sí (el
    histórico repite boletines de los runs de 2026) y sin esto cada boletín
    repetido aparecía dos veces. `en_rango`: si se pasa, solo se cargan
    esos boletines (filtro --desde-fecha).

    Leer el CSV, transformar e insertar las filas se reparte en procesos
    por bloques del archivo, cada uno a su propio SQLite temporal. Acá solo
    se copian los bloques en el orden del CSV: así las entradas de cada
    boletín quedan contiguas (ver populate_boletin_ranges)."""
    # Si el run no aporta ningún boletín nuevo (todos ya cargados de un run
    # más nuevo, o fuera del rango de fechas) ni se lee: el CSV del histórico
    # pesa ~8 GB. Los boletines del run son los .txt que tiene en pdfs/.
    del_run = {int(p.stem[len("boletin_"):]) for p in (csv_path.parent / "pdfs").glob("boletin_*.txt")}
    if del_run:
        utiles = del_run - loaded
        if en_rango is not None:
            utiles &= en_rango
        if not utiles:
            log("    sin boletines nuevos, se saltea")
            return 0

    # Precálculo de la corrida, si existe y es de esta versión del código.
    precalc_path = csv_path.parent / PRECALC_ARCHIVO
    sello_path = csv_path.parent / (PRECALC_ARCHIVO + ".version")
    version = precalc_version()
    usar_precalc = (precalc_path.exists() and sello_path.exists()
                    and sello_path.read_text().strip() == version
                    and precalc_path.stat().st_mtime >= csv_path.stat().st_mtime)
    origen = precalc_path if usar_precalc else csv_path
    log(f"    {'precálculo guardado' if usar_precalc else 'calculando (y guardando el precálculo)'}")

    with open(origen, newline="") as f:
        col = {name: i for i, name in enumerate(next(csv.reader(f)))}
    with open(origen, "rb") as f:
        inicio_datos = len(f.readline())
    total_bytes = origen.stat().st_size
    bloques = [(s, min(s + BLOQUE, total_bytes)) for s in range(inicio_datos, total_bytes, BLOQUE)]

    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    counts = {"inserted": 0, "skipped": 0}
    new_ids = set()
    cols = ", ".join(COLS_CARGA)

    with tempfile.TemporaryDirectory(prefix="build_db_", dir=tmp_dir) as tmp, \
            Pool(workers, initializer=_init_worker,
                 initargs=(str(origen), col, frozenset(loaded), en_rango, tmp, con_empresas,
                           con_personas, usar_precalc)) as pool:
        # Como mucho 2 bloques pendientes por proceso, para no llenar el
        # disco temporal si los procesos van más rápido que la copia.
        it = iter(bloques)
        pendientes = deque(pool.apply_async(_parse_bloque, (b,))
                           for b in itertools.islice(it, 2 * workers))
        partes_precalc = []
        while pendientes:
            shard, n, ids, salteadas, parte = pendientes.popleft().get()
            if parte:
                partes_precalc.append(parte)
            siguiente = next(it, None)
            if siguiente:
                pendientes.append(pool.apply_async(_parse_bloque, (siguiente,)))
            if n:
                conn.execute("ATTACH DATABASE ? AS bloque", (shard,))
                # ids nuevos: base+1 ... base+n, en el orden de las filas del bloque
                base = conn.execute("SELECT COALESCE(MAX(id), 0) FROM entradas").fetchone()[0]
                conn.execute(f"INSERT INTO entradas ({cols}) SELECT {cols} FROM bloque.filas ORDER BY rowid")
                if con_empresas:
                    conn.execute("INSERT INTO emp_stage SELECT ? + fila + 1, rol, nucleo, nombre "
                                 "FROM bloque.filas_empresas", (base,))
                    conn.execute("INSERT INTO entradas_partes SELECT ? + fila + 1, otras_actora, otras_demandada "
                                 "FROM bloque.filas_partes", (base,))
                if con_personas:
                    conn.execute("INSERT INTO personas_stage SELECT * FROM bloque.filas_personas")
                conn.commit()
                conn.execute("DETACH DATABASE bloque")
            os.remove(shard)
            new_ids.update(ids)
            counts["inserted"] += n
            counts["skipped"] += salteadas

        # Juntar las partes del precálculo en el orden del CSV y sellarlo.
        if not usar_precalc and partes_precalc:
            tmp_precalc = precalc_path.with_suffix(".csv.tmp")
            with open(tmp_precalc, "w", newline="") as out:
                csv.writer(out).writerow(PRECALC_COLS)
                for parte in partes_precalc:
                    with open(parte) as f:
                        shutil.copyfileobj(f, out)
                    os.remove(parte)
            os.replace(tmp_precalc, precalc_path)
            sello_path.write_text(version + "\n")

    loaded |= new_ids
    if counts["skipped"]:
        log(f"    {counts['skipped']} entradas salteadas (boletines ya cargados de un run más nuevo)")
    return counts["inserted"]


MIN_AVISOS_EMPRESA = 2


def build_empresas(conn, log):
    """Arma empresas / entradas_empresas / empresas_fts desde emp_stage.

    Solo entran las empresas con al menos MIN_AVISOS_EMPRESA avisos: los
    restos de parseo (una persona o un encabezado pegados a una razón
    social) casi siempre aparecen una sola vez, y estas páginas se indexan
    y no pueden mostrar personas. El nombre visible es la variante más
    frecuente tal cual está en el boletín."""
    log("Empresas...")
    conn.executescript("""
        CREATE TABLE empresas (
            id             INTEGER PRIMARY KEY,
            nucleo         TEXT UNIQUE NOT NULL,
            nombre         TEXT NOT NULL,
            slug           TEXT UNIQUE NOT NULL,
            avisos         INTEGER NOT NULL,
            como_actora    INTEGER NOT NULL,
            como_demandada INTEGER NOT NULL,
            expedientes    INTEGER,
            primer_boletin TEXT,
            ultimo_boletin TEXT
        );
        CREATE TEMP TABLE nombres AS
            SELECT nucleo, nombre, COUNT(*) AS n FROM emp_stage GROUP BY nucleo, nombre;
    """)
    totales = {r[0]: r[1:] for r in conn.execute("""
        SELECT nucleo, COUNT(*), SUM(rol = 'actora'), SUM(rol = 'demandada')
        FROM emp_stage GROUP BY nucleo HAVING COUNT(*) >= ?
    """, (MIN_AVISOS_EMPRESA,))}
    mejor = {}
    for nucleo, nombre, n in conn.execute("SELECT nucleo, nombre, n FROM nombres ORDER BY nucleo, n DESC, nombre"):
        if nucleo in totales and nucleo not in mejor:
            mejor[nucleo] = nombre
    slugs = set()
    filas = []
    for nucleo, (avisos, act, dem) in sorted(totales.items(), key=lambda x: -x[1][0]):
        base = emp.slugify(nucleo)  # sin forma societaria: /empresas/banco-mercantil-del-norte
        slug, k = base, 2
        while slug in slugs:
            slug, k = f"{base}-{k}", k + 1
        slugs.add(slug)
        filas.append((nucleo, mejor[nucleo], slug, avisos, act, dem))
    conn.executemany("INSERT INTO empresas (nucleo, nombre, slug, avisos, como_actora, como_demandada) "
                     "VALUES (?, ?, ?, ?, ?, ?)", filas)
    conn.executescript("""
        CREATE TABLE entradas_empresas (
            empresa_id INTEGER NOT NULL,
            entrada_id INTEGER NOT NULL,
            rol        TEXT NOT NULL
        );
        INSERT INTO entradas_empresas
            SELECT DISTINCT e.id, s.entrada_id, s.rol
            FROM emp_stage s JOIN empresas e ON e.nucleo = s.nucleo;
        DROP TABLE nombres;
        CREATE INDEX idx_ee_empresa ON entradas_empresas (empresa_id, entrada_id);
        CREATE INDEX idx_ee_entrada ON entradas_empresas (entrada_id);

        UPDATE empresas SET
            (avisos, como_actora, como_demandada, expedientes, primer_boletin, ultimo_boletin) = (
                SELECT COUNT(*), SUM(ee.rol = 'actora'), SUM(ee.rol = 'demandada'),
                       COUNT(DISTINCT COALESCE(en.sala, en.juzgado, '') || '|' || COALESCE(en.expediente, en.id)),
                       MIN(b.fecha_iso), MAX(b.fecha_iso)
                FROM entradas_empresas ee
                JOIN entradas en ON en.id = ee.entrada_id
                JOIN boletines b ON b.id = en.boletin_id
                WHERE ee.empresa_id = empresas.id
            );

        CREATE VIRTUAL TABLE empresas_fts USING fts5(
            nombre, content='empresas', content_rowid='id', detail=none
        );
        INSERT INTO empresas_fts(rowid, nombre) SELECT id, nombre FROM empresas;
    """)
    # La tabla intermedia se borra solo si está en esta base (en el build en
    # paralelo está en el archivo .staging, adjunto de solo lectura).
    if conn.execute("SELECT 1 FROM main.sqlite_master WHERE name = 'emp_stage'").fetchone():
        conn.execute("DROP TABLE main.emp_stage")
    log(f"  {len(filas)} empresas con al menos {MIN_AVISOS_EMPRESA} avisos")
    build_agregados_empresas(conn, log)


# "main.": en las etapas en paralelo la base va adjunta y ya puede tener
# estas tablas (build incremental); sin esquema, el DROP iría a ella.
SQL_AGREGADOS_EMPRESAS = """
    -- Lo que muestra /empresas/:slug, precalculado: con cientos de miles de
    -- avisos por empresa (bancos) agregarlo en cada visita tardaba ~35 s.
    DROP TABLE IF EXISTS main.empresas_tipos;
    DROP TABLE IF EXISTS main.empresas_anios;
    DROP TABLE IF EXISTS main.empresas_expedientes;
    CREATE TABLE empresas_tipos AS
        SELECT ee.empresa_id, COALESCE(en.tipo_juicio_norm, 'Sin tipo reconocido') AS tipo, COUNT(*) AS n
        FROM entradas_empresas ee JOIN entradas en ON en.id = ee.entrada_id
        GROUP BY ee.empresa_id, tipo;
    CREATE INDEX idx_empresas_tipos ON empresas_tipos (empresa_id, n DESC);
    CREATE TABLE empresas_anios AS
        SELECT ee.empresa_id, substr(b.fecha_iso, 1, 4) AS anio, COUNT(*) AS n
        FROM entradas_empresas ee
        JOIN entradas en ON en.id = ee.entrada_id
        JOIN boletines b ON b.id = en.boletin_id
        -- por expresión y no por alias: "anio" también es una columna de
        -- entradas (año del expediente) y SQLite agruparía por esa.
        GROUP BY ee.empresa_id, substr(b.fecha_iso, 1, 4);
    CREATE INDEX idx_empresas_anios ON empresas_anios (empresa_id, anio);
    DROP TABLE IF EXISTS main.empresas_meses;
    CREATE TABLE empresas_meses AS
        SELECT ee.empresa_id, substr(b.fecha_iso, 1, 7) AS mes, COUNT(*) AS n
        FROM entradas_empresas ee
        JOIN entradas en ON en.id = ee.entrada_id
        JOIN boletines b ON b.id = en.boletin_id
        GROUP BY ee.empresa_id, substr(b.fecha_iso, 1, 7);
    CREATE INDEX idx_empresas_meses ON empresas_meses (empresa_id, mes);
    -- Un renglón por (empresa, órgano, expediente, rol) con su último aviso
    -- por FECHA: los ids no son cronológicos (los runs se cargan del más
    -- nuevo al más viejo). ultimo_id sale de la fila con la fecha máxima
    -- (columna "bare" junto a MAX() en SQLite).
    CREATE TABLE empresas_expedientes AS
        SELECT ee.empresa_id, COALESCE(en.sala, en.juzgado) AS organo, en.expediente, ee.rol,
               COUNT(*) AS avisos, MAX(b.fecha_iso) AS ultima_fecha, en.id AS ultimo_id
        FROM entradas_empresas ee
        JOIN entradas en ON en.id = ee.entrada_id
        JOIN boletines b ON b.id = en.boletin_id
        GROUP BY ee.empresa_id, COALESCE(en.sala, en.juzgado), en.expediente, ee.rol;
    CREATE INDEX idx_empresas_expedientes ON empresas_expedientes (empresa_id, ultima_fecha DESC);
"""


# Desempeño por órgano y estadísticas por tipo de juicio (/salas,
# /juzgados, pestaña de desempeño de /juzgado/:slug y /tipos). Todo por mes
# de publicación: los gráficos se ven por año o por mes. Mide lo que publica
# el boletín, no la carga de trabajo (no hay ingresos ni personal).
SQL_ESTADISTICAS = """
    DROP TABLE IF EXISTS main.organo_mes;
    DROP TABLE IF EXISTS main.ponencia_mes;
    DROP TABLE IF EXISTS main.organo_duracion;
    DROP TABLE IF EXISTS main.tipo_duracion;
    DROP TABLE IF EXISTS main.tipo_mes;
    DROP TABLE IF EXISTS main.tipo_organo;

    -- Demora de publicación: días entre la fecha del acuerdo ("ACUERDOS
    -- DEL ...") y la del boletín. Fuera de 0 a 60 días se descarta: son
    -- errores de lectura de la fecha.
    CREATE TABLE organo_mes AS
        SELECT COALESCE(e.sala, e.juzgado) AS organo,
               CASE WHEN e.sala IS NOT NULL THEN 'sala' ELSE 'juzgado' END AS tipo_organo,
               substr(b.fecha_iso, 1, 7) AS mes,
               COUNT(*) AS avisos,
               SUM(e.tipo_actuacion = 'acuerdo') AS acuerdos,
               SUM(e.tipo_actuacion IN ('sentencia_definitiva', 'sentencia')) AS sentencias,
               SUM(e.tipo_actuacion = 'sentencia_interlocutoria') AS interlocutorias,
               SUM(e.tipo_actuacion = 'audiencia') AS audiencias,
               SUM(e.tipo_acuerdo = 'prevencion') AS prevenciones,
               SUM(e.tipo_acuerdo = 'negacion') AS negaciones,
               SUM(e.tipo_acuerdo = 'admision') AS admisiones,
               COUNT(DISTINCT e.expediente) AS expedientes,
               SUM(CASE WHEN julianday(b.fecha_iso) - julianday(e.fecha_acuerdo) BETWEEN 0 AND 60
                        THEN julianday(b.fecha_iso) - julianday(e.fecha_acuerdo) END) AS demora_suma,
               SUM(CASE WHEN julianday(b.fecha_iso) - julianday(e.fecha_acuerdo) BETWEEN 0 AND 60
                        THEN 1 ELSE 0 END) AS demora_n
        FROM entradas e JOIN boletines b ON b.id = e.boletin_id
        WHERE COALESCE(e.sala, e.juzgado) IS NOT NULL AND b.fecha_iso IS NOT NULL
        GROUP BY 1, 2, 3;
    CREATE INDEX idx_organo_mes ON organo_mes (organo, mes);
    CREATE INDEX idx_organo_mes_tipo ON organo_mes (tipo_organo, mes);

    CREATE TABLE ponencia_mes AS
        SELECT e.sala, e.ponencia, substr(b.fecha_iso, 1, 7) AS mes, COUNT(*) AS sentencias
        FROM entradas e JOIN boletines b ON b.id = e.boletin_id
        WHERE e.sala IS NOT NULL AND e.ponencia IS NOT NULL AND b.fecha_iso IS NOT NULL
        GROUP BY 1, 2, 3;
    CREATE INDEX idx_ponencia_mes ON ponencia_mes (sala, mes);

    -- Tiempo hasta sentencia, por expediente: del primer aviso publicado a
    -- la primera sentencia (definitiva, o de sala). Solo expedientes de
    -- 2017 en adelante: los anteriores empezaron antes del primer boletín
    -- disponible (octubre de 2016) y su primer aviso no es el real.
    CREATE TEMP TABLE exp_fechas AS
        SELECT COALESCE(e.sala, e.juzgado) AS organo, e.expediente,
               MAX(e.tipo_juicio_norm) AS tipo,
               MIN(b.fecha_iso) AS primero,
               MIN(CASE WHEN e.tipo_actuacion IN ('sentencia_definitiva', 'sentencia')
                        THEN b.fecha_iso END) AS sentencia
        FROM entradas e JOIN boletines b ON b.id = e.boletin_id
        WHERE e.anio >= 2017 AND e.expediente IS NOT NULL AND b.fecha_iso IS NOT NULL
              AND COALESCE(e.sala, e.juzgado) IS NOT NULL
        GROUP BY 1, 2;
    CREATE TEMP TABLE exp_dias AS
        SELECT organo, tipo, CAST(substr(sentencia, 1, 4) AS INTEGER) AS anio,
               julianday(sentencia) - julianday(primero) AS dias
        -- Si la sentencia es el primer aviso publicado no hay un tiempo que
        -- medir (el expediente empezó antes de lo que se ve, o el boletín
        -- no publicó sus acuerdos previos).
        FROM exp_fechas WHERE sentencia IS NOT NULL AND sentencia > primero;
    CREATE TABLE organo_duracion AS
        SELECT organo, anio, COUNT(*) AS n,
               MAX(CASE WHEN rn = (n + 1) / 2 THEN dias END) AS mediana,
               MAX(CASE WHEN rn = (n + 3) / 4 THEN dias END) AS p25,
               MAX(CASE WHEN rn = (3 * n + 3) / 4 THEN dias END) AS p75
        FROM (SELECT organo, anio, dias,
                     ROW_NUMBER() OVER (PARTITION BY organo, anio ORDER BY dias) AS rn,
                     COUNT(*) OVER (PARTITION BY organo, anio) AS n
              FROM exp_dias)
        GROUP BY organo, anio;
    CREATE INDEX idx_organo_duracion ON organo_duracion (organo, anio);
    CREATE TABLE tipo_duracion AS
        SELECT tipo, anio, COUNT(*) AS n,
               MAX(CASE WHEN rn = (n + 1) / 2 THEN dias END) AS mediana,
               MAX(CASE WHEN rn = (n + 3) / 4 THEN dias END) AS p25,
               MAX(CASE WHEN rn = (3 * n + 3) / 4 THEN dias END) AS p75
        FROM (SELECT tipo, anio, dias,
                     ROW_NUMBER() OVER (PARTITION BY tipo, anio ORDER BY dias) AS rn,
                     COUNT(*) OVER (PARTITION BY tipo, anio) AS n
              FROM exp_dias WHERE tipo IS NOT NULL)
        GROUP BY tipo, anio;
    CREATE INDEX idx_tipo_duracion ON tipo_duracion (tipo, anio);
    DROP TABLE exp_fechas;
    DROP TABLE exp_dias;

    CREATE TABLE tipo_mes AS
        SELECT e.tipo_juicio_norm AS tipo, substr(b.fecha_iso, 1, 7) AS mes, COUNT(*) AS avisos,
               SUM(e.tipo_actuacion IN ('sentencia_definitiva', 'sentencia')) AS sentencias
        FROM entradas e JOIN boletines b ON b.id = e.boletin_id
        WHERE e.tipo_juicio_norm IS NOT NULL AND b.fecha_iso IS NOT NULL
        GROUP BY 1, 2;
    CREATE INDEX idx_tipo_mes ON tipo_mes (tipo, mes);
    CREATE INDEX idx_tipo_mes_mes ON tipo_mes (mes);

    CREATE TABLE tipo_organo AS
        SELECT tipo_juicio_norm AS tipo, COALESCE(sala, juzgado) AS organo, COUNT(*) AS avisos
        FROM entradas
        WHERE tipo_juicio_norm IS NOT NULL AND COALESCE(sala, juzgado) IS NOT NULL
        GROUP BY 1, 2;
    CREATE INDEX idx_tipo_organo ON tipo_organo (tipo, avisos DESC);
"""

# Con las tablas de empresas: quiénes litigan cada tipo de juicio.
SQL_ESTADISTICAS_EMPRESAS = """
    DROP TABLE IF EXISTS main.tipo_empresa;
    CREATE TABLE tipo_empresa AS
        SELECT en.tipo_juicio_norm AS tipo, ee.empresa_id, ee.rol, COUNT(*) AS avisos
        FROM entradas_empresas ee JOIN entradas en ON en.id = ee.entrada_id
        WHERE en.tipo_juicio_norm IS NOT NULL
        GROUP BY 1, 2, 3;
    CREATE INDEX idx_tipo_empresa ON tipo_empresa (tipo, rol, avisos DESC);
"""


def build_estadisticas(conn, log):
    log("Estadísticas de órganos y tipos de juicio...")
    conn.commit()  # temp_store no se puede cambiar con una transacción abierta
    conn.execute("PRAGMA temp_store=FILE")
    # Bases armadas antes de tipo_acuerdo (nov. 2026): la columna queda vacía
    # y los conteos de prevenciones, negaciones y admisiones dan 0.
    if not any(c[1] == "tipo_acuerdo" for c in conn.execute("PRAGMA table_info(entradas)")):
        conn.execute("ALTER TABLE entradas ADD COLUMN tipo_acuerdo TEXT")
    conn.executescript(SQL_ESTADISTICAS)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'entradas_empresas'").fetchone():
        conn.executescript(SQL_ESTADISTICAS_EMPRESAS)
    conn.commit()


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


def build_agregados_empresas(conn, log):
    log("Agregados de empresas...")
    conn.executescript(SQL_AGREGADOS_EMPRESAS)
    conn.commit()


def _init_empresas_worker(db_path):
    _W["db"] = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _empresas_de_rango(bounds):
    desde, hasta = bounds
    filas_empresas, filas_partes = [], []
    for entrada_id, actora, demandada in _W["db"].execute(
            "SELECT id, actora, demandada FROM entradas WHERE id BETWEEN ? AND ?", (desde, hasta)):
        emp_a, otras_a = emp.partes(actora)
        emp_d, otras_d = emp.partes(demandada)
        for rol, lista in (("actora", emp_a), ("demandada", emp_d)):
            for nucleo, nombre in lista:
                filas_empresas.append((entrada_id, rol, nucleo, nombre))
        if emp_a or emp_d:
            filas_partes.append((entrada_id, int(otras_a), int(otras_d)))
    return filas_empresas, filas_partes


def recalcular_empresas(output, log, workers=None):
    """--solo-empresas: rearma las tablas de empresas sobre una base ya
    armada, sin volver a cargar los 34M avisos (~10 min en vez de ~1 h).
    Sirve cuando cambia la detección en scraper/empresas.py."""
    conn = sqlite3.connect(output)
    # WAL mientras dura: los procesos leen la base mientras este escribe.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    hilos_sqlite(conn)
    cache_sqlite(conn)
    conn.executescript("""
        DROP TABLE IF EXISTS empresas_fts;
        DROP TABLE IF EXISTS entradas_empresas;
        DROP TABLE IF EXISTS empresas;
        DROP TABLE IF EXISTS entradas_partes;
        DROP TABLE IF EXISTS emp_stage;
        CREATE TABLE emp_stage (entrada_id INTEGER, rol TEXT, nucleo TEXT, nombre TEXT);
        CREATE TABLE entradas_partes (
            entrada_id      INTEGER PRIMARY KEY,
            otras_actora    INTEGER NOT NULL,
            otras_demandada INTEGER NOT NULL
        );
    """)
    max_id = conn.execute("SELECT MAX(id) FROM entradas").fetchone()[0] or 0
    paso = 200_000
    rangos = [(a, min(a + paso - 1, max_id)) for a in range(1, max_id + 1, paso)]
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    log(f"Recalculando empresas de {max_id:,} avisos en {len(rangos)} rangos...")
    hechos = 0
    with Pool(workers, initializer=_init_empresas_worker, initargs=(str(output),)) as pool:
        for filas_empresas, filas_partes in pool.imap_unordered(_empresas_de_rango, rangos):
            conn.executemany("INSERT INTO emp_stage VALUES (?, ?, ?, ?)", filas_empresas)
            conn.executemany("INSERT INTO entradas_partes VALUES (?, ?, ?)", filas_partes)
            conn.commit()
            hechos += 1
            if hechos % 20 == 0:
                log(f"  {hechos}/{len(rangos)} rangos")
    build_empresas(conn, log)
    conn.commit()
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()


MIN_AVISOS_PERSONA = 2


def _init_personas_worker(db_path):
    _W["db"] = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _personas_de_rango(bounds):
    """Agrega por (núcleo, nombre) las personas de un rango de avisos:
    [avisos, como actora, como demandada, primer boletín, último boletín].
    Se agrega acá y no aviso por aviso en el proceso principal: así viajan
    y se escriben cientos de miles de filas en vez de ~60M."""
    desde, hasta = bounds
    acum = {}
    for actora, demandada, fecha in _W["db"].execute("""
            SELECT e.actora, e.demandada, b.fecha_iso
            FROM entradas e JOIN boletines b ON b.id = e.boletin_id
            WHERE e.id BETWEEN ? AND ?""", (desde, hasta)):
        for i, campo in ((1, actora), (2, demandada)):
            # Una misma persona dos veces en el mismo campo cuenta una vez.
            for clave in set(pers.personas(campo)):
                a = acum.get(clave)
                if a is None:
                    a = acum[clave] = [0, 0, 0, fecha, fecha]
                a[0] += 1
                a[i] += 1
                a[3] = min(filter(None, (a[3], fecha)), default=None)
                a[4] = max(filter(None, (a[4], fecha)), default=None)
    return [(n, nombre, *a) for (n, nombre), a in acum.items()]


def build_personas(output, log, workers=None, desde_carga=False):
    """Tablas personas / personas_fts para /personas del server (noindex).

    A diferencia de empresas no hay tabla de unión aviso-persona (serían
    ~60M filas): la ficha de una persona busca sus avisos en vivo en
    entradas_fts y filtra por el nombre exacto. Solo entran los nombres con
    al menos MIN_AVISOS_PERSONA avisos: los restos de parseo casi siempre
    aparecen una sola vez. El nombre visible es la variante más frecuente."""
    conn = sqlite3.connect(output)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    hilos_sqlite(conn)
    cache_sqlite(conn)
    if desde_carga:
        # Build completo con --personas: la tabla intermedia ya se llenó
        # durante la carga.
        conn.executescript("DROP TABLE IF EXISTS personas_fts; DROP TABLE IF EXISTS personas;")
        log("Personas (juntadas durante la carga)...")
    else:
        conn.executescript("""
            DROP TABLE IF EXISTS personas_fts;
            DROP TABLE IF EXISTS personas;
            DROP TABLE IF EXISTS personas_stage;
            CREATE TABLE personas_stage (nucleo TEXT, nombre TEXT, n INTEGER, act INTEGER, dem INTEGER,
                                         desde TEXT, hasta TEXT);
        """)
        max_id = conn.execute("SELECT MAX(id) FROM entradas").fetchone()[0] or 0
        paso = 200_000
        rangos = [(a, min(a + paso - 1, max_id)) for a in range(1, max_id + 1, paso)]
        workers = workers or max(1, (os.cpu_count() or 2) - 1)
        log(f"Personas de {max_id:,} avisos en {len(rangos)} rangos...")
        hechos = 0
        with Pool(workers, initializer=_init_personas_worker, initargs=(str(output),)) as pool:
            for filas in pool.imap_unordered(_personas_de_rango, rangos):
                conn.executemany("INSERT INTO personas_stage VALUES (?, ?, ?, ?, ?, ?, ?)", filas)
                conn.commit()
                hechos += 1
                if hechos % 20 == 0:
                    log(f"  {hechos}/{len(rangos)} rangos")

    agrupar_personas(conn, log)


def agrupar_personas(conn, log):
    """Arma personas y personas_fts desde personas_stage (en esta base o en
    un archivo adjunto)."""
    conn.executescript("""
        CREATE TABLE personas (
            id             INTEGER PRIMARY KEY,
            nucleo         TEXT UNIQUE NOT NULL,
            nombre         TEXT NOT NULL,
            slug           TEXT UNIQUE NOT NULL,
            avisos         INTEGER NOT NULL,
            como_actora    INTEGER NOT NULL,
            como_demandada INTEGER NOT NULL,
            primer_boletin TEXT,
            ultimo_boletin TEXT
        );
        CREATE TEMP TABLE variantes AS
            SELECT nucleo, nombre, SUM(n) AS n, SUM(act) AS act, SUM(dem) AS dem,
                   MIN(desde) AS desde, MAX(hasta) AS hasta
            FROM personas_stage GROUP BY nucleo, nombre;
    """)
    if conn.execute("SELECT 1 FROM main.sqlite_master WHERE name = 'personas_stage'").fetchone():
        conn.execute("DROP TABLE main.personas_stage")

    def por_nucleo():
        actual, fila = None, None
        for nucleo, nombre, n, act, dem, desde, hasta in conn.execute(
                "SELECT * FROM variantes ORDER BY nucleo, n DESC, nombre"):
            if nucleo != actual:
                if fila:
                    yield fila
                # La primera variante (la más frecuente) da el nombre visible.
                actual, fila = nucleo, [nucleo, nombre, 0, 0, 0, desde, hasta]
            fila[2] += n
            fila[3] += act
            fila[4] += dem
            fila[5] = min(filter(None, (fila[5], desde)), default=None)
            fila[6] = max(filter(None, (fila[6], hasta)), default=None)
        if fila:
            yield fila

    slugs = set()
    lote = []
    total = 0
    for nucleo, nombre, avisos, act, dem, desde, hasta in por_nucleo():
        if avisos < MIN_AVISOS_PERSONA:
            continue
        base = pers.slugify(nucleo)
        slug, k = base, 2
        while slug in slugs:
            slug, k = f"{base}-{k}", k + 1
        slugs.add(slug)
        lote.append((nucleo, nombre, slug, avisos, act, dem, desde, hasta))
        if len(lote) >= 50_000:
            conn.executemany("INSERT INTO personas (nucleo, nombre, slug, avisos, como_actora, como_demandada, "
                             "primer_boletin, ultimo_boletin) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", lote)
            total += len(lote)
            lote = []
    conn.executemany("INSERT INTO personas (nucleo, nombre, slug, avisos, como_actora, como_demandada, "
                     "primer_boletin, ultimo_boletin) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", lote)
    total += len(lote)
    conn.executescript("""
        DROP TABLE variantes;
        CREATE INDEX idx_personas_avisos ON personas (avisos DESC);
        CREATE VIRTUAL TABLE personas_fts USING fts5(
            nombre, content='personas', content_rowid='id', detail=none
        );
        INSERT INTO personas_fts(rowid, nombre) SELECT id, nombre FROM personas;
    """)
    conn.commit()
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()
    log(f"  {total:,} personas con al menos {MIN_AVISOS_PERSONA} avisos")


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
    etapa, base, staging, archivo = trabajo
    t0 = time.monotonic()
    Path(archivo).unlink(missing_ok=True)
    conn = sqlite3.connect(archivo)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    hilos_sqlite(conn)
    cache_sqlite(conn, 0.5)
    conn.execute("ATTACH DATABASE ? AS b", (f"file:{base}?mode=ro",))
    if Path(staging).exists():
        conn.execute("ATTACH DATABASE ? AS st", (f"file:{staging}?mode=ro",))
    nada = lambda msg: None
    if etapa == "fts":
        # Sin contenido propio: en la base se define con content='entradas'
        # y se le copian estas tablas internas (_data, _idx, _docsize).
        conn.executescript("""
            CREATE VIRTUAL TABLE entradas_fts USING fts5(
                actora, demandada, tipo_juicio, content='', detail=none
            );
            INSERT INTO entradas_fts(rowid, actora, demandada, tipo_juicio)
                SELECT id, actora, demandada, tipo_juicio FROM b.entradas;
            INSERT INTO entradas_fts(entradas_fts) VALUES('optimize');
        """)
    elif etapa == "empresas":
        build_empresas(conn, nada)
    elif etapa == "estadisticas":
        conn.execute("PRAGMA temp_store=FILE")
        conn.executescript(SQL_ESTADISTICAS)
    elif etapa == "personas":
        agrupar_personas(conn, nada)
        conn = sqlite3.connect(archivo)
    conn.commit()
    conn.close()
    return etapa, time.monotonic() - t0


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
        for sufijo in ("data", "idx", "docsize"):
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
    la búsqueda por texto. Empresas, personas y estadísticas se rearman
    enteras desde el estado (<base>.estado.sqlite: todas las detecciones de
    empresas y personas, también las que aparecen una sola vez), así el
    resultado es el mismo que el de un build completo. Corren en paralelo y
    son lo que más tarda."""
    estado = output.with_name(output.name + ".estado.sqlite")
    if not output.exists() or not estado.exists():
        sys.exit(f"--incremental necesita {output} y {estado}: correr antes un build completo con este código.")
    log(f"Sumando a {output}...")
    conn = sqlite3.connect(output)
    conn.execute("PRAGMA synchronous=OFF")
    hilos_sqlite(conn)
    cache_sqlite(conn)
    conn.execute("PRAGMA temp_store=MEMORY")
    max_id_antes = conn.execute("SELECT COALESCE(MAX(id), 0) FROM entradas").fetchone()[0]
    loaded = {r[0] for r in conn.execute("SELECT id FROM boletines WHERE n_entradas > 0")}
    runs = list(reversed(runs))
    for csv_path in runs:
        index_path = csv_path.parent / "index.json"
        if index_path.exists():
            load_index(conn, index_path)
    conn.execute("ATTACH DATABASE ? AS st", (str(estado),))
    total = 0
    for csv_path in runs:
        log(f"  {csv_path.parent.name}...")
        n = load_entradas(conn, csv_path, loaded, log, None, workers=args.workers, tmp_dir=str(output.parent),
                          con_empresas=args.empresas, con_personas=args.personas)
        total += n
        log(f"    {n} entradas nuevas")
    conn.commit()
    conn.execute("DETACH DATABASE st")
    if not total:
        log("No hay boletines nuevos.")
        conn.close()
        return

    log("Rangos y búsqueda por texto de lo nuevo...")
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
    populate_meta(conn)
    conn.commit()
    conn.close()

    etapas = [e for e, si in (("empresas", args.empresas), ("estadisticas", args.estadisticas),
                              ("personas", args.personas)) if si]
    if etapas:
        log(f"Etapas en paralelo: {', '.join(etapas)}...")
        trabajos = [(e, str(output), str(estado), str(output.with_name(f"{output.name}.{e}.sqlite")))
                    for e in etapas]
        with Pool(min(len(trabajos), ETAPAS_A_LA_VEZ or len(trabajos))) as pool:
            for etapa, segundos in pool.imap_unordered(_etapa_paralela, trabajos):
                log(f"  {etapa}: {segundos:.0f} s")
        conn = sqlite3.connect(output)
        conn.execute("PRAGMA synchronous=OFF")
        hilos_sqlite(conn)
        cache_sqlite(conn)
        for etapa, _, _, archivo in trabajos:
            _juntar(conn, etapa, archivo, log)
            os.remove(archivo)
        if args.estadisticas and args.empresas:
            conn.executescript(SQL_ESTADISTICAS_EMPRESAS)
        if args.sivep:
            build_sivep(conn, log, args.sivep)
        conn.commit()
        conn.close()
    if args.empresas or args.personas:
        build_vocabulario(output, log)
    log(f"Total: {total} entradas nuevas.")
    # Sin compactar: las tablas rearmadas dejan páginas libres que el
    # próximo incremental reusa. Compactar 20 GB tarda ~7 min.
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
    parser.add_argument("--agregados-empresas", action="store_true",
                        help="Rearmar solo los agregados de /empresas/:slug de una base ya armada (--output)")
    parser.add_argument("--solo-empresas", action="store_true",
                        help="Rearmar solo las tablas de empresas de una base ya armada (--output)")
    parser.add_argument("--empresas", action="store_true",
                        help="Tablas de empresas que son parte (sección /empresas del server)")
    parser.add_argument("--personas", action="store_true",
                        help="Tabla de personas físicas que son parte (sección /personas del server, noindex)")
    parser.add_argument("--solo-personas", action="store_true",
                        help="Rearmar solo la tabla de personas de una base ya armada (--output)")
    parser.add_argument("--workers", type=int,
                        help="Procesos para la carga y las personas (por defecto, todos los núcleos menos uno)")
    parser.add_argument("--estadisticas", action="store_true",
                        help="Tablas de desempeño por órgano y de tipos de juicio (/salas, /juzgados, /tipos)")
    parser.add_argument("--solo-estadisticas", action="store_true",
                        help="Rearmar solo las estadísticas de una base ya armada (--output)")
    parser.add_argument("--sivep",
                        help="sivep.sqlite con la tabla enlaces (scraper/sivep_enlace.py): "
                             "agrega los enlaces a versiones públicas de sentencias")
    parser.add_argument("--incremental", action="store_true",
                        help="Sumar a una base ya armada (--output) solo los boletines que no tiene. Requiere "
                             "el archivo <base>.estado.sqlite que deja el build completo")
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

    output = Path(args.output)
    # Cada línea con el tiempo transcurrido, para ver qué etapa pesa.
    t_inicio = time.monotonic()
    log = lambda msg: print(f"[{time.monotonic() - t_inicio:6.0f} s] {msg}", file=sys.stderr)

    if args.agregados_empresas:
        conn = sqlite3.connect(output)
        cache_sqlite(conn)
        conn.execute("PRAGMA temp_store=FILE")
        hilos_sqlite(conn)
        build_agregados_empresas(conn, log)
        conn.close()
        log(f"Listo: {output}")
        return
    if args.solo_empresas:
        recalcular_empresas(output, log, workers=args.workers)
        log(f"Listo: {output}")
        return
    if args.solo_personas:
        build_personas(output, log, workers=args.workers)
        log(f"Listo: {output}")
        return
    if args.solo_estadisticas:
        conn = sqlite3.connect(output)
        cache_sqlite(conn)
        hilos_sqlite(conn)
        build_estadisticas(conn, log)
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
        parser.error("--dir es obligatorio (salvo con --solo-empresas / --solo-personas / --solo-vocabulario / "
                     "--solo-estadisticas / --solo-sivep / --agregados-empresas)")
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
    if args.empresas:
        conn.execute("CREATE TABLE st.emp_stage (entrada_id INTEGER, rol TEXT, nucleo TEXT, nombre TEXT)")
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
    if args.personas:
        # Las personas se juntan durante la carga (ver _parse_bloque).
        conn.execute("CREATE TABLE st.personas_stage (nucleo TEXT, nombre TEXT, n INTEGER, act INTEGER, "
                     "dem INTEGER, desde TEXT, hasta TEXT)")
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
    etapas = ["fts"]
    if args.empresas:
        etapas.append("empresas")
    if args.estadisticas:
        etapas.append("estadisticas")
    if args.personas:
        etapas.append("personas")
    log(f"Etapas en paralelo: {', '.join(etapas)}...")
    trabajos = [(e, str(output), str(staging), str(output.with_name(f"{output.name}.{e}.sqlite"))) for e in etapas]
    with Pool(min(len(trabajos), ETAPAS_A_LA_VEZ or len(trabajos))) as pool:
        for etapa, segundos in pool.imap_unordered(_etapa_paralela, trabajos):
            log(f"  {etapa}: {segundos:.0f} s")

    conn = sqlite3.connect(output)
    conn.execute("PRAGMA synchronous=OFF")
    hilos_sqlite(conn)
    cache_sqlite(conn)
    for etapa, _, _, archivo in trabajos:
        _juntar(conn, etapa, archivo, log)
        os.remove(archivo)
    # Las tablas intermedias quedan como estado para --incremental.
    if staging.exists():
        os.replace(staging, output.with_name(output.name + ".estado.sqlite"))
    if args.estadisticas and args.empresas:
        conn.executescript(SQL_ESTADISTICAS_EMPRESAS)
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
