// Port de scraper/personas.py (personas) y scraper/empresas.py (partes).
// Índices siempre en bytes sobre &str; cada slice se trata como string nuevo,
// igual que los slices de Python (lookbehind y \b no ven fuera del slice).
use crate::cache::{memo, Cache};
use crate::py::{clen, is_space, split_ws, strip, strip_c};
use crate::re::Re;
use crate::sets;
use crate::tipos::{may_, min_};
use std::cell::RefCell;
use std::rc::Rc;
use std::collections::HashSet;
use std::sync::OnceLock;
use unicode_normalization::char::{decompose_canonical, is_combining_mark};

const ROLES: &str = r"(?:cesionari[oa]s?|subrogatari[oa]s?|causahabientes?(?:\s+por\s+fusi[oó]n)?|mandatari[oa]s?|endosatari[oa]s?(?:\s+en\s+procuraci[oó]n)?|fusionante|fiduciari[oa]\s+sustitut[oa])";
const PALABRA: &str = r"[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ'´]+";

struct R {
    // personas.py
    acdo_inicial: Re,
    sep_particula: Re,
    y_final: Re,
    sucesion: Re,
    sucesion_previa: Re,
    fin_nombre: Re,
    palabra_nombre: Re,
    // empresas.py
    forma_final: Re,
    forma_fuerte: Re,
    forma_corta_final_pos: Re,
    forma_final_corta: Re,
    prefijo_persona: Re,
    cola_societaria: Re,
    resto_encabezado: Re,
    corte_previo: Re,
    fin_oracion: Re,
    encabezado_inicial: Re,
    minusculas_iniciales: Re,
    y_otros: Re,
    y_otros_match: Re,
    separador: Re,
    sep_neg: Re,
    sep_pos: Re,
    separador_rol: Re,
    inicio_no_nombre: Re,
    solo_otros: Re,
    sospechoso: Re,
    corte_persona: Re,
    palabras: Re,
    banco: Re,
    banco_prev: Re,
    dos_letras: Re,
    // sets
    no_persona: HashSet<&'static str>,
    pila: HashSet<&'static str>,
    particulas: HashSet<&'static str>,
    palabras_forma: HashSet<&'static str>,
    genericos: HashSet<&'static str>,
    genericos_derecha: HashSet<&'static str>,
}

fn set(v: &'static [&'static str]) -> HashSet<&'static str> { v.iter().copied().collect() }

