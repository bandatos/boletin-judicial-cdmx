#!/usr/bin/env python3
"""
sivep_match.py — Empareja versiones públicas del SIVEP con avisos de
sentencia del Boletín Judicial.

El SIVEP testa el número de expediente. Lo que queda en el texto (OCR) y en
la metadata alcanza en muchos casos para encontrar el aviso:

- órgano (metadata del SIVEP, mapeado al nombre del boletín),
- fecha de la sentencia ("Ciudad de México, a veinticuatro de noviembre de
  dos mil veintitrés"): el aviso sale en el boletín de 0 a 10 días hábiles
  después, y su "ACUERDOS DEL ..." (fecha_acuerdo) suele ser esa misma fecha,
- tipo de juicio (metadata y "VISTOS los autos del JUICIO ..."),
- definitiva o interlocutoria,
- fecha de presentación de la demanda: el año del expediente suele ser ese,
- nombres de empresas que el testado deja visibles.

La fecha de emisión de la metadata del SIVEP no es confiable (hay casos
con un año de diferencia contra el texto), se usa solo si el OCR no da fecha.

Uso:
    python sivep_match.py [--db ../boletin_completo.v2.sqlite] [--archivo ID] [-v]

Escribe la tabla `emparejamientos` en data/sivep/sivep.sqlite.
"""
import argparse
import pickle
import re
import sqlite3
import sys
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "sivep"
DB_BOLETIN = Path(__file__).resolve().parent.parent / "boletin_completo.v2.sqlite"
MAX_DIAS_HABILES = 10

# --- números y fechas en letras -------------------------------------------

_UNIDADES = {
    "un": 1, "uno": 1, "primero": 1, "primer": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12,
    "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17,
    "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiun": 21, "veintiuno": 21,
    "veintidos": 22, "veintitres": 23, "veinticuatro": 24, "veinticinco": 25,
    "veintiseis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29, "treinta": 30,
}
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", s)


def _num(txt):
    txt = txt.strip()
    if txt.isdigit():
        return int(txt)
    total = 0
    for parte in re.split(r"\s+y\s+", txt):
        if parte not in _UNIDADES:
            return None
        total += _UNIDADES[parte]
    return total


def _anio(txt):
    txt = txt.strip()
    if re.fullmatch(r"\d{4}", txt):
        return int(txt)
    m = re.fullmatch(r"dos mil(?: (.+))?", txt)
    if not m:
        return None
    if not m[1]:
        return 2000
    n = _num(m[1])
    return 2000 + n if n is not None and n < 100 else None


_PAL = r"[a-z]+(?: y [a-z]+)?"
RE_FECHA = re.compile(
    rf"(?P<dia>\d{{1,2}}|{_PAL})\s+de\s+(?P<mes>{'|'.join(_MESES)})\s+(?:de|del)\s+(?:ano\s+)?"
    rf"(?P<anio>\d{{4}}|dos mil(?: [a-z]+(?: y [a-z]+)?)?)\b")


def _fecha(m):
    d, a = _num(m["dia"]), _anio(m["anio"])
    if d is None or a is None or not 1 <= d <= 31:
        return None
    try:
        return date(a, _MESES.index(m["mes"]) + 1, d)
    except ValueError:
        return None


def fecha_sentencia(t):
    """Primera fecha después de "Ciudad de México, a". Si no hay, la primera
    fecha del texto que no sea de presentación de un escrito."""
    m = re.search(r"(?:ciudad de mexico|cdmx|mexico, d\.? ?f\.?)[,.]?\s+a\s+(?:los\s+)?", t[:6000])
    if m:
        f = RE_FECHA.match(t, m.end())
        if f and _fecha(f):
            return _fecha(f)
    for f in RE_FECHA.finditer(t[:4000]):
        antes = t[max(0, f.start() - 60):f.start()]
        if "presentad" in antes or "recibid" in antes:
            continue
        if _fecha(f):
            return _fecha(f)
    return None


def fecha_demanda(t):
    for m in re.finditer(r"(?:escrito\s+(?:inicial\s+)?(?:de\s+demanda\s+)?)?presentad[oa]\s+(?:ante\s+[^.]{0,120}?\s+)?"
                         r"(?:el\s+)?(?:dia\s+)?", t[:12000]):
        f = RE_FECHA.match(t, m.end())
        if f and _fecha(f):
            return _fecha(f)
    return None


def tipo_vistos(t):
    m = re.search(r"vistos?[.,]?\s+(?:para resolver\s+)?(?:en\s+)?(?:los|las)\s+autos\s+(?:relativos\s+)?(?:al|del)\s+"
                  r"(?:juicio\S*\s+)?(.{3,80}?)(?:[,.]|\s+(?:seguido|promovido|instaurado|que\s))", t[:6000])
    return m[1].strip(" .,-—") if m else None


