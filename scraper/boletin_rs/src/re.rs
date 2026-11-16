// La única envoltura de expresiones regulares del crate, con la semántica
// de re de Python (sintaxis traducida en py::traducir) y los atajos que la
// hacen rápida sin cambiar el resultado.
//
// El problema: el \b Unicode hace que el DFA perezoso del crate regex se
// rinda ante el primer byte no ASCII y caiga a un motor mucho más lento, y
// las lookarounds exigen fancy-regex (backtracking). Tres atajos exactos:
//
// - `ascii`: la misma expresión con \b ASCII, que el DFA sí soporta. Se usa
//   solo si cada \b del patrón está pegado a algo que solo puede ser una
//   letra, dígito o _ ASCII (lo verifica `b_seguro` sobre el árbol de la
//   expresión) y el texto no tiene nada >= U+0100 (ahí el plegado de caja
//   puede salirse de ASCII: ſ, K Kelvin). Con eso, todo match con \b
//   Unicode es match con \b ASCII, así que si la ASCII no encuentra nada la
//   original tampoco, y el match ASCII es el match Unicode si en todo su
//   tramo los dos \b coinciden (ventana_ok). Si no, se corre la original.
// - `gate`: la expresión sin \b ni lookarounds acepta, desde cada posición,
//   un superconjunto. Si no encuentra nada, la original tampoco; y si
//   encuentra, la original no puede empezar antes: se arranca desde ahí
//   (find_at y find_from_pos ven el contexto anterior, igual que search de
//   Python con pos).
// - re.match (anclada al principio) compila aparte, solo si se usa, ^(?:p).
use crate::py::{sin_aserciones, traducir};
use fancy_regex::Regex as FRe;
use regex::Regex as RRe;
use regex_syntax::hir::{Class, Hir, HirKind, Look};
use std::borrow::Cow;
use std::sync::OnceLock;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct M { pub start: usize, pub end: usize }
impl M {
    pub fn as_str<'t>(&self, s: &'t str) -> &'t str { &s[self.start..self.end] }
}

/// Grupos de un match: (inicio, fin) por grupo, None si no participó.
#[derive(Clone, Debug)]
pub struct Caps { g: Vec<Option<(usize, usize)>> }
impl Caps {
    pub fn start(&self) -> usize { self.g[0].unwrap().0 }
    pub fn end(&self) -> usize { self.g[0].unwrap().1 }
    pub fn m(&self) -> M { M { start: self.start(), end: self.end() } }
    pub fn get<'t>(&self, s: &'t str, i: usize) -> Option<&'t str> { self.span(i).map(|(a, b)| &s[a..b]) }
    pub fn span(&self, i: usize) -> Option<(usize, usize)> { self.g.get(i).copied().flatten() }
}

#[derive(Clone)]
enum Motor { R(RRe), F(FRe) }

#[derive(Clone)]
pub struct Re {
    src: String,
    motor: Motor,
    gate: Option<RRe>,
    ascii: Option<RRe>,
    anclada: OnceLock<Box<Re>>,
}

fn caps_r(c: &regex::Captures) -> Caps { Caps { g: (0..c.len()).map(|i| c.get(i).map(|m| (m.start(), m.end()))).collect() } }
fn compilar(p: &str) -> RRe { RRe::new(p).unwrap_or_else(|e| panic!("regex {p}: {e}")) }

impl Re {
    /// Expresión con sintaxis de re de Python (con flags en línea, "(?i)...").
    pub fn new(p: &str) -> Re {
        let (t, tiene_b) = traducir(p, false);
        let con_look = sin_aserciones(p, false) != p;
        let g = sin_aserciones(p, true);
        let gate = if g != p { Some(compilar(&traducir(&g, false).0)) } else { None };
        let motor = if con_look {
            Motor::F(FRe::new(&t).unwrap_or_else(|e| panic!("regex {p}: {e:?}")))
        } else {
            Motor::R(compilar(&t))
        };
        let ascii = if tiene_b && !con_look && b_seguro(&regex_syntax::parse(&t).unwrap()) {
            Some(compilar(&traducir(p, true).0))
        } else { None };
        Re { src: p.to_string(), motor, gate, ascii, anclada: OnceLock::new() }
    }
    /// Con flags aparte: Re::nf(p, "(?i)").
    pub fn nf(p: &str, flags: &str) -> Re { Re::new(&format!("{flags}{p}")) }

