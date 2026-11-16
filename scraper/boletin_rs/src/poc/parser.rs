// parse_pdf de poc.py y todo lo que llama.
use crate::acuerdos::{descripcion, descripcion_de_sobrante, quitar_marca_publicacion, re_vs,
    separar_expediente, separar_parte, tipo_acuerdo};
use crate::py::{clen, cprefix, lstrip_c, py_alpha, py_int, rstrip, rstrip_c, split_ws, splitlines, strip, strip_c};
use crate::re::Re;
use crate::tipos::{match_canonical_tipo, CANONICAL_TIPOS};
use std::sync::OnceLock;

const SECTION_HEADERS: &[&str] = &[
    "TRIBUNAL DE DISCIPLINA JUDICIAL", "AVISOS", "SALAS", "JUZGADOS DE LO CIVIL", "JUZGADOS DE LO FAMILIAR",
    "JUZGADOS CIVILES DE PROCESO ORAL", "JUZGADOS DE PROCESO ORAL EN MATERIA FAMILIAR",
    "UNIDADES DE GESTIÓN JUDICIAL", "JUZGADOS DE TUTELA DE DERECHOS HUMANOS", "TRIBUNALES EN MATERIA LABORAL",
    "SUSCRIPCIÓN AL BOLETÍN JUDICIAL", "EDICTOS",
];
const SKIP_LINES: &[&str] = &[
    "SOLO CONSULTA", "SIN ACUERDOS", "BOLETÍN JUDICIAL", "DEL PODER JUDICIAL DE LA CIUDAD DE MÉXICO",
    "NO PUBLICADOS", "AUDIENCIA", "MAL PUBLICADOS", "SENTENCIAS", "Salas",
];
const SECCIONES_SIN_ORGANO: &[&str] = &["EDICTOS", "AVISOS", "SUSCRIPCIÓN AL BOLETÍN JUDICIAL"];

const TIPO_START_WORDS: &[&str] = &[
    "Ord", "Especial", "Esp", "Ejecutivo", "Ejec", "Ejecc", "Oral", "Juris",
    "Proced", "Aud", "Div", "Cont", "Vía", "Via", "Providencias", "Medios",
    "Actos", "Sucesorio", "Controversias", "Controversia", "Otros", "Juic", "Tercerías",
    "Tercerias", "Extinción", "Extincion", "Cump", "Resc", "Reivindicatorio",
    "Reivindicatoria", "Interdicto", "Nulidad", "Usucapión", "Usucapion",
    "Divorcio", "Rectificación", "Rectificacion", "Consignación",
    "Consignacion", "Otorgamiento", "Cancelación", "Cancelacion",
    "Ordinario", "Juicio", "Remate", "Arbitraje", "Arbitral", "Exhortos",
    "Pago", "Inmat", "Quieb", "Convencional", "Proforma", "Rescisión",
    "Rescision", "Ejecución", "Ejecucion", "Reconocimiento", "Reconoc",
    "Diligencias", "Prescripción", "Prescripcion", "Prescrip", "Nul", "Reiv",
    "Alim", "Guarda", "Rect", "Terc", "Susp", "Pérd", "Perd", "Venta",
    "Plenaria", "Protección", "Proteccion", "Suspensión", "Suspension",
    "Concurso", "Reg", "Tutela",
    "Int", "Interd", "Test", "Testament", "Exh",
];

struct P {
    skip_prefix: Re,
    sufijo_letra_suelta: Re,
    continua_razon_social: Re,
    sufijo_fusionado: Re,
    entry_end_bare: Re,
    juzgado: Re,
    sala: Re,
    organo_inicio: Re,
    titular: Re,
    secretaria: Re,
    acuerdos: Re,
    entry_end: Re,
    toca_end: Re,
    toca_guiones: Re,
    marca_actuacion: Re,
    marca_invertida: Re,
    ponencia: Re,
    tipo_acdos: Re,
    tipo_start: Re,
    tipo_tail: Re,
    looks_new: Re,
    minus: Re,
    num: Re,
    vs_inicio: Re,
    cadena_toca: Re,
    tipo_cadena: Re,
    arrastre_actora: Re,
}