def clase_sentencia(t):
    head = t[:6000]
    if "interlocutoria" in head:
        return "interlocutoria"
    if "definitiva" in head:
        return "definitiva"
    return None


# --- tipo de juicio -------------------------------------------------------

_STOP_TIPO = {"juicio", "de", "del", "la", "el", "los", "las", "en", "via", "y", "a", "civil", "familiar",
              "especial", "procedimiento", "judicial", "relativos", "al", "sobre", "accion", "materia",
              "oral", "controversia", "orden", "acciones"}


def tokens_tipo(s):
    return {w for w in re.findall(r"[a-z]{3,}", norm(s)) if w not in _STOP_TIPO}


def compat_tipo(sivep, boletin):
    a, b = tokens_tipo(sivep), tokens_tipo(boletin)
    if not a or not b:
        return 0
    inter = a & b
    if inter == a or inter == b:
        return 2
    return 1 if inter else -4


# --- órganos --------------------------------------------------------------

_ORD = {1: "PRIMERO", 2: "SEGUNDO", 3: "TERCERO", 4: "CUARTO", 5: "QUINTO", 6: "SEXTO", 7: "SÉPTIMO",
        8: "OCTAVO", 9: "NOVENO"}
_DECENAS = {10: "DÉCIMO", 20: "VIGÉSIMO", 30: "TRIGÉSIMO", 40: "CUADRAGÉSIMO", 50: "QUINCUAGÉSIMO",
            60: "SEXAGÉSIMO", 70: "SEPTUAGÉSIMO", 80: "OCTOGÉSIMO"}


def ordinal(n, femenino=False):
    d, u = n - n % 10, n % 10
    partes = ([_DECENAS[d]] if d else []) + ([_ORD[u]] if u else [])
    s = " ".join(partes)
    if femenino:
        s = re.sub(r"O\b", "A", s)
    return s


def organos_boletin(juzgado_cod, juzgado_label):
    """Patrón (regex sobre el nombre del órgano en el boletín) para un juzgado del SIVEP."""
    m = re.match(r"(\d+)", juzgado_label or "")
    n = int(m[1]) if m else None
    mat = re.sub(r"^\d+", "", juzgado_cod)
    if not n:
        return None
    o, oa = ordinal(n), ordinal(n, femenino=True)
    return {
        "PIC": rf"^{o} DE LO CIVIL(?: DE PROCESO ESCRITO)?\W*$",
        "PIF": rf"^{o} DE LO FAMILIAR\W*$",
        "JCO": rf"^{o} DE LO CIVIL DE PROCESO ORAL\W*$",
        "JFO": rf"^{o} (?:DE PROCESO ORAL EN MATERIA FAMILIAR|DE LO FAMILIAR DE PROCESO ORAL)\W*$",
        "PC": rf"^{o} DE LO CIVIL DE CUANT[IÍ]A(?: ME[N]?OR)?\W*$",
        "SC": rf"^{oa} SALA CIVIL\W*$",
        "SF": rf"^{oa} SALA FAMILIAR\W*$",
    }.get(mat)


# --- candidatos del boletín -------------------------------------------------

def _cache_path(organo):
    return DATA / "cache" / (re.sub(r"\W+", "_", organo) + ".pkl")


def avisos_boletin(con, nombres):
    """Todos los avisos de esos órganos (cualquier tipo de actuación: hay
    sentencias que el boletín publica como "1 Acdo."), con caché en disco."""
    out = []
    for organo in nombres:
        cp = _cache_path(organo)
        if cp.exists():
            out += pickle.loads(cp.read_bytes())
            continue
        t0 = time.monotonic()
        col = "sala" if " SALA " in f" {organo} " else "juzgado"
        rows = con.execute(f"""
            SELECT e.id, e.{col}, e.expediente, e.anio, e.tipo_juicio_norm, e.tipo_juicio, e.tipo_actuacion,
                   b.fecha_iso, e.fecha_acuerdo, e.actora, e.demandada, e.boletin_id
            FROM entradas e JOIN boletines b ON b.id = e.boletin_id
            WHERE e.{col} = ?""", (organo,)).fetchall()
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_bytes(pickle.dumps(rows))
        print(f"  {organo}: {len(rows)} avisos ({time.monotonic() - t0:.0f} s)", file=sys.stderr)
        out += rows
    return out


class Organo:
    """Avisos de un juzgado indexados por fecha de publicación, y fecha del
    primer aviso de cada expediente (aproxima la radicación)."""

    def __init__(self, rows):
        self.por_fecha = {}
        self.primero = {}
        for r in rows:
            if not r[7]:
                continue
            self.por_fecha.setdefault(r[7], []).append(r)
            k = expediente_corto(r[2])
            if k not in self.primero or r[7] < self.primero[k]:
                self.primero[k] = r[7]

    def ventana(self, desde, dias):
        d = desde
        for _ in range(dias * 2 + 6):
            for r in self.por_fecha.get(d.isoformat(), ()):
                yield r
            d += timedelta(days=1)


