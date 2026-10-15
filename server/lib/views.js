// views.js — handlers de página + render de templates pug con pugLocals
// compartidos, al estilo de sociedad-web-front/lib/views.js (renderCachedTemplate
// + un objeto de locals que se mezcla en cada render).

const path = require('path');
const pug = require('pug');
const db = require('./db');
const { expedienteHref, juzgadoHref } = require('./slug');
const { getBoletinText, findExcerpt } = require('./raw-text');
const fechas = require('./fechas');

const TEMPLATES_DIR = path.join(__dirname, '..', 'templates');
const isDev = process.env.NODE_ENV !== 'production';

const compiledCache = new Map();

function compile(name) {
  const file = path.join(TEMPLATES_DIR, `${name}.pug`);
  if (isDev) return pug.compileFile(file, { cache: false });
  if (!compiledCache.has(name)) {
    compiledCache.set(name, pug.compileFile(file, { cache: true }));
  }
  return compiledCache.get(name);
}

// num(): formatea números con separador de miles (es-MX), mismo rol que el
// helper num() de pugLocals en sociedad-web-front.
const numberFormat = new Intl.NumberFormat('es-MX');
function num(n) {
  return numberFormat.format(n);
}

// Assets con la versión en la URL: un hash del contenido del archivo. Así se
// pueden servir con cache "immutable" de un año (static-server.js) y el
// navegador baja de nuevo solo lo que cambió. En dev se recalcula si cambia
// la fecha del archivo.
const fs = require('fs');
const crypto = require('crypto');
const STATIC_DIR = path.join(__dirname, '..', 'static');
const versiones = new Map();
function asset(rel) {
  const archivo = path.join(STATIC_DIR, rel);
  let mtime = 0;
  try { mtime = fs.statSync(archivo).mtimeMs; } catch { return `/static/${rel}`; }
  const v = versiones.get(rel);
  if (v && v.mtime === mtime) return v.url;
  const hash = crypto.createHash('md5').update(fs.readFileSync(archivo)).digest('hex').slice(0, 10);
  const url = `/static/${rel}?v=${hash}`;
  versiones.set(rel, { mtime, url });
  return url;
}

// Todo el CSS va inline en el <head> (headStyle.css + main.css, ~17 KB, ~4 KB
// comprimido): con main.css asíncrono la página se pintaba sin los estilos de
// tarjetas y tablas y después se corría (CLS 0.30); bloqueante costaba un
// viaje más al server. Minificado simple: sin comentarios ni espacios de más.
let cssCache = null;
function cssInline() {
  if (cssCache && !isDev) return cssCache;
  const crudo = ['headStyle.css', 'main.css']
    .map(f => fs.readFileSync(path.join(STATIC_DIR, 'css', f), 'utf8')).join('\n');
  cssCache = crudo
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/\s+/g, ' ')
    .replace(/\s*([{}:;,>])\s*/g, '$1')
    .replace(/;}/g, '}')
    .trim();
  return cssCache;
}

// pugLocals: se mezcla en TODOS los renders (mismo patrón que sociedad-web-front).
const pugLocals = {
  siteTitle: 'Boletín Judicial CDMX',
  tagline: 'Juicios civiles, mercantiles y familiares en la Ciudad de México, ' +
    'a partir del Boletín Judicial del Poder Judicial de la CDMX.',
  gaId: process.env.GA_ID || '',
  asset,
  CHART_COLORS: db.CHART_COLORS,
  num,
  expedienteHref,
  juzgadoHref,
  fechaCorta: fechas.fechaCorta,
  fechaLarga: fechas.fechaLarga,
  // Link al PDF del boletín en la página del aviso (#page=N lo entienden
  // los visores de Chrome y Firefox).
  pdfHref: r => (r.pagina ? `${r.pdf_url}#page=${r.pagina}` : r.pdf_url),
};

function renderTemplate(name, locals) {
  const fn = compile(name);
  return fn({ ...pugLocals, css: cssInline(), ...locals });
}

