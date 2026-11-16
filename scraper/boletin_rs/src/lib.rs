// Scraper, parser y precálculo del Boletín Judicial CDMX en Rust. Port de
// los módulos de Python de scraper/, que son la referencia.
pub mod py; // semántica de str e int de Python, traducción de expresiones de re
pub mod re; // la envoltura de regex, con los atajos exactos
pub mod cache; // cachés por hilo (los lru_cache de Python)
pub mod json; // JSON como el de Python
pub mod sets; // conjuntos de palabras, generados desde Python (verificar.py)
pub mod tipos; // normalize_tipo.py
pub mod acuerdos; // acuerdos.py
pub mod partes; // personas.py y empresas.py
pub mod fechas; // fechas del boletín (build_db.py y poc.py)
pub mod derivar; // limpiar_fila y derivar de build_db.py
pub mod precalc; // generar_precalc de build_db.py, en CSV
pub mod poc; // poc.py y reparse.py
pub mod cli; // binario boletin
