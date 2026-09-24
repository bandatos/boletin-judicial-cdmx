#!/usr/bin/env python3
"""
cruce_qqw.py — Cruza las empresas que aparecen como parte en el Boletín
Judicial con las empresas de QuiénEsQuién.Wiki (proveedores del gobierno
federal: Compranet, IMSS, etc.) por nombre normalizado.

No hay RFC en el boletín, así que la única llave es el nombre. Se compara el
"núcleo" del nombre: sin acentos, sin puntuación y sin la forma societaria
("S.A. de C.V.", "Sociedad Anónima de Capital Variable", "SOFOM ENR"...),
que se escribe de mil maneras en ambas fuentes. Un match por nombre no es
prueba de que sea la misma empresa: el resultado es una lista de candidatos
para revisar, no una afirmación.

Uso:
    python cruces/cruce_qqw.py --db boletin.sqlite --qqw cruces/qqw_empresas.jsonl.gz \\
        --out cruces/cruce_qqw.csv
"""

import argparse
import csv
import gzip
import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
from empresas import normalizar, nucleo_valido, partes_empresa  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--qqw", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    log = lambda m: print(m, file=sys.stderr)

    # QuiénEsQuién: núcleo -> empresas (nombre y variantes)
    qqw = defaultdict(list)
    with gzip.open(args.qqw, "rt") as f:
        for line in f:
            e = json.loads(line)
            nombres = [e["name"]] + [o["name"] for o in e.get("other_names") or []]
            for n in {normalizar(x) for x in nombres}:
                if nucleo_valido(n):
                    qqw[n].append(e)
    log(f"QQW: {len(qqw)} núcleos de nombre")

    # Boletín: núcleo -> conteo de avisos, rol, ejemplo de nombre tal cual
    bol = defaultdict(lambda: {"avisos": 0, "actora": 0, "demandada": 0, "ejemplo": None,
                               "tipos": defaultdict(int), "primero": None, "ultimo": None})
    conn = sqlite3.connect(args.db)
    q = """SELECT e.actora, e.demandada, e.tipo_juicio_norm, b.fecha_iso
           FROM entradas e JOIN boletines b ON b.id = e.boletin_id"""
    for i, (actora, demandada, tipo, fecha) in enumerate(conn.execute(q)):
        for rol, campo in (("actora", actora), ("demandada", demandada)):
            for parte in partes_empresa(campo):
                n = normalizar(parte)
                if not nucleo_valido(n):
                    continue
                d = bol[n]
                d["avisos"] += 1
                d[rol] += 1
                d["ejemplo"] = d["ejemplo"] or parte.strip()
                d["tipos"][tipo or "Sin tipo"] += 1
                if fecha:
                    d["primero"] = min(d["primero"] or fecha, fecha)
                    d["ultimo"] = max(d["ultimo"] or fecha, fecha)
        if i % 2_000_000 == 0 and i:
            log(f"  {i:,} avisos leídos")
    log(f"Boletín: {len(bol)} empresas distintas (por núcleo de nombre)")

    filas = []
    for n, d in bol.items():
        if n not in qqw:
            continue
        cand = qqw[n]
        monto = sum((c.get("contract_amount") or {}).get("supplier") or 0 for c in cand)
        contratos = sum((c.get("contract_count") or {}).get("supplier") or 0 for c in cand)
        tipos = sorted(d["tipos"].items(), key=lambda x: -x[1])[:3]
        filas.append({
            "nucleo": n,
            "nombre_boletin": d["ejemplo"],
            "nombre_qqw": " | ".join(sorted({c["name"] for c in cand}))[:300],
            "qqw_ids": " ".join(sorted({c["id"] for c in cand}))[:300],
            "candidatos_qqw": len(cand),
            "avisos": d["avisos"],
            "como_actora": d["actora"],
            "como_demandada": d["demandada"],
            "tipos_juicio": "; ".join(f"{t} ({k})" for t, k in tipos),
            "primer_boletin": d["primero"],
            "ultimo_boletin": d["ultimo"],
            "contratos_qqw": contratos,
            "monto_contratos_qqw": round(monto, 2),
        })
    filas.sort(key=lambda r: -r["avisos"])

    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0].keys()) if filas else ["nucleo"])
        w.writeheader()
        w.writerows(filas)

    log(f"Coinciden: {len(filas)} empresas "
        f"({len(filas) / max(len(bol), 1) * 100:.1f}% de las del boletín). CSV: {args.out}")


if __name__ == "__main__":
    main()
