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
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from normalize_tipo import normalize

# normalize() es una cascada de regex y era ~80% del tiempo de carga, pero
# hay pocos cientos de tipo_juicio distintos en millones de filas.
normalize_tipo = functools.lru_cache(maxsize=None)(normalize)

csv.field_size_limit(10**9)

RE_ANIO = re.compile(r"/(\d{4})\b")


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
            pagina      INTEGER  -- página del PDF del boletín (1 = primera)
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


def create_indexes(conn, indices_server=False):
    # Después de la carga y no en el schema: armar el índice de una vez es
    # mucho más rápido que mantenerlo fila por fila durante 30M inserts.
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_expediente
            ON entradas (juzgado, expediente);

        CREATE INDEX IF NOT EXISTS idx_anio
            ON entradas (anio);

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
]
COLS_CARGA = FIELDS + ["tipo_juicio_norm", "anio"]
BLOQUE = 64 * 1024 * 1024  # bytes de CSV por tarea

# Estado de cada proceso de la carga paralela (lo fija _init_worker).
_W = {}


def _init_worker(csv_path, col, loaded, en_rango, tmp_dir):
    _W.update(path=csv_path, col=col, loaded=loaded, en_rango=en_rango, tmp_dir=tmp_dir)


def _parse_bloque(bounds):
    """Lee y transforma las filas que EMPIEZAN dentro de [start, end) del CSV.
    Cada fila ocupa exactamente una línea (ningún campo lleva saltos de
    línea), así que se puede cortar el archivo en cualquier "\n"."""
    start, end = bounds
    col, loaded, en_rango = _W["col"], _W["loaded"], _W["en_rango"]
    idx = [col.get(k) for k in FIELDS]
    i_bol, i_tipo, i_exp = col["boletin_id"], col["tipo_juicio"], col["expediente"]

    lineas = []
    with open(_W["path"], "rb") as f:
        f.seek(start - 1)
        f.readline()  # termina la línea que empezó antes de `start`
        while f.tell() < end:
            linea = f.readline()
            if not linea:
                break
            lineas.append(linea.decode("utf-8"))

    filas, ids, salteadas = [], set(), 0
    for row in csv.reader(lineas):
        boletin_id = int(row[i_bol])
        if en_rango is not None and boletin_id not in en_rango:
            continue
        if boletin_id in loaded:
            salteadas += 1
            continue
        ids.add(boletin_id)
        m_anio = RE_ANIO.search(row[i_exp])
        filas.append(
            tuple((row[i] or None) if i is not None else None for i in idx)
            + (normalize_tipo(row[i_tipo] or None), int(m_anio.group(1)) if m_anio else None)
        )

    # El bloque se escribe en un SQLite propio en vez de devolver las filas:
    # pasarlas serializadas al proceso principal costaba casi tanto como
    # leerlas. El principal las copia con INSERT ... SELECT (en C).
    shard = os.path.join(_W["tmp_dir"], f"bloque_{start}.sqlite")
    c = sqlite3.connect(shard)
    c.execute("PRAGMA journal_mode=OFF")
    c.execute("PRAGMA synchronous=OFF")
    c.execute(f"CREATE TABLE filas ({', '.join(COLS_CARGA)})")
    c.executemany(f"INSERT INTO filas VALUES ({', '.join(['?'] * len(COLS_CARGA))})", filas)
    c.commit()
    c.close()
    return shard, len(filas), ids, salteadas


