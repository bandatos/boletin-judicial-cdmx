// Semántica de Python sobre &str de Rust: str.strip/split/splitlines/
// isspace/isalpha, len y slices en caracteres, int(), y la traducción de la
// sintaxis de expresiones de re a la del crate regex. Lo usan todos los
// módulos (partes, acuerdos, tipos, derivar, poc).
use std::sync::OnceLock;

/// str.isspace() y \s de re: White_Space de Unicode más \x1c-\x1f (que Rust
/// no considera espacio).
#[inline]
pub fn is_space(c: char) -> bool {
    matches!(c, ' ' | '\t' | '\n' | '\r' | '\x0b' | '\x0c' | '\x1c'..='\x1f' | '\u{85}' | '\u{a0}'
        | '\u{1680}' | '\u{2000}'..='\u{200a}' | '\u{2028}' | '\u{2029}' | '\u{202f}' | '\u{205f}' | '\u{3000}')
}
pub fn strip(s: &str) -> &str { s.trim_matches(is_space) }
pub fn lstrip(s: &str) -> &str { s.trim_start_matches(is_space) }
pub fn rstrip(s: &str) -> &str { s.trim_end_matches(is_space) }
/// s.strip(cs)
pub fn strip_c<'a>(s: &'a str, cs: &str) -> &'a str { s.trim_matches(|c| cs.contains(c)) }
pub fn lstrip_c<'a>(s: &'a str, cs: &str) -> &'a str { s.trim_start_matches(|c| cs.contains(c)) }
pub fn rstrip_c<'a>(s: &'a str, cs: &str) -> &'a str { s.trim_end_matches(|c| cs.contains(c)) }
/// str.split() sin argumentos.
pub fn split_ws(s: &str) -> impl DoubleEndedIterator<Item = &str> { s.split(is_space).filter(|w| !w.is_empty()) }
/// len() de Python (caracteres).
pub fn clen(s: &str) -> usize { if s.is_ascii() { s.len() } else { s.chars().count() } }
/// s[:n] de Python (n en caracteres).
pub fn cprefix(s: &str, n: usize) -> &str {
    if s.len() <= n { return s; }
    match s.char_indices().nth(n) { Some((i, _)) => &s[..i], None => s }
}
/// str.splitlines().
pub fn splitlines(s: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut ini = 0;
    let mut it = s.char_indices().peekable();
    while let Some((i, c)) = it.next() {
        if matches!(c, '\n' | '\r' | '\x0b' | '\x0c' | '\x1c' | '\x1d' | '\x1e' | '\u{85}' | '\u{2028}' | '\u{2029}') {
            out.push(&s[ini..i]);
            let mut fin = i + c.len_utf8();
            if c == '\r' { if let Some(&(_, '\n')) = it.peek() { it.next(); fin += 1; } }
            ini = fin;
        }
    }
    if ini < s.len() { out.push(&s[ini..]); }
    out
}
/// s[:1].upper() + s[1:]
pub fn cap_first(s: &str) -> String {
    let mut ch = s.chars();
    match ch.next() { Some(c) => c.to_uppercase().chain(ch).collect(), None => String::new() }
}
/// Decodifica como Python con errors="replace" y newline=None (\r\n y \r -> \n).
pub fn decode_text(b: &[u8]) -> String {
    let s = String::from_utf8_lossy(b);
    if !s.contains('\r') { return s.into_owned(); }
    s.replace("\r\n", "\n").replace('\r', "\n")
}

fn clase(c: char, p: &'static str, cell: &'static OnceLock<regex::Regex>) -> bool {
    let mut b = [0u8; 4];
    cell.get_or_init(|| regex::Regex::new(p).unwrap()).is_match(c.encode_utf8(&mut b))
}
/// \w de re y isalnum() o "_": letras, números y _ (sin las marcas
/// combinantes que incluye el \w de Rust).
pub fn py_word(c: char) -> bool {
    if c.is_ascii() { return c.is_ascii_alphanumeric() || c == '_'; }
    static W: OnceLock<regex::Regex> = OnceLock::new();
    clase(c, r"^[\p{L}\p{N}_]$", &W)
}
/// str.isalpha(): categorías L*.
pub fn py_alpha(c: char) -> bool {
    if c.is_ascii() { return c.is_ascii_alphabetic(); }
    static L: OnceLock<regex::Regex> = OnceLock::new();
    clase(c, r"^\p{L}$", &L)
}
/// int() de un \d+ (dígitos decimales Unicode, como en Python). Satura en
/// i64::MAX en vez de crecer.
pub fn py_int(s: &str) -> i64 {
    if let Ok(n) = s.parse() { return n; }
    static ND: OnceLock<regex::Regex> = OnceLock::new();
    let nd = |c: u32| char::from_u32(c).map_or(false, |c| clase(c, r"^\p{Nd}$", &ND));
    s.chars().fold(0i64, |n, c| {
        // Los dígitos Unicode van en tramos de 10 seguidos, de 0 a 9.
        let mut z = c as u32;
        while z > 0 && nd(z - 1) { z -= 1; }
        n.saturating_mul(10).saturating_add(((c as u32 - z) % 10) as i64)
    })
}

// ---- sintaxis de re -> sintaxis de regex ----

const S: &str = r"\s\x1C-\x1F";
const W: &str = r"\p{L}\p{N}_";