impl R {
    fn new() -> R {
        let y_otros = r"[\s,]+y\s+(?:otr[oa]s?|coags?\.?)\s*\.?\s*$";
        R {
            acdo_inicial: Re::new(r"(?i)^[\s,.;:-]*(?:Pon\s+\d+\s*,\s*)?(?:\d+\s+(?:Acdos?|Sents?|Aud)\.?\s*)?"),
            sep_particula: Re::new(r"\s+y\s+(?=(?:(?:de|del|la|las|los)\s+)+[A-ZÁÉÍÓÚÑ])"),
            y_final: Re::new(r"\s+(?:y|e)$"),
            sucesion: Re::new(r"(?i)\s+(?:su\s+)?suc(?:esi[oó]n|\.)?\s*$"),
            sucesion_previa: Re::new(r"(?i)^(?:la\s+)?sucesi[oó]n\s+(?:testamentaria\s+|intestamentaria\s+)?(?:a\s+bienes\s+)?de\s+"),
            fin_nombre: Re::new(r"(?i)\.(?:\s|$)|\s+-\s+|\(|,|\s+y/o\s+|\s+(?:y\s+)?(?:por\s+(?:su\s+)?(?:propio|s[ií]\b)|en\s+(?:su\s+)?representaci|quien\s)"),
            palabra_nombre: Re::new(&format!(r"^(?:{p}(?:-{p})?|[A-ZÁÉÍÓÚÑ]\.?)$", p = PALABRA)),
            // Corre sobre texto normalizado (solo [a-z0-9ñ ]): ahí \w es [0-9a-zñ_].
            forma_final: Re::new(&format!(r"(?:\s+(?:{}))+\s*$", sets::FORMAS.join("|").replace(r"\w", "[0-9a-zñ_]"))),
            // El lookahead de "S.A." se consume en el grupo `la`: el fin real
            // del match es el inicio del grupo (mismo orden de alternativas).
            forma_fuerte: Re::new(concat!(
                r"(?i)\bS\.?\s?A\.?(?:\s?P\.?\s?I\.?|\s?B\.?)?\s+de\s+C\.?\s?V\b",
                r"|\bS\.?\s?A\.?(?P<la>\s*(?:,|$|\s+Instituci|\s+Grupo|\s+Sofom))",
                r"|\bS\.?\s?de\s?R\.?\s?L\b",
                r"|\bSofom\b|\bSociedad\s+An[oó]nima\b|\bSociedad\s+de\s+Responsabilidad\b",
                r"|\bSociedad\s+Civil\b|\bAsociaci[oó]n\s+Civil\b|\bInstituci[oó]n\s+de\s+Banca\b",
                r"|\bSociedad\s+Financiera\b|\bSociedad\s+Nacional\s+de\s+Cr[eé]dito\b",
                r"|\bSociedad\s+Cooperativa\b|\bS\.?\s?N\.?\s?C\b")),
            forma_corta_final_pos: Re::new(r"(?i)\s(?:A\.?\s?C|S\.?\s?C)\.?\s*$"),
            forma_final_corta: Re::new(r"(?i)^\S+\s+\S+.*\s(?:A\.?\s?C|S\.?\s?C)\.?\s*$"),
            prefijo_persona: Re::new(concat!(
                r"(?i)^(.*?)\b(?:en\s+(?:su\s+)?(?:car[aá]cter|calidad|representaci[oó]n)\s+de",
                r"|apoderad[oa]s?\s+(?:legal(?:es)?\s+)?de|representante\s+legal\s+de",
                r"|por\s+conducto\s+de|a\s+trav[eé]s\s+de",
                r"|(?:secretari[oa]|director[a]?|gerente)\s+general\s+de(?:l|\s+la)?",
                r"|administrador[a]?\s+[uú]nic[oa]\s+de(?:l|\s+la)?",
                r"|(?:secretari[oa]|president[ea])\s+del?\s+consejo\s+de\s+administraci[oó]n\s+de(?:l|\s+la)?",
                r"|socio\s+accionista\s+de(?:\s+la)?(?:\s+moral)?|accionista\s+de\s+la\s+moral)\s+")),
            cola_societaria: Re::new(concat!(
                r"(?i)^[\s,\.]*(?:",
                r"de\s+C\.?\s?V\.?|de\s+Capital\s+Variable|Sociedad\s+An[oó]nima(?:\s+de\s+Capital\s+Variable)?",
                r"|(?:Instituci[oó]n\s+de\s+Banca\s+)?(?:M[uú]ltiple|de\s+Desarrollo)",
                r"|Grupo\s+Financiero(?:\s+(?!(?:antes|hoy|como|en|de|del|su|y|e|quien|a|al|el|la|los|divisi[oó]n|cesionari\w*|causahabientes?|mandatari\w*)\b)[A-ZÁÉÍÓÚÑ][\w\&]*){1,2}",
                r"|Sociedad\s+Financiera\s+de\s+Objeto\s+M[uú]ltiple(?:,?\s+Entidad\s+(?:No\s+)?Regulada)?",
                r"|Sofom(?:,?\s+E\.?\s?N\.?\s?R\.?|,?\s+E\.?\s?R\.?)?|E\.?\s?N\.?\s?R\.?",
                r"|S\.?\s?N\.?\s?C\.?|Sociedad\s+Nacional\s+de\s+Cr[eé]dito",
                r"|S\.?\s?A\.?\s?P\.?\s?I\.?|S\.?\s?A\.?\s?B\.?|S\.?\s?A\.?|de\s+R\.?\s?L\.?",
                r")")),
            resto_encabezado: Re::new(r"(?i)\b(?:Juzgados?\s+de\s+lo|Secretar[ií]a\b|Acuerdos\s+del|Bolet[ií]n\s+Judicial)"),
            corte_previo: Re::new(concat!(
                r"(?i)(?:(?:\b|(?<=\d))(?:antes|hoy|ahora|actualmente|vs\.?|promueve|promovido\s+por|promov\.?(?:\s+por)?|promovente:?",
                r"|quejos[oa]:?|tercero\s+interesado:?",
                r"|representad[oa]s?(?:\s+en\s+este\s+acto)?\s*por(?:\s+su\s+(?:apoderad[oa]|fiduciari[oa]|mandatari[oa]))?",
                r"|sucesionari[oa]s?(?:\s+de)?|conocid[oa]s?\b.{0,60}?\bcomo",
                r"|(?:por\s+)?(?:conducto\s+de\s+)?su\s+(?:apoderad[oa]|fiduciari[oa])(?:\s+legal)?",
                r"|tambi[eé]n\s+conocid[oa]\s+(?:por\s+sus\s+abreviaturas\s+)?(?:como|por)",
                r")",
                r"|\d+/\d{4}(?:-[A-Z0-9]+)?)[\s,.:]+")),
            fin_oracion: Re::new(r"(?:[.:;])\s+(?=[A-ZÁÉÍÓÚÑ0-9])"),
            encabezado_inicial: Re::new(r"^(?:SENTENCIAS?|AUDIENCIAS?|ACUERDOS?|NO\s+PUBLICAD[OA]S?|MAL\s+PUBLICAD[OA]S?)\b[\s.:]*"),
            minusculas_iniciales: Re::new(r"^(?:[a-záéíóúñ][\w´'-]*\s+)+"),
            y_otros: Re::new(&format!("(?i){}", y_otros)),
            y_otros_match: Re::new(&format!("(?i)^{}", y_otros)),
            // RE_SEPARADOR sin lookarounds: candidato + chequeo a mano (ver split_separador).
            separador: Re::new(r"\s*;\s*|\s+(?:y|e)\s+|,\s+"),
            sep_neg: Re::new(r"^(?:Instituci[oó]n|Grupo\s+Financiero|Sociedad|Sofom|Entidad|S\.|Enr|Er)\b"),
            sep_pos: Re::new(r"^[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s"),
            separador_rol: Re::new(&(String::from(r"(?i)[\s,(]+(?:y\s+)?(?:como\s+|en\s+su\s+car[aá]cter\s+(?:de|como)\s+|a\s*tra\W?v[eé][sz]\s+de\s+)?(?:su\s+)?")
                + ROLES + concat!(r"(?:\s+de\s+los\s+derechos\s+\w+)?(?:\s+(?:del?|de\s+la))?[\s,]+",
                r"|[\s,(]+(?:anteri?o?r?mente|antes\s+denominad[oa]|tambi[eé]n\s+identificad[oa]\s+como)[\s,]+",
                r"|\s*\(\s*(?:antes|anteri?o?r?mente|hoy)\s*\)\s*|[\s,(]+ntes[\s,)]+",
                r"|\s+y/o\s+|[\s,]+quien\s+(?:tambi[eé]n\s+)?se\s+ostenta\s+como[\s,]+",
                r"|[\s,]+quien\s+opera\s+bajo\s+la\s+raz[oó]n\s+social[\s,]+",
                r"|[\s,]+por\s+(?:su\s+)?(?:propio\s+derecho|derecho\s+propio)(?:\s+y)?(?:\s+como\s+(?:\w+\s+){0,3}?del?)?[\s,]+",
                r"|[\s,]+contra\s+(?!(?:incendios?|fuegos?|el|la|los|las|todo)\b)",
                r"|[\s,]*(?<![\w/])\d{1,2}\)\s*",
                r"|[\s,]*\bnotari[oa]\s+p[uú]blic[oa]\b[^,]*,\s*"))),
            inicio_no_nombre: Re::new(&(String::from(r"(?i)^(?:su\s+)?(?:") + ROLES + concat!(r"|anteri?o?r?mente|denominad[oa])(?:\s+(?:del?|de\s+la))?\s+",
                r"|^(?:y\s+)?(?:de\s+)?(?:la\s+)?(?:persona\s+moral|sociedad)\s+denominada\s+|^persona\s+moral\s+",
                r"|^(?:(?:primer|segundo|tercer)\s+tomo|tomo\s+(?:[ivxl]+|\d+))\b\.?\s*"))),
            solo_otros: Re::new(r"(?i)^(?:y\s+)?otr[oa]s?\.?$"),
            sospechoso: Re::new(concat!(
                r"(?i)\bamparo\b|\bpromov|\bquejos|\bsucesi[oó]n\b|\balbacea|\bapoderad|\bLic\.|\d+/\d{4}",
                r"|representad[oa]s?\s*por|\bconocid[oa]\s+(?:como|por)\b|\bvs\.",
                r"|\bmenor(?:es)?\b|\blicenciad[oa]\b|\bnotari[oa]\b")),
            corte_persona: Re::new(r",\s+|\s+y\s+(?:la|el|los|las|su|sus|por\s+el|por\s+la)\s+"),
            palabras: Re::new(r"[A-Za-zÁÉÍÓÚÑáéíóúñ0-9&]{2,}"),
            banco: Re::new(r"\bBanco\b"),
            banco_prev: Re::new(r"(?i)\b(?:de|del|y|e|el|la|al|su)\s+$"),
            dos_letras: Re::new(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]{2}"),
            no_persona: set(sets::NO_PERSONA),
            pila: set(sets::NOMBRES_PILA),
            particulas: set(sets::PARTICULAS),
            palabras_forma: set(sets::PALABRAS_FORMA),
            genericos: set(sets::GENERICOS),
            genericos_derecha: set(sets::GENERICOS_DERECHA),
        }
    }
}

