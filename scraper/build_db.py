#!/usr/bin/env python3
"""
build_db.py — Genera boletin.sqlite desde los CSVs del scraper.

Uso:
    python build_db.py --dir data/ --output boletin.sqlite
    python build_db.py --dir data/ --output boletin.sqlite --since 2026-01-01
    python build_db.py --dir data/ --output boletin.sqlite --exclude 2026-07-02
    python build_db.py --dir data/ --output boletin.sqlite --desde-fecha 2026-01-01
"""

import argparse
import csv
import functools
import itertools
import os
from collections import deque
from multiprocessing import Pool
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from normalize_tipo import normalize
import empresas as emp
import personas as pers

# normalize() es una cascada de regex y era ~80% del tiempo de carga, pero
# hay pocos cientos de tipo_juicio distintos en millones de filas.
normalize_tipo = functools.lru_cache(maxsize=None)(normalize)

csv.field_size_limit(10**9)

RE_ANIO = re.compile(r"/(\d{4})\b")


def create_schema(conn):
    conn.executescript("""
        -- fecha_iso, n_entradas y entrada_desde/hasta: para navegar por
        -- fecha en el frontend. Las entradas de un boletín quedan contiguas
        -- (se insertan boletín por boletín), así que traer "todo lo del día"
        -- es un rango de rowid, sin tener que indexar entradas.boletin_id.
        CREATE TABLE IF NOT EXISTS boletines (
            id            INTEGER PRIMARY KEY,
            fecha         TEXT NOT NULL,
            fecha_iso     TEXT,
            pdf_url       TEXT,
            pages         INTEGER,
            n_entradas    INTEGER NOT NULL DEFAULT 0,
            entrada_desde INTEGER,
            entrada_hasta INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_boletines_fecha
            ON boletines (fecha_iso);

        -- La fecha de publicación no se guarda acá: es 1:1 con boletin_id y
        -- ya está en boletines.fecha. Repetirla por fila (una por aviso)
        -- inflaba el archivo ~40MB sin aportar nada (se llega a ella con
        -- JOIN boletines).
        CREATE TABLE IF NOT EXISTS entradas (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            boletin_id  INTEGER REFERENCES boletines(id),
            juzgado     TEXT,
            sala        TEXT,
            secretaria  TEXT,
            actora      TEXT,
            demandada   TEXT,
            tipo_juicio TEXT,
            tipo_juicio_norm TEXT,
            expediente  TEXT,
            num_acdos   INTEGER,
            anio        INTEGER,
            pagina      INTEGER  -- página del PDF del boletín (1 = primera)
        );


        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS carpetas_fgj (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha_inicio     TEXT,
            fecha_hecho      TEXT,
            delito           TEXT,
            categoria_delito TEXT,
            fiscalia         TEXT,
            alcaldia         TEXT,
            colonia          TEXT,
            lat              REAL,
            lon              REAL
        );

        CREATE INDEX IF NOT EXISTS idx_fgj_alcaldia
            ON carpetas_fgj (alcaldia, fecha_hecho);
    """)


def create_indexes(conn, indices_server=False, web=False):
    # Después de la carga y no en el schema: armar el índice de una vez es
    # mucho más rápido que mantenerlo fila por fila durante 30M inserts.
    if not web:
        # (juzgado, expediente): fichas de expediente y páginas de juzgado
        # del server. El sitio estático no filtra por juzgado y este índice
        # es ~14% de la base que baja el navegador.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_expediente ON entradas (juzgado, expediente)")
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_anio
            ON entradas (anio);

        CREATE INDEX IF NOT EXISTS idx_tipo_juicio_norm
            ON entradas (tipo_juicio_norm);
    """)
    if indices_server:
        # Solo para la base del server (en la del browser suman MB a la
        # descarga y con 2M de filas alcanza un recorrido):
        # - búsqueda por número de expediente sin saber el juzgado (el
        #   índice de arriba empieza por juzgado);
        # - gráfico por año y tipo leyendo solo el índice: con idx_anio
        #   SQLite va a la tabla por cada fila y con 34M tarda minutos.
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_expediente_num ON entradas (expediente);
            CREATE INDEX IF NOT EXISTS idx_anio_tipo ON entradas (anio, tipo_juicio_norm);
        """)


def create_fts(conn):
    # detail=none: no guardamos posición ni columna de cada término (solo
    # qué documentos matchean). No usamos snippet()/highlight() ni consultas
    # de columna o de frase, así que el costo es nulo y el índice pesa
    # ~1/4 de lo que pesaba con el detail (posicional) por default.
    conn.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS entradas_fts USING fts5(
            actora,
            demandada,
            tipo_juicio,
            content='entradas',
            content_rowid='id',
            detail=none
        );
    """)


def populate_meta(conn):
    """Guarda el commit de git y la fecha de build para que el frontend
    pueda mostrar qué versión de la DB está corriendo (facilita detectar
    si ya se hizo el deploy más reciente)."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True, cwd=Path(__file__).parent,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        commit = "sin-git"

    built_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    conn.executemany(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        [("commit", commit), ("built_at", built_at)],
    )


