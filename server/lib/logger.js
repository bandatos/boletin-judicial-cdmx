// logger.js — log de acceso por request, un renglón por request como en
// sociedad-web-front/lib/router.js (ahí es mucho más elaborado: tier de
// suscripción, IP, CDN, detección de bots — nada de eso aplica acá porque
// no hay auth ni pagos, así que queda fecha/hora/método/status/duración/url,
// dominio de origen y bot conocido).

function pad(str, len) {
  str = String(str);
  return str.length >= len ? str : str + ' '.repeat(len - str.length);
}

function padStart(str, len) {
  str = String(str);
  return str.length >= len ? str : ' '.repeat(len - str.length) + str;
}

// Dominio de origen (solo el dominio del Referer, sin la URL) cuando viene de
// otro sitio, y el nombre del bot si el user-agent es de uno conocido: así se
// ve cuántas veces ChatGPT o un buscador leen el sitio, que GA no registra.
const BOTS = /(ChatGPT-User|OAI-SearchBot|GPTBot|PerplexityBot|ClaudeBot|Claude-User|Googlebot|bingbot|Applebot|YandexBot|AhrefsBot|SemrushBot|Bytespider|facebookexternalhit|meta-externalagent)/i;

function origen(req) {
  try {
    const host = new URL(req.headers.referer || '').hostname;
    return host && host !== String(req.headers.host || '').split(':')[0] ? ` ref=${host}` : '';
  } catch {
    return '';
  }
}

function bot(req) {
  const m = String(req.headers['user-agent'] || '').match(BOTS);
  return m ? ` bot=${m[1]}` : '';
}

function logRequest(req, status, durationMs) {
  const now = new Date();
  const date = now.toISOString().slice(0, 10).replace(/-/g, '');
  const time = now.toTimeString().slice(0, 8);
  console.log(
    `${date} ${time} ${pad(req.method, 6)} ${padStart(status, 3)} ${padStart(durationMs + 'ms', 7)} ${req.url}${origen(req)}${bot(req)}`
  );
}

module.exports = { logRequest, origen, bot };
