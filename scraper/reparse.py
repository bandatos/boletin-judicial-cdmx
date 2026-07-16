#!/usr/bin/env python3
"""
reparse.py — Parsea los .txt ya generados y escribe entradas.csv.
No descarga nada ni llama a pdftotext. Multiproceso: parse_pdf es puro
CPU-bound por archivo, así que se reparte entre todos los cores.

Uso:
    python reparse.py [data_dir] [--workers N]
    python reparse.py data/2026-06-10
"""
import csv
import json
import os
import sys
from multiprocessing import Pool
from pathlib import Path

# Reutilizar parse_pdf del scraper principal
from poc import parse_pdf


def _parse_one(args):
    txt_path, boletin_id, fecha_raw = args
    text = txt_path.read_text(encoding="utf-8", errors="replace")
    return boletin_id, parse_pdf(text, boletin_id, fecha_raw)


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--workers")]
    workers_arg = next((a for a in sys.argv[1:] if a.startswith("--workers")), None)
    workers = int(workers_arg.split("=")[1]) if workers_arg and "=" in workers_arg else (os.cpu_count() or 4)

    data_dir = Path(argv[0]) if argv else Path("data/2026-06-10")
    log = lambda msg: print(msg, file=sys.stderr)

    index_path = data_dir / "index.json"
    index = {b["id"]: b for b in json.load(open(index_path))}

    txt_files = sorted((data_dir / "pdfs").glob("boletin_*.txt"))
    log(f"{len(txt_files)} archivos .txt encontrados en {data_dir}/pdfs/ — {workers} workers")

    jobs = []
    for txt_path in txt_files:
        boletin_id = int(txt_path.stem.replace("boletin_", ""))
        fecha_raw = index.get(boletin_id, {}).get("fecha_raw", "")
        jobs.append((txt_path, boletin_id, fecha_raw))

    all_entries = []
    done = 0
    with Pool(workers) as pool:
        for boletin_id, entries in pool.imap_unordered(_parse_one, jobs, chunksize=4):
            all_entries.extend(entries)
            done += 1
            if done % 50 == 0 or done == len(jobs):
                log(f"  {done}/{len(jobs)} boletines procesados ({len(all_entries)} entradas hasta ahora)")

    log(f"\nTotal: {len(all_entries)} entradas")

    if not all_entries:
        log("Sin entradas. Abortando.")
        sys.exit(1)

    fields = list(all_entries[0].keys())
    csv_path = data_dir / "entradas.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_entries)
    log(f"CSV guardado: {csv_path} ({len(all_entries)} filas)")


if __name__ == "__main__":
    main()
