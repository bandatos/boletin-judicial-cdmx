#!/usr/bin/env python3
"""
sivep.py — Cosecha las versiones públicas de sentencias del PJCDMX (SIVEP).

El SIVEP (http://sivepj.poderjudicialcdmx.gob.mx:819/consulta/) es una
página PHP con xajax. Se consulta por materia, juzgado, ejercicio y
trimestre. Cada fila trae un enlace al gestor documental con firma que
vence a los 10 minutos, así que el PDF se baja en el mismo momento en
que se consulta. El id estable de cada sentencia es el `documentPath`.

El número de expediente viene testado (la columna "Numero Exp." está
vacía): el emparejamiento con el boletín está en `sivep_match.py`.

Uso:
    python sivep.py materias
    python sivep.py juzgados PIC
    python sivep.py cosechar --materias PIC,PIF --juzgados 8PIC,3PIF \\
        --anios 2023,2024 [--trimestres T01,T02,T03,T04] [--sin-pdf]
    python sivep.py ocr [--paginas 2] [--workers 12] [--tika http://host:9998]

Archivos (bajo scraper/data/sivep/, fuera del repo):
    sivep.sqlite        tablas `sentencias` y `consultas`
    pdfs/<id>.pdf       el PDF tal como lo sirve el gestor documental
    pdfs/<id>.txt       texto extraído (OCR de las primeras N páginas o todo)

OCR: los PDF son escaneos (la capa de texto solo trae las firmas
electrónicas), así que hace falta OCR. Local: pdftoppm + tesseract con el
modelo `spa` (TESSDATA_PREFIX o --tessdata). En chocolatefactory: Tika con
OCR (`--tika URL`), que recibe el PDF por PUT /tika.
"""
import argparse
import html
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path

BASE = "http://sivepj.poderjudicialcdmx.gob.mx:819/consulta/"
UA = "Mozilla/5.0 (X11; Linux x86_64)"
PAUSA = 1.0  # segundos entre requests al SIVEP
DATA = Path(__file__).resolve().parent / "data" / "sivep"

