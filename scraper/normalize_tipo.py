"""
normalize_tipo.py — Normaliza tipo_juicio para agrupar variantes que son el
mismo tipo de juicio pero difieren en puntuación, abreviación o ruido de
parseo (código de actuario/secretaría, "Acuerdo.", tomos, etc. pegados al
final por el regex de extracción — ver PARSEO.md, sección "Casos pendientes
de mejorar").

No intenta ser perfecto: reduce el cardinal de ~76,000 valores distintos a
un puñado de categorías reales, a costa de descartar ruido residual que no
matchea ninguna regla (queda tal cual, sin normalizar).
"""

import re

ABBR = {
    'ord': 'Ordinario', 'merc': 'Mercantil', 'civ': 'Civil', 'fam': 'Familiar',
    'esp': 'Especial', 'hip': 'Hipotecario', 'ejec': 'Ejecutivo', 'cump': 'Cumplimiento',
    'resc': 'Rescisión', 'proced': 'Procedimiento', 'prescrip': 'Prescripción',
    'aud': 'Audiencia', 'div': 'Divorcio', 'nec': 'Necesario', 'alim': 'Alimentos',
    'comparec': 'Comparecencia', 'juris': 'Jurisdicción', 'domi': 'Dominio',
    'expdllo': 'Expedientillo', 'acdo': 'Acuerdo', 'acdos': 'Acuerdos',
}

# Palabras que marcan el final del tipo de juicio real: lo que sigue es
# descripción del acuerdo (Acuerdo, Auto, Trámite...) o ruido de parseo
# (código de secretaría, nombre de actuario, "Cédula Sin Diligenciar", etc).
STOP_WORDS = {
    'acuerdo', 'acuerdos', 'acdo', 'acdos', 'auto', 'tramite', 'trámite',
    'expedientillo', 'expdllo', 'prevencion', 'prevención', 'admision',
    'admisión', 'admisorio', 'admite', 'comparecencia', 'vista', 'turno',
    'turnese', 'dada', 'cuenta', 'cedula', 'cédula', 'amparo', 'tomo',
    'segundo', 'tercer', 'cuarto', 'quinto', 'sexto', 'septimo', 'séptimo',
    'octavo', 'noveno', 'requerimiento', 'informe', 'manifestaciones',
    'minuta', 'billete', 'desechamiento', 'desecha', 'desechado', 'no',
    'ha', 'lugar', 'puesta', 'disposicion', 'disposición', 'sicor', 'razon',
    'razón', 'actuarial', 'apelacion', 'apelación', 'autorizacion',
    'autorización', 'autorizaciones', 'copias', 'certificadas',
    'destruccion', 'destrucción', 'sin', 'diligenciar', 'diligenciada',
    'oficio', 'sala', 'justificado', 'exhorto', 'replica', 'réplica',
    'ejecutoria', 'indirecto', 'inf', 'just', 'agregar', 'nueva', 'llegada',
    'autos', 'entrega', 'proveniente', 'extinto', 'deberá', 'debera',
    'estarse', 'contestacion', 'contestación', 'promocion', 'promoción',
    'se', 'agrega', 'al', 'actuario', 'principal', 'archivo', 'judicial',
    'computo', 'cómputo', 'no publicado', 'publicado',
}

# Un solo carácter (con o sin punto) al final: inicial de actuario/secretaría.
RE_TRAILING_INITIAL = re.compile(r'^[a-záéíóúñ]\.?$')

# Palabras legítimas que sí pueden ser la última palabra de un tipo de juicio
# real (protege contra el heurístico de código de abajo).
WHITELIST_LAST = {
    'civil', 'mercantil', 'familiar', 'laboral', 'individual', 'colectivo',
    'voluntaria', 'voluntarios', 'arbitral', 'alimentos', 'necesario',
    'incausado', 'dominio', 'ejecutivo', 'ordinario', 'especial',
    'hipotecario', 'fianzas', 'garantia', 'garantías', 'garantias',
    'prenda', 'huelga', 'convencional', 'preliminar', 'positiva',
    'precautorias', 'providencias', 'preparatorios', 'medios', 'menor',
    'cuantia', 'cuantía', 'contrato', 'ejecutoria', 'oralidad', 'otros',
    'apremio', 'via', 'vía', 'prejudiciales', 'actos', 'extincion',
    'extinción', 'sucesorio', 'jurisdiccion', 'jurisdicción', 'del',
    'orden', 'controversias', 'arrendamiento', 'de', 'la', 'el', 'los',
    'las', 'en', 'y', 'a', 'oral', 'ley', 'transmision', 'transmisión',
    'pos', 'posesion', 'posesión', 'mediante', 'otorgadas', 'otorgada',
}

# Código de actuario/secretaría al final (2-8 letras que no son una palabra
# real de tipo de juicio): "Controversias del Orden Familiar Tesc" -> corta "Tesc".
RE_TRAILING_CODE = re.compile(r'^[a-záéíóúñ]{2,8}$')


def normalize(tipo_juicio):
    """Devuelve una forma canónica de tipo_juicio, o el valor original si
    queda vacío tras normalizar (nunca devuelve None si la entrada no lo es)."""
    if not tipo_juicio:
        return tipo_juicio
    t = tipo_juicio.strip().rstrip('.').strip()
    if not t:
        return tipo_juicio

    def expand(word):
        key = word.rstrip('.').lower()
        return ABBR.get(key, word)

    tokens = re.split(r'(\s+)', t)
    tokens = [expand(tok) if tok.strip() else tok for tok in tokens]
    t = ''.join(tokens)

    words = t.split(' ')
    out = []
    for i, w in enumerate(words):
        wl = w.rstrip('.').lower()
        is_last = i == len(words) - 1
        if wl in STOP_WORDS:
            break
        if is_last and RE_TRAILING_INITIAL.match(wl):
            break
        if is_last and RE_TRAILING_CODE.match(wl) and wl not in WHITELIST_LAST:
            break
        # palabra repetida consecutiva (ruido de parseo: "Familiar Familiar")
        if out and out[-1].lower() == w.lower():
            continue
        out.append(w)

    result = ' '.join(out).strip().rstrip('.').strip()
    return result if result else tipo_juicio