def populate_fts(conn):
    conn.execute("""
        INSERT INTO entradas_fts(rowid, actora, demandada, tipo_juicio)
        SELECT id, actora, demandada, tipo_juicio FROM entradas
    """)


MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
         "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12}
RE_FECHA_RAW = re.compile(r"(\d{1,2})-([a-z]{3})[a-z]*\.?-(\d{4})")


def fecha_iso(fecha_raw):
    """'Consulta del Boletin Judicial de fecha  22-sep.-2026' -> '2026-09-22'."""
    m = RE_FECHA_RAW.search(fecha_raw or "")
    if not m or m.group(2) not in MESES:
        return None
    return f"{m.group(3)}-{MESES[m.group(2)]:02d}-{int(m.group(1)):02d}"


RE_URL_PDF = re.compile(r'https://(?:(?!https://)[^"#])+\.pdf')


def url_pdf_directa(url):
    """Índices guardados antes de jul. 2026 tienen la URL envuelta en el
    proxy del portal (".../pdf/boletines/https://gestordocumental..."), que
    da 404: se usa la última URL, la directa al CDN (mismo criterio que
    fetch_index en poc.py)."""
    urls = RE_URL_PDF.findall(url or "")
    return urls[-1] if urls else url


def load_index(conn, index_path):
    with open(index_path) as f:
        boletines = json.load(f)
    conn.executemany(
        "INSERT OR IGNORE INTO boletines (id, fecha, fecha_iso, pdf_url) VALUES (?, ?, ?, ?)",
        [(b["id"], b["fecha_raw"], fecha_iso(b["fecha_raw"]), url_pdf_directa(b["pdf_url"]))
         for b in boletines],
    )
    # URLs corregidas por poc.py cuando el link del portal da 404 (ver
    # docs/boletines-faltantes.md): pisan a la del portal aunque otro run
    # con el link roto se haya cargado antes.
    conn.executemany(
        "UPDATE boletines SET pdf_url = ? WHERE id = ?",
        [(b["pdf_url"], b["id"]) for b in boletines if b.get("pdf_url_portal")],
    )


def populate_boletin_ranges(conn, log):
    conn.execute("""
        CREATE TEMP TABLE agg AS
        SELECT boletin_id, COUNT(*) AS n, MIN(id) AS desde, MAX(id) AS hasta
        FROM entradas GROUP BY boletin_id
    """)
    no_contiguos = conn.execute(
        "SELECT COUNT(*) FROM agg WHERE hasta - desde + 1 != n"
    ).fetchone()[0]
    if no_contiguos:
        # El frontend filtra además por boletin_id, así que no mezcla
        # boletines; solo lee algunas filas de más.
        log(f"  Aviso: {no_contiguos} boletines con entradas no contiguas")
    conn.execute("""
        UPDATE boletines
        SET n_entradas = agg.n, entrada_desde = agg.desde, entrada_hasta = agg.hasta
        FROM agg WHERE agg.boletin_id = boletines.id
    """)
    conn.execute("DROP TABLE agg")


FIELDS = [
    "boletin_id", "juzgado", "sala", "secretaria",
    "actora", "demandada", "tipo_juicio", "expediente", "num_acdos", "pagina",
]
COLS_CARGA = FIELDS + ["tipo_juicio_norm", "anio"]
BLOQUE = 64 * 1024 * 1024  # bytes de CSV por tarea

# Estado de cada proceso de la carga paralela (lo fija _init_worker).
_W = {}


def _init_worker(csv_path, col, loaded, en_rango, tmp_dir, con_empresas=False):
    _W.update(path=csv_path, col=col, loaded=loaded, en_rango=en_rango, tmp_dir=tmp_dir,
              con_empresas=con_empresas)


