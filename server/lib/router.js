// router.js — requestListener + tabla de rutas cargada de config/routes.json,
// al estilo de sociedad-web-front/lib/router.js. Acá simplificado: sin
// multi-país ni i18n (este sitio es de un solo idioma), pero mismo patrón
// de viewType "function" | "template" | "static".

const path = require('path');
const routes = require('../config/routes.json');
const views = require('./views');
const { serveStatic } = require('./static-server');
const { logRequest } = require('./logger');

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
      await serveStatic(params.file, res);
      return;
    }

    req.query = Object.fromEntries(url.searchParams);
    req.params = params;

    let result;
    if (route.viewType === 'function') {
      result = await views[route.handler](req, params);
    } else if (route.viewType === 'template') {
      result = { status: 200, html: views.renderTemplate(route.template, { req }) };
    } else {
      result = { status: 500, html: 'viewType desconocido' };
    }

    res.writeHead(result.status, { 'Content-Type': 'text/html; charset=utf-8', ...(result.headers || {}) });
    res.end(result.html || '');
  } catch (err) {
    console.error(err);
    res.writeHead(500, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('Error interno');
  }
}

module.exports = { requestListener, matchRoute };
