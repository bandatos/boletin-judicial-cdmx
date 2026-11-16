// Port de scraper/acuerdos.py: tipo_acuerdo, descripcion, separar_expediente,
// separar_parte, descripcion_de_sobrante, quitar_marca_publicacion. Lo usan
// el parser (poc) y el precálculo (derivar). Índices en bytes sobre &str;
// cada slice se trata como string nuevo, igual que los slices de Python.
use crate::cache::{memo, Cache};
use crate::py::{cap_first, clen, cprefix, lstrip, rstrip_c, split_ws, strip, strip_c};
use crate::re::Re;
use crate::tipos::{es_stop_word, expand_abbr, match_canonical_prefix};
use std::cell::RefCell;
use std::sync::OnceLock;

struct A {
    vs: Re,
    neg: Re,
    prev: Re,
    adm: Re,
    adm_exh: Re,
    cola: Re,
    ugj: Re,
    ugj_cola: Re,
    desc_vacia: Re,
    hashes: Re,
    ws: Re,
    desc_inicio: Re,
    desc_publ: Re,
    desc_codigo: Re,
    desc_num: Re,
    num_exp: Re,
    numero_exp: Re,
    inicio_desc: Re,
    punto_esp: Re,
    publ_split: Re,
    encabezados: Vec<Re>,
}

fn a() -> &'static A {
    static A_: OnceLock<A> = OnceLock::new();
    A_.get_or_init(|| {
        let dias = r"(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)";
        let meses = r"(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|sept?iembre|octubre|noviembre|diciembre)";
        let fecha = format!(r"(?:\d{{1,2}}\s+de\s+{meses}\s+(?:del?\s+)?)?\d{{4}}");
        let enc: Vec<String> = vec![
            r"^(?:NO|MAL)\s+PUBLICAD[OA]S?\b[.:,\s-]*".to_string(),
            r"^publicad[oa]s\b[.:,\s-]+".to_string(),
            format!(r"^(?:{dias},?\s+)?(?:(?:del?|al)\s+)?\d{{1,2}}\s+de\s+{meses}\s+(?:del?\s+)?\d{{4}}[.,:\s-]+"),
            r"^del?\s+\d{4}[.,:\s-]+".to_string(),
            format!(r"^sentencia[as]*\s+(?:def(?:initiva)?[as]*\.?|interlocutori\w*)(?:\s+(?:del?\s+)?{fecha})?[.,:\s-]+"),
            r"^sentencias?\s+(?=\S)".to_string(),
            r"^audiencias?\s+(?=\S)".to_string(),
            r"^pon(?:encia)?\.?\s*\d+(?:\s*,\s*\d+\s+sent(?:encias?)?\.?)?[.,:\s-]+".to_string(),
            r"^providencias\s+precautorias[.,:\s-]+".to_string(),
            r"^no\s+publ\.?\s+(?:del?\s+)?\d{1,2}\s+[a-z]{3,}\.?\s+\d{4}[.,:\s-]+".to_string(),
        ];
        let i = "(?i)";
        A {
            vs: Re::nf(r"\s+vs\.\s+", i),
            neg: Re::nf(r"\b(?:Desech\w*|No\s+Ha\s+Lugar|No\s+(?:Se\s+)?Admit\w*|(?:Se\s+)?Nieg[ao]n?\b|Improceden\w*|Inadmi\w*|No\s+(?:Se\s+)?Da\s+Tr[áa]mite)", i),
            prev: Re::nf(r"\b(?:Prevenci[óo]n|Previene|Prev\.)(?=[\s.,]|$)", i),
            adm: Re::nf(r"\b(?:Admisi[óo]n|Admisorio|Auto\s+Admisorio|Admite\s+(?:la\s+)?Demanda|Se\s+Admite\b|Radicaci[óo]n|Radica\b)(?!\s+(?:de\s+(?:la\s+)?)?(?:Prueb|Pbas|Recurso|Apel|Inc\b|Incidente|Exh))", i),
            adm_exh: Re::nf(r"\bExh(?:orto)?\.?\s+(?:Admisi|Radica)", i),
            cola: Re::nf(r"\s\d+\s+(?:Acdos?|Audiencias?|Aud|Sent)\b|\bN[úu]m\.\s*Exp\.\s*\d+/\d{4}\s*$", i),
            ugj: Re::new(r"N[úu]m\.\s*Exp\.\s*\d+/\d{4},\s*(.+)$"),
            ugj_cola: Re::new(r"\s\d+\s+(?:Acdos?|Audiencias?|Aud|Sent)\b"),
            desc_vacia: Re::nf(r"^(?:acuerdos?|acdos?\.?(?:\s+(?:i|amc|de\s+tr[aá]mite|\w{1,4}))?|autos?|tr[aá]mite|tomo\b.*|principal|expdllo\.?|expedientillo|no\s+publicado|mal\s+publicado|\w{1,2})$", i),
            hashes: Re::new(r"#+"),
            ws: Re::new(r"\s+"),
            desc_inicio: Re::nf(r"^(?:Acdos?\.?\s+)?(?:(?:por|de|del|y)\s+)?", i),
            desc_publ: Re::nf(r"(?:no|mal)\s+publ|no\s+definido", i),
            desc_codigo: Re::new(r"[A-Z]{1,2}\s*-?\s*\d+$"),
            desc_num: Re::new(r"\d+[/-]\d{4}|N[úu]m\.\s*Exp"),
            num_exp: Re::new(r"N[úu]m\.\s*Exp"),
            numero_exp: Re::nf(r"^\s*(\d+\s*[/-]\s*\d{4}(?:\s*[/-]\s*\d{1,3})?)(.*)$", "(?s)"),
            inicio_desc: Re::nf(r"^(?:Aud(?:iencia)?\.?|Acdos?\.?|Acuerdos?|Prevenci[óo]n|Previene|Desech\w*|No\s+Ha\s+Lugar|Admisi[óo]n|Admisorio|Señalamiento|Se\s+Tiene|T[ée]ngase|Se\s+Señala|Se\s+Ordena|Radicaci[óo]n)\b", i),
            punto_esp: Re::new(r"\.\s+"),
            publ_split: Re::nf(r"\s+(?:no|mal)\s+Publ\b", i),
            encabezados: enc.iter().map(|p| Re::nf(p, i)).collect(),
        }
    })
}

