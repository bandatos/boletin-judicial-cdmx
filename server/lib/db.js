// db.js — capa de datos. Lee el MISMO boletin.sqlite que genera
// scraper/build_db.py (el que también consume el sitio estático en sql.js
// del lado del cliente) — el schema no cambia, solo cambia quién lo lee.

const path = require('path');
const Database = require('better-sqlite3');
const { slugify } = require('./slug');
const busqueda = require('./busqueda');

const DB_PATH = process.env.DB_PATH || path.join(__dirname, '..', '..', 'boletin.sqlite');

let db = null;

function getDb() {
  if (!db) {
    db = new Database(DB_PATH, { readonly: true, fileMustExist: true });
    db.pragma('query_only = ON');
  }
  return db;
}

// Reabre la base y descarta los agregados en memoria. La llama
// litigios-cdmx.js cuando cambia el archivo en disco (en dev y en producción).
// Cambia cada vez que se reabre la base: la caché de HTML del router lo usa
// para descartar lo renderizado con la base anterior.
let generacion = 0;
function getGeneracion() {
  return generacion;
}

function reloadDb() {
  generacion++;
  if (db) db.close();
  db = null;
  resumen = null;
  resumenEmpresas = null;
  resumenPersonas = null;
  portada = null;
  getDb();
}

const MAX_RESULTS = 100;
const MAX_ROWS = 500;
const CHART_YEARS = 15;

// Agregados sobre toda la tabla (tipos, años, gráfico, conteos por tipo y
// por juzgado, total). Con el histórico completo (~30M entradas) cada uno
// es un recorrido de la tabla entera, así que se calculan una sola vez por
// apertura de la DB (reloadDb() los descarta) en vez de en cada request.
let resumen = null;

function getResumen() {
  if (resumen) return resumen;
  const d = getDb();

  // Un solo recorrido: (anio, tipo) -> n. De ahí salen el total, los
  // conteos por tipo, la lista de años y el gráfico.
  const porAnioTipo = d.prepare(`
    SELECT anio, tipo_juicio_norm AS tipo, COUNT(*) AS n
    FROM entradas GROUP BY anio, tipo_juicio_norm
  `).all();

  const porTipo = new Map();
  const anios = new Set();
  let total = 0;
  for (const { anio, tipo, n } of porAnioTipo) {
    total += n;
    if (tipo) porTipo.set(tipo, (porTipo.get(tipo) || 0) + n);
    if (anio) anios.add(anio);
  }

  // Usa idx_expediente (juzgado, expediente) como índice cubriente.
  const porJuzgado = new Map(
    d.prepare('SELECT juzgado, COUNT(*) AS n FROM entradas WHERE juzgado IS NOT NULL GROUP BY juzgado')
      .all().map(r => [r.juzgado, r.n])
  );
  const slugs = new Map([...porJuzgado.keys()].map(j => [slugify(j), j]));

  // Boletines por fecha, para el calendario de /fecha (unos 2,000: chico).
  const boletinesPorFecha = new Map();
  for (const b of d.prepare(`
    SELECT id, fecha_iso, pdf_url, n_entradas AS n, entrada_desde AS desde, entrada_hasta AS hasta
    FROM boletines WHERE fecha_iso IS NOT NULL AND n_entradas > 0 ORDER BY fecha_iso, id
  `).all()) {
    if (!boletinesPorFecha.has(b.fecha_iso)) boletinesPorFecha.set(b.fecha_iso, []);
    boletinesPorFecha.get(b.fecha_iso).push(b);
  }
  const fechas = [...boletinesPorFecha.keys()];

  resumen = { porAnioTipo, porTipo, anios, total, porJuzgado, slugs, boletinesPorFecha, fechas };
  return resumen;
}

function getTipos() {
  return [...getResumen().porTipo.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 200)
    .map(([tipo]) => tipo);
}

// Años del expediente para el filtro. Se descartan los imposibles: son
// errores de tipeo del propio boletín ("Núm. Exp. 567/9678").
function getAnios() {
  const hoy = new Date().getFullYear();
  return [...getResumen().anios].filter(a => a >= 1900 && a <= hoy).sort((a, b) => b - a);
}

// No hay tabla de juzgados ni un id propio — el nombre completo (tal cual
// aparece en el boletín) es la única identidad. Para /juzgado/:slug hay que
// resolver el slug contra los nombres reales.
function findJuzgadoBySlug(slug) {
  return getResumen().slugs.get(slug) || null;
}

const COLS_AVISO = `e.id, e.boletin_id, e.actora, e.demandada, e.tipo_juicio, e.tipo_juicio_norm,
             e.expediente, e.juzgado, e.sala, e.pagina, b.fecha, b.fecha_iso, b.pdf_url`;

// Tramos de ids en los que el orden de los ids es el de las fechas, del más
// nuevo al más viejo. Los ids no son cronológicos (las corridas del scraper
// se cargan de la más nueva a la más vieja, ver build_db.py), pero los avisos
// de cada boletín son contiguos y, dentro de una corrida, crecen con la fecha.
function getTramos() {
  const r = getResumen();
  if (r.tramos) return r.tramos;
  const tramos = [];
  for (const iso of [...r.fechas].reverse()) {
    for (const b of [...r.boletinesPorFecha.get(iso)].reverse()) {
      if (b.desde == null) continue;
      const t = tramos[tramos.length - 1];
      if (t && b.hasta === t.lo - 1) t.lo = b.desde;
      else tramos.push({ lo: b.desde, hi: b.hasta });
    }
  }
  r.tramos = tramos;
  return tramos;
}

