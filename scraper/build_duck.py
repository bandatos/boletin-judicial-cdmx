"""
build_duck.py — Build completo con DuckDB para la carga y los agregados.

SQLite agrupa y ordena con un solo núcleo, fila por fila: con 35M avisos
las estadísticas, empresas, personas y partes tardaban minutos cada una.
DuckDB (columnar, todos los núcleos) hace lo mismo en segundos. La base
que sirve el sitio sigue siendo SQLite: DuckDB arma las tablas y las
escribe ahí con su extensión sqlite. SQLite arma sus índices y la búsqueda
por texto (FTS5), que DuckDB no puede hacer.

Mismo resultado que el build con SQLite (build_db.py sin DuckDB, verificado
tabla por tabla). La corrida diaria (--incremental) no usa DuckDB.

Pasos:
1. Precálculo de cada corrida en Parquet: el binario precalc de Rust
   escribe CSV y DuckDB los pasa a Parquet (precalc_parquet).
2. DuckDB elige de qué corrida sale cada boletín (la más nueva), numera
   los avisos y los escribe en la base.
3. En paralelo: SQLite crea índices y FTS; DuckDB calcula partes,
   empresas, personas, estadísticas y resúmenes.
4. DuckDB escribe esas tablas en la base y SQLite les crea los índices.

Requiere el paquete duckdb y la extensión sqlite de DuckDB. Si DuckDB no
puede bajarla (proxy), se puede bajar a mano y pasar la ruta en
DUCKDB_SQLITE_EXT.
"""

import csv
import os
import shutil
import tempfile
import sqlite3
import threading
import time
import subprocess
import sys
from pathlib import Path

import duckdb

import agregados


# Extensión sqlite de DuckDB: DUCKDB_SQLITE_EXT, la copia en scraper/.venv
# (bajada a mano, para cuando DuckDB no puede bajarla por el proxy) o la
# que DuckDB instala solo.
EXT_LOCAL = Path(__file__).resolve().parent / ".venv" / "duckdb_ext" / "sqlite_scanner.duckdb_extension"


def _cargar_sqlite(db):
    ext = os.environ.get("DUCKDB_SQLITE_EXT") or (str(EXT_LOCAL) if EXT_LOCAL.exists() else None)
    if ext:
        db.execute(f"LOAD '{ext}'")
    else:
        db.execute("INSTALL sqlite")
        db.execute("LOAD sqlite")


def conectar_simple(nucleos):
    """Conexión en memoria con la extensión sqlite cargada."""
    db = duckdb.connect()
    db.execute(f"SET threads = {nucleos}")
    _cargar_sqlite(db)
    return db


def _conectar(base_dir, nucleos, log):
    db = duckdb.connect(str(base_dir / "build.duckdb"))
    tmp = base_dir / "duck_tmp"
    tmp.mkdir(exist_ok=True)
    db.execute(f"SET threads = {nucleos}")
    db.execute(f"SET temp_directory = '{tmp}'")
    # 30 % de la RAM (DUCKDB_MEMORIA_GB para cambiarlo): lo que no entra va
    # a disco (temp_directory). Con la mitad de la memoria libre el límite
    # quedaba en ~6 GB cuando la máquina usaba swap, y DuckDB se quedaba sin.
    try:
        ram = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        gb = float(os.environ.get("DUCKDB_MEMORIA_GB", 0)) or max(2, int(ram * 0.3 / 1e9))
        db.execute(f"SET memory_limit = '{gb}GB'")
    except (ValueError, OSError):
        pass
    db.execute("SET preserve_insertion_order = false")
    _cargar_sqlite(db)
    return db


# ── 1. Precálculo en Parquet ──────────────────────────────────────────────

def parquet_de(precalc_sqlite, version):
    """Rutas de las tablas del precálculo en Parquet, por versión."""
    base = precalc_sqlite.with_name(f"entradas.precalc.{version}")
    return {t: Path(f"{base}.{t}.parquet") for t in ("filas", "det", "partes")}