def _parse_bloque(bounds):
    """Lee y transforma las filas que EMPIEZAN dentro de [start, end) del CSV.
    Cada fila ocupa exactamente una línea (ningún campo lleva saltos de
    línea), así que se puede cortar el archivo en cualquier "\n"."""
    start, end = bounds
    col, loaded, en_rango = _W["col"], _W["loaded"], _W["en_rango"]
    idx = [col.get(k) for k in FIELDS]
    i_bol, i_tipo, i_exp = col["boletin_id"], col["tipo_juicio"], col["expediente"]

    lineas = []
    with open(_W["path"], "rb") as f:
        f.seek(start - 1)
        f.readline()  # termina la línea que empezó antes de `start`
        while f.tell() < end:
            linea = f.readline()
            if not linea:
                break
            lineas.append(linea.decode("utf-8"))

    filas, ids, salteadas = [], set(), 0
    # Con --empresas: (fila, rol, núcleo, nombre) por cada empresa detectada,
    # y (fila, otras_actora, otras_demandada) si el aviso tiene alguna: si
    # del lado de la actora o la demandada quedan partes que no son empresa
    # (personas). Solo el indicador: los nombres de personas no se guardan.
    filas_empresas, filas_partes = [], []
    i_act, i_dem = col["actora"], col["demandada"]
    for row in csv.reader(lineas):
        boletin_id = int(row[i_bol])
        if en_rango is not None and boletin_id not in en_rango:
            continue
        if boletin_id in loaded:
            salteadas += 1
            continue
        ids.add(boletin_id)
        m_anio = RE_ANIO.search(row[i_exp])
        if _W["con_empresas"]:
            fila = len(filas)
            emp_a, otras_a = emp.partes(row[i_act])
            emp_d, otras_d = emp.partes(row[i_dem])
            for rol, lista in (("actora", emp_a), ("demandada", emp_d)):
                for nucleo, nombre in lista:
                    filas_empresas.append((fila, rol, nucleo, nombre))
            if emp_a or emp_d:
                filas_partes.append((fila, int(otras_a), int(otras_d)))
        filas.append(
            tuple((row[i] or None) if i is not None else None for i in idx)
            + (normalize_tipo(row[i_tipo] or None), int(m_anio.group(1)) if m_anio else None)
        )

    # El bloque se escribe en un SQLite propio en vez de devolver las filas:
    # pasarlas serializadas al proceso principal costaba casi tanto como
    # leerlas. El principal las copia con INSERT ... SELECT (en C).
    shard = os.path.join(_W["tmp_dir"], f"bloque_{start}.sqlite")
    c = sqlite3.connect(shard)
    c.execute("PRAGMA journal_mode=OFF")
    c.execute("PRAGMA synchronous=OFF")
    c.execute(f"CREATE TABLE filas ({', '.join(COLS_CARGA)})")
    c.executemany(f"INSERT INTO filas VALUES ({', '.join(['?'] * len(COLS_CARGA))})", filas)
    if _W["con_empresas"]:
        c.execute("CREATE TABLE filas_empresas (fila, rol, nucleo, nombre)")
        c.executemany("INSERT INTO filas_empresas VALUES (?, ?, ?, ?)", filas_empresas)
        c.execute("CREATE TABLE filas_partes (fila, otras_actora, otras_demandada)")
        c.executemany("INSERT INTO filas_partes VALUES (?, ?, ?)", filas_partes)
    c.commit()
    c.close()
    return shard, len(filas), ids, salteadas


def load_entradas(conn, csv_path, loaded, log, en_rango=None, workers=None, tmp_dir=None,
                  con_empresas=False):
    """Carga las entradas de un run salteando los boletines que ya cargó un
    run anterior (`loaded`, se actualiza). Los runs se pisan entre sí (el
    histórico repite boletines de los runs de 2026) y sin esto cada boletín
    repetido aparecía dos veces. `en_rango`: si se pasa, solo se cargan
    esos boletines (filtro --desde-fecha).

    Leer el CSV, transformar e insertar las filas se reparte en procesos
    por bloques del archivo, cada uno a su propio SQLite temporal. Acá solo
    se copian los bloques en el orden del CSV: así las entradas de cada
    boletín quedan contiguas (ver populate_boletin_ranges)."""
    # Si el run no aporta ningún boletín nuevo (todos ya cargados de un run
    # más nuevo, o fuera del rango de fechas) ni se lee: el CSV del histórico
    # pesa ~8 GB. Los boletines del run son los .txt que tiene en pdfs/.
    del_run = {int(p.stem[len("boletin_"):]) for p in (csv_path.parent / "pdfs").glob("boletin_*.txt")}
    if del_run:
        utiles = del_run - loaded
        if en_rango is not None:
            utiles &= en_rango
        if not utiles:
            log("    sin boletines nuevos, se saltea")
            return 0

    with open(csv_path, newline="") as f:
        col = {name: i for i, name in enumerate(next(csv.reader(f)))}
    with open(csv_path, "rb") as f:
        inicio_datos = len(f.readline())
    total_bytes = csv_path.stat().st_size
    bloques = [(s, min(s + BLOQUE, total_bytes)) for s in range(inicio_datos, total_bytes, BLOQUE)]

    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    counts = {"inserted": 0, "skipped": 0}
    new_ids = set()
    cols = ", ".join(COLS_CARGA)

    with tempfile.TemporaryDirectory(prefix="build_db_", dir=tmp_dir) as tmp, \
            Pool(workers, initializer=_init_worker,
                 initargs=(str(csv_path), col, frozenset(loaded), en_rango, tmp, con_empresas)) as pool:
        # Como mucho 2 bloques pendientes por proceso, para no llenar el
        # disco temporal si los procesos van más rápido que la copia.
        it = iter(bloques)
        pendientes = deque(pool.apply_async(_parse_bloque, (b,))
                           for b in itertools.islice(it, 2 * workers))
        while pendientes:
            shard, n, ids, salteadas = pendientes.popleft().get()
            siguiente = next(it, None)
            if siguiente:
                pendientes.append(pool.apply_async(_parse_bloque, (siguiente,)))
            if n:
                conn.execute("ATTACH DATABASE ? AS bloque", (shard,))
                # ids nuevos: base+1 ... base+n, en el orden de las filas del bloque
                base = conn.execute("SELECT COALESCE(MAX(id), 0) FROM entradas").fetchone()[0]
                conn.execute(f"INSERT INTO entradas ({cols}) SELECT {cols} FROM bloque.filas ORDER BY rowid")
                if con_empresas:
                    conn.execute("INSERT INTO emp_stage SELECT ? + fila + 1, rol, nucleo, nombre "
                                 "FROM bloque.filas_empresas", (base,))
                    conn.execute("INSERT INTO entradas_partes SELECT ? + fila + 1, otras_actora, otras_demandada "
                                 "FROM bloque.filas_partes", (base,))
                conn.commit()
                conn.execute("DETACH DATABASE bloque")
            os.remove(shard)
            new_ids.update(ids)
            counts["inserted"] += n
            counts["skipped"] += salteadas

    loaded |= new_ids
    if counts["skipped"]:
        log(f"    {counts['skipped']} entradas salteadas (boletines ya cargados de un run más nuevo)")
    return counts["inserted"]


