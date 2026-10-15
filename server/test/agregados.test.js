// Mismo resultado con los agregados precalculados (build_db.py
// --agregados-empresas) que con las consultas en vivo. El SQL se lee de
// build_db.py para no mantener una copia que diverja.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const Database = require('better-sqlite3');
const { buildFixtureDb } = require('./fixtures');

const dbPath = buildFixtureDb();
// Un aviso más de la constructora en un boletín de 2026 con expediente de
// 2016: el año del boletín y el del expediente no coinciden (así se rompía
// empresas_anios agrupando por la columna "anio").
{
  const w = new Database(dbPath);
  w.exec("INSERT INTO entradas_empresas VALUES (1, 3, 'demandada')");
  w.close();
}
const buildDb = fs.readFileSync(path.join(__dirname, '..', '..', 'scraper', 'build_db.py'), 'utf8');
const sql = buildDb.match(/SQL_AGREGADOS_EMPRESAS = """([\s\S]*?)"""/)[1];

// Resultado en vivo, antes de crear los agregados.
process.env.DB_PATH = dbPath;
const db = require('../lib/db');
const empresaVivo = db.getEmpresa('constructora-ejemplo');
const expVivo = db.getExpedientesEmpresa(empresaVivo, 1).rows;

const w = new Database(dbPath);
w.exec(sql);
w.close();
db.reloadDb();

test('los agregados precalculados dan lo mismo que las consultas en vivo', () => {
  const e = db.getEmpresa('constructora-ejemplo');
  assert.deepEqual(e.tipos, empresaVivo.tipos);
  assert.deepEqual(e.porAnio, empresaVivo.porAnio);
  assert.deepEqual(e.porAnio, [{ anio: '2025', n: 1 }, { anio: '2026', n: 2 }]);
  const exp = db.getExpedientesEmpresa(e, 1).rows;
  assert.deepEqual(exp, expVivo);
  assert.equal(exp.find(r => r.expediente === '55/2024').contraPersonas, true);
  // Más reciente primero por FECHA del boletín, no por id.
  assert.deepEqual(exp.map(r => r.fecha_iso), [...exp.map(r => r.fecha_iso)].sort().reverse());
});
