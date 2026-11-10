// novedades.js — lee CHANGELOG.md (lo de debajo de "<!-- novedades -->") y
// lo devuelve como días con sus líneas, para /novedades. El criterio de qué
// entra y cómo se escribe está en el mismo archivo.
const fs = require('fs');
const path = require('path');

// En el repo está en la raíz, en la imagen de Docker junto al server.
const RUTAS = [path.join(__dirname, '..', 'CHANGELOG.md'), path.join(__dirname, '..', '..', 'CHANGELOG.md')];

let cache = null;

function parsear(texto) {
  const marca = texto.indexOf('<!-- novedades -->');
  const dias = [];
  if (marca < 0) return dias;
  for (const linea of texto.slice(marca).split('\n')) {
    const fecha = linea.match(/^## (\d{4}-\d{2}-\d{2})\s*$/);
    if (fecha) dias.push({ fecha: fecha[1], cambios: [] });
    else if (linea.startsWith('- ') && dias.length) dias[dias.length - 1].cambios.push(linea.slice(2).trim());
  }
  return dias.filter(d => d.cambios.length);
}

function getNovedades() {
  const ruta = RUTAS.find(r => fs.existsSync(r));
  if (!ruta) return [];
  const mtime = fs.statSync(ruta).mtimeMs;
  if (!cache || cache.mtime !== mtime) cache = { mtime, dias: parsear(fs.readFileSync(ruta, 'utf8')) };
  return cache.dias;
}

module.exports = { getNovedades, parsear };
