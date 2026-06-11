// search.js — Boletín Judicial CDMX
// Carga boletin.sqlite.gz desde GitHub Releases, lo cachea en IndexedDB,
// y ejecuta búsquedas FTS5 localmente con sql.js.

const DB_URL = 'https://github.com/bandatos/boletin-judicial-cdmx/releases/download/data/boletin.sqlite.gz';
const DB_CACHE_KEY = 'boletin-sqlite-v1';
const SQLS_CDN = 'https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.10.2/sql-wasm.js';
const SQLS_WASM = 'https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.10.2/sql-wasm.wasm';
const MAX_RESULTS = 100;

let db = null;

// ── UI helpers ────────────────────────────────────────────────────────────────

const $ = id => document.getElementById(id);

function setStatus(msg, progress = null) {
  $('status-text').textContent = msg;
  if (progress !== null) {
    $('progress-bar').style.width = progress + '%';
    $('progress-bar-wrap').style.display = 'block';
  } else {
    $('progress-bar-wrap').style.display = 'none';
  }
}

function setReady() {
  $('status').style.display = 'none';
  $('q').disabled = false;
  $('tipo').disabled = false;
  $('search-btn').disabled = false;
  $('q').focus();
}

function renderResults(rows, total) {
  $('result-count').textContent = total === 0
    ? 'Sin resultados.'
    : `${total > MAX_RESULTS ? MAX_RESULTS + '+' : total} resultado${total !== 1 ? 's' : ''}`;

  $('result-list').innerHTML = rows.map(r => {
    const partes = r.demandada
      ? `${escHtml(r.actora)} <span class="card-vs">vs.</span> ${escHtml(r.demandada)}`
      : escHtml(r.actora);

    const pdfLink = r.pdf_url
      ? `<div class="card-pdf"><a href="${escHtml(r.pdf_url)}" target="_blank" rel="noopener">Ver PDF del boletín</a></div>`
      : '';

    return `
      <div class="card">
        <div class="card-partes">${partes}</div>
        <div class="card-meta">
          ${r.tipo_juicio ? `<span class="tag">${escHtml(r.tipo_juicio)}</span>` : ''}
          <span>Exp. ${escHtml(r.expediente || '—')}</span>
          <span>${escHtml(r.juzgado || '—')}</span>
          ${r.sala ? `<span>${escHtml(r.sala)}</span>` : ''}
          <span>${escHtml(r.fecha || '—')}</span>
        </div>
        ${pdfLink}
      </div>`;
  }).join('');
}

function escHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ── IndexedDB cache ───────────────────────────────────────────────────────────

function openCache() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open('boletin-cache', 1);
    req.onupgradeneeded = e => e.target.result.createObjectStore('files');
    req.onsuccess = e => resolve(e.target.result);
    req.onerror = e => reject(e.target.error);
  });
}

async function getCached(idb) {
  return new Promise((resolve, reject) => {
    const tx = idb.transaction('files', 'readonly');
    const req = tx.objectStore('files').get(DB_CACHE_KEY);
    req.onsuccess = e => resolve(e.target.result || null);
    req.onerror = e => reject(e.target.error);
  });
}

async function setCached(idb, data) {
  return new Promise((resolve, reject) => {
    const tx = idb.transaction('files', 'readwrite');
    const req = tx.objectStore('files').put(data, DB_CACHE_KEY);
    req.onsuccess = () => resolve();
    req.onerror = e => reject(e.target.error);
  });
}

// ── DB loading ────────────────────────────────────────────────────────────────

async function fetchWithProgress(url, onProgress) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const total = parseInt(res.headers.get('content-length') || '0');
  const reader = res.body.getReader();
  const chunks = [];
  let received = 0;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.length;
    if (total) onProgress(Math.round(received / total * 100));
  }

  const merged = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) { merged.set(chunk, offset); offset += chunk.length; }
  return merged;
}