def dias_habiles(a, b):
    """Días hábiles (lunes a viernes) de a hasta b. Negativo si b < a."""
    if b < a:
        return -dias_habiles(b, a)
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


_STOP_PARTES = set("""sociedad anonima institucion banca multiple grupo financiero capital variable
    civil sucesion mexico tambien conocido conocida como denominacion antes ahora representante
    legal apoderado tercera tomo secretario proyectista secretaria instituto fondo nacional vivienda
    trabajadores enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre
    diciembre caracter numero estado ciudad credito banco federal gobierno registro publico propiedad
    comercio director general juzgado amparo""".split())

COBERTURA_DESDE = date(2016, 10, 1)  # primer boletín de la base


def expediente_corto(exp):
    m = re.match(r"\s*(\d+/\d{4})", exp or "")
    return m[1] if m else (exp or "").strip()[:20]


def puntuar(s, c, org):
    """s: datos de la versión pública. c: fila del boletín. org: Organo."""
    (_id, organo, exp, anio, tnorm, tjuicio, tact, fpub, facdo, actora, demandada, _bid) = c
    razones = []
    pts = 0.0
    dh = dias_habiles(s["fecha"], date.fromisoformat(fpub))
    if facdo and facdo == s["fecha"].isoformat():
        pts += 3
        razones.append("acdo=fecha")
    else:
        pts += 1.0 / (1 + dh)
    tc = compat_tipo(s["tipo"], tnorm or tjuicio or "")
    if tc == 0 and s.get("tipo_ocr"):
        tc = compat_tipo(s["tipo_ocr"], tnorm or tjuicio or "")
    pts += tc
    razones.append(f"tipo{tc:+d}")
    tact = tact or "acuerdo"
    if not tact.startswith("sentencia"):
        pts -= 2
        razones.append(tact)
    elif s["clase"] and tact != "sentencia":
        if tact.endswith(s["clase"]):
            pts += 1
            razones.append("clase")
        else:
            pts -= 2
            razones.append("clase≠")
    dem = s["demanda"]
    if dem and anio:
        d = anio - dem.year
        if d == 0:
            pts += 3
            razones.append("año=demanda")
        elif d == 1 and dem.month == 12:
            pts += 2
            razones.append("año=demanda+1")
        else:
            pts -= 3
            razones.append(f"año{d:+d}")
    elif anio and anio > s["fecha"].year:
        pts -= 5
    prim = org.primero.get(expediente_corto(exp))
    if dem and prim and dem > COBERTURA_DESDE + timedelta(days=30):
        delta = (date.fromisoformat(prim) - dem).days
        # El primer aviso sale días después de presentada la demanda: cuanto
        # más cerca, más probable que sea ese expediente (la numeración es
        # secuencial dentro del año).
        r = -3.0 if delta < -3 else max(-2.0, 3 - max(0, delta - 5) / 15)
        pts += r
        razones.append(f"radicación{delta:+d}d({r:+.1f})")
    hits = {w for w in re.findall(r"[a-z]{5,}", norm(f"{actora} {demandada}"))
            if w not in _STOP_PARTES and w in s["palabras"] and s["df"].get(w, 0) <= s["df_max"]}
    if hits:
        pts += min(3, len(hits))
        razones.append("partes:" + ",".join(sorted(hits))[:60])
    return pts, dh, razones


def _candidatos(s, org, solo_sentencias):
    vistos = {}
    for c in org.ventana(s["fecha"], MAX_DIAS_HABILES):
        if solo_sentencias and not (c[6] or "").startswith("sentencia"):
            continue
        dh = dias_habiles(s["fecha"], date.fromisoformat(c[7]))
        if dh < 0 or dh > MAX_DIAS_HABILES:
            continue
        pts, dh, raz = puntuar(s, c, org)
        clave = expediente_corto(c[2])  # tercerías, tomos y líneas repetidas del mismo expediente
        if clave not in vistos or vistos[clave][0] < pts:
            vistos[clave] = (pts, dh, raz, c)
    return sorted(vistos.values(), key=lambda x: -x[0])


def emparejar(s, org):
    """Primero entre los avisos de sentencia. Si ninguno alcanza, entre todos
    los avisos (sentencias publicadas como "1 Acdo."), con resultado
    `unico_debil`: sirve para revisar, no para publicar sin verificar."""
    orden = _candidatos(s, org, True)
    validos = [x for x in orden if x[0] >= 5]
    if validos:
        if len(validos) == 1 or validos[0][0] - validos[1][0] >= 2:
            return "unico", orden[:3]
        return "ambiguo", orden[:3]
    todos = _candidatos(s, org, False)
    validos = [x for x in todos if x[0] >= 5]
    if validos and (len(validos) == 1 or validos[0][0] - validos[1][0] >= 3):
        return "unico_debil", todos[:3]
    return "sin_candidato", (todos or orden)[:3]


