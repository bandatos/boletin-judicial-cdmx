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
            # Desde ~jul. 2026 el src viene envuelto en el proxy del portal
            # (".../pdf/boletines/https://gestordocumental..."), que da 404.
            # Nos quedamos con la última URL, la directa al CDN.
            pm = re.findall(r'https://(?:(?!https://)[^"#])+\.pdf', src)
            if pm:
                pdf_url = pm[-1]

        boletines.append({"id": boletin_id, "fecha_raw": fecha_raw, "pdf_url": pdf_url})

    return boletines


# ── Downloader ────────────────────────────────────────────────────────────────

def download_pdf(pdf_url, dest):
    r = requests.get(pdf_url, stream=True, timeout=120)
    r.raise_for_status()
    # Se escribe a un .part y se renombra al final: si la descarga se corta,
    # no queda un PDF truncado que la próxima corrida tome como "en caché".
    tmp = dest.with_suffix(".part")
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
    tmp.rename(dest)


MESES_ABBR = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
              "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12}


def urls_alternativas(pdf_url, fecha_raw):
    """URLs a probar si el link del portal da 404. En boletines viejos el
    nombre del archivo es DDMMAAAA + N.pdf y el portal a veces lo tiene mal:
    año equivocado (marzo-abril 2019 apuntan a "...2009") o el sufijo 1 en
    vez de 2 (31-ene-2022). Se arma el nombre desde la fecha del índice."""
    m_url = re.search(r"^(.*/pdf/boletines/)\d{8}\d?\.pdf$", pdf_url or "")
    m_fecha = re.search(r"(\d{1,2})-([a-z]{3})[a-z]*\.?-(\d{4})", fecha_raw or "")
    if not m_url or not m_fecha or m_fecha.group(2) not in MESES_ABBR:
        return []
    base = f"{int(m_fecha.group(1)):02d}{MESES_ABBR[m_fecha.group(2)]:02d}{m_fecha.group(3)}"
    candidatas = [f"{m_url.group(1)}{base}{suf}.pdf" for suf in ("1", "2", "")]
    return [u for u in candidatas if u != pdf_url]


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
    "NO PUBLICADOS", "AUDIENCIA", "MAL PUBLICADOS",
    # "SENTENCIAS": subsección de sentencias definitivas (formato Toca, ver
    # RE_TOCA_END) — no es contenido de una entrada.
    "SENTENCIAS",
    # "Salas" (con minúscula, Título): artefacto de encabezado de página
    # repetido en cada salto de página dentro de la sección de Salas — no
    # confundir con "SALAS" (mayúsculas), que es el header real de sección
    # y está en SECTION_HEADERS.
    "Salas",
}

# Encabezado de página que se repite en cada salto de página, con el
# número de boletín variable ("BOLETÍN JUDICIAL No. 40") — SKIP_LINES solo
# cubre la línea "BOLETÍN JUDICIAL" sin sufijo, que también aparece sola.
# Los boletines viejos (≤2022) tienen más variantes de subsección de
# sentencias: singular o plural, definitiva o interlocutoria, seguidas de
# fecha o de "NO PUBLICADAS"/"MAL PUBLICADAS" ("SENTENCIA DEFINITIVA DEL 9
# DE FEBRERO DEL 2022", "SENTENCIAS INTERLOCUTORIAS NO PUBLICADAS"). Se
# exige la línea entera en mayúsculas para no comerse texto de una entrada.
# Mismo criterio para las subsecciones de audiencias ("AUDIENCIAS DEL 22 DE
# ABRIL DEL 2021", "AUDIENCIAS MAL PUBLICADAS") y de acuerdos no/mal
# publicados. "ACUERDOS DEL ..." no va acá: lo toma RE_ACUERDOS (fecha).
RE_SKIP_PREFIX = re.compile(
    r"^BOLETÍN JUDICIAL\b"
    r"|^(?:SENTENCIAS? (?:DEFINITIVAS?|INTERLOCUTORIAS?)|AUDIENCIAS?|ACUERDOS? (?:NO|MAL) PUBLICADOS?)"
    r"\b[^a-záéíóúñ]*$"
    # También en Título ("Sentencia Interlocutoria" sola en su línea).
    r"|^Sentencias? (?:Definitivas?|Interlocutorias?)$"
)

# Sufijo de "Núm. Exp. NNN/AAAA <sufijo>." que en realidad es la entrada
# siguiente: falta el punto después del número y RE_ENTRY_END se come el
# aviso que sigue hasta su propio "N Acdo." ("Núm. Exp. 652/2015 Secreto
# Ejecutivo Mercantil 1 Acdo. Núm. Exp. 653/2015."). Los sufijos legítimos
# ("Bis. 3 Amparo Directo (Amparo)", "(Expedientillo)") no traen "vs." ni
# terminan en un conteo de acuerdos.
# Otro caso de fusión: la entrada siguiente arranca con una razón social y
# el "." de "S.A." se tomó como fin del sufijo ("Núm. Exp. 1134/2016
# Diseños y Clásicos de México, S" + "A. de C.V. vs. ..."). Se detecta
# porque el sufijo termina en una mayúscula suelta y lo que sigue continúa
# la razón social.
RE_SUFIJO_LETRA_SUELTA = re.compile(r"\s[A-Z]$")
RE_CONTINUA_RAZON_SOCIAL = re.compile(r"^(?:A\.|de\s+R\.|C\.\s*V\.|R\.\s*L\.)")