# Materias que cubre el Boletín Judicial (civil y familiar, primera y
# segunda instancia). Las penales y laborales quedan fuera por ahora.
MATERIAS_BOLETIN = ["PIC", "PIF", "JCO", "JFO", "PC", "SC", "SF"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS sentencias (
    document_path   TEXT PRIMARY KEY,  -- id estable dentro del gestor documental
    archivo         TEXT,              -- nombre base local (pdfs/<archivo>.pdf)
    materia_cod     TEXT,
    juzgado_cod     TEXT,              -- código del SIVEP ("8PIC")
    juzgado_label   TEXT,              -- etiqueta del SIVEP ("8 civil")
    ejercicio       INTEGER,
    trimestre       TEXT,
    periodo_inicio  TEXT,              -- ISO
    periodo_fin     TEXT,
    materia         TEXT,
    tipo_juicio     TEXT,
    fecha_emision   TEXT,              -- ISO, según el SIVEP (no siempre coincide con el texto)
    numero_exp      TEXT,              -- siempre vacío hasta ahora (testado)
    organo          TEXT,
    juez            TEXT,
    origen          TEXT,
    relacionado     TEXT,              -- documentPath del documento relacionado, si hay
    area            TEXT,
    fecha_actualizacion TEXT,
    nota            TEXT,
    paginas         INTEGER,
    bytes           INTEGER,
    bajado_en       TEXT,
    ocr_paginas     INTEGER,           -- 0 = todas
    ocr_metodo      TEXT,
    visto_en        TEXT
);
CREATE TABLE IF NOT EXISTS consultas (
    materia_cod TEXT, juzgado_cod TEXT, anio INTEGER, trimestre TEXT,
    filas INTEGER, consultado_en TEXT,
    PRIMARY KEY (materia_cod, juzgado_cod, anio, trimestre)
);
"""

_ultimo = [0.0]


def _pausa():
    espera = PAUSA - (time.monotonic() - _ultimo[0])
    if espera > 0:
        time.sleep(espera)
    _ultimo[0] = time.monotonic()


def _req(url, data=None, timeout=90):
    _pausa()
    headers = {"User-Agent": UA, "Referer": BASE}
    if data is not None:
        data = urllib.parse.urlencode(data, safe="[]").encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=headers)
    for intento in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(), r.headers.get("Content-Type", "")
        except Exception as e:  # red inestable: reintentar con espera creciente
            if intento == 2:
                raise
            print(f"  reintento {intento + 1} ({e})", file=sys.stderr)
            time.sleep(5 * (intento + 1))


def _decode(raw):
    txt = raw.decode("latin-1")
    # soap.php devuelve UTF-8 con BOM, el xajax ISO-8859-1.
    if txt.startswith("ï»¿"):
        txt = raw.decode("utf-8-sig")
    return txt


def _options(txt):
    return [(v.strip('"'), html.unescape(t).strip())
            for v, t in re.findall(r'<option value=("?[^">]*"?)>([^<]*)', txt) if v.strip('"')]


def materias():
    raw, _ = _req(BASE + "soap.php", {"action": "materia"})
    return _options(_decode(raw))


def juzgados(materia):
    raw, _ = _req(BASE + "soapjuz.php?" + urllib.parse.urlencode({"materia": materia}))
    return _options(_decode(raw))


def _iso(fecha):
    m = re.match(r"(\d{2})-(\d{2})-(\d{4})", fecha or "")
    return f"{m[3]}-{m[2]}-{m[1]}" if m else (fecha or None)


def _clean(td):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", td))).strip()


def _doc_path(href):
    q = urllib.parse.parse_qs(urllib.parse.urlparse(html.unescape(href)).query)
    return q.get("documentPath", [None])[0]


def archivo_de(document_path):
    """'/Estructuras/TSJ/SIVEP/1/123/1302/144771/TSJCDMX_94cb..._134661_pdf.pdf'
    -> '1_123_1302_144771_TSJCDMX_94cb..._134661'"""
    p = document_path.split("/SIVEP/", 1)[-1]
    p = re.sub(r"_pdf\.pdf$|\.pdf$", "", p, flags=re.I)
    return re.sub(r"[^A-Za-z0-9_-]+", "_", p)


def buscar(materia_cod, materia_label, juzgado_cod, anio, trimestre):
    q = (f"<xjxquery><q>closeM=&selector=esSentencia&materia={materia_cod}"
         f"&juzgado={juzgado_cod}&anio={anio}&trimestre={trimestre}</q></xjxquery>")
    data = {"xajax": "searchSentencia", "xajaxr": str(int(time.time() * 1000)),
            "xajaxargs[]": q}
    # Dos argumentos con la misma clave: armar el cuerpo a mano.
    body = urllib.parse.urlencode(data) + "&" + urllib.parse.urlencode({"xajaxargs[]": materia_label})
    _pausa()
    req = urllib.request.Request(BASE, data=body.encode(), headers={
        "User-Agent": UA, "Referer": BASE,
        "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=180) as r:
        txt = r.read().decode("latin-1")
    m = re.search(r't="tValores" p="innerHTML"><!\[CDATA\[(.*?)\]\]>', txt, re.S)
    if not m:
        return []
    filas = []
    for tr in re.findall(r"<tr>(.*?)</tr>", m[1], re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) < 12:
            continue
        hrefs = re.findall(r"href='([^']+)'", tds[11])
        abbrs = re.findall(r"<abbr title='([^']+)'>(.*?)</abbr>", tds[11], re.S)
        url = rel = None
        for titulo, contenido in abbrs:
            h = re.search(r"href='([^']+)'", contenido)
            if not h:
                continue
            if titulo.lower().startswith("sentencia"):
                url = html.unescape(h[1])
            else:
                rel = _doc_path(h[1])
        if url is None and hrefs:
            url = html.unescape(hrefs[0])
        c = [_clean(t) for t in tds]
        filas.append({
            "ejercicio": int(c[1]) if c[1].isdigit() else None,
            "periodo_inicio": _iso(c[2]), "periodo_fin": _iso(c[3]),
            "materia": c[4], "tipo_juicio": c[5], "fecha_emision": _iso(c[6]),
            "numero_exp": c[7], "organo": c[8], "juez": c[9], "origen": c[10],
            "url": url, "document_path": _doc_path(url) if url else None,
            "relacionado": rel,
            "area": c[12] if len(c) > 12 else None,
            "fecha_actualizacion": _iso(c[13]) if len(c) > 13 else None,
            "nota": c[14] if len(c) > 14 else None,
        })
    return filas


def _paginas_pdf(path):
    try:
        out = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, timeout=30).stdout
        m = re.search(r"^Pages:\s+(\d+)", out, re.M)
        return int(m[1]) if m else None
    except Exception:
        return None


def bajar_pdf(url, destino):
    raw, ctype = _req(url, timeout=120)
    if not raw.startswith(b"%PDF"):
        raise RuntimeError(f"no es PDF ({ctype}, {len(raw)} bytes)")
    tmp = destino.with_suffix(".part")
    tmp.write_bytes(raw)
    tmp.rename(destino)
    return len(raw)


def db_open():
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "pdfs").mkdir(exist_ok=True)
    con = sqlite3.connect(DATA / "sivep.sqlite", timeout=60)
    con.executescript(SCHEMA)
    return con


def cosechar(args):
    con = db_open()
    mats = dict(materias())
    anios = [int(a) for a in args.anios.split(",")]
    trims = args.trimestres.split(",")
    sel_juz = set(args.juzgados.split(",")) if args.juzgados else None
    ahora = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    for mcod in args.materias.split(","):
        mlabel = mats.get(mcod)
        if not mlabel:
            print(f"materia desconocida: {mcod}", file=sys.stderr)
            continue
        for jcod, jlabel in juzgados(mcod):
            if sel_juz and jcod not in sel_juz:
                continue
            for anio in anios:
                for trim in trims:
                    hecho = con.execute(
                        "SELECT filas FROM consultas WHERE materia_cod=? AND juzgado_cod=? AND anio=? AND trimestre=?",
                        (mcod, jcod, anio, trim)).fetchone()
                    if hecho and not args.reconsultar:
                        continue
                    t0 = time.monotonic()
                    filas = buscar(mcod, mlabel, jcod, anio, trim)
                    nuevos = 0
                    for f in filas:
                        if not f["document_path"]:
                            continue
                        arch = archivo_de(f["document_path"])
                        pdf = DATA / "pdfs" / f"{arch}.pdf"
                        existe = con.execute("SELECT 1 FROM sentencias WHERE document_path=?",
                                             (f["document_path"],)).fetchone()
                        con.execute("""INSERT INTO sentencias (document_path, archivo, materia_cod, juzgado_cod,
                                juzgado_label, ejercicio, trimestre, periodo_inicio, periodo_fin, materia, tipo_juicio,
                                fecha_emision, numero_exp, organo, juez, origen, relacionado, area,
                                fecha_actualizacion, nota, visto_en)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                            ON CONFLICT(document_path) DO UPDATE SET visto_en=excluded.visto_en,
                                fecha_actualizacion=excluded.fecha_actualizacion, nota=excluded.nota""",
                            (f["document_path"], arch, mcod, jcod, jlabel, f["ejercicio"], trim,
                             f["periodo_inicio"], f["periodo_fin"], f["materia"], f["tipo_juicio"],
                             f["fecha_emision"], f["numero_exp"], f["organo"], f["juez"], f["origen"],
                             f["relacionado"], f["area"], f["fecha_actualizacion"], f["nota"], ahora()))
                        if not existe:
                            nuevos += 1
                        if args.sin_pdf or pdf.exists():
                            continue
                        try:
                            n = bajar_pdf(f["url"], pdf)
                            con.execute("UPDATE sentencias SET bytes=?, paginas=?, bajado_en=? WHERE document_path=?",
                                        (n, _paginas_pdf(pdf), ahora(), f["document_path"]))
                        except Exception as e:
                            print(f"  PDF falló {arch}: {e}", file=sys.stderr)
                    con.execute("INSERT OR REPLACE INTO consultas VALUES (?,?,?,?,?,?)",
                                (mcod, jcod, anio, trim, len(filas), ahora()))
                    con.commit()
                    print(f"{mcod} {jlabel} {anio} {trim}: {len(filas)} filas, {nuevos} nuevas "
                          f"({time.monotonic() - t0:.0f} s)", flush=True)
    con.close()


# --- OCR -----------------------------------------------------------------

def _ocr_tesseract(pdf, paginas, tessdata):
    env = dict(os.environ, OMP_THREAD_LIMIT="1")
    with tempfile.TemporaryDirectory() as tmp:
        cmd = ["pdftoppm", "-r", "200", "-gray", "-png"]
        if paginas:
            cmd += ["-f", "1", "-l", str(paginas)]
        subprocess.run(cmd + [str(pdf), f"{tmp}/p"], check=True, capture_output=True, timeout=600)
        textos = []
        for img in sorted(Path(tmp).glob("p-*.png"), key=lambda p: int(re.search(r"-(\d+)\.png$", p.name)[1])):
            tcmd = ["tesseract", str(img), "-", "-l", "spa"]
            if tessdata:
                tcmd[1:1] = ["--tessdata-dir", tessdata]
            r = subprocess.run(tcmd, capture_output=True, text=True, env=env, timeout=600)
            textos.append(r.stdout)
        return "\f".join(textos)


def _ocr_tika(pdf, tika_url):
    """Tika server con tesseract (chocolatefactory). Procesa el PDF entero."""
    req = urllib.request.Request(tika_url.rstrip("/") + "/tika", data=pdf.read_bytes(), method="PUT",
                                 headers={"Accept": "text/plain", "Content-Type": "application/pdf",
                                          "X-Tika-PDFOcrStrategy": "ocr_and_text_extraction",
                                          "X-Tika-OCRLanguage": "spa"})
    with urllib.request.urlopen(req, timeout=1800) as r:
        return r.read().decode("utf-8", "replace")


def _ocr_uno(job):
    archivo, paginas, tessdata, tika = job
    pdf = DATA / "pdfs" / f"{archivo}.pdf"
    txt = pdf.with_suffix(".txt")
    t0 = time.monotonic()
    try:
        texto = _ocr_tika(pdf, tika) if tika else _ocr_tesseract(pdf, paginas, tessdata)
    except Exception as e:
        return archivo, None, str(e), 0
    txt.write_text(texto, encoding="utf-8")
    return archivo, texto, None, time.monotonic() - t0


def ocr(args):
    con = db_open()
    paginas = 0 if args.tika else args.paginas
    metodo = "tika" if args.tika else "tesseract-spa"
    rows = con.execute("""SELECT archivo FROM sentencias WHERE bajado_en IS NOT NULL
        AND (ocr_metodo IS NULL OR (ocr_paginas != 0 AND (? = 0 OR ocr_paginas < ?)))""",
        (paginas, paginas)).fetchall()
    jobs = [(a, paginas, args.tessdata, args.tika) for (a,) in rows]
    print(f"OCR de {len(jobs)} PDF ({metodo}, páginas={paginas or 'todas'}, workers={args.workers})", flush=True)
    t0 = time.monotonic()
    hechos = 0
    with Pool(args.workers) as pool:
        for archivo, texto, err, seg in pool.imap_unordered(_ocr_uno, jobs):
            if err:
                print(f"  OCR falló {archivo}: {err}", file=sys.stderr)
                continue
            con.execute("UPDATE sentencias SET ocr_paginas=?, ocr_metodo=? WHERE archivo=?",
                        (paginas, metodo, archivo))
            hechos += 1
            if hechos % 25 == 0:
                con.commit()
                print(f"  {hechos}/{len(jobs)} ({time.monotonic() - t0:.0f} s)", flush=True)
    con.commit()
    print(f"OCR listo: {hechos} en {time.monotonic() - t0:.0f} s", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("materias")
    j = sub.add_parser("juzgados")
    j.add_argument("materia")
    c = sub.add_parser("cosechar")
    c.add_argument("--materias", default=",".join(MATERIAS_BOLETIN))
    c.add_argument("--juzgados", help="códigos del SIVEP separados por coma (8PIC,3PIF). Sin esto, todos.")
    c.add_argument("--anios", default=",".join(str(a) for a in range(2019, datetime.now().year + 1)))
    c.add_argument("--trimestres", default="T01,T02,T03,T04")
    c.add_argument("--sin-pdf", action="store_true", help="solo metadata")
    c.add_argument("--reconsultar", action="store_true", help="repetir consultas ya hechas")
    o = sub.add_parser("ocr")
    o.add_argument("--paginas", type=int, default=2, help="primeras N páginas (0 = todas). Para emparejar alcanzan 2.")
    o.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    o.add_argument("--tessdata", default=os.environ.get("TESSDATA_PREFIX"))
    o.add_argument("--tika", help="URL de un Tika server con OCR (procesa el PDF entero)")
    a = ap.parse_args()
    if a.cmd == "materias":
        for cod, label in materias():
            print(f"{cod}\t{label}")
    elif a.cmd == "juzgados":
        for cod, label in juzgados(a.materia):
            print(f"{cod}\t{label}")
    elif a.cmd == "cosechar":
        cosechar(a)
    elif a.cmd == "ocr":
        ocr(a)


if __name__ == "__main__":
    main()
