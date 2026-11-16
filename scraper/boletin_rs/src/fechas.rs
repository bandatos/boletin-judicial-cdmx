// Fechas del boletín: fecha_iso y fecha_acuerdo_iso de build_db.py, y la
// fecha del índice que usa urls_alternativas de poc.py.
use crate::py::py_int;
use crate::re::Re;
use std::sync::OnceLock;

/// MESES / MESES_ABBR de Python.
pub const MESES: [&str; 12] = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
const MESES_LARGOS: [(&str, u32); 13] = [
    ("ENERO", 1), ("FEBRERO", 2), ("MARZO", 3), ("ABRIL", 4), ("MAYO", 5), ("JUNIO", 6), ("JULIO", 7),
    ("AGOSTO", 8), ("SEPTIEMBRE", 9), ("SETIEMBRE", 9), ("OCTUBRE", 10), ("NOVIEMBRE", 11), ("DICIEMBRE", 12),
];

struct F { raw: Re, acuerdo: Re }
fn f() -> &'static F {
    static F_: OnceLock<F> = OnceLock::new();
    F_.get_or_init(|| F {
        raw: Re::new(r"(\d{1,2})-([a-z]{3})[a-z]*\.?-(\d{4})"),
        acuerdo: Re::new(r"(\d{1,2})\s+DE\s+([A-ZÁÉÍÓÚ]+)\s+DEL?\s+(\d{4})"),
    })
}

/// RE_FECHA_RAW sobre la fecha del índice ('Consulta del Boletin Judicial
/// de fecha  22-sep.-2026'): (día, mes, año).
pub fn fecha_boletin(fecha_raw: &str) -> Option<(i64, u32, &str)> {
    let c = f().raw.caps(fecha_raw)?;
    let mes = MESES.iter().position(|&m| m == c.get(fecha_raw, 2).unwrap())? as u32 + 1;
    Some((py_int(c.get(fecha_raw, 1).unwrap()), mes, c.get(fecha_raw, 3).unwrap()))
}

/// fecha_iso: '... 22-sep.-2026' -> '2026-09-22'.
pub fn fecha_iso(fecha_raw: &str) -> Option<String> {
    let (d, m, a) = fecha_boletin(fecha_raw)?;
    Some(format!("{a}-{m:02}-{d:02}"))
}

/// fecha_acuerdo_iso: '21 DE SEPTIEMBRE DEL 2026' -> '2026-09-21'.
pub fn fecha_acuerdo_iso(texto: &str) -> Option<String> {
    let t = texto.to_uppercase();
    let c = f().acuerdo.caps(&t)?;
    let nombre = c.get(&t, 2).unwrap();
    let m = MESES_LARGOS.iter().find(|(n, _)| *n == nombre)?.1;
    Some(format!("{}-{:02}-{:02}", c.get(&t, 3).unwrap(), m, py_int(c.get(&t, 1).unwrap())))
}
