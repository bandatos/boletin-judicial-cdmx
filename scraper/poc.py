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
import json
from pathlib import Path
from datetime import datetime
import requests
from bs4 import BeautifulSoup

from normalize_tipo import expand_abbr, match_canonical_prefix, CANONICAL_TIPOS
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
            # Los boletines desde ~sept. 2022 se sirven desde el CDN
            # "gestordocumental...". Los anteriores usan una URL directa en
            # el propio dominio del portal (.../pdf/boletines/NNN.pdf). Antes
            # solo se reconocía el primer patrón, así que todo boletín viejo
            # quedaba marcado (incorrectamente) como "sin PDF, saltando".
            pm = re.search(r'(https://[^"#]+\.pdf)', src)
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

# Terminator: Núm. Exp. NNNN/YYYY [sufijo libre].
# El sufijo real es muy variable (nombre de juzgado, "Bis. N", "Tomo II",
# fecha, etc.) así que en vez de enumerar los casos observados se toma todo
# el texto hasta el primer punto, salvo que ese punto sea parte de una
# abreviatura común ("vs.", "Bis.") que aparece dentro del propio sufijo.
RE_ENTRY_END = re.compile(
    r"Núm\.\s+Exp\.\s+(\d+/\d+.*?)(?<!\bvs)(?<!\bBis)\.",
    re.IGNORECASE,
)

RE_VS = re.compile(r"\s+vs\.\s+", re.IGNORECASE)

# Tipo de juicio + num_acdos al final de la entrada, antes de Núm. Exp.
# Patrón: "[Demandada]. TIPO [M.] N Acdo(s)."
RE_TIPO_ACDOS = re.compile(
    r"\.\s+(.+?)\s+(\d+)\s+(?:Acdos?|Audiencias?)\.\s*$",
    re.IGNORECASE | re.DOTALL,
)

# La heurística de arriba corta en el primer punto, lo cual falla cuando la
# demandada tiene abreviaturas con punto ("De la O.", "S.A. de C.V."): el
# nombre se corta ahí y el resto (incluido el tipo de juicio real) termina
# adentro de "demandada", o al revés. Como alternativa más robusta: el tipo
# de juicio siempre empieza con una de estas palabras/abreviaturas conocidas
# y siempre termina en Civil/Mercantil/Familiar/Laboral (+ calificador). Se
# busca la ÚLTIMA ocurrencia de alguna de estas palabras — la demandada rara
# vez empieza justo con una de ellas — y se usa esa posición como el corte
# real entre demandada y tipo de juicio, ignorando los puntos intermedios.
TIPO_START_WORDS = [
    "Ord", "Especial", "Esp", "Ejecutivo", "Ejec", "Ejecc", "Oral", "Juris",
    "Proced", "Aud", "Div", "Cont", "Vía", "Via", "Providencias", "Medios",
    "Actos", "Sucesorio", "Controversias", "Otros", "Juic", "Tercerías",
    "Tercerias", "Extinción", "Extincion", "Cump", "Resc", "Reivindicatorio",
    "Reivindicatoria", "Interdicto", "Nulidad", "Usucapión", "Usucapion",
    "Divorcio", "Rectificación", "Rectificacion", "Consignación",
    "Consignacion", "Otorgamiento", "Cancelación", "Cancelacion",
    "Ordinario", "Juicio", "Remate", "Arbitraje", "Arbitral", "Exhortos",
    "Pago", "Inmat", "Quieb", "Convencional", "Proforma", "Rescisión",
    "Rescision", "Ejecución", "Ejecucion", "Reconocimiento", "Reconoc",
    "Diligencias", "Prescripción", "Prescripcion", "Prescrip", "Nul", "Reiv",
]
# Además, la primera palabra de cada tipo canónico conocido (Especial de
# Fianzas Mercantil -> "Especial" ya está, pero esto suma automáticamente
# la primera palabra de cualquier tipo que se agregue a CANONICAL_TIPOS a
# futuro, sin tener que repetirla acá a mano).
TIPO_START_WORDS = sorted(set(TIPO_START_WORDS) | {
    c.split()[0] for c in CANONICAL_TIPOS if c.split()[0].isalpha()
})
RE_TIPO_START = re.compile(r"\b(?:" + "|".join(TIPO_START_WORDS) + r")\.?\s", re.IGNORECASE)
RE_TIPO_TAIL = re.compile(
    r"\s+(\d+)\s+(?:Acdos?|Audiencias?)\.(?:\s*en\s+.+)?\s*$",
    re.IGNORECASE | re.DOTALL,
)


def _match_canonical_tipo(text):
    """Si `text` empieza (una vez expandidas sus abreviaciones) con uno de
    los tipos de juicio reales conocidos, devuelve ese tipo canónico. Si
    no, None.

    Esto reemplaza cortar en el conteo de acuerdos: el boletín a veces pega
    directo, sin ningún marcador, el verbo de la descripción del acuerdo
    ("Oral Mercantil Girar oficio...", "Ordinario Civil Ratificar..."), y
    cortar recién en "N Acdo." se traga ese verbo como si fuera parte del
    tipo de juicio (ver PARSEO.md).
    """
    return match_canonical_prefix(expand_abbr(text))