async function decompress(data) {
  const ds = new DecompressionStream('gzip');
  const blob = new Blob([data]);
  const stream = blob.stream().pipeThrough(ds);
  const buf = await new Response(stream).arrayBuffer();
  return new Uint8Array(buf);
}

async function loadSqlJs() {
  await new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = SQLS_CDN;
    s.onload = resolve;
    s.onerror = reject;
    document.head.appendChild(s);
  });
  return window.initSqlJs({ locateFile: f => SQLS_WASM });
}

async function loadDb() {
  const idb = await openCache();

  // Intentar caché local
  let bytes = await getCached(idb);

  if (!bytes) {
    setStatus('Descargando base de datos...', 0);
    const compressed = await fetchWithProgress(DB_URL, pct => setStatus(`Descargando... ${pct}%`, pct));
    setStatus('Descomprimiendo...', 99);
    bytes = await decompress(compressed);
    await setCached(idb, bytes);
  } else {
    setStatus('Cargando desde caché local...', 90);
  }

  setStatus('Abriendo base de datos...', 99);
  const SQL = await loadSqlJs();
  db = new SQL.Database(bytes);

  populateTipoFilter();
  setReady();
}

// ── Filtros ───────────────────────────────────────────────────────────────────

function populateTipoFilter() {
  const rows = db.exec(
    "SELECT DISTINCT tipo_juicio FROM entradas WHERE tipo_juicio IS NOT NULL ORDER BY tipo_juicio LIMIT 200"
  );
  if (!rows.length) return;
  const sel = $('tipo');
  for (const [tipo] of rows[0].values) {
    const opt = document.createElement('option');
    opt.value = opt.textContent = tipo;
    sel.appendChild(opt);
  }
}

// ── Búsqueda ──────────────────────────────────────────────────────────────────

function search() {
  if (!db) return;
  const q = $('q').value.trim();
  const tipo = $('tipo').value;

  if (!q && !tipo) {
    $('result-count').textContent = '';
    $('result-list').innerHTML = '';
    return;
  }

  let sql, params;

  if (q) {
    // FTS5: escapar comillas dobles en la query
    const ftsQ = q.replace(/"/g, '""');
    sql = `
      SELECT e.actora, e.demandada, e.tipo_juicio, e.expediente,
             e.juzgado, e.sala, e.fecha, b.pdf_url
      FROM entradas e
      JOIN entradas_fts f ON e.id = f.rowid
      LEFT JOIN boletines b ON e.boletin_id = b.id
      WHERE entradas_fts MATCH ?
      ${tipo ? 'AND e.tipo_juicio = ?' : ''}
      LIMIT ${MAX_RESULTS}
    `;
    params = tipo ? [ftsQ, tipo] : [ftsQ];
  } else {
    sql = `
      SELECT e.actora, e.demandada, e.tipo_juicio, e.expediente,
             e.juzgado, e.sala, e.fecha, b.pdf_url
      FROM entradas e
      LEFT JOIN boletines b ON e.boletin_id = b.id
      WHERE e.tipo_juicio = ?
      LIMIT ${MAX_RESULTS}
    `;
    params = [tipo];
  }

  try {
    const result = db.exec(sql, params);
    if (!result.length) { renderResults([], 0); return; }

    const cols = result[0].columns;
    const rows = result[0].values.map(row =>
      Object.fromEntries(cols.map((c, i) => [c, row[i]]))
    );
    renderResults(rows, rows.length);
  } catch (e) {
    $('result-count').textContent = 'Error en la búsqueda: ' + e.message;
    $('result-list').innerHTML = '';
  }
}

// ── Eventos ───────────────────────────────────────────────────────────────────

$('search-btn').addEventListener('click', search);
$('q').addEventListener('keydown', e => { if (e.key === 'Enter') search(); });

// ── Init ──────────────────────────────────────────────────────────────────────

loadDb().catch(err => {
  setStatus('Error cargando la base de datos: ' + err.message);
  console.error(err);
});
