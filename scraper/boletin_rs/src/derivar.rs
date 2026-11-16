// Port de build_db.py: limpiar_fila, derivar y lo que llaman (fecha_iso,
// fecha_acuerdo_iso, _tipo_acuerdo_fila, _descripcion_fila, RE_ANIO).
use crate::acuerdos;
use crate::fechas::{fecha_acuerdo_iso, fecha_iso};
use crate::partes::detectar;
use crate::py::py_int;
use crate::re::Re;
use crate::tipos::normalize;
use std::collections::{BTreeSet, HashMap};
use std::sync::OnceLock;

pub const FIELDS: [&str; 12] = [
    "boletin_id", "juzgado", "sala", "secretaria",
    "actora", "demandada", "tipo_juicio", "expediente", "num_acdos", "pagina",
    "tipo_actuacion", "ponencia",
];
const DERIVADAS: [&str; 6] = ["tipo_juicio_norm", "anio", "fecha_acuerdo", "tipo_acuerdo", "descripcion", "cuaderno"];
/// FIELDS + las derivadas.
pub const COLS_CARGA: [&str; 18] = {
    let mut a = [""; 18];
    let mut i = 0;
    while i < 18 { a[i] = if i < 12 { FIELDS[i] } else { DERIVADAS[i - 12] }; i += 1; }
    a
};
pub const COLS_ENTERAS: [&str; 5] = ["boletin_id", "num_acdos", "pagina", "ponencia", "anio"];
const I_EXPEDIENTE: usize = 7;
pub const TIPO_EMPRESA: u8 = 1;
pub const TIPO_PERSONA: u8 = 2;
/// ROLES: actora 1, demandada 2.
pub const ROL_ACTORA: u8 = 1;
pub const ROL_DEMANDADA: u8 = 2;

/// Posiciones de las columnas en el CSV (el dict `col` de Python). Los CSV
/// viejos no tienen algunas (cuaderno, tipo_acuerdo, descripcion...).
pub struct Cols {
    pub act: usize,
    pub dem: usize,
    pub exp: usize,
    pub tipo: usize,
    pub facdo: Option<usize>,
    pub tacdo: Option<usize>,
    pub raw: Option<usize>,
    pub act_t: Option<usize>,
    pub desc: Option<usize>,
    pub cuad: Option<usize>,
    pub fecha_raw: Option<usize>,
    pub fields: [Option<usize>; 12],
}

impl Cols {
    pub fn desde_encabezado(header: &[String]) -> Result<Cols, String> {
        let col: HashMap<&str, usize> = header.iter().enumerate().map(|(i, n)| (n.as_str(), i)).collect();
        let req = |k: &str| col.get(k).copied().ok_or_else(|| format!("falta la columna {k}"));
        let mut fields = [None; 12];
        for (j, k) in FIELDS.iter().enumerate() { fields[j] = col.get(k).copied(); }
        Ok(Cols {
            act: req("actora")?,
            dem: req("demandada")?,
            exp: req("expediente")?,
            tipo: req("tipo_juicio")?,
            facdo: col.get("fecha_acuerdo").copied(),
            tacdo: col.get("tipo_acuerdo").copied(),
            raw: col.get("raw_text").copied(),
            act_t: col.get("tipo_actuacion").copied(),
            desc: col.get("descripcion").copied(),
            cuad: col.get("cuaderno").copied(),
            fecha_raw: col.get("fecha").copied(),
            fields,
        })
    }
}

/// Resultado de derivar para un aviso.
pub struct Derivado {
    /// En el orden de COLS_CARGA. anio va como texto del entero.
    pub valores: Vec<Option<String>>,
    /// (tipo, rol, núcleo, nombre), sin repetidos: empresas (tipo 1) y personas (tipo 2).
    pub det: BTreeSet<(u8, u8, String, String)>,
    /// (otras_actora, otras_demandada) si el aviso tiene alguna empresa.
    pub partes: Option<(u8, u8)>,
    /// Fecha ISO del boletín.
    pub fecha: Option<String>,
}

