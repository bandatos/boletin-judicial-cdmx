// Casos de regresión del parser, pasados de los tests de Python
// (test_tipo_acuerdo.py y bench_empresas.py) cuando se borró el parser en
// Python. Cada caso es un error que ya se corrigió o un falso positivo que
// no hay que reintroducir.
use boletin::{acuerdos, partes};

fn sin_punto(xs: &[String]) -> Vec<String> { xs.iter().map(|x| x.trim_end_matches('.').to_string()).collect() }
const TIPO_ACUERDO: &[(&str, Option<&str>, Option<&str>)] = &[
    ("Madrigal Robles Silvia y Robles Mendoza Víctor Manuel. Ord. Civil Prevención. 1 Acdo. Núm. Exp. 396/2026.", None, Some("prevencion")),
    ("Banca Múltiple Grupo Financiero BBVA México Vs. Mandujano Velazco Rubén. Cnpcyf Especial Hipotecario Oral Núm. Exp. 6703/2026, Prevención. 1 Acdo.", Some("Mandujano Velazco Rubén"), Some("prevencion")),
    ("Pérez vs. Martínez Martínez Pedro Antonio. Controversias de Arrendamiento Civil Se Previene Señale Dom. Dda. 1 Acdo. Núm. Exp. 1496/2012.", Some("Martínez Martínez Pedro Antonio"), Some("prevencion")),
    ("Martínez Báez Luis Rodrigo su sucesión. Sucesorio Familiar No Desahogo la Prevencion. 1 Acdo. Núm. Exp. 536/2026.", None, Some("prevencion")),
    ("Quiroz Robles Alejandro vs. Guevara Herrera Leticia. Ord. Civil Desecha. 1 Acdo. Núm. Exp. 396/2026.", Some("Guevara Herrera Leticia"), Some("negacion")),
    ("Grupo Comercolor S.A. de C.V. y Chavero Hernández Brenda. Ord. Mercantil Desechamiento. 1 Acdo. Núm. Exp. 382/2026.", None, Some("negacion")),
    ("Landum Servicios Integrales, S.A. de C.V. vs. F. V. Promotora, S.A. de C.V. Ord. Civil No Ha Lugar. 1 Acdo. Núm. Exp. 1273/2008.", Some("F. V. Promotora, S.A. de C.V."), Some("negacion")),
    ("A vs. Barroso Barroso Guillermo. Especial Hipotecario Civil No Se Admite Recurso de Queja. 1 Acdo. Núm. Exp. 2479/2024 Tomo Séptimo.", Some("Barroso Barroso Guillermo"), Some("negacion")),
    ("Gutiérrez Sánchez Myriam del Carmen Vs. Cuevas Téllez Angello Daniel. Cnpcyf - Alimentos Núm. Exp. 10513/2026, Desechamiento. 1 Acdo.", Some("Cuevas Téllez Angello Daniel"), Some("negacion")),
    ("Castañeda Vargas Anel vs. Mp Agregador S de R.L. de C.V. Ord. Civil Admisorio. 1 Acdo. Núm. Exp. 396/2026.", Some("Mp Agregador S de R.L. de C.V."), Some("admision")),
    ("Manzano Fuentevilla María de Los Ángeles su sucesión. Sucesorio Familiar Radicación. 1 Acdo. Núm. Exp. 571/2026.", None, Some("admision")),
    ("Mejía Velázquez Noemí Guadalupe Vs. Villafaña Leyva Irving. Cnpcyf Guarda y Custodia Núm. Exp. 6675/2026, Admision. 1 Acdo.", Some("Villafaña Leyva Irving"), Some("admision")),
    ("Gim Compañía Editorial S.A. de C.V. Ord. Civil Se Admiten Pruebs. Supervenientes. 1 Acdo. Núm. Exp. 191/2024 Segundo Tomo.", None, None),
    ("Navarro Felipe. Providencias Precautorias Oral Mercantil Respuesta Cnbv, Negativa de Desbloqueo, Oficio a Banco (Turno). 1 Acdo. Núm. Exp. 1045/2025.", None, None),
    ("Caja de Previsión de la Policía Preventiva de la Ciudad de México Vs. Jiménez Carmona César Alberto. Oral Civil 1 Acdo. Núm. Exp. 1119/2026.", Some("Jiménez Carmona César Alberto"), None),
    ("Desechables del Norte S.A. de C.V. vs. Instituto de Prevención Social. Ord. Civil 2 Acdos. Núm. Exp. 10/2026.", Some("Instituto de Prevención Social"), None),
    ("Rodríguez vs. López. Ord. Civil 1 Acdo. Núm. Exp. 12/2026.", Some("López"), None),
    ("Benjamín Aguilar Lee vs. Génesis Krystal Altamirano de la Cruz Especial Familiar Radicación de Exh. 1 Acdo. Núm. Exp. 884/2019.", Some("Génesis Krystal Altamirano de la Cruz"), None),
    ("Rivera Ruíz Rosa Iliana vs. Franco Justo Andrés Exh. Familiar Exh. Admisión 1 Acdo. Núm. Exp. 490/2019.", Some("Franco Justo Andrés"), None),
    ("Zapata Tenorio Víctor vs. Carlos Tenorio Contreras su Suc. Ord. Civil Se Admite Apel. 1 Acdo. Núm. Exp. 215/2018.", Some("Carlos Tenorio Contreras su Suc."), None),
    ("Sofom Er vs. Hernández Lorenzo Ejecutivo Cuantía Menor Mercantil Computo, Desahogo de Vista-Admisión de Prueb. 2 Acdos. Núm. Exp. 921/2016.", Some("Hernández Lorenzo"), None),
    ("Zamudio Badillo Mauro vs. Arellanos Devoe Antonio Ejecutivo Cuantía Menor Mercantil Admisión de Inc. 1 Acdo. Núm. Exp. 800/2018.", Some("Arellanos Devoe Antonio"), None),
    ("López Machorro Mónica Beatriz vs. Del Olmo Mejía Moisés Especial Familiar Admisión 1 Acdo. Núm. Exp. 1110/2019 no Publ. del 2019- 06- 25.", Some("Del Olmo Mejía Moisés"), Some("admision")),
    ("Lupercio Arceo Elvira su Sucesión vs. Rosalba Ceja Lupersio Ord. Civil Desechamiento 1 Acdo. Núm. Exp. 272/2019 1bis. no Publ. del 2019- 05- 03. Marroquín Tinajero Jorge Arturo vs. Graciela Calderón Fuentes de Guerra y Mario Tinajero López. Ord. Civil Regularización 1 Acdo. Núm. Exp. 1066/2018 no Publ. del 2019- 05- 06.", Some("Ordinario Civil"), None),
];
const DESCRIPCIONES: &[(&str, Option<&str>, Option<&str>)] = &[
    ("Madrigal Robles Silvia y Robles Mendoza Víctor Manuel. Ord. Civil Prevención. 1 Acdo. Núm. Exp. 396/2026.", None, Some("Prevención")),
    ("Almeyda Larios Juan su sucesión. Sucesorio Familiar Desahoga Vista Mp. 1 Acdo. Núm. Exp. 340/2026 Principal.", None, Some("Desahoga Vista Mp")),
    ("Ana vs. Juan. Especial Familiar Admisión Apel. Efecto Devoluctivo Tramitación Inmediata. 2 Acdos. Núm. Exp. 1232/2026.", Some("Juan"), Some("Admisión Apel. Efecto Devoluctivo Tramitación Inmediata")),
    ("Gómez vs. Ávila Hernández Luis Rey. Cnpcyf - Especial de Arrendamiento Oral Núm. Exp. 6739/2026, Desechamiento. 1 Acdo.", Some("Ávila Hernández Luis Rey"), Some("Desechamiento")),
    ("Banco vs. Ovadia Arón Jacobo. Especial Hipotecario Civil 1 Acdo. Núm. Exp. 1607/2024.", Some("Ovadia Arón Jacobo"), None),
    ("Parra Gómez Regina. Ejecutivo Mercantil Nhl. 1 Acdo. Núm. Exp. 507/2024.", None, None),
    ("A vs. B. Ord. Civ. T. Ap 1841/2025/003, 1 Acdo.", Some("B"), None),
    ("A vs. B. Oral Mercantil no Publ. del 21 de Septiembre del 2026. 1 Acdo. Núm. Exp. 326/2026.", Some("B"), None),
    ("A vs. B. Ord. Civil P- 71. 1 Acdo. Núm. Exp. 1119/2024.", Some("B"), None),
    ("A vs. Nieto J. Especial Familiar por Revocadas Autorizaciones. 1 Acdo. Núm. Exp. 1232/2026.", Some("Nieto J."), Some("Revocadas Autorizaciones")),
    ("HSBC vs. Montes Acevedo Gerardo. Cnpcyf - Especial Hipotecario Oral Núm. Exp. 573/2025, Acdo. Oficio Rpp. 1 Acdo.", Some("Montes Acevedo Gerardo"), Some("Oficio Rpp")),
];
const EXPEDIENTES: &[(&str, &str, Option<&str>)] = &[
    ("1002/2017 Tomo II", "1002/2017", Some("Tomo II")),
    ("153/2024 Inc", "153/2024", Some("Inc")),
    ("39974/2026, Emplazar", "39974/2026", None),
    ("6403/2025, ### Debera Estar a Lo Ordenado", "6403/2025", None),
    ("1841/2025/003", "1841/2025/003", None),
    ("84-2020-001", "84/2020/001", None),
    ("744/2023 Principal Tomo Dos.", "744/2023", Some("Principal Tomo Dos")),
    ("Sin número", "Sin número", None),
];
const PARTES: &[(&str, &str)] = &[
    ("Guzmán García Lorena. Aud. 272-B no Publ. no Publ. del Día de su Fecha", "Guzmán García Lorena"),
    ("Pérez Vargas Saúl y Hernández Villegas Brenda Estefania. Especial Familiar Señalamiento Nueva Fecha", "Pérez Vargas Saúl y Hernández Villegas Brenda Estefania"),
    ("Pérez Espino Mildred. Ejecutivo Mercantil", "Pérez Espino Mildred"),
    ("Ecoineo S.A. de C.V. Ejecutivo Mercantil", "Ecoineo S.A. de C.V."),
    ("Casa Marzam, S.A. de C.V. Oral Mercantil Fecha de", "Casa Marzam, S.A. de C.V."),
    ("BBVA México, S.A., Institución de Banca Múltiple, Grupo Financiero BBVA México", "BBVA México, S.A., Institución de Banca Múltiple, Grupo Financiero BBVA México"),
    ("Instituto del Fondo Nacional de la Vivienda para los Trabajadores. Delegación Regional", "Instituto del Fondo Nacional de la Vivienda para los Trabajadores. Delegación Regional"),
];
const MARCAS: &[(&str, &str)] = &[
    ("NO PUBLICADO González Ayala Néstor Raúl", "González Ayala Néstor Raúl"),
    ("MAL PUBLICADO. Pérez Juan", "Pérez Juan"),
    ("Notaría 5", "Notaría 5"),
    ("del 05 de Junio del 2025 Núñez Sandoval Erendira", "Núñez Sandoval Erendira"),
    ("DEL 2022 Studio 28", "Studio 28"),
    ("Viernes 12 de julio del 2019 Inmobiliaria Horacio S.A", "Inmobiliaria Horacio S.A"),
    ("SENTENCIAS DEF. MAL PUBLICADAS Cruz Reyes Josefina Isabel", "Cruz Reyes Josefina Isabel"),
    ("Sentencia Definitivas del 06 de Mayo del 2024 Herrera Jerez Ana Laura", "Herrera Jerez Ana Laura"),
    ("SENTENCIAA DEFINITIVAA DEL 17 DE AGOSTO DEL 2022 Adj Products Group México", "Adj Products Group México"),
    ("SENTENCIA Granados Morales José Francisco", "Granados Morales José Francisco"),
    ("Audiencia Americas Resources S.A. de C.V.", "Americas Resources S.A. de C.V."),
    ("PUBLICADOS Toyota Financial Services México S.A. de C.V.", "Toyota Financial Services México S.A. de C.V."),
    ("Pon 3, 1 Sent. Monroy Serrano Gerardo", "Monroy Serrano Gerardo"),
    ("Pon 1 Novarec S.A. de C.V.", "Novarec S.A. de C.V."),
    ("Providencias Precautorias. Banco del Bajío Sociedad Anónima", "Banco del Bajío Sociedad Anónima"),
    ("Del Valle Pérez Juan", "Del Valle Pérez Juan"),
    ("Del Río Ana", "Del Río Ana"),
    ("Sentencia", "Sentencia"),
    ("Pon 3 no Publ. del 21 Ene 2025 Banco Santander México, S.A.", "Banco Santander México, S.A."),
    ("SENTENCIA INTERLOCUTORIS DEL 1 DE JULIO DEL 2019 Maresi Electronics, S.A.", "Maresi Electronics, S.A."),
];
const EMPRESAS: &[(&str, &[&str], bool)] = &[
    ("Olivo Martínez Jaime antes HSBC México, S.A.", &["HSBC México, S.A."], false),
    ("Gutiérrez Olvera José Miguel Amparo Indirecto 885/2016 Promov. por Hipotecaria Nacional S.A.", &["Hipotecaria Nacional S.A."], false),
    ("Banco Santander (México), S.A., Institución de Banca Múltiple, Grupo Financiero Santander México", &["Banco Santander (México), S.A., Institución de Banca Múltiple, Grupo Financiero Santander México"], false),
    ("BBVA Bancomer, Sociedad Anónima, Institución de Banca Múltiple, Grupo Financiero BBVA Bancomer", &["BBVA Bancomer, Sociedad Anónima, Institución de Banca Múltiple, Grupo Financiero BBVA Bancomer"], false),
    ("Constructora Ejemplo S.A. de C.V. y Pérez Gómez Ana", &["Constructora Ejemplo S.A. de C.V."], true),
    ("Pérez Gómez Ana en su carácter de apoderada de Inmobiliaria Norte S.A. de C.V.", &["Inmobiliaria Norte S.A. de C.V."], false),
    ("Inmobiliaria Norte S.A. de C.V. Representada por López Ruiz Pedro", &["Inmobiliaria Norte S.A. de C.V."], false),
    ("Club Universidad Nacional A.C.", &["Club Universidad Nacional A.C"], false),
    ("Soto A.C.", &[], true),
    ("Secreto", &[], true),
    ("Mercova S.A. de C.V. y Otros", &["Mercova S.A. de C.V."], false),
    ("Amextra Sofinco S.A. de C.V. S. F. C", &["Amextra Sofinco S.A. de C.V."], false),
    ("Red contra Incendio Joperz S.A. de C.V", &["Red contra Incendio Joperz S.A. de C.V"], false),
    ("Centro contra la Discriminación A.C", &["Centro contra la Discriminación A.C"], false),
    ("Concesionaria Mexiquense S.A. de C.V", &["Concesionaria Mexiquense S.A. de C.V"], false),
    ("Tomo Books Mx S de R.L. de C.V", &["Tomo Books Mx S de R.L. de C.V"], false),
    ("Ixe Banco, S.A., Institución de Banca Múltiple", &["Ixe Banco, S.A., Institución de Banca Múltiple"], false),
    ("Corporativo Laudex, S.A. P. I. de C.V., Sofom, E. N. R", &["Corporativo Laudex, S.A. P. I. de C.V., Sofom, E. N. R"], false),
    ("Santander Consumo Banco Santander México Sa", &["Banco Santander México Sa"], false),
    ("Banco Santander Serfín Banco Santander Serfín S.A. Institución de Banca Múltiple Grupo Financiero Santander Serfín", &["Banco Santander Serfín S.A. Institución de Banca Múltiple Grupo Financiero Santander Serfín"], false),
    ("Banco Banco Santander (México), Sociedad Anónima, Institución de Banca Múltiple", &["Banco Santander (México), Sociedad Anónima, Institución de Banca Múltiple"], false),
    ("Banco Bch Banco de Cedulas Hipotecarias Sa", &["Banco de Cedulas Hipotecarias Sa"], false),
    ("Tomo Ii Banco Santander México S.A. Institución de Banca Múltiple Grupo Financiero Santander México", &["Banco Santander México S.A. Institución de Banca Múltiple Grupo Financiero Santander México"], false),
    ("Segundo Tomo HSBC México, S.A., Institución de Banca Múltiple, Grupo Financiero HSBC", &["HSBC México, S.A., Institución de Banca Múltiple, Grupo Financiero HSBC"], false),
    ("Banco Actinver Sociedad Anónima Institución de Banca Múltiple Grupo Financiero Actinver como Fiduciario del Fideicomiso Irrevocable Número 1401", &["Banco Actinver Sociedad Anónima Institución de Banca Múltiple Grupo Financiero Actinver"], false),
    ("Robles Vega Luis Alberto como Cesionario de Consultoría Empresarial, S de R. L. de C.V. como Cedente", &["Consultoría Empresarial, S de R. L. de C.V."], true),
    ("Méndez Ortiz Laura Patricia como Cesionaria Santander Hipotecario, S.A. de C.V. Sociedad Financiera de Objeto Múltiple Entidad No Regulada", &["Santander Hipotecario, S.A. de C.V. Sociedad Financiera de Objeto Múltiple Entidad No Regulada"], true),
    ("Navarro Cruz María del Rosario su Cesionario Sunbelt Servicios Legales, S. C", &["Sunbelt Servicios Legales, S. C"], true),
    ("Integrante del Grupo Financiero Banamex, su Cesionario Csck 12 Mexco I, S. de R. L. de C.V.", &["Csck 12 Mexco I, S. de R. L. de C.V."], true),
    ("Cesionaria Banco Mercantil del Norte S.A. Institución de Banca Múltiple Grupo Financiero Banorte", &["Banco Mercantil del Norte S.A. Institución de Banca Múltiple Grupo Financiero Banorte"], false),
    ("Fuentes Lara Andrea Causahabiente de Banco Invex S.A. Institución de Banca Múltiple", &["Banco Invex S.A. Institución de Banca Múltiple"], true),
    ("Banco Mercantil del Norte como Causahabiente de Banco Interacciones Sa Institución de Banca Múltiple Grupo Financiero Interacciones", &["Banco Interacciones Sa Institución de Banca Múltiple Grupo Financiero Interacciones"], true),
    ("Mandatario Banco Nacional de México, S.A", &["Banco Nacional de México, S.A"], false),
    ("Banco de México Atraves de su Mandatario Banco Nacional de México, S.A.", &["Banco Nacional de México, S.A."], true),
    ("Ace Fianzas Monterrey S.A. como Fusionante de Chubb de México Compañía Afianzadora S.A. de C.V", &["Ace Fianzas Monterrey S.A.", "Chubb de México Compañía Afianzadora S.A. de C.V"], false),
    ("Torres Gil Rodrigo Anteriormente Inmobic Capital S.A. P I de C.V", &["Inmobic Capital S.A. P I de C.V"], true),
    ("Anteriormente Unifin Financiera Sociedad Anónima", &["Unifin Financiera Sociedad Anónima"], false),
    ("Banco Santander México S.A. (Institución de Banca Múltiple Grupo Financiero Santander México)", &["Banco Santander México S.A. (Institución de Banca Múltiple Grupo Financiero Santander México"], false),
    ("Cibanco, S.A., Institución de Banca Múltiple, en su Carácter de Fiduciaria en el Fideicomiso Irrevocable F/00385 (Anteriormente The Bank Of New York Mellon S.A. Institución de Banca Múltiple)", &["Cibanco, S.A., Institución de Banca Múltiple", "The Bank Of New York Mellon S.A. Institución de Banca Múltiple"], false),
    ("Banco Santander México Sa Anteriomente Banco Mexicano Somex Sa", &["Banco Santander México Sa", "Banco Mexicano Somex Sa"], false),
    ("Banco del Atlántico S N C y/o Banco del Atlántico S.A", &["Banco del Atlántico S N C", "Banco del Atlántico S.A"], false),
    ("Salas Ríos Jorge por Derecho Propio y como Socios Accionistas de Servicios de Autotransporte Urbano Ruta 86 S.A. de C.V", &["Servicios de Autotransporte Urbano Ruta 86 S.A. de C.V"], true),
    ("Seguros Banorte S.A. de C.V. Grupo Financiero Banorte, Exp. 548/2022, Acumulado Con el Juic. Promov. por Lara Díaz Mónica contra Seguros Banorte S.A. de C.V., Grupo Financiero Banorte", &["Seguros Banorte S.A. de C.V. Grupo Financiero Banorte", "Seguros Banorte S.A. de C.V., Grupo Financiero Banorte"], true),
    ("Ruiz Vega Ana y Mora Paz Luis Menor Hija G. S. S.A. en Representación de su Hija", &[], true),
    ("Méndez Ortiz Laura como Subrogatario de Banca Serfín, S.A. Institución de Banca Múltiple, Grupo Financiero Serfín", &["Banca Serfín, S.A. Institución de Banca Múltiple, Grupo Financiero Serfín"], true),
    ("Banco Santander México Sa Quien También Se Ostenta como Santander Consumo S.A. de C.V. Sociedad Financiera de Objeto Múltiple Entidad Regulada", &["Banco Santander México Sa", "Santander Consumo S.A. de C.V. Sociedad Financiera de Objeto Múltiple Entidad Regulada"], false),
    ("Persona Moral BBVA Bancomer Sociedad Anónima Institución de Banca Múltiple Grupo Financiero BBVA Bancomer", &["BBVA Bancomer Sociedad Anónima Institución de Banca Múltiple Grupo Financiero BBVA Bancomer"], false),
    ("Babco Mercantil del Norte S.A. Institución de Banca Múltiple Grupo Financiero Banorte Cesionario", &["Babco Mercantil del Norte S.A. Institución de Banca Múltiple Grupo Financiero Banorte"], false),
    ("Notario Público Nuero 221 de la Ciudad de México Licenciado Pedro Gómez Rey Santander Hipotecario S.A. de C.V.", &[], true),
    ("Robles Vega Luis Alberto, BBVA Bancomer, S.A. Institución de Banca Múltiple, Grupo Financiero BBVA Bancomer", &["BBVA Bancomer, S.A. Institución de Banca Múltiple, Grupo Financiero BBVA Bancomer"], true),
    ("Olmos Duarte Rafael Antonio, Ficrea, S.A. de C.V", &["Ficrea, S.A. de C.V"], true),
    ("Jesús de la Peña Gómez y la Latinoamericana Seguros S.A", &["Latinoamericana Seguros S.A"], true),
    ("Méndez Ríos Laura y por el Bienestar Social Cedros Asociación Civil", &["Bienestar Social Cedros Asociación Civil"], true),
    ("Torres Gil Ana Patricia, Sociedad Hipotecaria Federal S.N.C. Institución de Banca de Desarrollo", &["Sociedad Hipotecaria Federal S.N.C. Institución de Banca de Desarrollo"], true),
    ("Campos Rey Ana Lucía Secretario del Consejo de Administración de Banco del Bajío, Sociedad Anónima", &["Banco del Bajío, Sociedad Anónima"], false),
    ("Vega Ruiz Sergio Socio Accionista de la Moral Yomoga S.A. de C.V", &["Yomoga S.A. de C.V"], false),
    ("Jiménez Aguirre Abogados, S. C", &["Jiménez Aguirre Abogados, S. C"], false),
    ("Mejía Salazar & Sáenz Abogados S. C", &["Mejía Salazar & Sáenz Abogados S. C"], false),
    ("Inmobiliaria García Espinosa S.A. de C.V.", &["Inmobiliaria García Espinosa S.A. de C.V."], false),
    ("Sólida Administradora de Portafolios, S.A. de C.V.", &["Sólida Administradora de Portafolios, S.A. de C.V."], false),
    ("Maja Administradora de Edificios S.C", &["Maja Administradora de Edificios S.C"], false),
    ("Administradora General de Sociedades S.A. de C.V", &["Administradora General de Sociedades S.A. de C.V"], false),
    ("Vega Ruiz Sergio en su Carácter de Presidente del Consejo de Administración de la Sociedad Denominada Grupo de Desarrollos Fasse, S.A. de C.V", &["Grupo de Desarrollos Fasse, S.A. de C.V"], false),
    ("Mercader Financial, S.A. de Sofom E. R", &["Mercader Financial, S.A. de Sofom"], false),
    ("Integradora Estatal de Productores, de Chih, S.A. de C.V", &["Integradora Estatal de Productores, de Chih, S.A. de C.V"], false),
    ("Lara Soto Juan Carlos y Productos Químicos y de Servicios, S.A. de C.V", &["Productos Químicos y de Servicios, S.A. de C.V"], true),
    ("Autobuses de Oriente, A. D. O., S.A. de C.V. y Ricardo Gómez Rojas", &["Autobuses de Oriente, A. D. O., S.A. de C.V"], true),
    ("Rivas Paredes Ricardo Anteshsbc México Sociedad Anónima Institución de Banca Múltiple", &[], true),
    ("Morales Pinto José AntonioSinergia Consultores de Negocios, S. C", &[], true),
    ("Figueroa Vilchis Elvira Ycustompiec, S.A. de C.V", &[], true),
    ("Guzmán García Roberto y 2ar Desarrollos Inmobiliarios Sociedad Anónima de Capital Variable", &[], true),
    ("Grupo Ser 1 y 2 S.A. de C.V.", &["Grupo Ser 1 y 2 S.A. de C.V."], false),
    ("Inmobiliaria Lm y 3p S.A. de C.V. y Ruiz Soto Florencio", &["Inmobiliaria Lm y 3p S.A. de C.V."], true),
    ("Lara Soto Sergio y el Responsable Médico del Hospital Ángeles Quien Opera Bajo la Razon Social Operadora de Hospitales Ángeles S.A. de C.V.", &["Operadora de Hospitales Ángeles S.A. de C.V."], true),
    ("Lara Rivas Fiona Ntes Santander Hipotecario Sociedad Anónima de Capital Variable", &["Santander Hipotecario Sociedad Anónima de Capital Variable"], true),
    ("Rodríguez Díaz Mitzy (Antes)Santander Hipotecario S.A. de C.V. Sociedad Financiera de Objeto Múltiple Entidad Regulada", &["Santander Hipotecario S.A. de C.V. Sociedad Financiera de Objeto Múltiple Entidad Regulada"], true),
    ("Industrias Vinícolas Pedro Domecq, S.A. de C.V", &["Industrias Vinícolas Pedro Domecq, S.A. de C.V"], false),
    ("Centro Pedagógico María Montessori de Ecatepec S.C", &["Centro Pedagógico María Montessori de Ecatepec S.C"], false),
    ("Transportes Juan Pablo II S.A. de C.V", &["Transportes Juan Pablo II S.A. de C.V"], false),
    ("Administraciones José Luis Ordóñez S C", &["Administraciones José Luis Ordóñez S C"], false),
    ("Sanatorio Santa Teresa Zacatecas, S.A. de C.V", &["Sanatorio Santa Teresa Zacatecas, S.A. de C.V"], false),
    ("1) Campos Rey Ernesto Abel, 2) Apjusto, S.A. P. I. de C.V., Sofom, Enr 3) Notario Público Número 17 Licenciado Pedro Gómez Rey", &["Apjusto, S.A. P. I. de C.V., Sofom, Enr"], true),
    ("Ortega, Notario Público Número 99 del Estado de México, HSBC México S.A. Institución de Banca Múltiple Grupo Financiero HSBC)", &["HSBC México S.A. Institución de Banca Múltiple Grupo Financiero HSBC"], true),
    ("Rosas Peña Olivia, Hospital Médica Sur, S.A. B. de C.V. y Hospital San Ángel Inn (Operadora Coyoacan de Centros de Salud, S.A. de C.V. )", &["Hospital Médica Sur, S.A. B. de C.V", "Operadora Coyoacan de Centros de Salud, S.A. de C.V"], true),
];
const PERSONAS: &[(&str, &[&str])] = &[
    ("García Pérez Juan", &["García Pérez Juan"]),
    ("Chávez Trejo Demetrio su Sucesión", &["Chávez Trejo Demetrio"]),
    ("Juárez del Valle Antonio y de la Rosa Nieto Susana", &["Juárez del Valle Antonio", "de la Rosa Nieto Susana"]),
    ("Constructora Ejemplo S.A. de C.V. y Pérez Gómez Ana", &["Pérez Gómez Ana"]),
    ("Robles Vega Luis Alberto como Cesionario de Consultoría Empresarial, S de R. L. de C.V.", &["Robles Vega Luis Alberto"]),
    ("Torres Gil Rodrigo Anteriormente Inmobic Capital S.A. P I de C.V", &["Torres Gil Rodrigo"]),
    ("1) Campos Rey Ernesto Abel, 2) Apjusto, S.A. P. I. de C.V.", &["Campos Rey Ernesto Abel"]),
    ("Secreto", &[]),
    ("Fuerza Aérea", &[]),
    ("Fondo de Operación", &[]),
    ("Azul Concretos", &[]),
    ("Fundación Antonio Haghenbeck", &[]),
    ("José Luis", &[]),
    ("María del Carmen", &[]),
    ("Petróleos Mexicanos", &[]),
    ("Lakowsky Ivanova Vera", &["Lakowsky Ivanova Vera"]),
    ("de la Lama Antonio", &["de la Lama Antonio"]),
    ("Robles Vega Luis Alberto, BBVA Bancomer, S.A. Institución de Banca Múltiple", &["Robles Vega Luis Alberto"]),
    ("Jesús de la Peña Gómez y la Latinoamericana Seguros S.A", &["Jesús de la Peña Gómez"]),
];

