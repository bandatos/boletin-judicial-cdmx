#!/usr/bin/env python3
"""
PoC — boletin-judicial-cdmx
Descarga el índice de un rango de fechas, baja un PDF y parsea entradas.
Salida: CSV a stdout, log a stderr.

Uso:
    python poc.py [fecha_inicio] [fecha_fin]
    python poc.py 2026-06-09 2026-06-10
"""

import re
import sys
import subprocess
import csv
from pathlib import Path
import requests
from bs4 import BeautifulSoup
import urllib3

urllib3.disable_warnings()

BASE_URL = "https://consultabpj.poderjudicialcdmx.gob.mx:2096"

# ── Index ─────────────────────────────────────────────────────────────────────

def get_session():
    s = requests.Session()
    r = s.get(f"{BASE_URL}/consultaboletinpjcdmx", verify=False)
    token = BeautifulSoup(r.text, "html.parser").find("meta", {"name": "csrf-token"})["content"]
    return s, token


def fetch_index(session, token, fecha_inicio, fecha_fin):
    """Devuelve lista de dicts: {id, fecha_raw, pdf_url}"""
    r = session.post(
        f"{BASE_URL}/consultaboletinpjcdmx/filtrar",
        data={"_token": token, "fechainicial": fecha_inicio, "fechafinal": fecha_fin},
        headers={"Referer": f"{BASE_URL}/consultaboletinpjcdmx"},
        verify=False,
    )
    soup = BeautifulSoup(r.text, "html.parser")
    boletines = []

    for modal in soup.find_all("div", class_="modal"):
        mid = modal.get("id", "")
        m = re.search(r"boletinexterno(\d+)", mid)
        if not m:
            continue
        boletin_id = int(m.group(1))

        title_el = modal.find("h5", class_="modal-title")
        fecha_raw = title_el.text.strip() if title_el else ""

        embed = modal.find("embed")
        pdf_url = None
        if embed:
            src = embed.get("src", "")
            pm = re.search(r"(https://gestordocumental[^#]+\.pdf)", src)
            if pm:
                pdf_url = pm.group(1)

        boletines.append({"id": boletin_id, "fecha_raw": fecha_raw, "pdf_url": pdf_url})

    return boletines


# ── Downloader ────────────────────────────────────────────────────────────────

def download_pdf(pdf_url, dest):
    r = requests.get(pdf_url, stream=True)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)


# ── Parser ────────────────────────────────────────────────────────────────────

SECTION_HEADERS = {
    "TRIBUNAL DE DISCIPLINA JUDICIAL",
    "AVISOS",
    "SALAS",
    "JUZGADOS DE LO CIVIL",
    "JUZGADOS DE LO FAMILIAR",
    "JUZGADOS CIVILES DE PROCESO ORAL",
    "JUZGADOS DE PROCESO ORAL EN MATERIA FAMILIAR",
    "UNIDADES DE GESTIÓN JUDICIAL",
    "JUZGADOS DE TUTELA DE DERECHOS HUMANOS",
    "TRIBUNALES EN MATERIA LABORAL",
    "SUSCRIPCIÓN AL BOLETÍN JUDICIAL",
    "EDICTOS",
}

SKIP_LINES = {
    "SOLO CONSULTA", "SIN ACUERDOS", "BOLETÍN JUDICIAL",
    "DEL PODER JUDICIAL DE LA CIUDAD DE MÉXICO",
    "NO PUBLICADOS", "AUDIENCIA",
}

RE_JUZGADO = re.compile(
    r"^((?:PRIMERO?|SEGUNDO|TERCERO|CUARTO|QUINTO|SEXTO|SÉPTIMO|OCTAVO|NOVENO|"
    r"DÉCIMO(?:\s+(?:PRIMERO?|SEGUNDO|TERCERO|CUARTO|QUINTO|SEXTO|SÉPTIMO|OCTAVO|NOVENO))?|"
    r"VIGÉSIMO(?:\s+(?:PRIMERO?|SEGUNDO|TERCERO))?|TRIGÉSIMO|CUADRAGÉSIMO|QUINCUAGÉSIMO)"
    r"\s+DE\s+LO\s+\w+(?:\s+\w+)*)$"
)
RE_SECRETARIA = re.compile(r"^SECRETAR[IÍ]A\b")
RE_ACUERDOS = re.compile(r"^ACUERDOS DEL\s+(.+)$")

# Terminator: Núm. Exp. NNNN/YYYY [optional suffix].
# Suffixes observed: Tomo II/III/VI, Segundo/Tercer/Séptimo Tomo, Legajo, Amparo, Expedientillo
RE_ENTRY_END = re.compile(
    r"Núm\.\s+Exp\.\s+"
    r"(\d+/\d+"
    r"(?:\s+(?:Segundo|Tercer|Cuarto|Quinto|Sexto|Séptimo|Octavo|Noveno)\s+Tomo"
    r"|\s+Tomo\s+[IVXLC]+"
    r"|\s+(?:Legajo|Amparo|Expedientillo)"
    r")?)"
    r"\s*\.",
    re.IGNORECASE,
)

