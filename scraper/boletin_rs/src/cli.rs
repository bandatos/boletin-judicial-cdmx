// CLI única del crate (binario boletin). Subcomandos:
//   boletin poc descargar|reparsear ...      scraper (también binario poc)
//   boletin precalc <csv> <out_dir> [--hilos N]   precálculo (también binario precalc)
//   boletin partes --stdin                   un campo actora/demandada por línea -> JSON
//   boletin partes <campos> <salida> [reps]  benchmark de personas y empresas
// Por compatibilidad, `boletin --stdin` y `boletin <campos> <salida> [reps]`
// son `boletin partes ...`.
use crate::json::jl;
use crate::partes::{detectar, detectar_sin_cache, Deteccion};
use std::io::{BufRead, Write};
use std::time::Instant;

pub fn main(args: &[String]) -> i32 {
    match args.first().map(String::as_str) {
        Some("poc") => crate::poc::cli::main(&args[1..]),
        Some("precalc") => crate::precalc::main(&args[1..]),
        Some("partes") => partes(&args[1..]),
        _ => partes(args),
    }
}

fn linea(out: &mut String, d: &Deteccion) {
    out.push_str("{\"personas\": "); jl(out, &d.personas);
    out.push_str(", \"empresas\": "); jl(out, &d.empresas);
    out.push_str(", \"otras\": "); out.push_str(if d.otras { "true" } else { "false" });
    out.push_str("}\n");
}

fn partes(args: &[String]) -> i32 {
    if args.iter().take(2).any(|a| a == "--stdin") {
        // Desde build_db.py: un campo por línea en stdin, una línea JSON por
        // campo en stdout, en el mismo orden.
        let mut out = String::new();
        for l in std::io::stdin().lock().lines() { linea(&mut out, &detectar(&l.unwrap())); }
        std::io::stdout().write_all(out.as_bytes()).unwrap();
        return 0;
    }
    let entrada = args.first().map(String::as_str).unwrap_or("../campos.txt");
    let salida = args.get(1).map(String::as_str).unwrap_or("../salida.jsonl");
    let reps: usize = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(1);
    let campos: Vec<String> = std::fs::read_to_string(entrada).unwrap().lines().map(String::from).collect();
    let mut out = String::new();
    let mut tiempos = Vec::new();
    for rep in 0..reps {
        // Sin la caché por campo: mide el cálculo.
        out.clear();
        let t0 = Instant::now();
        for c in &campos { linea(&mut out, &detectar_sin_cache(c)); }
        tiempos.push(t0.elapsed().as_secs_f64() / campos.len() as f64 * 1e6);
        eprintln!("rep {}: {:.2} µs/campo", rep, tiempos[rep]);
    }
    tiempos.sort_by(|a, b| a.partial_cmp(b).unwrap());
    eprintln!("mediana {:.2} µs/campo ({} campos)", tiempos[tiempos.len() / 2], campos.len());
    std::fs::File::create(salida).unwrap().write_all(out.as_bytes()).unwrap();
    0
}