RE_SUFIJO_FUSIONADO = re.compile(
    r"\s+vs\.\s+|\s\d+\s+(?:Acdos?|Audiencias?|Aud|Sent)\s*$", re.IGNORECASE
)

# Entrada que termina en "Núm. Exp. NNN/AAAA" SIN punto final. Así cierran
# las entradas de sentencias en boletines viejos ("... 1 Sent. Def. Núm. Exp.
# 299/2019"). RE_ENTRY_END exige el punto, así que sin esto el bloque seguía
# juntando líneas (el encabezado de la subsección siguiente y la entrada que
# venía después) hasta el próximo punto. Solo se usa al llegar a un corte de
# bloque (línea vacía, encabezado, juzgado...), nunca en medio de un bloque.
RE_ENTRY_END_BARE = re.compile(r"Núm\.\s+Exp\.\s+\d+/\d+\s*$", re.IGNORECASE)

RE_JUZGADO = re.compile(
    r"^((?:PRIMERO?|SEGUNDO|TERCERO|CUARTO|QUINTO|SEXTO|SÉPTIMO|OCTAVO|NOVENO|"
    r"DÉCIMO(?:\s+(?:PRIMERO?|SEGUNDO|TERCERO|CUARTO|QUINTO|SEXTO|SÉPTIMO|OCTAVO|NOVENO))?|"
    r"VIGÉSIMO(?:\s+(?:PRIMERO?|SEGUNDO|TERCERO))?|TRIGÉSIMO|CUADRAGÉSIMO|QUINCUAGÉSIMO)"
    r"\s+DE\s+LO\s+\w+(?:\s+\w+)*)$"
)
# Encabezado de cada Sala (segunda instancia) dentro de la sección SALAS:
# "PRIMERA SALA CIVIL", "QUINTA SALA FAMILIAR", "CUARTA SALA DE LO PENAL".
# Sin esto las entradas de Salas quedaban sin órgano (ni juzgado ni sala).
RE_SALA = re.compile(
    r"^((?:PRIMERA|SEGUNDA|TERCERA|CUARTA|QUINTA|SEXTA|SÉPTIMA|OCTAVA|NOVENA|"
    r"DÉCIMA(?:\s+(?:PRIMERA|SEGUNDA|TERCERA|CUARTA|QUINTA|SEXTA|SÉPTIMA|OCTAVA|NOVENA))?)"
    r"\s+SALA\s+(?:DE\s+LO\s+)?(?:CIVIL|FAMILIAR|PENAL)(?:\s+[A-ZÁÉÍÓÚÑ]+)*)$"
)
# Órganos cuyo nombre no entra en RE_JUZGADO y suele partirse en dos líneas:
#   "PRIMERO DE PROCESO ORAL EN MATERIA" / "FAMILIAR"
#   "UNIDAD DE GESTIÓN JUDICIAL TIPO 2 MATERIA" / "CIVIL ETAPA POSTULATORIA NÚMERO 1"
#   "PRIMER TRIBUNAL LABORAL DE ASUNTOS" / "COLECTIVOS DE LA CIUDAD DE MÉXICO"
#   "JUZGADO SÉPTIMO DE PROCESO ORAL EN MATERIA" / "FAMILIAR"  (boletines viejos)
# La primera línea se reconoce con este patrón y las siguientes en mayúsculas
# se le agregan (ver _es_continuacion_organo). Solo fuera de EDICTOS y
# AVISOS, donde "JUZGADO ..." en mayúsculas aparece dentro del texto.
_ORDINAL = (
    r"(?:PRIMER[OA]?|SEGUND[OA]|TERCER[OA]?|CUART[OA]|QUINT[OA]|SEXT[OA]|SÉPTIM[OA]|"
    r"OCTAV[OA]|NOVEN[OA]|DÉCIM[OA]|VIGÉSIM[OA]|TRIGÉSIM[OA]|CUADRAGÉSIM[OA]|"
    r"QUINCUAGÉSIM[OA]|SEXAGÉSIM[OA]|SEPTUAGÉSIM[OA])"
)
RE_ORGANO_INICIO = re.compile(
    r"^(?:JUZGADO\s+)?" + _ORDINAL + r"(?:\s+" + _ORDINAL + r")?\s+"
    r"(?:DE\s+PROCESO\s+ORAL|TRIBUNAL\s+LABORAL|DE\s+LO\s+)"
    r"|^UNIDAD\s+DE\s+GESTIÓN\s+JUDICIAL\b"
)
SECCIONES_SIN_ORGANO = {"EDICTOS", "AVISOS", "SUSCRIPCIÓN AL BOLETÍN JUDICIAL"}
# Firma de titular que encabeza algunos órganos ("JUEZ GEORGINA RAMÍREZ
# PAREDES", "SUBDIRECTOR ISRAEL TORRES TINAJERO"): sin esto se pegaba al
# principio de la primera entrada.
RE_TITULAR = re.compile(r"^(?:JUEZA?|SUBDIRECTORA?)\s+[A-ZÁÉÍÓÚÑ .]+$")
RE_SECRETARIA = re.compile(r"^SECRETAR[IÍ]A\b")
RE_ACUERDOS = re.compile(r"^ACUERDOS DEL\s+(.+)$")