// Avisos que cumplen `where`, del más reciente al más viejo, hasta `limite`.
// Si hay pocos (menos de CANDIDATOS) se traen todos y se ordenan acá; si son
// muchos (un apellido común, un tipo de juicio) se recorren los tramos del
// más nuevo al más viejo con ORDER BY id DESC, que no ordena todo.
// `colId`: la columna por la que se acota el rango (f.rowid si hay FTS, así
// el índice FTS recibe el rango).
const CANDIDATOS = 5000;
// CROSS JOIN fija el orden: primero el índice FTS (con el rango de ids) y
// después la tabla. Con JOIN, SQLite a veces arranca por un índice de la
// tabla (tipo de juicio) y consulta el FTS fila por fila: 5 s en vez de 50 ms.
const FROM_FTS = 'entradas_fts f CROSS JOIN entradas e ON e.id = f.rowid';
function avisosRecientes({ from, where, params, limite, colId = 'e.id' }) {
  const d = getDb();
  const base = `SELECT ${COLS_AVISO} FROM ${from} LEFT JOIN boletines b ON e.boletin_id = b.id WHERE ${where}`;
  const candidatos = d.prepare(`${base} LIMIT ${CANDIDATOS}`).all(...params);
  const porFecha = (a, b) => String(b.fecha_iso || '').localeCompare(String(a.fecha_iso || '')) || b.id - a.id;
  if (candidatos.length < CANDIDATOS) {
    return { rows: candidatos.sort(porFecha).slice(0, limite), total: candidatos.length };
  }
  const stmt = d.prepare(`${base} AND ${colId} BETWEEN ? AND ? ORDER BY ${colId} DESC LIMIT ?`);
  const rows = [];
  for (const t of getTramos()) {
    rows.push(...stmt.all(...params, t.lo, t.hi, limite - rows.length));
    if (rows.length >= limite) break;
  }
  return { rows: rows.sort(porFecha), total: null };
}

// Consulta de avisos por nombre, expediente o filtros: { from, where,
// params, colId } para avisosRecientes / paginaExpedientes, o null si no hay
// nada que buscar.
function consultaAvisos({ q, tipo, anio, juzgado }) {
  const filtros = [];
  const params = [];
  if (tipo) { filtros.push('e.tipo_juicio_norm = ?'); params.push(tipo); }
  if (anio) { filtros.push('e.anio = ?'); params.push(Number(anio)); }
  if (juzgado) { filtros.push('e.juzgado = ?'); params.push(juzgado); }

  // Número de expediente en cualquier formato (ver busqueda.parsearExpediente):
  // no está en el índice FTS, se busca por prefijo en la columna. GLOB y no
  // LIKE: es sensible a mayúsculas y así SQLite puede usar idx_expediente_num.
  // Si además hay palabras ("garcia 184/2025"), se buscan en el FTS.
  const { expediente, palabras } = busqueda.parsearConsulta(q);
  if (expediente) {
    filtros.unshift('e.expediente GLOB ?');
    params.unshift(`${expediente}*`);
    return {
      from: 'entradas e', where: filtros.join(' AND '), params, expediente, palabras,
      filtro: busqueda.filtroPalabras(palabras),
    };
  }
  if (palabras.length) {
    return {
      from: FROM_FTS, colId: 'f.rowid', expediente, palabras,
      where: ['entradas_fts MATCH ?', ...filtros].join(' AND '),
      params: [busqueda.consultaFts(palabras), ...params],
      filtro: busqueda.filtroMismoLado(palabras),
    };
  }
  if (filtros.length) return { from: 'entradas e', where: filtros.join(' AND '), params, expediente, palabras };
  return null;
}

// Avisos del más reciente al más viejo, hasta MAX_ROWS; `total` es null si
// hay más de CANDIDATOS.
function searchEntradas(filtros) {
  const c = consultaAvisos(filtros);
  return c ? avisosRecientes({ ...c, limite: MAX_ROWS }) : { rows: [], total: 0 };
}

// Página de expedientes (agrupados como groupByExpediente), del que tiene el
// aviso más reciente al más viejo. Trae solo los avisos que hacen falta:
// recorre los tramos del más nuevo al más viejo en lotes, con un cursor de
// id dentro de cada tramo, hasta juntar los expedientes de la página y uno
// más (para saber si hay otra). Con pocos resultados (menos de CANDIDATOS)
// se traen todos y los totales son exactos. `filtro`: condición extra sobre
// cada aviso (la ficha de persona exige el nombre exacto).
// Devuelve { grupos, pagina, hayMas, totalExpedientes, totalAvisos }; los
// totales son null cuando no se conocen. Cada grupo lleva solo los avisos
// recorridos: en un expediente largo pueden faltar los más viejos.
// (EXPEDIENTES_POR_PAGINA, 50, está con las empresas.)
const LOTE = 1000;
const MAX_RECORRIDOS = 200000;