def precalc_parquet(db, csv_path, version, bd, hilos, tmp_dir, log):
    """El precálculo de una corrida en Parquet. Si no está (o es de otra
    versión del código), el binario precalc de Rust escribe CSV y DuckDB
    los pasa a Parquet en paralelo. El build completo no usa el precálculo
    en SQLite (ese es para la corrida diaria)."""
    rutas = parquet_de(csv_path.parent / bd.PRECALC_ARCHIVO, version)
    if all(r.exists() and r.stat().st_mtime >= csv_path.stat().st_mtime for r in rutas.values()):
        log("    precálculo guardado")
        return rutas
    log("    calculando (Rust) y guardando el precálculo en Parquet")
    for viejo in csv_path.parent.glob("entradas.precalc.*.parquet"):
        viejo.unlink()
    salida = Path(tempfile.mkdtemp(prefix="precalc_rs_", dir=tmp_dir))
    try:
        bd.correr_precalc_rs(csv_path, salida, hilos)
        for tabla, ruta in rutas.items():
            tmp = ruta.with_name(ruta.name + ".tmp")
            db.execute(f"COPY (SELECT * FROM {bd.leer_csv_precalc(salida, tabla)}) TO '{tmp}' "
                       "(FORMAT parquet, COMPRESSION zstd)")
            os.replace(tmp, ruta)
    finally:
        shutil.rmtree(salida, ignore_errors=True)
    return rutas


# ── 3. Agregados en DuckDB ───────────────────────────────────────────────

# Columnas de los avisos que usan los agregados (la tabla e de DuckDB).
COLS_AGREGADOS = ["boletin_id", "juzgado", "sala", "expediente", "tipo_juicio_norm", "tipo_actuacion",
                  "tipo_acuerdo", "anio", "ponencia", "fecha_acuerdo"]

SQL_PARTES = """
    CREATE OR REPLACE TABLE det AS
        SELECT e.id AS entrada_id, d.tipo, d.rol, d.nucleo, d.nombre
        FROM det_todas d JOIN e ON e.rango = d.rango AND e.fila = d.fila
        WHERE d.tipo IN ({tipos});
    CREATE OR REPLACE TABLE partes_nombres AS
        SELECT row_number() OVER (ORDER BY tipo, nucleo, nombre) AS id, tipo, nucleo, nombre
        FROM (SELECT DISTINCT tipo, nucleo, nombre FROM det);
    CREATE OR REPLACE TABLE partes_avisos AS
        SELECT DISTINCT n.id AS nombre_id, d.entrada_id, d.rol
        FROM det d JOIN partes_nombres n ON n.tipo = d.tipo AND n.nucleo = d.nucleo AND n.nombre = d.nombre;
    DROP TABLE det;
"""

# Fecha de cada aviso: la de su boletín.
SQL_HECHOS = """
    CREATE OR REPLACE TABLE h AS
        SELECT e.id, e.sala, COALESCE(e.sala, e.juzgado) AS organo, b.fecha_iso AS fecha,
               substr(b.fecha_iso, 1, 7) AS mes, e.expediente, e.tipo_juicio_norm AS tipo,
               e.tipo_actuacion AS act, e.tipo_acuerdo AS acdo, e.anio, e.ponencia, e.fecha_acuerdo,
               e.juzgado
        FROM e JOIN bol b ON b.id = e.boletin_id;
"""

SENT = "('sentencia_definitiva', 'sentencia')"
# julianday() de SQLite acepta días que no existen ("2026-02-30") y los
# corre al mes siguiente; strptime de DuckDB no. Se replica a mano: fecha
# del día 1 más (día - 1).
DIA = ("(CASE WHEN regexp_full_match({c}, '\\d{{4}}-\\d{{2}}-\\d{{2}}') "
       "AND CAST(substr({c}, 6, 2) AS INT) BETWEEN 1 AND 12 AND CAST(substr({c}, 9, 2) AS INT) BETWEEN 1 AND 31 "
       "THEN CAST(make_date(CAST(substr({c}, 1, 4) AS INT), CAST(substr({c}, 6, 2) AS INT), 1) AS DATE) "
       "+ CAST(substr({c}, 9, 2) AS INT) - 1 END)")


def _dias(a, b):
    """julianday(b) - julianday(a) para fechas ISO, como en SQLite."""
    return f"CAST(date_diff('day', {DIA.format(c=a)}, {DIA.format(c=b)}) AS DOUBLE)"