fn p() -> &'static P {
    static P_: OnceLock<P> = OnceLock::new();
    P_.get_or_init(|| {
        let ordinal = r"(?:PRIMER[OA]?|SEGUND[OA]|TERCER[OA]?|CUART[OA]|QUINT[OA]|SEXT[OA]|SÉPTIM[OA]|OCTAV[OA]|NOVEN[OA]|DÉCIM[OA]|VIGÉSIM[OA]|TRIGÉSIM[OA]|CUADRAGÉSIM[OA]|QUINCUAGÉSIM[OA]|SEXAGÉSIM[OA]|SEPTUAGÉSIM[OA])";
        let poison = r"SOLO CONSULTA|BOLET[IÍ]N JUDICIAL|ACUERDOS DEL|SALAS?\b|SECRETARI[OA] DE ACUERDOS|MAL PUBLICADOS|NO PUBLICADOS|AUDIENCIA\b";
        let toca_end = format!(concat!(
            r"T\.",
            r"(?i:\s*(?:[A-ZÁÉÍÓÚÑ]{{1,4}}\.?\s*)?)",
            r"(\d+/\d+(?:/\d+)?|\d{{1,5}}-\d{{4}}(?:-?\d{{1,3}})?)",
            r"(?:(?!(?i:{poison})).){{0,200}}?",
            r"(?i:(?:(\d+)\s+(?:Acdos?|Audiencias?|Sent\.?)\.",
            r"|Sent\.(?:\s*[a-záéíóúñ]+\.){{0,4}}\s*Pon\.?\s*\d{{1,2}}(?:\.|(?=\s+en\s)))",
            r"(?:,?\s*(?:en\s+)?(?:Cuad|Expdllo)\.?(?:\s+(?:de\s+)?[A-Z][a-z]*\.?){{0,3}}(?:\s*[IVX\d]+)?\.?)?",
            r"(?:\s*(?:no|mal)\s+Publ\.?(?:\s*del\s+\d{{1,2}}(?:\s+de)?\s+[a-z]{{3,}}\.?",
            r"(?:\s+(?:del?\s+)?\d{{4}})?\.?)?)?)"), poison = poison);
        let mut words: Vec<String> = TIPO_START_WORDS.iter().map(|s| s.to_string()).collect();
        for c in CANONICAL_TIPOS {
            let w = split_ws(c).next().unwrap();
            if w.chars().all(py_alpha) { words.push(w.to_string()); }
        }
        words.sort();
        words.dedup();
        P {
            skip_prefix: Re::nf(concat!(r"^BOLETÍN JUDICIAL\b",
                r"|^(?:SENTENCIAS? (?:DEFINITIVAS?|INTERLOCUTORIAS?)|AUDIENCIAS?|ACUERDOS? (?:NO|MAL) PUBLICADOS?)",
                r"\b[^a-záéíóúñ]*$",
                r"|^Sentencias? (?:Definitivas?|Interlocutorias?)$"), ""),
            sufijo_letra_suelta: Re::nf(r"\s[A-Z]$", ""),
            continua_razon_social: Re::nf(r"^(?:A\.|de\s+R\.|C\.\s*V\.|R\.\s*L\.)", ""),
            sufijo_fusionado: Re::nf(r"\s+vs\.\s+|\s\d+\s+(?:Acdos?|Audiencias?|Aud|Sent)\s*$", "(?i)"),
            entry_end_bare: Re::nf(r"Núm\.\s+Exp\.\s+\d+/\d+\s*$", "(?i)"),
            juzgado: Re::nf(concat!(r"^((?:PRIMERO?|SEGUNDO|TERCERO|CUARTO|QUINTO|SEXTO|SÉPTIMO|OCTAVO|NOVENO|",
                r"DÉCIMO(?:\s+(?:PRIMERO?|SEGUNDO|TERCERO|CUARTO|QUINTO|SEXTO|SÉPTIMO|OCTAVO|NOVENO))?|",
                r"VIGÉSIMO(?:\s+(?:PRIMERO?|SEGUNDO|TERCERO))?|TRIGÉSIMO|CUADRAGÉSIMO|QUINCUAGÉSIMO)",
                r"\s+DE\s+LO\s+\w+(?:\s+\w+)*)$"), ""),
            sala: Re::nf(concat!(r"^((?:PRIMERA|SEGUNDA|TERCERA|CUARTA|QUINTA|SEXTA|SÉPTIMA|OCTAVA|NOVENA|",
                r"DÉCIMA(?:\s+(?:PRIMERA|SEGUNDA|TERCERA|CUARTA|QUINTA|SEXTA|SÉPTIMA|OCTAVA|NOVENA))?)",
                r"\s+SALA\s+(?:DE\s+LO\s+)?(?:CIVIL|FAMILIAR|PENAL)(?:\s+[A-ZÁÉÍÓÚÑ]+)*)$"), ""),
            organo_inicio: Re::nf(&format!(concat!(r"^(?:JUZGADO\s+)?{o}(?:\s+{o})?\s+",
                r"(?:DE\s+PROCESO\s+ORAL|TRIBUNAL\s+LABORAL|DE\s+LO\s+)",
                r"|^UNIDAD\s+DE\s+GESTIÓN\s+JUDICIAL\b"), o = ordinal), ""),
            titular: Re::nf(r"^(?:JUEZA?|SUBDIRECTORA?)\s+[A-ZÁÉÍÓÚÑ .]+$", ""),
            secretaria: Re::nf(r"^SECRETAR[IÍ]A\b", ""),
            acuerdos: Re::nf(r"^ACUERDOS DEL\s+(.+)$", ""),
            entry_end: Re::nf(concat!(r"Núm\.\s+Exp\.\s+(\d+/\d+.*?)(?<!\bvs)(?<!\bBis)(?<!\bPubl)\.",
                r"(?:\s*(\d+)\s+(?:Acdos?|Audiencias?)\.)?"), "(?i)"),
            toca_end: Re::nf(&toca_end, "(?s)"),
            toca_guiones: Re::nf(r"^(\d+)-(\d{4})(?:-?(\d{1,3}))?$", ""),
            marca_actuacion: Re::nf(concat!(r"\b\d+\s+(Acdos?|Audiencias?|Aud|Sent(?:encias?)?)\b\.?",
                r"(?:\s*(Def|Definitivas?|Interl|Inter|Interlocutorias?)\b)?"), "(?i)"),
            marca_invertida: Re::nf(concat!(r"\b(Acdos?|Audiencias?|Aud|Sent(?:encias?)?)\b\.?",
                r"(?:\s*(Def|Definitivas?|Interl|Inter|Interlocutorias?)\b\.?)?\s*\d+\s+Núm\."), "(?i)"),
            ponencia: Re::nf(r"\bSent\.(?:\s*[a-záéíóúñ]+\.){0,4}\s*Pon\.?\s*(\d{1,2})\b", "(?i)"),
            tipo_acdos: Re::nf(r"\.\s+(.+?)\s+(\d+)\s+(?:Acdos?|Audiencias?)\.\s*$", "(?is)"),
            tipo_start: Re::nf(&format!(r"\b(?:{})\.?(?:\s|$)", words.join("|")), "(?i)"),
            tipo_tail: Re::nf(concat!(r"\s+(\d+)\s+(?:Acdos?|Audiencias?|Aud|Sent|Sentencias?)\.?",
                r"(?:\s+(?:Def|Definitivas?|Interl|Inter|Interlocutorias?)\.?)?",
                r"(?:\s*en\s+.+)?\s*$"), "(?is)"),
            looks_new: Re::nf(r"^[A-ZÁÉÍÓÚÑ]", ""),
            minus: Re::nf(r"[a-záéíóúñ]", ""),
            num: Re::nf(r"\d+/\d+", ""),
            vs_inicio: Re::nf(r"vs\.\s", "(?i)"),
            cadena_toca: Re::nf(r"^([,.]?)\s*(.{0,120}?)\s*T\.(?i:\s*(?:[A-ZÁÉÍÓÚÑ]{1,4}\.?\s*)?)(?:\d+/\d+|\d{1,5}-\d{4})", "(?s)"),
            tipo_cadena: Re::nf(r"^(?:Cnpcyf|Suc|Controv|Prov|Interd|Inc|Med|Proc|Rec)\b", ""),
            arrastre_actora: Re::nf(concat!(r"^(?:\((?:Expedientillo|Amparo|Cuadernillo)\)\.?\s*",
                r"|(?:no|mal)\s+Publ\.?\s*",
                r"|del\s+\d{4}-\s*\d{1,2}-\s*\d{1,2}\.\s*",
                r"|\d{4}\s+(?=[A-ZÁÉÍÓÚÑ]))+"), ""),
        }
    })
}