def load_entradas(conn, csv_path, loaded, log, en_rango=None, workers=None, tmp_dir=None):
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

    with open(csv_path, newline="") as f:
        col = {name: i for i, name in enumerate(next(csv.reader(f)))}
    with open(csv_path, "rb") as f:
        inicio_datos = len(f.readline())
    total_bytes = csv_path.stat().st_size
    bloques = [(s, min(s + BLOQUE, total_bytes)) for s in range(inicio_datos, total_bytes, BLOQUE)]

    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    counts = {"inserted": 0, "skipped": 0}
    new_ids = set()
    cols = ", ".join(COLS_CARGA)

    with tempfile.TemporaryDirectory(prefix="build_db_", dir=tmp_dir) as tmp, \
            Pool(workers, initializer=_init_worker,
                 initargs=(str(csv_path), col, frozenset(loaded), en_rango, tmp)) as pool:
        # Como mucho 2 bloques pendientes por proceso, para no llenar el
        # disco temporal si los procesos van más rápido que la copia.
        it = iter(bloques)
        pendientes = deque(pool.apply_async(_parse_bloque, (b,))
                           for b in itertools.islice(it, 2 * workers))
        while pendientes:
            shard, n, ids, salteadas = pendientes.popleft().get()
            siguiente = next(it, None)
            if siguiente:
                pendientes.append(pool.apply_async(_parse_bloque, (siguiente,)))
            if n:
                conn.execute("ATTACH DATABASE ? AS bloque", (shard,))
                conn.execute(f"INSERT INTO entradas ({cols}) SELECT {cols} FROM bloque.filas ORDER BY rowid")
                conn.commit()
                conn.execute("DETACH DATABASE bloque")
            os.remove(shard)
            new_ids.update(ids)
            counts["inserted"] += n
            counts["skipped"] += salteadas

    loaded |= new_ids
    if counts["skipped"]:
        log(f"    {counts['skipped']} entradas salteadas (boletines ya cargados de un run más nuevo)")
    return counts["inserted"]


def find_runs(data_dir, since=None, exclude=()):
    """Devuelve lista de (run_dir) ordenados por fecha."""
    runs = sorted(data_dir.glob("*/entradas.csv"))
    if since:
        runs = [r for r in runs if r.parent.name >= since]
    return [r for r in runs if r.parent.name not in exclude]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True, help="Directorio raíz de datos (data/)")
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
    parser.add_argument("--desde-fecha",
                        help="Solo boletines publicados desde esta fecha YYYY-MM-DD, "
                             "de cualquier run (a diferencia de --since, que filtra "
                             "por nombre del run)")
    args = parser.parse_args()

    data_dir = Path(args.dir)
    output = Path(args.output)
    log = lambda msg: print(msg, file=sys.stderr)

    runs = find_runs(data_dir, args.since, args.exclude)
    if not runs:
        log("No se encontraron datos.")
        sys.exit(1)

    log(f"Encontrados {len(runs)} runs en {data_dir}")
    log(f"Generando {output}...")

    output.unlink(missing_ok=True)
    conn = sqlite3.connect(output)
    # Archivo nuevo y descartable (si el build se corta se vuelve a correr):
    # sin journal ni fsync, con caché grande.
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA cache_size=-2000000")  # ~2 GB
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA locking_mode=EXCLUSIVE")

    create_schema(conn)

    # Del run más nuevo al más viejo: si un boletín está en varios, gana el
    # más reciente (parseado con la versión más nueva del parser).
    runs = list(reversed(runs))
    for csv_path in runs:
        index_path = csv_path.parent / "index.json"
        if index_path.exists():
            load_index(conn, index_path)

    en_rango = None
    if args.desde_fecha:
        conn.execute("DELETE FROM boletines WHERE fecha_iso IS NULL OR fecha_iso < ?",
                     (args.desde_fecha,))
        en_rango = {r[0] for r in conn.execute("SELECT id FROM boletines")}

    total = 0
    loaded = set()
    for csv_path in runs:
        run_dir = csv_path.parent
        log(f"  {run_dir.name}...")

        n = load_entradas(conn, csv_path, loaded, log, en_rango, tmp_dir=str(output.parent))
        total += n
        log(f"    {n} entradas")

    log("Índices...")
    create_indexes(conn, args.indices_server)
    populate_boletin_ranges(conn, log)
    populate_meta(conn)
    conn.commit()

    log(f"\nTotal: {total} entradas. Construyendo índice FTS5...")
    create_fts(conn)
    populate_fts(conn)
    conn.commit()

    log("Optimizando...")
    conn.execute("INSERT INTO entradas_fts(entradas_fts) VALUES('optimize')")
    conn.commit()
    if not args.sin_vacuum:
        log("Compactando...")
        conn.execute("VACUUM")
    conn.close()

    size_mb = output.stat().st_size / 1e6
    log(f"\nListo: {output} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
