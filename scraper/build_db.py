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
import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from normalize_tipo import normalize as normalize_tipo

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

        CREATE INDEX IF NOT EXISTS idx_expediente
            ON entradas (juzgado, expediente);

        CREATE INDEX IF NOT EXISTS idx_anio
            ON entradas (anio);

        CREATE INDEX IF NOT EXISTS idx_tipo_juicio_norm
            ON entradas (tipo_juicio_norm);

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


def load_index(conn, index_path):
    with open(index_path) as f:
        boletines = json.load(f)
    conn.executemany(
        "INSERT OR IGNORE INTO boletines (id, fecha, fecha_iso, pdf_url) VALUES (?, ?, ?, ?)",
        [(b["id"], b["fecha_raw"], fecha_iso(b["fecha_raw"]), b["pdf_url"]) for b in boletines],
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


def load_entradas(conn, csv_path, loaded, log, en_rango=None):
    """Carga las entradas de un run salteando los boletines que ya cargó un
    run anterior (`loaded`, se actualiza). Los runs se pisan entre sí (el
    histórico repite boletines de los runs de 2026) y sin esto cada boletín
    repetido aparecía dos veces. `en_rango`: si se pasa, solo se cargan
    esos boletines (filtro --desde-fecha)."""
    fields = [
        "boletin_id", "juzgado", "sala", "secretaria",
        "actora", "demandada", "tipo_juicio", "expediente", "num_acdos", "pagina",
    ]
    counts = {"inserted": 0, "skipped": 0}
    new_ids = set()

    def rows():
        with open(csv_path, newline="") as f:
            for row in csv.DictReader(f):
                boletin_id = int(row["boletin_id"])
                if en_rango is not None and boletin_id not in en_rango:
                    continue
                if boletin_id in loaded:
                    counts["skipped"] += 1
                    continue
                new_ids.add(boletin_id)
                expediente = row.get("expediente") or ""
                m_anio = RE_ANIO.search(expediente)
                anio = int(m_anio.group(1)) if m_anio else None
                tipo_juicio_norm = normalize_tipo(row.get("tipo_juicio") or None)
                counts["inserted"] += 1
                yield (
                    tuple(row.get(k) or None for k in fields)
                    + (tipo_juicio_norm, anio)
                )

    # Generador en vez de lista: el run histórico tiene ~13M filas.
    conn.executemany(
        f"INSERT INTO entradas ({', '.join(fields)}, tipo_juicio_norm, anio) "
        f"VALUES ({', '.join(['?']*len(fields))}, ?, ?)",
        rows(),
    )
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
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

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

        n = load_entradas(conn, csv_path, loaded, log, en_rango)
        total += n
        log(f"    {n} entradas")

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
    conn.execute("VACUUM")
    conn.close()

    size_mb = output.stat().st_size / 1e6
    log(f"\nListo: {output} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