#[derive(Clone, Debug, Default)]
pub struct Ctx {
    pub seccion: Option<String>,
    pub juzgado: Option<String>,
    pub sala: Option<String>,
    pub secretaria: Option<String>,
    pub fecha_acuerdo: Option<String>,
    pub subseccion: Option<String>,
    pub pagina: usize,
}

#[derive(Clone, Debug)]
pub struct Row {
    pub boletin_id: i64,
    pub fecha: String,
    pub ctx: Ctx,
    pub actora: Option<String>,
    pub demandada: Option<String>,
    pub tipo_juicio: Option<String>,
    pub num_acdos: Option<i64>,
    pub expediente: String,
    pub cuaderno: Option<String>,
    pub tipo_actuacion: String,
    pub ponencia: Option<i64>,
    pub tipo_acuerdo: Option<&'static str>,
    pub descripcion: Option<String>,
    pub raw_text: String,
}

pub const COLUMNAS: &[&str] = &["boletin_id", "fecha", "seccion", "juzgado", "sala", "secretaria", "fecha_acuerdo",
    "subseccion", "pagina", "actora", "demandada", "tipo_juicio", "num_acdos", "expediente", "cuaderno",
    "tipo_actuacion", "ponencia", "tipo_acuerdo", "descripcion", "raw_text"];

