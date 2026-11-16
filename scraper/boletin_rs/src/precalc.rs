// Precálculo (generar_precalc de build_db.py): deriva cada aviso del CSV del
// scraper y escribe en out_dir, en CSV para DuckDB (encabezado, \N para
// NULL, comillas dobles cuando hace falta)
//   filas.csv   fila + valores en el orden de COLS_CARGA
//   det.csv     fila, tipo (1 empresa, 2 persona), rol (1 actora, 2 demandada), nucleo, nombre
//   partes.csv  fila, otras_actora, otras_demandada (avisos con alguna empresa)
//   tramos.csv  boletin_id, desde, hasta (filas consecutivas del mismo boletín)
// fila es el número de fila de datos del CSV, desde 1. NULL = \N.
// Cada hilo lee y procesa su propio pedazo del archivo (PEDAZO bytes,
// cortado en saltos de línea: cada fila ocupa una línea, ningún campo lleva
// saltos) y el principal escribe los pedazos en orden. Antes un solo hilo
// leía y repartía, y con la corrida histórica (9 GB, textos largos) los
// demás quedaban esperando filas.
use crate::derivar::{derivar, Cols, Derivado, COLS_CARGA};
use std::collections::BTreeMap;
use std::fs::File;
use std::io::{BufReader, BufWriter, Read, Write};
use std::os::unix::fs::FileExt;
use std::path::Path;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::mpsc::sync_channel;
use std::time::{Duration, Instant};

/// Un campo CSV. Se entrecomillan los vacíos (para distinguirlos de NULL),
/// el texto literal \N y lo que tenga coma, comillas, saltos o espacios en
/// los bordes. Leer con read_csv(..., nullstr='\N', allow_quoted_nulls=false).
pub fn campo(out: &mut Vec<u8>, v: Option<&str>) {
    let s = match v {
        None => { out.extend_from_slice(b"\\N"); return; }
        Some(s) => s,
    };
    let comillas = s.is_empty()
        || s == "\\N"
        || s.bytes().any(|b| matches!(b, b',' | b'"' | b'\n' | b'\r'))
        || s.starts_with(char::is_whitespace)
        || s.ends_with(char::is_whitespace);
    if !comillas { out.extend_from_slice(s.as_bytes()); return; }
    out.push(b'"');
    for b in s.bytes() {
        if b == b'"' { out.push(b'"'); }
        out.push(b);
    }
    out.push(b'"');
}

/// Agrega las líneas de un aviso a los buffers de filas, det y partes.
pub fn escribir(fila: u64, d: &Derivado, filas: &mut Vec<u8>, det: &mut Vec<u8>, partes: &mut Vec<u8>) {
    filas.extend_from_slice(fila.to_string().as_bytes());
    for v in &d.valores {
        filas.push(b',');
        campo(filas, v.as_deref());
    }
    filas.push(b'\n');
    for (tipo, rol, nucleo, nombre) in &d.det {
        det.extend_from_slice(format!("{fila},{tipo},{rol},").as_bytes());
        campo(det, Some(nucleo));
        det.push(b',');
        campo(det, Some(nombre));
        det.push(b'\n');
    }
    if let Some((a, b)) = d.partes {
        partes.extend_from_slice(format!("{fila},{a},{b}\n").as_bytes());
    }
}

/// Bytes de CSV por pedazo.
const PEDAZO: u64 = 16 << 20;

/// Lo que devuelve un pedazo: sus líneas de salida y los tramos de boletín
/// (boletin_id, desde, hasta) que contiene, en orden.
struct Hecho { filas: Vec<u8>, det: Vec<u8>, partes: Vec<u8>, tramos: Vec<(Option<String>, u64, u64)> }

fn uso() -> ! {
    eprintln!("uso: precalc <entradas.csv> <out_dir> [--hilos N]");
    std::process::exit(2);
}

fn leer(f: &File, desde: u64, hasta: u64) -> Vec<u8> {
    let mut buf = vec![0u8; (hasta - desde) as usize];
    f.read_exact_at(&mut buf, desde).expect("no se pudo leer el CSV");
    buf
}

