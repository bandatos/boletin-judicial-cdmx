// main() de poc.py (descargar) y de reparse.py (reparsear).
use super::html;
use super::parser::{parse_pdf, Row};
use super::salida::write_rows;
use crate::fechas::fecha_boletin;
use crate::json::{self, obj, py_float, py_round1, J, V};
use crate::py::decode_text;
use crate::re::Re;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command;

pub const BASE_URL: &str = "https://consultabpj.poderjudicialcdmx.gob.mx:2096";

fn log(s: &str) { eprintln!("{}", s); }

pub struct Boletin { pub id: i64, pub fecha_raw: String, pub pdf_url: Option<String>, pub pdf_url_portal: Option<String> }

fn curl(args: &[&str]) -> Result<Vec<u8>, String> {
    let o = Command::new("curl").args(args).output().map_err(|e| format!("curl: {}", e))?;
    if !o.status.success() {
        return Err(format!("curl salió con {}: {}", o.status.code().unwrap_or(-1), String::from_utf8_lossy(&o.stderr).trim()));
    }
    Ok(o.stdout)
}

/// get_session + fetch_index. El cookie jar va en un archivo temporal.
pub fn fetch_index(fecha_inicio: &str, fecha_fin: &str, tmp: &Path) -> Result<Vec<Boletin>, String> {
    let jar = tmp.join(format!(".poc_cookies_{}", std::process::id()));
    let jar_s = jar.to_string_lossy().to_string();
    let r = (|| {
        let pag = curl(&["-sSk", "-L", "-c", &jar_s, &format!("{}/consultaboletinpjcdmx", BASE_URL)])?;
        let token = html::csrf_token(&String::from_utf8_lossy(&pag)).ok_or("sin csrf-token")?;
        let body = curl(&["-sSk", "-L", "-b", &jar_s, "-c", &jar_s,
            "-H", &format!("Referer: {}/consultaboletinpjcdmx", BASE_URL),
            "--data-urlencode", &format!("_token={}", token),
            "--data-urlencode", &format!("fechainicial={}", fecha_inicio),
            "--data-urlencode", &format!("fechafinal={}", fecha_fin),
            &format!("{}/consultaboletinpjcdmx/filtrar", BASE_URL)])?;
        Ok(parse_index(&String::from_utf8_lossy(&body)))
    })();
    let _ = fs::remove_file(&jar);
    r
}

pub fn parse_index(body: &str) -> Vec<Boletin> {
    let re_id = Re::new(r"boletinexterno(\d+)");
    let re_pdf = Re::new(r##"https://(?:(?!https://)[^"#])+\.pdf"##);
    let mut out = Vec::new();
    for m in html::modales(body) {
        let c = match re_id.caps(&m.id) { Some(c) => c, None => continue };
        let id: i64 = c.get(&m.id, 1).unwrap().parse().unwrap_or(0);
        let pdf_url = m.embed_src.as_deref().and_then(|src| re_pdf.finditer(src).last().map(|x| src[x.start..x.end].to_string()));
        out.push(Boletin { id, fecha_raw: m.titulo.unwrap_or_default(), pdf_url, pdf_url_portal: None });
    }
    out
}

fn download_pdf(url: &str, dest: &Path) -> Result<(), String> {
    let tmp = dest.with_extension("part");
    let tmp_s = tmp.to_string_lossy().to_string();
    // Igual que requests.get(..., timeout=120) sin verify=False: verifica TLS.
    let r = curl(&["-sS", "-L", "--fail", "--connect-timeout", "120", "--speed-time", "120", "--speed-limit", "1", "-o", &tmp_s, url]);
    if let Err(e) = r { let _ = fs::remove_file(&tmp); return Err(e); }
    fs::rename(&tmp, dest).map_err(|e| e.to_string())
}

pub fn urls_alternativas(pdf_url: &str, fecha_raw: &str) -> Vec<String> {
    let mu = match Re::new(r"^(.*/pdf/boletines/)\d{8}\d?\.pdf$").caps(pdf_url) { Some(m) => m, None => return vec![] };
    let (dia, mes, anio) = match fecha_boletin(fecha_raw) { Some(f) => f, None => return vec![] };
    let base = format!("{:02}{:02}{}", dia, mes, anio);
    ["1", "2", ""].iter().map(|suf| format!("{}{}{}.pdf", mu.get(pdf_url, 1).unwrap(), base, suf))
        .filter(|u| u != pdf_url).collect()
}

pub fn pdf_to_text(pdf: &Path) -> String {
    match Command::new("pdftotext").arg(pdf).arg("-").output() {
        Ok(o) => decode_text(&o.stdout),
        Err(_) => String::new(),
    }
}

fn index_json(bs: &[Boletin]) -> String {
    if bs.is_empty() { return "[]".into(); }
    let mut out = String::from("[\n");
    for (i, b) in bs.iter().enumerate() {
        out.push_str("  ");
        let mut campos = vec![("id", V::I(b.id)), ("fecha_raw", V::S(b.fecha_raw.clone())),
            ("pdf_url", b.pdf_url.clone().map(V::S).unwrap_or(V::N))];
        if let Some(p) = &b.pdf_url_portal { campos.push(("pdf_url_portal", V::S(p.clone()))); }
        obj(&mut out, &campos, 1);
        if i + 1 < bs.len() { out.push(','); }
        out.push('\n');
    }
    out.push(']');
    out
}

fn hoy() -> String {
    Command::new("date").arg("+%Y-%m-%d").output().ok()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string()).unwrap_or_else(|| "hoy".into())
}

