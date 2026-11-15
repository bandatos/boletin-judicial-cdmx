#!/usr/bin/env python3
"""
sivep_enlace.py — Enlaza versiones públicas del SIVEP con avisos de
sentencia del boletín usando solo la metadata del SIVEP, sin OCR.

La "fecha de emisión" del SIVEP sigue dos convenciones, mezcladas incluso
dentro de un mismo juzgado y año (medido contra los emparejamientos con OCR
de sivep_match.py, ver docs/sivep-versiones-publicas.md):
  - la fecha del boletín que publicó el aviso (la mayoría), o
  - la fecha de la sentencia, que es la del "ACUERDOS DEL ..." del aviso
    (fecha_acuerdo), publicado de 1 a 3 días hábiles después.
Candidatos: avisos de sentencia del mismo órgano con tipo de juicio
compatible que cumplen cualquiera de las dos. Si entre varios uno solo tiene
el tipo de juicio exacto, queda ese. Si la fecha cae después del fin del
período informado (error de carga, a veces con el año corrido), se prueba
con el año anterior.

Resultado por versión pública:
  unico          un solo expediente candidato
  ambiguo        más de uno (no se enlaza)
  sin_candidato  ninguno (sentencia publicada como "1 Acdo.", fecha mal cargada en
                 el SIVEP, órgano que el mapeo no reconoce...)

Solo los `unico` se publican como enlace en la ficha. La URL del PDF vence a
los 10 minutos, así que se guarda lo necesario para pedir una nueva al SIVEP
(materia, juzgado, ejercicio, trimestre y document_path) y el server la
regenera al hacer clic.

Uso:
    python sivep_enlace.py [--db ../boletin_completo.v2.sqlite] [-v]

Escribe la tabla `enlaces` en data/sivep/sivep.sqlite.
"""
import argparse
import re
import sqlite3
from datetime import date, timedelta

from sivep_match import DATA, DB_BOLETIN, Organo, avisos_boletin, compat_tipo, expediente_corto, organos_boletin

SENTENCIAS = ("sentencia_definitiva", "sentencia")

SCHEMA = """
CREATE TABLE IF NOT EXISTS enlaces (
    document_path TEXT PRIMARY KEY,
    resultado     TEXT,    -- unico, ambiguo, sin_candidato, sin_organo, sin_fecha (o fecha fuera del período)
    entrada_id    INTEGER, -- entradas.id del aviso de sentencia (si es unico)
    organo        TEXT,
    expediente    TEXT,
    fecha_boletin TEXT,
    candidatos    INTEGER,
    desfase       INTEGER  -- días hábiles entre fecha_emision y el boletín (0 casi siempre)
);
"""


def dia_habil(d, n):
    """d corrido n días hábiles (n puede ser negativo)."""
    paso = 1 if n >= 0 else -1
    while n:
        d += timedelta(days=paso)
        if d.weekday() < 5:
            n -= paso
    return d


def candidatos(org, fecha, tipo):
    """Expedientes con aviso de sentencia publicado en el boletín de esa
    fecha, o con "ACUERDOS DEL <fecha>" publicado en los 3 días hábiles
    siguientes, y tipo de juicio compatible (compat_tipo >= 0: sin dato
    también cuenta). Devuelve {expediente: (compat_tipo, fila)}."""
    filas = list(org.por_fecha.get(fecha.isoformat(), ()))
    for k in (1, 2, 3):
        filas += [r for r in org.por_fecha.get(dia_habil(fecha, k).isoformat(), ()) if r[8] == fecha.isoformat()]
    vistos = {}
    for r in filas:
        if r[6] not in SENTENCIAS:
            continue
        tc = compat_tipo(tipo or "", r[4] or r[5] or "")
        if tc < 0:
            continue
        k = expediente_corto(r[2])
        if k not in vistos or vistos[k][0] < tc:
            vistos[k] = (tc, r)
    return vistos


def fecha_confiable(femision, periodo_fin):
    """La fecha de emisión, o la del año anterior si cae después del fin del
    período informado (error de carga con el año corrido). None si tampoco."""
    if not femision:
        return None
    f = date.fromisoformat(femision)
    if not periodo_fin or femision <= periodo_fin:
        return f
    try:
        f = f.replace(year=f.year - 1)
    except ValueError:  # 29 de febrero
        return None
    return f if f.isoformat() <= periodo_fin else None


def enlazar(org, femision, tipo):
    """Sin corrimiento de día: con -1 o +1 aparecían más únicos, pero
    fallaban más contra el OCR (precisión de 86 % contra 95 %)."""
    cands = candidatos(org, femision, tipo)
    if len(cands) > 1:
        exactos = {k: v for k, v in cands.items() if v[0] == 2}
        if len(exactos) == 1:
            cands = exactos
    filas = [v[1] for v in cands.values()]
    if filas:
        return ("unico" if len(filas) == 1 else "ambiguo"), filas, 0
    return "sin_candidato", [], None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_BOLETIN))
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    sv = sqlite3.connect(DATA / "sivep.sqlite", timeout=60)
    sv.executescript(SCHEMA)
    bol = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    organos_todos = [r[0] for r in bol.execute("SELECT DISTINCT organo FROM organo_mes")]
    filas = sv.execute("""
        SELECT document_path, juzgado_cod, juzgado_label, tipo_juicio, fecha_emision, periodo_fin
        FROM sentencias""").fetchall()
    grupos = {}
    for f in filas:
        grupos.setdefault(organos_boletin(f[1], f[2]), []).append(f)
    cuenta = {}
    for pat, grupo in grupos.items():
        org = None
        if pat:
            nombres = [o for o in organos_todos if re.match(pat, o)]
            org = Organo(avisos_boletin(bol, nombres)) if nombres else None
        for dp, jcod, jlabel, tipo, femision, periodo_fin in grupo:
            reg = dict(document_path=dp, resultado=None, entrada_id=None, organo=None, expediente=None,
                       fecha_boletin=None, candidatos=0, desfase=None)
            f = fecha_confiable(femision, periodo_fin)
            if not org:
                reg["resultado"] = "sin_organo"
            elif not f:
                reg["resultado"] = "sin_fecha"
            else:
                res, cands, desfase = enlazar(org, f, tipo)
                reg.update(resultado=res, candidatos=len(cands), desfase=desfase)
                if res == "unico":
                    c = cands[0]
                    reg.update(entrada_id=c[0], organo=c[1], expediente=expediente_corto(c[2]), fecha_boletin=c[7])
                if a.v:
                    print(f"{jlabel} {femision} {tipo} -> {res} {[(c[2][:20], c[4]) for c in cands][:4]}")
            cuenta[reg["resultado"]] = cuenta.get(reg["resultado"], 0) + 1
            cols = list(reg)
            sv.execute(f"INSERT OR REPLACE INTO enlaces ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                       [reg[k] for k in cols])
    sv.commit()
    print(cuenta)


if __name__ == "__main__":
    main()
