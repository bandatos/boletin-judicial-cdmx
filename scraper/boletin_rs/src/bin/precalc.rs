// precalc <entradas.csv> <out_dir> [--hilos N]: ver boletin::precalc.
fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    std::process::exit(boletin::precalc::main(&args));
}