MIN_AVISOS_EMPRESA = 2


def build_empresas(conn, log):
    """Arma empresas / entradas_empresas / empresas_fts desde emp_stage.

    Solo entran las empresas con al menos MIN_AVISOS_EMPRESA avisos: los
    restos de parseo (una persona o un encabezado pegados a una razón
    social) casi siempre aparecen una sola vez, y estas páginas se indexan
    y no pueden mostrar personas. El nombre visible es la variante más
    frecuente tal cual está en el boletín."""
    log("Empresas...")
    conn.executescript("""
        CREATE TABLE empresas (
            id             INTEGER PRIMARY KEY,
            nucleo         TEXT UNIQUE NOT NULL,
            nombre         TEXT NOT NULL,
            slug           TEXT UNIQUE NOT NULL,
            avisos         INTEGER NOT NULL,
            como_actora    INTEGER NOT NULL,
            como_demandada INTEGER NOT NULL,
            expedientes    INTEGER,
            primer_boletin TEXT,
            ultimo_boletin TEXT
        );
        CREATE TEMP TABLE nombres AS
            SELECT nucleo, nombre, COUNT(*) AS n FROM emp_stage GROUP BY nucleo, nombre;
    """)
    totales = {r[0]: r[1:] for r in conn.execute("""
        SELECT nucleo, COUNT(*), SUM(rol = 'actora'), SUM(rol = 'demandada')
        FROM emp_stage GROUP BY nucleo HAVING COUNT(*) >= ?
    """, (MIN_AVISOS_EMPRESA,))}
    mejor = {}
    for nucleo, nombre, n in conn.execute("SELECT nucleo, nombre, n FROM nombres ORDER BY nucleo, n DESC, nombre"):
        if nucleo in totales and nucleo not in mejor:
            mejor[nucleo] = nombre
    slugs = set()
    filas = []
    for nucleo, (avisos, act, dem) in sorted(totales.items(), key=lambda x: -x[1][0]):
        base = emp.slugify(nucleo)  # sin forma societaria: /empresas/banco-mercantil-del-norte
        slug, k = base, 2
        while slug in slugs:
            slug, k = f"{base}-{k}", k + 1
        slugs.add(slug)
        filas.append((nucleo, mejor[nucleo], slug, avisos, act, dem))
    conn.executemany("INSERT INTO empresas (nucleo, nombre, slug, avisos, como_actora, como_demandada) "
                     "VALUES (?, ?, ?, ?, ?, ?)", filas)
    conn.executescript("""
        CREATE TABLE entradas_empresas (
            empresa_id INTEGER NOT NULL,
            entrada_id INTEGER NOT NULL,
            rol        TEXT NOT NULL
        );
        INSERT INTO entradas_empresas
            SELECT DISTINCT e.id, s.entrada_id, s.rol
            FROM emp_stage s JOIN empresas e ON e.nucleo = s.nucleo;
        DROP TABLE emp_stage;
        DROP TABLE nombres;
        CREATE INDEX idx_ee_empresa ON entradas_empresas (empresa_id, entrada_id);
        CREATE INDEX idx_ee_entrada ON entradas_empresas (entrada_id);

        UPDATE empresas SET
            (avisos, como_actora, como_demandada, expedientes, primer_boletin, ultimo_boletin) = (
                SELECT COUNT(*), SUM(ee.rol = 'actora'), SUM(ee.rol = 'demandada'),
                       COUNT(DISTINCT COALESCE(en.sala, en.juzgado, '') || '|' || COALESCE(en.expediente, en.id)),
                       MIN(b.fecha_iso), MAX(b.fecha_iso)
                FROM entradas_empresas ee
                JOIN entradas en ON en.id = ee.entrada_id
                JOIN boletines b ON b.id = en.boletin_id
                WHERE ee.empresa_id = empresas.id
            );

        CREATE VIRTUAL TABLE empresas_fts USING fts5(
            nombre, content='empresas', content_rowid='id', detail=none
        );
        INSERT INTO empresas_fts(rowid, nombre) SELECT id, nombre FROM empresas;
    """)
    log(f"  {len(filas)} empresas con al menos {MIN_AVISOS_EMPRESA} avisos")
    build_agregados_empresas(conn, log)


