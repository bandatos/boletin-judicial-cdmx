// Port de scraper/normalize_tipo.py: expand_abbr, match_canonical_prefix,
// normalize y sus tablas (en el orden de Python).
use crate::cache::{memo, Cache};
use crate::py::{clen, py_alpha, rstrip_c, strip, is_space};
use crate::re::Re;
use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::rc::Rc;
use std::sync::OnceLock;

pub const ABBR: &[(&str, &str)] = &[
    ("ord", "Ordinario"), ("merc", "Mercantil"), ("civ", "Civil"), ("fam", "Familiar"),
    ("esp", "Especial"), ("hip", "Hipotecario"), ("ejec", "Ejecutivo"), ("cump", "Cumplimiento"),
    ("resc", "Rescisión"), ("proced", "Procedimiento"), ("prescrip", "Prescripción"),
    ("aud", "Audiencia"), ("div", "Divorcio"), ("nec", "Necesario"), ("alim", "Alimentos"),
    ("comparec", "Comparecencia"), ("juris", "Jurisdicción"), ("domi", "Dominio"),
    ("expdllo", "Expedientillo"), ("acdo", "Acuerdo"), ("acdos", "Acuerdos"),
    ("reiv", "Reivindicatorio"), ("nul", "Nulidad"), ("ejecc", "Ejecución"),
    ("potest", "Potestad"), ("pérd", "Pérdida"), ("perd", "Pérdida"), ("vol", "Voluntaria"),
    ("inmat", "Inmatriculación"),
    ("controv", "Controversias de"), ("arrend", "Arrendamiento"), ("inmob", "Inmobiliario"),
    ("exh", "Exhorto"), ("juic", "Juicio"), ("reconoc", "Reconocimiento"),
    ("patern", "Paternidad"), ("matern", "Maternidad"), ("concil", "Conciliación"),
    ("repos", "Reposición"), ("cancel", "Cancelación"), ("titulos", "Títulos"),
    ("prelim", "Preliminar"), ("prec", "Precautorias"), ("pos", "Posesión"),
    ("susp", "Suspensión"), ("rect", "Rectificación"), ("quieb", "Quiebra"),
    ("presc", "Prescripción"), ("prep", "Preparatorios"), ("interl", "Interlocutoria"),
    ("sent", "Sentencia"), ("def", "Definitiva"), ("gral", "General"),
    ("concurs", "Concurso"), ("convivencias", "Convivencias"), ("reg", "Registro"),
    ("convenc", "Convencional"), ("pref", "Preferente"), ("o", "o"),
    ("int", "Intestamentario"), ("test", "Testamentario"), ("testament", "Testamentario"),
    ("interd", "Interdicción"),
];

