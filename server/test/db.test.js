const test = require('node:test');
const assert = require('node:assert/strict');
const { buildFixtureDb } = require('./fixtures');

process.env.DB_PATH = buildFixtureDb();
const db = require('../lib/db');

test('getTipos ordena por frecuencia', () => {
  const tipos = db.getTipos();
  assert.ok(tipos.includes('Sucesorio Familiar'));
  assert.ok(tipos.includes('Ordinario Civil'));
  assert.ok(!tipos.includes(null));
});

test('getAnios ordena descendente', () => {
  const anios = db.getAnios();
  assert.deepEqual(anios, [...anios].sort((a, b) => b - a));
  assert.ok(anios.includes(2025));
  assert.ok(anios.includes(2016));
});

test('searchEntradas por texto encuentra por nombre (FTS)', () => {
  const rows = db.searchEntradas({ q: 'thebart' }).rows;
  // 3 filas: 2 avisos de Esquivel Thebart como actora + 1 como demandada.
  assert.equal(rows.length, 3);
});

test('searchEntradas por tipo filtra exacto', () => {
  const rows = db.searchEntradas({ tipo: 'Ordinario Civil' }).rows;
  assert.equal(rows.length, 1);
  assert.equal(rows[0].expediente, '722/2016');
});

test('searchEntradas por año filtra exacto', () => {
  const rows = db.searchEntradas({ anio: 2024 }).rows;
  assert.equal(rows.length, 1);
  assert.equal(rows[0].actora, 'García Pérez Juan');
});

test('searchEntradas combina texto + tipo + año', () => {
  const rows = db.searchEntradas({ q: 'thebart', tipo: 'Sucesorio Familiar', anio: 2025 }).rows;
  assert.equal(rows.length, 2);
});

test('searchEntradas sin ningún filtro devuelve vacío', () => {
  const rows = db.searchEntradas({}).rows;
  assert.equal(rows.length, 0);
});

test('groupByExpediente agrupa avisos del mismo caso (juzgado+expediente)', () => {
  const rows = db.searchEntradas({ q: 'thebart' }).rows;
  const groups = db.groupByExpediente(rows);
  // Debe haber 2 expedientes: 184/2025 (2 avisos fusionados) y 722/2016 (1 aviso).
  assert.equal(groups.length, 2);
  const g184 = groups.find(g => g.expediente === '184/2025');
  assert.equal(g184.avisos.length, 2);
  // La fila representativa debe ser la del boletin_id más alto (más reciente).
  assert.equal(g184.boletin_id, 2);
});

test('groupByExpediente no fusiona el mismo número de expediente en juzgados distintos', () => {
  // Fabricamos un escenario ad-hoc reusando filas ya cargadas más una simulada.
  const rows = [
    { id: 10, boletin_id: 1, juzgado: 'A', sala: null, expediente: '1/2020', actora: 'X' },
    { id: 11, boletin_id: 1, juzgado: 'B', sala: null, expediente: '1/2020', actora: 'Y' },
  ];
  const groups = db.groupByExpediente(rows);
  assert.equal(groups.length, 2);
});

test('getExpedienteById devuelve el expediente con todos sus avisos', () => {
  const exp = db.getExpedienteById(2); // el aviso más reciente del expediente 184/2025
  assert.ok(exp);
  assert.equal(exp.expediente, '184/2025');
  assert.equal(exp.avisos.length, 2);
  // Ordenados por boletin_id descendente (más reciente primero).
  assert.equal(exp.avisos[0].boletin_id, 2);
});

test('getExpedienteById con id inexistente devuelve null', () => {
  assert.equal(db.getExpedienteById(99999), null);
});

test('getExpedienteById cuenta cuántos avisos comparten tipo y juzgado', () => {
  const exp = db.getExpedienteById(2);
  // Sucesorio Familiar aparece en id 1 y 2 (mismo expediente, 2 avisos).
  assert.equal(exp.mismoTipo, 2);
  // TRIGÉSIMO DE LO FAMILIAR también solo en esos 2.
  assert.equal(exp.mismoJuzgado, 2);
});

test('searchEntradas también filtra por juzgado', () => {
  const rows = db.searchEntradas({ juzgado: 'CUADRAGÉSIMO DE LO CIVIL' }).rows;
  assert.equal(rows.length, 1);
  assert.equal(rows[0].expediente, '722/2016');
});