fn cuenta(entries: &[Row]) -> (usize, usize, usize) {
    let nv = |s: &Option<String>| s.as_ref().map_or(false, |s| !s.is_empty());
    (entries.iter().filter(|r| nv(&r.actora) && nv(&r.demandada) && nv(&r.tipo_juicio)).count(),
     entries.iter().filter(|r| !nv(&r.demandada)).count(),
     entries.iter().filter(|r| !nv(&r.tipo_juicio)).count())
}

/// `poc descargar ...` o `poc reparsear ...` (args sin "poc").
pub fn main(args: &[String]) -> i32 {
    match args.first().map(String::as_str) {
        Some("descargar") => descargar(&args[1..]),
        Some("reparsear") => reparsear(&args[1..]),
        _ => { eprintln!("uso: poc descargar [fecha_inicio] [fecha_fin] [max_boletines] [out_dir]\n     poc reparsear [run_dir] [--workers=N]"); 2 }
    }
}

/// `poc descargar [fecha_inicio] [fecha_fin] [max_boletines] [out_dir]`
pub fn descargar(args: &[String]) -> i32 {
    let fecha_inicio = args.first().cloned().unwrap_or_else(|| "2026-06-09".into());
    let fecha_fin = args.get(1).cloned().unwrap_or_else(|| "2026-06-10".into());
    let max_boletines: usize = args.get(2).map(|s| s.parse().expect("max_boletines")).unwrap_or(1);
    let out_dir = args.get(3).map(PathBuf::from).unwrap_or_else(|| Path::new("data").join(hoy()));
    fs::create_dir_all(&out_dir).unwrap();
    let pdf_dir = out_dir.join("pdfs");
    let _ = fs::create_dir(&pdf_dir);

    log(&format!("Directorio de salida: {}", out_dir.display()));
    log(&format!("Obteniendo índice {} → {}...", fecha_inicio, fecha_fin));
    let mut boletines = match fetch_index(&fecha_inicio, &fecha_fin, &out_dir) {
        Ok(b) => b,
        Err(e) => { log(&format!("ERROR obteniendo el índice: {}", e)); return 1; }
    };
    log(&format!("  {} boletín(es) encontrado(s)", boletines.len()));

    let index_path = out_dir.join("index.json");
    fs::write(&index_path, index_json(&boletines)).unwrap();
    log(&format!("  Índice guardado en {}", index_path.display()));

    let csv_path = out_dir.join("entradas.csv");
    let mut csv_file: Option<fs::File> = None;
    let (mut total, mut completas, mut sin_vs, mut sin_tipo, mut corregidas) = (0usize, 0usize, 0usize, 0usize, 0usize);
    let n = max_boletines.min(boletines.len());

    for b in boletines[..n].iter_mut() {
        log(&format!("\nBoletín {} — {}", b.id, b.fecha_raw));
        let url0 = match &b.pdf_url { Some(u) if !u.is_empty() => u.clone(), _ => { log("  Sin URL de PDF, saltando."); continue; } };
        let pdf_path = pdf_dir.join(format!("boletin_{}.pdf", b.id));
        if !pdf_path.exists() {
            log("  Descargando PDF...");
            let mut error: Option<String> = None;
            let mut urls = vec![url0.clone()];
            urls.extend(urls_alternativas(&url0, &b.fecha_raw));
            for url in urls {
                if let Err(e) = download_pdf(&url, &pdf_path) {
                    if error.is_none() { error = Some(e); }
                    continue;
                }
                if url != url0 {
                    log(&format!("  Link del portal roto, bajado de {}", url));
                    b.pdf_url_portal = Some(url0.clone());
                    b.pdf_url = Some(url.clone());
                    corregidas += 1;
                }
                error = None;
                break;
            }
            if let Some(e) = error { log(&format!("  ERROR descargando, saltando: {}", e)); continue; }
            let size = fs::metadata(&pdf_path).map(|m| m.len()).unwrap_or(0);
            log(&format!("  {:.1} MB descargados", size as f64 / 1e6));
        } else {
            log("  PDF ya en caché.");
        }

        log("  Extrayendo texto con pdftotext...");
        let text = pdf_to_text(&pdf_path);
        let txt_path = pdf_dir.join(format!("boletin_{}.txt", b.id));
        fs::write(&txt_path, text.as_bytes()).unwrap();
        log(&format!("  Texto guardado en {}", txt_path.display()));

        log("  Parseando entradas...");
        let entries = parse_pdf(&text, b.id, &b.fecha_raw);
        log(&format!("  {} entradas encontradas", entries.len()));
        if !entries.is_empty() {
            let header = csv_file.is_none();
            if header { csv_file = Some(fs::File::create(&csv_path).unwrap()); }
            let f = csv_file.as_mut().unwrap();
            write_rows(f, &entries, header).unwrap();
            f.flush().unwrap();
        }
        let (c, s, t) = cuenta(&entries);
        total += entries.len(); completas += c; sin_vs += s; sin_tipo += t;
    }
    drop(csv_file);

    if corregidas > 0 {
        fs::write(&index_path, index_json(&boletines)).unwrap();
        log(&format!("  {} URL(s) corregida(s) en {}", corregidas, index_path.display()));
    }
    log(&format!("\nTotal: {} entradas", total));
    if total > 0 {
        log(&format!("CSV guardado en {}", csv_path.display()));
        let procesados = boletines[..n].iter().filter(|b| b.pdf_url.as_ref().map_or(false, |u| !u.is_empty())).count();
        let pct = py_round1(completas as f64 / total as f64 * 100.0);
        let mut out = String::new();
        obj(&mut out, &[
            ("fecha_inicio", V::S(fecha_inicio.clone())), ("fecha_fin", V::S(fecha_fin.clone())),
            ("boletines_procesados", V::I(procesados as i64)), ("entradas_totales", V::I(total as i64)),
            ("entradas_completas", V::I(completas as i64)), ("pct_completas", V::F(pct)),
            ("sin_demandada", V::I(sin_vs as i64)), ("sin_tipo_juicio", V::I(sin_tipo as i64)),
        ], 0);
        let summary_path = out_dir.join("resumen.json");
        fs::write(&summary_path, out).unwrap();
        log(&format!("Resumen guardado en {}", summary_path.display()));
        log(&format!("\n{}", "─".repeat(40)));
        log(&format!("Entradas totales   : {}", total));
        log(&format!("Completas          : {} ({}%)", completas, py_float(pct)));
        log(&format!("Sin demandada      : {} ({:.1}%) — casos unilaterales", sin_vs, sin_vs as f64 / total as f64 * 100.0));
        log(&format!("Sin tipo_juicio    : {} ({:.1}%)", sin_tipo, sin_tipo as f64 / total as f64 * 100.0));
    }
    0
}