SQL_ESTADISTICAS = f"""
    CREATE OR REPLACE TABLE organo_mes AS
        SELECT organo, CASE WHEN sala IS NOT NULL THEN 'sala' ELSE 'juzgado' END AS tipo_organo, mes,
               COUNT(*) AS avisos,
               SUM(CAST(act = 'acuerdo' AS INT)) AS acuerdos,
               SUM(CAST(act IN {SENT} AS INT)) AS sentencias,
               SUM(CAST(act = 'sentencia_interlocutoria' AS INT)) AS interlocutorias,
               SUM(CAST(act = 'audiencia' AS INT)) AS audiencias,
               SUM(CAST(acdo = 'prevencion' AS INT)) AS prevenciones,
               SUM(CAST(acdo = 'negacion' AS INT)) AS negaciones,
               SUM(CAST(acdo = 'admision' AS INT)) AS admisiones,
               COUNT(DISTINCT expediente) AS expedientes,
               SUM(CASE WHEN {_dias('fecha_acuerdo', 'fecha')} BETWEEN 0 AND 60
                        THEN {_dias('fecha_acuerdo', 'fecha')} END) AS demora_suma,
               SUM(CASE WHEN {_dias('fecha_acuerdo', 'fecha')} BETWEEN 0 AND 60 THEN 1 ELSE 0 END) AS demora_n
        FROM h WHERE organo IS NOT NULL AND fecha IS NOT NULL
        GROUP BY organo, 2, mes;
    CREATE OR REPLACE TABLE ponencia_mes AS
        SELECT sala, ponencia, mes, COUNT(*) AS sentencias
        FROM h WHERE sala IS NOT NULL AND ponencia IS NOT NULL AND fecha IS NOT NULL
        GROUP BY 1, 2, 3;
    CREATE OR REPLACE TABLE tipo_mes AS
        SELECT tipo, mes, COUNT(*) AS avisos, SUM(CAST(act IN {SENT} AS INT)) AS sentencias
        FROM h WHERE tipo IS NOT NULL AND fecha IS NOT NULL GROUP BY 1, 2;
    CREATE OR REPLACE TABLE tipo_organo AS
        SELECT tipo, organo, COUNT(*) AS avisos FROM h
        WHERE tipo IS NOT NULL AND organo IS NOT NULL GROUP BY 1, 2;
    CREATE OR REPLACE TABLE duracion_expedientes AS
        SELECT organo, expediente, tipo, CAST(substr(sentencia, 1, 4) AS INTEGER) AS anio,
               {_dias('primero', 'sentencia')} AS dias
        FROM (SELECT organo, expediente, MAX(tipo) AS tipo, MIN(fecha) AS primero,
                     MIN(CASE WHEN act IN {SENT} THEN fecha END) AS sentencia
              FROM h
              WHERE anio >= 2017 AND expediente IS NOT NULL AND fecha IS NOT NULL AND organo IS NOT NULL
              GROUP BY 1, 2)
        WHERE sentencia IS NOT NULL AND sentencia > primero;
    CREATE OR REPLACE TABLE organo_duracion AS
        SELECT organo, anio, COUNT(*) AS n,
               MAX(CASE WHEN rn = (n + 1) // 2 THEN dias END) AS mediana,
               MAX(CASE WHEN rn = (n + 3) // 4 THEN dias END) AS p25,
               MAX(CASE WHEN rn = (3 * n + 3) // 4 THEN dias END) AS p75
        FROM (SELECT organo, anio, dias,
                     ROW_NUMBER() OVER (PARTITION BY organo, anio ORDER BY dias) AS rn,
                     COUNT(*) OVER (PARTITION BY organo, anio) AS n
              FROM duracion_expedientes)
        GROUP BY organo, anio;
    CREATE OR REPLACE TABLE tipo_duracion AS
        SELECT tipo, anio, COUNT(*) AS n,
               MAX(CASE WHEN rn = (n + 1) // 2 THEN dias END) AS mediana,
               MAX(CASE WHEN rn = (n + 3) // 4 THEN dias END) AS p25,
               MAX(CASE WHEN rn = (3 * n + 3) // 4 THEN dias END) AS p75
        FROM (SELECT tipo, anio, dias,
                     ROW_NUMBER() OVER (PARTITION BY tipo, anio ORDER BY dias) AS rn,
                     COUNT(*) OVER (PARTITION BY tipo, anio) AS n
              FROM duracion_expedientes WHERE tipo IS NOT NULL)
        GROUP BY tipo, anio;
"""

