#!/usr/bin/env python3
"""
reparse.py — Parsea los .txt ya generados y escribe entradas.csv.
No descarga nada ni llama a pdftotext.

Uso:
    python reparse.py [data_dir]
    python reparse.py data/2026-06-10
"""
import csv
import json
import sys
from pathlib import Path

# Reutilizar parse_pdf del scraper principal
from poc import parse_pdf

def main():
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/2026-06-10")
    log = lambda msg: print(msg, file=sys.stderr)

    index_path = data_dir / "index.json"
    index = {b["id"]: b for b in json.load(open(index_path))}

    txt_files = sorted((data_dir / "pdfs").glob("boletin_*.txt"))
    log(f"{len(txt_files)} archivos .txt encontrados en {data_dir}/pdfs/")

    all_entries = []
    for txt_path in txt_files:
        boletin_id = int(txt_path.stem.replace("boletin_", ""))
        meta = index.get(boletin_id, {})
        fecha_raw = meta.get("fecha_raw", "")

        text = txt_path.read_text(encoding="utf-8", errors="replace")
        entries = parse_pdf(text, boletin_id, fecha_raw)
        all_entries.extend(entries)
        log(f"  {boletin_id} ({fecha_raw.strip()}): {len(entries)} entradas")

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
