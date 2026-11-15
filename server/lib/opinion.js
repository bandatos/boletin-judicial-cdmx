// opinion.js — opiniones del formulario del sitio (mixin formOpinion). El
// server no las guarda: las valida y las reenvía al formulario de Google
// (config/opinion.json), donde quedan en la hoja de cálculo. Nunca se
// loguean ni se mandan a Analytics.

const config = require('../config/opinion.json');

const LARGO = { bueno: 2000, falta: 2000, nombre: 120, contacto: 200 };
const LIMITE_POR_HORA = Number(process.env.OPINION_LIMITE) || 5;
const HORA = 3600 * 1000;
const envios = new Map(); // ip -> [timestamps]

const entry = hex => 'entry.' + parseInt(hex, 16);
const formResponse = () => process.env.OPINION_FORM_URL || config.formResponse;

function limpiar(campos) {
  const d = {};
  for (const k of ['bueno', 'falta', 'nombre', 'contacto', 'intencion', 'lugar', 'acepto', 'sitio']) {
    d[k] = String(campos[k] || '').replace(/\r\n/g, '\n').trim();
  }
  return d;
}

// Devuelve el código de error o null.
function validar(d) {
  for (const [k, max] of Object.entries(LARGO)) if (d[k].length > max) return 'largo';
  if (!d.bueno && !d.falta) return 'vacio';
  if ((d.nombre || d.contacto) && d.acepto !== 'si') return 'consentimiento';
  return null;
}

function cuerpoGoogle(d) {
  const c = config.campos;
  const b = new URLSearchParams();
  if (d.bueno) b.append(entry(c.bueno), d.bueno);
  if (d.falta) b.append(entry(c.falta), d.falta);
  if (config.intencion[d.intencion]) {
    b.append(entry(c.intencion), config.intencion[d.intencion]);
  } else if (d.intencion === 'otro') {
    b.append(entry(c.intencion), '__other_option__');
    b.append(entry(c.intencion) + '.other_option_response', 'Otra cosa');
  }
  if (d.nombre) b.append(entry(c.nombre), d.nombre);
  if (d.contacto) b.append(entry(c.contacto), d.contacto);
  if (d.acepto === 'si' && (d.nombre || d.contacto)) b.append(entry(c.acepto), config.acepto);
  return b;
}

function ipDe(req) {
  const xff = req.headers['x-forwarded-for'];
  return (xff ? xff.split(',')[0] : req.socket.remoteAddress || '').trim();
}

function excedeLimite(ip, ahora = Date.now()) {
  const lista = (envios.get(ip) || []).filter(t => ahora - t < HORA);
  if (lista.length >= LIMITE_POR_HORA) { envios.set(ip, lista); return true; }
  lista.push(ahora);
  envios.set(ip, lista);
  if (envios.size > 5000) envios.delete(envios.keys().next().value);
  return false;
}

// Procesa un envío. Devuelve { ok, error, datos }.
async function recibir(req, campos) {
  const d = limpiar(campos);
  // Campo trampa: invisible para las personas, lo llenan los bots. Se
  // contesta como si estuviera bien para que no reintenten.
  if (d.sitio) return { ok: true, datos: d };
  const error = validar(d);
  if (error) return { ok: false, error, datos: d };
  if (excedeLimite(ipDe(req))) return { ok: false, error: 'limite', datos: d };
  try {
    const r = await fetch(formResponse(), {
      method: 'POST',
      body: cuerpoGoogle(d),
      redirect: 'manual',
      signal: AbortSignal.timeout(8000),
    });
    if (r.status >= 400) throw new Error(`Google respondió ${r.status}`);
    return { ok: true, datos: d };
  } catch (err) {
    console.error(`opinion: no se pudo reenviar (${err.message})`);
    return { ok: false, error: 'envio', datos: d };
  }
}

const MENSAJES = {
  vacio: 'Escribe algo en al menos uno de los dos campos.',
  consentimiento: 'Para dejarnos tus datos, marca que aceptas que te contactemos.',
  largo: 'El texto es demasiado largo.',
  limite: 'Recibimos varios envíos seguidos. Intenta de nuevo en un rato.',
  envio: 'No se pudo enviar. Intenta de nuevo en unos minutos.',
};

module.exports = { recibir, validar, cuerpoGoogle, limpiar, excedeLimite, MENSAJES, LARGO, _envios: envios };
