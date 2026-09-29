"""
empresas.py — Detecta las empresas que son parte en un aviso del boletín y
normaliza su nombre.

Lo usan build_db.py (tablas empresas / entradas_empresas para la sección
/empresas del server) y cruces/cruce_qqw.py (cruce con QuiénEsQuién).

Regla de producto: las páginas de empresas se indexan y NO pueden mostrar
nombres de personas físicas. Por eso la detección prefiere perder una
empresa antes que tomar por empresa a una persona: solo cuenta como empresa
un trozo con forma societaria inequívoca, y "A.C." / "S.C." (que también
pueden ser iniciales) solo al final y después de al menos dos palabras.

No hay RFC en el boletín: el "núcleo" (nombre sin acentos, puntuación ni
forma societaria) es la única llave para juntar las variantes de una misma
empresa ("Banco Santander (México), S.A., Institución de Banca Múltiple" y
"BANCO SANTANDER MEXICO S.A." dan "banco santander mexico").
"""

import re
import unicodedata

# Formas societarias y colas habituales, ya normalizadas (minúsculas, sin
# acentos ni puntuación). Se sacan del final del nombre, repetidamente.
FORMAS = [
    r"sociedad anonima promotora de inversion de capital variable",
    r"sociedad anonima bursatil de capital variable",
    r"sociedad anonima de capital variable",
    r"sociedad de responsabilidad limitada de capital variable",
    r"sociedad de responsabilidad limitada",
    r"sociedad de responsabilidad",
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

# Forma societaria inequívoca en cualquier parte del trozo.
RE_FORMA_FUERTE = re.compile(
    r"\bS\.?\s?A\.?(?:\s?P\.?\s?I\.?|\s?B\.?)?\s+de\s+C\.?\s?V\b"      # S.A. de C.V., S.A.P.I. de C.V.
    r"|\bS\.?\s?A\.?(?=\s*(?:,|$|\s+Instituci|\s+Grupo|\s+Sofom))"     # "S.A.", "S.A., Institución..."
    r"|\bS\.?\s?de\s?R\.?\s?L\b"                                       # S. de R.L.
    r"|\bSofom\b|\bSociedad\s+An[oó]nima\b|\bSociedad\s+de\s+Responsabilidad\b"
    r"|\bSociedad\s+Civil\b|\bAsociaci[oó]n\s+Civil\b|\bInstituci[oó]n\s+de\s+Banca\b"
    r"|\bSociedad\s+Financiera\b|\bSociedad\s+Nacional\s+de\s+Cr[eé]dito\b"
    r"|\bSociedad\s+Cooperativa\b|\bS\.\s?N\.\s?C\b",
    re.IGNORECASE,
)
# "A.C." / "S.C." solo al final y con al menos dos palabras antes.
RE_FORMA_CORTA_FINAL_POS = re.compile(r"\s(?:A\.?\s?C|S\.?\s?C)\.?\s*$", re.IGNORECASE)
RE_FORMA_FINAL_CORTA = re.compile(r"\S+\s+\S+.*\s(?:A\.?\s?C|S\.?\s?C)\.?\s*$", re.IGNORECASE)

# Personas pegadas delante de la empresa en el mismo trozo: "Juan Pérez en
# su carácter de apoderado de X S.A. de C.V." -> "X S.A. de C.V.".
RE_PREFIJO_PERSONA = re.compile(
    r"^.*?\b(?:en\s+(?:su\s+)?(?:car[aá]cter|calidad|representaci[oó]n)\s+de"
    r"|apoderad[oa]s?\s+(?:legal(?:es)?\s+)?de|representante\s+legal\s+de"
    r"|por\s+conducto\s+de|a\s+trav[eé]s\s+de)\s+",
    re.IGNORECASE,
)
# Lo que puede seguir a la forma societaria sin dejar de ser el nombre de la
# empresa. Todo lo que viene después se descarta ("Representada por Fulano",
# "hoy Fulano", restos del tipo de juicio): ahí suelen colarse personas.
RE_COLA_SOCIETARIA = re.compile(
    r"[\s,\.]*(?:"
    r"de\s+C\.?\s?V\.?|de\s+Capital\s+Variable|Sociedad\s+An[oó]nima(?:\s+de\s+Capital\s+Variable)?"
    r"|(?:Instituci[oó]n\s+de\s+Banca\s+)?(?:M[uú]ltiple|de\s+Desarrollo)"
    r"|Grupo\s+Financiero(?:\s+(?!antes\b|hoy\b)[A-ZÁÉÍÓÚÑ][\w&]*){1,2}"
    r"|Sociedad\s+Financiera\s+de\s+Objeto\s+M[uú]ltiple(?:,?\s+Entidad\s+(?:No\s+)?Regulada)?"
    r"|Sofom(?:,?\s+E\.?\s?N\.?\s?R\.?|,?\s+E\.?\s?R\.?)?|E\.?\s?N\.?\s?R\.?"
    r"|S\.?\s?N\.?\s?C\.?|Sociedad\s+Nacional\s+de\s+Cr[eé]dito"
    r"|S\.?\s?A\.?\s?P\.?\s?I\.?|S\.?\s?A\.?\s?B\.?|S\.?\s?A\.?|de\s+R\.?\s?L\.?"
    r")",
    re.IGNORECASE,
)
# Restos de encabezados del boletín pegados a una parte por un error de parseo.
RE_RESTO_ENCABEZADO = re.compile(r"\b(?:Juzgados?\s+de\s+lo|Secretar[ií]a\b|Acuerdos\s+del|Bolet[ií]n\s+Judicial)", re.IGNORECASE)
# Bloque inicial en MAYÚSCULAS seguido de texto en minúsculas: casi siempre
# un nombre de persona o un encabezado pegado ("MIGUEL ÁNGEL PÉREZ Grupo X").
RE_PREFIJO_MAYUS = re.compile(r"^(?:[A-ZÁÉÍÓÚÑ]{2,}\s+){2,}(?=[A-ZÁÉÍÓÚÑ][a-záéíóúñ])")
# Antes de la forma societaria, estas palabras separan otra cosa (una
# persona, el nombre anterior, un amparo) de la empresa: se toma lo que
# sigue. "Olivo Martínez Jaime antes HSBC México, S.A." -> "HSBC México, S.A.";
# "Amparo Indirecto 438/2024 Promueve Dicipa S.A." -> "Dicipa S.A.".
RE_CORTE_PREVIO = re.compile(
    r"(?:(?:\b|(?<=\d))(?:antes|hoy|ahora|actualmente|vs\.?|promueve|promovido\s+por|promov\.?(?:\s+por)?|promovente:?"
    r"|quejos[oa]:?|tercero\s+interesado:?"
    # "Representado por X", no "Representaciones X S.A." (nombre real)
    r"|representad[oa]s?(?:\s+en\s+este\s+acto)?\s*por(?:\s+su\s+(?:apoderad[oa]|fiduciari[oa]|mandatari[oa]))?"
    r"|sucesionari[oa]s?(?:\s+de)?|conocid[oa]s?\b.{0,60}?\bcomo"
    r"|(?:por\s+)?(?:conducto\s+de\s+)?su\s+(?:apoderad[oa]|fiduciari[oa])(?:\s+legal)?"
    r"|tambi[eé]n\s+conocid[oa]\s+(?:por\s+sus\s+abreviaturas\s+)?(?:como|por)"
    r")"
    # o un número de expediente / amparo: "Amparo Indirecto 885/2016 ..."
    r"|\d+/\d{4}(?:-[A-Z0-9]+)?)[\s,.:]+",
    re.IGNORECASE,
)
# Fin de oración antes de la empresa: restos del aviso anterior ("1 Acdo. en
# Exhorto. BBVA México S.A.", "Principal Tomo II. Banco Nacional de México").
RE_FIN_ORACION = re.compile(r"(?:[.:;])\s+(?=[A-ZÁÉÍÓÚÑ0-9])")
RE_ENCABEZADO_INICIAL = re.compile(
    r"^(?:SENTENCIAS?|AUDIENCIAS?|ACUERDOS?|NO\s+PUBLICAD[OA]S?|MAL\s+PUBLICAD[OA]S?)\b[\s.:]*"
)
RE_MINUSCULAS_INICIALES = re.compile(r"^(?:[a-záéíóúñ][\w´'-]*\s+)+")
RE_Y_OTROS = re.compile(r"[\s,]+y\s+(?:otr[oa]s?|coags?\.?)\s*\.?\s*$", re.IGNORECASE)

# Corte entre varias partes de un mismo campo ("X S.A. de C.V. y Fulano").
# No se corta antes de una cola societaria ("S.A., Institución de Banca
# Múltiple, Grupo Financiero X" es una sola empresa).
RE_SEPARADOR = re.compile(
    r"\s*;\s*|\s+(?:y|e)\s+(?=[A-ZÁÉÍÓÚÑ])"
    r"|,\s+(?!(?:Instituci[oó]n|Grupo\s+Financiero|Sociedad|Sofom|Entidad|S\.)\b)(?=[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s)"
)
# Palabras de forma societaria: un núcleo hecho solo de estas no es una
# empresa ("Institución de Banca", "Sociedad de Responsabilidad").
PALABRAS_FORMA = set("""sociedad anonima responsabilidad limitada capital variable civil asociacion
institucion banca multiple desarrollo grupo financiero financiera objeto entidad regulada no
nacional credito cooperativa de del la y s a c v r l sofom enr snc sa cv""".split())

# Red de seguridad: si después de limpiar el nombre todavía trae esto, puede
# llevar una persona o un resto del aviso pegado, y no se publica (las
# páginas de empresas se indexan). Mejor perder la empresa que mostrar a
# una persona.
RE_SOSPECHOSO = re.compile(
    r"\bamparo\b|\bpromov|\bquejos|\bsucesi[oó]n\b|\balbacea|\bapoderad|\bLic\.|\d+/\d{4}"
    r"|representad[oa]s?\s*por|\bconocid[oa]\s+(?:como|por)\b|\bvs\.",
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
    return (bool(n) and len(n) >= 6 and n not in GENERICOS and not n.isdigit()
            and not set(n.split()) <= PALABRAS_FORMA)


def es_empresa(trozo):
    return bool(RE_FORMA_FUERTE.search(trozo) or RE_FORMA_FINAL_CORTA.match(trozo))


def limpiar(trozo):
    t = RE_Y_OTROS.sub("", trozo.strip())
    anterior = None
    while anterior != t:  # "en su carácter de apoderado de ...": varias capas
        anterior = t
        t = RE_PREFIJO_PERSONA.sub("", t)
    t = RE_PREFIJO_MAYUS.sub("", t)
    return t.strip(" ,.;")


def _palabras(texto):
    return len(re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ0-9&]{2,}", texto))


def recortar_inicio(t):
    """Quita lo que hay antes del nombre de la empresa (ver RE_CORTE_PREVIO y
    RE_FIN_ORACION). Solo corta si después queda un nombre antes de la forma
    societaria ("Lwz Consultoría Estratégica. S.A. de C.V." no se corta)."""
    t = RE_ENCABEZADO_INICIAL.sub("", t)
    # Forma larga o, si no hay, la corta del final ("... hoy Sun Belt S. C").
    m = RE_FORMA_FUERTE.search(t) or RE_FORMA_CORTA_FINAL_POS.search(t)
    if not m:
        return t
    antes = t[:m.start()]
    # Con un separador explícito ("antes", "promueve") alcanza una palabra
    # después; con un simple fin de oración se piden dos.
    for regex, minimo in ((RE_CORTE_PREVIO, 1), (RE_FIN_ORACION, 2)):
        cortes = [c.end() for c in regex.finditer(antes) if _palabras(antes[c.end():]) >= minimo]
        if cortes:
            t = t[cortes[-1]:]
            m = RE_FORMA_FUERTE.search(t) or RE_FORMA_CORTA_FINAL_POS.search(t)
            antes = t[:m.start()] if m else t
    t = RE_MINUSCULAS_INICIALES.sub("", t)
    return t


def cortar_en_forma(t):
    """Corta el nombre al final de la forma societaria y sus colas conocidas."""
    m = RE_FORMA_FUERTE.search(t)
    if not m:
        return t
    fin = m.end()
    while True:
        c = RE_COLA_SOCIETARIA.match(t, fin)
        if not c or c.end() == fin:
            break
        fin = c.end()
    return t[:fin].strip(" ,;")


def partes(campo):
    """Divide un campo actora/demandada en (empresas, hay_otras_partes).

    empresas: lista de (nucleo, nombre tal cual en el boletín, limpio).
    hay_otras_partes: True si en el campo queda alguna parte que no es
    empresa (una persona física o algo que no se reconoce como empresa). Se
    usa para mostrar "persona física" sin mostrar el nombre."""
    if not campo:
        return [], False
    empresas, otras = [], False
    for trozo in RE_SEPARADOR.split(campo):
        t = limpiar(trozo)
        if not t:
            continue
        if es_empresa(t) and not RE_RESTO_ENCABEZADO.search(t):
            t = cortar_en_forma(recortar_inicio(t))
            n = normalizar(t)
            if nucleo_valido(n) and not RE_SOSPECHOSO.search(t):
                empresas.append((n, t))
                continue
        if re.search(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]{2}", t) and not RE_Y_OTROS.match(" " + t):
            otras = True
    return empresas, otras


def partes_empresa(campo):
    """Solo los nombres de las empresas del campo (compatibilidad con
    cruces/cruce_qqw.py)."""
    return [nombre for _, nombre in partes(campo)[0]]


def slugify(texto, largo=80):
    s = unicodedata.normalize("NFD", str(texto or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:largo].strip("-") or "empresa"
