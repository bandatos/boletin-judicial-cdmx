#!/usr/bin/env python3
"""Corrida diaria: baja los boletines nuevos, los suma a la base con
build_db.py --incremental, la publica si pasa los chequeos y manda un resumen
por correo.

Pensado para correr como tarea programada de Coolify dentro del contenedor
del sitio (ver docs/actualizacion-diaria.md), pero anda en cualquier lado con
las variables de entorno:

  DATA_DIR          carpeta con boletin.sqlite y su .estado.sqlite (/data)
  SCRAPER_DATA_DIR  carpeta de corridas del scraper (/data/scraper-data)
  SIVEP_DB          sivep.sqlite para enlazar versiones públicas (opcional)
  MSMTP_HOST, MSMTP_PORT, MSMTP_USER, MSMTP_PASSWORD, MSMTP_FROM
  RESUMEN_PARA      destinatarios del resumen, separados por coma

Nunca toca la base publicada hasta el final: trabaja sobre una copia y la
cambia con un rename (el sitio la recarga solo). La anterior queda como
boletin.sqlite.anterior.

Uso: python3 diario.py [--sin-publicar] [--sin-correo] [--desde AAAA-MM-DD]
"""
import argparse
import fcntl
import gzip
import os
import shutil
import smtplib
import sqlite3
import subprocess
import sys
import time
import traceback
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

AQUI = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
SCRAPER_DATA_DIR = Path(os.environ.get("SCRAPER_DATA_DIR", DATA_DIR / "scraper-data"))
SIVEP_DB = os.environ.get("SIVEP_DB", str(DATA_DIR / "sivep.sqlite"))
BASE = DATA_DIR / "boletin.sqlite"
ESTADO = DATA_DIR / "boletin.sqlite.estado.sqlite"
TRABAJO = DATA_DIR / "trabajo"

# Lo que se compara entre la base publicada y la nueva: ninguna puede bajar.
CUENTAS = {
    "avisos": "SELECT COUNT(*) FROM entradas",
    "boletines con avisos": "SELECT COUNT(*) FROM boletines WHERE n_entradas > 0",
    "empresas": "SELECT COUNT(*) FROM empresas",
    "personas": "SELECT COUNT(*) FROM personas",
    "palabras del vocabulario": "SELECT COUNT(*) FROM vocabulario",
    "sentencias con versión pública": "SELECT COUNT(*) FROM sentencias_publicas",
}


class Corrida:
    def __init__(self):
        self.inicio = time.monotonic()
        self.pasos = []      # (paso, segundos)
        self.lineas = []     # log completo
        self.datos = {}

    def log(self, msg):
        linea = f"[{time.monotonic() - self.inicio:6.0f} s] {msg}"
        print(linea, flush=True)
        self.lineas.append(linea)

    def paso(self, nombre, fn, *a, **kw):
        self.log(f"== {nombre}")
        t = time.monotonic()
        try:
            return fn(*a, **kw)
        finally:
            self.pasos.append((nombre, time.monotonic() - t))


def cuentas(db):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out = {}
    for k, sql in CUENTAS.items():
        try:
            out[k] = conn.execute(sql).fetchone()[0]
        except sqlite3.OperationalError:
            out[k] = None
    out["último boletín"] = conn.execute(
        "SELECT MAX(fecha_iso) FROM boletines WHERE n_entradas > 0").fetchone()[0]
    out["max_id"] = conn.execute("SELECT COALESCE(MAX(id), 0) FROM entradas").fetchone()[0]
    conn.close()
    return out


def correr(c, cmd, env=None, cwd=AQUI):
    """Corre un comando y pasa su salida al log, línea por línea."""
    c.log("$ " + " ".join(cmd))
    p = subprocess.Popen(cmd, cwd=cwd, env={**os.environ, **(env or {})}, text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, errors="replace")
    for linea in p.stdout:
        c.log("  " + linea.rstrip())
    if p.wait():
        raise RuntimeError(f"{cmd[1]} terminó con código {p.returncode}")


def descargar(c, desde, hasta, run_dir):
    correr(c, [sys.executable, "poc.py", desde, hasta, "500", str(run_dir)])
    # Los PDF ya no hacen falta: el texto queda en .txt (y después en .txt.gz).
    for pdf in (run_dir / "pdfs").glob("*.pdf"):
        pdf.unlink()


def sumar(c, base):
    cmd = [sys.executable, "build_db.py", "--dir", str(SCRAPER_DATA_DIR), "--incremental",
           "--output", str(base), "--indices-server", "--empresas", "--estadisticas", "--personas",
           "--workers", os.environ.get("BUILD_WORKERS", "4")]
    if Path(SIVEP_DB).exists():
        cmd += ["--sivep", SIVEP_DB]
    # Poca memoria: el server comparte 7 GB con el sitio.
    correr(c, cmd, env={"BUILD_CACHE_MB": os.environ.get("BUILD_CACHE_MB", "300"),
                        "BUILD_ETAPAS_A_LA_VEZ": os.environ.get("BUILD_ETAPAS_A_LA_VEZ", "1"),
                        "SQLITE_TMPDIR": str(TRABAJO)})