# Terminator: Núm. Exp. NNNN/YYYY [sufijo libre].
# El sufijo real es muy variable (nombre de juzgado, "Bis. N", "Tomo II",
# fecha, etc.) así que en vez de enumerar los casos observados se toma todo
# el texto hasta el primer punto, salvo que ese punto sea parte de una
# abreviatura común ("vs.", "Bis.") que aparece dentro del propio sufijo.
#
# Grupo 2 (opcional): en variantes más nuevas del boletín ("Cnpcyf -
# Alimentos Núm. Exp. 4803/2026, S/T. 1 Acdo.") el conteo de acuerdos va
# DESPUÉS de "Núm. Exp." en vez de antes (lo usual es "[Tipo] N Acdo. Núm.
# Exp. NNNN/YYYY."). Sin consumirlo acá, ese "N Acdo." queda pegado al
# principio de la SIGUIENTE entrada (se cuela en su actora). _parse_entry
# usa este grupo como num_acdos cuando no encuentra uno antes de Núm. Exp.
RE_ENTRY_END = re.compile(
    r"Núm\.\s+Exp\.\s+(\d+/\d+.*?)(?<!\bvs)(?<!\bBis)(?<!\bPubl)\."
    r"(?:\s*(\d+)\s+(?:Acdos?|Audiencias?)\.)?",
    re.IGNORECASE,
)

# Segundo formato de terminador, sin "Núm. Exp.": el que usan las Salas
# (segunda instancia) para TODAS sus entradas, y los Juzgados para su
# subsección "SENTENCIAS". Ejemplos reales:
#   "Controv. de Arrendamiento T. Ap 849/2024/002, 1 Acdo."
#   "Esp. Hip. T. Qu 191/2026/007 Sent. Pon 2, 1 Sent."
#   "Ord. Civ. T. 308/2024/001 Cuad. Amp. 1, 1 Acdo."
#   "Ord. Civ. T. 319/2022/003 Cuad. Amp. Iv. del Exp. 387/2021 del
#    Juzg, 69° de Lo Civil de Proceso Escrito de la Cdmx, 2 Acdos."
# El código de 1-4 letras tras "T." (Ap, In, Qu, Cc, Rc...) se deja
# genérico en vez de enumerado, mismo criterio que RE_ENTRY_END arriba.
# "T." (el literal, no el resto) va SIN IGNORECASE: la única forma en que
# aparece de verdad es mayúscula — permitir minúscula generaba falsos
# positivos con abreviaturas ajenas que también terminan en "t." ("Amparo
# Directo Dt. 245/2025.").
#
# Entre el número de toca y su cola ("N Acdo(s)./Sent.") puede haber un
# "Cuad. Amp." con calificador variable — desde "1," hasta cláusulas largas
# ("del Exp. NNN/AAAA del Juzg. NN° de Lo Civil de Proceso Escrito de la
# Cdmx,"). Un límite ciego de caracteres no distingue "cláusula legítima
# larga" de "salto de página que separa a un registro de su propia cola,
# fusionando dos avisos" (el regex se "come" hasta la próxima cola válida
# que encuentra). En vez de acotar por longitud, se prohíbe que el hueco
# contenga alguna de las frases que solo aparecen en encabezados/pies de
# página o separadores de sección — eso sí distingue ambos casos.
_RE_TOCA_POISON = (
    r"SOLO CONSULTA|BOLET[IÍ]N JUDICIAL|ACUERDOS DEL|SALAS?\b|"
    r"SECRETARI[OA] DE ACUERDOS|MAL PUBLICADOS|NO PUBLICADOS|AUDIENCIA\b"
)
#
# Hasta septiembre de 2024 el número de toca va con guiones ("Ord. Civ. T.
# 84-2020-001, 1 Acdo."), a veces sin el segundo ("T. 300-2019001, 002 y
# 003"). Sin esa variante se perdía toda la sección SALAS de esos años. Se
# exige año de cuatro dígitos y nada de espacios para no tomar fechas
# sueltas ("del 2017- 05- 18"). _normalizar_toca lo deja con barras.
#
# En ese formato viejo las sentencias de sala no traen conteo: "T.
# 436-2019-003, Sent. Pon. 1." o "Sent. Cump. Ejec. Amp. Pon. 2." (grupo 2
# vacío: es una sentencia).
#
# Colas opcionales que se consumen con el aviso: el cuaderno después del
# conteo ("en Cuad. Amp.", "Cuad. Amp. Act.", "en Cuad. de Const.", "en
# Expdllo.", hasta 2024) y "no Publ. del 18 Sep 2026" / "mal Publ. del 31
# de Enero.", marca de un acuerdo que se publica tarde. Sin consumirlas
# acá quedaban al principio del aviso siguiente, que no "parece" un aviso
# nuevo, y los dos se fundían en uno (se perdía el segundo).
RE_TOCA_END = re.compile(
    r"T\."
    r"(?i:\s*(?:[A-ZÁÉÍÓÚÑ]{1,4}\.?\s*)?)"
    r"(\d+/\d+(?:/\d+)?|\d{1,5}-\d{4}(?:-?\d{1,3})?)"
    r"(?:(?!(?i:" + _RE_TOCA_POISON + r")).){0,200}?"
    r"(?i:(?:(\d+)\s+(?:Acdos?|Audiencias?|Sent\.?)\."
    r"|Sent\.(?:\s*[a-záéíóúñ]+\.){0,4}\s*Pon\.?\s*\d{1,2}(?:\.|(?=\s+en\s)))"
    r"(?:,?\s*(?:en\s+)?(?:Cuad|Expdllo)\.?(?:\s+(?:de\s+)?[A-Z][a-z]*\.?){0,3}(?:\s*[IVX\d]+)?\.?)?"
    r"(?:\s*(?:no|mal)\s+Publ\.?(?:\s*del\s+\d{1,2}(?:\s+de)?\s+[a-z]{3,}\.?"
    r"(?:\s+(?:del?\s+)?\d{4})?\.?)?)?)",
    re.DOTALL,
)
RE_TOCA_GUIONES = re.compile(r"^(\d+)-(\d{4})(?:-?(\d{1,3}))?$")


