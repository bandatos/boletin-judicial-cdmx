const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { buildFixtureDb, buildFixtureScraperData } = require('./fixtures');

process.env.DB_PATH = buildFixtureDb();
process.env.SCRAPER_DATA_DIR = buildFixtureScraperData();
process.env.NODE_ENV = 'development'; // evita minificación en el test de estáticos
const { requestListener } = require('../lib/router');

let server;
let base;

test.before(async () => {
  server = http.createServer(requestListener);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => server.close());

async function get(pathAndQuery) {
  const res = await fetch(base + pathAndQuery);
  const body = await res.text();
  return { status: res.status, body };
}

test('GET / responde 200 y trae el formulario de búsqueda', async () => {
  const { status, body } = await get('/buscar');
  assert.equal(status, 200);
  assert.match(body, /Boletín Judicial CDMX/);
  assert.match(body, /<form id="search-box"/);
  assert.match(body, /name="q"/);
});

test('GET /?q=... hace la búsqueda server-side y agrupa por expediente', async () => {
  const { status, body: html } = await get('/buscar?q=thebart');
  assert.equal(status, 200);
  assert.match(html, /<mark>Thebart<\/mark>/);
  const body = html.replace(/<\/?mark>/g, '');
  assert.match(body, /2 expedientes/);
  assert.match(body, /Esquivel Thebart Alejandro/);
  assert.match(body, /5 avisos|2 avisos/); // el detail de "N avisos" para el grupo con 2
});

test('GET /?tipo=... filtra por tipo exacto', async () => {
  const { body } = await get('/buscar?tipo=' + encodeURIComponent('Ordinario Civil'));
  assert.match(body, /722\/2016/);
  // El tag del resultado debe ser "Ord. Civil"; "Sucesorio Familiar" solo
  // puede aparecer como <option> del dropdown, nunca como resultado (tag).
  assert.doesNotMatch(body, /class="tag">Sucesorio Familiar</);
});

test('GET /?anio=... filtra por año', async () => {
  const { body } = await get('/buscar?anio=2024');
  assert.match(body, /García Pérez Juan/);
  assert.doesNotMatch(body, /Esquivel Thebart/);
});

test('GET /buscar sin filtros no muestra resultados (estado inicial)', async () => {
  const { body } = await get('/buscar');
  assert.doesNotMatch(body, /id="result-list"/);
});

test('GET /expediente/:idslug muestra el detalle con todos los avisos', async () => {
  const { status, body } = await get('/expediente/2-esquivel-thebart-alejandro');
  assert.equal(status, 200);
  assert.match(body, /184\/2025/);
  assert.match(body, /Avisos \(2\)/);
});

test('GET /expediente/:id sin slug también funciona (parseInt ignora lo que sigue)', async () => {
  const { status, body } = await get('/expediente/2');
  assert.equal(status, 200);
  assert.match(body, /184\/2025/);
});

test('GET /expediente/:idslug con id inexistente devuelve 404', async () => {
  const { status, body } = await get('/expediente/999999-lo-que-sea');
  assert.equal(status, 404);
  assert.match(body, /No encontramos esa página/);
});

test('GET /expediente/:idslug muestra el PDF embebido y links relacionados', async () => {
  const { body } = await get('/expediente/2');
  // Abre en la página del aviso (fixtures: pagina = id + 2).
  assert.match(body, /<embed src="https:\/\/example\.org\/boletin2\.pdf#page=4&amp;toolbar=0"/);
  assert.match(body, /Juicios de tipo Sucesorio Familiar/);
  // Número para copiar, aclaración del contenido y qué hacer con el número.
  assert.match(body, /class="boton-copiar"/);
  assert.match(body, /no qué se decidió/);
  assert.match(body, /Qué hacer con este número/);
  assert.match(body, /todo-derecho-vecino/);
  assert.match(body, /Otros casos de TRIGÉSIMO DE LO FAMILIAR/);
});

test('el header es igual en todas las páginas (salvo el ítem activo del menú)', async () => {
  const header = async path => (await get(path)).body
    .match(/<header class="site-header">[\s\S]*?<\/header>/)[0]
    .replace(/ class="(activo)?"/g, '');
  const home = await header('/');
  for (const path of ['/buscar', '/expediente/2', '/fecha', '/sources']) {
    assert.equal(await header(path), home, `header distinto en ${path}`);
  }
});

test('todas las páginas traen el footer', async () => {
  for (const path of ['/', '/expediente/2', '/sources']) {
    const { body } = await get(path);
    assert.match(body, /class="site-footer"/, `falta footer en ${path}`);
  }
});

test('GET /sources explica el origen de los datos y las limitaciones', async () => {
  const { status, body } = await get('/sources');
  assert.equal(status, 200);
  assert.match(body, /Boletín Judicial/);
  assert.match(body, /Información básica/);
  assert.match(body, /class="breadcrumbs"/);
  assert.match(body, /No incluye/);
  assert.match(body, /Domicilios, RFC/);
  assert.match(body, /Empresas identificadas/);
  assert.match(body, /\d+(\.\d)?% de los avisos queda clasificado/);
  assert.match(body, /Conjunto de datos original/);
});

test('GET /static/css/main.css sirve el CSS', async () => {
  const { status, body } = await get('/static/css/main.css');
  assert.equal(status, 200);
  assert.match(body, /\.chart-box/);
});

test('GET a una ruta desconocida devuelve 404', async () => {
  const { status } = await get('/no-existe');
  assert.equal(status, 404);
});

test('en producción, /static sirve la versión minificada y la cachea a disco', async () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const minPath = path.join(__dirname, '..', 'static', 'css', 'main.min.css');
  fs.rmSync(minPath, { force: true });

  process.env.NODE_ENV = 'production';
  try {
    const { status, body } = await get('/static/css/main.css');
    assert.equal(status, 200);
    assert.ok(fs.existsSync(minPath), 'debería haber generado el .min.css');
    const original = fs.readFileSync(path.join(__dirname, '..', 'static', 'css', 'main.css'), 'utf8');
    assert.ok(body.length < original.length, 'el minificado debería pesar menos');
  } finally {
    process.env.NODE_ENV = 'development';
    fs.rmSync(minPath, { force: true });
  }
});