/// RE_VS (\s+vs\.\s+, IGNORECASE), que usa también el parser.
pub fn re_vs() -> &'static Re { &a().vs }

/// Lo que el aviso dice después de la demandada y del último "vs.".
fn sin_vs<'t>(raw: &'t str, demandada: Option<&str>) -> &'t str {
    let mut t = raw;
    if let Some(d) = demandada.filter(|d| !d.is_empty()) {
        if let Some(i) = t.rfind(d) { t = &t[i + d.len()..]; }
    }
    match a().vs.finditer(t).last() { Some(m) => &t[m.end..], None => t }
}

/// _tramo: (tramo, es_ugj).
fn tramo<'t>(raw: &'t str, demandada: Option<&str>) -> (&'t str, bool) {
    let a = a();
    let t = sin_vs(raw, demandada);
    if let Some(c) = a.ugj.caps(t) {
        return (a.ugj_cola.split_first(c.get(t, 1).unwrap()), true);
    }
    match a.cola.search(t) { Some(m) => (&t[..m.start], false), None => (t, false) }
}

thread_local! {
    static C_SIN_TIPO: RefCell<Cache<Option<String>>> = RefCell::new(Cache::new(300_000));
    static C_SEPARAR_PARTE: RefCell<Cache<(String, Option<String>)>> = RefCell::new(Cache::new(300_000));
}

/// _sin_tipo (con caché, como en Python).
pub fn sin_tipo(tramo: &str) -> Option<String> { memo(&C_SIN_TIPO, tramo, || sin_tipo_(tramo)) }

fn sin_tipo_(tramo: &str) -> Option<String> {
    let p: Vec<&str> = split_ws(tramo).collect();
    let n = p.len();
    let inicios = std::iter::once(0).chain((1..n).filter(|&i| p[i - 1].ends_with('.')));
    for i in inicios {
        let fin = n.min(i + 8);
        if match_canonical_prefix(strip_c(&expand_abbr(&p[i..fin].join(" ")), " .,-")).is_none() { continue; }
        for k in (i + 1..=fin).rev() {
            let e1 = expand_abbr(&p[i..k].join(" "));
            let e = strip_c(&e1, " .,-");
            if let Some(c) = match_canonical_prefix(e) {
                if clen(c) + 1 >= clen(e) {
                    let r = p[k..].join(" ");
                    return if r.is_empty() { None } else { Some(r) };
                }
            }
        }
    }
    let inicio = (1..n).find(|&i| es_stop_word(&rstrip_c(p[i], ".,").to_lowercase()));
    inicio.map(|i| p[i..].join(" "))
}