function paginaExpedientes({ from, where, params, colId = 'e.id', pagina, filtro = () => true, maxCandidatos = CANDIDATOS }) {
  const d = getDb();
  const p = Math.max(1, parseInt(pagina, 10) || 1);
  const desde = (p - 1) * EXPEDIENTES_POR_PAGINA;
  const base = `SELECT ${COLS_AVISO} FROM ${from} LEFT JOIN boletines b ON e.boletin_id = b.id WHERE ${where}`;
  const porFecha = (a, b) => String(b.fecha_iso || '').localeCompare(String(a.fecha_iso || '')) || b.id - a.id;

  const candidatos = d.prepare(`${base} LIMIT ${maxCandidatos}`).all(...params);
  if (candidatos.length < maxCandidatos) {
    const rows = candidatos.filter(filtro).sort(porFecha);
    const grupos = groupByExpediente(rows);
    return {
      grupos: grupos.slice(desde, desde + EXPEDIENTES_POR_PAGINA), pagina: p,
      hayMas: grupos.length > desde + EXPEDIENTES_POR_PAGINA,
      totalExpedientes: grupos.length, totalAvisos: rows.length,
      todos: rows, // todos los avisos (pocos): para resumir por año y tipo
    };
  }

  const stmt = d.prepare(`${base} AND ${colId} BETWEEN ? AND ? ORDER BY ${colId} DESC LIMIT ${LOTE}`);
  const necesarios = desde + EXPEDIENTES_POR_PAGINA + 1;
  const vistos = [];
  const claves = new Set();
  let recorridos = 0;
  recorrer: for (const t of getTramos()) {
    let hi = t.hi;
    while (hi >= t.lo) {
      const lote = stmt.all(...params, t.lo, hi);
      recorridos += lote.length;
      for (const r of lote) {
        if (!filtro(r)) continue;
        claves.add(r.expediente ? `${r.juzgado || ''}|${r.sala || ''}|${r.expediente}` : `id${r.id}`);
        vistos.push(r);
      }
      if (claves.size >= necesarios || recorridos >= MAX_RECORRIDOS) break recorrer;
      if (lote.length < LOTE) break;
      hi = lote[lote.length - 1].id - 1;
    }
  }
  const grupos = groupByExpediente(vistos);
  return {
    grupos: grupos.slice(desde, desde + EXPEDIENTES_POR_PAGINA), pagina: p,
    hayMas: grupos.length > desde + EXPEDIENTES_POR_PAGINA,
    totalExpedientes: null, totalAvisos: null,
  };
}

// Total exacto de avisos cuando contarlo es barato: una sola palabra (se
// cuenta en el índice FTS, 0.1 s para "garcia", 2.7M avisos) o un solo filtro
// sin texto. Con dos palabras o más el total depende del filtro de "mismo
// lado", que hay que aplicar aviso por aviso; con combinaciones de texto y
// filtros hay que cruzar cada coincidencia con la tabla.
function contarAvisos(c, { tipo, anio, juzgado }) {
  const filtros = [tipo, anio, juzgado, c.expediente].filter(Boolean).length;
  if (c.palabras.length === 1 && filtros === 0) {
    return getDb().prepare('SELECT COUNT(*) AS n FROM entradas_fts WHERE entradas_fts MATCH ?')
      .get(busqueda.consultaFts(c.palabras)).n;
  }
  if (c.palabras.length === 0 && filtros === 1) {
    return getDb().prepare(`SELECT COUNT(*) AS n FROM ${c.from} WHERE ${c.where}`).get(...c.params).n;
  }
  return null;
}

// Búsqueda de /buscar y /juzgado: página de expedientes más los datos para
// ayudar a acotar. Si con dos palabras o más ningún aviso las tiene todas del
// mismo lado del juicio, se muestran los que las tienen en cualquier parte
// (relajado: true).
function buscarExpedientes(filtros, pagina) {
  const c = consultaAvisos(filtros);
  if (!c) return { grupos: [], pagina: 1, hayMas: false, totalExpedientes: 0, totalAvisos: 0 };
  let res = paginaExpedientes({ ...c, pagina, filtro: c.filtro || undefined });
  if (c.filtro && !c.expediente && !res.grupos.length && res.pagina === 1) {
    res = paginaExpedientes({ ...c, pagina, filtro: undefined });
    res.relajado = true;
  }
  if (res.totalAvisos == null) res.totalAvisos = contarAvisos(c, filtros);
  res.palabras = c.palabras;
  res.expediente = c.expediente;
  // El mismo número de expediente existe en muchos juzgados: se listan los
  // que aparecen, para elegir uno (solo si se trajeron todos los avisos).
  if (c.expediente && res.todos) {
    const porJuzgado = new Map();
    for (const r of res.todos) if (r.juzgado) porJuzgado.set(r.juzgado, (porJuzgado.get(r.juzgado) || 0) + 1);
    res.juzgados = [...porJuzgado.entries()].sort((a, b) => b[1] - a[1])
      .map(([nombre, n]) => ({ nombre, slug: slugify(nombre), n }));
  }
  delete res.todos;
  return res;
}

