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
import unicodedata
from collections import defaultdict

# Formas societarias y colas habituales, ya normalizadas (minúsculas, sin
# acentos ni puntuación). Se sacan del final del nombre, repetidamente.
FORMAS = [
    r"sociedad anonima promotora de inversion de capital variable",
    r"sociedad anonima bursatil de capital variable",
    r"sociedad anonima de capital variable",
    r"sociedad de responsabilidad limitada de capital variable",
    r"sociedad de responsabilidad limitada",
    r"sociedad cooperativa de responsabilidad limitada",
    r"sociedad financiera de objeto multiple entidad no regulada",
    r"sociedad financiera de objeto multiple entidad regulada",
    r"sociedad financiera de objeto multiple",
    r"sociedad nacional de credito",
    r"sociedad anonima",
    r"sociedad civil",
    r"asociacion civil",
    r"institucion de banca multiple",
    r"institucion de banca de desarrollo",
    r"grupo financiero \w+(?: \w+)?",
    r"s a p i de c v", r"s a b de c v", r"s de r l de c v", r"s de r l mi",
    r"s de r l", r"s a de c v", r"s c de r l", r"s de s s", r"s en c",
    r"sapi de cv", r"sab de cv", r"sa de cv", r"s de rl de cv", r"s de rl",
    r"sofom e n r", r"sofom e r", r"sofom enr", r"sofom er", r"sofom",
    r"s n c", r"snc", r"s a", r"sa", r"s c", r"sc", r"a c", r"ac",
    r"de c v", r"de cv", r"c v", r"cv", r"i a p", r"iap",
]
RE_FORMA_FINAL = re.compile(r"(?:\s+(?:" + "|".join(FORMAS) + r"))+\s*$")

# Detecta que una parte del boletín es una empresa (y no una persona física).
RE_ES_EMPRESA = re.compile(
    r"\bS\.?\s?A\.?(?:\s?P\.?\s?I\.?|\s?B\.?)?(?:\s+de\s+C\.?\s?V\.?)?(?=$|[\s,.;)])"
    r"|\bS\.?\s?de\s?R\.?\s?L\.?|\bS\.?\s?C\.?(?=$|[\s,.;)])|\bA\.?\s?C\.?(?=$|[\s,.;)])"
    r"|\bSofom\b|Sociedad\s+An[oó]nima|Sociedad\s+de\s+Responsabilidad|Sociedad\s+Civil"
    r"|Asociaci[oó]n\s+Civil|Instituci[oó]n\s+de\s+Banca|Sociedad\s+Financiera"
    r"|Sociedad\s+Nacional\s+de\s+Cr[eé]dito",
    re.IGNORECASE,
)

GENERICOS = {"banco", "grupo", "servicios", "inmobiliaria", "constructora", "comercializadora",
             "secreto", "desarrollos", "promotora", "operadora", "corporativo", "grupo industrial"}


def normalizar(nombre):
    s = unicodedata.normalize("NFD", str(nombre or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    s = re.sub(r"[^a-z0-9ñ ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = RE_FORMA_FINAL.sub("", s).strip()
    return s


def nucleo_valido(n):
    return n and len(n) >= 6 and n not in GENERICOS and len(n.split()) >= 1 and not n.isdigit()


def partes_empresa(campo):
    """Devuelve las razones sociales dentro de un campo actora/demandada."""
    if not campo or not RE_ES_EMPRESA.search(campo):
        return []
    trozos = re.split(r"\s+(?:y|e)\s+(?=[A-ZÁÉÍÓÚÑ])|,\s+(?=[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s)", campo)
    return [t for t in trozos if RE_ES_EMPRESA.search(t)]


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