/// `poc reparsear [data_dir] [--workers=N]`
pub fn reparsear(args: &[String]) -> i32 {
    let resto: Vec<&String> = args.iter().filter(|a| !a.starts_with("--workers")).collect();
    let workers = args.iter().find(|a| a.starts_with("--workers")).and_then(|a| a.split_once('=')).and_then(|(_, n)| n.parse().ok())
        .unwrap_or_else(|| std::thread::available_parallelism().map(|n| n.get()).unwrap_or(4)).max(1);
    let data_dir = resto.first().map(PathBuf::from).unwrap_or_else(|| PathBuf::from("data/2026-06-10"));

    let index_txt = fs::read_to_string(data_dir.join("index.json")).expect("index.json");
    let index = json::parse(&index_txt).expect("index.json inválido");
    let mut fechas = std::collections::HashMap::new();
    if let J::Arr(v) = &index {
        for b in v {
            if let Some(id) = b.get("id").and_then(J::as_i64) {
                fechas.insert(id, b.get("fecha_raw").and_then(J::as_str).unwrap_or("").to_string());
            }
        }
    }
    let pdfs = data_dir.join("pdfs");
    let mut txts: Vec<PathBuf> = fs::read_dir(&pdfs).map(|d| d.filter_map(|e| e.ok()).map(|e| e.path())
        .filter(|p| p.file_name().and_then(|n| n.to_str()).map_or(false, |n| n.starts_with("boletin_") && n.ends_with(".txt")))
        .collect()).unwrap_or_default();
    txts.sort();
    log(&format!("{} archivos .txt encontrados en {}/pdfs/ — {} workers", txts.len(), data_dir.display(), workers));

    let jobs: Vec<(PathBuf, i64, String)> = txts.into_iter().map(|p| {
        let stem = p.file_stem().unwrap().to_string_lossy().replace("boletin_", "");
        let id: i64 = stem.parse().expect("id de boletín");
        let fecha = fechas.get(&id).cloned().unwrap_or_default();
        (p, id, fecha)
    }).collect();

    let csv_path = data_dir.join("entradas.csv");
    let tmp_path = data_dir.join("entradas.csv.part");
    let mut f = std::io::BufWriter::new(fs::File::create(&tmp_path).unwrap());
    let (mut total, mut done, mut header) = (0usize, 0usize, true);
    for lote in jobs.chunks(workers) {
        let res: Vec<Vec<Row>> = std::thread::scope(|s| {
            let hs: Vec<_> = lote.iter().map(|(p, id, fecha)| s.spawn(move || {
                let text = decode_text(&fs::read(p).unwrap());
                let r = parse_pdf(&text, *id, fecha);
                r
            })).collect();
            hs.into_iter().map(|h| h.join().unwrap()).collect()
        });
        for entries in res {
            if !entries.is_empty() { write_rows(&mut f, &entries, header).unwrap(); header = false; }
            total += entries.len();
            done += 1;
            if done % 50 == 0 || done == jobs.len() {
                log(&format!("  {}/{} boletines procesados ({} entradas hasta ahora)", done, jobs.len(), total));
            }
        }
    }
    f.flush().unwrap();
    drop(f);
    log(&format!("\nTotal: {} entradas", total));
    if total == 0 {
        let _ = fs::remove_file(&tmp_path);
        log("Sin entradas. Abortando.");
        return 1;
    }
    fs::rename(&tmp_path, &csv_path).unwrap();
    log(&format!("CSV guardado: {} ({} filas)", csv_path.display(), total));
    0
}


#[cfg(test)]
mod tests {
    use super::urls_alternativas;
    #[test]
    fn alternativas_como_python() {
        let b = "https://consultabpj.poderjudicialcdmx.gob.mx:2096/pdf/boletines/";
        assert_eq!(urls_alternativas(&format!("{b}300420091.pdf"), "Consulta del Boletin Judicial de fecha  30-abr.-2019"),
            vec![format!("{b}300420191.pdf"), format!("{b}300420192.pdf"), format!("{b}30042019.pdf")]);
        assert_eq!(urls_alternativas(&format!("{b}290420191.pdf"), "Consulta del Boletin Judicial de fecha  29-abr.-2019"),
            vec![format!("{b}290420192.pdf"), format!("{b}29042019.pdf")]);
        assert_eq!(urls_alternativas("https://x/pdf/boletines/310120221.pdf", "Consulta del Boletin Judicial de fecha  31-ene.-2022"),
            vec!["https://x/pdf/boletines/310120222.pdf".to_string(), "https://x/pdf/boletines/31012022.pdf".to_string()]);
        assert!(urls_alternativas("https://gestordocumental.x/a.pdf", "13-nov.-2026").is_empty());
    }
}
