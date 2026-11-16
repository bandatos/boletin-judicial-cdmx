// CSV como csv.DictWriter de Python (QUOTE_MINIMAL, \r\n).
use super::parser::{Row, COLUMNAS};
use std::io::Write;

fn campo(out: &mut String, v: &str) {
    if v.contains(|c| c == ',' || c == '"' || c == '\r' || c == '\n') {
        out.push('"');
        out.push_str(&v.replace('"', "\"\""));
        out.push('"');
    } else {
        out.push_str(v);
    }
}

pub fn csv_header() -> String { let mut s = COLUMNAS.join(","); s.push_str("\r\n"); s }

pub fn csv_rows(rows: &[Row]) -> String {
    let mut out = String::new();
    for r in rows {
        for (i, v) in r.valores().iter().enumerate() {
            if i > 0 { out.push(','); }
            if let Some(v) = v { campo(&mut out, v); }
        }
        out.push_str("\r\n");
    }
    out
}

pub fn write_rows<W: Write>(w: &mut W, rows: &[Row], con_header: bool) -> std::io::Result<()> {
    if con_header { w.write_all(csv_header().as_bytes())?; }
    w.write_all(csv_rows(rows).as_bytes())
}
