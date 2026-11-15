"""
acuerdos.py: tipo de acuerdo de un aviso del boletín (prevención, negación,
admisión). Sin dependencias, para que lo usen el scraper (poc.py) y el build
(build_db.py) sin cargar requests ni bs4. Casos en test_tipo_acuerdo.py.
"""
import functools
import re

from normalize_tipo import STOP_WORDS, expand_abbr, match_canonical_prefix

RE_VS = re.compile(r"\s+vs\.\s+", re.IGNORECASE)

# Tipo de acuerdo: qué se resolvió, cuando el órgano lo publica. Muchos
# juzgados (sobre todo los orales y las UGJ) agregan una descripción corta
# entre el tipo de juicio y la cola ("Ord. Civil Prevención. 1 Acdo.",
# "Ord. Mercantil Desechamiento. 1 Acdo.", "Vía de Apremio Civil Admisorio.").
# Los que no la publican quedan sin tipo de acuerdo: no quiere decir que no
# haya sido una prevención, sino que el boletín no lo dice.
#   prevencion: se previene a la parte para que corrija o complete la demanda.
#   negacion:   se desecha, no se admite, no ha lugar, se niega, improcedente.
#   admision:   se admite la demanda o se radica el asunto.
# El orden importa: "No Se Admite" es negación aunque contenga "Admite", y
# "No Desahogó la Prevención" queda como prevención (el acuerdo es sobre la
# prevención, y el boletín no dice qué se resolvió después).
RE_ACUERDO_NEGACION = re.compile(
    r"\b(?:Desech\w*|No\s+Ha\s+Lugar|No\s+(?:Se\s+)?Admit\w*|(?:Se\s+)?Nieg[ao]n?\b"
    r"|Improceden\w*|Inadmi\w*|No\s+(?:Se\s+)?Da\s+Tr[áa]mite)",
    re.IGNORECASE,
)
RE_ACUERDO_PREVENCION = re.compile(r"\b(?:Prevenci[óo]n|Previene|Prev\.)(?=[\s.,]|$)", re.IGNORECASE)
RE_ACUERDO_ADMISION = re.compile(
    r"\b(?:Admisi[óo]n|Admisorio|Auto\s+Admisorio|Admite\s+(?:la\s+)?Demanda|Se\s+Admite\b|Radicaci[óo]n|Radica\b)"
    r"(?!\s+(?:de\s+(?:la\s+)?)?(?:Prueb|Pbas|Recurso|Apel|Inc\b|Incidente|Exh))",
    re.IGNORECASE,
)
# "Exh. Admisión", "Radicación de Exh.": se admite o radica un exhorto, no
# la demanda.
RE_ADMISION_EXHORTO = re.compile(r"\bExh(?:orto)?\.?\s+(?:Admisi|Radica)", re.IGNORECASE)
# Cola del aviso: desde el conteo ("1 Acdo.", "2 Acdos.") o el número de
# expediente en adelante ya no hay descripción.
RE_COLA_DESCRIPCION = re.compile(
    r"\s\d+\s+(?:Acdos?|Audiencias?|Aud|Sent)\b|\bN[úu]m\.\s*Exp\.\s*\d+/\d{4}\s*$",
    re.IGNORECASE,
)


def _tramo(raw_text, demandada=None):
    """(tramo, es_ugj): lo que el aviso dice entre las partes y la cola."""
    t = raw_text or ""
    if demandada:
        i = t.rfind(demandada)
        if i >= 0:
            t = t[i + len(demandada):]
    partes = RE_VS.split(t)
    if len(partes) > 1:
        t = partes[-1]
    m_ugj = re.search(r"N[úu]m\.\s*Exp\.\s*\d+/\d{4},\s*(.+)$", t)
    if m_ugj:
        return re.split(r"\s\d+\s+(?:Acdos?|Audiencias?|Aud|Sent)\b", m_ugj.group(1))[0], True
    m_cola = RE_COLA_DESCRIPCION.search(t)
    return (t[:m_cola.start()] if m_cola else t), False


# Descripciones que no dicen nada sobre lo acordado.
_DESC_VACIA = re.compile(
    r"^(?:acuerdos?|acdos?\.?(?:\s+(?:i|amc|de\s+tr[aá]mite|\w{1,4}))?|autos?|tr[aá]mite|tomo\b.*|principal"
    r"|expdllo\.?|expedientillo|no\s+publicado|mal\s+publicado|\w{1,2})$",
    re.IGNORECASE,
)