RE_VS = re.compile(r"\s+vs\.\s+", re.IGNORECASE)

# Tipo de juicio + num_acdos al final de la entrada, antes de Núm. Exp.
# Patrón: "[Demandada]. TIPO [M.] N Acdo(s)."
RE_TIPO_ACDOS = re.compile(
    r"\.\s+(.+?)\s+(\d+)\s+(?:Acdos?|Audiencias?)\.\s*$",
    re.IGNORECASE | re.DOTALL,
)


def pdf_to_text(pdf_path):
    res = subprocess.run(
        ["pdftotext", str(pdf_path), "-"],
        capture_output=True, text=True, errors="replace",
    )
    return res.stdout


def parse_pdf(text, boletin_id, fecha_raw):
    entries = []
    ctx = {"seccion": None, "juzgado": None, "secretaria": None, "fecha_acuerdo": None}
    block = []

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line or len(line) < 3:
            # Línea vacía: descartar bloque si no contiene expediente
            if block and not RE_ENTRY_END.search(" ".join(block)):
                block = []
            continue

        if line in SECTION_HEADERS:
            ctx.update(seccion=line, juzgado=None, secretaria=None)
            block = []
            continue

        if line in SKIP_LINES:
            continue

        if RE_JUZGADO.match(line):
            ctx.update(juzgado=line, secretaria=None)
            block = []
            continue

        if RE_SECRETARIA.match(line):
            ctx["secretaria"] = line
            block = []
            continue

        m_ac = RE_ACUERDOS.match(line)
        if m_ac:
            ctx["fecha_acuerdo"] = m_ac.group(1).strip()
            block = []
            continue

        block.append(line)
        joined = " ".join(block)

        if RE_ENTRY_END.search(joined) and joined.rstrip().endswith("."):
            entry = _parse_entry(joined, boletin_id, fecha_raw, ctx)
            if entry:
                entries.append(entry)
            block = []

    return entries


def _parse_entry(text, boletin_id, fecha_raw, ctx):
    m_exp = RE_ENTRY_END.search(text)
    if not m_exp:
        return None

    expediente = m_exp.group(1).strip()
    pre = text[:m_exp.start()].strip()

    partes = RE_VS.split(pre, maxsplit=1)
    actora = partes[0].strip()

    if len(partes) < 2:
        return _row(boletin_id, fecha_raw, ctx, actora, None, None, None, expediente, text)

    resto = partes[1].strip()

    m_tipo = RE_TIPO_ACDOS.search(resto)
    if m_tipo:
        demandada = resto[: m_tipo.start() + 1].strip()
        tipo_juicio = m_tipo.group(1).strip()
        num_acdos = int(m_tipo.group(2))
    else:
        demandada = resto
        tipo_juicio = None
        num_acdos = None

    return _row(boletin_id, fecha_raw, ctx, actora, demandada, tipo_juicio, num_acdos, expediente, text)


def _row(boletin_id, fecha_raw, ctx, actora, demandada, tipo_juicio, num_acdos, expediente, raw_text):
    return {
        "boletin_id": boletin_id,
        "fecha": fecha_raw,
        **ctx,
        "actora": actora,
        "demandada": demandada,
        "tipo_juicio": tipo_juicio,
        "num_acdos": num_acdos,
        "expediente": expediente,
        "raw_text": raw_text,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    fecha_inicio = sys.argv[1] if len(sys.argv) > 1 else "2026-06-09"
    fecha_fin = sys.argv[2] if len(sys.argv) > 2 else "2026-06-10"
    max_boletines = int(sys.argv[3]) if len(sys.argv) > 3 else 1

    log = lambda msg: print(msg, file=sys.stderr)

    log(f"Obteniendo índice {fecha_inicio} → {fecha_fin}...")
    session, token = get_session()
    boletines = fetch_index(session, token, fecha_inicio, fecha_fin)
    log(f"  {len(boletines)} boletín(es) encontrado(s)")

    all_entries = []

    for b in boletines[:max_boletines]:
        log(f"\nBoletín {b['id']} — {b['fecha_raw']}")

        if not b["pdf_url"]:
            log("  Sin URL de PDF, saltando.")
            continue

        pdf_path = Path(f"/tmp/boletin_{b['id']}.pdf")
        if not pdf_path.exists():
            log("  Descargando PDF...")
            download_pdf(b["pdf_url"], pdf_path)
            log(f"  {pdf_path.stat().st_size / 1e6:.1f} MB descargados")
        else:
            log("  PDF ya en caché.")

        log("  Extrayendo texto con pdftotext...")
        text = pdf_to_text(pdf_path)

        log("  Parseando entradas...")
        entries = parse_pdf(text, b["id"], b["fecha_raw"])
        log(f"  {len(entries)} entradas encontradas")
        all_entries.extend(entries)

    log(f"\nTotal: {len(all_entries)} entradas")

    if all_entries:
        fields = list(all_entries[0].keys())
        writer = csv.DictWriter(sys.stdout, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_entries)


if __name__ == "__main__":
    main()
