"""
agregados.py — Lo que la corrida diaria (build_db.py --incremental) rehace
sobre la base publicada: empresas, personas, estadísticas y resúmenes, solo
lo que tocan los avisos nuevos (ids > max_id_antes, detecciones en
st.partes_carga). El build completo los arma con DuckDB (build_duck.py),
con el mismo resultado (verificado tabla por tabla) y las definiciones de
tabla de acá. Los ids de empresas y personas nuevas sí difieren: el build
completo los numera por cantidad de avisos o por núcleo, el incremental los
agrega al final.

Supuesto: toda entrada tiene su boletín con fecha (load_index carga todos
los del índice; verificado en la base de 2026-11).
"""

import re
import unicodedata

TIPO_EMPRESA, TIPO_PERSONA = 1, 2
MIN_AVISOS_EMPRESA = 2
MIN_AVISOS_PERSONA = 2

SENTENCIAS = "('sentencia_definitiva', 'sentencia')"


def slugify(texto, vacio, largo=80):
    """Slug de la URL de una empresa o persona: el núcleo sin acentos, en
    minúsculas, con guiones."""
    s = unicodedata.normalize("NFD", str(texto or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:largo].strip("-") or vacio


def slug_empresa(nucleo):
    return slugify(nucleo, "empresa")


def slug_persona(nucleo):
    return slugify(nucleo, "persona") if nucleo else "persona"


def _slug_libre(conn, tabla, base, usados=None):
    slug, k = base, 2
    while (usados is not None and slug in usados) or (
            usados is None and conn.execute(f"SELECT 1 FROM {tabla} WHERE slug = ?", (slug,)).fetchone()):
        slug, k = f"{base}-{k}", k + 1
    if usados is not None:
        usados.add(slug)
    return slug


# ── Empresas ──────────────────────────────────────────────────────────────

SQL_EMPRESAS_TABLAS = """
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
    CREATE TABLE entradas_empresas (
        empresa_id INTEGER NOT NULL,
        entrada_id INTEGER NOT NULL,
        rol        TEXT NOT NULL
    );
    CREATE TABLE empresas_tipos (empresa_id INTEGER, tipo TEXT, n INTEGER);
    CREATE TABLE empresas_anios (empresa_id INTEGER, anio TEXT, n INTEGER);
    CREATE TABLE empresas_meses (empresa_id INTEGER, mes TEXT, n INTEGER);
    CREATE TABLE empresas_expedientes (
        empresa_id INTEGER, organo TEXT, expediente TEXT, rol TEXT,
        avisos INTEGER, ultima_fecha TEXT, ultimo_id INTEGER
    );
    CREATE TABLE tipo_empresa (tipo TEXT, empresa_id INTEGER, rol TEXT, avisos INTEGER);
"""

SQL_EMPRESAS_INDICES = """
    CREATE INDEX idx_ee_empresa ON entradas_empresas (empresa_id, entrada_id);
    CREATE INDEX idx_ee_entrada ON entradas_empresas (entrada_id);
    CREATE INDEX idx_empresas_tipos ON empresas_tipos (empresa_id, n DESC);
    CREATE INDEX idx_empresas_anios ON empresas_anios (empresa_id, anio);
    CREATE INDEX idx_empresas_meses ON empresas_meses (empresa_id, mes);
    CREATE INDEX idx_empresas_expedientes ON empresas_expedientes (empresa_id, ultima_fecha DESC);
    CREATE INDEX idx_tipo_empresa ON tipo_empresa (tipo, rol, avisos DESC);
    CREATE VIRTUAL TABLE empresas_fts USING fts5(
        nombre, content='empresas', content_rowid='id', detail=none
    );
    INSERT INTO empresas_fts(rowid, nombre) SELECT id, nombre FROM empresas;
"""


def _variantes(conn, fuente, tipo, filtro=""):
    """(núcleo, nombre, pares aviso-lado, como actora, como demandada) por
    variante de nombre. `filtro`: condición extra sobre n (núcleos)."""
    return conn.execute(f"""
        SELECT n.nucleo, n.nombre, COUNT(*), SUM(pa.rol = 1), SUM(pa.rol = 2)
        FROM {fuente}.partes_nombres n JOIN {fuente}.partes_avisos pa ON pa.nombre_id = n.id
        WHERE n.tipo = {tipo} {filtro}
        GROUP BY n.id ORDER BY n.nucleo, COUNT(*) DESC, n.nombre
    """)


def _por_nucleo(filas):
    """Suma las variantes de cada núcleo. La primera (la más frecuente)
    da el nombre visible."""
    actual = None
    for nucleo, nombre, n, act, dem in filas:
        if nucleo != actual:
            if actual is not None:
                yield fila
            actual, fila = nucleo, [nucleo, nombre, 0, 0, 0]
        fila[2] += n
        fila[3] += act
        fila[4] += dem
    if actual is not None:
        yield fila


def _empresas_hechos(conn, donde=""):
    """Tabla temporal con lo que necesitan los agregados de cada empresa.
    Sin `donde` recorre entradas_empresas en orden de entrada_id (así se
    insertó), de modo que las lecturas de entradas van en orden."""
    conn.execute("DROP TABLE IF EXISTS temp.eh")
    conn.execute(f"""
        CREATE TEMP TABLE eh AS
            SELECT ee.empresa_id, ee.entrada_id, ee.rol, b.fecha_iso AS fecha,
                   en.tipo_juicio_norm AS tipo, COALESCE(en.sala, en.juzgado) AS organo,
                   COALESCE(en.sala, en.juzgado, '') || '|' || COALESCE(en.expediente, en.id) AS clave,
                   en.expediente
            FROM entradas_empresas ee
            CROSS JOIN entradas en ON en.id = ee.entrada_id
            JOIN boletines b ON b.id = en.boletin_id
            {donde}
    """)


SQL_EMPRESAS_AGREGADOS = """
    UPDATE empresas SET
        (avisos, como_actora, como_demandada, expedientes, primer_boletin, ultimo_boletin) =
        (a.n, a.act, a.dem, a.exps, a.primero, a.ultimo)
    FROM (SELECT empresa_id, COUNT(*) AS n, SUM(rol = 'actora') AS act, SUM(rol = 'demandada') AS dem,
                 COUNT(DISTINCT clave) AS exps, MIN(fecha) AS primero, MAX(fecha) AS ultimo
          FROM temp.eh GROUP BY empresa_id) AS a
    WHERE a.empresa_id = empresas.id;
    INSERT INTO empresas_tipos
        SELECT empresa_id, COALESCE(tipo, 'Sin tipo reconocido'), COUNT(*) FROM temp.eh GROUP BY 1, 2;
    INSERT INTO empresas_anios
        SELECT empresa_id, substr(fecha, 1, 4), COUNT(*) FROM temp.eh GROUP BY 1, 2;
    INSERT INTO empresas_meses
        SELECT empresa_id, substr(fecha, 1, 7), COUNT(*) FROM temp.eh GROUP BY 1, 2;
    -- Último aviso por FECHA (los ids no son cronológicos) y, con la misma
    -- fecha, el de id más alto: determinista, igual que en build_duck.py.
    INSERT INTO empresas_expedientes
        SELECT empresa_id, organo, expediente, rol, COUNT(*), MAX(fecha),
               CAST(substr(MAX(fecha || printf('%012d', entrada_id)), 11) AS INTEGER)
        FROM temp.eh GROUP BY empresa_id, organo, expediente, rol;
    INSERT INTO tipo_empresa
        SELECT tipo, empresa_id, rol, COUNT(*) FROM temp.eh WHERE tipo IS NOT NULL GROUP BY 1, 2, 3;
    DROP TABLE temp.eh;
"""


def empresas_incremental(conn, log):
    """Rehace solo las empresas que aparecen en los avisos nuevos (y las que
    con ellos llegan al mínimo)."""
    conn.executescript(f"""
        DROP TABLE IF EXISTS temp.afectadas;
        CREATE TEMP TABLE afectadas AS SELECT DISTINCT nucleo FROM st.partes_carga WHERE tipo = {TIPO_EMPRESA};
    """)
    filtro = "AND n.nucleo IN (SELECT nucleo FROM temp.afectadas)"
    nuevas = 0
    for nucleo, nombre, n, act, dem in _por_nucleo(_variantes(conn, "main", TIPO_EMPRESA, filtro)):
        if n < MIN_AVISOS_EMPRESA:
            continue
        if conn.execute("SELECT 1 FROM empresas WHERE nucleo = ?", (nucleo,)).fetchone():
            conn.execute("UPDATE empresas SET nombre = ?, avisos = ?, como_actora = ?, como_demandada = ? "
                         "WHERE nucleo = ?", (nombre, n, act, dem, nucleo))
        else:
            conn.execute("INSERT INTO empresas (nucleo, nombre, slug, avisos, como_actora, como_demandada) "
                         "VALUES (?, ?, ?, ?, ?, ?)",
                         (nucleo, nombre, _slug_libre(conn, "empresas", slug_empresa(nucleo)), n, act, dem))
            nuevas += 1
    conn.executescript(f"""
        DROP TABLE IF EXISTS temp.ids;
        CREATE TEMP TABLE ids AS
            SELECT id FROM empresas WHERE nucleo IN (SELECT nucleo FROM temp.afectadas);
        DELETE FROM entradas_empresas WHERE empresa_id IN (SELECT id FROM temp.ids);
        INSERT INTO entradas_empresas
            SELECT e.id, pa.entrada_id, CASE pa.rol WHEN 1 THEN 'actora' ELSE 'demandada' END
            FROM partes_nombres n
            JOIN partes_avisos pa ON pa.nombre_id = n.id
            JOIN empresas e ON e.nucleo = n.nucleo
            WHERE n.tipo = {TIPO_EMPRESA} AND n.nucleo IN (SELECT nucleo FROM temp.afectadas)
            GROUP BY 1, 2, 3;
        DELETE FROM empresas_tipos WHERE empresa_id IN (SELECT id FROM temp.ids);
        DELETE FROM empresas_anios WHERE empresa_id IN (SELECT id FROM temp.ids);
        DELETE FROM empresas_meses WHERE empresa_id IN (SELECT id FROM temp.ids);
        DELETE FROM empresas_expedientes WHERE empresa_id IN (SELECT id FROM temp.ids);
        DELETE FROM tipo_empresa WHERE empresa_id IN (SELECT id FROM temp.ids);
    """)
    _empresas_hechos(conn, "WHERE ee.empresa_id IN (SELECT id FROM temp.ids)")
    conn.executescript(SQL_EMPRESAS_AGREGADOS)
    n_afectadas = conn.execute("SELECT COUNT(*) FROM temp.ids").fetchone()[0]
    conn.executescript("""
        INSERT INTO empresas_fts(empresas_fts) VALUES('rebuild');
        DROP TABLE temp.ids;
        DROP TABLE temp.afectadas;
    """)
    log(f"  empresas: {n_afectadas} rehechas, {nuevas} nuevas")


# ── Personas ──────────────────────────────────────────────────────────────

def _personas_variantes(conn, fuente, filtro=""):
    """Por variante de nombre de persona: avisos distintos, como actora,
    como demandada, primera y última fecha. La fecha sale del rango de ids
    de cada boletín (las entradas de un boletín son contiguas), sin ir a la
    tabla entradas."""
    conn.executescript("""
        DROP TABLE IF EXISTS temp.rangos;
        CREATE TEMP TABLE rangos AS
            SELECT entrada_desde AS desde, fecha_iso AS fecha FROM boletines
            WHERE n_entradas > 0 AND entrada_desde IS NOT NULL;
        CREATE INDEX temp.idx_rangos ON rangos (desde);
    """)
    return conn.execute(f"""
        SELECT n.nucleo, n.nombre, COUNT(DISTINCT pa.entrada_id), SUM(pa.rol = 1), SUM(pa.rol = 2),
               MIN(f.fecha), MAX(f.fecha)
        FROM {fuente}.partes_nombres n
        JOIN {fuente}.partes_avisos pa ON pa.nombre_id = n.id
        JOIN temp.rangos f ON f.desde = (SELECT MAX(desde) FROM temp.rangos WHERE desde <= pa.entrada_id)
        WHERE n.tipo = {TIPO_PERSONA} {filtro}
        GROUP BY n.id ORDER BY n.nucleo, 3 DESC, n.nombre
    """)


def _personas_por_nucleo(filas):
    actual = None
    for nucleo, nombre, n, act, dem, desde, hasta in filas:
        if nucleo != actual:
            if actual is not None:
                yield fila
            actual, fila = nucleo, [nucleo, nombre, 0, 0, 0, desde, hasta]
        fila[2] += n
        fila[3] += act
        fila[4] += dem
        fila[5] = min(filter(None, (fila[5], desde)), default=None)
        fila[6] = max(filter(None, (fila[6], hasta)), default=None)
    if actual is not None:
        yield fila


def personas_incremental(conn, log):
    conn.executescript(f"""
        DROP TABLE IF EXISTS temp.afectadas;
        CREATE TEMP TABLE afectadas AS SELECT DISTINCT nucleo FROM st.partes_carga WHERE tipo = {TIPO_PERSONA};
    """)
    filtro = "AND n.nucleo IN (SELECT nucleo FROM temp.afectadas)"
    nuevas = rehechas = 0
    filas = list(_personas_por_nucleo(_personas_variantes(conn, "main", filtro)))
    for nucleo, nombre, n, act, dem, desde, hasta in filas:
        if n < MIN_AVISOS_PERSONA:
            continue
        previa = conn.execute("SELECT id, nombre FROM personas WHERE nucleo = ?", (nucleo,)).fetchone()
        if previa:
            conn.execute("UPDATE personas SET nombre = ?, avisos = ?, como_actora = ?, como_demandada = ?, "
                         "primer_boletin = ?, ultimo_boletin = ? WHERE id = ?",
                         (nombre, n, act, dem, desde, hasta, previa[0]))
            if previa[1] != nombre:
                conn.execute("INSERT INTO personas_fts(personas_fts, rowid, nombre) VALUES('delete', ?, ?)", previa)
                conn.execute("INSERT INTO personas_fts(rowid, nombre) VALUES (?, ?)", (previa[0], nombre))
            rehechas += 1
        else:
            cur = conn.execute(
                "INSERT INTO personas (nucleo, nombre, slug, avisos, como_actora, como_demandada, "
                "primer_boletin, ultimo_boletin) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (nucleo, nombre, _slug_libre(conn, "personas", slug_persona(nucleo)), n, act, dem, desde, hasta))
            conn.execute("INSERT INTO personas_fts(rowid, nombre) VALUES (?, ?)", (cur.lastrowid, nombre))
            nuevas += 1
    conn.executescript("DROP TABLE temp.afectadas; DROP TABLE temp.rangos;")
    log(f"  personas: {rehechas} rehechas, {nuevas} nuevas")


# ── Estadísticas por órgano y tipo de juicio ──────────────────────────────
# /salas, /juzgados, pestaña de desempeño de /juzgado/:slug y /tipos. Todo
# por mes de publicación. Mide lo que publica el boletín, no la carga de
# trabajo (no hay ingresos ni personal).

def _hechos(conn, desde_boletines=None):
    """Tabla temporal angosta con lo que usan las estadísticas: una sola
    lectura de entradas. `desde_boletines`: consulta con los ids de
    boletines a leer (incremental); sin ella, todo."""
    conn.execute("DROP TABLE IF EXISTS temp.h")
    if desde_boletines is None:
        origen = "entradas e JOIN boletines b ON b.id = e.boletin_id"
    else:
        origen = (f"boletines b JOIN entradas e ON e.id BETWEEN b.entrada_desde AND b.entrada_hasta "
                  f"AND e.boletin_id = b.id WHERE b.id IN ({desde_boletines})")
    conn.execute(f"""
        CREATE TEMP TABLE h AS
            SELECT e.id, e.sala, COALESCE(e.sala, e.juzgado) AS organo, b.fecha_iso AS fecha,
                   substr(b.fecha_iso, 1, 7) AS mes, e.expediente, e.tipo_juicio_norm AS tipo,
                   e.tipo_actuacion AS act, e.tipo_acuerdo AS acdo, e.anio, e.ponencia, e.fecha_acuerdo
            FROM {origen}
    """)


SQL_POR_MES = f"""
    -- Demora de publicación: días entre la fecha del acuerdo ("ACUERDOS
    -- DEL ...") y la del boletín. Fuera de 0 a 60 días se descarta: son
    -- errores de lectura de la fecha.
    INSERT INTO organo_mes
        SELECT organo, CASE WHEN sala IS NOT NULL THEN 'sala' ELSE 'juzgado' END, mes,
               COUNT(*),
               SUM(act = 'acuerdo'),
               SUM(act IN {SENTENCIAS}),
               SUM(act = 'sentencia_interlocutoria'),
               SUM(act = 'audiencia'),
               SUM(acdo = 'prevencion'),
               SUM(acdo = 'negacion'),
               SUM(acdo = 'admision'),
               COUNT(DISTINCT expediente),
               SUM(CASE WHEN julianday(fecha) - julianday(fecha_acuerdo) BETWEEN 0 AND 60
                        THEN julianday(fecha) - julianday(fecha_acuerdo) END),
               SUM(CASE WHEN julianday(fecha) - julianday(fecha_acuerdo) BETWEEN 0 AND 60 THEN 1 ELSE 0 END)
        FROM temp.h WHERE organo IS NOT NULL AND fecha IS NOT NULL
        GROUP BY organo, 2, mes;
    INSERT INTO ponencia_mes
        SELECT sala, ponencia, mes, COUNT(*)
        FROM temp.h WHERE sala IS NOT NULL AND ponencia IS NOT NULL AND fecha IS NOT NULL
        GROUP BY 1, 2, 3;
    INSERT INTO tipo_mes
        SELECT tipo, mes, COUNT(*), SUM(act IN {SENTENCIAS})
        FROM temp.h WHERE tipo IS NOT NULL AND fecha IS NOT NULL
        GROUP BY 1, 2;
"""

# Tiempo hasta sentencia, por expediente: del primer aviso publicado a la
# primera sentencia (definitiva, o de sala). Solo expedientes de 2017 en
# adelante: los anteriores empezaron antes del primer boletín disponible
# (octubre de 2016) y su primer aviso no es el real. Si la sentencia es el
# primer aviso publicado no hay un tiempo que medir. duracion_expedientes
# queda en la base para que el incremental rehaga solo los expedientes que
# cambian.
SQL_EXP_FECHAS = f"""
    SELECT organo, expediente, MAX(tipo) AS tipo, MIN(fecha) AS primero,
           MIN(CASE WHEN act IN {SENTENCIAS} THEN fecha END) AS sentencia
    FROM temp.h
    WHERE anio >= 2017 AND expediente IS NOT NULL AND fecha IS NOT NULL AND organo IS NOT NULL
    GROUP BY 1, 2
"""
SQL_DURACION_DE = """
    INSERT INTO duracion_expedientes
        SELECT organo, expediente, tipo, CAST(substr(sentencia, 1, 4) AS INTEGER),
               julianday(sentencia) - julianday(primero)
        FROM ({fechas}) WHERE sentencia IS NOT NULL AND sentencia > primero;
"""
SQL_DURACIONES = """
    DELETE FROM organo_duracion;
    INSERT INTO organo_duracion
        SELECT organo, anio, COUNT(*) AS n,
               MAX(CASE WHEN rn = (n + 1) / 2 THEN dias END),
               MAX(CASE WHEN rn = (n + 3) / 4 THEN dias END),
               MAX(CASE WHEN rn = (3 * n + 3) / 4 THEN dias END)
        FROM (SELECT organo, anio, dias,
                     ROW_NUMBER() OVER (PARTITION BY organo, anio ORDER BY dias) AS rn,
                     COUNT(*) OVER (PARTITION BY organo, anio) AS n
              FROM duracion_expedientes)
        GROUP BY organo, anio;
    DELETE FROM tipo_duracion;
    INSERT INTO tipo_duracion
        SELECT tipo, anio, COUNT(*) AS n,
               MAX(CASE WHEN rn = (n + 1) / 2 THEN dias END),
               MAX(CASE WHEN rn = (n + 3) / 4 THEN dias END),
               MAX(CASE WHEN rn = (3 * n + 3) / 4 THEN dias END)
        FROM (SELECT tipo, anio, dias,
                     ROW_NUMBER() OVER (PARTITION BY tipo, anio ORDER BY dias) AS rn,
                     COUNT(*) OVER (PARTITION BY tipo, anio) AS n
              FROM duracion_expedientes WHERE tipo IS NOT NULL)
        GROUP BY tipo, anio;
"""

SQL_ESTADISTICAS_TABLAS = """
    CREATE TABLE organo_mes (
        organo TEXT, tipo_organo TEXT, mes TEXT, avisos INTEGER, acuerdos INTEGER, sentencias INTEGER,
        interlocutorias INTEGER, audiencias INTEGER, prevenciones INTEGER, negaciones INTEGER,
        admisiones INTEGER, expedientes INTEGER, demora_suma REAL, demora_n INTEGER
    );
    CREATE TABLE ponencia_mes (sala TEXT, ponencia INTEGER, mes TEXT, sentencias INTEGER);
    CREATE TABLE tipo_mes (tipo TEXT, mes TEXT, avisos INTEGER, sentencias INTEGER);
    CREATE TABLE tipo_organo (tipo TEXT, organo TEXT, avisos INTEGER);
    CREATE TABLE duracion_expedientes (
        organo TEXT NOT NULL, expediente TEXT NOT NULL, tipo TEXT, anio INTEGER, dias REAL,
        PRIMARY KEY (organo, expediente)
    ) WITHOUT ROWID;
    CREATE TABLE organo_duracion (organo TEXT, anio INTEGER, n INTEGER, mediana REAL, p25 REAL, p75 REAL);
    CREATE TABLE tipo_duracion (tipo TEXT, anio INTEGER, n INTEGER, mediana REAL, p25 REAL, p75 REAL);
"""
SQL_ESTADISTICAS_INDICES = """
    CREATE INDEX idx_organo_mes ON organo_mes (organo, mes);
    CREATE INDEX idx_organo_mes_tipo ON organo_mes (tipo_organo, mes);
    CREATE INDEX idx_ponencia_mes ON ponencia_mes (sala, mes);
    CREATE INDEX idx_tipo_mes ON tipo_mes (tipo, mes);
    CREATE INDEX idx_tipo_mes_mes ON tipo_mes (mes);
    CREATE INDEX idx_tipo_organo ON tipo_organo (tipo, avisos DESC);
    CREATE INDEX idx_organo_duracion ON organo_duracion (organo, anio);
    CREATE INDEX idx_tipo_duracion ON tipo_duracion (tipo, anio);
"""


def _sumar(conn, tabla, claves, valores, consulta):
    """Suma a `tabla` los conteos de `consulta` (claves + valores); crea las
    filas que falten. Las claves pueden ser NULL (se comparan con IS)."""
    conn.execute("DROP TABLE IF EXISTS temp.suma")
    conn.execute(f"CREATE TEMP TABLE suma AS {consulta}")
    igual = " AND ".join(f"{tabla}.{c} IS temp.suma.{c}" for c in claves)
    conn.execute(f"""UPDATE {tabla} SET {", ".join(f"{v} = {tabla}.{v} + temp.suma.{v}" for v in valores)}
                     FROM temp.suma WHERE {igual}""")
    conn.execute(f"""INSERT INTO {tabla} ({", ".join(claves + valores)})
                     SELECT {", ".join(claves + valores)} FROM temp.suma
                     WHERE NOT EXISTS (SELECT 1 FROM {tabla} WHERE {igual})""")
    conn.execute("DROP TABLE temp.suma")


def estadisticas_incremental(conn, log, max_id_antes):
    """Meses tocados por los boletines nuevos: se rehacen enteros (leyendo
    solo los boletines de esos meses). tipo_organo: se suma lo nuevo.
    Duraciones: se rehacen los expedientes que tienen avisos nuevos y después
    las medianas (tabla chica)."""
    conn.execute("PRAGMA temp_store=FILE")
    conn.executescript(f"""
        DROP TABLE IF EXISTS temp.meses;
        CREATE TEMP TABLE meses AS
            SELECT DISTINCT substr(fecha_iso, 1, 7) AS mes FROM boletines
            WHERE n_entradas > 0 AND entrada_hasta > {int(max_id_antes)};
        DELETE FROM organo_mes WHERE mes IN (SELECT mes FROM temp.meses);
        DELETE FROM ponencia_mes WHERE mes IN (SELECT mes FROM temp.meses);
        DELETE FROM tipo_mes WHERE mes IN (SELECT mes FROM temp.meses);
    """)
    _hechos(conn, "SELECT id FROM boletines WHERE n_entradas > 0 "
                  "AND substr(fecha_iso, 1, 7) IN (SELECT mes FROM temp.meses)")
    conn.executescript(SQL_POR_MES)
    meses = conn.execute("SELECT COUNT(*) FROM temp.meses").fetchone()[0]
    _sumar(conn, "tipo_organo", ["tipo", "organo"], ["avisos"],
           f"""SELECT tipo, organo, COUNT(*) AS avisos FROM temp.h
               WHERE id > {int(max_id_antes)} AND tipo IS NOT NULL AND organo IS NOT NULL GROUP BY 1, 2""")
    # Expedientes con avisos nuevos: se recalculan con todos sus avisos.
    conn.executescript(f"""
        DROP TABLE IF EXISTS temp.exps;
        CREATE TEMP TABLE exps AS
            SELECT DISTINCT organo, expediente FROM temp.h
            WHERE id > {int(max_id_antes)} AND anio >= 2017 AND expediente IS NOT NULL AND organo IS NOT NULL;
        DROP TABLE temp.h;
        DELETE FROM duracion_expedientes
            WHERE (organo, expediente) IN (SELECT organo, expediente FROM temp.exps);
    """)
    conn.execute("""
        CREATE TEMP TABLE h AS
            SELECT e.id, e.sala, COALESCE(e.sala, e.juzgado) AS organo, b.fecha_iso AS fecha,
                   e.expediente, e.tipo_juicio_norm AS tipo, e.tipo_actuacion AS act, e.anio
            FROM temp.exps x
            JOIN entradas e ON e.expediente = x.expediente AND COALESCE(e.sala, e.juzgado) = x.organo
            JOIN boletines b ON b.id = e.boletin_id
    """)
    exps = conn.execute("SELECT COUNT(*) FROM temp.exps").fetchone()[0]
    conn.executescript(SQL_DURACION_DE.format(fechas=SQL_EXP_FECHAS) + SQL_DURACIONES + """
        DROP TABLE temp.h;
        DROP TABLE temp.exps;
        DROP TABLE temp.meses;
    """)
    log(f"  estadísticas: {meses} meses rehechos, {exps} expedientes revisados")


# ── Resumen para el arranque del server ───────────────────────────────────
# Conteos sobre toda la tabla que el server calculaba al abrir la base (~5 s
# y un índice de 1.2 GB): por año del expediente y tipo, y por juzgado.


def resumen_incremental(conn, max_id_antes):
    _sumar(conn, "resumen_anio_tipo", ["anio", "tipo"], ["n"],
           f"""SELECT anio, tipo_juicio_norm AS tipo, COUNT(*) AS n FROM entradas
               WHERE id > {int(max_id_antes)} GROUP BY 1, 2""")
    _sumar(conn, "resumen_juzgado", ["juzgado"], ["n"],
           f"""SELECT juzgado, COUNT(*) AS n FROM entradas
               WHERE id > {int(max_id_antes)} AND juzgado IS NOT NULL GROUP BY 1""")