// Personas y empresas cuyo nombre tiene todas las palabras buscadas (la
// última como prefijo): se muestran antes de los avisos, con link a la
// ficha. Primero las de nombre idéntico a lo buscado, después las de más
// avisos.
const MAX_COINCIDENCIAS = 5;
function coincidenciasFichas(q) {
  const { expediente, palabras } = busqueda.parsearConsulta(q);
  if (expediente || !palabras.length) return { empresas: [], personas: [] };
  const fts = palabras.map(w => `${w}*`).join(' ');
  const exacto = palabras.join(' ');
  const buscar = (tabla, cols) => hayTabla(tabla) ? getDb().prepare(`
    SELECT ${cols} FROM ${tabla}
    WHERE id IN (SELECT rowid FROM ${tabla}_fts WHERE ${tabla}_fts MATCH ?)
    ORDER BY (nucleo = ?) DESC, avisos DESC LIMIT ${MAX_COINCIDENCIAS}
  `).all(fts, exacto) : [];
  return {
    empresas: buscar('empresas', 'nombre, slug, avisos, expedientes'),
    personas: buscar('personas', 'nombre, slug, avisos'),
  };
}

// Un mismo expediente acumula un aviso por cada acuerdo publicado. El
// número de expediente se reinicia por juzgado, así que la clave de
// agrupación es (juzgado o sala, expediente), no solo el expediente.
// (Misma lógica que groupByExpediente en el search.js del sitio estático.)
function groupByExpediente(rows) {
  const groups = new Map();
  let noKeyIdx = 0;

  for (const r of rows) {
    const key = r.expediente
      ? `${r.juzgado || ''}|${r.sala || ''}|${r.expediente}`
      : `__sin_exp_${noKeyIdx++}`;

    if (!groups.has(key)) groups.set(key, { avisos: [], ...r });
    const g = groups.get(key);
    g.avisos.push(r);
    if (r.boletin_id > g.boletin_id) Object.assign(g, r);
  }

  return Array.from(groups.values());
}

function getExpedienteById(id) {
  const row = getDb().prepare(`
    SELECT e.id, e.boletin_id, e.actora, e.demandada, e.tipo_juicio, e.expediente,
           e.tipo_juicio_norm, e.juzgado, e.sala, e.secretaria, e.anio, e.pagina,
           b.fecha, b.fecha_iso, b.pdf_url
    FROM entradas e
    LEFT JOIN boletines b ON e.boletin_id = b.id
    WHERE e.id = ?
  `).get(id);
  if (!row) return null;

  const avisos = getDb().prepare(`
    SELECT e.id, e.boletin_id, e.tipo_juicio, e.num_acdos, e.pagina, b.fecha, b.fecha_iso, b.pdf_url
    FROM entradas e
    LEFT JOIN boletines b ON e.boletin_id = b.id
    WHERE e.juzgado IS ? AND e.sala IS ? AND e.expediente = ?
    ORDER BY e.boletin_id DESC
  `).all(row.juzgado, row.sala, row.expediente);

  const { porTipo, porJuzgado } = getResumen();
  const mismoTipo = (row.tipo_juicio_norm && porTipo.get(row.tipo_juicio_norm)) || 0;
  const mismoJuzgado = (row.juzgado && porJuzgado.get(row.juzgado)) || 0;

  return { ...row, avisos, mismoTipo, mismoJuzgado };
}

// Validada con la guía de dataviz sobre el crema de Bandatos (#f6f2eb):
// separación para daltonismo, banda de luminosidad y contraste >= 3:1. El
// orden importa: verde y rojo no van juntos. "Otro" es un gris cálido.
const CHART_COLORS = ['#3d7a3a', '#7b4b94', '#a8730f', '#2f6db5', '#b8392f', '#b5ad9f'];

function getYearChart() {
  const { porAnioTipo } = getResumen();
  const top5 = getTipos().slice(0, 5);
  const enTop = new Set(top5);

  const buckets = [...top5, 'Otro'];
  const byAnio = new Map();
  for (const { anio, tipo, n } of porAnioTipo) {
    if (!anio) continue;
    const bucket = enTop.has(tipo) ? tipo : 'Otro';
    if (!byAnio.has(anio)) byAnio.set(anio, {});
    byAnio.get(anio)[bucket] = (byAnio.get(anio)[bucket] || 0) + n;
  }

  const currentYear = new Date().getFullYear();
  const anios = Array.from(byAnio.keys())
    .filter(a => a <= currentYear)
    .sort((a, b) => a - b)
    .slice(-CHART_YEARS);

  const years = anios.map(anio => {
    const datos = byAnio.get(anio);
    const total = buckets.reduce((s, b) => s + (datos[b] || 0), 0);
    return {
      anio,
      total,
      segments: buckets.map(b => ({ bucket: b, n: datos[b] || 0 })),
    };
  });

  const maxTotal = Math.max(...years.map(y => y.total), 1);
  return { buckets, years, maxTotal, colors: CHART_COLORS };
}