/// Límites de los pedazos: cada uno empieza al principio de una línea.
fn limites(f: &File, inicio: u64, total: u64) -> Vec<u64> {
    let mut l = vec![inicio];
    let mut s = inicio + PEDAZO;
    while s < total {
        // El pedazo termina después del primer salto de línea desde s.
        let mut corte = None;
        let mut pos = s;
        while corte.is_none() && pos < total {
            let trozo = leer(f, pos, (pos + 65536).min(total));
            corte = memchr::memchr(b'\n', &trozo).map(|k| pos + k as u64 + 1);
            pos += trozo.len() as u64;
        }
        let c = corte.unwrap_or(total);
        if c >= total { break; }
        l.push(c);
        s = c + PEDAZO;
    }
    l.push(total);
    l
}

/// Filas (registros) de cada pedazo: una por salto de línea, más una si el
/// último pedazo no termina en salto.
fn contar(f: &File, lim: &[u64], hilos: usize) -> Vec<u64> {
    let n = lim.len() - 1;
    let siguiente = AtomicUsize::new(0);
    let cuentas: Vec<AtomicUsize> = (0..n).map(|_| AtomicUsize::new(0)).collect();
    std::thread::scope(|s| {
        for _ in 0..hilos.min(n) {
            s.spawn(|| loop {
                let i = siguiente.fetch_add(1, Ordering::Relaxed);
                if i >= n { break; }
                let buf = leer(f, lim[i], lim[i + 1]);
                let mut c = memchr::memchr_iter(b'\n', &buf).count();
                if i == n - 1 && buf.last().map_or(false, |&b| b != b'\n') { c += 1; }
                cuentas[i].store(c, Ordering::Relaxed);
            });
        }
    });
    cuentas.into_iter().map(|c| c.into_inner() as u64).collect()
}

fn procesar(buf: &[u8], primera: u64, cols: &Cols, ancho: usize, i_boletin: Option<usize>) -> Hecho {
    let mut h = Hecho { filas: Vec::with_capacity(buf.len()), det: Vec::new(), partes: Vec::new(), tramos: Vec::new() };
    let mut rdr = csv::ReaderBuilder::new().has_headers(false).flexible(true).from_reader(buf);
    let mut rec = csv::StringRecord::new();
    let mut fila = primera;
    while rdr.read_record(&mut rec).unwrap_or_else(|e| { eprintln!("CSV: {e}"); std::process::exit(1) }) {
        let mut row: Vec<String> = rec.iter().map(String::from).collect();
        if row.len() < ancho { row.resize(ancho, String::new()); }
        let bol = i_boletin.and_then(|i| if row[i].is_empty() { None } else { Some(row[i].clone()) });
        match h.tramos.last_mut() {
            Some(t) if t.0 == bol => t.2 = fila,
            _ => h.tramos.push((bol, fila, fila)),
        }
        let r = derivar(&mut row, cols);
        escribir(fila, &r, &mut h.filas, &mut h.det, &mut h.partes);
        fila += 1;
    }
    assert_eq!(fila - primera, h.tramos.iter().map(|t| t.2 - t.1 + 1).sum::<u64>());
    h
}

