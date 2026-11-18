// Mismo resultado con los agregados precalculados (scraper/agregados.py)
// que con las consultas en vivo. El SQL se lee del módulo para no mantener
// una copia que diverja.
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
// El SQL se lee de scraper/agregados.py: tabla temporal eh y agregados.
const agregados = fs.readFileSync(path.join(__dirname, '..', '..', 'scraper', 'agregados.py'), 'utf8');
const tablas = agregados.match(/SQL_EMPRESAS_TABLAS = """([\s\S]*?)"""/)[1]
  .split(';').filter(t => /CREATE TABLE (empresas_\w+|tipo_empresa)/.test(t))
  .map(t => t.replace('CREATE TABLE', 'CREATE TABLE IF NOT EXISTS')).join(';\n');
const hechos = agregados.match(/(CREATE TEMP TABLE eh AS[\s\S]*?)\{donde\}/)[1].replace('{fuente}', 'entradas_empresas');
const sql = `DROP TABLE IF EXISTS empresas_tipos; DROP TABLE IF EXISTS empresas_anios;
  DROP TABLE IF EXISTS empresas_meses; DROP TABLE IF EXISTS empresas_expedientes; DROP TABLE IF EXISTS tipo_empresa;
  ${tablas};\n${hechos};\n${agregados.match(/SQL_EMPRESAS_AGREGADOS = """([\s\S]*?)"""/)[1]}`;

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