// Avisos por año de PUBLICACIÓN (getYearChart agrupa por año del
// expediente). El total por año sale de boletines.n_entradas; el desglose
// por tipo, de contar en idx_tipo_juicio_norm dentro de los rangos de ids de
// cada año (los avisos de un boletín son contiguos): ~1 s con la base
// completa, sin recorrer la tabla. "Otro" es el resto.
function getPublicacionChart() {
  const r = getResumen();
  if (r.publicacionChart) return r.publicacionChart;
  const d = getDb();
  const top5 = getTipos().slice(0, 5);
  const buckets = [...top5, 'Otro'];
  const rangos = [];
  const totales = new Map();
  for (const b of d.prepare(`
    SELECT entrada_desde AS lo, entrada_hasta AS hi, n_entradas AS n, CAST(substr(fecha_iso, 1, 4) AS INTEGER) AS anio
    FROM boletines WHERE n_entradas > 0 AND entrada_desde IS NOT NULL AND fecha_iso IS NOT NULL
    ORDER BY entrada_desde
  `).all()) {
    totales.set(b.anio, (totales.get(b.anio) || 0) + b.n);
    const t = rangos[rangos.length - 1];
    if (t && t.anio === b.anio && b.lo === t.hi + 1) t.hi = b.hi;
    else rangos.push({ lo: b.lo, hi: b.hi, anio: b.anio });
  }
  const contar = d.prepare('SELECT COUNT(*) AS n FROM entradas WHERE tipo_juicio_norm = ? AND id BETWEEN ? AND ?');
  const porAnio = new Map([...totales.keys()].map(a => [a, {}]));
  for (const tipo of top5) {
    for (const g of rangos) porAnio.get(g.anio)[tipo] = (porAnio.get(g.anio)[tipo] || 0) + contar.get(tipo, g.lo, g.hi).n;
  }
  const years = [...totales.keys()].sort((a, b) => a - b).slice(-CHART_YEARS).map(anio => {
    const datos = porAnio.get(anio);
    const total = totales.get(anio);
    const otro = total - top5.reduce((s, t) => s + (datos[t] || 0), 0);
    return {
      anio, total,
      segments: buckets.map(b => ({ bucket: b, n: b === 'Otro' ? Math.max(otro, 0) : datos[b] || 0 })),
    };
  });
  r.publicacionChart = {
    buckets, years, colors: CHART_COLORS,
    maxTotal: Math.max(...years.map(y => y.total), 1),
  };
  return r.publicacionChart;
}

function normTxt(str) {
  return String(str || '').normalize('NFD').replace(/\p{M}/gu, '').toLowerCase();
}

// Todo lo publicado en los boletines de un día, agrupado por juzgado o sala
// en el orden del PDF. Las entradas de un boletín son contiguas (ver
// populate_boletin_ranges en build_db.py): se traen por rango de id, sin
// índice por boletin_id. `q` filtra por partes, expediente o tipo.
function getDia(iso, { q } = {}) {
  const boletines = getResumen().boletinesPorFecha.get(iso) || [];
  const stmt = getDb().prepare(`
    SELECT id, juzgado, sala, secretaria, actora, demandada, tipo_juicio, expediente,
           num_acdos, pagina
    FROM entradas WHERE id BETWEEN ? AND ? AND boletin_id = ? ORDER BY id
  `);
  const filtro = normTxt((q || '').trim());
  const grupos = new Map();
  let total = 0;
  for (const b of boletines) {
    for (const r of stmt.all(b.desde, b.hasta, b.id)) {
      total++;
      if (filtro && !normTxt(`${r.actora} ${r.demandada} ${r.expediente} ${r.tipo_juicio}`).includes(filtro)) continue;
      const nombre = r.sala || r.juzgado || 'Sin juzgado identificado';
      if (!grupos.has(nombre)) grupos.set(nombre, { nombre, slug: slugify(nombre), rows: [] });
      grupos.get(nombre).rows.push({ ...r, pdf_url: b.pdf_url, fecha_iso: iso });
    }
  }
  return { iso, boletines, grupos: [...grupos.values()], total };
}

function getFechas() {
  const { boletinesPorFecha, fechas } = getResumen();
  return { boletinesPorFecha, fechas };
}

// ── Empresas ──────────────────────────────────────────────────────────────
// Tablas armadas por build_db.py --empresas. Estas páginas se indexan y NO
// muestran nombres de personas: nada acá lee entradas.actora/demandada. La
// contraparte sale de entradas_empresas (otras empresas del aviso) y de
// entradas_partes (solo el indicador de que del otro lado hay personas).

const EMPRESAS_POR_PAGINA = 50;
const EXPEDIENTES_POR_PAGINA = 50;

function hayTabla(nombre) {
  return Boolean(getDb().prepare(
    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?"
  ).get(nombre));
}

function hayEmpresas() {
  return hayTabla('empresas');
}

// build_db.py --agregados-empresas precalcula tipos, años y expedientes por
// empresa. Sin eso, agregarlos en vivo para un banco con cientos de miles
// de avisos tarda ~35 s; se mantiene como respaldo para bases viejas.
function hayAgregados() {
  return hayTabla('empresas_expedientes');
}