pub const CANONICAL_TIPOS: &[&str] = &[
    "Ordinario (Individual) Laboral Individual",
    "Otros (Paraprocesales o Voluntarios) Laboral Individual",
    "Especial de Pérdida de la Patria Potestad Familiar",
    "Especial de Fianzas Mercantil",
    "Especial de Matriculación",
    "Especial (Colectivo) Laboral",
    "Especial Hipotecario Civil",
    "Especial Hipotecario",
    "Especial Mercantil",
    "Especial Familiar",
    "Ejecutivo Cuantía Menor Mercantil",
    "Ejecutivo Paz Mercantil",
    "Ejecutivo Mercantil",
    "Ejecutivo Civil",
    "Controversias del Orden Familiar",
    "Controversias de Arrendamiento Civil",
    "Jurisdicción Voluntaria Civil",
    "Jurisdicción Voluntaria Familiar",
    "Jurisdicción Voluntaria Mercantil",
    "Jurisdicción Voluntaria",
    "Providencias Precautorias Civil",
    "Providencias Precautorias Mercantil",
    "Providencias Precautorias",
    "Medios Preparatorios Civil",
    "Medios Preparatorios Mercantil",
    "Medios Preparatorios",
    "Actos Prejudiciales Civil",
    "Actos Prejudiciales Familiar",
    "Vía Ejecutiva Cuantía Menor Civil",
    "Vía de Apremio Oralidad Civil",
    "Vía de Apremio Civil",
    "Vía de Apremio Familiar",
    "Vía de Apremio",
    "Vía Correo Electrónico",
    "Vía Electrónica",
    "Sucesorio Familiar",
    "Intestamentario",
    "Testamentario",
    "Interdicción",
    "Divorcio Necesario",
    "Divorcio Incausado",
    "Divorcio",
    "Alimentos",
    "Extinción de Dominio Civil",
    "Arbitraje Comercial Mercantil",
    "Arbitral Civil",
    "Convencional o Preferente Mercantil",
    "Ejecución de Garantías Otorgadas Mediante Prenda",
    "Procedimiento Convencional Mercantil Civil",
    "Procedimiento de Huelga Laboral",
    "Procedimiento de Ejecución Laboral Individual",
    "Cumplimiento de Contrato Oralidad Mercantil",
    "Cumplimiento de Contrato Oralidad Civil",
    "Cumplimiento de Ejecutoria",
    "Cumplimiento Voluntario",
    "Rescisión de Contrato Oralidad Mercantil",
    "Rescisión de Contrato Oralidad Civil",
    "Nulidad de Contrato Oralidad Mercantil",
    "Pago de Seguro Oralidad Mercantil",
    "Prescripción Positiva Oralidad Civil",
    "Reivindicatorio Oralidad Civil",
    "Proforma Oralidad Civil",
    "Otros Oralidad Mercantil",
    "Otros Oralidad Civil",
    "Oral Oralidad Mercantil",
    "Audiencia Preliminar",
    "Audiencia Incidental",
    "Audiencia Previa y de Conciliación",
    "Audiencia de Conciliación",
    "Audiencia de Ley",
    "Audiencia Digitalizada",
    "Exhortos Civil",
    "Exhorto Familiar",
    "Exhorto Mercantil",
    "Exhorto Civil",
    "Tercerías Civil",
    "Remate",
    "Controversias de Arrendamiento Inmobiliario",
    "Juicio Concluido Civil",
    "Reconocimiento de Paternidad",
    "Especial de Cancelación y Reposición de Títulos de Crédito Mercantil",
    "Diligencias de Conciliación Civil",
    "Ordinario Civil Familiar",
    "Ordinario Civil",
    "Ordinario Mercantil",
    "Ordinario Familiar",
    "Oral Mercantil",
    "Oral Familiar",
    "Oral Paz Civil",
    "Oral Civil",
    "Inmatriculación Judicial Civil",
    "Pago de Seguro Oralidad Civil",
    "Guarda y Custodia",
    "Quiebra Mercantil",
    "Protección Efectiva de Derechos Tutela Efectiva de Derechos Humanos",
    "Venta de Prenda Mercantil",
    "Pago de Daños Culposos Causados por Motivos del Transito de Vehículo Civil",
    "Pago de Daños Culposos Causados por Motivos del Transito de Vehículo Cuantía Menor Civil",
    "Pago de Daños Culposos Causados por Motivos del Transito de Vehículo Paz Civil",
    "Ejecución de Sentencias Dictadas en el Extranjero Mercantil",
    "Ejecución de Sentencias Dictadas en el Extranjero Paz Mercantil",
    "Suspensión de Pagos Mercantil",
    "Concurso Voluntario y Necesario Civil",
    "Plenaria de Posesión Oralidad Civil",
    "Rectificación de Acta",
    "Reconocimiento de Maternidad",
    "Registro de Convivencias",
    "Tercerías Mercantil",
    "Especial Cuantía Menor Civil",
    "Especial de Fianzas Paz Mercantil",
    "Convencional o Preferente Cuantía Menor Mercantil",
    "Arbitraje Comercial Cuantía Menor Mercantil",
    "Especial de Cancelación y Reposición de Títulos de Crédito Cuantía Menor Mercantil",
    "Especial de Pérdida de la Patria Potestad",
    "Especial de Levantamiento de Acta Familiar",
    "No Definido",
];

pub const STOP_WORDS: &[&str] = &[
    "acuerdo", "acuerdos", "acdo", "acdos", "auto", "tramite", "trámite",
    "expedientillo", "expdllo", "prevencion", "prevención", "admision",
    "admisión", "admisorio", "admite", "comparecencia", "vista", "turno",
    "turnese", "dada", "cuenta", "cedula", "cédula", "amparo", "tomo",
    "segundo", "tercer", "cuarto", "quinto", "sexto", "septimo", "séptimo",
    "octavo", "noveno", "requerimiento", "informe", "manifestaciones",
    "minuta", "billete", "desechamiento", "desecha", "desechado", "no",
    "ha", "lugar", "puesta", "disposicion", "disposición", "sicor", "razon",
    "razón", "actuarial", "apelacion", "apelación", "autorizacion",
    "autorización", "autorizaciones", "copias", "certificadas",
    "destruccion", "destrucción", "sin", "diligenciar", "diligenciada",
    "oficio", "sala", "justificado", "exhorto", "replica", "réplica",
    "ejecutoria", "indirecto", "inf", "just", "agregar", "nueva", "llegada",
    "autos", "entrega", "proveniente", "extinto", "deberá", "debera",
    "estarse", "contestacion", "contestación", "promocion", "promoción",
    "se", "agrega", "al", "actuario", "principal", "archivo", "judicial",
    "computo", "cómputo", "no publicado", "publicado",
];

