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
  db.getPublicacionChart();
  db.getPortada();
  console.log(`Resumen de la DB listo en ${((Date.now() - t0) / 1000).toFixed(1)}s`);
});

// Recarga de la base sin reiniciar: se revisa cada DB_WATCH_MS (30 s) si
// cambió el archivo (inodo, tamaño o fecha). Para publicar una base nueva
// alcanza con reemplazarla con mv (boletin.sqlite.new -> boletin.sqlite).
// Recarga recién cuando dos revisiones seguidas ven lo mismo, así no abre un
// archivo a medio copiar si alguien lo pisa con cp. Por sondeo y no con
// eventos del sistema (inotify/chokidar): un mv cambia el archivo debajo del
// nombre, y con eventos hay que vigilar el directorio y esperar a que
// termine la escritura; un stat cada 30 s resuelve las dos cosas sin
// dependencias, a cambio de tardar hasta un minuto en tomar la base nueva.
// Mientras se recalcula el resumen (unos segundos) los pedidos esperan.
const fs = require('fs');
const INTERVALO = Number(process.env.DB_WATCH_MS) || 30000;

function firma() {
  try {
    const s = fs.statSync(db.DB_PATH);
    return `${s.ino}:${s.size}:${s.mtimeMs}`;
  } catch {
    return null;
  }
}

let actual = firma();
let candidata = null;
setInterval(() => {
  const f = firma();
  if (!f || f === actual) { candidata = null; return; }
  if (f !== candidata) { candidata = f; return; }
  actual = f;
  candidata = null;
  console.log(`[db] ${db.DB_PATH} cambió, recargando...`);
  const t0 = Date.now();
  db.reloadDb();
  db.getResumen();
  db.getPublicacionChart();
  db.getPortada();
  console.log(`[db] base recargada en ${((Date.now() - t0) / 1000).toFixed(1)}s`);
}, INTERVALO).unref();

module.exports = server;
