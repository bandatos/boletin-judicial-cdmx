// search.js — Boletín Judicial CDMX
// Carga boletin.sqlite.gz (partido en trozos, ver boletin.sqlite.json) desde
// el mismo origen, lo cachea en IndexedDB y ejecuta búsquedas FTS5
// localmente con sql.js.

// GitHub rechaza archivos de más de 100 MB, así que deploy.sh parte el .gz
// en trozos y deja un manifiesto con la lista y la versión.
const DB_MANIFEST_URL = './boletin.sqlite.json';
// v2 guarda el .gz como Blob (no la DB descomprimida): ~4x menos espacio en
// IndexedDB.
const DB_CACHE_KEY = 'boletin-sqlite-v2';
const DB_CACHE_KEY_OLD = 'boletin-sqlite-v1';
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

// ── Onboarding ────────────────────────────────────────────────────────────────
// Se abre de una, antes de tocar la DB (descarga/descompresión/carga en
// sql.js), para que la persona vea qué es esto sin tener que esperar.

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

$('about-detail-btn').addEventListener('click', () => {
  const detail = $('about-detail');
  const open = detail.classList.toggle('open');
  $('about-detail-btn').setAttribute('aria-expanded', open ? 'true' : 'false');
  $('about-detail-btn').textContent = open ? 'Menos detalle' : 'Más detalle';
});

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
  if (!location.hash.startsWith('#fecha')) $('q').focus();
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
      ? `<div class="card-pdf">${pdfLinkHtml(r, 'Ver PDF del boletín')}</div>`
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
                  ${fechaLink(a)}
                  ${a.tipo_juicio ? ` — ${escHtml(a.tipo_juicio)}` : ''}
                  ${a.pdf_url ? ` — ${pdfLinkHtml(a, 'PDF')}` : ''}
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
          <span>${fechaLink(r)}</span>
        </div>
        ${otrosAvisos}
        ${pdfLink}
      </div>`;
  }).join('');
}

// Link al PDF del boletín en la página del aviso. Con clic normal se abre
// en el panel lateral (ver abrirPdf); con Ctrl/clic del medio sigue siendo
// un link común.
function pdfLinkHtml(r, texto) {
  const href = r.pagina ? `${r.pdf_url}#page=${r.pagina}` : r.pdf_url;
  const titulo = [r.fecha_iso && fechaCorta(r.fecha_iso), r.pagina && `p. ${r.pagina}`]
    .filter(Boolean).join(' · ');
  return `<a class="ver-pdf" href="${escHtml(href)}" target="_blank" rel="noopener"
            data-pdf="${escHtml(r.pdf_url)}" data-pagina="${r.pagina || ''}"
            data-titulo="${escHtml(titulo)}">${escHtml(texto)}</a>`;
}

// Link a la vista del día ("Navegar por fecha") con la fecha legible.
function fechaLink(r) {
  if (!r.fecha_iso) return escHtml(r.fecha || '—');
  return `<a href="#fecha/${r.fecha_iso}">${escHtml(fechaCorta(r.fecha_iso))}</a>`;
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
    const store = tx.objectStore('files');
    store.delete(DB_CACHE_KEY_OLD);
    const req = store.put(data, DB_CACHE_KEY);
    req.onsuccess = () => resolve();
    req.onerror = e => reject(e.target.error);
  });
}

// ── DB loading ────────────────────────────────────────────────────────────────

