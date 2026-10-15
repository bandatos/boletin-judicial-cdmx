// busqueda.js — interpreta lo que escribe la persona en el buscador:
// número de expediente en cualquier formato, palabras del nombre (con la
// última como prefijo) y el filtro de "todas las palabras del mismo lado del
// juicio". Sin acceso a la base: lo usa db.js.

function normalizar(s) {
  return String(s || '').normalize('NFD').replace(/\p{M}/gu, '').toLowerCase();
}

// Palabras como las separa el índice FTS (unicode61: todo lo que no es letra
// ni número separa), sin acentos.
function tokens(s) {
  return normalizar(s).split(/[^\p{L}\p{N}]+/u).filter(Boolean);
}

// Número de expediente: "184/2025", "184-2025", "184 del 2025", "exp. 184/2025",
// "expediente 0184/2025", "184/25". Devuelve "184/2025" (sin ceros adelante,
// año con cuatro dígitos) y el resto del texto, o null si no hay.
const RE_EXPEDIENTE = /(?:\b(?:exp(?:ediente)?|n[uú]m(?:ero)?|no)\b\.?\s*)?\b0*(\d{1,6})\s*(?:\/|-|\s+del?\s+)\s*(\d{4}|\d{2})\b/i;

function parsearExpediente(q) {
  const m = String(q || '').match(RE_EXPEDIENTE);
  if (!m) return null;
  const hoy = new Date().getFullYear();
  let anio = Number(m[2]);
  if (m[2].length === 2) anio += anio <= hoy % 100 ? 2000 : 1900;
  if (anio < 1950 || anio > hoy + 1) return null;
  const resto = (q.slice(0, m.index) + ' ' + q.slice(m.index + m[0].length)).trim();
  return { expediente: `${Number(m[1])}/${anio}`, resto };
}

// Consulta interpretada: { expediente, palabras }. Las palabras van sin
// acentos; las de una letra se descartan salvo que sean lo único.
function parsearConsulta(q) {
  const texto = String(q || '').trim();
  const exp = parsearExpediente(texto);
  const resto = exp ? exp.resto : texto;
  let palabras = tokens(resto);
  if (palabras.some(w => w.length > 1)) palabras = palabras.filter(w => w.length > 1);
  return { expediente: exp ? exp.expediente : null, palabras };
}

// Consulta FTS5: todas las palabras (AND). Con dos palabras o más, la
// última va como prefijo si tiene tres letras o más ("garcia gonz" encuentra
// "García González"). Con una sola palabra no: un prefijo corto se expande a
// millones de avisos ("gonz*": 2.4M) y la búsqueda tarda segundos; para eso
// están las fichas que coinciden, que sí usan prefijo. Solo letras y
// números: FTS5 no acepta otros caracteres fuera de comillas.
function consultaFts(palabras) {
  if (!palabras.length) return null;
  const prefijo = palabras.length >= 2;
  return palabras.map((w, i) => (prefijo && i === palabras.length - 1 && w.length >= 3 ? `${w}*` : w)).join(' ');
}

// Con número de expediente y palabras ("garcia 184/2025"): se arranca por el
// expediente (pocos avisos) y las palabras se buscan en las partes, de
// cualquier lado.
function filtroPalabras(palabras) {
  if (!palabras.length) return null;
  const prefijo = palabras.length >= 2;
  return r => {
    const t = tokens(`${r.actora || ''} ${r.demandada || ''}`);
    return palabras.every((w, i) => (prefijo && i === palabras.length - 1 ? t.some(x => x.startsWith(w)) : t.includes(w)));
  };
}

// Con dos palabras o más, un aviso cuenta solo si todas están del mismo lado
// del juicio (actora o demandada): "juan garcia" no trae "García Sara vs.
// Juan Pérez". La última palabra vale como prefijo, igual que en el FTS.
function filtroMismoLado(palabras) {
  if (palabras.length < 2) return null;
  const ultima = palabras[palabras.length - 1];
  const otras = palabras.slice(0, -1);
  const cumple = campo => {
    const t = tokens(campo);
    return otras.every(w => t.includes(w)) && t.some(x => x.startsWith(ultima));
  };
  return r => cumple(r.actora) || cumple(r.demandada);
}

// Distancia de edición (Levenshtein) entre dos palabras cortas.
function distancia(a, b) {
  let previa = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    const fila = [i];
    for (let j = 1; j <= b.length; j++) {
      fila[j] = Math.min(previa[j] + 1, fila[j - 1] + 1, previa[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    }
    previa = fila;
  }
  return previa[b.length];
}

// Todas las palabras a una edición de distancia (borrar, cambiar o agregar
// una letra, o invertir dos seguidas). Para buscarlas exactas en el
// vocabulario: con trigramas, "garsia" y "garcia" comparten uno solo.
const LETRAS = 'abcdefghijklmnopqrstuvwxyz';
function variantes1(w) {
  const out = new Set();
  for (let i = 0; i <= w.length; i++) {
    const a = w.slice(0, i);
    const b = w.slice(i);
    if (b) out.add(a + b.slice(1));
    if (b.length > 1) out.add(a + b[1] + b[0] + b.slice(2));
    for (const c of LETRAS) {
      if (b) out.add(a + c + b.slice(1));
      out.add(a + c + b);
    }
  }
  out.delete(w);
  return [...out];
}

// Trigramas de una palabra, como consulta FTS5 con OR (cada uno entre comillas).
function consultaTrigramas(palabra) {
  const tri = new Set();
  for (let i = 0; i + 3 <= palabra.length; i++) tri.add(palabra.slice(i, i + 3));
  return [...tri].map(t => `"${t}"`).join(' OR ');
}

module.exports = {
  distancia, consultaTrigramas, variantes1, normalizar, tokens, parsearExpediente, parsearConsulta, consultaFts, filtroMismoLado, filtroPalabras };
