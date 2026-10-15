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

function getBoletinText(boletinId) {
  const p = findBoletinTextPath(boletinId);
  if (!p) return null;
  try {
    const buf = fs.readFileSync(p);
    return (p.endsWith('.gz') ? zlib.gunzipSync(buf) : buf).toString('utf8');
  } catch {
    return null;
  }
}

// Ubica un extracto aproximado del aviso dentro del texto completo del
// boletín (que tiene TODOS los avisos de ese día). No es exacto al 100%
// (no se persiste el offset real del parseo), pero para mostrar "el texto
// original de donde salió esto" alcanza: busca el nombre de la actora y
// corta en el primer "Núm. Exp. <expediente>." que aparezca después.
function findExcerpt(fullText, { actora, expediente }) {
  if (!fullText || !actora) return null;

  const joined = fullText.replace(/\s+/g, ' ');
  const idx = joined.indexOf(actora);
  if (idx === -1) return null;

  let end = Math.min(idx + 1200, joined.length);
  if (expediente) {
    const needle = `Núm. Exp. ${expediente}`;
    const expIdx = joined.indexOf(needle, idx);
    if (expIdx !== -1) {
      const dot = joined.indexOf('.', expIdx + needle.length);
      end = dot !== -1 ? dot + 1 : expIdx + needle.length;
    }
  }

  return joined.slice(idx, end).trim();
}

module.exports = { getBoletinText, findExcerpt, DATA_DIR };