test('num() formatea con separador de miles (es-MX)', () => {
  const { pugLocals } = require('../lib/views');
  assert.equal(pugLocals.num(1234567), '1,234,567');
  assert.equal(pugLocals.num(500), '500');
});

test('GET /expediente/:id muestra un extracto del texto original leído de disco', async () => {
  const { body } = await get('/expediente/2');
  assert.match(body, /class="raw-text"/);
  assert.match(body, /Sucesorio Familiar 1 Acdo\. Núm\. Exp\. 184\/2025\./);
  // No debe filtrarse el texto de otro aviso del mismo archivo.
  assert.doesNotMatch(body, /999\/2020/);
});

test('GET /juzgado/:slug lista los expedientes de ese juzgado', async () => {
  const { status, body } = await get('/juzgado/trigesimo-de-lo-familiar');
  assert.equal(status, 200);
  assert.match(body, /TRIGÉSIMO DE LO FAMILIAR/);
  assert.match(body, /184\/2025/);
  assert.doesNotMatch(body, /722\/2016/); // es de otro juzgado
});

test('GET /juzgado/:slug con slug inexistente devuelve 404', async () => {
  const { status } = await get('/juzgado/no-existe');
  assert.equal(status, 404);
});

test('GET /juzgado/:slug + ?q= combina el juzgado con más filtros', async () => {
  const { body } = await get('/juzgado/trigesimo-de-lo-familiar?q=thebart');
  assert.match(body, /184\/2025/);
});

