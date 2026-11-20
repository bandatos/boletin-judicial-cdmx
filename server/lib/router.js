// router.js — requestListener + tabla de rutas cargada de config/routes.json,
// al estilo de sociedad-web-front/lib/router.js. Acá simplificado: sin
// multi-país ni i18n (este sitio es de un solo idioma), pero mismo patrón
// de viewType "function" | "template" | "static".

const crypto = require('crypto');
const routes = require('../config/routes.json');
const views = require('./views');
const { serveStatic } = require('./static-server');
const { logRequest } = require('./logger');
const db = require('./db');

// Caché del HTML renderizado (las fichas de persona y las búsquedas grandes
// tardan hasta un segundo; repetidas salen de acá). Solo GET con 200 o 404,
// LRU de CACHE_HTML_MAX páginas, se vacía cuando cambia la base (ver
// db.getGeneracion). En dev no se usa: los templates cambian.
const CACHE_HTML_MAX = Number(process.env.CACHE_HTML_MAX) || 500;
const cacheHtml = new Map();
let generacionCache = null;
const usarCache = process.env.NODE_ENV === 'production';

function desdeCache(clave) {
  if (generacionCache !== db.getGeneracion()) {
    cacheHtml.clear();
    bytesCache = 0;
    generacionCache = db.getGeneracion();
  }
  const r = cacheHtml.get(clave);
  if (r) { cacheHtml.delete(clave); cacheHtml.set(clave, r); } // más reciente al final
  return r;
}

// Además del tope de páginas, un tope de tamaño: unas pocas fichas enormes
// no pueden llenar la memoria.
const CACHE_HTML_BYTES = (Number(process.env.CACHE_HTML_MB) || 200) * 1024 * 1024;
let bytesCache = 0;
const tamano = r => (r.html ? r.html.length * 2 : 0);

function aCache(clave, r) {
  if (tamano(r) > CACHE_HTML_BYTES / 10) return;
  const previo = cacheHtml.get(clave);
  if (previo) { bytesCache -= tamano(previo); cacheHtml.delete(clave); }
  cacheHtml.set(clave, r);
  bytesCache += tamano(r);
  while (cacheHtml.size > CACHE_HTML_MAX || bytesCache > CACHE_HTML_BYTES) {
    const [k, v] = cacheHtml.entries().next().value;
    cacheHtml.delete(k);
    bytesCache -= tamano(v);
  }
}

// HTML: el navegador lo guarda pero revalida cada vez con el ETag (304 sin
// cuerpo si no cambió). Con max-age, después de un deploy se mezclaban
// páginas viejas y nuevas (menús distintos) durante unos minutos.
const CACHE_CONTROL_HTML = 'no-cache';
function etagDe(html) {
  return `"${crypto.createHash('md5').update(html).digest('hex').slice(0, 20)}"`;
}

function matchRoute(pathname) {
  if (routes[pathname]) return { route: routes[pathname], params: {} };

  for (const [pattern, route] of Object.entries(routes)) {
    if (!pattern.includes(':')) continue;
    const patternParts = pattern.split('/').filter(Boolean);
    const pathParts = pathname.split('/').filter(Boolean);
    if (patternParts.length !== pathParts.length) continue;

    const params = {};
    const ok = patternParts.every((part, i) => {
      if (part.startsWith(':')) { params[part.slice(1)] = pathParts[i]; return true; }
      return part === pathParts[i];
    });
    if (ok) return { route, params };
  }

  for (const [prefix, route] of Object.entries(routes)) {
    if (route.viewType === 'static' && pathname.startsWith(prefix + '/')) {
      return { route, params: { file: pathname.slice(prefix.length + 1) } };
    }
  }

  return null;
}

// Cuerpo de los POST (solo formularios urlencoded), con tope de tamaño.
const MAX_CUERPO = 8 * 1024;
function leerCuerpo(req) {
  return new Promise((resolve, reject) => {
    let tam = 0;
    const partes = [];
    req.on('data', c => {
      tam += c.length;
      if (tam > MAX_CUERPO) { reject(Object.assign(new Error('cuerpo grande'), { status: 413 })); req.destroy(); return; }
      partes.push(c);
    });
    req.on('end', () => resolve(Object.fromEntries(new URLSearchParams(Buffer.concat(partes).toString('utf8')))));
    req.on('error', reject);
  });
}

async function requestListener(req, res) {
  const start = Date.now();
  res.on('finish', () => logRequest(req, res.statusCode, Date.now() - start));

  try {
    const url = new URL(req.url, `http://${req.headers.host}`);
    const match = matchRoute(url.pathname);

    if (!match) {
      res.writeHead(404, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(views.renderTemplate('404', { req }));
      return;
    }

    const { route, params } = match;

    if (route.viewType === 'static') {
      await serveStatic(params.file, res, url.searchParams.has('v'));
      return;
    }

    req.query = Object.fromEntries(url.searchParams);
    req.params = params;

    // Solo las rutas con "post": true aceptan POST; el resto, solo GET/HEAD.
    if (req.method === 'POST' && route.post) {
      try {
        req.body = await leerCuerpo(req);
      } catch (err) {
        res.writeHead(err.status || 400, { 'Content-Type': 'text/plain; charset=utf-8' });
        res.end('Pedido inválido');
        return;
      }
    } else if (req.method !== 'GET' && req.method !== 'HEAD') {
      res.writeHead(405, { 'Content-Type': 'text/plain; charset=utf-8', Allow: route.post ? 'GET, HEAD, POST' : 'GET, HEAD' });
      res.end('Método no permitido');
      return;
    }

    const clave = req.method === 'GET' && route.handler !== 'health' ? `${url.pathname}${url.search}` : null;
    let result = usarCache && clave ? desdeCache(clave) : null;
    if (!result) {
      if (route.viewType === 'function') {
        result = await views[route.handler](req, params);
      } else if (route.viewType === 'template') {
        result = { status: 200, html: views.renderTemplate(route.template, { req }) };
      } else {
        result = { status: 500, html: 'viewType desconocido' };
      }
      if (result.status === 200 && result.html && !(result.headers || {})['Content-Type']) {
        result.etag = etagDe(result.html);
      }
      if (usarCache && clave && (result.status === 200 || result.status === 404)) aCache(clave, result);
    }

    const headers = { 'Content-Type': 'text/html; charset=utf-8', ...(result.headers || {}) };
    if (result.etag) {
      headers.ETag = result.etag;
      headers['Cache-Control'] = CACHE_CONTROL_HTML;
      if (req.headers['if-none-match'] === result.etag) {
        res.writeHead(304, { ETag: result.etag, 'Cache-Control': CACHE_CONTROL_HTML });
        res.end();
        return;
      }
    }
    res.writeHead(result.status, headers);
    res.end(result.html || '');
  } catch (err) {
    console.error(err);
    res.writeHead(500, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('Error interno');
  }
}

module.exports = { requestListener, matchRoute };
