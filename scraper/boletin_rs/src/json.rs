// JSON con la forma de Python: escritura como json.dumps(ensure_ascii=False,
// indent=2) y un lector mínimo (para index.json): objetos, listas, cadenas,
// números, true/false/null.
#[derive(Debug, Clone)]
pub enum J { Null, Bool(bool), Num(f64), Str(String), Arr(Vec<J>), Obj(Vec<(String, J)>) }

impl J {
    pub fn get(&self, k: &str) -> Option<&J> {
        match self { J::Obj(v) => v.iter().find(|(kk, _)| kk == k).map(|(_, v)| v), _ => None }
    }
    pub fn as_str(&self) -> Option<&str> { match self { J::Str(s) => Some(s), _ => None } }
    pub fn as_i64(&self) -> Option<i64> { match self { J::Num(n) => Some(*n as i64), _ => None } }
}

struct P<'a> { s: &'a [u8], i: usize }

impl<'a> P<'a> {
    fn ws(&mut self) { while self.i < self.s.len() && matches!(self.s[self.i], b' ' | b'\t' | b'\n' | b'\r') { self.i += 1; } }
    fn val(&mut self) -> Result<J, String> {
        self.ws();
        let c = *self.s.get(self.i).ok_or("fin inesperado")?;
        match c {
            b'{' => {
                self.i += 1; let mut v = Vec::new();
                loop {
                    self.ws();
                    if self.s.get(self.i) == Some(&b'}') { self.i += 1; break; }
                    let k = match self.val()? { J::Str(s) => s, _ => return Err("clave no cadena".into()) };
                    self.ws();
                    if self.s.get(self.i) != Some(&b':') { return Err("falta :".into()); }
                    self.i += 1;
                    let x = self.val()?;
                    v.push((k, x));
                    self.ws();
                    match self.s.get(self.i) { Some(b',') => self.i += 1, Some(b'}') => { self.i += 1; break; } _ => return Err("objeto mal formado".into()) }
                }
                Ok(J::Obj(v))
            }
            b'[' => {
                self.i += 1; let mut v = Vec::new();
                loop {
                    self.ws();
                    if self.s.get(self.i) == Some(&b']') { self.i += 1; break; }
                    v.push(self.val()?);
                    self.ws();
                    match self.s.get(self.i) { Some(b',') => self.i += 1, Some(b']') => { self.i += 1; break; } _ => return Err("lista mal formada".into()) }
                }
                Ok(J::Arr(v))
            }
            b'"' => {
                self.i += 1;
                let mut out: Vec<u16> = Vec::new();
                let mut s = String::new();
                loop {
                    let c = *self.s.get(self.i).ok_or("cadena sin cerrar")?;
                    if c == b'"' { self.i += 1; break; }
                    if c == b'\\' {
                        let e = *self.s.get(self.i + 1).ok_or("escape")?;
                        self.i += 2;
                        let ch = match e {
                            b'n' => '\n', b'r' => '\r', b't' => '\t', b'b' => '\x08', b'f' => '\x0c',
                            b'/' => '/', b'\\' => '\\', b'"' => '"',
                            b'u' => {
                                let h = std::str::from_utf8(&self.s[self.i..self.i + 4]).map_err(|_| "u")?;
                                out.push(u16::from_str_radix(h, 16).map_err(|_| "u")?);
                                self.i += 4;
                                continue;
                            }
                            _ => return Err("escape desconocido".into()),
                        };
                        if !out.is_empty() { s.push_str(&String::from_utf16_lossy(&out)); out.clear(); }
                        s.push(ch);
                        continue;
                    }
                    if !out.is_empty() { s.push_str(&String::from_utf16_lossy(&out)); out.clear(); }
                    let ini = self.i;
                    while self.i < self.s.len() && self.s[self.i] != b'"' && self.s[self.i] != b'\\' { self.i += 1; }
                    s.push_str(std::str::from_utf8(&self.s[ini..self.i]).map_err(|_| "utf8")?);
                }
                if !out.is_empty() { s.push_str(&String::from_utf16_lossy(&out)); }
                Ok(J::Str(s))
            }
            b't' if self.s[self.i..].starts_with(b"true") => { self.i += 4; Ok(J::Bool(true)) }
            b'f' if self.s[self.i..].starts_with(b"false") => { self.i += 5; Ok(J::Bool(false)) }
            b'n' if self.s[self.i..].starts_with(b"null") => { self.i += 4; Ok(J::Null) }
            _ => {
                let ini = self.i;
                while self.i < self.s.len() && matches!(self.s[self.i], b'0'..=b'9' | b'-' | b'+' | b'.' | b'e' | b'E') { self.i += 1; }
                std::str::from_utf8(&self.s[ini..self.i]).ok().and_then(|t| t.parse().ok()).map(J::Num).ok_or_else(|| "número".into())
            }
        }
    }
}

pub fn parse(s: &str) -> Result<J, String> { P { s: s.as_bytes(), i: 0 }.val() }

/// Cadena JSON con los escapes de json.dumps(ensure_ascii=False).
pub fn js(out: &mut String, s: &str) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\x08' => out.push_str("\\b"),
            '\x0c' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

/// repr() de un float de Python.
pub fn py_float(x: f64) -> String {
    let s = format!("{}", x);
    if s.contains('.') || s.contains('e') || s.contains("inf") || s.contains("NaN") { s } else { format!("{}.0", s) }
}

/// round(x, 1) de Python (redondeo correcto sobre el valor binario exacto).
pub fn py_round1(x: f64) -> f64 { format!("{:.1}", x).parse().unwrap() }

pub enum V { I(i64), F(f64), S(String), N }

/// Objeto JSON plano con indent=2, en el nivel `nivel` de indentación.
pub fn obj(out: &mut String, campos: &[(&str, V)], nivel: usize) {
    let ind = "  ".repeat(nivel);
    out.push_str("{\n");
    for (i, (k, v)) in campos.iter().enumerate() {
        out.push_str(&ind); out.push_str("  ");
        js(out, k);
        out.push_str(": ");
        match v {
            V::I(n) => out.push_str(&n.to_string()),
            V::F(f) => out.push_str(&py_float(*f)),
            V::S(s) => js(out, s),
            V::N => out.push_str("null"),
        }
        if i + 1 < campos.len() { out.push(','); }
        out.push('\n');
    }
    out.push_str(&ind); out.push('}');
}

/// Lista de pares [[a, b], ...] como json.dumps.
pub fn jl(out: &mut String, v: &[(String, String)]) {
    out.push('[');
    for (i, (a, b)) in v.iter().enumerate() {
        if i > 0 { out.push_str(", "); }
        out.push('['); js(out, a); out.push_str(", "); js(out, b); out.push(']');
    }
    out.push(']');
}
