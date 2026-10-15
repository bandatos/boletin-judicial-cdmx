// litigios-cdmx.js — entrypoint principal, al estilo de
// sociedad-web-front/sociedad-web-front.js: http.createServer crudo (sin
// framework), sin auth, sin pagos, sin PiTrack ni publicidad.

const http = require('http');
const { requestListener } = require('./lib/router');
const db = require('./lib/db');

const PORT = process.env.PORT || 3000;
const HOST = process.env.HOST || '0.0.0.0';

const server = http.createServer(requestListener);

server.listen(PORT, HOST, () => {
  console.log(`Litigios CDMX escuchando en http://${HOST}:${PORT}`);
  // Los agregados (tipos, años, gráfico, conteos) recorren toda la tabla:
  // se calculan al arrancar para que no los pague el primer request.
  const t0 = Date.now();
  db.getResumen();
  console.log(`Resumen de la DB listo en ${((Date.now() - t0) / 1000).toFixed(1)}s`);
});

// En dev, "node --watch" ya reinicia el proceso cuando cambia código (lib/,
// config/, este archivo). Pero boletin.sqlite no es un módulo require()-ado,
// así que node --watch no lo ve — por eso acá se lo vigila aparte con
// chokidar y se reabre la conexión en caliente, sin reiniciar el server.
if (process.env.NODE_ENV !== 'production') {
  const chokidar = require('chokidar');
  const watcher = chokidar.watch(db.DB_PATH, { ignoreInitial: true });
  watcher.on('change', () => {
    console.log(`[watch] ${db.DB_PATH} cambió, recargando conexión...`);
    db.reloadDb();
  });
}

module.exports = server;
