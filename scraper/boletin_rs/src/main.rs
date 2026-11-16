// boletin: ver boletin::cli.
fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    std::process::exit(boletin::cli::main(&args));
}
