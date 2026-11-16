# boletin_rs

Scraper, parser y cálculo por aviso del Boletín Judicial CDMX, en Rust. Reemplaza al parser en Python (`poc.py`, `reparse.py`, `acuerdos.py`, `normalize_tipo.py`, `empresas.py`, `personas.py` y el `derivar` de `build_db.py`), que se borró en noviembre de 2026 después de verificar que el port daba la misma salida byte a byte.

## Compilar

Rust 1.100 (`rust-version` en `Cargo.toml`, el mismo de la imagen del Dockerfile).

```
cd scraper/boletin_rs
cargo build --release
cargo test --release
```

Los binarios quedan en `target/release/`. Si el Rust del sistema es más viejo, se puede instalar el oficial dentro del proyecto (ignorado por git) bajando el tarball de `static.rust-lang.org/dist/rust-1.100.0-x86_64-unknown-linux-gnu.tar.xz`, verificando su `.sha256` y corriendo su `install.sh --prefix=$PWD/.rust --components=rustc,cargo,rust-std-x86_64-unknown-linux-gnu --disable-ldconfig`. Después: `PATH=$PWD/.rust/bin:$PATH cargo build --release`.

## Binarios

| Binario | Uso | Reemplaza a |
|---|---|---|
| `poc descargar [desde] [hasta] [max] [carpeta]` | Baja el índice y los PDF del portal, extrae el texto con `pdftotext` y escribe `entradas.csv`, `index.json` y `resumen.json` | `poc.py` |
| `poc reparsear <carpeta>` | Vuelve a parsear los `.txt` de una corrida | `reparse.py` |
| `precalc <entradas.csv> <carpeta> [--hilos N]` | Cálculo por aviso (nombres, número, tipo de acuerdo, descripción, empresas, personas): `filas.csv`, `det.csv`, `partes.csv`, `tramos.csv` | `derivar` de `build_db.py` |
| `boletin --stdin` | Un campo actora/demandada por línea, JSON con personas y empresas | `personas.personas()` y `empresas.partes()` |

`build_db.py` llama a `precalc` y `diario.py` a `poc`. Necesitan `curl` y `pdftotext` en el sistema.

## Módulos

- `py.rs`: semántica de `str` e `int` de Python y traducción de expresiones de `re` (`\s` incluye `\x1c-\x1f`, `\w` sin marcas combinantes).
- `re.rs`: la envoltura de regex. Un `\b` Unicode hace que el crate `regex` caiga a su motor lento con texto no ASCII: cada expresión con `\b` o lookarounds pasa primero por una versión sin ellos, más amplia y rápida, que descarta textos y adelanta el inicio de la búsqueda. El atajo con `\b` ASCII solo se usa si todo `\b` del patrón está pegado a una letra ASCII (un `s[ií]\b` lo rompía).
- `cache.rs`: cachés por hilo (los `lru_cache` de Python).
- `tipos.rs`, `acuerdos.rs`, `partes.rs` (+ `sets.rs`): normalización del tipo de juicio, tipo de acuerdo y descripción, detección de personas y empresas.
- `fechas.rs`, `derivar.rs`, `precalc.rs`: cálculo por aviso y salida del precálculo.
- `poc/`: scraper y parser de los PDF.

## Tests

`tests/casos.rs` tiene los casos de regresión que eran de `test_tipo_acuerdo.py` y `bench_empresas.py` (errores ya corregidos y falsos positivos). Todo cambio en la detección tiene que pasarlos y sumar el caso nuevo.
