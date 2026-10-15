// logger.js — log de acceso por request, un renglón por request como en
// sociedad-web-front/lib/router.js (ahí es mucho más elaborado: tier de
// suscripción, IP, CDN, detección de bots — nada de eso aplica acá porque
// no hay auth ni pagos, así que queda solo fecha/hora/método/status/duración/url).

function pad(str, len) {
  str = String(str);
  return str.length >= len ? str : str + ' '.repeat(len - str.length);
}

function padStart(str, len) {
  str = String(str);
  return str.length >= len ? str : ' '.repeat(len - str.length) + str;
}

function logRequest(req, status, durationMs) {
  const now = new Date();
  const date = now.toISOString().slice(0, 10).replace(/-/g, '');
  const time = now.toTimeString().slice(0, 8);
  console.log(
    `${date} ${time} ${pad(req.method, 6)} ${padStart(status, 3)} ${padStart(durationMs + 'ms', 7)} ${req.url}`
  );
}

module.exports = { logRequest };
