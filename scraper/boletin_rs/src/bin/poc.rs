// poc descargar|reparsear ...: ver boletin::poc::cli (o `boletin poc ...`).
fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    std::process::exit(boletin::poc::cli::main(&args));
}