impl Row {
    /// Valores en el orden de COLUMNAS (None = celda vacía).
    pub fn valores(&self) -> Vec<Option<String>> {
        let c = &self.ctx;
        vec![Some(self.boletin_id.to_string()), Some(self.fecha.clone()), c.seccion.clone(), c.juzgado.clone(),
            c.sala.clone(), c.secretaria.clone(), c.fecha_acuerdo.clone(), c.subseccion.clone(), Some(c.pagina.to_string()),
            self.actora.clone(), self.demandada.clone(), self.tipo_juicio.clone(), self.num_acdos.map(|n| n.to_string()),
            Some(self.expediente.clone()), self.cuaderno.clone(), Some(self.tipo_actuacion.clone()),
            self.ponencia.map(|n| n.to_string()), self.tipo_acuerdo.map(|s| s.to_string()), self.descripcion.clone(),
            Some(self.raw_text.clone())]
    }
}

fn en(lista: &[&str], s: &str) -> bool { lista.contains(&s) }
fn no_vacio(s: &Option<String>) -> bool { s.as_ref().map_or(false, |s| !s.is_empty()) }

fn normalizar_toca(numero: &str) -> String {
    match p().toca_guiones.match_caps(numero) {
        None => numero.to_string(),
        Some(c) => (1..=3).filter_map(|i| c.get(numero, i)).filter(|g| !g.is_empty()).collect::<Vec<_>>().join("/"),
    }
}

fn subseccion(line: &str) -> Option<&'static str> {
    let u = line.to_uppercase();
    if u.starts_with("SENTENCIA") {
        if u.contains("INTERLOCUTORIA") { return Some("sentencia_interlocutoria"); }
        if u.contains("DEFINITIVA") { return Some("sentencia_definitiva"); }
        return Some("sentencia");
    }
    if u.starts_with("AUDIENCIA") { return Some("audiencia"); }
    None
}

fn tipo_actuacion(text: &str, sub: Option<&str>) -> String {
    let p = p();
    let mut ultima: Option<(usize, String, String)> = None;
    for r in [&p.marca_actuacion, &p.marca_invertida] {
        for c in r.finditer_caps(text) {
            if ultima.as_ref().map_or(true, |u| c.start() > u.0) {
                ultima = Some((c.start(), c.get(text, 1).unwrap().to_lowercase(), c.get(text, 2).unwrap_or("").to_lowercase()));
            }
        }
    }
    let (_, palabra, calif) = match ultima { Some(u) => u, None => return sub.unwrap_or("acuerdo").to_string() };
    if palabra.starts_with("acdo") { return "acuerdo".into(); }
    if palabra.starts_with("aud") { return "audiencia".into(); }
    if calif.starts_with("def") { return "sentencia_definitiva".into(); }
    if calif.starts_with("inter") { return "sentencia_interlocutoria".into(); }
    match sub { Some(s) if s.starts_with("sentencia") => s.to_string(), _ => "sentencia".into() }
}

fn relocate_tipo_juicio(demandada: Option<String>, tipo: Option<String>) -> (Option<String>, Option<String>) {
    let t = match &tipo { Some(t) if !t.is_empty() => t.clone(), _ => return (demandada, tipo) };
    if match_canonical_tipo(&t).is_some() { return (demandada, tipo); }
    for m in p().tipo_start.finditer(&t) {
        if m.start == 0 { continue; }
        if let Some(canon) = match_canonical_tipo(&t[m.start..]) {
            let leaked = strip(strip_c(strip(&t[..m.start]), ",")).to_string();
            let d = match &demandada {
                Some(d) if !d.is_empty() => strip(&format!("{} {}", d, leaked)).to_string(),
                _ => leaked,
            };
            return (Some(d), Some(canon.to_string()));
        }
    }
    (demandada, tipo)
}