# Cuando la demandada es una razón social ("Seguros Azteca S.A. de C.V.") o
# hay varias demandadas encadenadas con "y", el punto de "S.A." se confunde
# con el corte demandada/tipo_juicio y el resto ("de C.V. y Fulano...")
# queda pegado al inicio del tipo de juicio en vez de a la demandada.
def _relocate_tipo_juicio(demandada, tipo_juicio):
    """Si tipo_juicio no matchea un tipo canónico desde el principio, pero
    lo hace más adelante (porque lo de antes es en realidad cola de la
    demandada: sufijo de razón social, más partes demandadas...), mueve ese
    prefijo colado a demandada y usa el tipo canónico encontrado."""
    if not tipo_juicio or _match_canonical_tipo(tipo_juicio):
        return demandada, tipo_juicio

    for m in RE_TIPO_START.finditer(tipo_juicio):
        if m.start() == 0:
            continue
        canon = _match_canonical_tipo(tipo_juicio[m.start():])
        if canon:
            leaked = tipo_juicio[: m.start()].strip().strip(',').strip()
            demandada = f"{demandada} {leaked}".strip() if demandada else leaked
            return demandada, canon

    return demandada, tipo_juicio


def _split_tipo_juicio(last_seg):
    """Encuentra el corte demandada/tipo_juicio anclado en vocabulario conocido.

    Devuelve (demandada, tipo_juicio, num_acdos) o None si no hay match.
    """
    starts = list(RE_TIPO_START.finditer(last_seg))
    if not starts:
        return None
    tail = RE_TIPO_TAIL.search(last_seg)
    if not tail:
        return None
    starts = [m for m in starts if m.start() < tail.start()]
    if not starts:
        return None

    # Preferimos la ÚLTIMA palabra de arranque (la demandada rara vez
    # empieza justo con una) — pero la descripción del acuerdo a veces
    # menciona de paso otro término reconocido ("Solic. Div[orcio]...",
    # "Aud[iencia]. 272-B Cpc." como referencia a un artículo, no un tipo
    # de juicio) que queda MÁS a la derecha que el tipo de juicio real. Se
    # prueba de derecha a izquierda y se usa la primera que efectivamente
    # matchea un tipo canónico completo; si ninguna matchea, se cae al
    # comportamiento anterior (la última) para no perder cobertura en
    # tipos legítimos que aún no están en CANONICAL_TIPOS.
    for m in reversed(starts):
        start = m.start()
        canon = _match_canonical_tipo(last_seg[start:tail.start()])
        if canon:
            return last_seg[:start].strip().rstrip('.'), canon, int(tail.group(1))

    start = starts[-1].start()
    demandada = last_seg[:start].strip().rstrip('.')
    tipo_juicio = last_seg[start:tail.start()].strip()
    return demandada, tipo_juicio, int(tail.group(1))


# Después de cortar en un posible terminador, lo que sigue debería ser el
# inicio de un aviso nuevo (una actora seguida de "vs." no muy lejos) o
# quedar vacío. Si no, el punto usado como corte era en realidad parte de
# una abreviatura no contemplada (como "Expdllo.") y hay que seguir buscando
# el próximo punto en vez de cortar ahí.
RE_LOOKS_LIKE_NEW_ENTRY = re.compile(r"^[A-ZÁÉÍÓÚÑ]")