SQL_AGREGADOS_EMPRESAS = """
    -- Lo que muestra /empresas/:slug, precalculado: con cientos de miles de
    -- avisos por empresa (bancos) agregarlo en cada visita tardaba ~35 s.
    DROP TABLE IF EXISTS empresas_tipos;
    DROP TABLE IF EXISTS empresas_anios;
    DROP TABLE IF EXISTS empresas_expedientes;
    CREATE TABLE empresas_tipos AS
        SELECT ee.empresa_id, COALESCE(en.tipo_juicio_norm, 'Sin tipo reconocido') AS tipo, COUNT(*) AS n
        FROM entradas_empresas ee JOIN entradas en ON en.id = ee.entrada_id
        GROUP BY ee.empresa_id, tipo;
    CREATE INDEX idx_empresas_tipos ON empresas_tipos (empresa_id, n DESC);
    CREATE TABLE empresas_anios AS
        SELECT ee.empresa_id, substr(b.fecha_iso, 1, 4) AS anio, COUNT(*) AS n
        FROM entradas_empresas ee
        JOIN entradas en ON en.id = ee.entrada_id
        JOIN boletines b ON b.id = en.boletin_id
        -- por expresión y no por alias: "anio" también es una columna de
        -- entradas (año del expediente) y SQLite agruparía por esa.
        GROUP BY ee.empresa_id, substr(b.fecha_iso, 1, 4);
    CREATE INDEX idx_empresas_anios ON empresas_anios (empresa_id, anio);
    -- Un renglón por (empresa, órgano, expediente, rol) con su último aviso
    -- por FECHA: los ids no son cronológicos (los runs se cargan del más
    -- nuevo al más viejo). ultimo_id sale de la fila con la fecha máxima
    -- (columna "bare" junto a MAX() en SQLite).
    CREATE TABLE empresas_expedientes AS
        SELECT ee.empresa_id, COALESCE(en.sala, en.juzgado) AS organo, en.expediente, ee.rol,
               COUNT(*) AS avisos, MAX(b.fecha_iso) AS ultima_fecha, en.id AS ultimo_id
        FROM entradas_empresas ee
        JOIN entradas en ON en.id = ee.entrada_id
        JOIN boletines b ON b.id = en.boletin_id
        GROUP BY ee.empresa_id, COALESCE(en.sala, en.juzgado), en.expediente, ee.rol;
    CREATE INDEX idx_empresas_expedientes ON empresas_expedientes (empresa_id, ultima_fecha DESC);
"""


def build_agregados_empresas(conn, log):
    log("Agregados de empresas...")
    conn.executescript(SQL_AGREGADOS_EMPRESAS)
    conn.commit()


def _init_empresas_worker(db_path):
    _W["db"] = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _empresas_de_rango(bounds):
    desde, hasta = bounds
    filas_empresas, filas_partes = [], []
    for entrada_id, actora, demandada in _W["db"].execute(
            "SELECT id, actora, demandada FROM entradas WHERE id BETWEEN ? AND ?", (desde, hasta)):
        emp_a, otras_a = emp.partes(actora)
        emp_d, otras_d = emp.partes(demandada)
        for rol, lista in (("actora", emp_a), ("demandada", emp_d)):
            for nucleo, nombre in lista:
                filas_empresas.append((entrada_id, rol, nucleo, nombre))
        if emp_a or emp_d:
            filas_partes.append((entrada_id, int(otras_a), int(otras_d)))
    return filas_empresas, filas_partes


def recalcular_empresas(output, log, workers=None):
    """--solo-empresas: rearma las tablas de empresas sobre una base ya
    armada, sin volver a cargar los 34M avisos (~10 min en vez de ~1 h).
    Sirve cuando cambia la detección en scraper/empresas.py."""
    conn = sqlite3.connect(output)
    # WAL mientras dura: los procesos leen la base mientras este escribe.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA cache_size=-2000000")
    conn.executescript("""
        DROP TABLE IF EXISTS empresas_fts;
        DROP TABLE IF EXISTS entradas_empresas;
        DROP TABLE IF EXISTS empresas;
        DROP TABLE IF EXISTS entradas_partes;
        DROP TABLE IF EXISTS emp_stage;
        CREATE TABLE emp_stage (entrada_id INTEGER, rol TEXT, nucleo TEXT, nombre TEXT);
        CREATE TABLE entradas_partes (
            entrada_id      INTEGER PRIMARY KEY,
            otras_actora    INTEGER NOT NULL,
            otras_demandada INTEGER NOT NULL
        );
    """)
    max_id = conn.execute("SELECT MAX(id) FROM entradas").fetchone()[0] or 0
    paso = 200_000
    rangos = [(a, min(a + paso - 1, max_id)) for a in range(1, max_id + 1, paso)]
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    log(f"Recalculando empresas de {max_id:,} avisos en {len(rangos)} rangos...")
    hechos = 0
    with Pool(workers, initializer=_init_empresas_worker, initargs=(str(output),)) as pool:
        for filas_empresas, filas_partes in pool.imap_unordered(_empresas_de_rango, rangos):
            conn.executemany("INSERT INTO emp_stage VALUES (?, ?, ?, ?)", filas_empresas)
            conn.executemany("INSERT INTO entradas_partes VALUES (?, ?, ?)", filas_partes)
            conn.commit()
            hechos += 1
            if hechos % 20 == 0:
                log(f"  {hechos}/{len(rangos)} rangos")
    build_empresas(conn, log)
    conn.commit()
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()