function buscar(req, { juzgado, formAction, heading } = {}) {
  const q = (req.query.q || '').trim();
  const tipo = req.query.tipo || '';
  const anio = req.query.anio || '';

  const tipos = db.getTipos();
  const anios = db.getAnios();
  const chart = db.getYearChart();

  const searched = Boolean(q || tipo || anio || juzgado);
  // 50 expedientes por página, del que tiene el aviso más reciente al más viejo.
  const resultado = searched ? db.buscarExpedientes({ q, tipo, anio, juzgado }, req.query.p) : null;
  const params = Object.fromEntries(Object.entries({ q, tipo, anio }).filter(([, v]) => v));

  return renderTemplate('index', {
    req, q, tipo, anio, tipos, anios, chart, searched, resultado,
    groups: resultado ? resultado.grupos : [],
    hrefPagina: n => `${formAction || '/buscar'}?${new URLSearchParams({ ...params, p: n })}`,
    formAction: formAction || '/buscar',
    heading: heading || null,
    candidatos: db.CANDIDATOS, porPagina: db.EXPEDIENTES_POR_PAGINA,
  });
}

function home(req) {
  return { status: 200, html: buscar(req) };
}

// Página propia por juzgado (no un filtro por querystring): mejor para
// compartir/marcar como favorito, y con URL legible.
function juzgado(req, params) {
  const nombre = db.findJuzgadoBySlug(params.slug);
  if (!nombre) {
    return { status: 404, html: renderTemplate('404', { req }) };
  }

  return {
    status: 200,
    html: buscar(req, {
      juzgado: nombre,
      formAction: juzgadoHref(nombre),
      heading: nombre,
    }),
  };
}

// El id real de la causa es el entradas.id de CUALQUIER aviso que le
// pertenezca (el que se haya usado para armar el link) — no un id propio
// de "expediente" (no existe esa entidad en el schema, un expediente es
// solo un (juzgado, sala, expediente) compartido por N filas de entradas).
// El slug es puro adorno para la URL: parseInt() ignora todo lo que sigue
// a los dígitos iniciales, así que cualquier slug (o ninguno) funciona.
function expediente(req, params) {
  const id = parseInt(params.idslug, 10);
  const data = Number.isInteger(id) ? db.getExpedienteById(id) : null;

  if (!data) {
    return { status: 404, html: renderTemplate('404', { req }) };
  }

  // Texto original del aviso más reciente, leído del .txt de pdftotext ya
  // guardado por el scraper (no se duplica en el sqlite).
  const ultimoAviso = data.avisos[0];
  let textoOriginal = null;
  if (ultimoAviso) {
    const fullText = getBoletinText(ultimoAviso.boletin_id);
    textoOriginal = findExcerpt(fullText, { actora: data.actora, expediente: data.expediente });
  }

  const empresasDelCaso = db.getEmpresasDeEntradas(data.avisos.map(a => a.id));

  return {
    status: 200,
    html: renderTemplate('expediente', { req, expediente: data, textoOriginal, empresasDelCaso }),
  };
}

// Navegación por fecha: calendario del mes y todo lo publicado ese día,
// agrupado por juzgado o sala. Sin ?organo ni ?q se listan solo los
// órganos con su cantidad de avisos (un día tiene ~15k: pintarlos todos
// haría una página de varios MB); con ?organo se ven los de ese órgano y
// con ?q los que coinciden en todo el día.
const MAX_AVISOS_FILTRO = 1000;