// RE_PEGADO.sub(" ", s): (?<=[a-záéíóúñ]{2})(?=[A-ZÁÉÍÓÚÑ][a-záéíóúñ])
fn despegar(s: &str) -> String {
    let c: Vec<char> = s.chars().collect();
    let mut out = String::with_capacity(s.len() + 4);
    for i in 0..c.len() {
        if i >= 2 && i + 1 < c.len() && min_(c[i - 2]) && min_(c[i - 1]) && may_(c[i]) && min_(c[i + 1]) { out.push(' '); }
        out.push(c[i]);
    }
    out
}

/// RE_PREFIJO_MAYUS.sub("", s) = ^(?:U{2,}\s+){2,}(?=U L). Cada grupo tiene
/// que ser una racha máxima de mayúsculas más toda la racha de espacios (si
/// no, el \s+ siguiente no encaja o el lookahead ve un espacio). Con menos
/// grupos que los máximos el lookahead ve dos mayúsculas y falla: solo
/// queda probar con todos.
fn sin_prefijo_mayus(s: &str) -> &str {
    let b: Vec<(usize, char)> = s.char_indices().collect();
    let (mut i, mut grupos) = (0, 0);
    loop {
        let j0 = i;
        while i < b.len() && may_(b[i].1) { i += 1; }
        if i - j0 < 2 { i = j0; break; }
        let k0 = i;
        while i < b.len() && is_space(b[i].1) { i += 1; }
        if i == k0 { i = j0; break; }
        grupos += 1;
    }
    if grupos >= 2 && i + 1 < b.len() && may_(b[i].1) && min_(b[i + 1].1) { &s[b[i].0..] } else { s }
}

