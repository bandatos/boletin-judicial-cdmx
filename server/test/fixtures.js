// fixtures.js — crea una DB SQLite temporal con EXACTAMENTE el mismo schema
// que scraper/build_db.py, para no divergir entre el sitio estático (sql.js)
// y este server (better-sqlite3) leyendo el mismo tipo de archivo.

const fs = require('fs');
const os = require('os');
const path = require('path');
const Database = require('better-sqlite3');

function buildFixtureDb() {
  const dbPath = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'litigios-test-')), 'test.sqlite');
  const db = new Database(dbPath);

  db.exec(`
    CREATE TABLE boletines (
      id            INTEGER PRIMARY KEY,
      fecha         TEXT NOT NULL,
      fecha_iso     TEXT,
      pdf_url       TEXT,
      pages         INTEGER,
      n_entradas    INTEGER NOT NULL DEFAULT 0,
      entrada_desde INTEGER,
      entrada_hasta INTEGER
    );

    CREATE TABLE entradas (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      boletin_id  INTEGER REFERENCES boletines(id),
      juzgado     TEXT,
      sala        TEXT,
      secretaria  TEXT,
      actora      TEXT,
      demandada   TEXT,
      tipo_juicio TEXT,
      tipo_juicio_norm TEXT,
      expediente  TEXT,
      num_acdos   INTEGER,
      anio        INTEGER,
      pagina      INTEGER
    );

    CREATE INDEX idx_expediente ON entradas (juzgado, expediente);
    CREATE INDEX idx_anio ON entradas (anio);
    CREATE INDEX idx_tipo_juicio_norm ON entradas (tipo_juicio_norm);

    CREATE VIRTUAL TABLE entradas_fts USING fts5(
      actora, demandada, tipo_juicio,
      content='entradas', content_rowid='id', detail=none
    );
  `);

  const insertBoletin = db.prepare('INSERT INTO boletines (id, fecha, fecha_iso, pdf_url) VALUES (?, ?, ?, ?)');
  const insertEntrada = db.prepare(`
    INSERT INTO entradas (id, boletin_id, juzgado, sala, secretaria, actora, demandada, tipo_juicio, tipo_juicio_norm, expediente, num_acdos, anio, pagina)
    VALUES (@id, @boletin_id, @juzgado, @sala, @secretaria, @actora, @demandada, @tipo_juicio, @tipo_juicio_norm, @expediente, @num_acdos, @anio, @pagina)
  `);

  insertBoletin.run(1, 'Consulta del Boletin Judicial de fecha 05-jun.-2026', '2026-06-05', 'https://example.org/boletin1.pdf');
  insertBoletin.run(2, 'Consulta del Boletin Judicial de fecha 20-mar.-2026', '2026-03-20', 'https://example.org/boletin2.pdf');
  insertBoletin.run(3, 'Consulta del Boletin Judicial de fecha 03-jun.-2025', '2025-06-03', 'https://example.org/boletin3.pdf');

  const rows = [
    // Caso con 2 avisos en el mismo expediente y juzgado (debe agruparse).
    { id: 1, boletin_id: 1, juzgado: 'TRIGÉSIMO DE LO FAMILIAR', sala: null, secretaria: 'A',
      actora: 'Esquivel Thebart Alejandro', demandada: null,
      tipo_juicio: 'Sucesorio Familiar', tipo_juicio_norm: 'Sucesorio Familiar', expediente: '184/2025', num_acdos: 4, anio: 2025 },
    { id: 2, boletin_id: 2, juzgado: 'TRIGÉSIMO DE LO FAMILIAR', sala: null, secretaria: 'A',
      actora: 'Esquivel Thebart Alejandro', demandada: null,
      tipo_juicio: 'Sucesorio Familiar', tipo_juicio_norm: 'Sucesorio Familiar', expediente: '184/2025', num_acdos: 1, anio: 2025 },
    // Caso distinto, mismo actora pero otro expediente/juzgado.
    { id: 3, boletin_id: 1, juzgado: 'CUADRAGÉSIMO DE LO CIVIL', sala: null, secretaria: null,
      actora: 'Tello de Meneses y Castañón María Eugenia', demandada: 'Esquivel Thebart Alejandro su Sucesión',
      tipo_juicio: 'Ord. Civil', tipo_juicio_norm: 'Ordinario Civil', expediente: '722/2016', num_acdos: 1, anio: 2016 },
    // Un tercer registro de personas totalmente distintas, para tipo/año distintos.
    { id: 4, boletin_id: 3, juzgado: 'DÉCIMO DE LO CIVIL', sala: null, secretaria: null,
      actora: 'García Pérez Juan', demandada: 'Constructora Ejemplo S.A. de C.V.',
      tipo_juicio: 'Especial Hipotecario Civil', tipo_juicio_norm: 'Especial Hipotecario Civil', expediente: '55/2024', num_acdos: 2, anio: 2024 },
    // Empresa contra empresa + persona (para /empresas: la persona no se nombra).
    { id: 6, boletin_id: 1, juzgado: 'DÉCIMO DE LO CIVIL', sala: null, secretaria: null,
      actora: 'Banco Ejemplo S.A. Institución de Banca Múltiple',
      demandada: 'Constructora Ejemplo S.A. de C.V. y Pérez Gómez Ana',
      tipo_juicio: 'Especial Hipotecario Civil', tipo_juicio_norm: 'Especial Hipotecario Civil',
      expediente: '77/2025', num_acdos: 1, anio: 2025 },
    // Fila sin tipo_juicio ni expediente (caso incompleto real).
    { id: 5, boletin_id: 3, juzgado: 'DÉCIMO DE LO CIVIL', sala: null, secretaria: null,
      actora: 'Ramírez Sin Tipo', demandada: null,
      tipo_juicio: null, tipo_juicio_norm: null, expediente: null, num_acdos: null, anio: null },
  ];

  for (const r of rows) insertEntrada.run({ pagina: r.id + 2, ...r });

  // Mismo cálculo que populate_boletin_ranges() en build_db.py.
  db.exec(`
    UPDATE boletines SET
      n_entradas = (SELECT COUNT(*) FROM entradas WHERE boletin_id = boletines.id),
      entrada_desde = (SELECT MIN(id) FROM entradas WHERE boletin_id = boletines.id),
      entrada_hasta = (SELECT MAX(id) FROM entradas WHERE boletin_id = boletines.id)
  `);

  // Mismas tablas que build_db.py --empresas.
  db.exec(`
    CREATE TABLE empresas (
      id INTEGER PRIMARY KEY, nucleo TEXT UNIQUE NOT NULL, nombre TEXT NOT NULL, slug TEXT UNIQUE NOT NULL,
      avisos INTEGER NOT NULL, como_actora INTEGER NOT NULL, como_demandada INTEGER NOT NULL,
      expedientes INTEGER, primer_boletin TEXT, ultimo_boletin TEXT
    );
    CREATE TABLE entradas_empresas (empresa_id INTEGER NOT NULL, entrada_id INTEGER NOT NULL, rol TEXT NOT NULL);
    CREATE TABLE entradas_partes (entrada_id INTEGER PRIMARY KEY, otras_actora INTEGER NOT NULL, otras_demandada INTEGER NOT NULL);
    INSERT INTO empresas VALUES
      (1, 'constructora ejemplo', 'Constructora Ejemplo S.A. de C.V.', 'constructora-ejemplo', 2, 0, 2, 2, '2025-06-03', '2026-06-05'),
      (2, 'banco ejemplo', 'Banco Ejemplo S.A. Institución de Banca Múltiple', 'banco-ejemplo', 1, 1, 0, 1, '2026-06-05', '2026-06-05');
    INSERT INTO entradas_empresas VALUES (1, 4, 'demandada'), (1, 6, 'demandada'), (2, 6, 'actora');
    INSERT INTO entradas_partes VALUES (4, 1, 0), (6, 0, 1);
    CREATE VIRTUAL TABLE empresas_fts USING fts5(nombre, content='empresas', content_rowid='id', detail=none);
    INSERT INTO empresas_fts(rowid, nombre) SELECT id, nombre FROM empresas;
  `);

  // Misma tabla que build_db.py --personas.
  db.exec(`
    CREATE TABLE personas (
      id INTEGER PRIMARY KEY, nucleo TEXT UNIQUE NOT NULL, nombre TEXT NOT NULL, slug TEXT UNIQUE NOT NULL,
      avisos INTEGER NOT NULL, como_actora INTEGER NOT NULL, como_demandada INTEGER NOT NULL,
      primer_boletin TEXT, ultimo_boletin TEXT
    );
    INSERT INTO personas VALUES
      (1, 'esquivel thebart alejandro', 'Esquivel Thebart Alejandro', 'esquivel-thebart-alejandro', 3, 2, 1, '2026-03-20', '2026-06-05'),
      (2, 'garcia perez juan', 'García Pérez Juan', 'garcia-perez-juan', 2, 2, 0, '2025-06-03', '2025-06-03');
    CREATE VIRTUAL TABLE personas_fts USING fts5(nombre, content='personas', content_rowid='id', detail=none);
    INSERT INTO personas_fts(rowid, nombre) SELECT id, nombre FROM personas;
  `);

  // Mismas tablas que build_db.py build_vocabulario.
  db.exec(`
    CREATE TABLE vocabulario (id INTEGER PRIMARY KEY, palabra TEXT UNIQUE NOT NULL, n INTEGER NOT NULL);
    INSERT INTO vocabulario (palabra, n) VALUES
      ('esquivel', 3), ('thebart', 3), ('alejandro', 3), ('garcia', 2), ('perez', 2), ('juan', 2),
      ('constructora', 2), ('ejemplo', 3), ('banco', 1), ('thebrat', 0);
    CREATE VIRTUAL TABLE vocabulario_tri USING fts5(palabra, content='vocabulario', content_rowid='id', tokenize='trigram', detail=none);
    INSERT INTO vocabulario_tri(rowid, palabra) SELECT id, palabra FROM vocabulario;
  `);

  db.exec(`
    INSERT INTO entradas_fts(rowid, actora, demandada, tipo_juicio)
    SELECT id, actora, demandada, tipo_juicio FROM entradas
  `);

  db.close();
  return dbPath;
}

