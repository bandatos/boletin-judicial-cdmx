// Precálculo (generar_precalc de build_db.py): deriva cada aviso del CSV del
// scraper y escribe en out_dir, en CSV para DuckDB (encabezado, \N para
// NULL, comillas dobles cuando hace falta)
//   filas.csv   fila + valores en el orden de COLS_CARGA
//   det.csv     fila, tipo (1 empresa, 2 persona), rol (1 actora, 2 demandada), nucleo, nombre
//   partes.csv  fila, otras_actora, otras_demandada (avisos con alguna empresa)
//   tramos.csv  boletin_id, desde, hasta (filas consecutivas del mismo boletín)
// fila es el número de fila de datos del CSV, desde 1. NULL = \N.
// Lee en streaming, deriva por lotes en N hilos y escribe en orden.
use crate::derivar::{derivar, Cols, Derivado, COLS_CARGA};
use std::collections::BTreeMap;
use std::fs::File;
use std::io::{BufReader, BufWriter, Write};
use std::path::Path;
use std::sync::mpsc::{sync_channel, Receiver};
use std::sync::{Arc, Mutex};
use std::time::Instant;

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

const LOTE: usize = 4096;

struct Lote { n: usize, primera: u64, filas: Vec<Vec<String>> }
struct Hecho { n: usize, filas: Vec<u8>, det: Vec<u8>, partes: Vec<u8> }

fn uso() -> ! {
    eprintln!("uso: precalc <entradas.csv> <out_dir> [--hilos N]");
    std::process::exit(2);
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

    let mut rdr = csv::ReaderBuilder::new()
        .has_headers(false)
        .flexible(true)
        .from_reader(BufReader::with_capacity(1 << 22, File::open(entrada).expect("no se pudo abrir el CSV")));
    let mut rec = csv::StringRecord::new();
    if !rdr.read_record(&mut rec).expect("CSV ilegible") { eprintln!("CSV vacío"); std::process::exit(1); }
    let header: Vec<String> = rec.iter().map(String::from).collect();
    let cols = Arc::new(Cols::desde_encabezado(&header).unwrap_or_else(|e| { eprintln!("{e}"); std::process::exit(1) }));
    let i_boletin = header.iter().rposition(|h| h == "boletin_id");
    let ancho = header.len();

    let crear = |nombre: &str, enc: &str| {
        let mut w = BufWriter::with_capacity(1 << 20, File::create(out_dir.join(nombre)).unwrap());
        w.write_all(enc.as_bytes()).unwrap();
        w.write_all(b"\n").unwrap();
        w
    };
    let mut w_filas = crear("filas.csv", &format!("fila,{}", COLS_CARGA.join(",")));
    let mut w_det = crear("det.csv", "fila,tipo,rol,nucleo,nombre");
    let mut w_partes = crear("partes.csv", "fila,otras_actora,otras_demandada");
    let mut w_tramos = crear("tramos.csv", "boletin_id,desde,hasta");

    let (tx_lote, rx_lote) = sync_channel::<Lote>(hilos * 2);
    let rx_lote: Arc<Mutex<Receiver<Lote>>> = Arc::new(Mutex::new(rx_lote));
    let (tx_hecho, rx_hecho) = sync_channel::<Hecho>(hilos * 2);
    let mut trabajadores = Vec::new();
    for _ in 0..hilos {
        let rx = Arc::clone(&rx_lote);
        let tx = tx_hecho.clone();
        let cols = Arc::clone(&cols);
        trabajadores.push(std::thread::spawn(move || {
            loop {
                let lote = match rx.lock().unwrap().recv() { Ok(l) => l, Err(_) => break };
                let mut h = Hecho { n: lote.n, filas: Vec::with_capacity(1 << 20), det: Vec::new(), partes: Vec::new() };
                for (k, mut row) in lote.filas.into_iter().enumerate() {
                    let r = derivar(&mut row, &cols);
                    escribir(lote.primera + k as u64, &r, &mut h.filas, &mut h.det, &mut h.partes);
                }
                if tx.send(h).is_err() { break; }
            }
        }));
    }
    drop(tx_hecho);

    // Escribe los lotes en orden.
    let escritor = std::thread::spawn(move || {
        let mut pendientes: BTreeMap<usize, Hecho> = BTreeMap::new();
        let mut siguiente = 0;
        for h in rx_hecho {
            pendientes.insert(h.n, h);
            while let Some(h) = pendientes.remove(&siguiente) {
                w_filas.write_all(&h.filas).unwrap();
                w_det.write_all(&h.det).unwrap();
                w_partes.write_all(&h.partes).unwrap();
                siguiente += 1;
            }
        }
        assert!(pendientes.is_empty(), "faltan lotes");
        for w in [&mut w_filas, &mut w_det, &mut w_partes] { w.flush().unwrap(); }
    });

    // Lectura: lotes de filas y tramos de boletín.
    let mut tramo: Option<(Option<String>, u64)> = None; // (boletin_id, desde)
    let cerrar_tramo = |tramo: &(Option<String>, u64), hasta: u64, w: &mut BufWriter<File>| {
        let mut b = Vec::new();
        campo(&mut b, tramo.0.as_deref());
        b.extend_from_slice(format!(",{},{}\n", tramo.1, hasta).as_bytes());
        w.write_all(&b).unwrap();
    };
    let (mut fila, mut n_lote) = (0u64, 0usize);
    let mut lote: Vec<Vec<String>> = Vec::with_capacity(LOTE);
    loop {
        let hay = rdr.read_record(&mut rec).unwrap_or_else(|e| { eprintln!("CSV: {e}"); std::process::exit(1) });
        if hay {
            fila += 1;
            let mut row: Vec<String> = rec.iter().map(String::from).collect();
            if row.len() < ancho { row.resize(ancho, String::new()); }
            let bol = i_boletin.and_then(|i| if row[i].is_empty() { None } else { Some(row[i].clone()) });
            match &tramo {
                Some(t) if t.0 == bol => {}
                _ => {
                    if let Some(t) = &tramo { cerrar_tramo(t, fila - 1, &mut w_tramos); }
                    tramo = Some((bol, fila));
                }
            }
            lote.push(row);
        }
        if lote.len() == LOTE || (!hay && !lote.is_empty()) {
            let filas = std::mem::replace(&mut lote, Vec::with_capacity(LOTE));
            let primera = fila - filas.len() as u64 + 1;
            tx_lote.send(Lote { n: n_lote, primera, filas }).expect("los hilos terminaron antes de tiempo");
            n_lote += 1;
        }
        if !hay { break; }
    }
    if let Some(t) = &tramo { cerrar_tramo(t, fila, &mut w_tramos); }
    w_tramos.flush().unwrap();
    drop(tx_lote);
    for t in trabajadores { t.join().expect("falló un hilo"); }
    escritor.join().expect("falló el escritor");
    eprintln!("{} filas en {:.1} s ({} hilos)", fila, t0.elapsed().as_secs_f64(), hilos);
    0
}