    #[cfg(test)]
    fn usa_ascii(&self) -> bool { self.ascii.is_some() }

    #[inline]
    fn por_ascii(&self, s: &str, pos: usize) -> Option<Option<M>> {
        let a = self.ascii.as_ref()?;
        if !latin1(&s.as_bytes()[pos.saturating_sub(4)..]) { return None; }
        match a.find_at(s, pos) {
            None => Some(None),
            Some(m) if ventana_ok(s, m.start(), m.end()) => Some(Some(M { start: m.start(), end: m.end() })),
            Some(_) => None,
        }
    }
    #[inline]
    fn desde(&self, s: &str, pos: usize) -> Option<usize> {
        match &self.gate { Some(g) => g.find_at(s, pos).map(|m| m.start()), None => Some(pos) }
    }

    /// re.search(s, pos)
    pub fn search_at(&self, s: &str, pos: usize) -> Option<M> {
        if let Some(r) = self.por_ascii(s, pos) { return r; }
        let p = self.desde(s, pos)?;
        match &self.motor {
            Motor::R(r) => r.find_at(s, p).map(|m| M { start: m.start(), end: m.end() }),
            Motor::F(f) => f.find_from_pos(s, p).unwrap().map(|m| M { start: m.start(), end: m.end() }),
        }
    }
    pub fn search(&self, s: &str) -> Option<M> { self.search_at(s, 0) }
    pub fn is_search(&self, s: &str) -> bool {
        if self.ascii.is_none() && self.gate.is_none() {
            if let Motor::R(r) = &self.motor { return r.is_match(s); }
        }
        self.search(s).is_some()
    }
    pub fn caps_at(&self, s: &str, pos: usize) -> Option<Caps> {
        if let Some(a) = &self.ascii {
            if latin1(&s.as_bytes()[pos.saturating_sub(4)..]) {
                let c = a.captures_at(s, pos)?;
                let m = c.get(0).unwrap();
                if ventana_ok(s, m.start(), m.end()) { return Some(caps_r(&c)); }
            }
        }
        let p = self.desde(s, pos)?;
        match &self.motor {
            Motor::R(r) => r.captures_at(s, p).map(|c| caps_r(&c)),
            Motor::F(f) => f.captures_from_pos(s, p).unwrap()
                .map(|c| Caps { g: (0..c.len()).map(|i| c.get(i).map(|m| (m.start(), m.end()))).collect() }),
        }
    }
    pub fn caps(&self, s: &str) -> Option<Caps> { self.caps_at(s, 0) }

    fn anclada(&self) -> &Re { self.anclada.get_or_init(|| Box::new(Re::new(&format!("^(?:{})", self.src)))) }
    /// re.match
    pub fn match_(&self, s: &str) -> Option<M> { self.anclada().search(s) }
    pub fn is_match_start(&self, s: &str) -> bool { self.anclada().is_search(s) }
    pub fn match_caps(&self, s: &str) -> Option<Caps> { self.anclada().caps(s) }