/// Traduce una expresión de re: \s, \S, \w y \W con las definiciones de
/// Python, \Z, y lo que dentro de una clase es literal en Python pero
/// operador en Rust ([, &&, --, ~~). \b queda con la definición de Rust, o
/// la ASCII (?-u:\b) si `b_ascii`. Devuelve también si el patrón tiene \b.
pub fn traducir(p: &str, b_ascii: bool) -> (String, bool) {
    let c: Vec<char> = p.chars().collect();
    let mut out = String::with_capacity(p.len() + 16);
    let mut en_clase = false;
    let mut tiene_b = false;
    let mut i = 0;
    while i < c.len() {
        let ch = c[i];
        if ch == '\\' && i + 1 < c.len() {
            let n = c[i + 1];
            i += 2;
            match (n, en_clase) {
                ('s', true) => out.push_str(S),
                ('s', false) => { out.push('['); out.push_str(S); out.push(']'); }
                ('S', true) => { out.push_str("[^"); out.push_str(S); out.push(']'); }
                ('S', false) => { out.push_str("[^"); out.push_str(S); out.push(']'); }
                ('w', true) => out.push_str(W),
                ('w', false) => { out.push('['); out.push_str(W); out.push(']'); }
                ('W', _) => { out.push_str("[^"); out.push_str(W); out.push(']'); }
                ('Z', false) => out.push_str(r"\z"),
                ('b', false) => { tiene_b = true; out.push_str(if b_ascii { r"(?-u:\b)" } else { r"\b" }); }
                ('b', true) => out.push_str(r"\x08"),
                _ => { out.push('\\'); out.push(n); }
            }
            continue;
        }
        if en_clase {
            match ch {
                ']' => { en_clase = false; out.push(ch); }
                '[' => out.push_str(r"\["),
                '&' | '-' | '~' if c.get(i + 1) == Some(&ch) => { out.push(ch); out.push('\\'); out.push(ch); i += 1; }
                _ => out.push(ch),
            }
            i += 1;
            continue;
        }
        out.push(ch);
        i += 1;
        if ch == '[' {
            en_clase = true;
            if c.get(i) == Some(&'^') { out.push('^'); i += 1; }
            if c.get(i) == Some(&']') { out.push_str(r"\]"); i += 1; }
        }
    }
    (out, tiene_b)
}

/// Saca de una expresión de re las lookarounds (?=..) (?!..) (?<=..)
/// (?<!..) y, si `b`, los \b. El resultado acepta, desde cada posición,
/// todo lo que acepta el original (y más).
pub fn sin_aserciones(p: &str, b: bool) -> String {
    let v: Vec<char> = p.chars().collect();
    let mut out = String::with_capacity(p.len());
    let mut i = 0;
    let mut en_clase = false;
    while i < v.len() {
        let c = v[i];
        if c == '\\' {
            if b && !en_clase && v.get(i + 1) == Some(&'b') { i += 2; continue; }
            out.push(c);
            if let Some(&n) = v.get(i + 1) { out.push(n); }
            i += 2;
            continue;
        }
        if en_clase { if c == ']' { en_clase = false; } out.push(c); i += 1; continue; }
        if c == '[' {
            en_clase = true; out.push(c); i += 1;
            if v.get(i) == Some(&'^') { out.push('^'); i += 1; }
            if v.get(i) == Some(&']') { out.push(']'); i += 1; }
            continue;
        }
        let es_look = c == '(' && v.get(i + 1) == Some(&'?') && (matches!(v.get(i + 2), Some('=') | Some('!'))
            || (v.get(i + 2) == Some(&'<') && matches!(v.get(i + 3), Some('=') | Some('!'))));
        if es_look {
            // saltar hasta el paréntesis que lo cierra (un cuantificador
            // detrás de una lookaround no tiene sentido y no aparece)
            let mut prof = 0;
            let mut clase = false;
            while i < v.len() {
                let d = v[i];
                if d == '\\' { i += 2; continue; }
                if clase { if d == ']' { clase = false; } i += 1; continue; }
                if d == '[' { clase = true; i += 1; if v.get(i) == Some(&'^') { i += 1; } if v.get(i) == Some(&']') { i += 1; } continue; }
                if d == '(' { prof += 1; }
                if d == ')' { prof -= 1; if prof == 0 { i += 1; break; } }
                i += 1;
            }
            continue;
        }
        out.push(c);
        i += 1;
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn traduce() {
        assert_eq!(traducir(r"\s+\w", false).0, r"[\s\x1C-\x1F]+[\p{L}\p{N}_]");
        assert_eq!(traducir(r"[\s,]\bx", true), (r"[\s\x1C-\x1F,](?-u:\b)x".to_string(), true));
        assert_eq!(traducir(r"tra\W?v", false).0, r"tra[^\p{L}\p{N}_]?v");
        assert_eq!(sin_aserciones(r"\ba(?!x\))[(]", true), "a[(]");
        assert_eq!(sin_aserciones(r"\ba(?<=x)", false), r"\ba");
    }
    #[test]
    fn texto() {
        assert_eq!(strip("\x1c a \u{a0}"), "a");
        assert_eq!(split_ws(" a\x1fb  c ").collect::<Vec<_>>(), vec!["a", "b", "c"]);
        assert_eq!(splitlines("a\r\nb\x0cc\n"), vec!["a", "b", "c"]);
        assert_eq!(cprefix("ñandú", 2), "ña");
        assert_eq!(py_int("0042"), 42);
        assert_eq!(py_int("٣٤"), 34);
    }
}
