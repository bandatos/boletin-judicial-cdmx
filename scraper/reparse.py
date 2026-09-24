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

    # Se escribe al CSV a medida que llega cada boletín (imap, en orden de
    # boletín) en vez de juntar todo en memoria: con el histórico completo
    # (~30M entradas) la lista no entra en RAM.
    csv_path = data_dir / "entradas.csv"
    tmp_path = csv_path.with_suffix(".csv.part")
    total = 0
    done = 0
    writer = None
    with open(tmp_path, "w", newline="") as f, Pool(workers) as pool:
        for boletin_id, entries in pool.imap(_parse_one, jobs, chunksize=4):
            if entries and writer is None:
                writer = csv.DictWriter(f, fieldnames=list(entries[0].keys()), extrasaction="ignore")
                writer.writeheader()
            if entries:
                writer.writerows(entries)
            total += len(entries)
            done += 1
            if done % 50 == 0 or done == len(jobs):
                log(f"  {done}/{len(jobs)} boletines procesados ({total} entradas hasta ahora)")

    log(f"\nTotal: {total} entradas")

    if not total:
        tmp_path.unlink()
        log("Sin entradas. Abortando.")
        sys.exit(1)

    # Recién acá se reemplaza el CSV anterior: si el reparseo se corta a la
    # mitad, el entradas.csv previo queda intacto.
    tmp_path.rename(csv_path)
    log(f"CSV guardado: {csv_path} ({total} filas)")

if __name__ == "__main__":
    main()