def _normalizar_toca(numero):
    """'84-2020-001' y '300-2019001' -> '84/2020/001' y '300/2019/001'."""
    m = RE_TOCA_GUIONES.match(numero)
    if not m:
        return numero
    return "/".join(g for g in m.groups() if g)


# Tipo de actuación de cada aviso: la marca del conteo ("1 Acdo.", "2 Sent.
# Def.", "1 Aud.") y, si la marca no alcanza, la subsección del órgano en la
# que está ("SENTENCIAS", "SENTENCIA INTERLOCUTORIA DEL ...", "AUDIENCIAS").
RE_MARCA_ACTUACION = re.compile(
    r"\b\d+\s+(Acdos?|Audiencias?|Aud|Sent(?:encias?)?)\b\.?"
    r"(?:\s*(Def|Definitivas?|Interl|Inter|Interlocutorias?)\b)?",
    re.IGNORECASE,
)
# Variante con el número después ("Ord. Civil Oral Familiar Sent. Def. 1
# Núm. Exp. 1545/2026.", "Aud. y Sent. Def. 1 Núm. Exp.").
RE_MARCA_INVERTIDA = re.compile(
    r"\b(Acdos?|Audiencias?|Aud|Sent(?:encias?)?)\b\.?"
    r"(?:\s*(Def|Definitivas?|Interl|Inter|Interlocutorias?)\b\.?)?\s*\d+\s+Núm\.",
    re.IGNORECASE,
)
RE_PONENCIA = re.compile(r"\bSent\.(?:\s*[a-záéíóúñ]+\.){0,4}\s*Pon\.?\s*(\d{1,2})\b", re.IGNORECASE)


def _subseccion(line):
    """Subsección del órgano que abre esta línea de encabezado, o None."""
    u = line.upper()
    if u.startswith("SENTENCIA"):
        if "INTERLOCUTORIA" in u:
            return "sentencia_interlocutoria"
        if "DEFINITIVA" in u:
            return "sentencia_definitiva"
        return "sentencia"
    if u.startswith("AUDIENCIA"):
        return "audiencia"
    return None