MIN_AVISOS_PERSONA = 2


def _init_personas_worker(db_path):
    _W["db"] = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _personas_de_rango(bounds):
    """Agrega por (núcleo, nombre) las personas de un rango de avisos:
    [avisos, como actora, como demandada, primer boletín, último boletín].
    Se agrega acá y no aviso por aviso en el proceso principal: así viajan
    y se escriben cientos de miles de filas en vez de ~60M."""
    desde, hasta = bounds
    acum = {}
    for actora, demandada, fecha in _W["db"].execute("""
            SELECT e.actora, e.demandada, b.fecha_iso
            FROM entradas e JOIN boletines b ON b.id = e.boletin_id
            WHERE e.id BETWEEN ? AND ?""", (desde, hasta)):
        for i, campo in ((1, actora), (2, demandada)):
            # Una misma persona dos veces en el mismo campo cuenta una vez.
            for clave in set(pers.personas(campo)):
                a = acum.get(clave)
                if a is None:
                    a = acum[clave] = [0, 0, 0, fecha, fecha]
                a[0] += 1
                a[i] += 1
                a[3] = min(filter(None, (a[3], fecha)), default=None)
                a[4] = max(filter(None, (a[4], fecha)), default=None)
    return [(n, nombre, *a) for (n, nombre), a in acum.items()]


def build_personas(output, log, workers=None):
    """Tablas personas / personas_fts para /personas del server (noindex).

    A diferencia de empresas no hay tabla de unión aviso-persona (serían
    ~60M filas): la ficha de una persona busca sus avisos en vivo en
    entradas_fts y filtra por el nombre exacto. Solo entran los nombres con
    al menos MIN_AVISOS_PERSONA avisos: los restos de parseo casi siempre
    aparecen una sola vez. El nombre visible es la variante más frecuente."""
    conn = sqlite3.connect(output)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA cache_size=-2000000")
    conn.executescript("""
        DROP TABLE IF EXISTS personas_fts;
        DROP TABLE IF EXISTS personas;
        DROP TABLE IF EXISTS personas_stage;
        CREATE TABLE personas_stage (nucleo TEXT, nombre TEXT, n INTEGER, act INTEGER, dem INTEGER,
                                     desde TEXT, hasta TEXT);
    """)
    max_id = conn.execute("SELECT MAX(id) FROM entradas").fetchone()[0] or 0
    paso = 200_000
    rangos = [(a, min(a + paso - 1, max_id)) for a in range(1, max_id + 1, paso)]
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    log(f"Personas de {max_id:,} avisos en {len(rangos)} rangos...")
    hechos = 0
    with Pool(workers, initializer=_init_personas_worker, initargs=(str(output),)) as pool:
        for filas in pool.imap_unordered(_personas_de_rango, rangos):
            conn.executemany("INSERT INTO personas_stage VALUES (?, ?, ?, ?, ?, ?, ?)", filas)
            conn.commit()
            hechos += 1
            if hechos % 20 == 0:
                log(f"  {hechos}/{len(rangos)} rangos")

    conn.executescript("""
        CREATE TABLE personas (
            id             INTEGER PRIMARY KEY,
            nucleo         TEXT UNIQUE NOT NULL,
            nombre         TEXT NOT NULL,
            slug           TEXT UNIQUE NOT NULL,
            avisos         INTEGER NOT NULL,
            como_actora    INTEGER NOT NULL,
            como_demandada INTEGER NOT NULL,
            primer_boletin TEXT,
            ultimo_boletin TEXT
        );
        CREATE TEMP TABLE variantes AS
            SELECT nucleo, nombre, SUM(n) AS n, SUM(act) AS act, SUM(dem) AS dem,
                   MIN(desde) AS desde, MAX(hasta) AS hasta
            FROM personas_stage GROUP BY nucleo, nombre;
        DROP TABLE personas_stage;
    """)

    def por_nucleo():
        actual, fila = None, None
        for nucleo, nombre, n, act, dem, desde, hasta in conn.execute(
                "SELECT * FROM variantes ORDER BY nucleo, n DESC, nombre"):
            if nucleo != actual:
                if fila:
                    yield fila
                # La primera variante (la más frecuente) da el nombre visible.
                actual, fila = nucleo, [nucleo, nombre, 0, 0, 0, desde, hasta]
            fila[2] += n
            fila[3] += act
            fila[4] += dem
            fila[5] = min(filter(None, (fila[5], desde)), default=None)
            fila[6] = max(filter(None, (fila[6], hasta)), default=None)
        if fila:
            yield fila

    slugs = set()
    lote = []
    total = 0
    for nucleo, nombre, avisos, act, dem, desde, hasta in por_nucleo():
        if avisos < MIN_AVISOS_PERSONA:
            continue
        base = pers.slugify(nucleo)
        slug, k = base, 2
        while slug in slugs:
            slug, k = f"{base}-{k}", k + 1
        slugs.add(slug)
        lote.append((nucleo, nombre, slug, avisos, act, dem, desde, hasta))
        if len(lote) >= 50_000:
            conn.executemany("INSERT INTO personas (nucleo, nombre, slug, avisos, como_actora, como_demandada, "
                             "primer_boletin, ultimo_boletin) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", lote)
            total += len(lote)
            lote = []
    conn.executemany("INSERT INTO personas (nucleo, nombre, slug, avisos, como_actora, como_demandada, "
                     "primer_boletin, ultimo_boletin) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", lote)
    total += len(lote)
    conn.executescript("""
        DROP TABLE variantes;
        CREATE INDEX idx_personas_avisos ON personas (avisos DESC);
        CREATE VIRTUAL TABLE personas_fts USING fts5(
            nombre, content='personas', content_rowid='id', detail=none
        );
        INSERT INTO personas_fts(rowid, nombre) SELECT id, nombre FROM personas;
    """)
    conn.commit()
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()
    log(f"  {total:,} personas con al menos {MIN_AVISOS_PERSONA} avisos")