fn split_demandada_tipo(segment: &str, end: usize) -> (String, Option<String>) {
    let starts: Vec<usize> = p().tipo_start.finditer(segment).into_iter().map(|m| m.start).filter(|&s| s < end).collect();
    if starts.is_empty() {
        return (rstrip_c(strip(&segment[..end]), ".").to_string(), None);
    }
    for &start in starts.iter().rev() {
        if let Some(canon) = match_canonical_tipo(&segment[start..end]) {
            return (rstrip_c(strip(&segment[..start]), ".").to_string(), Some(canon.to_string()));
        }
    }
    let start = *starts.last().unwrap();
    (rstrip_c(strip(&segment[..start]), ".").to_string(), Some(strip(&segment[start..end]).to_string()))
}

fn split_tipo_canonico(segment: &str) -> Option<(String, String)> {
    for m in p().tipo_start.finditer(segment).into_iter().rev() {
        if m.start == 0 { continue; }
        if let Some(canon) = match_canonical_tipo(&segment[m.start..]) {
            return Some((rstrip_c(strip(&segment[..m.start]), ".").to_string(), canon.to_string()));
        }
    }
    None
}

fn split_tipo_juicio(last_seg: &str) -> Option<(String, String, i64)> {
    let tail = p().tipo_tail.caps(last_seg)?;
    let (dem, tipo) = split_demandada_tipo(last_seg, tail.start());
    let tipo = tipo?;
    Some((dem, tipo, py_int(tail.get(last_seg, 1).unwrap())))
}

fn looks_like_new_entry_start(resto: &str) -> bool {
    if resto.is_empty() { return true; }
    if !p().looks_new.is_match_start(resto) { return false; }
    re_vs().is_search(cprefix(resto, 300))
}

fn es_mayusculas(line: &str) -> bool { !p().minus.is_search(line) }

fn es_continuacion_organo(line: &str) -> bool {
    let p = p();
    es_mayusculas(line) && clen(line) <= 70
        && !en(SECTION_HEADERS, line) && !en(SKIP_LINES, line)
        && !p.skip_prefix.is_match_start(line) && !p.titular.is_match_start(line)
        && !p.secretaria.is_match_start(line) && !p.acuerdos.is_match_start(line)
        && !p.juzgado.is_match_start(line) && !p.sala.is_match_start(line)
        && !p.organo_inicio.is_match_start(line)
}

struct Parser<'a> {
    boletin_id: i64,
    fecha: &'a str,
    ctx: Ctx,
    entries: Vec<Row>,
    block: Vec<String>,
}

impl<'a> Parser<'a> {
    fn flush_bare(&mut self) {
        if self.block.is_empty() { return; }
        let jb = self.block.join(" ");
        let entry = if p().entry_end_bare.is_search(&jb) {
            parse_entry(&format!("{}.", rstrip(&jb)), self.boletin_id, self.fecha, &self.ctx)
        } else {
            match p().entry_end.search(&jb) {
                Some(m) if strip(&jb[m.end..]).is_empty() => {}
                _ => return,
            }
            parse_entry(&jb, self.boletin_id, self.fecha, &self.ctx)
        };
        if let Some(e) = entry { self.entries.push(e); }
        self.block.clear();
    }
}