test('el link "otros casos del mismo juzgado" apunta a /juzgado/:slug', async () => {
  const { body } = await get('/expediente/2');
  assert.match(body, /href="\/juzgado\/trigesimo-de-lo-familiar"/);
});

test('noindex en fichas, juzgados y búsquedas, no en la portada', async () => {
  const noindex = /<meta name="robots" content="noindex">/;
  assert.doesNotMatch((await get('/')).body, noindex);
  assert.match((await get('/buscar?q=thebart')).body, noindex);
  assert.match((await get('/expediente/1')).body, noindex);
  assert.match((await get('/juzgado/trigesimo-de-lo-familiar')).body, noindex);
});

test('GET /fecha abre el último boletín con calendario y órganos', async () => {
  const { status, body } = await get('/fecha');
  assert.equal(status, 200);
  assert.match(body, /viernes 5 de junio de 2026/);
  assert.match(body, /class="cal-day selected"/);
  assert.match(body, /TRIGÉSIMO DE LO FAMILIAR/);
  assert.match(body, /<meta name="robots" content="noindex">/);
});

test('GET /fecha/:iso?organo= muestra los avisos con link al PDF en su página', async () => {
  const { body } = await get('/fecha/2026-06-05?organo=trigesimo-de-lo-familiar');
  assert.match(body, /Esquivel Thebart Alejandro/);
  assert.match(body, /boletin1\.pdf#page=3/);
  assert.match(body, /ver en PDF \(p\. 3\)/);
  assert.doesNotMatch(body, /Tello de Meneses/);
});

test('GET /fecha/:iso?q= filtra en todo el día; día sin boletín y fecha inválida', async () => {
  const { body } = await get('/fecha/2026-06-05?q=tello');
  assert.match(body, /Tello de Meneses/);
  assert.doesNotMatch(body, /Esquivel Thebart Alejandro<\/b>/);
  const vacio = await get('/fecha/2026-06-06');
  assert.equal(vacio.status, 200);
  assert.match(vacio.body, /No hay boletín del sábado 6 de junio de 2026/);
  assert.equal((await get('/fecha/no-es-fecha')).status, 404);
});

test('GET /?q=NNN/AAAA busca por expediente y las fechas llevan al día', async () => {
  const { body } = await get('/buscar?q=' + encodeURIComponent('184/2025'));
  assert.match(body, /1 expediente\b/);
  assert.match(body, /href="\/fecha\/2026-06-05"/);
});

test('GET /fecha con desplegables ?a=&m= muestra ese mes', async () => {
  const { body } = await get('/fecha/2026-06-05?a=2026&m=3');
  assert.match(body, /<option value="3" selected>marzo<\/option>/);
  assert.match(body, /<option value="2026" selected>2026<\/option>/);
  assert.match(body, /href="\/fecha\/2026-03-20"/); // boletín 2 del fixture
  // El día elegido no cambia: se sigue viendo el 5 de junio.
  assert.match(body, /viernes 5 de junio de 2026/);
});

const PERSONAS_FIXTURE = /García Pérez Juan|Pérez Gómez Ana|Esquivel Thebart|Tello de Meneses|Ramírez Sin Tipo/;

test('GET /empresas lista empresas, indexable y sin nombres de personas', async () => {
  const { status, body } = await get('/empresas');
  assert.equal(status, 200);
  assert.match(body, /href="\/empresas\/constructora-ejemplo"/);
  assert.match(body, /Banco Ejemplo S\.A\./);
  assert.doesNotMatch(body, /<meta name="robots" content="noindex">/);
  assert.doesNotMatch(body, PERSONAS_FIXTURE);
  const filtro = await get('/empresas?q=banco');
  assert.doesNotMatch(filtro.body, /href="\/empresas\/constructora-ejemplo"/);
});

test('GET /empresas/:slug muestra expedientes sin nombrar personas', async () => {
  const { status, body } = await get('/empresas/constructora-ejemplo');
  assert.equal(status, 200);
  assert.match(body, /Exp\. 55\/2024/);
  assert.match(body, /Exp\. 77\/2025/);
  assert.match(body, /persona\(s\) física\(s\)/);
  assert.match(body, /href="\/empresas\/banco-ejemplo"/);
  assert.match(body, /boletin3\.pdf#page=6/);
  assert.doesNotMatch(body, /<meta name="robots" content="noindex">/);
  assert.doesNotMatch(body, PERSONAS_FIXTURE);
  assert.equal((await get('/empresas/no-existe')).status, 404);
});

test('la ficha de expediente linkea a las empresas del caso', async () => {
  const { body } = await get('/expediente/4');
  assert.match(body, /href="\/empresas\/constructora-ejemplo"/);
});

test('la ficha lee el texto original también de .txt.gz', async () => {
  const { body } = await get('/expediente/4');
  assert.match(body, /Núm\. Exp\. 55\/2024/);
  assert.match(body, /class="raw-text"/);
});

test('GET / es la portada: buscador general, tablas y sin nombres de personas', async () => {
  const { status, body } = await get('/');
  assert.equal(status, 200);
  assert.match(body, /<h1>¿No sabes el número de expediente\?<\/h1>/);
  assert.match(body, /<form method="GET" action="\/buscar"/);
  assert.match(body, /href="\/empresas\/constructora-ejemplo"/);
  assert.match(body, /href="\/fecha\/2026-06-05"/);
  assert.match(body, /href="\/juzgado\/decimo-de-lo-civil"/);
  assert.match(body, /role="tablist"/);
  assert.doesNotMatch(body, /<meta name="robots" content="noindex">/);
  assert.doesNotMatch(body, PERSONAS_FIXTURE);
});
test('todas las páginas llevan la marca de Bandatos y ninguna la de Sociedad.info', async () => {
  for (const path of ['/', '/buscar', '/buscar?q=thebart', '/fecha', '/sources', '/expediente/2', '/empresas/constructora-ejemplo', '/personas']) {
    const { body } = await get(path);
    assert.match(body, /bandatos-logo\.webp/, `falta el logo en ${path}`);
    assert.match(body, /href="https:\/\/bandatos\.org"/, `falta el link en ${path}`);
    assert.doesNotMatch(body, /sociedad\.?info/i, `aparece Sociedad.info en ${path}`);
  }
});

test('la portada tiene las cifras como links en la caja del buscador y los botones afuera', async () => {
  const { body } = await get('/');
  const caja = body.match(/<section class="infobanner">[^]*?<\/section>/)[0];
  assert.match(caja, /<a href="\/empresas">2 empresas<\/a>/);
  assert.match(caja, /<a href="\/personas">2 personas<\/a>/);
  assert.match(caja, /<a href="\/fecha">boletines de 2025 a 2026<\/a>/);
  assert.doesNotMatch(caja, /acciones-portada/);
  const botones = body.match(/<nav class="acciones-portada"[^]*?<\/nav>/)[0];
  for (const href of ['/empresas', '/personas', '/fecha', '/sources']) {
    assert.match(botones, new RegExp(`href="${href}"`));
  }
});
test('GET /personas lista personas, siempre con noindex', async () => {
  const { status, body } = await get('/personas');
  assert.equal(status, 200);
  assert.match(body, /<meta name="robots" content="noindex">/);
  assert.match(body, /href="\/personas\/esquivel-thebart-alejandro"/);
  const filtro = await get('/personas?q=garcia');
  assert.match(filtro.body, /<meta name="robots" content="noindex">/);
  assert.match(filtro.body, /García Pérez Juan/);
  assert.doesNotMatch(filtro.body, /Esquivel Thebart/);
});

test('GET /personas/:slug muestra sus expedientes como actora y demandada, con noindex', async () => {
  const { status, body } = await get('/personas/esquivel-thebart-alejandro');
  assert.equal(status, 200);
  assert.match(body, /<meta name="robots" content="noindex">/);
  assert.match(body, /Exp\. 184\/2025/);
  assert.match(body, /Exp\. 722\/2016/); // como demandada ("su Sucesión")
  assert.doesNotMatch(body, /Exp\. 55\/2024/);
  assert.equal((await get('/personas/no-existe')).status, 404);
});

test('GET /health responde 200', async () => {
  const res = await fetch(base + '/health');
  assert.equal(res.status, 200);
  assert.equal(await res.text(), 'ok');
});

test('las imágenes se sirven byte a byte (sin corromperlas como texto)', async () => {
  const res = await fetch(base + '/static/img/bandatos-logo.webp');
  assert.equal(res.status, 200);
  assert.equal(res.headers.get('content-type'), 'image/webp');
  const bytes = Buffer.from(await res.arrayBuffer());
  const original = require('fs').readFileSync(require('path').join(__dirname, '..', 'static', 'img', 'bandatos-logo.webp'));
  assert.ok(bytes.equals(original));
});

test('el paginador muestra primera, ventana, "…" y última, con la actual marcada', () => {
  const { renderTemplate } = require('../lib/views');
  const html = renderTemplate('personas', {
    req: {}, q: '', lista: { rows: [], total: 1000, pagina: 10, paginas: 20 },
  });
  const nav = html.match(/<nav class="pagesContainer[^]*?<\/nav>/)[0];
  const paginas = [...nav.matchAll(/>(\d+)<\/a>/g)].map(m => Number(m[1]));
  assert.deepEqual(paginas, [1, 9, 10, 11, 12, 20]);
  assert.match(nav, /aria-current="page"[^>]*>10</);
  assert.match(nav, /href="\/personas\?p=9"[^>]*rel="prev"/);
  assert.equal((nav.match(/page-ellipsis/g) || []).length, 2);
});

test('el paginador sin total conocido muestra la siguiente pero no la última', () => {
  const { renderTemplate } = require('../lib/views');
  const html = renderTemplate('parte', {
    req: {}, clase: 'persona', parte: { nombre: 'X', slug: 'x', avisos: 1, como_actora: 1, como_demandada: 0 },
    totalExpedientes: null, chart: null, tipos: null,
    expedientes: { grupos: [], pagina: 3, hayMas: true, paginas: null },
  });
  const nav = html.match(/<nav class="pagesContainer[^]*?<\/nav>/)[0];
  assert.match(nav, /href="\/personas\/x\?p=4"[^>]*rel="next"/);
  assert.doesNotMatch(nav, /Última página/);
  assert.match(html, /150\+/);
});

test('el conteo de una búsqueda grande dice "más de 5,000" y pagina sin NaN', () => {
  const { renderTemplate } = require('../lib/views');
  const db = require('../lib/db');
  const html = renderTemplate('index', {
    req: {}, q: 'x', tipo: '', anio: '', tipos: [], anios: [], chart: db.getYearChart(), searched: true,
    resultado: { grupos: [], pagina: 2, hayMas: true, totalExpedientes: null, totalAvisos: null },
    groups: [], hrefPagina: n => `/buscar?q=x&p=${n}`, formAction: '/buscar', heading: null,
    candidatos: db.CANDIDATOS, porPagina: db.EXPEDIENTES_POR_PAGINA,
  });
  assert.match(html, /Más de 5,000 avisos/);
  assert.doesNotMatch(html, /NaN/);
  assert.match(html, /href="\/buscar\?q=x&amp;p=3"/);
});

test('GET /buscar con resultados no muestra NaN', async () => {
  const { body } = await get('/buscar?q=thebart');
  assert.doesNotMatch(body, /NaN/);
});

test('empresa y persona usan la misma ficha: resumen con pestañas y expedientes', async () => {
  const empresa = (await get('/empresas/constructora-ejemplo')).body;
  const persona = (await get('/personas/esquivel-thebart-alejandro')).body;
  for (const body of [empresa, persona]) {
    assert.match(body, /class="tarjeta-titulo"/);
    assert.match(body, /Expedientes/);
    assert.match(body, /Del más reciente al más viejo/);
  }
  assert.match(persona, /id="tab-resumen-tipos"/);
  assert.match(persona, /<meta name="robots" content="noindex">/);
  assert.doesNotMatch(empresa, /<meta name="robots" content="noindex">/);
  assert.doesNotMatch(empresa, PERSONAS_FIXTURE);
});

test('CSS inline en el head, sin pedir main.css aparte', async () => {
  const { body } = await get('/');
  assert.match(body, /<style>[^<]*\.tarjeta\{[^<]*<\/style>/);
  assert.doesNotMatch(body, /href="\/static\/css\/main\.css/);
});

test('assets con versión por hash, cacheados como inmutables', async () => {
  const { body } = await get('/');
  const logo = body.match(/src="(\/static\/img\/bandatos-logo\.webp\?v=[0-9a-f]{10})"/)[1];
  assert.match(body, /<script src="\/static\/js\/behavior\.js\?v=[0-9a-f]{10}" defer>/);
  const res = await fetch(base + logo);
  assert.equal(res.headers.get('cache-control'), 'public, max-age=31536000, immutable');
  const sinVersion = await fetch(base + '/static/img/bandatos-logo.webp');
  assert.equal(sinVersion.headers.get('cache-control'), 'public, max-age=3600');
});

test('el HTML lleva ETag y responde 304 si no cambió', async () => {
  const res = await fetch(base + '/sources');
  const etag = res.headers.get('etag');
  assert.ok(etag);
  assert.equal(res.headers.get('cache-control'), 'public, max-age=300');
  const again = await fetch(base + '/sources', { headers: { 'If-None-Match': etag } });
  assert.equal(again.status, 304);
});

test('accesibilidad: main, salto al contenido, un h1, selects con nombre y sin envío automático', async () => {
  for (const ruta of ['/', '/buscar', '/buscar?q=thebart', '/fecha', '/empresas', '/personas', '/sources', '/empresas/constructora-ejemplo']) {
    const { body } = await get(ruta);
    assert.match(body, /<main id="contenido">/, `main en ${ruta}`);
    assert.match(body, /<a class="saltar" href="#contenido">/, `salto en ${ruta}`);
    assert.equal((body.match(/<h1[ >]/g) || []).length, 1, `un h1 en ${ruta}`);
    assert.doesNotMatch(body, /onchange="this\.form\.submit\(\)"/, `envío automático en ${ruta}`);
  }
  const buscar = (await get('/buscar')).body;
  assert.match(buscar, /<select id="tipo" name="tipo" aria-label="Tipo de juicio">/);
  assert.match(buscar, /class="chart" role="img" aria-label="[^"]*\d{4}: /);
});

test('con una búsqueda no se muestra el gráfico general; sin resultados hay sugerencias', async () => {
  assert.doesNotMatch((await get('/buscar?q=thebart')).body, /class="chart-box"/);
  assert.match((await get('/buscar')).body, /class="chart-box"/);
  assert.match((await get('/buscar?q=zzzqqq')).body, /Busca solo un apellido/);
});

test('búsqueda: expediente en otros formatos y combinado con un nombre', async () => {
  for (const q of ['184-2025', 'exp. 184/2025', 'expediente 0184/2025', '184/25']) {
    const body = (await get('/buscar?' + new URLSearchParams({ q }))).body;
    assert.match(body, /Exp\. 184\/2025/, q);
  }
  const mixto = (await get('/buscar?' + new URLSearchParams({ q: 'esquivel 184/2025' }))).body;
  assert.match(mixto, /Exp\. 184\/2025/);
  assert.doesNotMatch(mixto, /Exp\. 722\/2016/);
});

test('búsqueda: fichas que coinciden, prefijo y palabras del mismo lado', async () => {
  const body = (await get('/buscar?q=garcia')).body;
  assert.match(body, /Fichas que coinciden/);
  assert.match(body, /href="\/personas\/garcia-perez-juan"/);
  assert.match((await get('/buscar?' + new URLSearchParams({ q: 'esquivel theb' }))).body, /Exp\. 184\/2025/, 'prefijo de la última palabra');
  // "tello thebart": cada palabra está en un lado distinto del juicio 722/2016;
  // como ningún aviso las tiene juntas, se avisa y se muestran igual.
  const repartidas = (await get('/buscar?' + new URLSearchParams({ q: 'tello thebart' }))).body;
  assert.match(repartidas, /Ningún aviso tiene todas esas palabras del mismo lado/);
  assert.match(repartidas, /Exp\. 722\/2016/);
  const juntas = (await get('/buscar?' + new URLSearchParams({ q: 'esquivel thebart' }))).body;
  assert.doesNotMatch(juntas, /Ningún aviso tiene todas esas palabras del mismo lado/);
});

test('¿Quisiste decir…? ante una errata, y nada si la búsqueda está bien escrita', async () => {
  const errata = (await get('/buscar?' + new URLSearchParams({ q: 'esquivel thebrat' }))).body;
  assert.match(errata, /¿Quisiste decir/);
  assert.match(errata, /href="\/buscar\?q=esquivel\+thebart">«esquivel thebart»<\/a>/);
  assert.doesNotMatch((await get('/buscar?q=thebart')).body, /¿Quisiste decir/);
  assert.match((await get('/buscar?' + new URLSearchParams({ q: 'constructra' }))).body, /«constructora»/);
});

test('pie con la fecha del último boletín cargado', async () => {
  const { body } = await get('/buscar');
  assert.match(body, /Datos hasta el boletín del/);
});

test('Mis expedientes: página noindex y API con ids numéricos y tope', async () => {
  const pagina = await get('/mis-expedientes');
  assert.equal(pagina.status, 200);
  assert.match(pagina.body, /id="mis-expedientes"/);
  assert.match(pagina.body, /name="robots" content="noindex"/);

  const res = await fetch(`${base}/api/expedientes?ids=1,abc,999999999`);
  assert.equal(res.status, 200);
  assert.match(res.headers.get('content-type'), /application\/json/);
  const datos = await res.json();
  assert.ok(datos.hasta);
  assert.equal(datos.expedientes.length, 2);
  assert.equal(datos.expedientes[0].encontrado, true);
  assert.match(datos.expedientes[0].href, /^\/expediente\/1-/);
  assert.equal(datos.expedientes[1].encontrado, false);
});

test('maqueta de alertas: el correo nunca se envía y la vista previa usa el expediente', async () => {
  const ficha = await get('/expediente/1');
  const campo = ficha.body.match(/<input id="alerta-correo"[^>]*>/)[0];
  assert.doesNotMatch(campo, /name=/);
  const previa = await get('/alertas/ejemplo?id=1');
  assert.equal(previa.status, 200);
  assert.match(previa.body, /vista previa/);
  assert.match(previa.body, /no es una notificación oficial/);
  assert.equal((await get('/alertas/ejemplo?id=x')).status, 404);
});

test('sin tablas de estadísticas (base vieja) las secciones nuevas dan 404 y no salen en el menú', async () => {
  for (const ruta of ['/salas', '/juzgados', '/tipos', '/salas/primera-sala-civil', '/tipos/ordinario-civil']) {
    assert.equal((await get(ruta)).status, 404, ruta);
  }
  const { body } = await get('/buscar');
  assert.doesNotMatch(body, /href="\/salas"/);
});