def nuevos(db, desde_id):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    boletines = conn.execute("""
        SELECT b.id, b.fecha_iso, COUNT(*) FROM entradas e JOIN boletines b ON b.id = e.boletin_id
        WHERE e.id > ? GROUP BY b.id ORDER BY b.fecha_iso""", (desde_id,)).fetchall()
    actuaciones = conn.execute("""
        SELECT COALESCE(tipo_actuacion, 'acuerdo'), COUNT(*) FROM entradas WHERE id > ?
        GROUP BY 1 ORDER BY 2 DESC""", (desde_id,)).fetchall()
    tipos_acuerdo = conn.execute("""
        SELECT tipo_acuerdo, COUNT(*) FROM entradas WHERE id > ? AND tipo_acuerdo IS NOT NULL
        GROUP BY 1 ORDER BY 2 DESC""", (desde_id,)).fetchall()
    prueba = conn.execute(
        "SELECT COUNT(*) FROM entradas_fts WHERE entradas_fts MATCH 'banco' AND rowid > ?",
        (desde_id,)).fetchone()[0]
    conn.close()
    return boletines, actuaciones, tipos_acuerdo, prueba


def chequear(antes, despues):
    problemas = []
    for k in CUENTAS:
        if antes.get(k) is not None and (despues.get(k) or 0) < antes[k]:
            problemas.append(f"{k} bajó de {antes[k]:,} a {despues.get(k) or 0:,}")
    if despues["avisos"] <= antes["avisos"]:
        problemas.append("no sumó avisos")
    return problemas


def publicar(c, trabajo, trabajo_estado):
    anterior = BASE.with_name(BASE.name + ".anterior")
    anterior.unlink(missing_ok=True)
    os.link(BASE, anterior)
    os.replace(trabajo_estado, ESTADO)
    os.replace(trabajo, BASE)
    c.log(f"publicada, la anterior queda en {anterior.name}")


def comprimir_textos(run_dir):
    for txt in (run_dir / "pdfs").glob("*.txt"):
        with open(txt, "rb") as f, gzip.open(str(txt) + ".gz", "wb") as g:
            shutil.copyfileobj(f, g)
        txt.unlink()


def enviar(asunto, cuerpo):
    para = [d.strip() for d in os.environ.get("RESUMEN_PARA", "").split(",") if d.strip()]
    host = os.environ.get("MSMTP_HOST")
    if not para or not host:
        print("Sin RESUMEN_PARA o MSMTP_HOST: el resumen no se manda por correo.", file=sys.stderr)
        return
    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = os.environ.get("MSMTP_FROM") or os.environ.get("MSMTP_USER")
    msg["To"] = ", ".join(para)
    msg.set_content(cuerpo)
    with smtplib.SMTP(host, int(os.environ.get("MSMTP_PORT", "587")), timeout=60) as s:
        s.starttls()
        s.login(os.environ["MSMTP_USER"], os.environ["MSMTP_PASSWORD"])
        s.send_message(msg)


def num(n):
    return "—" if n is None else f"{n:,}".replace(",", ".")