test('getYearChart arma buckets top5 + Otro y respeta el total por año', () => {
  const chart = db.getYearChart();
  assert.ok(chart.buckets.includes('Otro'));
  for (const y of chart.years) {
    const sumaSegmentos = y.segments.reduce((s, seg) => s + seg.n, 0);
    assert.equal(sumaSegmentos, y.total);
  }
});

test('getStats cuenta el total de entradas', () => {
  const stats = db.getStats();
  assert.equal(stats.totalEntradas, 6);
});

test('searchEntradas con caracteres especiales de FTS5 no rompe', () => {
  assert.doesNotThrow(() => db.searchEntradas({ q: 'thebart/2025 (sucesión): "x"' }));
  assert.equal(db.searchEntradas({ q: '///' }).rows.length, 0);
  assert.equal(db.searchEntradas({ q: 'Esquivel-Thebart' }).rows.length, 3);
});

test('getStats usa fecha_iso para el rango de fechas', () => {
  const stats = db.getStats();
  assert.equal(stats.totalEntradas, 6);
  assert.equal(stats.minFecha, '2025-06-03');
  assert.equal(stats.maxFecha, '2026-06-05');
});

test('searchEntradas por número de expediente busca por prefijo en la columna', () => {
  const rows = db.searchEntradas({ q: '184/2025' }).rows;
  assert.equal(rows.length, 2);
  assert.ok(rows.every(r => r.expediente === '184/2025'));
  assert.equal(db.searchEntradas({ q: ' 722 / 2016 ' }).rows.length, 1);
  assert.equal(db.searchEntradas({ q: '184/2025', tipo: 'Ordinario Civil' }).rows.length, 0);
});

test('getDia agrupa por juzgado en orden y filtra con q', () => {
  const dia = db.getDia('2026-06-05');
  assert.equal(dia.total, 3); // boletín 1: entradas 1, 3 y 6
  assert.deepEqual(dia.grupos.map(g => g.nombre), ['TRIGÉSIMO DE LO FAMILIAR', 'CUADRAGÉSIMO DE LO CIVIL', 'DÉCIMO DE LO CIVIL']);
  assert.equal(dia.grupos[0].rows[0].pagina, 3);
  assert.equal(dia.grupos[0].rows[0].pdf_url, 'https://example.org/boletin1.pdf');
  const filtrado = db.getDia('2026-06-05', { q: 'tello' });
  assert.equal(filtrado.grupos.length, 1);
  assert.equal(filtrado.grupos[0].rows[0].expediente, '722/2016');
  assert.equal(db.getDia('2030-01-01').total, 0);
});

test('getEmpresas lista, busca por prefijo y ordena', () => {
  const todas = db.getEmpresas({});
  assert.equal(todas.total, 2);
  assert.equal(todas.rows[0].slug, 'constructora-ejemplo'); // más avisos primero
  assert.equal(db.getEmpresas({ q: 'constr' }).total, 1);
  assert.equal(db.getEmpresas({ q: 'banco ejem' }).rows[0].slug, 'banco-ejemplo');
  assert.equal(db.getEmpresas({ orden: 'nombre' }).rows[0].slug, 'banco-ejemplo');
});

test('getExpedientesEmpresa: contraparte solo como empresa o "persona física"', () => {
  const e = db.getEmpresa('constructora-ejemplo');
  const { rows } = db.getExpedientesEmpresa(e, 1);
  assert.equal(rows.length, 2);
  const conBanco = rows.find(r => r.expediente === '77/2025');
  assert.deepEqual(conBanco.contraEmpresas.map(c => c.slug), ['banco-ejemplo']);
  assert.equal(conBanco.contraPersonas, false); // Pérez Gómez Ana está del MISMO lado
  const conPersona = rows.find(r => r.expediente === '55/2024');
  assert.equal(conPersona.contraEmpresas.length, 0);
  assert.equal(conPersona.contraPersonas, true); // García Pérez Juan, sin nombrarlo
  assert.ok(!('actora' in conPersona) && !('demandada' in conPersona));
  assert.equal(db.getEmpresa('no-existe'), null);
});

test('searchEntradas devuelve del aviso más reciente al más viejo, con el total', () => {
  const { rows, total } = db.searchEntradas({ q: 'thebart' });
  assert.equal(total, rows.length);
  const fechas = rows.map(r => r.fecha_iso);
  assert.deepEqual(fechas, [...fechas].sort().reverse());
});