/// descripcion.
pub fn descripcion(raw: &str, demandada: Option<&str>) -> Option<String> {
    let a = a();
    let (tr, ugj) = tramo(raw, demandada);
    let t1 = a.hashes.sub(tr, " ", 0);
    let t2 = a.ws.sub(&t1, " ", 0);
    let mut t: String = strip_c(&t2, " .,;-").to_string();
    if !ugj {
        t = sin_tipo(&t)?;
        if t.is_empty() { return None; }
    }
    let t = strip_c(&t, " .,;-");
    let t = a.desc_inicio.sub(t, "", 0);
    let t = cap_first(&t);
    if clen(&t) < 3 || a.desc_vacia.is_match_start(&t) { return None; }
    if a.desc_publ.is_match_start(&t) { return None; }
    if a.desc_codigo.is_match_start(&t) { return None; }
    if a.desc_num.is_search(&t) { return None; }
    if !t.contains(' ') && clen(t.trim_end_matches('.')) <= 4 { return None; }
    Some(cprefix(&t, 160).to_string())
}

/// tipo_acuerdo.
pub fn tipo_acuerdo(raw: &str, demandada: Option<&str>) -> Option<&'static str> {
    let a = a();
    let t = sin_vs(raw, demandada);
    // A diferencia de _tramo, en las UGJ toma todo lo que sigue a la coma.
    let tramo = match a.ugj.caps(t) {
        Some(c) => c.get(t, 1).unwrap(),
        None => match a.cola.search(t) { Some(m) => &t[..m.start], None => t },
    };
    if a.neg.is_search(tramo) { return Some("negacion"); }
    if a.prev.is_search(tramo) { return Some("prevencion"); }
    if a.adm.is_search(tramo) && !a.adm_exh.is_search(tramo) { return Some("admision"); }
    None
}

/// separar_expediente: (número, cuaderno). Vacío o sin número N/AAAA: tal cual.
pub fn separar_expediente(exp: &str) -> (String, Option<String>) {
    let a = a();
    let c = match a.numero_exp.match_caps(exp) { Some(c) if !exp.is_empty() => c, _ => return (exp.to_string(), None) };
    let numero = a.ws.sub(c.get(exp, 1).unwrap(), "", 0).replace('-', "/");
    let resto = c.get(exp, 2).unwrap();
    if lstrip(resto).starts_with(',') { return (numero, None); }
    let r1 = a.hashes.sub(resto, " ", 0);
    let r2 = a.ws.sub(&r1, " ", 0);
    let r = cprefix(strip_c(&r2, " .,;-"), 120);
    (numero, if r.is_empty() { None } else { Some(r.to_string()) })
}

/// separar_parte: (nombre, sobrante). Con caché, como en Python.
pub fn separar_parte(nombre: &str) -> (String, Option<String>) {
    if nombre.is_empty() || !nombre.contains(". ") { return (nombre.to_string(), None); }
    memo(&C_SEPARAR_PARTE, nombre, || separar_parte_(nombre))
}

fn separar_parte_(nombre: &str) -> (String, Option<String>) {
    let a = a();
    for m in a.punto_esp.finditer(nombre) {
        let resto = &nombre[m.end..];
        if resto.is_empty() { break; }
        let corta = a.inicio_desc.is_match_start(resto) || match_canonical_prefix(&expand_abbr(resto)).is_some();
        if corta {
            let mut antes = strip(&nombre[..m.start]).to_string();
            if split_ws(&antes).next_back().map_or(false, |w| w.contains('.')) { antes.push('.'); }
            return (antes, Some(strip(resto).to_string()));
        }
    }
    (nombre.to_string(), None)
}

/// descripcion_de_sobrante.
pub fn descripcion_de_sobrante(sobrante: Option<&str>) -> Option<String> {
    let a = a();
    let s = sobrante.filter(|s| !s.is_empty())?;
    let sin;
    let t: &str = if match_canonical_prefix(&expand_abbr(s)).is_some() {
        sin = sin_tipo(s);
        sin.as_deref().unwrap_or("")
    } else { s };
    let t = a.publ_split.split_first(t);
    let t2 = a.ws.sub(t, " ", 0);
    let t = strip_c(&t2, " .,;-");
    if clen(t) < 3 || a.desc_vacia.is_match_start(t) || a.num_exp.is_search(t) { return None; }
    Some(cprefix(t, 160).to_string())
}

/// quitar_marca_publicacion.
pub fn quitar_marca_publicacion(nombre: &str) -> String {
    if nombre.is_empty() { return String::new(); }
    let mut limpio = nombre.to_string();
    loop {
        let mut cambio = false;
        for r in &a().encabezados {
            if let Some(m) = r.match_(&limpio) {
                if m.end > 0 { limpio.drain(..m.end); cambio = true; }
            }
        }
        if !cambio { break; }
    }
    let s = strip(&limpio);
    if s.is_empty() { nombre.to_string() } else { s.to_string() }
}