SCHEMA = """
CREATE TABLE IF NOT EXISTS emparejamientos (
    document_path TEXT PRIMARY KEY,
    fecha_sentencia TEXT, fecha_fuente TEXT, fecha_demanda TEXT, tipo_ocr TEXT, clase TEXT,
    resultado TEXT,          -- unico, ambiguo, sin_candidato, sin_fecha, sin_organo
    entrada_id INTEGER, organo TEXT, expediente TEXT, fecha_boletin TEXT,
    puntaje REAL, segundo REAL, candidatos INTEGER, razones TEXT
);
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_BOLETIN))
    ap.add_argument("--archivo", help="solo esta versión pública (archivo o document_path)")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    sv = sqlite3.connect(DATA / "sivep.sqlite", timeout=60)
    sv.executescript(SCHEMA)
    bol = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    organos_todos = [r[0] for r in bol.execute("SELECT DISTINCT organo FROM organo_mes")]
    q = "SELECT document_path, archivo, juzgado_cod, juzgado_label, tipo_juicio, fecha_emision FROM sentencias WHERE ocr_metodo IS NOT NULL"
    params = ()
    if a.archivo:
        q += " AND (archivo = ? OR document_path = ?)"
        params = (a.archivo, a.archivo)
    filas = sv.execute(q, params).fetchall()
    textos = {}
    df = {}
    for dp, arch, *_ in filas:
        tp = DATA / "pdfs" / f"{arch}.txt"
        if tp.exists():
            t = norm(tp.read_text(encoding="utf-8", errors="replace"))
            textos[dp] = t
            for w in set(re.findall(r"[a-z]{5,}", t)):
                df[w] = df.get(w, 0) + 1
    df_max = max(2, len(textos) // 50)  # palabra presente en menos del 2 % de las sentencias
    grupos = {}
    for fila in filas:
        if fila[0] in textos:
            grupos.setdefault(organos_boletin(fila[2], fila[3]), []).append(fila)
    cuenta = {}
    for pat, grupo in grupos.items():
        org = None
        if pat:
            nombres = [o for o in organos_todos if re.match(pat, o)]
            org = Organo(avisos_boletin(bol, nombres))
        for dp, arch, jcod, jlabel, tipo, femision in grupo:
            texto = textos[dp]
            f = fecha_sentencia(texto)
            fuente = "texto"
            if not f and femision:
                f, fuente = date.fromisoformat(femision), "metadata"
            s = {"fecha": f, "demanda": fecha_demanda(texto), "tipo": tipo, "tipo_ocr": tipo_vistos(texto),
                 "clase": clase_sentencia(texto), "palabras": set(re.findall(r"[a-z]{5,}", texto)),
                 "df": df, "df_max": df_max}
            reg = dict(document_path=dp, fecha_sentencia=f and f.isoformat(), fecha_fuente=fuente,
                       fecha_demanda=s["demanda"] and s["demanda"].isoformat(), tipo_ocr=s["tipo_ocr"],
                       clase=s["clase"], resultado=None, entrada_id=None, organo=None, expediente=None,
                       fecha_boletin=None, puntaje=None, segundo=None, candidatos=0, razones=None)
            if not f:
                reg["resultado"] = "sin_fecha"
            elif not org:
                reg["resultado"] = "sin_organo"
            else:
                res, top = emparejar(s, org)
                reg["resultado"] = res
                reg["candidatos"] = len(top)
                if top:
                    pts, dh, raz, c = top[0]
                    reg.update(entrada_id=c[0], organo=c[1], expediente=expediente_corto(c[2]),
                               fecha_boletin=c[7], puntaje=pts, segundo=top[1][0] if len(top) > 1 else None,
                               razones=f"dh={dh} " + " ".join(raz))
                if a.v:
                    print(f"{arch[:40]} {jlabel} sent={f} dem={s['demanda']} {tipo}/{s['tipo_ocr']} {s['clase']} -> {res}")
                    for pts, dh, raz, c in top:
                        print(f"    {pts:5.2f} {c[7]} {c[2][:30]} {c[4]} {c[6]} acdo={c[8]} {' '.join(raz)}")
            cuenta[reg["resultado"]] = cuenta.get(reg["resultado"], 0) + 1
            cols = list(reg)
            sv.execute(f"INSERT OR REPLACE INTO emparejamientos ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                       [reg[k] for k in cols])
    sv.commit()
    print(cuenta)


if __name__ == "__main__":
    main()
