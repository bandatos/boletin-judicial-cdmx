// raw-text.js — lee el .txt de pdftotext ya guardado por el scraper para
// mostrar el texto original del aviso. No se duplica en el sqlite (eso
// infla la base sin necesidad, como pasó con la columna "fecha" que se
// sacó antes) — se lee directo de scraper/data/*/pdfs/boletin_<id>.txt.
//
// Cada corrida de poc.py crea su propio scraper/data/YYYY-MM-DD/, así que
// el mismo boletín puede haber quedado guardado bajo distintas fechas de
// corrida a lo largo del tiempo. Por eso se busca en todos los run dirs,
// no en uno fijo.

const fs = require('fs');
const path = require('path');
const zlib = require('zlib');

const DATA_DIR = process.env.SCRAPER_DATA_DIR
  || path.join(__dirname, '..', '..', 'scraper', 'data');

function findBoletinTextPath(boletinId) {
  if (!fs.existsSync(DATA_DIR)) return null;

  const runDirs = fs.readdirSync(DATA_DIR, { withFileTypes: true })
    .filter(d => d.isDirectory())
    .map(d => d.name)
    .sort()
    .reverse(); // preferir la corrida más reciente si está en varias

  // .txt.gz: en el server los textos van comprimidos (6 GB -> ~1.5 GB).
  for (const run of runDirs) {
    for (const ext of ['.txt', '.txt.gz']) {
      const p = path.join(DATA_DIR, run, 'pdfs', `boletin_${boletinId}${ext}`);
      if (fs.existsSync(p)) return p;
    }
  }
  return null;
}

// Los últimos boletines leídos quedan en memoria: al desplegar varios avisos
// de una ficha se piden seguido, y muchos son del mismo boletín.
const CACHE_TEXTOS = 4;
const cacheTextos = new Map();

function getBoletinText(boletinId) {
  if (cacheTextos.has(boletinId)) return cacheTextos.get(boletinId);
  const p = findBoletinTextPath(boletinId);
  if (!p) return null;
  let texto = null;
  try {
    const buf = fs.readFileSync(p);
    texto = (p.endsWith('.gz') ? zlib.gunzipSync(buf) : buf).toString('utf8');
  } catch {
    return null;
  }
  cacheTextos.set(boletinId, texto);
  if (cacheTextos.size > CACHE_TEXTOS) cacheTextos.delete(cacheTextos.keys().next().value);
  return texto;
}

// Ubica el aviso dentro del texto completo del boletín (que tiene TODOS los
// avisos de ese día). No se persiste el offset real del parseo, así que se
// busca: cada aparición del número de expediente ("184/2025", o la toca
// "84/2020/001") y, hacia atrás, el nombre de la actora. Gana la aparición
// con la actora más cerca: un banco tiene decenas de avisos el mismo día, y
// tomar la primera mención de su nombre mostraba el aviso de otro
// expediente. El final es el punto después del número ("Núm. Exp. 184/2025.")
// o, en el formato de las UGJ, la cola del conteo ("Núm. Exp. 6703/2026,
// Prevención. 1 Acdo.").
const MAX_AVISO = 900;

// Si el expediente tiene más de un aviso ese día (una sentencia y "4
// Acdos."), `marca` desempata: el texto del aviso tiene que contenerla.
function marcaDe(aviso) {
  if (!aviso) return null;
  if ((aviso.tipo_actuacion || '').startsWith('sentencia')) return /\bSent\b/i;
  if (aviso.tipo_actuacion === 'audiencia') return /\bAud/i;
  if (aviso.num_acdos) return new RegExp(`\\b${aviso.num_acdos}\\s+Acdos?\\b`, 'i');
  return null;
}

// Copia plana de un pedazo del texto. Un slice de V8 apunta al texto
// completo del boletín (varios MB): guardado en la caché del router, cada
// aviso retenía su boletín entero y el server se quedaba sin memoria (300
// avisos, 1,6 GB). Con la copia, el boletín se libera.
function copiar(s) {
  return s == null ? s : Buffer.from(s, 'utf8').toString('utf8');
}

function findExcerpt(fullText, opciones) {
  return copiar(buscarExcerpt(fullText, opciones));
}

function buscarExcerpt(fullText, { actora, expediente, marca }) {
  if (!fullText || !actora) return null;
  const joined = fullText.replace(/\s+/g, ' ');
  const numero = ((expediente || '').match(/\d+[/-]\d{4}(?:[/-]\d{1,3})?/) || [])[0];

  let mejor = null;
  if (numero) {
    for (let i = joined.indexOf(numero); i !== -1; i = joined.indexOf(numero, i + 1)) {
      // Que no sea parte de otro número ("1184/2025" o "184/20251").
      if (/\d/.test(joined[i - 1] || '') || /\d/.test(joined[i + numero.length] || '')) continue;
      const desde = Math.max(0, i - MAX_AVISO);
      const a = joined.lastIndexOf(actora, i);
      if (a < desde) continue;
      // Lo que sigue al número es de este aviso solo en el formato de las
      // UGJ (", Prevención. 1 Acdo."). Si no, se corta en el número.
      const despues = joined.slice(i + numero.length, i + numero.length + 120);
      const colaUgj = (despues.match(/^,[^]*?\d+\s+(?:Acdos?|Audiencias?|Aud|Sent)\b\.?/) || [''])[0];
      const tramo = joined.slice(a, i + numero.length) + colaUgj;
      const c = { inicio: a, fin: i + numero.length, conMarca: Boolean(marca && marca.test(tramo)) };
      if (!mejor || (c.conMarca && !mejor.conMarca)
        || (c.conMarca === mejor.conMarca && c.fin - c.inicio < mejor.fin - mejor.inicio)) mejor = c;
    }
  }
  if (!mejor) {
    const a = joined.indexOf(actora);
    if (a === -1) return null;
    return joined.slice(a, Math.min(a + 600, joined.length)).trim();
  }
  let fin = mejor.fin;
  const cola = joined.slice(fin, fin + 200);
  const ugj = cola.match(/^,[^]*?\d+\s+(?:Acdos?|Audiencias?|Aud|Sent)\b\.?/);
  if (ugj && !/Núm\. Exp\./.test(ugj[0])) fin += ugj[0].length;
  else {
    // El punto final, o un sufijo corto ("Tomo II.", "Principal.").
    const m = cola.match(/^[^.]*\./);
    if (m && m[0].length <= 25) fin += m[0].length;
  }
  return joined.slice(mejor.inicio, fin).trim();
}

module.exports = { getBoletinText, findExcerpt, marcaDe, DATA_DIR };