SQL_RESUMEN = """
    CREATE OR REPLACE TABLE resumen_anio_tipo AS
        SELECT anio, tipo_juicio_norm AS tipo, COUNT(*) AS n FROM e GROUP BY 1, 2;
    CREATE OR REPLACE TABLE resumen_juzgado AS
        SELECT juzgado, COUNT(*) AS n FROM e WHERE juzgado IS NOT NULL GROUP BY 1;
"""


def _empresas(db):
    """Empresas: núcleos con al menos
    MIN_AVISOS_EMPRESA pares aviso-lado, nombre visible = variante más
    frecuente (y la primera alfabéticamente si empatan), ids por cantidad
    de pares y núcleo, slugs en ese orden."""
    filas = db.execute(f"""
        WITH v AS (
            SELECT n.nucleo, n.nombre, COUNT(*) AS c,
                   SUM(CAST(pa.rol = 1 AS INT)) AS act, SUM(CAST(pa.rol = 2 AS INT)) AS dem
            FROM partes_nombres n JOIN partes_avisos pa ON pa.nombre_id = n.id
            WHERE n.tipo = {agregados.TIPO_EMPRESA} GROUP BY n.nucleo, n.nombre)
        SELECT nucleo, arg_min(nombre, (-c, nombre)), SUM(c) AS total, SUM(act), SUM(dem)
        FROM v GROUP BY nucleo HAVING SUM(c) >= {agregados.MIN_AVISOS_EMPRESA}
        ORDER BY total DESC, nucleo
    """).fetchall()
    usados = set()
    filas = [(i + 1, nucleo, nombre, agregados._slug_libre(None, "", agregados.slug_empresa(nucleo), usados), int(c), int(a), int(d))
             for i, (nucleo, nombre, c, a, d) in enumerate(filas)]
    # A DuckDB por un CSV temporal (executemany va fila por fila).
    csv_path = Path(db.execute("SELECT current_setting('temp_directory')").fetchone()[0]) / "empresas.csv"
    with open(csv_path, "w", newline="") as f:
        csv.writer(f).writerows(filas)
    db.execute(f"""CREATE OR REPLACE TABLE empresas AS SELECT * FROM read_csv('{csv_path}', header = false,
                   columns = {{'id': 'BIGINT', 'nucleo': 'VARCHAR', 'nombre': 'VARCHAR', 'slug': 'VARCHAR',
                              'avisos': 'BIGINT', 'como_actora': 'BIGINT', 'como_demandada': 'BIGINT'}},
                   quote = '"', escape = '"', nullstr = '\\N')""")
    csv_path.unlink()
    db.execute(f"""
        CREATE OR REPLACE TABLE entradas_empresas AS
            SELECT DISTINCT e.id AS empresa_id, pa.entrada_id,
                   CASE pa.rol WHEN 1 THEN 'actora' ELSE 'demandada' END AS rol
            FROM partes_nombres n
            JOIN partes_avisos pa ON pa.nombre_id = n.id
            JOIN empresas e ON e.nucleo = n.nucleo
            WHERE n.tipo = {agregados.TIPO_EMPRESA};
        CREATE OR REPLACE TABLE eh AS
            SELECT ee.empresa_id, ee.entrada_id, ee.rol, b.fecha_iso AS fecha,
                   en.tipo_juicio_norm AS tipo, COALESCE(en.sala, en.juzgado) AS organo,
                   COALESCE(en.sala, en.juzgado, '') || '|' || COALESCE(en.expediente, CAST(en.id AS VARCHAR)) AS clave,
                   en.expediente
            FROM entradas_empresas ee JOIN e en ON en.id = ee.entrada_id JOIN bol b ON b.id = en.boletin_id;
        CREATE OR REPLACE TABLE empresas_final AS
            SELECT e.id, e.nucleo, e.nombre, e.slug, a.n AS avisos, a.act AS como_actora, a.dem AS como_demandada,
                   a.exps AS expedientes, a.primero AS primer_boletin, a.ultimo AS ultimo_boletin
            FROM empresas e
            JOIN (SELECT empresa_id, COUNT(*) AS n, SUM(CAST(rol = 'actora' AS INT)) AS act,
                         SUM(CAST(rol = 'demandada' AS INT)) AS dem, COUNT(DISTINCT clave) AS exps,
                         MIN(fecha) AS primero, MAX(fecha) AS ultimo
                  FROM eh GROUP BY empresa_id) a ON a.empresa_id = e.id;
        CREATE OR REPLACE TABLE empresas_tipos AS
            SELECT empresa_id, COALESCE(tipo, 'Sin tipo reconocido') AS tipo, COUNT(*) AS n FROM eh GROUP BY 1, 2;
        CREATE OR REPLACE TABLE empresas_anios AS
            SELECT empresa_id, substr(fecha, 1, 4) AS anio, COUNT(*) AS n FROM eh GROUP BY 1, 2;
        CREATE OR REPLACE TABLE empresas_meses AS
            SELECT empresa_id, substr(fecha, 1, 7) AS mes, COUNT(*) AS n FROM eh GROUP BY 1, 2;
        CREATE OR REPLACE TABLE empresas_expedientes AS
            SELECT empresa_id, organo, expediente, rol, COUNT(*) AS avisos, MAX(fecha) AS ultima_fecha,
                   CAST(substr(MAX(fecha || lpad(CAST(entrada_id AS VARCHAR), 12, '0')), 11) AS BIGINT) AS ultimo_id
            FROM eh GROUP BY empresa_id, organo, expediente, rol;
        CREATE OR REPLACE TABLE tipo_empresa AS
            SELECT tipo, empresa_id, rol, COUNT(*) AS avisos FROM eh WHERE tipo IS NOT NULL GROUP BY 1, 2, 3;
        DROP TABLE eh;
    """)
    return len(filas)