function ftsPrefijo(q) {
  const palabras = String(q || '').replace(/[^\p{L}\p{N}]+/gu, ' ').trim().split(/\s+/).filter(Boolean);
  return palabras.length ? palabras.map(p => `${p}*`).join(' ') : null;
}

function getEmpresas({ q, orden, pagina } = {}) {
  const d = getDb();
  const fts = ftsPrefijo(q);
  const where = fts ? 'WHERE e.id IN (SELECT rowid FROM empresas_fts WHERE empresas_fts MATCH ?)' : '';
  const params = fts ? [fts] : [];
  const orderBy = orden === 'nombre' ? 'e.nombre COLLATE NOCASE' : 'e.avisos DESC, e.nombre COLLATE NOCASE';
  const p = Math.max(1, parseInt(pagina, 10) || 1);
  const total = d.prepare(`SELECT COUNT(*) AS n FROM empresas e ${where}`).get(...params).n;
  const rows = d.prepare(`
    SELECT e.nombre, e.slug, e.avisos, e.como_actora, e.como_demandada, e.expedientes,
           e.primer_boletin, e.ultimo_boletin
    FROM empresas e ${where}
    ORDER BY ${orderBy}
    LIMIT ${EMPRESAS_POR_PAGINA} OFFSET ${(p - 1) * EMPRESAS_POR_PAGINA}
  `).all(...params);
  return { rows, total, pagina: p, paginas: Math.max(1, Math.ceil(total / EMPRESAS_POR_PAGINA)) };
}

function getEmpresa(slug) {
  const d = getDb();
  const e = d.prepare('SELECT * FROM empresas WHERE slug = ?').get(slug);
  if (!e) return null;
  if (hayAgregados()) {
    const tipos = d.prepare(
      'SELECT tipo, n FROM empresas_tipos WHERE empresa_id = ? ORDER BY n DESC LIMIT 10'
    ).all(e.id);
    const porAnio = d.prepare(
      'SELECT anio, n FROM empresas_anios WHERE empresa_id = ? ORDER BY anio'
    ).all(e.id);
    return { ...e, tipos, porAnio, maxAnio: Math.max(1, ...porAnio.map(r => r.n)) };
  }
  const tipos = d.prepare(`
    SELECT COALESCE(en.tipo_juicio_norm, 'Sin tipo reconocido') AS tipo, COUNT(*) AS n
    FROM entradas_empresas ee JOIN entradas en ON en.id = ee.entrada_id
    WHERE ee.empresa_id = ? GROUP BY 1 ORDER BY n DESC LIMIT 10
  `).all(e.id);
  const porAnio = d.prepare(`
    SELECT substr(b.fecha_iso, 1, 4) AS anio, COUNT(*) AS n
    FROM entradas_empresas ee
    JOIN entradas en ON en.id = ee.entrada_id
    JOIN boletines b ON b.id = en.boletin_id
    WHERE ee.empresa_id = ? GROUP BY 1 ORDER BY 1
  `).all(e.id);
  return { ...e, tipos, porAnio, maxAnio: Math.max(1, ...porAnio.map(r => r.n)) };
}

// Expedientes de una empresa, del más reciente al más viejo, con la otra
// parte expresada solo como empresas + "persona(s) física(s)".
function getExpedientesEmpresa(empresa, pagina) {
  const d = getDb();
  const p = Math.max(1, parseInt(pagina, 10) || 1);
  const grupos = hayAgregados() ? d.prepare(`
    SELECT organo, expediente, rol, avisos, ultima_fecha, ultimo_id FROM empresas_expedientes
    WHERE empresa_id = ? ORDER BY ultima_fecha DESC, ultimo_id DESC
    LIMIT ${EXPEDIENTES_POR_PAGINA} OFFSET ${(p - 1) * EXPEDIENTES_POR_PAGINA}
  `).all(empresa.id) : d.prepare(`
    SELECT COALESCE(en.sala, en.juzgado) AS organo, en.expediente, ee.rol,
           COUNT(*) AS avisos, MAX(b.fecha_iso) AS ultima_fecha, en.id AS ultimo_id
    FROM entradas_empresas ee
    JOIN entradas en ON en.id = ee.entrada_id
    JOIN boletines b ON b.id = en.boletin_id
    WHERE ee.empresa_id = ?
    GROUP BY organo, en.expediente, ee.rol
    ORDER BY ultima_fecha DESC, ultimo_id DESC
    LIMIT ${EXPEDIENTES_POR_PAGINA} OFFSET ${(p - 1) * EXPEDIENTES_POR_PAGINA}
  `).all(empresa.id);
  if (!grupos.length) return { rows: [], pagina: p, paginas: 1 };

  const ids = grupos.map(g => g.ultimo_id);
  const marcas = ids.map(() => '?').join(',');
  const detalle = new Map(d.prepare(`
    SELECT en.id, en.tipo_juicio, en.pagina, b.fecha_iso, b.pdf_url
    FROM entradas en JOIN boletines b ON b.id = en.boletin_id WHERE en.id IN (${marcas})
  `).all(...ids).map(r => [r.id, r]));
  const otrasEmpresas = new Map();
  for (const r of d.prepare(`
    SELECT ee.entrada_id, ee.rol, e.nombre, e.slug
    FROM entradas_empresas ee JOIN empresas e ON e.id = ee.empresa_id
    WHERE ee.entrada_id IN (${marcas}) AND ee.empresa_id != ?
  `).all(...ids, empresa.id)) {
    if (!otrasEmpresas.has(r.entrada_id)) otrasEmpresas.set(r.entrada_id, []);
    otrasEmpresas.get(r.entrada_id).push(r);
  }
  const personas = new Map(d.prepare(
    `SELECT entrada_id, otras_actora, otras_demandada FROM entradas_partes WHERE entrada_id IN (${marcas})`
  ).all(...ids).map(r => [r.entrada_id, r]));

  const rows = grupos.map(g => {
    const otroRol = g.rol === 'actora' ? 'demandada' : 'actora';
    const contra = (otrasEmpresas.get(g.ultimo_id) || []).filter(r => r.rol === otroRol);
    const pers = personas.get(g.ultimo_id);
    return {
      ...g,
      ...detalle.get(g.ultimo_id),
      contraEmpresas: contra,
      contraPersonas: Boolean(pers && pers[`otras_${otroRol}`]),
    };
  });
  return {
    rows, pagina: p,
    paginas: Math.max(1, Math.ceil((empresa.expedientes || rows.length) / EXPEDIENTES_POR_PAGINA)),
  };
}