fn re_anio() -> &'static Re {
    static R: OnceLock<Re> = OnceLock::new();
    R.get_or_init(|| Re::new(r"/(\d{4})\b"))
}

fn no_vacio(s: &str) -> Option<String> { if s.is_empty() { None } else { Some(s.to_string()) } }

/// limpiar_fila: limpia actora y demandada en el lugar y devuelve el sobrante.
pub fn limpiar_fila(row: &mut [String], c: &Cols) -> Option<String> {
    row[c.act] = acuerdos::quitar_marca_publicacion(&row[c.act]);
    row[c.dem] = acuerdos::quitar_marca_publicacion(&row[c.dem]);
    let i = if !row[c.dem].is_empty() { c.dem } else if !row[c.act].is_empty() { c.act } else { return None };
    let (nombre, sobrante) = acuerdos::separar_parte(&row[i]);
    row[i] = nombre;
    sobrante
}

/// _tipo_acuerdo_fila y _descripcion_fila: solo para acuerdos.
fn es_acuerdo(row: &[String], c: &Cols) -> bool {
    c.raw.is_some() && c.act_t.map_or(true, |i| row[i].is_empty() || row[i] == "acuerdo")
}

/// derivar. `row` tiene que tener al menos tantos campos como el encabezado.
pub fn derivar(row: &mut [String], c: &Cols) -> Derivado {
    let sobrante = limpiar_fila(row, c);
    let anio = re_anio().caps(&row[c.exp]).map(|m| py_int(m.get(&row[c.exp], 1).unwrap()));
    let mut det = BTreeSet::new();
    let mut hay_empresa = false;
    let mut otras = [false; 2];
    for (j, (i, rol)) in [(c.act, ROL_ACTORA), (c.dem, ROL_DEMANDADA)].into_iter().enumerate() {
        let d = detectar(&row[i]);
        for (n, nombre) in &d.personas { det.insert((TIPO_PERSONA, rol, n.clone(), nombre.clone())); }
        hay_empresa |= !d.empresas.is_empty();
        otras[j] = d.otras;
        for (n, nombre) in &d.empresas { det.insert((TIPO_EMPRESA, rol, n.clone(), nombre.clone())); }
    }
    let partes = if hay_empresa { Some((otras[0] as u8, otras[1] as u8)) } else { None };
    let mut valores: Vec<Option<String>> = Vec::with_capacity(COLS_CARGA.len());
    valores.extend(c.fields.iter().map(|i| i.and_then(|i| no_vacio(&row[i]))));
    let cuaderno = match c.cuad {
        Some(i) => no_vacio(&row[i]),
        None => match valores[I_EXPEDIENTE].take() {
            Some(e) => {
                let (numero, cuaderno) = acuerdos::separar_expediente(&e);
                valores[I_EXPEDIENTE] = Some(numero);
                cuaderno
            }
            None => None,
        },
    };
    let tipo_norm = normalize(Some(row[c.tipo].as_str()));
    let fecha_acuerdo = c.facdo.and_then(|i| fecha_acuerdo_iso(&row[i]));
    let dem = Some(row[c.dem].as_str()).filter(|s| !s.is_empty());
    let tipo_acuerdo = match c.tacdo {
        Some(i) => no_vacio(&row[i]),
        None if es_acuerdo(row, c) => acuerdos::tipo_acuerdo(&row[c.raw.unwrap()], dem).map(String::from),
        None => None,
    };
    let desc = match c.desc {
        Some(i) => no_vacio(&row[i]),
        None if es_acuerdo(row, c) => acuerdos::descripcion(&row[c.raw.unwrap()], dem),
        None => None,
    };
    let desc = desc.or_else(|| acuerdos::descripcion_de_sobrante(sobrante.as_deref()));
    valores.push(tipo_norm.map(String::from));
    valores.push(anio.map(|a| a.to_string()));
    valores.push(fecha_acuerdo);
    valores.push(tipo_acuerdo);
    valores.push(desc);
    valores.push(cuaderno);
    let fecha = c.fecha_raw.and_then(|i| fecha_iso(&row[i]));
    Derivado { valores, det, partes, fecha }
}