pub fn parse_pdf(text: &str, boletin_id: i64, fecha_raw: &str) -> Vec<Row> {
    let p = p();
    let mut ps = Parser { boletin_id, fecha: fecha_raw, ctx: Ctx { pagina: 1, ..Default::default() }, entries: Vec::new(), block: Vec::new() };
    let mut ultimo_toca: Option<Row> = None;
    let mut en_cadena = false;
    let mut organo: Option<Vec<String>> = None;

    for (n, pagina) in text.split('\x0c').enumerate() {
        for raw_line in splitlines(pagina) {
            ps.ctx.pagina = n + 1;
            let line = strip(raw_line);

            if let Some(org) = organo.as_mut() {
                if !line.is_empty() && es_continuacion_organo(line) { org.push(line.to_string()); continue; }
                ps.ctx.juzgado = Some(org.join(" "));
                ps.ctx.sala = None; ps.ctx.secretaria = None; ps.ctx.subseccion = None;
                organo = None;
                ultimo_toca = None; en_cadena = false;
            }

            if line.is_empty() || clen(line) < 3 {
                ps.flush_bare();
                if !ps.block.is_empty() {
                    let jb = ps.block.join(" ");
                    if !p.entry_end.is_search(&jb) && !p.toca_end.is_search(&jb) { ps.block.clear(); }
                }
                continue;
            }

            let es_juzgado = p.juzgado.is_match_start(line);
            let es_organo = !ps.ctx.seccion.as_deref().map_or(false, |s| en(SECCIONES_SIN_ORGANO, s))
                && es_mayusculas(line) && p.organo_inicio.is_match_start(line) && !es_juzgado;
            let es_titular = p.titular.is_match_start(line);
            let es_header = en(SECTION_HEADERS, line);
            let es_skip = en(SKIP_LINES, line) || p.skip_prefix.is_match_start(line);
            let es_sala = p.sala.is_match_start(line);
            let es_secretaria = p.secretaria.is_match_start(line);
            let m_ac = p.acuerdos.match_caps(line);

            if es_header || es_skip || es_juzgado || es_sala || es_organo || es_titular || es_secretaria || m_ac.is_some() {
                ps.flush_bare();
            }
            if es_organo { organo = Some(vec![line.to_string()]); ps.block.clear(); continue; }
            if es_titular { continue; }
            if es_header {
                ps.ctx.seccion = Some(line.to_string());
                ps.ctx.juzgado = None; ps.ctx.sala = None; ps.ctx.secretaria = None; ps.ctx.subseccion = None;
                ps.block.clear();
                ultimo_toca = None; en_cadena = false;
                continue;
            }
            if es_skip {
                if let Some(sub) = subseccion(line) { ps.ctx.subseccion = Some(sub.to_string()); }
                else if line.to_uppercase().starts_with("ACUERDO") { ps.ctx.subseccion = None; }
                continue;
            }
            if es_juzgado {
                ps.ctx.juzgado = Some(line.to_string());
                ps.ctx.sala = None; ps.ctx.secretaria = None; ps.ctx.subseccion = None;
                ps.block.clear();
                ultimo_toca = None; en_cadena = false;
                continue;
            }
            if es_sala {
                ps.ctx.sala = Some(line.to_string());
                ps.ctx.juzgado = None; ps.ctx.secretaria = None; ps.ctx.subseccion = None;
                ps.block.clear();
                ultimo_toca = None; en_cadena = false;
                continue;
            }
            if es_secretaria { ps.ctx.secretaria = Some(line.to_string()); ps.block.clear(); continue; }
            if let Some(c) = m_ac {
                ps.ctx.fecha_acuerdo = Some(strip(c.get(line, 1).unwrap()).to_string());
                ps.ctx.subseccion = None;
                ps.block.clear();
                continue;
            }

            ps.block.push(line.to_string());
            let mut joined = ps.block.join(" ");

            let mut search_from = 0;
            loop {
                let m_exp = p.entry_end.caps_at(&joined, search_from);
                let m_toca = p.toca_end.caps_at(&joined, search_from);
                let (m_end, is_toca) = match (m_exp, m_toca) {
                    (Some(e), Some(t)) => if e.start() < t.start() { (e, false) } else { (t, true) },
                    (Some(e), None) => (e, false),
                    (None, Some(t)) => (t, true),
                    (None, None) => break,
                };

                let mut letra_suelta = false;
                let mut razon_social = false;
                if !is_toca {
                    let (g1s, g1e) = m_end.span(1).unwrap();
                    let g1 = &joined[g1s..g1e];
                    let num_end = p.num.match_(g1).unwrap().end;
                    let sufijo = &g1[num_end..];
                    let siguiente = strip(&joined[m_end.end()..]);
                    letra_suelta = p.sufijo_letra_suelta.is_search(sufijo);
                    if letra_suelta && siguiente.is_empty() { break; }
                    razon_social = letra_suelta && p.continua_razon_social.is_match_start(siguiente);
                    if p.sufijo_fusionado.is_search(sufijo) || razon_social {
                        let corte = g1s + num_end;
                        if let Some(e) = parse_entry(&format!("{}.", &joined[..corte]), boletin_id, fecha_raw, &ps.ctx) { ps.entries.push(e); }
                        joined = strip(&joined[corte..]).to_string();
                        ps.block = vec![joined.clone()];
                        search_from = 0;
                        continue;
                    }
                }

                let resto = strip(&joined[m_end.end()..]).to_string();
                let corte_seguro = !is_toca && letra_suelta && !razon_social;
                let cadena = is_toca && prefijo_cadena(&resto).is_some();
                if !corte_seguro && !cadena && !looks_like_new_entry_start(&resto) {
                    search_from = m_end.end();
                    continue;
                }

                let mut texto: String = joined[..m_end.end()].to_string();
                let mut entry: Option<Row> = None;
                if is_toca && en_cadena {
                    entry = parse_toca_cadena(&texto, ultimo_toca.as_ref(), boletin_id, fecha_raw, &ps.ctx);
                    if entry.is_none() { texto = lstrip_c(&texto, ",. ").to_string(); }
                }
                if entry.is_none() {
                    entry = if is_toca { parse_toca_entry(&texto, boletin_id, fecha_raw, &ps.ctx) }
                            else { parse_entry(&texto, boletin_id, fecha_raw, &ps.ctx) };
                }
                if let Some(e) = &entry { ps.entries.push(e.clone()); }
                ultimo_toca = if is_toca { entry } else { None };
                en_cadena = cadena;

                if resto.is_empty() { ps.block.clear(); break; }
                ps.block = vec![resto.clone()];
                joined = resto;
                search_from = 0;
            }
        }
    }
    ps.entries
}