#[test]
fn tipo_acuerdo() {
    for (raw, dem, esperado) in TIPO_ACUERDO {
        assert_eq!(acuerdos::tipo_acuerdo(raw, *dem), *esperado, "{raw}");
    }
}

#[test]
fn descripcion() {
    for (raw, dem, esperado) in DESCRIPCIONES {
        assert_eq!(acuerdos::descripcion(raw, *dem).as_deref(), *esperado, "{raw}");
    }
}

#[test]
fn separar_expediente() {
    for (exp, numero, cuaderno) in EXPEDIENTES {
        let (n, c) = acuerdos::separar_expediente(exp);
        assert_eq!((n.as_str(), c.as_deref()), (*numero, *cuaderno), "{exp}");
    }
}

#[test]
fn separar_parte() {
    for (nombre, esperado) in PARTES {
        assert_eq!(acuerdos::separar_parte(nombre).0, *esperado, "{nombre}");
    }
}

#[test]
fn quitar_marca_publicacion() {
    for (nombre, esperado) in MARCAS {
        assert_eq!(acuerdos::quitar_marca_publicacion(nombre), *esperado, "{nombre}");
    }
}

#[test]
fn empresas() {
    for (campo, esperadas, otras) in EMPRESAS {
        let (lista, hay_otras) = partes::partes(campo);
        let obtenidas: Vec<String> = lista.into_iter().map(|(_, n)| n).collect();
        let esperadas: Vec<String> = esperadas.iter().map(|s| s.to_string()).collect();
        assert_eq!((sin_punto(&obtenidas), hay_otras), (sin_punto(&esperadas), *otras), "{campo}");
    }
}

#[test]
fn personas() {
    for (campo, esperadas) in PERSONAS {
        let obtenidas: Vec<String> = partes::personas(campo).into_iter().map(|(_, n)| n).collect();
        assert_eq!(obtenidas, esperadas.iter().map(|s| s.to_string()).collect::<Vec<_>>(), "{campo}");
    }
}
