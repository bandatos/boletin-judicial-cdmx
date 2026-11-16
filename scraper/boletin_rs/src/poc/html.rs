// Lo que poc.py hace con BeautifulSoup sobre el HTML del portal: el
// csrf-token y, por cada div.modal, el id, el título (h5.modal-title) y el
// src del primer <embed>. Tokenizador mínimo: comentarios fuera, etiquetas
// con sus atributos, anidamiento de <div> para saber dónde termina el modal.
use crate::py::strip;

#[derive(Debug)]
struct Tag { name: String, close: bool, attrs: Vec<(String, String)>, ini: usize, fin: usize }

pub fn unescape(s: &str) -> String {
    if !s.contains('&') { return s.to_string(); }
    let mut out = String::with_capacity(s.len());
    let mut rest = s;
    while let Some(i) = rest.find('&') {
        out.push_str(&rest[..i]);
        rest = &rest[i..];
        let fin = rest.find(';').filter(|&j| j <= 12);
        let rep = fin.and_then(|j| {
            let ent = &rest[1..j];
            let c = if let Some(n) = ent.strip_prefix("#x").or_else(|| ent.strip_prefix("#X")) { u32::from_str_radix(n, 16).ok().and_then(char::from_u32) }
                else if let Some(n) = ent.strip_prefix('#') { n.parse::<u32>().ok().and_then(char::from_u32) }
                else { match ent { "amp" => Some('&'), "lt" => Some('<'), "gt" => Some('>'), "quot" => Some('"'), "apos" => Some('\''),
                    "nbsp" => Some('\u{a0}'), "times" => Some('×'), _ => None } };
            c.map(|c| (c, j + 1))
        });
        match rep { Some((c, n)) => { out.push(c); rest = &rest[n..]; } None => { out.push('&'); rest = &rest[1..]; } }
    }
    out.push_str(rest);
    out
}

fn sin_comentarios(h: &str) -> String {
    let mut out = String::with_capacity(h.len());
    let mut rest = h;
    while let Some(i) = rest.find("<!--") {
        out.push_str(&rest[..i]);
        match rest[i + 4..].find("-->") { Some(j) => rest = &rest[i + 4 + j + 3..], None => { rest = ""; } }
        out.push(' ');
    }
    out.push_str(rest);
    out
}

fn tags(h: &str) -> Vec<Tag> {
    let b = h.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while let Some(off) = h[i..].find('<') {
        let ini = i + off;
        let mut j = ini + 1;
        let close = b.get(j) == Some(&b'/');
        if close { j += 1; }
        let ns = j;
        while j < b.len() && (b[j].is_ascii_alphanumeric()) { j += 1; }
        if j == ns { i = ini + 1; continue; }
        let name = h[ns..j].to_ascii_lowercase();
        let mut attrs = Vec::new();
        // atributos
        loop {
            while j < b.len() && (b[j].is_ascii_whitespace() || b[j] == b'/') { j += 1; }
            if j >= b.len() || b[j] == b'>' { break; }
            let an = j;
            while j < b.len() && !b[j].is_ascii_whitespace() && b[j] != b'=' && b[j] != b'>' && b[j] != b'/' { j += 1; }
            let aname = h[an..j].to_ascii_lowercase();
            let mut k = j;
            while k < b.len() && b[k].is_ascii_whitespace() { k += 1; }
            let mut val = String::new();
            if k < b.len() && b[k] == b'=' {
                k += 1;
                while k < b.len() && b[k].is_ascii_whitespace() { k += 1; }
                if k < b.len() && (b[k] == b'"' || b[k] == b'\'') {
                    let q = b[k];
                    let vs = k + 1;
                    let ve = h[vs..].find(q as char).map(|x| vs + x).unwrap_or(b.len());
                    val = unescape(&h[vs..ve]);
                    k = (ve + 1).min(b.len());
                } else {
                    let vs = k;
                    while k < b.len() && !b[k].is_ascii_whitespace() && b[k] != b'>' { k += 1; }
                    val = unescape(&h[vs..k]);
                }
                j = k;
            }
            if aname.is_empty() { j += 1; continue; }
            attrs.push((aname, val));
        }
        let fin = (j + 1).min(b.len());
        out.push(Tag { name, close, attrs, ini, fin });
        i = fin;
    }
    out
}

impl Tag {
    fn attr(&self, k: &str) -> Option<&str> { self.attrs.iter().find(|(a, _)| a == k).map(|(_, v)| v.as_str()) }
    fn has_class(&self, c: &str) -> bool { self.attr("class").map_or(false, |v| v.split_ascii_whitespace().any(|x| x == c)) }
}

fn texto(h: &str) -> String {
    let mut out = String::new();
    let mut en_tag = false;
    let mut ini = 0;
    for (i, c) in h.char_indices() {
        if !en_tag && c == '<' { out.push_str(&h[ini..i]); en_tag = true; }
        else if en_tag && c == '>' { en_tag = false; ini = i + 1; }
    }
    if !en_tag { out.push_str(&h[ini..]); }
    unescape(&out)
}

pub fn csrf_token(html: &str) -> Option<String> {
    let h = sin_comentarios(html);
    tags(&h).into_iter().find(|t| !t.close && t.name == "meta" && t.attr("name") == Some("csrf-token"))
        .and_then(|t| t.attr("content").map(|s| s.to_string()))
}

pub struct Modal { pub id: String, pub titulo: Option<String>, pub embed_src: Option<String> }

pub fn modales(html: &str) -> Vec<Modal> {
    let h = sin_comentarios(html);
    let ts = tags(&h);
    let mut out = Vec::new();
    for (k, t) in ts.iter().enumerate() {
        if t.close || t.name != "div" || !t.has_class("modal") { continue; }
        // fin del div: siguiente </div> que lo cierra
        let mut prof = 0;
        let mut fin = ts.len();
        for (kk, u) in ts.iter().enumerate().skip(k + 1) {
            if u.name == "div" {
                if u.close { if prof == 0 { fin = kk; break; } prof -= 1; } else { prof += 1; }
            }
        }
        let dentro = &ts[k + 1..fin];
        let titulo = dentro.iter().position(|u| !u.close && u.name == "h5" && u.has_class("modal-title")).map(|p| {
            let u = &dentro[p];
            let cierre = dentro[p + 1..].iter().find(|v| v.close && v.name == "h5").map(|v| v.ini).unwrap_or(h.len());
            strip(&texto(&h[u.fin..cierre])).to_string()
        });
        let embed_src = dentro.iter().find(|u| !u.close && u.name == "embed").map(|u| u.attr("src").unwrap_or("").to_string());
        out.push(Modal { id: t.attr("id").unwrap_or("").to_string(), titulo, embed_src });
    }
    out
}