def _looks_like_new_entry_start(resto):
    if not resto:
        return True
    if not RE_LOOKS_LIKE_NEW_ENTRY.match(resto):
        return False
    return bool(RE_VS.search(resto[:300]))


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

        # Un bloque puede traer más de un aviso encadenado (p. ej. un aviso
        # principal seguido de un sub-aviso de amparo). Se corta en cada
        # ocurrencia del terminador en vez de esperar a que la línea en curso
        # termine en punto, si no las entradas siguientes quedan pegadas.
        # Antes de aceptar un corte se valida que lo que sigue parezca una
        # entrada nueva; si no, el punto usado era parte de una abreviatura
        # (p. ej. "Expdllo.") y hay que seguir buscando el próximo terminador
        # sin cortar ahí.
        search_from = 0
        while True:
            m_end = RE_ENTRY_END.search(joined, search_from)
            if not m_end:
                break

            resto = joined[m_end.end():].strip()
            if not _looks_like_new_entry_start(resto):
                search_from = m_end.end()
                continue

            entry = _parse_entry(joined[: m_end.end()], boletin_id, fecha_raw, ctx)
            if entry:
                entries.append(entry)

            if not resto:
                block = []
                break

            block = [resto]
            joined = resto
            search_from = 0

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

    # Entries with amparo sub-notices use // as separator; tipo_juicio is
    # always in the last segment and demandada is before the first //.
    if '//' in resto:
        segments = resto.split('//')
        demandada_raw = segments[0].strip().rstrip('.')
        last_seg = segments[-1].strip()
    else:
        demandada_raw = None
        last_seg = resto

    split = _split_tipo_juicio(last_seg) if demandada_raw is None else None
    if split:
        demandada, tipo_juicio, num_acdos = split
    else:
        m_tipo = RE_TIPO_ACDOS.search(last_seg)
        if m_tipo:
            if demandada_raw is None:
                demandada = last_seg[: m_tipo.start() + 1].strip()
            else:
                demandada = demandada_raw
            tipo_raw = m_tipo.group(1).strip()
            tipo_juicio = _match_canonical_tipo(tipo_raw) or tipo_raw
            num_acdos = int(m_tipo.group(2))
        else:
            demandada = demandada_raw if demandada_raw is not None else resto
            tipo_juicio = None
            num_acdos = None

    demandada, tipo_juicio = _relocate_tipo_juicio(demandada, tipo_juicio)

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

    # Directorio de salida: data/YYYY-MM-DD/
    run_date = datetime.now().strftime("%Y-%m-%d")
    out_dir = Path("data") / run_date
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir = out_dir / "pdfs"
    pdf_dir.mkdir(exist_ok=True)

    log(f"Directorio de salida: {out_dir}")
    log(f"Obteniendo índice {fecha_inicio} → {fecha_fin}...")

    session, token = get_session()
    boletines = fetch_index(session, token, fecha_inicio, fecha_fin)
    log(f"  {len(boletines)} boletín(es) encontrado(s)")

    # Guardar índice en JSON
    index_path = out_dir / "index.json"
    with open(index_path, "w") as f:
        json.dump(boletines, f, ensure_ascii=False, indent=2)
    log(f"  Índice guardado en {index_path}")

    all_entries = []

    for b in boletines[:max_boletines]:
        log(f"\nBoletín {b['id']} — {b['fecha_raw']}")

        if not b["pdf_url"]:
            log("  Sin URL de PDF, saltando.")
            continue

        pdf_path = pdf_dir / f"boletin_{b['id']}.pdf"
        if not pdf_path.exists():
            log("  Descargando PDF...")
            download_pdf(b["pdf_url"], pdf_path)
            log(f"  {pdf_path.stat().st_size / 1e6:.1f} MB descargados")
        else:
            log("  PDF ya en caché.")

        log("  Extrayendo texto con pdftotext...")
        text = pdf_to_text(pdf_path)

        txt_path = pdf_dir / f"boletin_{b['id']}.txt"
        txt_path.write_text(text, encoding="utf-8")
        log(f"  Texto guardado en {txt_path}")

        log("  Parseando entradas...")
        entries = parse_pdf(text, b["id"], b["fecha_raw"])
        log(f"  {len(entries)} entradas encontradas")
        all_entries.extend(entries)

    log(f"\nTotal: {len(all_entries)} entradas")

    if all_entries:
        fields = list(all_entries[0].keys())

        # Nota: acá antes se armaba también un search_index.json (índice
        # invertido en JSON) para búsqueda del lado del cliente. Se sacó:
        # nadie lo consume (ni build_db.py ni el server lo leen — la
        # búsqueda real es FTS5 sobre SQLite) y con datasets grandes
        # (millones de entradas) tenía una complejidad cuadrática en
        # build_search_index() que llegó a comerse >13GB de RAM y colgar
        # el proceso sin terminar.

        # CSV completo
        csv_path = out_dir / "entradas.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_entries)
        log(f"CSV guardado en {csv_path}")

        # Resumen de cobertura
        total = len(all_entries)
        completas = sum(1 for r in all_entries if r["actora"] and r["demandada"] and r["tipo_juicio"])
        sin_vs = sum(1 for r in all_entries if not r["demandada"])
        sin_tipo = sum(1 for r in all_entries if not r["tipo_juicio"])

        summary = {
            "fecha_inicio": fecha_inicio,
            "fecha_fin": fecha_fin,
            "boletines_procesados": len([b for b in boletines[:max_boletines] if b["pdf_url"]]),
            "entradas_totales": total,
            "entradas_completas": completas,
            "pct_completas": round(completas / total * 100, 1) if total else 0,
            "sin_demandada": sin_vs,
            "sin_tipo_juicio": sin_tipo,
        }
        summary_path = out_dir / "resumen.json"
        with open(summary_path, "w") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        log(f"Resumen guardado en {summary_path}")

        # También imprime el resumen en pantalla
        log(f"\n{'─'*40}")
        log(f"Entradas totales   : {total}")
        log(f"Completas          : {completas} ({summary['pct_completas']}%)")
        log(f"Sin demandada      : {sin_vs} ({sin_vs/total*100:.1f}%) — casos unilaterales")
        log(f"Sin tipo_juicio    : {sin_tipo} ({sin_tipo/total*100:.1f}%)")


if __name__ == "__main__":
    main()