fn sub(r: &Re, s: &str) -> String { r.sub(s, "", 0).into_owned() }

/// NFD, sin marcas, minúsculas, [^a-z0-9ñ ]+ -> " ", \s+ -> " ", strip.
fn norm_base(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut pend_space = false;
    let mut push = |c: char, out: &mut String| {
        if c.is_ascii_lowercase() || c.is_ascii_digit() || c == 'ñ' {
            if pend_space && !out.is_empty() { out.push(' '); }
            pend_space = false;
            out.push(c);
        } else {
            pend_space = true;
        }
    };
    for ch in s.chars() {
        let mut buf: [char; 8] = ['\0'; 8];
        let mut n = 0;
        decompose_canonical(ch, |d| { if !is_combining_mark(d) && n < 8 { buf[n] = d; n += 1; } });
        for &d in &buf[..n] {
            for l in d.to_lowercase() { push(l, &mut out); }
        }
    }
    out
}

impl R {
    // ---------- empresas.py ----------
    /// RE_SEPARADOR.split: en cada posición solo una alternativa puede
    /// empezar (";" con espacios, espacio, coma). La de ";" no tiene
    /// condición. Las otras dos terminan en \s+ codicioso y el lookahead pide
    /// un carácter no espacio: si falla, achicar \s+ no lo arregla, así que
    /// no hay match en esa posición y se sigue en la siguiente.
    fn split_separador<'a>(&self, s: &'a str) -> Vec<&'a str> {
        let mut out = Vec::new();
        let (mut last, mut pos) = (0, 0);
        while pos <= s.len() {
            let m = match self.separador.search_at(s, pos) { None => break, Some(m) => m };
            let txt = m.as_str(s);
            let resto = &s[m.end..];
            let ok = if txt.contains(';') {
                true
            } else if txt.starts_with(',') {
                !self.sep_neg.is_search(resto) && self.sep_pos.is_search(resto)
            } else {
                resto.chars().next().map_or(false, may_)
            };
            if ok {
                out.push(&s[last..m.start]);
                last = m.end;
                pos = m.end;
            } else {
                pos = m.start + s[m.start..].chars().next().unwrap().len_utf8();
            }
        }
        out.push(&s[last..]);
        out
    }
    fn emp_normalizar(&self, s: &str) -> String {
        let b = norm_base(s);
        strip(&sub(&self.forma_final, &b)).to_string()
    }
    fn nucleo_valido(&self, n: &str) -> bool {
        !n.is_empty() && clen(n) >= 6 && !self.genericos.contains(n)
            && !n.chars().all(|c| c.is_ascii_digit())
            && !split_ws(n).all(|w| self.palabras_forma.contains(w))
    }
    fn fuerte(&self, t: &str) -> Option<(usize, usize)> {
        let c = self.forma_fuerte.caps(t)?;
        // grupo 1: (?P<la>...)
        Some((c.start(), c.span(1).map_or(c.end(), |g| g.0)))
    }
    fn fuerte_o_corta(&self, t: &str) -> Option<usize> {
        self.fuerte(t).map(|m| m.0).or_else(|| self.forma_corta_final_pos.search(t).map(|m| m.start))
    }
    fn es_empresa(&self, t: &str) -> bool {
        self.forma_fuerte.is_search(t) || self.forma_final_corta.is_search(t)
    }
    fn emp_limpiar(&self, trozo: &str) -> String {
        let mut t = sub(&self.y_otros, strip(trozo));
        loop {
            let anterior = t.clone();
            if let Some(c) = self.prefijo_persona.caps(&t) {
                let izq = strip_c(c.get(&t, 1).unwrap(), " ,");
                let der = &t[c.end()..];
                let nuevo = if self.es_empresa(izq) && !self.es_empresa(der) { izq } else { der };
                t = nuevo.to_string();
            }
            if t == anterior { break; }
        }
        let mut t = sin_prefijo_mayus(&t).to_string();
        loop {
            let anterior = t.clone();
            t = sub(&self.inicio_no_nombre, strip_c(&t, " ,.;"));
            if t == anterior { break; }
        }
        strip_c(&t, " ,.;").to_string()
    }
    fn n_palabras(&self, s: &str) -> usize { self.palabras.finditer(s).len() }

    fn recortar_inicio(&self, t0: &str) -> String {
        let mut t: String = sub(&self.encabezado_inicial, t0);
        let m = match self.fuerte_o_corta(&t) { None => return t, Some(m) => m };
        let mut antes: String = t[..m].to_string();
        for (rx, minimo) in [(&self.corte_previo, 1usize), (&self.fin_oracion, 2usize)] {
            let mut ultimo = None;
            for c in rx.finditer(&antes) {
                if self.n_palabras(&antes[c.end..]) >= minimo { ultimo = Some(c.end); }
            }
            if let Some(e) = ultimo {
                t = t[e..].to_string();
                antes = match self.fuerte_o_corta(&t) { Some(m) => t[..m].to_string(), None => t.clone() };
            }
        }
        let abre = antes.matches('(').count();
        if abre > antes.matches(')').count() {
            let ri = antes.rfind('(').unwrap();
            if self.n_palabras(&antes[ri + 1..]) >= 1 {
                t = sub(&self.inicio_no_nombre, strip(&t[ri + 1..]));
                antes = match self.fuerte_o_corta(&t) { Some(m) => t[..m].to_string(), None => t.clone() };
            }
        }
        let mut ultimo = None;
        for b in self.banco.finditer(&antes) {
            if b.start > 0 && !self.banco_prev.is_search(&antes[..b.start]) && self.n_palabras(&antes[b.end..]) >= 1 {
                ultimo = Some(b.start);
            }
        }
        if let Some(c) = ultimo { t = t[c..].to_string(); }
        sub(&self.minusculas_iniciales, &t)
    }
    fn cortar_en_forma(&self, t: &str) -> String {
        let mut fin = match self.fuerte(t) { None => return t.to_string(), Some(m) => m.1 };
        loop {
            match self.cola_societaria.search(&t[fin..]) {
                Some(c) if c.end != 0 => fin += c.end,
                _ => break,
            }
        }
        strip_c(&t[..fin], " ,;").to_string()
    }
    fn persona_delante<'a>(&self, t: &'a str) -> Option<(&'a str, &'a str)> {
        let m = self.corte_persona.search(t)?;
        if !self.es_empresa(&t[m.end..]) || self.es_empresa(&t[..m.start]) { return None; }
        let (izq, der) = (&t[..m.start], &t[m.end..]);
        let il = self.per_limpiar(izq);
        if !self.es_nombre(&il) { return None; }
        if !split_ws(&self.emp_normalizar(&il)).any(|w| self.pila.contains(w)) { return None; }
        let base = match self.fuerte_o_corta(der) { Some(f) => &der[..f], None => der };
        let n = self.emp_normalizar(base);
        if split_ws(&n).any(|w| !self.genericos_derecha.contains(w)) { Some((izq, der)) } else { None }
    }
    fn persona_al_inicio(&self, nombre: &str) -> bool {
        let base = match self.fuerte_o_corta(nombre) { Some(m) => &nombre[..m], None => nombre };
        let palabras: Vec<&str> = split_ws(base).filter(|w| !self.particulas.contains(w.to_lowercase().as_str())).collect();
        if palabras.len() < 4 || !palabras[..3].iter().all(|w| self.palabra_nombre.is_search(w)) { return false; }
        let norm: Vec<String> = palabras[..3].iter().map(|w| norm_base(w)).collect();
        if norm.iter().any(|w| self.no_persona.contains(w.as_str())) { return false; }
        self.pila.contains(norm[0].as_str()) || self.pila.contains(norm[2].as_str())
    }
    fn trozos<'a>(&self, campo: &str) -> Vec<String> {
        let mut salida = Vec::new();
        let pegado = despegar(campo);
        for parte in self.split_separador(&pegado) {
            for t in self.separador_rol.split(parte, 0) {
                if strip(t).is_empty() { continue; }
                match self.persona_delante(t) {
                    Some((a, b)) => { salida.push(a.to_string()); salida.push(b.to_string()); }
                    None => salida.push(t.to_string()),
                }
            }
        }
        salida
    }
    /// partes() de empresas.py sobre los trozos ya limpios: (trozo limpio, es_empresa).
    fn partes_de(&self, limpios: &[(String, bool)]) -> (Vec<(String, String)>, bool) {
        let mut empresas = Vec::new();
        let mut otras = false;
        for (t, es_emp) in limpios {
            if t.is_empty() || self.solo_otros.is_search(t) { continue; }
            if *es_emp && !self.resto_encabezado.is_search(t) {
                let t2 = self.cortar_en_forma(&self.recortar_inicio(t));
                let n = self.emp_normalizar(&t2);
                if self.nucleo_valido(&n) && !self.sospechoso.is_search(&t2) && !self.persona_al_inicio(&t2) {
                    empresas.push((n, t2));
                    continue;
                }
            }
            if self.dos_letras.is_search(t) && !self.y_otros_match.is_search(&format!(" {}", t)) { otras = true; }
        }
        (empresas, otras)
    }

    // ---------- personas.py ----------
    fn per_limpiar(&self, trozo: &str) -> String {
        let mut t: String = strip(trozo).to_string();
        let bajo = t.to_lowercase();
        if bajo.contains("otr") || bajo.contains("coag") { t = sub(&self.y_otros, &t); }
        let primero = t.chars().next();
        let tres: String = t.chars().take(3).collect::<String>().to_lowercase();
        if primero.map_or(false, |c| " \t\n,.;:-0123456789".contains(c)) || tres == "pon" {
            t = sub(&self.acdo_inicial, &t);
        }
        if let Some(m) = self.fin_nombre.search(&t) { t.truncate(m.start); }
        let mut t = strip(&t).to_string();
        if t.to_lowercase().contains("suc") {
            t = sub(&self.sucesion_previa, &t);
            t = sub(&self.sucesion, &t);
        }
        let t = strip_c(&t, " ,.;:-");
        let cs: Vec<char> = t.chars().rev().take(2).collect();
        let penult_space = cs.len() == 2 && is_space(cs[1]);
        let fin2 = t.ends_with(" y") || t.ends_with(" e") || t.ends_with("\ty") || t.ends_with("\te");
        if fin2 || penult_space { sub(&self.y_final, t) } else { t.to_string() }
    }
    fn es_nombre(&self, t: &str) -> bool {
        let palabras: Vec<&str> = split_ws(t).collect();
        if palabras.len() < 2 || palabras.len() > 8 { return false; }
        let mut propias = 0;
        for p in &palabras {
            if self.particulas.contains(p.to_lowercase().as_str()) { continue; }
            if !self.palabra_nombre.is_search(p) { return false; }
            if clen(p.trim_end_matches('.')) > 1 { propias += 1; }
        }
        if propias < 2 { return false; }
        let n = norm_base(t);
        let ws: Vec<&str> = split_ws(&n).filter(|w| !self.particulas.contains(w)).collect();
        if ws.iter().any(|w| self.no_persona.contains(w)) { return false; }
        let pila = ws.iter().filter(|w| self.pila.contains(*w)).count();
        if pila > 0 && pila == ws.len() { return false; }
        pila > 0 || ws.len() >= 3
    }
    /// personas() de personas.py. Cada trozo se parte en RE_SEPARADOR_PARTICULA;
    /// si queda entero, su limpieza ya está hecha (la de partes).
    fn personas_de(&self, trozos: &[String], limpios: &[(String, bool)]) -> Vec<(String, String)> {
        let mut salida = Vec::new();
        let mut agregar = |t: &str, es_emp: bool| {
            if t.is_empty() || es_emp { return; }
            let t = self.per_limpiar(t);
            if !t.is_empty() && self.es_nombre(&t) {
                let n = norm_base(&t);
                if clen(&n) >= 8 { salida.push((n, t)); }
            }
        };
        for (parte, (limpio, es_emp)) in trozos.iter().zip(limpios) {
            let piezas = self.sep_particula.split(parte, 0);
            if piezas.len() == 1 { agregar(limpio, *es_emp); continue; }
            for trozo in piezas {
                let t = self.emp_limpiar(trozo);
                let e = !t.is_empty() && self.es_empresa(&t);
                agregar(&t, e);
            }
        }
        salida
    }

    fn detectar(&self, campo: &str) -> Deteccion {
        if campo.is_empty() { return Deteccion::default(); }
        let trozos = self.trozos(campo);
        let limpios: Vec<(String, bool)> = trozos.iter().map(|t| {
            let l = self.emp_limpiar(t);
            let e = !l.is_empty() && self.es_empresa(&l);
            (l, e)
        }).collect();
        let (empresas, otras) = self.partes_de(&limpios);
        Deteccion { personas: self.personas_de(&trozos, &limpios), empresas, otras }
    }
}