const WHITELIST_LAST: &[&str] = &[
    "civil", "mercantil", "familiar", "laboral", "individual", "colectivo",
    "voluntaria", "voluntarios", "arbitral", "alimentos", "necesario",
    "incausado", "dominio", "ejecutivo", "ordinario", "especial",
    "hipotecario", "fianzas", "garantia", "garantías", "garantias",
    "prenda", "huelga", "convencional", "preliminar", "positiva",
    "precautorias", "providencias", "preparatorios", "medios", "menor",
    "cuantia", "cuantía", "contrato", "ejecutoria", "oralidad", "otros",
    "apremio", "via", "vía", "prejudiciales", "actos", "extincion",
    "extinción", "sucesorio", "jurisdiccion", "jurisdicción", "del",
    "orden", "controversias", "arrendamiento", "de", "la", "el", "los",
    "las", "en", "y", "a", "oral", "ley", "transmision", "transmisión",
    "pos", "posesion", "posesión", "mediante", "otorgadas", "otorgada",
];

const CONNECTOR_WORDS: &[&str] = &["y", "de", "del", "la", "el", "los", "las", "en", "a", "o"];

struct Datos {
    abbr: HashMap<&'static str, &'static str>,
    /// primera letra en minúsculas -> [(tipo en minúsculas, tipo)], más largos primero
    canon: HashMap<char, Vec<(String, &'static str)>>,
    /// largo en bytes del tipo más largo en minúsculas
    max_canon: usize,
    stop: HashSet<&'static str>,
    whitelist: HashSet<&'static str>,
    connector: HashSet<&'static str>,
    fixes: Vec<(Re, &'static str)>,
}

fn datos() -> &'static Datos {
    static D: OnceLock<Datos> = OnceLock::new();
    D.get_or_init(|| {
        let mut tipos: Vec<&'static str> = CANONICAL_TIPOS.to_vec();
        tipos.sort_by(|a, b| clen(b).cmp(&clen(a))); // estable, como sort(key=len, reverse=True)
        let mut canon: HashMap<char, Vec<(String, &'static str)>> = HashMap::new();
        let mut max_canon = 0;
        for c in tipos {
            let l = c.to_lowercase();
            max_canon = max_canon.max(l.len());
            canon.entry(l.chars().next().unwrap()).or_default().push((l, c));
        }
        Datos {
            abbr: ABBR.iter().copied().collect(),
            canon,
            max_canon,
            stop: STOP_WORDS.iter().copied().collect(),
            whitelist: WHITELIST_LAST.iter().copied().collect(),
            connector: CONNECTOR_WORDS.iter().copied().collect(),
            // SPELLING_FIXES salvo el último (palabras pegadas), que va a mano.
            fixes: vec![
                (Re::new(r"(?i)\bGatantía\b"), "Garantía"),
                (Re::new(r"(?i)\bGarantía Otorgada\b"), "Garantías Otorgadas"),
                (Re::new(r"(?i)\bNo Definido No Definido\b"), "No Definido"),
                (Re::new(r"(?i)\bControversia de\b"), "Controversias de"),
                (Re::new(r"(?i)\bTutela de Derechos(?! Humanos)\b"), "Tutela Efectiva de Derechos Humanos"),
                (Re::new(r"(?i)\bTutela de Derechos Humanos\b"), "Tutela Efectiva de Derechos Humanos"),
            ],
        }
    })
}

pub fn es_stop_word(w: &str) -> bool { datos().stop.contains(w) }

/// match_canonical_prefix. Solo hace falta pasar a minúsculas lo que puede
/// cubrir el tipo más largo y un carácter más.
pub fn match_canonical_prefix(text: &str) -> Option<&'static str> {
    let d = datos();
    let mut t = String::with_capacity(d.max_canon + 8);
    for c in text.chars() {
        t.extend(c.to_lowercase());
        if t.len() > d.max_canon { break; }
    }
    for (cl, canon) in d.canon.get(&t.chars().next()?)? {
        if t.starts_with(cl.as_str()) {
            match t[cl.len()..].chars().next() {
                Some(c) if py_alpha(c) => {}
                _ => return Some(canon),
            }
        }
    }
    None
}

pub fn min_(c: char) -> bool { c.is_ascii_lowercase() || "áéíóúñ".contains(c) }
pub fn may_(c: char) -> bool { c.is_ascii_uppercase() || "ÁÉÍÓÚÑ".contains(c) }

thread_local! {
    static C_EXPAND: RefCell<Cache<Rc<str>>> = RefCell::new(Cache::new(400_000));
    static C_NORMALIZE: RefCell<Cache<Option<&'static str>>> = RefCell::new(Cache::new(400_000));
}

/// expand_abbr (con caché, como en Python).
pub fn expand_abbr(text: &str) -> Rc<str> { memo(&C_EXPAND, text, || expand_abbr_(text).into()) }

fn expand_abbr_(text: &str) -> String {
    let d = datos();
    // re.split(r'(\s+)', text): se alternan palabras y espacios.
    let mut out = String::with_capacity(text.len() + 16);
    let mut ini = 0;
    let mut en_espacio: Option<bool> = None;
    let tok = |a: usize, b: usize, esp: bool, out: &mut String| {
        let w = &text[a..b];
        if esp { out.push_str(w); return; }
        let key = rstrip_c(w, ".").to_lowercase();
        out.push_str(d.abbr.get(key.as_str()).copied().unwrap_or(w));
    };
    for (i, c) in text.char_indices() {
        let e = is_space(c);
        match en_espacio {
            Some(prev) if prev != e => { tok(ini, i, prev, &mut out); ini = i; }
            _ => {}
        }
        en_espacio = Some(e);
    }
    if let Some(prev) = en_espacio { tok(ini, text.len(), prev, &mut out); }
    let mut t = out;
    for (r, rep) in &d.fixes {
        if let std::borrow::Cow::Owned(s) = r.sub(&t, rep, 0) { t = s; }
    }
    // (?<=[a-záéíóúñ])(?=[A-ZÁÉÍÓÚÑ]) -> " "
    let mut prev: Option<char> = None;
    if !t.chars().any(|c| { let r = prev.map_or(false, min_) && may_(c); prev = Some(c); r }) { return t; }
    let mut s = String::with_capacity(t.len() + 4);
    let mut prev: Option<char> = None;
    for c in t.chars() {
        if prev.map_or(false, min_) && may_(c) { s.push(' '); }
        s.push(c);
        prev = Some(c);
    }
    s
}

/// ^[a-záéíóúñ]{lo,hi}(\.)?$ con el $ de Python (también antes de un \n final).
fn todo_minus(s: &str, lo: usize, hi: usize, punto: bool) -> bool {
    let f = |s: &str| {
        let s2 = if punto { s.strip_suffix('.').unwrap_or(s) } else { s };
        let n = clen(s2);
        n >= lo && n <= hi && s2.chars().all(min_)
    };
    f(s) || s.strip_suffix('\n').map_or(false, f)
}

/// normalize (con caché, como normalize_tipo de build_db.py).
pub fn normalize(tipo_juicio: Option<&str>) -> Option<&'static str> {
    let tj = tipo_juicio.filter(|s| !s.is_empty())?;
    memo(&C_NORMALIZE, tj, || normalize_(tj))
}