def _personas(db):
    """Personas: núcleos con al menos MIN_AVISOS_PERSONA avisos (distintos),
    nombre visible = variante más frecuente, ids y slugs en orden de núcleo.
    La corrida diaria las actualiza con agregados.personas_incremental."""
    filas = db.execute(f"""
        WITH v AS (
            SELECT n.nucleo, n.nombre, COUNT(DISTINCT pa.entrada_id) AS c,
                   SUM(CAST(pa.rol = 1 AS INT)) AS act, SUM(CAST(pa.rol = 2 AS INT)) AS dem,
                   MIN(b.fecha_iso) AS desde, MAX(b.fecha_iso) AS hasta
            FROM partes_nombres n
            JOIN partes_avisos pa ON pa.nombre_id = n.id
            JOIN e ON e.id = pa.entrada_id
            JOIN bol b ON b.id = e.boletin_id
            WHERE n.tipo = {agregados.TIPO_PERSONA} GROUP BY n.nucleo, n.nombre)
        SELECT nucleo, arg_min(nombre, (-c, nombre)), SUM(c) AS total, SUM(act), SUM(dem), MIN(desde), MAX(hasta)
        FROM v GROUP BY nucleo HAVING SUM(c) >= {agregados.MIN_AVISOS_PERSONA}
        ORDER BY nucleo
    """).fetchall()
    usados = set()
    filas = [(i + 1, nucleo, nombre, agregados._slug_libre(None, "", agregados.slug_persona(nucleo), usados),
              int(c), int(a), int(d), desde, hasta)
             for i, (nucleo, nombre, c, a, d, desde, hasta) in enumerate(filas)]
    return filas


# Tablas que DuckDB escribe en la base, con su definición en SQLite (las de
# agregados.py) y el orden de inserción.
def _ddl(sql_tablas, nombre):
    for t in sql_tablas.split(";"):
        if f"CREATE TABLE {nombre} (" in t:
            return t.strip()
    raise KeyError(nombre)


SQL_PARTES_DDL = {
    "partes_nombres": """CREATE TABLE partes_nombres (
        id INTEGER PRIMARY KEY, tipo INTEGER NOT NULL, nucleo TEXT NOT NULL, nombre TEXT NOT NULL,
        UNIQUE (tipo, nucleo, nombre))""",
    "partes_avisos": """CREATE TABLE partes_avisos (
        nombre_id INTEGER NOT NULL, entrada_id INTEGER NOT NULL, rol INTEGER NOT NULL,
        PRIMARY KEY (nombre_id, entrada_id, rol)) WITHOUT ROWID""",
    "entradas_partes": """CREATE TABLE entradas_partes (
        entrada_id INTEGER PRIMARY KEY, otras_actora INTEGER NOT NULL, otras_demandada INTEGER NOT NULL)""",
}