/// Lo que se detecta en un campo actora o demandada: personas (núcleo,
/// nombre), empresas (núcleo, nombre) y si hay otras partes además de las
/// empresas (el `otras` de empresas.partes).
#[derive(Default, Debug)]
pub struct Deteccion {
    pub personas: Vec<(String, String)>,
    pub empresas: Vec<(String, String)>,
    pub otras: bool,
}

fn r() -> &'static R {
    static R_: OnceLock<R> = OnceLock::new();
    R_.get_or_init(R::new)
}

thread_local! {
    static C_DETECTAR: RefCell<Cache<Rc<Deteccion>>> = RefCell::new(Cache::new(500_000));
}

/// personas.personas(campo) y empresas.partes(campo) juntas, con caché por
/// campo (empresas.partes la tiene en Python): comparten los trozos.
pub fn detectar(campo: &str) -> Rc<Deteccion> { memo(&C_DETECTAR, campo, || Rc::new(r().detectar(campo))) }
/// detectar sin pasar por la caché (para medir).
pub fn detectar_sin_cache(campo: &str) -> Deteccion { r().detectar(campo) }
pub fn personas(campo: &str) -> Vec<(String, String)> { detectar(campo).personas.clone() }
pub fn partes(campo: &str) -> (Vec<(String, String)>, bool) { let d = detectar(campo); (d.empresas.clone(), d.otras) }