    /// re.finditer (una coincidencia vacía avanza un carácter).
    pub fn finditer(&self, s: &str) -> Vec<M> {
        let mut out = Vec::new();
        let mut pos = 0;
        while pos <= s.len() {
            let m = match self.search_at(s, pos) { Some(m) => m, None => break };
            out.push(m);
            pos = if m.end > m.start { m.end } else {
                match s[m.end..].chars().next() { Some(c) => m.end + c.len_utf8(), None => break }
            };
        }
        out
    }
    pub fn finditer_caps(&self, s: &str) -> Vec<Caps> {
        let mut out = Vec::new();
        let mut pos = 0;
        while pos <= s.len() {
            let c = match self.caps_at(s, pos) { Some(c) => c, None => break };
            let (a, b) = (c.start(), c.end());
            out.push(c);
            pos = if b > a { b } else { match s[b..].chars().next() { Some(ch) => b + ch.len_utf8(), None => break } };
        }
        out
    }
    /// re.split(s, maxsplit) para patrones sin grupos (maxsplit 0 = todas).
    pub fn split<'t>(&self, s: &'t str, maxsplit: usize) -> Vec<&'t str> {
        let mut out = Vec::new();
        let mut last = 0;
        for m in self.finditer(s) {
            if maxsplit > 0 && out.len() >= maxsplit { break; }
            out.push(&s[last..m.start]);
            last = m.end;
        }
        out.push(&s[last..]);
        out
    }
    /// re.split(s)[0]
    pub fn split_first<'t>(&self, s: &'t str) -> &'t str {
        match self.search(s) { Some(m) => &s[..m.start], None => s }
    }
    /// re.sub(rep, s, count) con reemplazo literal (count 0 = todas).
    pub fn sub<'t>(&self, s: &'t str, rep: &str, count: usize) -> Cow<'t, str> {
        let mut out = String::new();
        let mut last = 0;
        let mut n = 0;
        let mut pos = 0;
        while pos <= s.len() && (count == 0 || n < count) {
            let m = match self.search_at(s, pos) { Some(m) => m, None => break };
            out.push_str(&s[last..m.start]);
            out.push_str(rep);
            last = m.end;
            n += 1;
            pos = if m.end > m.start { m.end } else {
                match s[m.end..].chars().next() { Some(c) => m.end + c.len_utf8(), None => break }
            };
        }
        if n == 0 { return Cow::Borrowed(s); }
        out.push_str(&s[last..]);
        Cow::Owned(out)
    }
}

#[inline]
fn latin1(b: &[u8]) -> bool { !b.iter().any(|&b| b >= 0xC4) }
#[inline] fn uw(c: Option<char>) -> bool { c.map_or(false, regex_syntax::is_word_character) }
#[inline] fn aw(c: Option<char>) -> bool { c.map_or(false, |c| c.is_ascii_alphanumeric() || c == '_') }
/// ¿Dan lo mismo \b Unicode y \b ASCII en todas las posiciones de [ini, fin]?
fn ventana_ok(s: &str, ini: usize, fin: usize) -> bool {
    if s.as_bytes()[ini.saturating_sub(1)..(fin + 1).min(s.len())].is_ascii() { return true; }
    let mut q = ini;
    loop {
        let (a, b) = (s[..q].chars().next_back(), s[q..].chars().next());
        if (uw(a) != uw(b)) != (aw(a) != aw(b)) { return false; }
        match b { Some(c) if q < fin => q += c.len_utf8(), _ => return true }
    }
}

// ---- ¿el \b ASCII da lo mismo? ----