// Cifras del listado de empresas (portada). Recorre las ~150k empresas una
// vez por apertura de la DB.
let resumenEmpresas = null;
function getResumenEmpresas() {
  if (!resumenEmpresas) {
    resumenEmpresas = getDb().prepare(`
      SELECT COUNT(*) AS empresas, SUM(avisos) AS avisos, SUM(expedientes) AS expedientes,
             MIN(primer_boletin) AS desde, MAX(ultimo_boletin) AS hasta
      FROM empresas
    `).get();
  }
  return resumenEmpresas;
}

// Empresas que son parte en un aviso (para linkear desde la ficha).
function getEmpresasDeEntradas(ids) {
  if (!ids.length || !hayEmpresas()) return [];
  return getDb().prepare(`
    SELECT DISTINCT e.nombre, e.slug FROM entradas_empresas ee JOIN empresas e ON e.id = ee.empresa_id
    WHERE ee.entrada_id IN (${ids.map(() => '?').join(',')})
  `).all(...ids);
}

// ── Portada ───────────────────────────────────────────────────────────────
// Tablas de la portada (indexable, sin nombres de personas): empresas con
// más avisos y expedientes, últimos boletines, días con más avisos,
// juzgados y tipos de juicio. Se arman una vez por apertura de la DB.
const TOP_PORTADA = 10;
let portada = null;

function getPortada() {
  if (portada) return portada;
  const { boletinesPorFecha, fechas, porJuzgado, porTipo } = getResumen();
  const dias = fechas.map(iso => ({
    iso,
    avisos: boletinesPorFecha.get(iso).reduce((s, b) => s + b.n, 0),
  }));
  const top = (mapa, n) => [...mapa.entries()].sort((a, b) => b[1] - a[1]).slice(0, n);
  let empresas = null;
  if (hayEmpresas()) {
    const cols = 'nombre, slug, avisos, expedientes, primer_boletin, ultimo_boletin';
    empresas = {
      porAvisos: getDb().prepare(`SELECT ${cols} FROM empresas ORDER BY avisos DESC LIMIT ${TOP_PORTADA}`).all(),
      porExpedientes: getDb().prepare(
        `SELECT ${cols} FROM empresas ORDER BY expedientes DESC LIMIT ${TOP_PORTADA}`
      ).all(),
    };
  }
  portada = {
    empresas,
    ultimosDias: dias.slice(-TOP_PORTADA).reverse(),
    diasConMasAvisos: [...dias].sort((a, b) => b.avisos - a.avisos).slice(0, TOP_PORTADA),
    juzgados: top(porJuzgado, TOP_PORTADA).map(([nombre, avisos]) => ({ nombre, slug: slugify(nombre), avisos })),
    tipos: top(porTipo, TOP_PORTADA).map(([tipo, avisos]) => ({ tipo, avisos })),
  };
  return portada;
}

// ── Personas ──────────────────────────────────────────────────────────────
// Tabla armada por build_db.py --personas (scraper/personas.py). Estas
// páginas llevan noindex: nombran personas en juicios, muchos familiares.
// No hay tabla de unión aviso-persona: los avisos de una persona se buscan
// en vivo en entradas_fts y se filtran por el nombre exacto.

const PERSONAS_POR_PAGINA = 50;

function hayPersonas() {
  return hayTabla('personas');
}

