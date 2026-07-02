// search.js — Boletín Judicial CDMX
// Carga boletin.sqlite.gz desde GitHub Releases, lo cachea en IndexedDB,
// y ejecuta búsquedas FTS5 localmente con sql.js.

const DB_URL = './boletin.sqlite.gz';
const DB_CACHE_KEY = 'boletin-sqlite-v1';
// El build oficial de sql.js no incluye el módulo FTS5. Usamos sql.js-fts5,
// un build alternativo compilado con FTS5 habilitado.
const SQLS_CDN = 'https://unpkg.com/sql.js-fts5@1.4.0/dist/sql-wasm.js';
const SQLS_WASM = 'https://unpkg.com/sql.js-fts5@1.4.0/dist/sql-wasm.wasm';
const MAX_RESULTS = 100;
// Se piden más filas de las que se muestran porque varias filas (avisos)
// pueden pertenecer al mismo expediente; agrupamos después en el cliente.
const MAX_ROWS = 500;

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
  $('anio').disabled = false;
  $('search-btn').disabled = false;
  $('q').focus();
}

// Un mismo expediente acumula un aviso por cada acuerdo publicado. El
// número de expediente se reinicia por juzgado, así que la clave de
// agrupación es (juzgado o sala, expediente), no solo el expediente.
function groupByExpediente(rows) {
  const groups = new Map();
  let noKeyIdx = 0;

  for (const r of rows) {
    const key = r.expediente
      ? `${r.juzgado || ''}|${r.sala || ''}|${r.expediente}`
      : `__sin_exp_${noKeyIdx++}`;

    if (!groups.has(key)) {
      groups.set(key, { avisos: [], ...r });
    }
    const g = groups.get(key);
    g.avisos.push(r);
    // El aviso con mayor boletin_id es el más reciente: lo usamos como
    // datos representativos de la card.
    if (r.boletin_id > g.boletin_id) {
      Object.assign(g, r);
    }
  }

  return Array.from(groups.values());
}