# Los tramos se repiten muchísimo ("Ord. Civil Prevención"): caché por texto.
@functools.lru_cache(maxsize=300_000)
def _sin_tipo(tramo):
    """Quita del principio el tipo de juicio: el prefijo más largo que,
    expandidas las abreviaturas, es exactamente un tipo canónico
    ("Ord. Civil", "Cnpcyf - Especial de Arrendamiento Oral"). Si ningún
    prefijo lo es, corta en la primera palabra de corte de normalize_tipo
    (el mismo criterio con el que se recorta tipo_juicio)."""
    palabras = tramo.split()
    # El tipo empieza al principio del tramo o, en avisos sin "vs."
    # ("Pérez Juan su sucesión. Sucesorio Familiar ..."), después de un punto.
    inicios = [0] + [i for i in range(1, len(palabras)) if palabras[i - 1].endswith(".")]
    for i in inicios:
        fin = min(len(palabras), i + 8)  # un tipo tiene a lo sumo 8 palabras
        # Una sola expansión desde este inicio: si no empieza con un tipo
        # canónico, ningún prefijo más corto tampoco (antes se expandía cada
        # prefijo, 8 llamadas por inicio, y era lo más caro del build).
        if not match_canonical_prefix(expand_abbr(" ".join(palabras[i:fin])).strip(" .,-")):
            continue
        for k in range(fin, i, -1):
            e = expand_abbr(" ".join(palabras[i:k])).strip(" .,-")
            c = match_canonical_prefix(e)
            if c and len(c) >= len(e) - 1:
                return " ".join(palabras[k:]) or None
    inicio = next((i for i, w in enumerate(palabras) if i > 0 and w.rstrip(".,").lower() in STOP_WORDS), None)
    return " ".join(palabras[inicio:]) if inicio is not None else None


def descripcion(raw_text, demandada=None):
    """Lo que el órgano escribió sobre lo acordado, si lo escribió:
    "Prevención", "Desechamiento por No Desahogar Prevención", "Admisión
    Apel. Efecto Devoluctivo". En el formato normal va después del tipo de
    juicio. En las UGJ, después de "Núm. Exp. N,". None si no hay o si es
    genérica ("Acdo.", "Trámite")."""
    tramo, ugj = _tramo(raw_text, demandada)
    tramo = re.sub(r"#+", " ", tramo)
    tramo = re.sub(r"\s+", " ", tramo).strip(" .,;-")
    if not ugj:
        tramo = _sin_tipo(tramo)
        if not tramo:
            return None
    tramo = tramo.strip(" .,;-")
    # El juzgado a veces omite el verbo ("Especial Familiar por Revocadas
    # Autorizaciones" en vez de "Se tiene por..."): sin el "por" suelto se lee
    # mejor.
    tramo = re.sub(r"^(?:Acdos?\.?\s+)?(?:(?:por|de|del|y)\s+)?", "", tramo, flags=re.IGNORECASE)
    tramo = tramo[:1].upper() + tramo[1:]
    if len(tramo) < 3 or _DESC_VACIA.match(tramo):
        return None
    # Marcas de republicación ("no Publ. del 21 de Septiembre") y relleno.
    if re.match(r"(?:no|mal)\s+publ|no\s+definido", tramo, re.IGNORECASE):
        return None
    # Códigos de quien captura ("P- 71", "B-12").
    if re.fullmatch(r"[A-Z]{1,2}\s*-?\s*\d+", tramo):
        return None
    # Números de toca o de expediente: son restos de la cola, no descripción.
    if re.search(r"\d+[/-]\d{4}|N[úu]m\.\s*Exp", tramo):
        return None
    # Una sola palabra corta sin sentido propio: iniciales de quien firma
    # ("Nhl", "Jjvb", "P34").
    if " " not in tramo and len(tramo.rstrip(".")) <= 4:
        return None
    return tramo[:160]


def tipo_acuerdo(raw_text, demandada=None):
    """prevencion, negacion, admision o None. Mira solo el tramo entre las
    partes y la cola del aviso, para no confundir con nombres ("Instituto
    de Prevención...", "Desechables S.A."). Formato CNPCyF de las UGJ:
    "Núm. Exp. 3193/2026, Prevención." (la descripción va después)."""
    t = raw_text or ""
    if demandada:
        i = t.rfind(demandada)
        if i >= 0:
            t = t[i + len(demandada):]
    # Avisos fusionados con el anterior ("... 1 Acdo. Núm. Exp. 272/2019
    # no Publ. del ... Fulano vs. Mengano. Ord. Civil ..."): solo cuenta lo
    # que sigue al último "vs.".
    partes = RE_VS.split(t)
    if len(partes) > 1:
        t = partes[-1]
    m_ugj = re.search(r"N[úu]m\.\s*Exp\.\s*\d+/\d{4},\s*(.+)$", t)
    if m_ugj:
        tramo = m_ugj.group(1)
    else:
        m_cola = RE_COLA_DESCRIPCION.search(t)
        tramo = t[:m_cola.start()] if m_cola else t
    if RE_ACUERDO_NEGACION.search(tramo):
        return "negacion"
    if RE_ACUERDO_PREVENCION.search(tramo):
        return "prevencion"
    if RE_ACUERDO_ADMISION.search(tramo) and not RE_ADMISION_EXHORTO.search(tramo):
        return "admision"
    return None


