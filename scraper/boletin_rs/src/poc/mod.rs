// Port de poc.py (scraper del índice + descarga + parseo) y reparse.py.
pub mod cli;
pub mod html;
pub mod parser;
pub mod salida;

pub use parser::{parse_pdf, Row, COLUMNAS};
