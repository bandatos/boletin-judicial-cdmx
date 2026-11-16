// sivep.js — URL vigente de una versión pública de sentencia en el SIVEP
// del Poder Judicial de la CDMX (http://sivepj.poderjudicialcdmx.gob.mx:819/consulta/).
//
// El SIVEP no da enlaces fijos: cada consulta devuelve URLs del gestor
// documental firmadas que vencen a los 10 minutos. Por eso no se guarda la
// URL sino los parámetros de la consulta (materia, juzgado, ejercicio,
// trimestre) y el documentPath, que es estable. Al hacer clic se repite la
// consulta (un POST xajax, sin captcha) y se redirige a la URL nueva.
// Ver private-docs/sivep-versiones-publicas.md.

const http = require('http');

const BASE = 'http://sivepj.poderjudicialcdmx.gob.mx:819/consulta/';
const TIMEOUT_MS = Number(process.env.SIVEP_TIMEOUT_MS) || 25000;
// Las URLs vencen a los 10 minutos: se reusan durante 8.
const VIGENCIA_MS = 8 * 60 * 1000;
const cache = new Map(); // clave de consulta -> { hasta, urls: Map(documentPath -> url) }
const enCurso = new Map(); // clave de consulta -> Promise (pedidos simultáneos comparten la consulta)

function claveConsulta(s) {
  return [s.materia_cod, s.juzgado_cod, s.ejercicio, s.trimestre].join('|');
}

function decodificarEntidades(s) {
  return s.replace(/&amp;/g, '&').replace(/&#0?39;/g, "'").replace(/&quot;/g, '"');
}

// documentPath -> URL firmada, de la respuesta xajax.
function urlsDeRespuesta(xml) {
  const urls = new Map();
  for (const m of xml.matchAll(/href='(https:\/\/gestordocumental\.poderjudicialcdmx\.gob\.mx\/[^']+)'/g)) {
    const url = decodificarEntidades(m[1]);
    try {
      const dp = new URL(url).searchParams.get('documentPath');
      if (dp && !urls.has(dp)) urls.set(dp, url);
    } catch { /* href mal formado: se ignora */ }
  }
  return urls;
}

function consultar(s) {
  const q = `<xjxquery><q>closeM=&selector=esSentencia&materia=${s.materia_cod}`
    + `&juzgado=${s.juzgado_cod}&anio=${s.ejercicio}&trimestre=${s.trimestre}</q></xjxquery>`;
  const body = new URLSearchParams([
    ['xajax', 'searchSentencia'],
    ['xajaxr', String(Date.now())],
    ['xajaxargs[]', q],
    ['xajaxargs[]', s.materia || ''],
  ]).toString();
  return new Promise((resolve, reject) => {
    const req = http.request(BASE, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Content-Length': Buffer.byteLength(body),
        Referer: BASE,
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64)',
      },
      timeout: TIMEOUT_MS,
    }, res => {
      if (res.statusCode !== 200) {
        res.resume();
        reject(new Error(`SIVEP respondió ${res.statusCode}`));
        return;
      }
      const partes = [];
      res.on('data', c => partes.push(c));
      res.on('end', () => resolve(urlsDeRespuesta(Buffer.concat(partes).toString('latin1'))));
      res.on('error', reject);
    });
    req.on('timeout', () => req.destroy(new Error('SIVEP no respondió a tiempo')));
    req.on('error', reject);
    req.end(body);
  });
}

// URL vigente del PDF, o null si la consulta ya no lo lista.
async function urlVigente(s) {
  const clave = claveConsulta(s);
  const c = cache.get(clave);
  if (c && c.hasta > Date.now()) return c.urls.get(s.document_path) || null;
  if (!enCurso.has(clave)) {
    enCurso.set(clave, consultar(s)
      .then(urls => { cache.set(clave, { hasta: Date.now() + VIGENCIA_MS, urls }); return urls; })
      .finally(() => enCurso.delete(clave)));
  }
  const urls = await enCurso.get(clave);
  return urls.get(s.document_path) || null;
}

module.exports = { BASE, urlVigente, urlsDeRespuesta };