fn parse_entry(text: &str, boletin_id: i64, fecha: &str, ctx: &Ctx) -> Option<Row> {
    let p = p();
    let m = p.entry_end.caps(text)?;
    let expediente = strip(m.get(text, 1).unwrap()).to_string();
    let g2 = m.get(text, 2).filter(|s| !s.is_empty());
    let mut pre: String = strip(&text[..m.start()]).to_string();
    if p.vs_inicio.is_match_start(&pre) { pre = format!(" {}", pre); }
    let vs = &re_vs();
    let partes = vs.split(&pre, 1);
    let actora = strip(partes[0]).to_string();

    if partes.len() < 2 {
        let (mut actora, mut tipo, mut num) = (actora, None, None);
        if let Some((a, t, n)) = split_tipo_juicio(&actora) {
            actora = a; tipo = Some(t); num = Some(n);
        } else if let Some(g2) = g2 {
            let (a, t) = split_demandada_tipo(&actora, actora.len());
            actora = a; tipo = t; num = Some(py_int(g2));
        } else if let Some((a, t)) = split_tipo_canonico(&actora) {
            actora = a; tipo = Some(t);
        }
        let (a, t) = relocate_tipo_juicio(Some(actora), tipo);
        let a = a.filter(|s| !s.is_empty());
        return Some(row(boletin_id, fecha, ctx, a, None, t, num, &expediente, text));
    }

    let resto = strip(partes[1]);
    let (demandada_raw, last_seg): (Option<String>, &str) = if resto.contains("//") {
        let segs: Vec<&str> = resto.split("//").collect();
        (Some(rstrip_c(strip(segs[0]), ".").to_string()), strip(segs[segs.len() - 1]))
    } else { (None, resto) };

    let split = if demandada_raw.is_none() { split_tipo_juicio(last_seg) } else { None };
    let (demandada, tipo, num): (Option<String>, Option<String>, Option<i64>);
    if let Some((d, t, n)) = split {
        demandada = Some(d); tipo = Some(t); num = Some(n);
    } else if let Some(mt) = p.tipo_acdos.caps(last_seg) {
        demandada = Some(match &demandada_raw { None => strip(&last_seg[..mt.start() + 1]).to_string(), Some(d) => d.clone() });
        let tipo_raw = strip(mt.get(last_seg, 1).unwrap());
        tipo = Some(match_canonical_tipo(tipo_raw).map(|s| s.to_string()).unwrap_or_else(|| tipo_raw.to_string()));
        num = Some(py_int(mt.get(last_seg, 2).unwrap()));
    } else if let Some(g2) = g2 {
        match &demandada_raw {
            None => { let (d, t) = split_demandada_tipo(last_seg, last_seg.len()); demandada = Some(d); tipo = t; }
            Some(d) => { demandada = Some(d.clone()); tipo = match_canonical_tipo(last_seg).map(|s| s.to_string()); }
        }
        num = Some(py_int(g2));
    } else {
        let mut d = Some(demandada_raw.clone().unwrap_or_else(|| resto.to_string()));
        let mut t = None;
        if demandada_raw.is_none() {
            if let Some((dd, tt)) = split_tipo_canonico(last_seg) { d = Some(dd); t = Some(tt); }
        }
        demandada = d; tipo = t; num = None;
    }
    let (demandada, tipo) = relocate_tipo_juicio(demandada, tipo);
    Some(row(boletin_id, fecha, ctx, Some(actora), demandada, tipo, num, &expediente, text))
}

