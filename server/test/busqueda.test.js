const test = require('node:test');
const assert = require('node:assert/strict');
const b = require('../lib/busqueda');

test('número de expediente en cualquier formato', () => {
  const casos = {
    '184/2025': '184/2025', '184-2025': '184/2025', '184 / 2025': '184/2025', '184 del 2025': '184/2025',
    'exp. 184/2025': '184/2025', 'Expediente 184/2025': '184/2025', 'núm. 184/2025': '184/2025',
    '0184/2025': '184/2025', '184/25': '184/2025', '722/16': '722/2016', '722/98': '722/1998',
  };
  for (const [q, esperado] of Object.entries(casos)) {
    assert.equal(b.parsearConsulta(q).expediente, esperado, q);
    assert.deepEqual(b.parsearConsulta(q).palabras, [], `sin palabras de más en ${q}`);
  }
  const mixto = b.parsearConsulta('garcia 184/2025');
  assert.equal(mixto.expediente, '184/2025');
  assert.deepEqual(mixto.palabras, ['garcia']);
  assert.equal(b.parsearConsulta('juan garcia').expediente, null);
  assert.equal(b.parsearConsulta('184/1850').expediente, null, 'año imposible');
});

test('palabras sin acentos y la última como prefijo', () => {
  assert.deepEqual(b.parsearConsulta('José  Peña-García').palabras, ['jose', 'pena', 'garcia']);
  assert.equal(b.consultaFts(['garcia', 'gonz']), 'garcia gonz*');
  assert.equal(b.consultaFts(['gonz']), 'gonz', 'una sola palabra va exacta');
  assert.equal(b.consultaFts(['de', 'la']), 'de la', 'prefijo solo con 3 letras o más');
  assert.equal(b.consultaFts([]), null);
});

test('mismo lado del juicio: todas las palabras en la actora o en la demandada', () => {
  const f = b.filtroMismoLado(['juan', 'garcia']);
  assert.ok(f({ actora: 'García López Juan', demandada: 'Banco X S.A.' }));
  assert.ok(f({ actora: 'Banco X S.A.', demandada: 'Juan García Pérez' }));
  assert.ok(!f({ actora: 'Díaz García Sara', demandada: 'Juan Pérez' }), 'palabras repartidas');
  assert.ok(b.filtroMismoLado(['garcia', 'gonz'])({ actora: 'García González Ana', demandada: '' }), 'prefijo');
  assert.equal(b.filtroMismoLado(['garcia']), null, 'con una palabra no filtra');
});