/// `precalc <entradas.csv> <out_dir> [--hilos N]` (args sin el nombre del programa).
pub fn main(args: &[String]) -> i32 {
    let mut pos = Vec::new();
    let mut hilos = std::thread::available_parallelism().map_or(4, |n| n.get()).min(12);
    let mut i = 0;
    while i < args.len() {
        if args[i] == "--hilos" {
            hilos = args.get(i + 1).and_then(|s| s.parse().ok()).unwrap_or_else(|| uso());
            i += 2;
        } else {
            pos.push(args[i].clone());
            i += 1;
        }
    }
    if pos.len() != 2 || hilos == 0 { uso(); }
    let (entrada, out_dir) = (Path::new(&pos[0]), Path::new(&pos[1]));
    std::fs::create_dir_all(out_dir).expect("no se pudo crear out_dir");
    let t0 = Instant::now();

    // Encabezado.
    let f = File::open(entrada).expect("no se pudo abrir el CSV");
    let total = f.metadata().unwrap().len();
    let mut rdr = csv::ReaderBuilder::new().has_headers(false).flexible(true)
        .from_reader(BufReader::new(f.try_clone().unwrap().take(1 << 20)));
    let mut rec = csv::StringRecord::new();
    if !rdr.read_record(&mut rec).expect("CSV ilegible") { eprintln!("CSV vacío"); std::process::exit(1); }
    // Los datos empiezan después del primer salto de línea (el encabezado no
    // lleva saltos adentro). position() del lector queda antes del \n de
    // un \r\n, y el primer pedazo arrancaba con un salto de más.
    let primer_bloque = leer(&f, 0, total.min(1 << 20));
    let inicio = memchr::memchr(b'\n', &primer_bloque).map_or(total, |k| k as u64 + 1);
    let header: Vec<String> = rec.iter().map(String::from).collect();
    let cols = Cols::desde_encabezado(&header).unwrap_or_else(|e| { eprintln!("{e}"); std::process::exit(1) });
    let i_boletin = header.iter().rposition(|h| h == "boletin_id");
    let ancho = header.len();

    let crear = |nombre: &str, enc: &str| {
        let mut w = BufWriter::with_capacity(1 << 22, File::create(out_dir.join(nombre)).unwrap());
        w.write_all(enc.as_bytes()).unwrap();
        w.write_all(b"\n").unwrap();
        w
    };
    let mut w_filas = crear("filas.csv", &format!("fila,{}", COLS_CARGA.join(",")));
    let mut w_det = crear("det.csv", "fila,tipo,rol,nucleo,nombre");
    let mut w_partes = crear("partes.csv", "fila,otras_actora,otras_demandada");
    let mut w_tramos = crear("tramos.csv", "boletin_id,desde,hasta");

    // Pedazos y la primera fila de cada uno.
    let lim = limites(&f, inicio, total);
    let cuentas = contar(&f, &lim, hilos);
    let mut primeras = Vec::with_capacity(cuentas.len());
    let mut acumulado = 1u64;
    for c in &cuentas { primeras.push(acumulado); acumulado += c; }
    let n = cuentas.len();

    // Cada hilo toma el próximo pedazo; el principal escribe en orden. Un
    // hilo no se adelanta más de 4 pedazos por hilo a lo ya escrito, para
    // no juntar en memoria pedazos que esperan a uno lento.
    let siguiente = AtomicUsize::new(0);
    let escritos = AtomicUsize::new(0);
    let (tx, rx) = sync_channel::<(usize, Hecho)>(hilos * 2);
    let mut tramos: Vec<(Option<String>, u64, u64)> = Vec::new();
    std::thread::scope(|s| {
        for _ in 0..hilos.min(n.max(1)) {
            let (tx, f, lim, primeras, cols, siguiente, escritos) = (tx.clone(), &f, &lim, &primeras, &cols, &siguiente, &escritos);
            s.spawn(move || loop {
                let i = siguiente.fetch_add(1, Ordering::Relaxed);
                if i >= n { break; }
                while i > escritos.load(Ordering::Relaxed) + 4 * hilos { std::thread::sleep(Duration::from_millis(2)); }
                let h = procesar(&leer(f, lim[i], lim[i + 1]), primeras[i], cols, ancho, i_boletin);
                if tx.send((i, h)).is_err() { break; }
            });
        }
        drop(tx);
        let mut pendientes: BTreeMap<usize, Hecho> = BTreeMap::new();
        for (i, h) in rx {
            pendientes.insert(i, h);
            while let Some(h) = pendientes.remove(&escritos.load(Ordering::Relaxed)) {
                w_filas.write_all(&h.filas).unwrap();
                w_det.write_all(&h.det).unwrap();
                w_partes.write_all(&h.partes).unwrap();
                // Un tramo cortado entre dos pedazos se une.
                for t in h.tramos {
                    match tramos.last_mut() {
                        Some(u) if u.0 == t.0 && u.2 + 1 == t.1 => u.2 = t.2,
                        _ => tramos.push(t),
                    }
                }
                escritos.fetch_add(1, Ordering::Relaxed);
            }
        }
        assert!(pendientes.is_empty(), "faltan pedazos");
    });
    for t in &tramos {
        let mut b = Vec::new();
        campo(&mut b, t.0.as_deref());
        b.extend_from_slice(format!(",{},{}\n", t.1, t.2).as_bytes());
        w_tramos.write_all(&b).unwrap();
    }
    for w in [&mut w_filas, &mut w_det, &mut w_partes, &mut w_tramos] { w.flush().unwrap(); }
    eprintln!("{} filas en {:.1} s ({} hilos)", acumulado - 1, t0.elapsed().as_secs_f64(), hilos);
    0
}
