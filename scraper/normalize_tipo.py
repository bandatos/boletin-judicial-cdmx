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
    'reiv': 'Reivindicatorio', 'nul': 'Nulidad', 'ejecc': 'Ejecución',
    'potest': 'Potestad', 'pérd': 'Pérdida', 'perd': 'Pérdida', 'vol': 'Voluntaria',
    'inmat': 'Inmatriculación',
    'controv': 'Controversias de', 'arrend': 'Arrendamiento', 'inmob': 'Inmobiliario',
    'exh': 'Exhorto', 'juic': 'Juicio', 'reconoc': 'Reconocimiento',
    'patern': 'Paternidad', 'matern': 'Maternidad', 'concil': 'Conciliación',
    'repos': 'Reposición', 'cancel': 'Cancelación', 'titulos': 'Títulos',
    'prelim': 'Preliminar', 'prec': 'Precautorias', 'pos': 'Posesión',
    'susp': 'Suspensión', 'rect': 'Rectificación', 'quieb': 'Quiebra',
    'presc': 'Prescripción', 'prep': 'Preparatorios', 'interl': 'Interlocutoria',
    'sent': 'Sentencia', 'def': 'Definitiva', 'gral': 'General',
    'concurs': 'Concurso', 'convivencias': 'Convivencias', 'reg': 'Registro',
    'convenc': 'Convencional', 'pref': 'Preferente', 'o': 'o',
}