async function fetchManifest() {
  const res = await fetch(DB_MANIFEST_URL, { cache: 'no-cache' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  // manifest.version (hash del .gz) cambia cuando cambia la base; con esto
  // detectamos que hay una nueva y no seguimos usando la vieja cacheada para
  // siempre (le pasó justo eso: quedó pegada a una base sin la columna anio).
  return res.json();
}

async function fetchWithProgress(manifest, onProgress) {
  const total = manifest.size;
  const chunks = [];
  let received = 0;

  for (const part of manifest.parts) {
    const res = await fetch(`./${part}?v=${manifest.version}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const reader = res.body.getReader();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      received += value.length;
      if (total) onProgress(Math.round(received / total * 100));
    }
  }

  // Blob y no Uint8Array: Chrome se cuelga (sin error) al guardar en
  // IndexedDB un Uint8Array de más de ~100 MB. Un Blob lo guarda aparte, en
  // disco, sin ese límite.
  return new Blob(chunks);
}

async function decompress(blob) {
  const ds = new DecompressionStream('gzip');
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

  const cached = await getCached(idb);

  let manifest = null;
  try { manifest = await fetchManifest(); } catch (e) { /* sin red: seguimos con lo cacheado si hay */ }

  let compressed;
  if (cached && (manifest === null || cached.meta === manifest.version)) {
    compressed = cached.bytes;
    setStatus('Cargando desde caché local...', 90);
  } else {
    if (!manifest) throw new Error('No se pudo leer boletin.sqlite.json');
    setStatus('Descargando base de datos...', 0);
    compressed = await fetchWithProgress(manifest, pct => setStatus(`Descargando... ${pct}%`, pct));
    await setCached(idb, { bytes: compressed, meta: manifest.version });
  }
  setStatus('Descomprimiendo...', 99);
  const bytes = await decompress(compressed);

  setStatus('Abriendo base de datos...', 99);
  const SQL = await loadSqlJs();
  db = new SQL.Database(bytes);

  populateTipoFilter();
  populateAnioFilter();
  renderYearChart();
  renderDbVersion();
  if (!loadBoletinesIndex()) $('tab-fecha').classList.add('disabled');
  setReady();
  route();
}

function renderDbVersion() {
  let rows;
  try {
    rows = db.exec("SELECT key, value FROM meta");
  } catch (e) {
    return; // DB vieja sin tabla meta todavía
  }
  if (!rows.length) return;
  const meta = Object.fromEntries(rows[0].values.map(([k, v]) => [k, v]));
  if (!meta.commit && !meta.built_at) return;
  $('db-version').textContent =
    `DB: ${meta.commit || '?'} · generada ${meta.built_at || '?'}`;
}

// ── Filtros ───────────────────────────────────────────────────────────────────

const TIPO_OTROS = '__otros__';

function populateTipoFilter() {
  const sel = $('tipo');

  const rows = db.exec(
    "SELECT tipo_juicio_norm FROM entradas WHERE tipo_juicio_norm IS NOT NULL " +
    "GROUP BY tipo_juicio_norm ORDER BY COUNT(*) DESC LIMIT 200"
  );
  if (!rows.length) return;
  for (const [tipo] of rows[0].values) {
    const opt = document.createElement('option');
    opt.value = opt.textContent = tipo;
    sel.appendChild(opt);
  }

  // Avisos sin tipo reconocido: sin "vs." (casos unilaterales legítimos) o
  // con tipo_juicio que no matcheó ningún tipo canónico conocido.
  const otrosCount = db.exec(
    "SELECT COUNT(*) FROM entradas WHERE tipo_juicio_norm IS NULL"
  )[0]?.values?.[0]?.[0] || 0;
  if (otrosCount > 0) {
    const opt = document.createElement('option');
    opt.value = TIPO_OTROS;
    opt.textContent = `Otros / sin tipo reconocido (${otrosCount.toLocaleString('es-MX')})`;
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

const CHART_COLORS = [
  '#1a5276', '#1f618d', '#2874a6', '#2e86c1', '#3498db', '#5dade2', '#85c1e9', '#aed6f1',
  '#ccc', '#eee',
];
const CHART_TOP_N = 8;
const CHART_YEARS = 15;

function renderYearChart() {
  const topRows = db.exec(
    "SELECT tipo_juicio_norm FROM entradas WHERE tipo_juicio_norm IS NOT NULL " +
    `GROUP BY tipo_juicio_norm ORDER BY COUNT(*) DESC LIMIT ${CHART_TOP_N}`
  );
  const top = topRows.length ? topRows[0].values.map(v => v[0]) : [];
  // "Otro": tipo_juicio_norm reconocido pero fuera del top. "Sin tipo": no
  // se pudo clasificar (sin "vs." o texto no reconocido) — se muestran
  // separados para no mezclar avisos reales sin clasificar con la cola de
  // tipos canónicos menores.
  const buckets = [...top, 'Otro', 'Sin tipo'];
  const placeholders = top.map(() => '?').join(',');

  const sql = `
    SELECT anio,
      CASE
        ${top.length ? `WHEN tipo_juicio_norm IN (${placeholders}) THEN tipo_juicio_norm` : ''}
        WHEN tipo_juicio_norm IS NULL THEN 'Sin tipo'
        ELSE 'Otro'
      END AS bucket,
      COUNT(*) AS n
    FROM entradas
    WHERE anio IS NOT NULL
    GROUP BY anio, bucket
  `;
  const result = db.exec(sql, top);
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
  if (tipo === TIPO_OTROS) {
    filtros.push('e.tipo_juicio_norm IS NULL');
  } else if (tipo) {
    filtros.push('e.tipo_juicio_norm = ?');
    params.push(tipo);
  }
  if (anio) { filtros.push('e.anio = ?'); params.push(Number(anio)); }

  let sql;

  // Número de expediente ("387/2025"): no está indexado en FTS y la "/"
  // rompe la sintaxis de MATCH, así que se busca directo en la columna.
  const expedienteQ = q.match(/^\s*(\d+)\s*\/\s*(\d+)\s*$/);

  if (expedienteQ) {
    filtros.push('e.expediente LIKE ?');
    params.unshift(`${expedienteQ[1]}/${expedienteQ[2]}%`);
    sql = `
      SELECT e.boletin_id, e.actora, e.demandada, e.tipo_juicio, e.expediente,
             e.juzgado, e.sala, e.pagina, b.fecha, b.fecha_iso, b.pdf_url
      FROM entradas e
      LEFT JOIN boletines b ON e.boletin_id = b.id
      WHERE ${filtros.join(' AND ')}
      LIMIT ${MAX_ROWS}
    `;
  } else if (q) {
    // FTS5 (detail=none) no soporta phrase queries ni caracteres especiales
    // ("/", "-", "(", ":"...) fuera de un bareword: se sanean a espacios
    // para evitar errores de sintaxis y buscar cada palabra por separado
    // (AND implícito).
    const ftsQ = q.replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
    if (!ftsQ) {
      $('result-count').textContent = 'Sin resultados.';
      $('result-list').innerHTML = '';
      return;
    }
    sql = `
      SELECT e.boletin_id, e.actora, e.demandada, e.tipo_juicio, e.expediente,
             e.juzgado, e.sala, e.pagina, b.fecha, b.fecha_iso, b.pdf_url
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
             e.juzgado, e.sala, e.pagina, b.fecha, b.fecha_iso, b.pdf_url
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

// ── Navegar por fecha ────────────────────────────────────────────────────────

const MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
  'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];
const DIAS = ['domingo', 'lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado'];
// Con el filtro activo, los grupos se abren solos si hay pocos resultados;
// con muchos se muestran cerrados con su conteo (abrir 15k avisos de golpe
// traba el navegador).
const FILTRO_AUTO_ABRIR = 300;

// fecha_iso -> [{ id, pdf_url, n, desde, hasta }]
let boletinesPorFecha = null;
let fechasOrdenadas = [];
let calMes = null; // { anio, mes } con mes 1-12
let boletinActual = null; // { iso, grupos }

const pad = n => String(n).padStart(2, '0');

function isoToDate(iso) {
  const [a, m, d] = iso.split('-').map(Number);
  return new Date(a, m - 1, d);
}

function fechaCorta(iso) {
  const d = isoToDate(iso);
  return `${d.getDate()} ${MESES[d.getMonth()].slice(0, 3)}. ${d.getFullYear()}`;
}

function fechaLarga(iso) {
  const d = isoToDate(iso);
  return `${DIAS[d.getDay()]} ${d.getDate()} de ${MESES[d.getMonth()]} de ${d.getFullYear()}`;
}

function normTxt(str) {
  return (str || '').normalize('NFD').replace(/\p{M}/gu, '').toLowerCase();
}

function loadBoletinesIndex() {
  if (boletinesPorFecha) return boletinesPorFecha.size > 0;
  let res;
  try {
    res = db.exec(`
      SELECT id, fecha_iso, pdf_url, n_entradas, entrada_desde, entrada_hasta
      FROM boletines
      WHERE fecha_iso IS NOT NULL AND n_entradas > 0
      ORDER BY fecha_iso, id
    `);
  } catch (e) {
    boletinesPorFecha = new Map();
    return false; // DB vieja, sin fecha_iso / n_entradas
  }
  boletinesPorFecha = new Map();
  for (const [id, iso, pdf_url, n, desde, hasta] of (res[0]?.values || [])) {
    if (!boletinesPorFecha.has(iso)) boletinesPorFecha.set(iso, []);
    boletinesPorFecha.get(iso).push({ id, pdf_url, n, desde, hasta });
  }
  fechasOrdenadas = Array.from(boletinesPorFecha.keys());
  if (!fechasOrdenadas.length) return false;

  const primero = isoToDate(fechasOrdenadas[0]).getFullYear();
  const ultimo = isoToDate(fechasOrdenadas[fechasOrdenadas.length - 1]).getFullYear();
  $('cal-anio').innerHTML = '';
  for (let a = ultimo; a >= primero; a--) {
    $('cal-anio').insertAdjacentHTML('beforeend', `<option value="${a}">${a}</option>`);
  }
  $('cal-mes').innerHTML = MESES.map((m, i) => `<option value="${i + 1}">${m}</option>`).join('');
  return true;
}

function mesKey(anio, mes) { return anio * 12 + (mes - 1); }

function renderCalendar(selectedIso) {
  const { anio, mes } = calMes;
  $('cal-anio').value = anio;
  $('cal-mes').value = mes;

  const primero = isoToDate(fechasOrdenadas[0]);
  const ultimo = isoToDate(fechasOrdenadas[fechasOrdenadas.length - 1]);
  const actual = mesKey(anio, mes);
  $('cal-prev').disabled = actual <= mesKey(primero.getFullYear(), primero.getMonth() + 1);
  $('cal-next').disabled = actual >= mesKey(ultimo.getFullYear(), ultimo.getMonth() + 1);

  // Semana de lunes a domingo.
  const offset = (new Date(anio, mes - 1, 1).getDay() + 6) % 7;
  const diasMes = new Date(anio, mes, 0).getDate();
  const celdas = ['lun', 'mar', 'mié', 'jue', 'vie', 'sáb', 'dom']
    .map(d => `<div class="cal-dow">${d}</div>`);
  for (let i = 0; i < offset; i++) celdas.push('<div class="cal-day empty"></div>');
  for (let d = 1; d <= diasMes; d++) {
    const iso = `${anio}-${pad(mes)}-${pad(d)}`;
    const bols = boletinesPorFecha.get(iso);
    if (bols) {
      const n = bols.reduce((s, b) => s + b.n, 0);
      celdas.push(`
        <a class="cal-day${iso === selectedIso ? ' selected' : ''}" href="#fecha/${iso}"
           title="${n.toLocaleString('es-MX')} avisos">
          ${d}<span class="cal-day-n">${n.toLocaleString('es-MX')}</span>
        </a>`);
    } else {
      celdas.push(`<div class="cal-day">${d}</div>`);
    }
  }
  $('cal-grid').innerHTML = celdas.join('');
}

function moverMes(delta) {
  const k = mesKey(calMes.anio, calMes.mes) + delta;
  calMes = { anio: Math.floor(k / 12), mes: (k % 12) + 1 };
  renderCalendar(boletinActual?.iso);
}

// Agrupa las entradas de un boletín por juzgado o sala, en el orden en que
// aparecen en el PDF (entradas.id sigue ese orden).
function agruparPorJuzgado(rows) {
  const grupos = new Map();
  for (const r of rows) {
    const nombre = r.sala || r.juzgado || 'Sin juzgado identificado';
    if (!grupos.has(nombre)) grupos.set(nombre, { nombre, rows: [] });
    r._t = normTxt(`${r.actora} ${r.demandada} ${r.expediente} ${r.tipo_juicio}`);
    grupos.get(nombre).rows.push(r);
  }
  return Array.from(grupos.values());
}

function renderAvisos(rows) {
  let secretaria = null;
  return rows.map(r => {
    let sec = '';
    if (r.secretaria && r.secretaria !== secretaria) {
      secretaria = r.secretaria;
      sec = `<li class="secretaria">${escHtml(r.secretaria)}</li>`;
    }
    const partes = r.demandada
      ? `<strong>${escHtml(r.actora)}</strong> vs. ${escHtml(r.demandada)}`
      : `<strong>${escHtml(r.actora)}</strong>`;
    const meta = [
      r.tipo_juicio && escHtml(r.tipo_juicio),
      r.expediente && `Exp. ${escHtml(r.expediente)}`,
      r.num_acdos && `${r.num_acdos} acdo${r.num_acdos > 1 ? 's' : ''}.`,
      r.pdf_url && pdfLinkHtml(r, r.pagina ? `ver en PDF (p. ${r.pagina})` : 'ver PDF'),
    ].filter(Boolean).join(' · ');
    return `${sec}<li><div>${partes}</div><div class="aviso-meta">${meta}</div></li>`;
  }).join('');
}

function renderBoletin(iso) {
  const view = $('boletin-view');
  const bols = boletinesPorFecha.get(iso);
  if (!bols) {
    boletinActual = { iso, grupos: [] };
    view.innerHTML = `<p class="boletin-head"><span>No hay boletín del ${escHtml(fechaLarga(iso))}.</span></p>`;
    return;
  }

  const rows = [];
  for (const b of bols) {
    const res = db.exec(`
      SELECT juzgado, sala, secretaria, actora, demandada, tipo_juicio, expediente, num_acdos, pagina
      FROM entradas
      WHERE id BETWEEN ? AND ? AND boletin_id = ?
      ORDER BY id
    `, [b.desde, b.hasta, b.id]);
    if (!res.length) continue;
    const cols = res[0].columns;
    for (const v of res[0].values) {
      rows.push({ ...Object.fromEntries(cols.map((c, i) => [c, v[i]])), pdf_url: b.pdf_url, fecha_iso: iso });
    }
  }
  const grupos = agruparPorJuzgado(rows);
  boletinActual = { iso, grupos };

  const pdfs = bols.filter(b => b.pdf_url)
    .map(b => pdfLinkHtml({ pdf_url: b.pdf_url, fecha_iso: iso }, 'Ver PDF del boletín'))
    .join(' · ');

  view.innerHTML = `
    <div class="boletin-head">
      <h2>${escHtml(fechaLarga(iso))}</h2>
      <span>${rows.length.toLocaleString('es-MX')} avisos · ${grupos.length} juzgados y salas</span>
      ${pdfs}
    </div>
    <input id="boletin-filtro" type="search" autocomplete="off"
           placeholder="Filtrar en este boletín por nombre, expediente o tipo de juicio...">
    <div id="boletin-grupos">
      ${grupos.map((g, i) => `
        <details class="grupo" data-i="${i}">
          <summary><span>${escHtml(g.nombre)}</span><span class="grupo-n">${g.rows.length.toLocaleString('es-MX')}</span></summary>
          <ol></ol>
        </details>`).join('')}
    </div>`;

  // El contenido de cada grupo se arma recién al abrirlo: un boletín tiene
  // ~15k avisos y pintarlos todos de entrada es lento.
  view.querySelectorAll('.grupo').forEach(el => {
    el.addEventListener('toggle', () => { if (el.open) pintarGrupo(el); });
  });

  let t = null;
  $('boletin-filtro').addEventListener('input', () => {
    clearTimeout(t);
    t = setTimeout(aplicarFiltro, 200);
  });
}

function filasVisibles(g) {
  const f = normTxt($('boletin-filtro')?.value.trim());
  return f ? g.rows.filter(r => r._t.includes(f)) : g.rows;
}

function pintarGrupo(el) {
  const g = boletinActual.grupos[Number(el.dataset.i)];
  el.querySelector('ol').innerHTML = renderAvisos(filasVisibles(g));
}

function aplicarFiltro() {
  const hayFiltro = !!$('boletin-filtro').value.trim();
  const els = Array.from(document.querySelectorAll('#boletin-grupos .grupo'));
  const visibles = els.map(el => filasVisibles(boletinActual.grupos[Number(el.dataset.i)]));
  const total = visibles.reduce((s, v) => s + v.length, 0);

  els.forEach((el, i) => {
    const g = boletinActual.grupos[i];
    const n = visibles[i].length;
    el.style.display = n ? '' : 'none';
    el.querySelector('.grupo-n').textContent = hayFiltro
      ? `${n.toLocaleString('es-MX')} de ${g.rows.length.toLocaleString('es-MX')}`
      : g.rows.length.toLocaleString('es-MX');
    const abrir = hayFiltro && n > 0 && total <= FILTRO_AUTO_ABRIR;
    if (abrir || el.open) {
      el.open = abrir || (el.open && n > 0);
      if (el.open) pintarGrupo(el);
    }
  });
}

// ── Panel lateral con el PDF ─────────────────────────────────────────────────
// Para comparar lo parseado contra el original: el PDF se abre al costado,
// en la página del aviso, con el visor del navegador (el CDN del PJCDMX no
// manda X-Frame-Options, así que se puede embeber).

function abrirPdf(url, pagina, titulo) {
  const href = pagina ? `${url}#page=${pagina}` : url;
  const actual = $('pdf-frame');
  // Si solo cambia el #page, el visor de Chrome no salta de página (ni
  // cambiando el src del mismo iframe): se reemplaza el iframe por uno
  // nuevo. El PDF sale de la caché del navegador, no se vuelve a bajar.
  if (actual.dataset.href !== href) {
    const frame = document.createElement('iframe');
    frame.id = 'pdf-frame';
    frame.title = actual.title;
    frame.dataset.href = href;
    frame.src = href;
    actual.replaceWith(frame);
  }
  $('pdf-panel-title').textContent = titulo || 'PDF del boletín';
  $('pdf-panel-open').href = href;
  $('pdf-panel').hidden = false;
  document.body.classList.add('pdf-open');
}

function cerrarPdf() {
  $('pdf-panel').hidden = true;
  document.body.classList.remove('pdf-open');
  document.querySelectorAll('li.comparando').forEach(li => li.classList.remove('comparando'));
}

document.addEventListener('click', e => {
  const a = e.target.closest('a[data-pdf]');
  if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
  e.preventDefault();
  document.querySelectorAll('li.comparando').forEach(li => li.classList.remove('comparando'));
  a.closest('.grupo li')?.classList.add('comparando');
  abrirPdf(a.dataset.pdf, Number(a.dataset.pagina) || null, a.dataset.titulo);
});
$('pdf-panel-close').addEventListener('click', cerrarPdf);
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !$('pdf-panel').hidden) cerrarPdf();
});

// ── Vistas y ruteo ───────────────────────────────────────────────────────────
// "#fecha" y "#fecha/AAAA-MM-DD" abren la vista por fecha; cualquier otro
// hash, la búsqueda. Así se puede compartir el link a un día y el botón
// "atrás" del navegador funciona.

function showView(nombre) {
  const fecha = nombre === 'fecha';
  for (const id of ['search-box', 'chart-box', 'results']) $(id).classList.toggle('view-hidden', fecha);
  $('fecha-view').classList.toggle('view-hidden', !fecha);
  $('tab-buscar').classList.toggle('active', !fecha);
  $('tab-fecha').classList.toggle('active', fecha);
}

function route() {
  const m = location.hash.match(/^#fecha(?:\/(\d{4}-\d{2}-\d{2}))?$/);
  if (!m) { showView('buscar'); return; }
  showView('fecha');
  if (!db || !loadBoletinesIndex()) return; // se vuelve a llamar al terminar de cargar la DB

  const iso = m[1] || fechasOrdenadas[fechasOrdenadas.length - 1];
  const d = isoToDate(iso);
  calMes = { anio: d.getFullYear(), mes: d.getMonth() + 1 };
  renderCalendar(iso);
  if (boletinActual?.iso !== iso) renderBoletin(iso);
}

// ── Eventos ───────────────────────────────────────────────────────────────────

window.addEventListener('hashchange', route);
$('cal-prev').addEventListener('click', () => moverMes(-1));
$('cal-next').addEventListener('click', () => moverMes(1));
$('cal-mes').addEventListener('change', () => {
  calMes = { anio: calMes.anio, mes: Number($('cal-mes').value) };
  renderCalendar(boletinActual?.iso);
});
$('cal-anio').addEventListener('change', () => {
  calMes = { anio: Number($('cal-anio').value), mes: calMes.mes };
  renderCalendar(boletinActual?.iso);
});

$('search-btn').addEventListener('click', search);
$('q').addEventListener('keydown', e => { if (e.key === 'Enter') search(); });

// ── Init ──────────────────────────────────────────────────────────────────────

route();

loadDb().catch(err => {
  setStatus('Error cargando la base de datos: ' + err.message);
  console.error(err);
});