def build_vocabulario(output, log):
    """Tablas vocabulario / vocabulario_tri para sugerir correcciones en el
    buscador ("santnder" -> "santander"). Palabras de los nombres de
    personas y empresas (núcleos, sin acentos), con la suma de avisos de los
    nombres que las usan como frecuencia. vocabulario_tri es un índice de
    trigramas (FTS5 tokenize=trigram) para encontrar palabras parecidas."""
    conn = sqlite3.connect(output)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    frecuencia = {}
    for tabla in ("empresas", "personas"):
        existe = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (tabla,)).fetchone()
        if not existe:
            continue
        for nucleo, avisos in conn.execute(f"SELECT nucleo, avisos FROM {tabla}"):
            for palabra in set(nucleo.split()):
                if len(palabra) >= 3 and not palabra.isdigit():
                    frecuencia[palabra] = frecuencia.get(palabra, 0) + avisos
    conn.executescript("""
        DROP TABLE IF EXISTS vocabulario_tri;
        DROP TABLE IF EXISTS vocabulario;
        CREATE TABLE vocabulario (id INTEGER PRIMARY KEY, palabra TEXT UNIQUE NOT NULL, n INTEGER NOT NULL);
    """)
    conn.executemany("INSERT INTO vocabulario (palabra, n) VALUES (?, ?)", sorted(frecuencia.items()))
    conn.executescript("""
        CREATE VIRTUAL TABLE vocabulario_tri USING fts5(
            palabra, content='vocabulario', content_rowid='id', tokenize='trigram', detail=none
        );
        INSERT INTO vocabulario_tri(rowid, palabra) SELECT id, palabra FROM vocabulario;
    """)
    conn.commit()
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()
    log(f"  {len(frecuencia):,} palabras en el vocabulario")