# Tipos de juicio reales, tal como se ven una vez limpios. El regex de
# extracción (poc.py) a veces pega directo, sin ningún marcador, el verbo de
# la descripción del acuerdo ("Ordinario Civil Ratificar", "Oral Mercantil
# Girar", "Ejecutivo Mercantil Contesta"...) — es una lista abierta de verbos
# imposible de cubrir con una lista de stopwords. En cambio, matchear el
# prefijo más largo de esta lista cerrada de categorías reales corta ahí sin
# importar qué venga después.
CANONICAL_TIPOS = [
    'Ordinario (Individual) Laboral Individual',
    'Otros (Paraprocesales o Voluntarios) Laboral Individual',
    'Especial de Pérdida de la Patria Potestad Familiar',
    'Especial de Fianzas Mercantil',
    'Especial de Matriculación',
    'Especial (Colectivo) Laboral',
    'Especial Hipotecario Civil',
    'Especial Hipotecario',
    'Especial Mercantil',
    'Especial Familiar',
    'Ejecutivo Cuantía Menor Mercantil',
    'Ejecutivo Paz Mercantil',
    'Ejecutivo Mercantil',
    'Ejecutivo Civil',
    'Controversias del Orden Familiar',
    'Controversias de Arrendamiento Civil',
    'Jurisdicción Voluntaria Civil',
    'Jurisdicción Voluntaria Familiar',
    'Jurisdicción Voluntaria Mercantil',
    'Jurisdicción Voluntaria',
    'Providencias Precautorias Civil',
    'Providencias Precautorias Mercantil',
    'Providencias Precautorias',
    'Medios Preparatorios Civil',
    'Medios Preparatorios Mercantil',
    'Medios Preparatorios',
    'Actos Prejudiciales Civil',
    'Actos Prejudiciales Familiar',
    'Vía Ejecutiva Cuantía Menor Civil',
    'Vía de Apremio Oralidad Civil',
    'Vía de Apremio Civil',
    'Vía de Apremio Familiar',
    'Vía de Apremio',
    'Vía Correo Electrónico',
    'Vía Electrónica',
    'Sucesorio Familiar',
    'Divorcio Necesario',
    'Divorcio Incausado',
    'Divorcio',
    'Alimentos',
    'Extinción de Dominio Civil',
    'Arbitraje Comercial Mercantil',
    'Arbitral Civil',
    'Convencional o Preferente Mercantil',
    'Ejecución de Garantías Otorgadas Mediante Prenda',
    'Procedimiento Convencional Mercantil Civil',
    'Procedimiento de Huelga Laboral',
    'Procedimiento de Ejecución Laboral Individual',
    'Cumplimiento de Contrato Oralidad Mercantil',
    'Cumplimiento de Contrato Oralidad Civil',
    'Cumplimiento de Ejecutoria',
    'Cumplimiento Voluntario',
    'Rescisión de Contrato Oralidad Mercantil',
    'Rescisión de Contrato Oralidad Civil',
    'Nulidad de Contrato Oralidad Mercantil',
    'Pago de Seguro Oralidad Mercantil',
    'Prescripción Positiva Oralidad Civil',
    'Reivindicatorio Oralidad Civil',
    'Proforma Oralidad Civil',
    'Otros Oralidad Mercantil',
    'Otros Oralidad Civil',
    'Oral Oralidad Mercantil',
    'Audiencia Preliminar',
    'Audiencia Incidental',
    'Audiencia Previa y de Conciliación',
    'Audiencia de Conciliación',
    'Audiencia de Ley',
    'Audiencia Digitalizada',
    'Exhortos Civil',
    'Exhorto Familiar',
    'Exhorto Mercantil',
    'Exhorto Civil',
    'Tercerías Civil',
    'Remate',
    'Controversias de Arrendamiento Inmobiliario',
    'Juicio Concluido Civil',
    'Reconocimiento de Paternidad',
    'Especial de Cancelación y Reposición de Títulos de Crédito Mercantil',
    'Diligencias de Conciliación Civil',
    'Ordinario Civil Familiar',
    'Ordinario Civil',
    'Ordinario Mercantil',
    'Ordinario Familiar',
    'Oral Mercantil',
    'Oral Familiar',
    'Oral Paz Civil',
    'Oral Civil',
    'Inmatriculación Judicial Civil',
    'Pago de Seguro Oralidad Civil',
    'Guarda y Custodia',
    'Quiebra Mercantil',
    'Protección Efectiva de Derechos Tutela Efectiva de Derechos Humanos',
    'Venta de Prenda Mercantil',
    'Pago de Daños Culposos Causados por Motivos del Transito de Vehículo Civil',
    'Pago de Daños Culposos Causados por Motivos del Transito de Vehículo Cuantía Menor Civil',
    'Pago de Daños Culposos Causados por Motivos del Transito de Vehículo Paz Civil',
    'Ejecución de Sentencias Dictadas en el Extranjero Mercantil',
    'Ejecución de Sentencias Dictadas en el Extranjero Paz Mercantil',
    'Suspensión de Pagos Mercantil',
    'Concurso Voluntario y Necesario Civil',
    'Plenaria de Posesión Oralidad Civil',
    'Rectificación de Acta',
    'Reconocimiento de Maternidad',
    'Registro de Convivencias',
    'Tercerías Mercantil',
    'Especial Cuantía Menor Civil',
    'Especial de Fianzas Paz Mercantil',
    'Convencional o Preferente Cuantía Menor Mercantil',
    'Arbitraje Comercial Cuantía Menor Mercantil',
    'Especial de Cancelación y Reposición de Títulos de Crédito Cuantía Menor Mercantil',
    'Especial de Pérdida de la Patria Potestad',
    'Especial de Levantamiento de Acta Familiar',
    'No Definido',
]
# Prefijos más largos primero, para que "Especial Hipotecario Civil" gane
# sobre "Especial Hipotecario" o "Especial".
CANONICAL_TIPOS.sort(key=len, reverse=True)

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

# Código de actuario/secretaría al final (2-20 letras que no son una palabra
# real de tipo de juicio): "Controversias del Orden Familiar Tesc" -> corta "Tesc".
# WHITELIST_LAST protege las palabras reales que sí pueden terminar un tipo.
RE_TRAILING_CODE = re.compile(r'^[a-záéíóúñ]{2,20}$')

# Palabras conectoras que no tiene sentido dejar como última palabra
# sobreviviente ("Guarda y", "Cumplimiento de", "Nulidad de"): si el corte
# por stopword/código deja una de estas al final, se descarta también.
CONNECTOR_WORDS = {'y', 'de', 'del', 'la', 'el', 'los', 'las', 'en', 'a', 'o'}