def _escribir(db, conn, tabla, ddl, consulta, orden=None):
    """Crea `tabla` en SQLite (conn, base adjunta en DuckDB como s) y la
    llena desde DuckDB."""
    conn.execute(f"DROP TABLE IF EXISTS {tabla}")
    conn.execute(ddl)
    conn.commit()
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({tabla})")]
    # SUM() de enteros da HUGEINT en DuckDB, que la extensión sqlite no
    # escribe: se pasa a BIGINT.
    tipos = dict(db.execute(f"SELECT column_name, column_type FROM (DESCRIBE {consulta})").fetchall())
    expr = [f"CAST({c} AS BIGINT)" if tipos.get(c) == "HUGEINT" else c for c in cols]
    db.execute(f"INSERT INTO s.{tabla} ({', '.join(cols)}) SELECT {', '.join(expr)} FROM ({consulta})"
               + (f" ORDER BY {orden}" if orden else ""))


# ── Build completo ────────────────────────────────────────────────────────

def build_completo(args, output, runs, log, bd):
    """Build completo con DuckDB. `bd` es el módulo build_db (funciones
    de SQLite que se reusan)."""
    t_inicio = time.monotonic()
    trabajo = output.with_name(output.name + ".duck")
    trabajo.mkdir(exist_ok=True)
    for f in trabajo.glob("*"):
        if f.is_file():
            f.unlink()
    # DuckDB comparte la máquina con los índices de SQLite (bd.HILOS hilos).
    db = _conectar(trabajo, max(2, bd.NUCLEOS - bd.HILOS), log)
    version = bd.precalc_version()
    runs = list(reversed(runs))  # del más nuevo al más viejo: gana la corrida más reciente

    log("Precálculos...")
    rutas = []
    for csv_path in runs:
        log(f"  {csv_path.parent.name}...")
        rutas.append(precalc_parquet(db, csv_path, version, bd, args.workers or bd.NUCLEOS, str(output.parent), log))

    # Base y boletines (SQLite).
    output.unlink(missing_ok=True)
    conn = sqlite3.connect(output)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    bd.hilos_sqlite(conn)
    bd.cache_sqlite(conn)
    bd.create_schema(conn)
    for csv_path in runs:
        index_path = csv_path.parent / "index.json"
        if index_path.exists():
            bd.load_index(conn, index_path)
    filtro_fecha = bool(args.desde_fecha or args.hasta_fecha)
    if filtro_fecha:
        conn.execute("DELETE FROM boletines WHERE fecha_iso IS NULL OR fecha_iso < ? OR fecha_iso > ?",
                     (args.desde_fecha or "0000-00-00", args.hasta_fecha or "9999-99-99"))
    conn.commit()

    log("Avisos (DuckDB)...")
    t0 = time.monotonic()
    db.execute(f"ATTACH '{output}' AS s (TYPE sqlite)")
    db.execute("CREATE OR REPLACE TABLE bol AS SELECT id, fecha_iso FROM s.boletines")
    union = lambda tabla: " UNION ALL ".join(
        f"SELECT {k} AS rango, * FROM read_parquet('{r[tabla]}')" for k, r in enumerate(rutas))
    cols = ", ".join(bd.COLS_CARGA)
    db.execute(f"CREATE OR REPLACE VIEW filas AS {union('filas')}")
    # Cada boletín sale de la corrida más nueva que lo tiene (y, con
    # --desde-fecha / --hasta-fecha, solo los del rango). La tabla e lleva
    # solo las columnas que usan los agregados: los textos largos (partes,
    # descripción) se leen del Parquet recién al escribir la base.
    db.execute(f"""
        CREATE OR REPLACE TABLE e AS
            SELECT row_number() OVER (ORDER BY f.rango, f.fila) AS id, f.rango, f.fila,
                   {', '.join('f.' + c for c in COLS_AGREGADOS)}
            FROM filas f
            JOIN (SELECT boletin_id, MIN(rango) AS rango FROM filas
                  {'WHERE boletin_id IN (SELECT id FROM bol)' if filtro_fecha else ''}
                  GROUP BY boletin_id) x
              ON x.boletin_id = f.boletin_id AND x.rango = f.rango
    """)
    total = db.execute("SELECT COUNT(*) FROM e").fetchone()[0]
    salteadas = db.execute("SELECT COUNT(*) FROM filas").fetchone()[0] - total
    log(f"  {total:,} avisos ({salteadas:,} de boletines que ya estaban en una corrida más nueva o fuera de rango)")
    # De a una corrida, para no ordenar los 35M avisos con sus textos juntos.
    for k, r in enumerate(rutas):
        db.execute(f"""INSERT INTO s.entradas (id, {cols})
                       SELECT e.id, {', '.join('f.' + c for c in bd.COLS_CARGA)}
                       FROM read_parquet('{r['filas']}') f JOIN e ON e.rango = {k} AND e.fila = f.fila
                       ORDER BY e.id""")
    db.execute("DETACH s")
    log(f"  escritos en la base: {time.monotonic() - t0:.0f} s")

    # SQLite: índices y FTS, mientras DuckDB calcula los agregados.
    def sqlite_lado():
        c = sqlite3.connect(output)
        c.execute("PRAGMA journal_mode=OFF")
        c.execute("PRAGMA synchronous=OFF")
        bd.hilos_sqlite(c)
        bd.cache_sqlite(c)
        t = time.monotonic()
        bd.create_indexes(c, args.indices_server, args.web)
        bd.populate_boletin_ranges(c, log)
        bd.populate_meta(c)
        c.commit()
        c.close()
        log(f"  índices (SQLite): {time.monotonic() - t:.0f} s")

    hilo_sqlite = threading.Thread(target=sqlite_lado)
    hilo_sqlite.start()

    log("Agregados (DuckDB)...")
    t0 = time.monotonic()
    tipos = [t for t, si in ((agregados.TIPO_EMPRESA, args.empresas), (agregados.TIPO_PERSONA, args.personas)) if si]
    if tipos:
        db.execute(f"CREATE OR REPLACE VIEW det_todas AS {union('det')}")
        db.execute(SQL_PARTES.format(tipos=", ".join(map(str, tipos))))
        log(f"  partes: {time.monotonic() - t0:.0f} s")
    if args.empresas:
        db.execute(f"""CREATE OR REPLACE TABLE entradas_partes AS
            SELECT e.id AS entrada_id, p.otras_actora, p.otras_demandada
            FROM ({union('partes')}) p JOIN e ON e.rango = p.rango AND e.fila = p.fila""")
    db.execute(SQL_HECHOS)
    db.execute(SQL_RESUMEN)
    if args.estadisticas:
        db.execute(SQL_ESTADISTICAS)
        log(f"  estadísticas: {time.monotonic() - t0:.0f} s")
    n_empresas = _empresas(db) if args.empresas else 0
    log(f"  empresas ({n_empresas:,}): {time.monotonic() - t0:.0f} s")
    filas_personas = _personas(db) if args.personas else []
    log(f"  personas ({len(filas_personas):,}): {time.monotonic() - t0:.0f} s")

    # FTS en su propio proceso y archivo (lee la base terminada).
    hilo_sqlite.join()
    fts_archivo = str(output.with_name(output.name + ".fts.sqlite"))
    # Como programa aparte y no con fork: copiar este proceso con DuckDB
    # corriendo en varios hilos puede dejar la copia trabada en un candado
    # que algún hilo tenía tomado (pasó con la base completa).
    t_fts = time.monotonic()
    proc_fts = subprocess.Popen([sys.executable, "-c", "import sys, build_db; build_db.armar_fts(sys.argv[1], sys.argv[2])",
                                 str(output), fts_archivo], cwd=Path(bd.__file__).resolve().parent)

    # Escribir los agregados en un archivo aparte mientras el FTS lee la
    # base, y juntarlos al final.
    log("Escribiendo agregados...")
    t0 = time.monotonic()
    agg_path = output.with_name(output.name + ".agregados.sqlite")
    agg_path.unlink(missing_ok=True)
    agg = sqlite3.connect(agg_path)
    agg.execute("PRAGMA journal_mode=OFF")
    agg.execute("PRAGMA synchronous=OFF")
    db.execute(f"ATTACH '{agg_path}' AS s (TYPE sqlite)")
    if tipos:
        _escribir(db, agg, "partes_nombres", SQL_PARTES_DDL["partes_nombres"], "SELECT * FROM partes_nombres", "id")
        _escribir(db, agg, "partes_avisos", SQL_PARTES_DDL["partes_avisos"], "SELECT * FROM partes_avisos",
                  "nombre_id, entrada_id, rol")
    if args.empresas:
        _escribir(db, agg, "entradas_partes", SQL_PARTES_DDL["entradas_partes"], "SELECT * FROM entradas_partes",
                  "entrada_id")
    _escribir(db, agg, "resumen_anio_tipo", "CREATE TABLE resumen_anio_tipo (anio INT, tipo TEXT, n INT)",
              "SELECT * FROM resumen_anio_tipo")
    _escribir(db, agg, "resumen_juzgado", "CREATE TABLE resumen_juzgado (juzgado TEXT, n INT)",
              "SELECT * FROM resumen_juzgado")
    if args.estadisticas:
        for t in ("organo_mes", "ponencia_mes", "tipo_mes", "tipo_organo", "duracion_expedientes",
                  "organo_duracion", "tipo_duracion"):
            _escribir(db, agg, t, _ddl(agregados.SQL_ESTADISTICAS_TABLAS, t), f"SELECT * FROM {t}",
                      "organo, expediente" if t == "duracion_expedientes" else None)
    if args.empresas:
        _escribir(db, agg, "empresas", _ddl(agregados.SQL_EMPRESAS_TABLAS, "empresas"),
                  "SELECT * FROM empresas_final", "id")
        _escribir(db, agg, "entradas_empresas", _ddl(agregados.SQL_EMPRESAS_TABLAS, "entradas_empresas"),
                  "SELECT * FROM entradas_empresas", "entrada_id")
        for t in ("empresas_tipos", "empresas_anios", "empresas_meses", "empresas_expedientes", "tipo_empresa"):
            _escribir(db, agg, t, _ddl(agregados.SQL_EMPRESAS_TABLAS, t), f"SELECT * FROM {t}")
    db.execute("DETACH s")
    db.close()
    if args.personas:
        agg.executescript("""
            CREATE TABLE personas (
                id INTEGER PRIMARY KEY, nucleo TEXT UNIQUE NOT NULL, nombre TEXT NOT NULL, slug TEXT UNIQUE NOT NULL,
                avisos INTEGER NOT NULL, como_actora INTEGER NOT NULL, como_demandada INTEGER NOT NULL,
                primer_boletin TEXT, ultimo_boletin TEXT
            );
        """)
        agg.executemany("INSERT INTO personas VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", filas_personas)
    # Índices y FTS de las tablas chicas, en el archivo aparte.
    if args.estadisticas:
        agg.executescript(agregados.SQL_ESTADISTICAS_INDICES)
    if args.empresas:
        agg.executescript(agregados.SQL_EMPRESAS_INDICES)
    if args.personas:
        agg.executescript("""
            CREATE INDEX idx_personas_avisos ON personas (avisos DESC);
            CREATE VIRTUAL TABLE personas_fts USING fts5(
                nombre, content='personas', content_rowid='id', detail=none
            );
            INSERT INTO personas_fts(rowid, nombre) SELECT id, nombre FROM personas;
        """)
    agg.commit()
    agg.close()
    log(f"  escritos: {time.monotonic() - t0:.0f} s")

    if proc_fts.wait():
        raise RuntimeError(f"la etapa FTS terminó con código {proc_fts.returncode}")
    log(f"  fts: {time.monotonic() - t_fts:.0f} s")

    conn = sqlite3.connect(output)
    conn.execute("PRAGMA synchronous=OFF")
    bd.hilos_sqlite(conn)
    bd.cache_sqlite(conn)
    bd._juntar(conn, "fts", fts_archivo, log)
    os.remove(fts_archivo)
    bd._juntar(conn, "agregados", str(agg_path), log)
    agg_path.unlink()
    if args.sivep:
        bd.build_sivep(conn, log, args.sivep)
    conn.commit()
    conn.close()
    if args.empresas or args.personas:
        bd.build_vocabulario(output, log)
    for f in trabajo.rglob("*"):
        if f.is_file():
            f.unlink()
    for d in sorted(trabajo.rglob("*"), reverse=True):
        d.rmdir()
    trabajo.rmdir()
    log(f"Total: {total:,} entradas en {time.monotonic() - t_inicio:.0f} s.")