// Fixture del texto de pdftotext, para probar la lectura de raw-text.js sin
// tocar scraper/data/ real. Reproduce la misma estructura de directorios
// que usa el scraper: <dataDir>/<run>/pdfs/boletin_<id>.txt
function buildFixtureScraperData() {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'litigios-scraper-data-'));
  const pdfsDir = path.join(dataDir, '2026-06-01', 'pdfs');
  fs.mkdirSync(pdfsDir, { recursive: true });

  // El boletín 2 del fixture de la DB (aviso más reciente del expediente 184/2025).
  fs.writeFileSync(
    path.join(pdfsDir, 'boletin_2.txt'),
    'JUZGADOS DE LO FAMILIAR\n' +
    'TRIGÉSIMO DE LO FAMILIAR\n' +
    'Esquivel Thebart Alejandro. Sucesorio Familiar 1 Acdo. Núm. Exp. 184/2025.\n' +
    'Otro Actora Cualquiera vs. Otro Demandado. Ord. Civil 1 Acdo. Núm. Exp. 999/2020.\n'
  );

  // Boletín 3 comprimido, como se sube al server.
  fs.writeFileSync(
    path.join(pdfsDir, 'boletin_3.txt.gz'),
    require('zlib').gzipSync('DÉCIMO DE LO CIVIL\nGarcía Pérez Juan vs. Constructora Ejemplo S.A. de C.V. Esp. Hip. 2 Acdos. Núm. Exp. 55/2024.\n')
  );

  return dataDir;
}

module.exports = { buildFixtureDb, buildFixtureScraperData };