# Número de expediente: el boletín pega al número lo que sigue en el aviso
# ("1002/2017 Tomo II", "153/2024 Inc", "39974/2026, Emplazar", "101/2026
# Amparo 667/2026 Quejosa..."). El número limpio es la clave del expediente
# (antes, con el resto pegado, un mismo expediente quedaba repartido en
# varias fichas). Lo que sigue es el cuaderno o tomo ("Tomo II", "Inc",
# "Principal"), salvo en las UGJ, donde después de la coma va la descripción
# de lo acordado (ya la toma descripcion()).
RE_NUMERO_EXP = re.compile(r"^\s*(\d+\s*[/-]\s*\d{4}(?:\s*[/-]\s*\d{1,3})?)(.*)$", re.DOTALL)


def separar_expediente(expediente):
    """(número, cuaderno). Si no empieza con un número N/AAAA, se devuelve
    tal cual y sin cuaderno."""
    if not expediente:
        return expediente, None
    m = RE_NUMERO_EXP.match(expediente)
    if not m:
        return expediente, None
    numero = re.sub(r"\s+", "", m.group(1)).replace("-", "/")
    resto = m.group(2)
    if resto.lstrip().startswith(","):
        return numero, None
    resto = re.sub(r"#+", " ", resto)
    resto = re.sub(r"\s+", " ", resto).strip(" .,;-")
    return numero, (resto[:120] or None)


# Nombres de partes que se tragaron el resto del aviso. Pasa cuando el
# boletín escribe la descripción antes del tipo de juicio ("Guzmán García
# Lorena. Aud. 272-B no Publ. ... Especial Familiar. 1 Audiencia.") o cuando
# no hay "vs." y el tipo se reconoce recién al final ("Pérez Vargas Saúl y
# Hernández Villegas Brenda. Especial Familiar Señalamiento ... Div."). Se
# corta el nombre en el primer punto seguido de un tipo de juicio reconocido
# o de una palabra que abre una descripción. Conservador: los nombres de
# empresas también tienen puntos ("S.A. de C.V. Institución...").
RE_INICIO_DESCRIPCION = re.compile(
    r"^(?:Aud(?:iencia)?\.?|Acdos?\.?|Acuerdos?|Prevenci[óo]n|Previene|Desech\w*|No\s+Ha\s+Lugar|Admisi[óo]n|Admisorio"
    r"|Señalamiento|Se\s+Tiene|T[ée]ngase|Se\s+Señala|Se\s+Ordena|Radicaci[óo]n)\b",
    re.IGNORECASE,
)


@functools.lru_cache(maxsize=300_000)
def separar_parte(nombre):
    """(nombre, sobrante). sobrante es lo que no era parte del nombre, o None."""
    if not nombre or ". " not in nombre:
        return nombre, None
    for m in re.finditer(r"\.\s+", nombre):
        resto = nombre[m.end():]
        if not resto:
            break
        corta = RE_INICIO_DESCRIPCION.match(resto) or match_canonical_prefix(expand_abbr(resto))
        if corta:
            antes = nombre[:m.start()].strip()
            # El punto se queda si cierra una sigla ("S.A. de C.V.").
            if "." in (antes.split() or [""])[-1]:
                antes += "."
            return antes, resto.strip()
    return nombre, None


def descripcion_de_sobrante(sobrante):
    """Descripción a partir de lo que se le sacó al nombre: sin el tipo de
    juicio del principio, sin la marca de republicación ("no Publ. del...")."""
    if not sobrante:
        return None
    t = _sin_tipo(sobrante) if match_canonical_prefix(expand_abbr(sobrante)) else sobrante
    t = re.split(r"\s+(?:no|mal)\s+Publ\b", t or "", flags=re.IGNORECASE)[0]
    t = re.sub(r"\s+", " ", t).strip(" .,;-")
    if len(t) < 3 or _DESC_VACIA.match(t) or re.search(r"N[úu]m\.\s*Exp", t):
        return None
    return t[:160]


# Marca de republicación pegada delante de la actora ("NO PUBLICADO González
# Ayala Néstor Raúl", "MAL PUBLICADO ..."): el aviso se vuelve a publicar
# porque salió mal o no salió. No es parte del nombre.
RE_MARCA_PUBLICACION = re.compile(r"^(?:NO|MAL)\s+PUBLICAD[OA]S?\b[.:,\s-]*")


def quitar_marca_publicacion(nombre):
    if not nombre:
        return nombre
    return RE_MARCA_PUBLICACION.sub("", nombre) or nombre