/// Todo lo que puede matchear `ranges`, dentro de Latin-1, es letra, dígito o _ ASCII.
fn rangos_ok(rs: impl Iterator<Item = (u32, u32)>) -> bool {
    rs.into_iter().all(|(a, b)| (a..=b.min(0xFF)).all(|c| c < 0x80 && ((c as u8).is_ascii_alphanumeric() || c == b'_' as u32)))
}
fn char_ok(c: Option<char>) -> bool { c.map_or(false, |c| c.is_ascii_alphanumeric() || c == '_') }
/// (puede ser vacía, todo primer/último carácter posible es palabra ASCII)
fn borde(h: &Hir, primero: bool) -> (bool, bool) {
    match h.kind() {
        HirKind::Empty | HirKind::Look(_) => (true, true),
        HirKind::Literal(l) => {
            let s = std::str::from_utf8(&l.0).ok();
            let c = s.and_then(|s| if primero { s.chars().next() } else { s.chars().next_back() });
            (false, char_ok(c))
        }
        HirKind::Class(Class::Unicode(c)) => (false, rangos_ok(c.ranges().iter().map(|r| (r.start() as u32, r.end() as u32)))),
        HirKind::Class(Class::Bytes(c)) => (false, rangos_ok(c.ranges().iter().map(|r| (r.start() as u32, r.end() as u32)))),
        HirKind::Repetition(r) => { let (n, ok) = borde(&r.sub, primero); (n || r.min == 0, ok) }
        HirKind::Capture(c) => borde(&c.sub, primero),
        HirKind::Concat(v) => secuencia(v, primero),
        HirKind::Alternation(v) => {
            let mut res = (false, true);
            for h in v { let (n, ok) = borde(h, primero); res.0 |= n; res.1 &= ok; }
            res
        }
    }
}
fn secuencia(v: &[Hir], primero: bool) -> (bool, bool) {
    let mut ok = true;
    let it: Box<dyn Iterator<Item = &Hir>> = if primero { Box::new(v.iter()) } else { Box::new(v.iter().rev()) };
    for h in it {
        let (n, o) = borde(h, primero);
        ok &= o;
        if !n { return (false, ok); }
    }
    (true, ok)
}
/// Cada \b Unicode está, en su secuencia, justo antes de algo que no puede
/// ser vacío y empieza siempre con palabra ASCII, o justo después de algo
/// que termina siempre con palabra ASCII. Cualquier otra aserción de
/// palabra lo descarta.
fn b_seguro(h: &Hir) -> bool {
    match h.kind() {
        HirKind::Look(Look::WordUnicode) => false,
        // \B y las variantes de inicio o fin de palabra Unicode: no se analizan.
        HirKind::Look(l) => !format!("{l:?}").contains("Unicode"),
        HirKind::Empty | HirKind::Literal(_) | HirKind::Class(_) => true,
        HirKind::Repetition(r) => b_seguro(&r.sub),
        HirKind::Capture(c) => b_seguro(&c.sub),
        HirKind::Alternation(v) => v.iter().all(b_seguro),
        HirKind::Concat(v) => v.iter().enumerate().all(|(i, h)| match h.kind() {
            HirKind::Look(Look::WordUnicode) => {
                let (n1, o1) = secuencia(&v[i + 1..], true);
                let (n2, o2) = secuencia(&v[..i], false);
                (!n1 && o1) || (!n2 && o2)
            }
            _ => b_seguro(h),
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn ascii_solo_si_es_seguro() {
        assert!(Re::new(r"(?i)\bS\.?A\b").usa_ascii());
        assert!(Re::new(r"(?i)\b(?:Desech\w*|(?:Se\s+)?Nieg[ao]n?\b)").usa_ascii());
        assert!(!Re::new(r"(?i)s[ií]\b").usa_ascii());
        assert!(!Re::new(r"\b\.").usa_ascii());
        assert!(!Re::new(r"(?:\w|\.)\b").usa_ascii());
    }
    #[test]
    fn como_python() {
        let r = Re::new(r"(?i)\bsi\b");
        assert_eq!(r.search("así si"), Some(M { start: 5, end: 7 }));
        assert_eq!(r.search("ñsi sí"), None);
        let r = Re::new(r"(?i)s[ií]\b");
        assert_eq!(r.search("por sí mismo"), Some(M { start: 4, end: 7 }));
        let f = Re::new(r"\s+y\s+(?=(?:de|la)\s+[A-Z])");
        assert_eq!(f.split("Ana y de Torre y Luis", 0), vec!["Ana", "de Torre y Luis"]);
        assert_eq!(Re::new(r"a").match_("ba"), None);
        assert_eq!(Re::new(r"#+").sub("a##b#", " ", 0), "a b ");
        assert_eq!(Re::new(r"x*").sub("abxd", "-", 0), "-a-b--d-");
    }
}