fn parse_toca_entry(text: &str, boletin_id: i64, fecha: &str, ctx: &Ctx) -> Option<Row> {
    let m = p().toca_end.caps(text)?;
    let expediente = normalizar_toca(strip(m.get(text, 1).unwrap()));
    let num = m.get(text, 2).filter(|s| !s.is_empty()).map(py_int).unwrap_or(1);
    let pre = strip(&text[..m.start()]);
    let partes = re_vs().split(pre, 1);
    let actora = strip(partes[0]).to_string();
    if partes.len() < 2 {
        return Some(row(boletin_id, fecha, ctx, Some(actora), None, None, Some(num), &expediente, text));
    }
    let resto = strip(partes[1]);
    let (d, t) = split_demandada_tipo(resto, resto.len());
    let (d, t) = relocate_tipo_juicio(Some(d), t);
    Some(row(boletin_id, fecha, ctx, Some(actora), d, t, Some(num), &expediente, text))
}

fn prefijo_cadena(resto: &str) -> Option<String> {
    let p = p();
    let m = p.cadena_toca.match_caps(resto)?;
    let coma = m.get(resto, 1).unwrap_or("");
    let prefijo = strip(m.get(resto, 2).unwrap_or(""));
    if re_vs().is_search(&format!(" {} ", prefijo)) { return None; }
    if coma == "," || prefijo.is_empty() || p.tipo_start.is_match_start(prefijo) || p.tipo_cadena.is_match_start(prefijo)
        || match_canonical_tipo(prefijo).is_some() {
        return Some(prefijo.to_string());
    }
    None
}

fn parse_toca_cadena(text: &str, padre: Option<&Row>, boletin_id: i64, fecha: &str, ctx: &Ctx) -> Option<Row> {
    let m = p().toca_end.caps(text)?;
    let padre = padre?;
    let prefijo = strip(lstrip_c(strip(&text[..m.start()]), ",."));
    if clen(prefijo) > 120 || re_vs().is_search(&format!(" {} ", prefijo)) { return None; }
    let tipo = match match_canonical_tipo(prefijo) {
        Some(c) => Some(c.to_string()),
        None => {
            let r = rstrip_c(prefijo, " ,.");
            if !r.is_empty() { Some(r.to_string()) } else { padre.tipo_juicio.clone() }
        }
    };
    let num = m.get(text, 2).filter(|s| !s.is_empty()).map(py_int).unwrap_or(1);
    let exp = normalizar_toca(strip(m.get(text, 1).unwrap()));
    Some(row(boletin_id, fecha, ctx, padre.actora.clone(), padre.demandada.clone(), tipo, Some(num), &exp, text))
}

#[allow(clippy::too_many_arguments)]
fn row(boletin_id: i64, fecha: &str, ctx: &Ctx, actora: Option<String>, demandada: Option<String>, tipo_juicio: Option<String>,
       num_acdos: Option<i64>, expediente: &str, raw_text: &str) -> Row {
    let p = p();
    let mut actora = actora;
    if no_vacio(&actora) {
        let a = actora.unwrap();
        let s = p.arrastre_actora.sub(&a, "", 0);
        actora = Some(if s.is_empty() { a } else { s.into_owned() });
    }
    let actora_q = match &actora { Some(a) => quitar_marca_publicacion(a), None => String::new() };
    let mut actora = if actora_q.is_empty() { None } else { Some(actora_q) };
    let mut demandada = demandada;
    let mut sobrante: Option<String> = None;
    if no_vacio(&demandada) {
        let (d, s) = separar_parte(demandada.as_deref().unwrap());
        demandada = Some(d); sobrante = s;
    } else if no_vacio(&actora) {
        let (a, s) = separar_parte(actora.as_deref().unwrap());
        actora = Some(a); sobrante = s;
    }
    let (expediente, cuaderno) = separar_expediente(expediente);
    let ponencia = p.ponencia.caps(raw_text).map(|c| py_int(c.get(raw_text, 1).unwrap()));
    let dem = demandada.as_deref();
    Row {
        boletin_id,
        fecha: fecha.to_string(),
        ctx: ctx.clone(),
        tipo_actuacion: tipo_actuacion(raw_text, ctx.subseccion.as_deref()),
        ponencia,
        tipo_acuerdo: tipo_acuerdo(raw_text, dem),
        descripcion: descripcion(raw_text, dem).or_else(|| descripcion_de_sobrante(sobrante.as_deref())),
        actora,
        demandada,
        tipo_juicio,
        num_acdos,
        expediente,
        cuaderno,
        raw_text: raw_text.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn toca() { assert_eq!(normalizar_toca("300-2019001"), "300/2019/001"); assert_eq!(normalizar_toca("84-2020"), "84/2020"); }
}