fn normalize_(tj: &str) -> Option<&'static str> {
    let t = strip(rstrip_c(strip(tj), "."));
    if t.is_empty() { return None; }
    let t = expand_abbr(t);
    if let Some(c) = match_canonical_prefix(&t) { return Some(c); }
    let d = datos();
    let words: Vec<&str> = t.split(' ').collect();
    let mut out: Vec<&str> = Vec::new();
    for (i, w) in words.iter().enumerate() {
        let wl = rstrip_c(w, ".").to_lowercase();
        let is_last = i == words.len() - 1;
        if d.stop.contains(wl.as_str()) { break; }
        if is_last && todo_minus(&wl, 1, 1, true) { break; }
        if is_last && todo_minus(&wl, 2, 20, false) && !d.whitelist.contains(wl.as_str()) { break; }
        if let Some(o) = out.last() {
            if o.to_lowercase() == w.to_lowercase() { continue; }
        }
        out.push(w);
    }
    while let Some(o) = out.last() {
        if d.connector.contains(rstrip_c(o, ".").to_lowercase().as_str()) { out.pop(); } else { break; }
    }
    let joined = out.join(" ");
    let result = strip(rstrip_c(strip(&joined), "."));
    if result.is_empty() { return None; }
    match_canonical_prefix(result)
}

/// _match_canonical_tipo de poc.py.
pub fn match_canonical_tipo(text: &str) -> Option<&'static str> { match_canonical_prefix(&expand_abbr(text)) }

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn tipos() {
        assert_eq!(normalize(Some("Ord. Civil")), Some("Ordinario Civil"));
        assert_eq!(normalize(Some("Controversia de Arrendamiento Civil Tesc")), Some("Controversias de Arrendamiento Civil"));
        assert_eq!(match_canonical_prefix("Oral Civilización"), None);
        assert_eq!(&*expand_abbr("Esp. Hip.Civ"), "Especial Hip.Civ");
        assert_eq!(&*expand_abbr("Tutela de Derechos x"), "Tutela Efectiva de Derechos Humanos x");
    }
}