def find_runs(data_dir, since=None, exclude=()):
    """Devuelve lista de (run_dir) ordenados por fecha."""
    runs = sorted(data_dir.glob("*/entradas.csv"))
    if since:
        runs = [r for r in runs if r.parent.name >= since]
    return [r for r in runs if r.parent.name not in exclude]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", help="Directorio raíz de datos (data/)")
    parser.add_argument("--output", default="boletin.sqlite", help="Archivo SQLite de salida")
    parser.add_argument("--since", help="Solo incluir runs desde esta fecha YYYY-MM-DD")
    parser.add_argument("--exclude", action="append", default=[],
                        help="Run a omitir (nombre del directorio). Repetible")
    parser.add_argument("--indices-server", action="store_true",
                        help="Índices extra para la base del server: por número de "
                             "expediente y por (año, tipo de juicio)")
    parser.add_argument("--sin-vacuum", action="store_true",
                        help="No compactar al final (más rápido; para la base del "
                             "server, donde el tamaño del archivo no importa)")
    parser.add_argument("--agregados-empresas", action="store_true",
                        help="Rearmar solo los agregados de /empresas/:slug de una base ya armada (--output)")
    parser.add_argument("--solo-empresas", action="store_true",
                        help="Rearmar solo las tablas de empresas de una base ya armada (--output)")
    parser.add_argument("--empresas", action="store_true",
                        help="Tablas de empresas que son parte (sección /empresas del server)")
    parser.add_argument("--personas", action="store_true",
                        help="Tabla de personas físicas que son parte (sección /personas del server, noindex)")
    parser.add_argument("--solo-personas", action="store_true",
                        help="Rearmar solo la tabla de personas de una base ya armada (--output)")
    parser.add_argument("--solo-vocabulario", action="store_true",
                        help="Rearmar solo el vocabulario de sugerencias de una base ya armada (--output)")
    parser.add_argument("--web", action="store_true",
                        help="Base para el sitio estático: sin el índice (juzgado, expediente)")
    parser.add_argument("--hasta-fecha",
                        help="Solo boletines publicados hasta esta fecha YYYY-MM-DD (inclusive)")
    parser.add_argument("--desde-fecha",
                        help="Solo boletines publicados desde esta fecha YYYY-MM-DD, "
                             "de cualquier run (a diferencia de --since, que filtra "
                             "por nombre del run)")
    args = parser.parse_args()

    output = Path(args.output)
    log = lambda msg: print(msg, file=sys.stderr)

    if args.agregados_empresas:
        conn = sqlite3.connect(output)
        conn.execute("PRAGMA cache_size=-2000000")
        conn.execute("PRAGMA temp_store=FILE")
        build_agregados_empresas(conn, log)
        conn.close()
        log(f"Listo: {output}")
        return
    if args.solo_empresas:
        recalcular_empresas(output, log)
        log(f"Listo: {output}")
        return
    if args.solo_personas:
        build_personas(output, log)
        log(f"Listo: {output}")
        return
    if args.solo_vocabulario:
        build_vocabulario(output, log)
        log(f"Listo: {output}")
        return
    if not args.dir:
        parser.error("--dir es obligatorio (salvo con --solo-empresas / --solo-personas / --solo-vocabulario / --agregados-empresas)")
    data_dir = Path(args.dir)

    runs = find_runs(data_dir, args.since, args.exclude)
    if not runs:
        log("No se encontraron datos.")
        sys.exit(1)

    log(f"Encontrados {len(runs)} runs en {data_dir}")
    log(f"Generando {output}...")

    output.unlink(missing_ok=True)
    conn = sqlite3.connect(output)
    # Archivo nuevo y descartable (si el build se corta se vuelve a correr):
    # sin journal ni fsync, con caché grande.
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA cache_size=-2000000")  # ~2 GB
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA locking_mode=EXCLUSIVE")

    create_schema(conn)
    if args.empresas:
        conn.executescript("""
            CREATE TABLE emp_stage (entrada_id INTEGER, rol TEXT, nucleo TEXT, nombre TEXT);
            -- Por aviso con alguna empresa: si del lado de la actora o de la
            -- demandada hay partes que no son empresa. Solo el indicador,
            -- para mostrar "persona física" sin el nombre.
            CREATE TABLE entradas_partes (
                entrada_id      INTEGER PRIMARY KEY,
                otras_actora    INTEGER NOT NULL,
                otras_demandada INTEGER NOT NULL
            );
        """)

    # Del run más nuevo al más viejo: si un boletín está en varios, gana el
    # más reciente (parseado con la versión más nueva del parser).
    runs = list(reversed(runs))
    for csv_path in runs:
        index_path = csv_path.parent / "index.json"
        if index_path.exists():
            load_index(conn, index_path)

    en_rango = None
    if args.desde_fecha or args.hasta_fecha:
        conn.execute("DELETE FROM boletines WHERE fecha_iso IS NULL OR fecha_iso < ? OR fecha_iso > ?",
                     (args.desde_fecha or "0000-00-00", args.hasta_fecha or "9999-99-99"))
        en_rango = {r[0] for r in conn.execute("SELECT id FROM boletines")}

    total = 0
    loaded = set()
    for csv_path in runs:
        run_dir = csv_path.parent
        log(f"  {run_dir.name}...")

        n = load_entradas(conn, csv_path, loaded, log, en_rango, tmp_dir=str(output.parent),
                          con_empresas=args.empresas)
        total += n
        log(f"    {n} entradas")

    log("Índices...")
    create_indexes(conn, args.indices_server, args.web)
    populate_boletin_ranges(conn, log)
    if args.empresas:
        build_empresas(conn, log)
    populate_meta(conn)
    conn.commit()

    log(f"\nTotal: {total} entradas. Construyendo índice FTS5...")
    create_fts(conn)
    populate_fts(conn)
    conn.commit()

    log("Optimizando...")
    conn.execute("INSERT INTO entradas_fts(entradas_fts) VALUES('optimize')")
    conn.commit()
    if not args.sin_vacuum:
        log("Compactando...")
        conn.execute("VACUUM")
    conn.close()
    if args.personas:
        # Después de cerrar: los procesos leen la base mientras se escribe.
        build_personas(output, log)
    if args.empresas or args.personas:
        build_vocabulario(output, log)

    size_mb = output.stat().st_size / 1e6
    log(f"\nListo: {output} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
