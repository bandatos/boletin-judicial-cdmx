// fechas.js — formato de fechas ISO (AAAA-MM-DD) para las vistas y armado
// del calendario mensual de /fecha.

const MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
  'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];
const DIAS = ['domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado'];

const pad = n => String(n).padStart(2, '0');

function isoToDate(iso) {
  const [a, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(a, m - 1, d));
}

function fechaCorta(iso) {
  if (!iso) return '—';
  const d = isoToDate(iso);
  return `${d.getUTCDate()} ${MESES[d.getUTCMonth()].slice(0, 3)}. ${d.getUTCFullYear()}`;
}

function fechaLarga(iso) {
  const d = isoToDate(iso);
  return `${DIAS[d.getUTCDay()]} ${d.getUTCDate()} de ${MESES[d.getUTCMonth()]} de ${d.getUTCFullYear()}`;
}

function nombreMes(anio, mes) {
  return `${MESES[mes - 1]} ${anio}`;
}

// Semanas (lunes a domingo) del mes, con null en los huecos.
function semanasDelMes(anio, mes) {
  const offset = (new Date(Date.UTC(anio, mes - 1, 1)).getUTCDay() + 6) % 7;
  const dias = new Date(Date.UTC(anio, mes, 0)).getUTCDate();
  const celdas = Array(offset).fill(null);
  for (let d = 1; d <= dias; d++) celdas.push(`${anio}-${pad(mes)}-${pad(d)}`);
  while (celdas.length % 7) celdas.push(null);
  const semanas = [];
  for (let i = 0; i < celdas.length; i += 7) semanas.push(celdas.slice(i, i + 7));
  return semanas;
}

function moverMes(anio, mes, delta) {
  const k = anio * 12 + (mes - 1) + delta;
  return { anio: Math.floor(k / 12), mes: (k % 12) + 1 };
}

const mesKey = (anio, mes) => `${anio}-${pad(mes)}`;

module.exports = { MESES, fechaCorta, fechaLarga, nombreMes, semanasDelMes, moverMes, mesKey, isoToDate };
