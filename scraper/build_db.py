#!/usr/bin/env python3
"""
build_db.py — Genera boletin.sqlite desde los CSVs del scraper.

Uso:
    python build_db.py --dir data/ --output boletin.sqlite
    python build_db.py --dir data/ --output boletin.sqlite --since 2026-01-01
"""

import argparse
import csv
import json
import re
import sqlite3
import sys
from pathlib import Path

RE_ANIO = re.compile(r"/(\d{4})\b")


def create_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS boletines (
            id       INTEGER PRIMARY KEY,
            fecha    TEXT NOT NULL,
            pdf_url  TEXT,
            pages    INTEGER
        );

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
            expediente  TEXT,
            num_acdos   INTEGER,
            anio        INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_expediente
            ON entradas (juzgado, expediente);

        CREATE INDEX IF NOT EXISTS idx_anio
            ON entradas (anio);

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


def populate_fts(conn):
    conn.execute("""
        INSERT INTO entradas_fts(rowid, actora, demandada, tipo_juicio)
        SELECT id, actora, demandada, tipo_juicio FROM entradas
    """)


def load_index(conn, index_path):
    with open(index_path) as f:
        boletines = json.load(f)
    conn.executemany(
        "INSERT OR IGNORE INTO boletines (id, fecha, pdf_url) VALUES (?, ?, ?)",
        [(b["id"], b["fecha_raw"], b["pdf_url"]) for b in boletines],
    )


def load_entradas(conn, csv_path, log):
    inserted = 0
    skipped = 0
    fields = [
        "boletin_id", "juzgado", "sala", "secretaria",
        "actora", "demandada", "tipo_juicio", "expediente", "num_acdos",
    ]
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            # Saltar si el boletin_id ya está cargado
            expediente = row.get("expediente") or ""
            m_anio = RE_ANIO.search(expediente)
            anio = int(m_anio.group(1)) if m_anio else None
            rows.append(tuple(row.get(k) or None for k in fields) + (anio,))
        conn.executemany(
            f"INSERT INTO entradas ({', '.join(fields)}, anio) "
            f"VALUES ({', '.join(['?']*len(fields))}, ?)",
            rows,
        )
        inserted = len(rows)
    return inserted


def find_runs(data_dir, since=None):
    """Devuelve lista de (run_dir) ordenados por fecha."""
    runs = sorted(data_dir.glob("*/entradas.csv"))
    if since:
        runs = [r for r in runs if r.parent.name >= since]
    return runs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True, help="Directorio raíz de datos (data/)")
    parser.add_argument("--output", default="boletin.sqlite", help="Archivo SQLite de salida")
    parser.add_argument("--since", help="Solo incluir runs desde esta fecha YYYY-MM-DD")
    args = parser.parse_args()

    data_dir = Path(args.dir)
    output = Path(args.output)
    log = lambda msg: print(msg, file=sys.stderr)

    runs = find_runs(data_dir, args.since)
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

    total = 0
    for csv_path in runs:
        run_dir = csv_path.parent
        log(f"  {run_dir.name}...")

        index_path = run_dir / "index.json"
        if index_path.exists():
            load_index(conn, index_path)

        n = load_entradas(conn, csv_path, log)
        total += n
        log(f"    {n} entradas")

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