def _tipo_actuacion(text, subseccion):
    """Sin marca de conteo es un acuerdo, salvo que la subsección diga otra
    cosa: los avisos de las UGJ en formato CNPCyF no traen conteo, sino lo
    que se acordó ("Núm. Exp. 3193/2026, Reconoce Personalidad.")."""
    marcas = list(RE_MARCA_ACTUACION.finditer(text or "")) + list(RE_MARCA_INVERTIDA.finditer(text or ""))
    if not marcas:
        return subseccion or "acuerdo"
    ultima = max(marcas, key=lambda m: m.start())
    palabra, calificador = ultima.group(1).lower(), (ultima.group(2) or "").lower()
    if palabra.startswith("acdo"):
        return "acuerdo"
    if palabra.startswith("aud"):
        return "audiencia"
    if calificador.startswith("def"):
        return "sentencia_definitiva"
    if calificador.startswith("inter"):
        return "sentencia_interlocutoria"
    return subseccion if subseccion and subseccion.startswith("sentencia") else "sentencia"

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
    "Actos", "Sucesorio", "Controversias", "Controversia", "Otros", "Juic", "Tercerías",
    "Tercerias", "Extinción", "Extincion", "Cump", "Resc", "Reivindicatorio",
    "Reivindicatoria", "Interdicto", "Nulidad", "Usucapión", "Usucapion",
    "Divorcio", "Rectificación", "Rectificacion", "Consignación",
    "Consignacion", "Otorgamiento", "Cancelación", "Cancelacion",
    "Ordinario", "Juicio", "Remate", "Arbitraje", "Arbitral", "Exhortos",
    "Pago", "Inmat", "Quieb", "Convencional", "Proforma", "Rescisión",
    "Rescision", "Ejecución", "Ejecucion", "Reconocimiento", "Reconoc",
    "Diligencias", "Prescripción", "Prescripcion", "Prescrip", "Nul", "Reiv",
    "Alim", "Guarda", "Rect", "Terc", "Susp", "Pérd", "Perd", "Venta",
    "Plenaria", "Protección", "Proteccion", "Suspensión", "Suspension",
    "Concurso", "Reg", "Tutela",
    "Int", "Interd", "Test", "Testament", "Exh",
]
# Además, la primera palabra de cada tipo canónico conocido (Especial de
# Fianzas Mercantil -> "Especial" ya está, pero esto suma automáticamente
# la primera palabra de cualquier tipo que se agregue a CANONICAL_TIPOS a
# futuro, sin tener que repetirla acá a mano).
TIPO_START_WORDS = sorted(set(TIPO_START_WORDS) | {
    c.split()[0] for c in CANONICAL_TIPOS if c.split()[0].isalpha()
})
# (?:\s|$): la palabra de arranque puede ir seguida de espacio (caso normal)
# o ser la última palabra de la cadena (formatos sin "N Acdo." al final,
# como el toca o "Cnpcyf - Alimentos" al final de last_seg, donde no hay
# nada después de la palabra que ancle el corte).
RE_TIPO_START = re.compile(r"\b(?:" + "|".join(TIPO_START_WORDS) + r")\.?(?:\s|$)", re.IGNORECASE)
# Cola "N Acdo(s)." y variantes de audiencias y sentencias: "1 Aud. en
# Tercería.", "1 Sent. Interlocutoria.", "2 Sent. Def.".
RE_TIPO_TAIL = re.compile(
    r"\s+(\d+)\s+(?:Acdos?|Audiencias?|Aud|Sent|Sentencias?)\.?"
    r"(?:\s+(?:Def|Definitivas?|Interl|Inter|Interlocutorias?)\.?)?"
    r"(?:\s*en\s+.+)?\s*$",
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


def _split_demandada_tipo(segment, end):
    """Encuentra el corte demandada/tipo_juicio anclado en vocabulario
    conocido, dentro de segment[:end]. Devuelve (demandada, tipo_juicio) —
    tipo_juicio es None si no hay ninguna palabra de arranque reconocida.
    """
    starts = [m for m in RE_TIPO_START.finditer(segment) if m.start() < end]
    if not starts:
        return segment[:end].strip().rstrip('.'), None

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
        canon = _match_canonical_tipo(segment[start:end])
        if canon:
            return segment[:start].strip().rstrip('.'), canon

    start = starts[-1].start()
    return segment[:start].strip().rstrip('.'), segment[start:end].strip()


def _split_tipo_canonico(segment):
    """Último recurso cuando no hay cola "N Acdo." ni conteo después de Núm.
    Exp. ("... Ord. Mercantil Sent. Def."): corta solo si encuentra un tipo
    de juicio canónico, de derecha a izquierda. Devuelve (resto, tipo) o
    None. Sin ancla a la derecha, cortar en una palabra de arranque que no
    forma un tipo real sería adivinar."""
    for m in reversed(list(RE_TIPO_START.finditer(segment))):
        if m.start() == 0:
            continue
        canon = _match_canonical_tipo(segment[m.start():])
        if canon:
            return segment[:m.start()].strip().rstrip('.'), canon
    return None


def _split_tipo_juicio(last_seg):
    """Encuentra el corte demandada/tipo_juicio anclado en vocabulario
    conocido, cuando el tipo termina en "N Acdo(s)./Audiencia(s)." (formato
    Núm. Exp. de juzgados). Devuelve (demandada, tipo_juicio, num_acdos) o
    None si no hay match.
    """
    tail = RE_TIPO_TAIL.search(last_seg)
    if not tail:
        return None
    demandada, tipo_juicio = _split_demandada_tipo(last_seg, tail.start())
    if tipo_juicio is None:
        return None
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


def _es_mayusculas(line):
    return not re.search(r"[a-záéíóúñ]", line)


def _es_continuacion_organo(line):
    return (
        _es_mayusculas(line) and len(line) <= 70
        and line not in SECTION_HEADERS and line not in SKIP_LINES
        and not RE_SKIP_PREFIX.match(line) and not RE_TITULAR.match(line)
        and not RE_SECRETARIA.match(line) and not RE_ACUERDOS.match(line)
        and not RE_JUZGADO.match(line) and not RE_SALA.match(line)
        and not RE_ORGANO_INICIO.match(line)
    )


def parse_pdf(text, boletin_id, fecha_raw):
    entries = []
    # pagina: página del PDF (1 = primera) donde se cerró la entrada, para
    # abrir el PDF justo ahí (visor con #page=N). pdftotext separa las
    # páginas con un form feed (\f).
    ctx = {"seccion": None, "juzgado": None, "sala": None, "secretaria": None,
           "fecha_acuerdo": None, "subseccion": None, "pagina": 1}
    # Último aviso de toca cerrado y si el próximo aviso es otra toca de
    # las mismas partes ("[,] [Tipo] T. NNN, N Acdo.", ver _prefijo_cadena).
    ultimo_toca = None
    en_cadena = False
    block = []

    def flush_bare():
        """Si el bloque termina en una entrada completa que quedó pendiente
        (sin punto final, ver RE_ENTRY_END_BARE, o esperando la línea
        siguiente por una razón social, ver RE_SUFIJO_LETRA_SUELTA), la
        cierra y vacía el bloque."""
        nonlocal block
        if not block:
            return
        joined_block = " ".join(block)
        if RE_ENTRY_END_BARE.search(joined_block):
            entry = _parse_entry(joined_block.rstrip() + ".", boletin_id, fecha_raw, ctx)
        else:
            m = RE_ENTRY_END.search(joined_block)
            if not m or joined_block[m.end():].strip():
                return
            entry = _parse_entry(joined_block, boletin_id, fecha_raw, ctx)
        if entry:
            entries.append(entry)
        block = []

    organo = None  # líneas del nombre de un órgano multilínea, en curso

    lineas = (
        (n, raw_line)
        for n, pagina in enumerate(text.split("\f"), start=1)
        for raw_line in pagina.splitlines()
    )

    for pagina, raw_line in lineas:
        ctx["pagina"] = pagina
        line = raw_line.strip()

        if organo is not None:
            if line and _es_continuacion_organo(line):
                organo.append(line)
                continue
            ctx.update(juzgado=" ".join(organo), sala=None, secretaria=None, subseccion=None)
            organo = None
            ultimo_toca, en_cadena = None, False

        if not line or len(line) < 3:
            flush_bare()
            # Línea vacía: descartar bloque si no contiene expediente
            # (de ninguno de los dos formatos de terminador)
            if block:
                joined_block = " ".join(block)
                if not RE_ENTRY_END.search(joined_block) and not RE_TOCA_END.search(joined_block):
                    block = []
            continue

        es_organo = (
            ctx["seccion"] not in SECCIONES_SIN_ORGANO
            and _es_mayusculas(line) and RE_ORGANO_INICIO.match(line)
            and not RE_JUZGADO.match(line)
        )

        if (line in SECTION_HEADERS or line in SKIP_LINES or RE_SKIP_PREFIX.match(line)
                or RE_JUZGADO.match(line) or RE_SALA.match(line) or es_organo
                or RE_TITULAR.match(line)
                or RE_SECRETARIA.match(line) or RE_ACUERDOS.match(line)):
            flush_bare()

        if es_organo:
            organo = [line]
            block = []
            continue

        if RE_TITULAR.match(line):
            continue

        if line in SECTION_HEADERS:
            ctx.update(seccion=line, juzgado=None, sala=None, secretaria=None, subseccion=None)
            block = []
            ultimo_toca, en_cadena = None, False
            continue

        if line in SKIP_LINES or RE_SKIP_PREFIX.match(line):
            sub = _subseccion(line)
            if sub:
                ctx["subseccion"] = sub
            elif line.upper().startswith("ACUERDO"):
                ctx["subseccion"] = None
            continue

        if RE_JUZGADO.match(line):
            ctx.update(juzgado=line, sala=None, secretaria=None, subseccion=None)
            block = []
            ultimo_toca, en_cadena = None, False
            continue

        if RE_SALA.match(line):
            ctx.update(sala=line, juzgado=None, secretaria=None, subseccion=None)
            block = []
            ultimo_toca, en_cadena = None, False
            continue

        if RE_SECRETARIA.match(line):
            ctx["secretaria"] = line
            block = []
            continue

        m_ac = RE_ACUERDOS.match(line)
        if m_ac:
            ctx["fecha_acuerdo"] = m_ac.group(1).strip()
            ctx["subseccion"] = None
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
            m_exp = RE_ENTRY_END.search(joined, search_from)
            m_toca = RE_TOCA_END.search(joined, search_from)
            if m_exp and m_toca:
                m_end, is_toca = (m_exp, False) if m_exp.start() < m_toca.start() else (m_toca, True)
            elif m_exp:
                m_end, is_toca = m_exp, False
            elif m_toca:
                m_end, is_toca = m_toca, True
            else:
                break

            letra_suelta = razon_social = None
            if not is_toca:
                num = re.match(r"\d+/\d+", m_end.group(1))
                sufijo = m_end.group(1)[num.end():]
                siguiente = joined[m_end.end():].strip()
                letra_suelta = RE_SUFIJO_LETRA_SUELTA.search(sufijo)
                if letra_suelta and not siguiente:
                    break  # decidir cuando llegue la línea siguiente
                razon_social = letra_suelta and RE_CONTINUA_RAZON_SOCIAL.match(siguiente)
                if RE_SUFIJO_FUSIONADO.search(sufijo) or razon_social:
                    corte = m_end.start(1) + num.end()
                    entry = _parse_entry(joined[:corte] + ".", boletin_id, fecha_raw, ctx)
                    if entry:
                        entries.append(entry)
                    joined = joined[corte:].strip()
                    block = [joined]
                    search_from = 0
                    continue

            resto = joined[m_end.end():].strip()
            # Sufijo de una letra ("Núm. Exp. 1483/2024 A.") que no sigue en
            # razón social: el punto sí cierra la entrada, aunque la próxima
            # sea de una sola parte (sin "vs." que la delate).
            corte_seguro = not is_toca and letra_suelta and not razon_social
            # Otra toca de las mismas partes a continuación (", Esp. Hip. T.
            # 39-2020-002, 1 Acdo." o "Ord. Civ. T. In 1256/2026/002, 1
            # Acdo."): también es un corte. Sin esto se seguía buscando el
            # próximo terminador y la segunda toca quedaba dentro del texto
            # de la primera (se perdía).
            cadena = is_toca and _prefijo_cadena(resto) is not None
            if not corte_seguro and not cadena and not _looks_like_new_entry_start(resto):
                search_from = m_end.end()
                continue

            texto = joined[: m_end.end()]
            entry = None
            if is_toca and en_cadena:
                entry = _parse_toca_cadena(texto, ultimo_toca, boletin_id, fecha_raw, ctx)
                if entry is None:
                    texto = texto.lstrip(",. ")
            if entry is None:
                parse_fn = _parse_toca_entry if is_toca else _parse_entry
                entry = parse_fn(texto, boletin_id, fecha_raw, ctx)
            if entry:
                entries.append(entry)
            ultimo_toca = entry if is_toca else None
            en_cadena = cadena

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
    # El boletín a veces omite la actora y la entrada arranca con "vs.":
    # sin el espacio previo RE_VS no corta y "vs. Fulano" quedaba de actora.
    if re.match(r"vs\.\s", pre, re.IGNORECASE):
        pre = " " + pre

    partes = RE_VS.split(pre, maxsplit=1)
    actora = partes[0].strip()

    if len(partes) < 2:
        # Caso de una sola parte (sin "vs."): sucesiones, jurisdicciones
        # voluntarias, "Secreto"... El tipo de juicio y el conteo de acuerdos
        # vienen pegados al nombre ("Islas Retana Julieta Juris. Voluntaria
        # Civil 1 Acdo."); se cortan con el mismo criterio que la demandada
        # en los casos con "vs.".
        tipo_juicio = num_acdos = None
        split = _split_tipo_juicio(actora)
        if split:
            actora, tipo_juicio, num_acdos = split
        elif m_exp.group(2):
            actora, tipo_juicio = _split_demandada_tipo(actora, len(actora))
            num_acdos = int(m_exp.group(2))
        else:
            split = _split_tipo_canonico(actora)
            if split:
                actora, tipo_juicio = split
        actora, tipo_juicio = _relocate_tipo_juicio(actora, tipo_juicio)
        return _row(boletin_id, fecha_raw, ctx, actora or None, None, tipo_juicio, num_acdos, expediente, text)

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
        elif m_exp.group(2):
            # Variante donde "N Acdo." va después de "Núm. Exp." (ver
            # comentario en RE_ENTRY_END): no hay conteo antes del punto,
            # así que no hay un límite derecho obvio para anclar el corte
            # demandada/tipo — se usa todo last_seg, igual que en el
            # formato Toca.
            if demandada_raw is None:
                demandada, tipo_juicio = _split_demandada_tipo(last_seg, len(last_seg))
            else:
                demandada = demandada_raw
                tipo_juicio = _match_canonical_tipo(last_seg)
            num_acdos = int(m_exp.group(2))
        else:
            demandada = demandada_raw if demandada_raw is not None else resto
            tipo_juicio = None
            num_acdos = None
            if demandada_raw is None:
                split = _split_tipo_canonico(last_seg)
                if split:
                    demandada, tipo_juicio = split

    demandada, tipo_juicio = _relocate_tipo_juicio(demandada, tipo_juicio)

    return _row(boletin_id, fecha_raw, ctx, actora, demandada, tipo_juicio, num_acdos, expediente, text)


def _parse_toca_entry(text, boletin_id, fecha_raw, ctx):
    """Análogo a _parse_entry, pero para el formato Toca (Salas y
    subsección Sentencias de Juzgados): no hay "Núm. Exp.", el número de
    expediente es el toca mismo y va ANTES del conteo de acuerdos/sentencias
    en vez de después (ver RE_TOCA_END)."""
    m_toca = RE_TOCA_END.search(text)
    if not m_toca:
        return None

    expediente = _normalizar_toca(m_toca.group(1).strip())
    num_acdos = int(m_toca.group(2) or 1)
    pre = text[:m_toca.start()].strip()

    partes = RE_VS.split(pre, maxsplit=1)
    actora = partes[0].strip()

    if len(partes) < 2:
        return _row(boletin_id, fecha_raw, ctx, actora, None, None, num_acdos, expediente, text)

    resto = partes[1].strip()
    demandada, tipo_juicio = _split_demandada_tipo(resto, len(resto))
    demandada, tipo_juicio = _relocate_tipo_juicio(demandada, tipo_juicio)

    return _row(boletin_id, fecha_raw, ctx, actora, demandada, tipo_juicio, num_acdos, expediente, text)


# Comienzo de otra toca de las mismas partes después de un aviso de toca:
# ", Esp. Hip. T. 39-2020-002" (hasta 2024) u "Ord. Civ. T. In 1256/2026/002"
# (después). Basta con que llegue el número: la cola puede venir en la
# línea siguiente.
RE_CADENA_TOCA = re.compile(
    r"^([,.]?)\s*(.{0,120}?)\s*T\.(?i:\s*(?:[A-ZÁÉÍÓÚÑ]{1,4}\.?\s*)?)(?:\d+/\d+|\d{1,5}-\d{4})",
    re.DOTALL,
)


# Abreviaturas de tipo de las salas que RE_TIPO_START no tiene ("Cnpcyf -
# Especial de Arrendamiento Oral", "Suc.", "Controv. Fam.", "Prov. Prec.").
RE_TIPO_CADENA = re.compile(r"^(?:Cnpcyf|Suc|Controv|Prov|Interd|Inc|Med|Proc|Rec)\b")


def _prefijo_cadena(resto):
    """Tipo de juicio de la toca encadenada que empieza en `resto`, o None
    si `resto` es un aviso nuevo. Sin coma adelante se exige que sea un tipo
    de juicio reconocible: un aviso de una sola parte ("Fulano su Sucesión.
    Suc. Test. T. 12/2026/001") también llega a la toca sin "vs."."""
    m = RE_CADENA_TOCA.match(resto)
    if not m:
        return None
    coma, prefijo = m.group(1), m.group(2).strip()
    if RE_VS.search(" " + prefijo + " "):
        return None
    if (coma == "," or not prefijo or RE_TIPO_START.match(prefijo) or RE_TIPO_CADENA.match(prefijo)
            or _match_canonical_tipo(prefijo)):
        return prefijo
    return None


def _parse_toca_cadena(text, padre, boletin_id, fecha_raw, ctx):
    """Toca encadenada a la anterior, con las mismas partes (ver
    _prefijo_cadena). Devuelve None si no hay aviso anterior o si lo que va
    antes de la toca no es un tipo de juicio corto."""
    m = RE_TOCA_END.search(text)
    if not m or not padre:
        return None
    prefijo = text[:m.start()].strip().lstrip(",.").strip()
    if len(prefijo) > 120 or RE_VS.search(" " + prefijo + " "):
        return None
    tipo = _match_canonical_tipo(prefijo) or prefijo.rstrip(" ,.") or padre["tipo_juicio"]
    return _row(boletin_id, fecha_raw, ctx, padre["actora"], padre["demandada"], tipo,
                int(m.group(2) or 1), _normalizar_toca(m.group(1).strip()), text)


# Restos de la entrada anterior que quedan al principio de la actora: la
# marca "(Expedientillo)." o "(Amparo)", "no Publ." / "mal Publ." y su fecha
# ("del 2017- 05- 18."), o un año suelto.
RE_ARRASTRE_ACTORA = re.compile(
    r"^(?:\((?:Expedientillo|Amparo|Cuadernillo)\)\.?\s*"
    r"|(?:no|mal)\s+Publ\.?\s*"
    r"|del\s+\d{4}-\s*\d{1,2}-\s*\d{1,2}\.\s*"
    r"|\d{4}\s+(?=[A-ZÁÉÍÓÚÑ]))+"
)


def _row(boletin_id, fecha_raw, ctx, actora, demandada, tipo_juicio, num_acdos, expediente, raw_text):
    if actora:
        actora = RE_ARRASTRE_ACTORA.sub("", actora) or actora
    actora = actora or None
    return {
        "boletin_id": boletin_id,
        "fecha": fecha_raw,
        **ctx,
        "actora": actora,
        "demandada": demandada,
        "tipo_juicio": tipo_juicio,
        "num_acdos": num_acdos,
        "expediente": expediente,
        "tipo_actuacion": _tipo_actuacion(raw_text, ctx.get("subseccion")),
        "ponencia": int(m_pon.group(1)) if (m_pon := RE_PONENCIA.search(raw_text or "")) else None,
        "raw_text": raw_text,
    }




# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    fecha_inicio = sys.argv[1] if len(sys.argv) > 1 else "2026-06-09"
    fecha_fin = sys.argv[2] if len(sys.argv) > 2 else "2026-06-10"
    max_boletines = int(sys.argv[3]) if len(sys.argv) > 3 else 1

    log = lambda msg: print(msg, file=sys.stderr)

    # Directorio de salida: data/YYYY-MM-DD/ (fecha de la corrida), salvo que
    # se pase otro como 4to argumento.
    run_date = datetime.now().strftime("%Y-%m-%d")
    out_dir = Path(sys.argv[4]) if len(sys.argv) > 4 else Path("data") / run_date
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

    # Las entradas se escriben al CSV a medida que se parsea cada boletín, en
    # vez de juntarlas todas en memoria: con el histórico completo (~30M
    # entradas) la lista no entra en RAM.
    csv_path = out_dir / "entradas.csv"
    csv_file = None
    writer = None
    total = completas = sin_vs = sin_tipo = 0
    corregidas = 0  # boletines bajados de una URL distinta a la del portal

    for b in boletines[:max_boletines]:
        log(f"\nBoletín {b['id']} — {b['fecha_raw']}")

        if not b["pdf_url"]:
            log("  Sin URL de PDF, saltando.")
            continue

        pdf_path = pdf_dir / f"boletin_{b['id']}.pdf"
        if not pdf_path.exists():
            log("  Descargando PDF...")
            error = None
            for url in [b["pdf_url"], *urls_alternativas(b["pdf_url"], b["fecha_raw"])]:
                try:
                    download_pdf(url, pdf_path)
                except requests.RequestException as e:
                    error = error or e
                    continue
                if url != b["pdf_url"]:
                    log(f"  Link del portal roto, bajado de {url}")
                    b["pdf_url_portal"], b["pdf_url"] = b["pdf_url"], url
                    corregidas += 1
                error = None
                break
            if error:
                # Un boletín que falla (p. ej. el del día, que todavía no se
                # subió y da 404) no debe tirar abajo una corrida de cientos.
                log(f"  ERROR descargando, saltando: {error}")
                continue
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
        if entries and writer is None:
            csv_file = open(csv_path, "w", newline="")
            writer = csv.DictWriter(csv_file, fieldnames=list(entries[0].keys()), extrasaction="ignore")
            writer.writeheader()
        if entries:
            writer.writerows(entries)
            csv_file.flush()
        total += len(entries)
        completas += sum(1 for r in entries if r["actora"] and r["demandada"] and r["tipo_juicio"])
        sin_vs += sum(1 for r in entries if not r["demandada"])
        sin_tipo += sum(1 for r in entries if not r["tipo_juicio"])

    if csv_file:
        csv_file.close()

    if corregidas:
        # El índice se guardó antes de bajar: se reescribe con las URLs que
        # sí funcionaron (la del portal queda en pdf_url_portal).
        with open(index_path, "w") as f:
            json.dump(boletines, f, ensure_ascii=False, indent=2)
        log(f"  {corregidas} URL(s) corregida(s) en {index_path}")

    log(f"\nTotal: {total} entradas")

    if total:

        # Nota: acá antes se armaba también un search_index.json (índice
        # invertido en JSON) para búsqueda del lado del cliente. Se sacó:
        # nadie lo consume (ni build_db.py ni el server lo leen — la
        # búsqueda real es FTS5 sobre SQLite) y con datasets grandes
        # (millones de entradas) tenía una complejidad cuadrática en
        # build_search_index() que llegó a comerse >13GB de RAM y colgar
        # el proceso sin terminar.

        log(f"CSV guardado en {csv_path}")

        # Resumen de cobertura
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