def resumen(c, estado, error=None):
    d = c.datos
    total = time.monotonic() - c.inicio
    partes = [f"Corrida diaria del Boletín Judicial, {datetime.now():%Y-%m-%d %H:%M}.", "",
              f"Resultado: {estado}", ""]
    if d.get("boletines"):
        partes.append("Boletines nuevos:")
        for bid, fecha, n in d["boletines"]:
            partes.append(f"  {fecha}  boletín {bid}: {num(n)} avisos")
        partes.append("")
    if d.get("actuaciones"):
        partes.append("Avisos nuevos por actuación: " + ", ".join(f"{k} {num(n)}" for k, n in d["actuaciones"]))
    if d.get("tipos_acuerdo"):
        partes.append("Por tipo de acuerdo: " + ", ".join(f"{k} {num(n)}" for k, n in d["tipos_acuerdo"]))
    if d.get("antes") and d.get("despues"):
        partes += ["", "Cantidades (antes → después):"]
        for k in [*CUENTAS, "último boletín"]:
            a, b = d["antes"].get(k), d["despues"].get(k)
            if isinstance(a, int) or isinstance(b, int):
                partes.append(f"  {k}: {num(a)} → {num(b)} ({'+' if (b or 0) >= (a or 0) else ''}{num((b or 0) - (a or 0))})")
            else:
                partes.append(f"  {k}: {a} → {b}")
    if d.get("problemas"):
        partes += ["", "Chequeos que fallaron:"] + [f"  {p}" for p in d["problemas"]]
    partes += ["", f"Tiempos (total {total / 60:.1f} min):"]
    partes += [f"  {p}: {s:.0f} s" for p, s in c.pasos]
    etapas = [l for l in c.lineas if l.rstrip().endswith(" s") and ":" in l.split("]", 1)[-1] and "  " in l]
    if etapas:
        partes += ["", "Etapas del build:"] + [f"  {l.split(']', 1)[-1].strip()}" for l in etapas[-12:]]
    if d.get("tamano"):
        partes.append(f"\nTamaño de la base: {d['tamano'] / 1e9:.1f} GB")
    if error:
        partes += ["", "Error:", error, "", "Últimas líneas del log:"] + c.lineas[-40:]
    return "\n".join(partes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sin-publicar", action="store_true", help="arma la base nueva pero no la publica")
    ap.add_argument("--sin-correo", action="store_true", help="imprime el resumen en vez de mandarlo")
    ap.add_argument("--desde", help="fecha inicial del índice (por defecto, el último boletín de la base)")
    args = ap.parse_args()

    TRABAJO.mkdir(parents=True, exist_ok=True)
    candado = open(TRABAJO / "diario.lock", "w")
    try:
        fcntl.flock(candado, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("Ya hay una corrida diaria en curso.")

    c = Corrida()
    trabajo = TRABAJO / BASE.name
    trabajo_estado = TRABAJO / ESTADO.name
    estado, error = "error", None
    try:
        # Restos de una corrida cortada (p. ej. un deploy a mitad de camino).
        for f in TRABAJO.glob("boletin.sqlite*"):
            f.unlink()
        if not ESTADO.exists():
            raise RuntimeError(f"Falta {ESTADO.name}: el incremental necesita la base armada con su estado.")
        antes = c.paso("Cantidades de la base publicada", cuentas, BASE)
        c.datos["antes"] = antes
        # Desde el día siguiente al último boletín cargado: si no hay nada
        # nuevo, poc.py no escribe entradas.csv y no se copia la base.
        desde = args.desde or (date.fromisoformat(antes["último boletín"]) + timedelta(days=1)).isoformat()
        hoy = date.today().isoformat()
        run_dir = SCRAPER_DATA_DIR / hoy
        c.paso(f"Descarga del índice y boletines {desde} a {hoy}", descargar, c, desde, hoy, run_dir)
        if not (run_dir / "entradas.csv").exists():
            estado = "sin boletines nuevos en el portal"
            return
        c.paso("Copia de trabajo de la base y su estado", lambda: (shutil.copyfile(BASE, trabajo),
                                                                   shutil.copyfile(ESTADO, trabajo_estado)))
        c.paso("Build incremental", sumar, c, trabajo)
        despues = c.paso("Cantidades de la base nueva", cuentas, trabajo)
        c.datos["despues"] = despues
        c.datos["tamano"] = trabajo.stat().st_size
        if despues["avisos"] == antes["avisos"]:
            estado = "sin boletines nuevos (los del portal ya estaban en la base)"
            return
        (c.datos["boletines"], c.datos["actuaciones"], c.datos["tipos_acuerdo"],
         prueba) = c.paso("Detalle de lo nuevo", nuevos, trabajo, antes["max_id"])
        c.datos["problemas"] = problemas = chequear(antes, despues)
        if problemas:
            estado = "NO publicada: fallaron chequeos"
            return
        if args.sin_publicar:
            estado = f"armada sin publicar en {trabajo}"
            return
        c.paso("Publicación", publicar, c, trabajo, trabajo_estado)
        c.paso("Compresión de textos", comprimir_textos, run_dir)
        estado = "publicada"
    except Exception:
        error = traceback.format_exc()
        c.log(error)
    finally:
        if estado not in ("publicada",) and not estado.startswith("armada"):
            for f in TRABAJO.glob("boletin.sqlite*"):
                f.unlink()
        nuevos_avisos = (c.datos.get("despues") or {}).get("avisos", 0) - (c.datos.get("antes") or {}).get("avisos", 0)
        asunto = (f"Boletín judicial: ERROR en la corrida diaria" if error else
                  f"Boletín judicial: +{num(max(nuevos_avisos, 0))} avisos, {estado}")
        cuerpo = resumen(c, estado, error)
        if args.sin_correo:
            print("\n" + asunto + "\n\n" + cuerpo)
        else:
            try:
                enviar(asunto, cuerpo)
            except Exception:
                print("No se pudo mandar el resumen:\n" + traceback.format_exc(), file=sys.stderr)
                print(cuerpo)
    if error:
        sys.exit(1)


if __name__ == "__main__":
    main()