function fecha(req, params) {
  const { boletinesPorFecha, fechas: dias } = db.getFechas();
  if (!dias.length) return { status: 404, html: renderTemplate('404', { req }) };

  const iso = params.iso || dias[dias.length - 1];
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso)) return { status: 404, html: renderTemplate('404', { req }) };

  // Mes que muestra el calendario: el del día, salvo que se navegue con
  // ?mes=AAAA-MM (flechas) o ?a=AAAA&m=M (desplegables).
  let mesPedido = req.query.mes || '';
  if (/^\d{4}$/.test(req.query.a || '') && /^\d{1,2}$/.test(req.query.m || '')) {
    mesPedido = `${req.query.a}-${String(req.query.m).padStart(2, '0')}`;
  }
  const [a0, m0] = (/^\d{4}-(0[1-9]|1[0-2])$/.test(mesPedido) ? mesPedido : iso.slice(0, 7))
    .split('-').map(Number);
  const primero = dias[0].slice(0, 7);
  const ultimo = dias[dias.length - 1].slice(0, 7);
  const ant = fechas.moverMes(a0, m0, -1);
  const sig = fechas.moverMes(a0, m0, 1);
  const calendario = {
    titulo: fechas.nombreMes(a0, m0),
    anio: a0,
    mes: m0,
    meses: fechas.MESES,
    anios: Array.from({ length: Number(ultimo.slice(0, 4)) - Number(primero.slice(0, 4)) + 1 },
      (_, k) => Number(ultimo.slice(0, 4)) - k),
    semanas: fechas.semanasDelMes(a0, m0).map(sem => sem.map(d => d && {
      iso: d,
      dia: Number(d.slice(8)),
      n: (boletinesPorFecha.get(d) || []).reduce((s, b) => s + b.n, 0),
    })),
    anterior: fechas.mesKey(ant.anio, ant.mes) >= primero ? fechas.mesKey(ant.anio, ant.mes) : null,
    siguiente: fechas.mesKey(sig.anio, sig.mes) <= ultimo ? fechas.mesKey(sig.anio, sig.mes) : null,
  };

  const i = dias.indexOf(iso);
  const q = (req.query.q || '').trim();
  const organo = req.query.organo || '';
  const dia = i === -1 ? null : db.getDia(iso, { q });

  let grupos = [];
  let truncado = false;
  let coincidencias = 0;
  if (dia) {
    grupos = organo ? dia.grupos.filter(g => g.slug === organo) : dia.grupos;
    coincidencias = grupos.reduce((s, g) => s + g.rows.length, 0);
    if (q) {
      let restantes = MAX_AVISOS_FILTRO;
      grupos = grupos.map(g => {
        const rows = g.rows.slice(0, Math.max(restantes, 0));
        restantes -= g.rows.length;
        if (rows.length < g.rows.length) truncado = true;
        return { ...g, total: g.rows.length, rows };
      }).filter(g => g.rows.length);
    }
  }

  return {
    status: 200,
    html: renderTemplate('fecha', {
      req, iso, dia, grupos, q, organo, truncado, calendario, coincidencias,
      maxAvisos: MAX_AVISOS_FILTRO,
      diaAnterior: i > 0 ? dias[i - 1] : (i === -1 ? dias.filter(d => d < iso).pop() : null),
      diaSiguiente: i !== -1 && i < dias.length - 1 ? dias[i + 1] : (i === -1 ? dias.find(d => d > iso) : null),
      mostrarAvisos: Boolean(organo || q),
    }),
  };
}

// /empresas y /empresas/:slug: indexables, sin nombres de personas (ver
// la sección Empresas de lib/db.js).
function empresas(req) {
  if (!db.hayEmpresas()) return { status: 404, html: renderTemplate('404', { req }) };
  const q = (req.query.q || '').trim();
  const orden = req.query.orden === 'nombre' ? 'nombre' : 'avisos';
  const lista = db.getEmpresas({ q, orden, pagina: req.query.p });
  return {
    status: 200,
    html: renderTemplate('empresas', {
      req, q, orden, lista, resumen: db.getResumenEmpresas(),
      personas: db.hayPersonas() ? db.getResumenPersonas().personas : null,
    }),
  };
}

// Portada: buscador general y tablas (empresas, boletines, juzgados, tipos),
// al estilo de la portada de país de sociedad.info. Se indexa: no nombra
// personas.
function portada(req) {
  return {
    status: 200,
    html: renderTemplate('portada', {
      req,
      datos: db.getPortada(),
      resumen: db.hayEmpresas() ? db.getResumenEmpresas() : null,
      personas: db.hayPersonas() ? db.getResumenPersonas().personas : null,
      chart: db.getYearChart(),
      chartPublicacion: db.getPublicacionChart(),
    }),
  };
}