function renderResults(rows, total) {
  const groups = groupByExpediente(rows);
  const shown = groups.slice(0, MAX_RESULTS);

  $('result-count').textContent = total === 0
    ? 'Sin resultados.'
    : `${groups.length > MAX_RESULTS ? MAX_RESULTS + '+' : groups.length} expediente${groups.length !== 1 ? 's' : ''}` +
      (total > groups.length ? ` (${total > MAX_ROWS ? MAX_ROWS + '+' : total} avisos)` : '');

  $('result-list').innerHTML = shown.map(r => {
    const partes = r.demandada
      ? `${escHtml(r.actora)} <span class="card-vs">vs.</span> ${escHtml(r.demandada)}`
      : escHtml(r.actora);

    const pdfLink = r.pdf_url
      ? `<div class="card-pdf"><a href="${escHtml(r.pdf_url)}" target="_blank" rel="noopener">Ver PDF del boletín</a></div>`
      : '';

    const otrosAvisos = r.avisos.length > 1
      ? `<details class="card-avisos">
          <summary>${r.avisos.length} avisos</summary>
          <ul>
            ${r.avisos
              .slice()
              .sort((a, b) => (b.boletin_id || 0) - (a.boletin_id || 0))
              .map(a => `
                <li>
                  ${escHtml(a.fecha || '—')}
                  ${a.tipo_juicio ? ` — ${escHtml(a.tipo_juicio)}` : ''}
                  ${a.pdf_url ? ` — <a href="${escHtml(a.pdf_url)}" target="_blank" rel="noopener">PDF</a>` : ''}
                </li>`)
              .join('')}
          </ul>
        </details>`
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
        ${otrosAvisos}
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
  populateAnioFilter();
  renderYearChart();
  setReady();
}

// ── Filtros ───────────────────────────────────────────────────────────────────

function populateTipoFilter() {
  const rows = db.exec(
    "SELECT tipo_juicio FROM entradas WHERE tipo_juicio IS NOT NULL " +
    "GROUP BY tipo_juicio ORDER BY COUNT(*) DESC LIMIT 200"
  );
  if (!rows.length) return;
  const sel = $('tipo');
  for (const [tipo] of rows[0].values) {
    const opt = document.createElement('option');
    opt.value = opt.textContent = tipo;
    sel.appendChild(opt);
  }
}

function populateAnioFilter() {
  // El año es el del expediente (Núm. Exp. NNNN/AAAA), no el de publicación
  // del boletín.
  const rows = db.exec(
    "SELECT anio FROM entradas WHERE anio IS NOT NULL " +
    "GROUP BY anio ORDER BY anio DESC"
  );
  if (!rows.length) return;
  const sel = $('anio');
  for (const [anio] of rows[0].values) {
    const opt = document.createElement('option');
    opt.value = opt.textContent = anio;
    sel.appendChild(opt);
  }
}

// ── Gráfico: avisos por año ──────────────────────────────────────────────────

const CHART_COLORS = ['#1a5276', '#2e86c1', '#5dade2', '#85c1e9', '#aed6f1', '#ccc'];
const CHART_YEARS = 15;

function renderYearChart() {
  const topRows = db.exec(
    "SELECT tipo_juicio FROM entradas WHERE tipo_juicio IS NOT NULL " +
    "GROUP BY tipo_juicio ORDER BY COUNT(*) DESC LIMIT 5"
  );
  const top5 = topRows.length ? topRows[0].values.map(v => v[0]) : [];
  const buckets = [...top5, 'Otro'];
  const placeholders = top5.map(() => '?').join(',');

  const sql = `
    SELECT anio,
      ${top5.length ? `CASE WHEN tipo_juicio IN (${placeholders}) THEN tipo_juicio ELSE 'Otro' END` : `'Otro'`} AS bucket,
      COUNT(*) AS n
    FROM entradas
    WHERE anio IS NOT NULL
    GROUP BY anio, bucket
  `;
  const result = db.exec(sql, top5);
  if (!result.length) { $('chart-box').style.display = 'none'; return; }

  // anio -> { bucket -> n }
  const byAnio = new Map();
  for (const [anio, bucket, n] of result[0].values) {
    if (!byAnio.has(anio)) byAnio.set(anio, {});
    byAnio.get(anio)[bucket] = n;
  }

  // Años recientes y plausibles (se descartan años con errores de OCR, ej. 2040).
  const currentYear = new Date().getFullYear();
  const anios = Array.from(byAnio.keys())
    .filter(a => a <= currentYear)
    .sort((a, b) => a - b)
    .slice(-CHART_YEARS);

  const totales = anios.map(a => buckets.reduce((s, b) => s + (byAnio.get(a)[b] || 0), 0));
  const maxTotal = Math.max(...totales, 1);

  const chart = $('chart');
  const labels = $('chart-labels');
  chart.innerHTML = '';
  labels.innerHTML = '';

  anios.forEach((anio, i) => {
    const datos = byAnio.get(anio);
    const col = document.createElement('div');
    col.className = 'chart-col';
    col.style.height = `${(totales[i] / maxTotal) * 100}%`;
    col.title = `${anio}: ${totales[i]} avisos`;

    buckets.forEach((bucket, bi) => {
      const n = datos[bucket] || 0;
      if (!n) return;
      const seg = document.createElement('div');
      seg.className = 'chart-col-segment';
      seg.style.height = `${(n / totales[i]) * 100}%`;
      seg.style.background = CHART_COLORS[bi];
      col.appendChild(seg);
    });

    chart.appendChild(col);

    const label = document.createElement('div');
    label.className = 'chart-col-label';
    label.textContent = anio;
    labels.appendChild(label);
  });

  $('chart-legend').innerHTML = buckets.map((b, i) => `
    <span class="chart-legend-item">
      <span class="chart-legend-swatch" style="background:${CHART_COLORS[i]}"></span>
      ${escHtml(b)}
    </span>`).join('');
}

// ── Búsqueda ──────────────────────────────────────────────────────────────────

function search() {
  if (!db) return;
  const q = $('q').value.trim();
  const tipo = $('tipo').value;
  const anio = $('anio').value;

  if (!q && !tipo && !anio) {
    $('result-count').textContent = '';
    $('result-list').innerHTML = '';
    return;
  }

  const filtros = [];
  const params = [];
  if (tipo) { filtros.push('e.tipo_juicio = ?'); params.push(tipo); }
  if (anio) { filtros.push('e.anio = ?'); params.push(Number(anio)); }

  let sql;

  if (q) {
    // FTS5: escapar comillas dobles en la query
    const ftsQ = q.replace(/"/g, '""');
    sql = `
      SELECT e.boletin_id, e.actora, e.demandada, e.tipo_juicio, e.expediente,
             e.juzgado, e.sala, b.fecha, b.pdf_url
      FROM entradas e
      JOIN entradas_fts f ON e.id = f.rowid
      LEFT JOIN boletines b ON e.boletin_id = b.id
      WHERE entradas_fts MATCH ?
      ${filtros.map(f => 'AND ' + f).join(' ')}
      LIMIT ${MAX_ROWS}
    `;
    params.unshift(ftsQ);
  } else {
    sql = `
      SELECT e.boletin_id, e.actora, e.demandada, e.tipo_juicio, e.expediente,
             e.juzgado, e.sala, b.fecha, b.pdf_url
      FROM entradas e
      LEFT JOIN boletines b ON e.boletin_id = b.id
      WHERE ${filtros.join(' AND ')}
      LIMIT ${MAX_ROWS}
    `;
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

// ── Onboarding ────────────────────────────────────────────────────────────────

const ABOUT_SEEN_KEY = 'boletin-about-seen';

function openAbout() { $('about-overlay').classList.add('open'); }
function closeAbout() {
  $('about-overlay').classList.remove('open');
  localStorage.setItem(ABOUT_SEEN_KEY, '1');
}

if (!localStorage.getItem(ABOUT_SEEN_KEY)) openAbout();

$('about-btn').addEventListener('click', openAbout);
$('about-close-btn').addEventListener('click', closeAbout);
$('about-overlay').addEventListener('click', e => {
  if (e.target.id === 'about-overlay') closeAbout();
});

// ── Eventos ───────────────────────────────────────────────────────────────────

$('search-btn').addEventListener('click', search);
$('q').addEventListener('keydown', e => { if (e.key === 'Enter') search(); });

// ── Init ──────────────────────────────────────────────────────────────────────

loadDb().catch(err => {
  setStatus('Error cargando la base de datos: ' + err.message);
  console.error(err);
});