function getPersonas({ q, pagina } = {}) {
  const d = getDb();
  const fts = ftsPrefijo(q);
  const where = fts ? 'WHERE p.id IN (SELECT rowid FROM personas_fts WHERE personas_fts MATCH ?)' : '';
  const params = fts ? [fts] : [];
  const p = Math.max(1, parseInt(pagina, 10) || 1);
  // Sin búsqueda, el total sale del resumen (contar ~1M filas en cada visita no hace falta).
  const total = fts
    ? d.prepare(`SELECT COUNT(*) AS n FROM personas p ${where}`).get(...params).n
    : getResumenPersonas().personas;
  const rows = d.prepare(`
    SELECT p.nombre, p.slug, p.avisos, p.como_actora, p.como_demandada, p.primer_boletin, p.ultimo_boletin
    FROM personas p ${where}
    ORDER BY p.avisos DESC, p.nombre COLLATE NOCASE
    LIMIT ${PERSONAS_POR_PAGINA} OFFSET ${(p - 1) * PERSONAS_POR_PAGINA}
  `).all(...params);
  return { rows, total, pagina: p, paginas: Math.max(1, Math.ceil(total / PERSONAS_POR_PAGINA)) };
}

let resumenPersonas = null;
function getResumenPersonas() {
  if (!resumenPersonas) resumenPersonas = getDb().prepare('SELECT COUNT(*) AS personas FROM personas').get();
  return resumenPersonas;
}

function getPersona(slug) {
  return getDb().prepare('SELECT * FROM personas WHERE slug = ?').get(slug) || null;
}

// Misma normalización que personas.normalizar() en Python.
function normNombre(str) {
  return ` ${normTxt(str).replace(/[^a-z0-9ñ ]+/g, ' ').replace(/\s+/g, ' ').trim()} `;
}

// Expedientes donde la persona es actora o demandada, de a 50 (ver
// paginaExpedientes). Las palabras cortas y las partículas ("de", "la")
// quedan fuera del MATCH (con detail=none sus listas son enormes) y se
// verifican en el filtro, que además exige el nombre exacto.
const MAX_CANDIDATOS_PERSONA = 200000;
function getExpedientesPersona(persona, pagina) {
  const palabras = persona.nucleo.split(' ').filter(w => w.length >= 3 && !['del', 'las', 'los'].includes(w));
  if (!palabras.length) return { grupos: [], pagina: 1, hayMas: false, totalExpedientes: 0, totalAvisos: 0 };
  const buscado = ` ${persona.nucleo} `;
  // Se traen todas las coincidencias del índice (las de la persona y las de
  // otros nombres con las mismas palabras) para contar expedientes exactos y
  // resumir por año y tipo. Tope: varias veces sus avisos, hasta
  // MAX_CANDIDATOS_PERSONA; si se pasa, se pagina sin totales.
  const res = paginaExpedientes({
    from: FROM_FTS, colId: 'f.rowid', where: 'entradas_fts MATCH ?', params: [palabras.join(' ')],
    pagina, filtro: r => normNombre(r.actora).includes(buscado) || normNombre(r.demandada).includes(buscado),
    maxCandidatos: Math.min(MAX_CANDIDATOS_PERSONA, Math.max(CANDIDATOS, (persona.avisos || 0) * 5)),
  });
  for (const g of res.grupos) g.rol = normNombre(g.actora).includes(buscado) ? 'actora' : 'demandada';
  // Con todos los avisos a mano, las cifras de la ficha salen de ellos (así
  // cierran con los expedientes listados) y no de la tabla personas.
  if (res.todos) {
    res.conteos = {
      avisos: res.todos.length,
      como_actora: res.todos.filter(r => normNombre(r.actora).includes(buscado)).length,
      como_demandada: res.todos.filter(r => normNombre(r.demandada).includes(buscado)).length,
    };
  }
  return res;
}

function getStats() {
  // fecha_iso y no fecha: fecha es el texto crudo del portal ("Consulta del
  // Boletin Judicial de fecha 22-sep.-2026") y su MIN/MAX es alfabético.
  const { minFecha, maxFecha, boletines } = getDb().prepare(`
    SELECT MIN(fecha_iso) AS minFecha, MAX(fecha_iso) AS maxFecha, COUNT(*) AS boletines
    FROM boletines WHERE n_entradas > 0
  `).get();
  const { total, porTipo } = getResumen();
  const conTipo = [...porTipo.values()].reduce((s, n) => s + n, 0);
  return {
    totalEntradas: total, minFecha, maxFecha, boletines,
    pctConTipo: total ? Math.round((conTipo / total) * 1000) / 10 : 0,
    empresas: hayEmpresas() ? getResumenEmpresas() : null,
    personas: hayPersonas() ? getResumenPersonas() : null,
  };
}

// Para el healthcheck del contenedor: la base abre y responde.
function ping() {
  return getDb().prepare('SELECT 1 AS ok').get().ok === 1;
}

module.exports = {
  ping, getGeneracion, getTipos, getAnios, findJuzgadoBySlug, searchEntradas, groupByExpediente, getExpedienteById,
  getYearChart, getPublicacionChart, getStats, getResumen, getDia, getFechas, reloadDb,
  coincidenciasFichas, getPortada, hayPersonas, getPersonas, getPersona, getResumenPersonas, getExpedientesPersona, buscarExpedientes, EXPEDIENTES_POR_PAGINA, PERSONAS_POR_PAGINA,
  hayEmpresas, getEmpresas, getEmpresa, getExpedientesEmpresa, getEmpresasDeEntradas, getResumenEmpresas, MAX_RESULTS, MAX_ROWS, CANDIDATOS, CHART_COLORS, DB_PATH,
};