# Variantes de escritura del mismo tipo (typo real del boletín, singular
# vs. plural) que deben converger a una sola forma canónica antes de
# buscar el match, para no terminar con "Garantía"/"Garantías"/"Gatantía"
# como si fueran tres tipos de juicio distintos.
SPELLING_FIXES = [
    (re.compile(r'\bGatantía\b', re.IGNORECASE), 'Garantía'),
    (re.compile(r'\bGarantía Otorgada\b', re.IGNORECASE), 'Garantías Otorgadas'),
    (re.compile(r'\bNo Definido No Definido\b', re.IGNORECASE), 'No Definido'),
    # Boilerplate truncado de forma variable en el boletín: siempre es el
    # mismo tipo, se completa a la forma larga antes de matchear.
    (re.compile(r'\bTutela de Derechos(?! Humanos)\b', re.IGNORECASE), 'Tutela Efectiva de Derechos Humanos'),
    (re.compile(r'\bTutela de Derechos Humanos\b', re.IGNORECASE), 'Tutela Efectiva de Derechos Humanos'),
    # Palabras pegadas sin espacio por error de OCR/parseo ("LeyOrd.", "LeyControversias").
    (re.compile(r'(?<=[a-záéíóúñ])(?=[A-ZÁÉÍÓÚÑ])'), ' '),
]


def expand_abbr(text):
    """Expande abreviaciones token por token ("Ord." -> "Ordinario") y
    corrige variantes de escritura conocidas (typos, singular/plural)."""
    def expand(word):
        key = word.rstrip('.').lower()
        return ABBR.get(key, word)

    tokens = re.split(r'(\s+)', text)
    tokens = [expand(tok) if tok.strip() else tok for tok in tokens]
    text = ''.join(tokens)

    for pattern, replacement in SPELLING_FIXES:
        text = pattern.sub(replacement, text)
    return text


def match_canonical_prefix(text):
    """Si `text` empieza (sin abreviar) con uno de los CANONICAL_TIPOS,
    devuelve ese tipo canónico. Si no, None. Usado tanto por el extractor
    (poc.py, en el momento del parseo) como por normalize() más abajo."""
    t_lower = text.lower()
    for canon in CANONICAL_TIPOS:
        cl = canon.lower()
        if t_lower.startswith(cl):
            rest = t_lower[len(cl):]
            # el match no debe cortar en medio de una palabra ("Oral Civil"
            # no debe matchear "Oral Civilización")
            if not rest or not rest[0].isalpha():
                return canon
    return None


def normalize(tipo_juicio):
    """Devuelve una forma canónica de tipo_juicio, o None si no se puede
    reconocer un tipo real (fail-closed: mejor no clasificar que mostrar
    basura en el filtro/dropdown — el tipo_juicio crudo sigue intacto y
    visible en los resultados, solo tipo_juicio_norm queda sin valor)."""
    if not tipo_juicio:
        return None
    t = tipo_juicio.strip().rstrip('.').strip()
    if not t:
        return None

    t = expand_abbr(t)

    canon = match_canonical_prefix(t)
    if canon:
        return canon

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

    # Palabra conectora colgante al final ("Guarda y", "Cumplimiento de"):
    # la palabra real que la seguía era ruido y ya se cortó arriba.
    while out and out[-1].rstrip('.').lower() in CONNECTOR_WORDS:
        out.pop()

    result = ' '.join(out).strip().rstrip('.').strip()
    if not result:
        return None

    # Reintentar el match canónico sobre el resultado recortado: cubre
    # casos como "Rescisión de Contrato Oralidad Mercantil Señala Nuevo
    # Domicilio..." donde el corte por stopword ("Señala" no es stopword,
    # pero si lo fuera más adelante) deja un prefijo que sí es un tipo real.
    canon = match_canonical_prefix(result)
    if canon:
        return canon

    # No matcheó ningún tipo canónico ni siquiera tras recortar: es texto
    # no reconocido (nombre de persona, código, fragmento). Fail-closed.
    return None