// Ficha de una parte (empresa o persona): mismo template (parte.pug).
// Gráfico de avisos por año de publicación con una sola serie, en el mismo
// formato que db.getYearChart() para usar el mixin graficoAnios.
function chartPorAnio(porAnio) {
  const years = porAnio.map(r => ({
    anio: Number(r.anio), total: r.n, segments: [{ bucket: 'Avisos', n: r.n }],
  }));
  return { buckets: ['Avisos'], colors: [db.CHART_COLORS[0]], years, maxTotal: Math.max(1, ...years.map(y => y.total)) };
}

// Avisos por año de publicación y tipos más frecuentes, a partir de todos
// los avisos de una persona (solo cuando son pocos y se trajeron todos).
function resumirAvisos(rows) {
  const anios = new Map();
  const tipos = new Map();
  for (const r of rows) {
    const a = (r.fecha_iso || '').slice(0, 4);
    if (a) anios.set(a, (anios.get(a) || 0) + 1);
    const t = r.tipo_juicio_norm || 'Sin tipo reconocido';
    tipos.set(t, (tipos.get(t) || 0) + 1);
  }
  return {
    porAnio: [...anios.entries()].sort().map(([anio, n]) => ({ anio, n })),
    tipos: [...tipos.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10).map(([tipo, n]) => ({ tipo, n })),
  };
}

function empresa(req, params) {
  const e = db.hayEmpresas() ? db.getEmpresa(params.slug) : null;
  if (!e) return { status: 404, html: renderTemplate('404', { req }) };
  const exp = db.getExpedientesEmpresa(e, req.query.p);
  return {
    status: 200,
    html: renderTemplate('parte', {
      req, clase: 'empresa', parte: e,
      totalExpedientes: e.expedientes || 0,
      chart: e.porAnio.length > 1 ? chartPorAnio(e.porAnio) : null,
      tipos: e.tipos,
      expedientes: { filas: exp.rows, pagina: exp.pagina, paginas: exp.paginas, hayMas: exp.pagina < exp.paginas },
    }),
  };
}
// /personas y /personas/:slug: noindex (ver la sección Personas de lib/db.js).
function personas(req) {
  if (!db.hayPersonas()) return { status: 404, html: renderTemplate('404', { req }) };
  const q = (req.query.q || '').trim();
  const lista = db.getPersonas({ q, pagina: req.query.p });
  return { status: 200, html: renderTemplate('personas', { req, q, lista }) };
}

function persona(req, params) {
  const p = db.hayPersonas() ? db.getPersona(params.slug) : null;
  if (!p) return { status: 404, html: renderTemplate('404', { req }) };
  const res = db.getExpedientesPersona(p, req.query.p);
  const resumen = res.todos ? resumirAvisos(res.todos) : null;
  return {
    status: 200,
    html: renderTemplate('parte', {
      req, clase: 'persona', parte: p,
      totalExpedientes: res.totalExpedientes,
      chart: resumen && resumen.porAnio.length > 1 ? chartPorAnio(resumen.porAnio) : null,
      tipos: resumen ? resumen.tipos : null,
      expedientes: {
        grupos: res.grupos, pagina: res.pagina, hayMas: res.hayMas,
        paginas: res.totalExpedientes == null ? null : Math.max(1, Math.ceil(res.totalExpedientes / db.EXPEDIENTES_POR_PAGINA)),
      },
    }),
  };
}
function sources(req) {
  return { status: 200, html: renderTemplate('sources', { req, stats: db.getStats() }) };
}

// Healthcheck del contenedor (ver Dockerfile).
function health() {
  return { status: db.ping() ? 200 : 503, headers: { 'Content-Type': 'text/plain; charset=utf-8' }, html: 'ok' };
}

module.exports = {
  health, home, portada, personas, persona, juzgado, expediente, fecha, empresas, empresa, sources, renderTemplate, pugLocals,
};
