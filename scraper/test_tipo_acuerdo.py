"""Casos reales del boletín 4115 (22/09/2026) para tipo_acuerdo.

    python3 scraper/test_tipo_acuerdo.py
"""
from acuerdos import descripcion, quitar_marca_publicacion, separar_expediente, separar_parte, tipo_acuerdo

CASOS = [
    # (raw_text, demandada, esperado)
    ("Madrigal Robles Silvia y Robles Mendoza Víctor Manuel. Ord. Civil Prevención. 1 Acdo. Núm. Exp. 396/2026.", None, "prevencion"),
    ("Banca Múltiple Grupo Financiero BBVA México Vs. Mandujano Velazco Rubén. Cnpcyf Especial Hipotecario Oral Núm. Exp. 6703/2026, Prevención. 1 Acdo.", "Mandujano Velazco Rubén", "prevencion"),
    ("Pérez vs. Martínez Martínez Pedro Antonio. Controversias de Arrendamiento Civil Se Previene Señale Dom. Dda. 1 Acdo. Núm. Exp. 1496/2012.", "Martínez Martínez Pedro Antonio", "prevencion"),
    ("Martínez Báez Luis Rodrigo su sucesión. Sucesorio Familiar No Desahogo la Prevencion. 1 Acdo. Núm. Exp. 536/2026.", None, "prevencion"),
    ("Quiroz Robles Alejandro vs. Guevara Herrera Leticia. Ord. Civil Desecha. 1 Acdo. Núm. Exp. 396/2026.", "Guevara Herrera Leticia", "negacion"),
    ("Grupo Comercolor S.A. de C.V. y Chavero Hernández Brenda. Ord. Mercantil Desechamiento. 1 Acdo. Núm. Exp. 382/2026.", None, "negacion"),
    ("Landum Servicios Integrales, S.A. de C.V. vs. F. V. Promotora, S.A. de C.V. Ord. Civil No Ha Lugar. 1 Acdo. Núm. Exp. 1273/2008.", "F. V. Promotora, S.A. de C.V.", "negacion"),
    ("A vs. Barroso Barroso Guillermo. Especial Hipotecario Civil No Se Admite Recurso de Queja. 1 Acdo. Núm. Exp. 2479/2024 Tomo Séptimo.", "Barroso Barroso Guillermo", "negacion"),
    ("Gutiérrez Sánchez Myriam del Carmen Vs. Cuevas Téllez Angello Daniel. Cnpcyf - Alimentos Núm. Exp. 10513/2026, Desechamiento. 1 Acdo.", "Cuevas Téllez Angello Daniel", "negacion"),
    ("Castañeda Vargas Anel vs. Mp Agregador S de R.L. de C.V. Ord. Civil Admisorio. 1 Acdo. Núm. Exp. 396/2026.", "Mp Agregador S de R.L. de C.V.", "admision"),
    ("Manzano Fuentevilla María de Los Ángeles su sucesión. Sucesorio Familiar Radicación. 1 Acdo. Núm. Exp. 571/2026.", None, "admision"),
    ("Mejía Velázquez Noemí Guadalupe Vs. Villafaña Leyva Irving. Cnpcyf Guarda y Custodia Núm. Exp. 6675/2026, Admision. 1 Acdo.", "Villafaña Leyva Irving", "admision"),
    # Falsos positivos que no deben marcarse
    ("Gim Compañía Editorial S.A. de C.V. Ord. Civil Se Admiten Pruebs. Supervenientes. 1 Acdo. Núm. Exp. 191/2024 Segundo Tomo.", None, None),
    ("Navarro Felipe. Providencias Precautorias Oral Mercantil Respuesta Cnbv, Negativa de Desbloqueo, Oficio a Banco (Turno). 1 Acdo. Núm. Exp. 1045/2025.", None, None),
    ("Caja de Previsión de la Policía Preventiva de la Ciudad de México Vs. Jiménez Carmona César Alberto. Oral Civil 1 Acdo. Núm. Exp. 1119/2026.", "Jiménez Carmona César Alberto", None),
    ("Desechables del Norte S.A. de C.V. vs. Instituto de Prevención Social. Ord. Civil 2 Acdos. Núm. Exp. 10/2026.", "Instituto de Prevención Social", None),
    ("Rodríguez vs. López. Ord. Civil 1 Acdo. Núm. Exp. 12/2026.", "López", None),
    ("Benjamín Aguilar Lee vs. Génesis Krystal Altamirano de la Cruz Especial Familiar Radicación de Exh. 1 Acdo. Núm. Exp. 884/2019.", "Génesis Krystal Altamirano de la Cruz", None),
    ("Rivera Ruíz Rosa Iliana vs. Franco Justo Andrés Exh. Familiar Exh. Admisión 1 Acdo. Núm. Exp. 490/2019.", "Franco Justo Andrés", None),
    ("Zapata Tenorio Víctor vs. Carlos Tenorio Contreras su Suc. Ord. Civil Se Admite Apel. 1 Acdo. Núm. Exp. 215/2018.", "Carlos Tenorio Contreras su Suc.", None),
    ("Sofom Er vs. Hernández Lorenzo Ejecutivo Cuantía Menor Mercantil Computo, Desahogo de Vista-Admisión de Prueb. 2 Acdos. Núm. Exp. 921/2016.", "Hernández Lorenzo", None),
    ("Zamudio Badillo Mauro vs. Arellanos Devoe Antonio Ejecutivo Cuantía Menor Mercantil Admisión de Inc. 1 Acdo. Núm. Exp. 800/2018.", "Arellanos Devoe Antonio", None),
    ("López Machorro Mónica Beatriz vs. Del Olmo Mejía Moisés Especial Familiar Admisión 1 Acdo. Núm. Exp. 1110/2019 no Publ. del 2019- 06- 25.", "Del Olmo Mejía Moisés", "admision"),
    # Aviso fusionado con el anterior: la negación es del otro expediente.
    ("Lupercio Arceo Elvira su Sucesión vs. Rosalba Ceja Lupersio Ord. Civil Desechamiento 1 Acdo. Núm. Exp. 272/2019 1bis. no Publ. del 2019- 05- 03. Marroquín Tinajero Jorge Arturo vs. Graciela Calderón Fuentes de Guerra y Mario Tinajero López. Ord. Civil Regularización 1 Acdo. Núm. Exp. 1066/2018 no Publ. del 2019- 05- 06.", "Ordinario Civil", None),
]

DESCRIPCIONES = [
    ("Madrigal Robles Silvia y Robles Mendoza Víctor Manuel. Ord. Civil Prevención. 1 Acdo. Núm. Exp. 396/2026.", None, "Prevención"),
    ("Almeyda Larios Juan su sucesión. Sucesorio Familiar Desahoga Vista Mp. 1 Acdo. Núm. Exp. 340/2026 Principal.", None, "Desahoga Vista Mp"),
    ("Ana vs. Juan. Especial Familiar Admisión Apel. Efecto Devoluctivo Tramitación Inmediata. 2 Acdos. Núm. Exp. 1232/2026.", "Juan", "Admisión Apel. Efecto Devoluctivo Tramitación Inmediata"),
    ("Gómez vs. Ávila Hernández Luis Rey. Cnpcyf - Especial de Arrendamiento Oral Núm. Exp. 6739/2026, Desechamiento. 1 Acdo.", "Ávila Hernández Luis Rey", "Desechamiento"),
    ("Banco vs. Ovadia Arón Jacobo. Especial Hipotecario Civil 1 Acdo. Núm. Exp. 1607/2024.", "Ovadia Arón Jacobo", None),
    ("Parra Gómez Regina. Ejecutivo Mercantil Nhl. 1 Acdo. Núm. Exp. 507/2024.", None, None),
    ("A vs. B. Ord. Civ. T. Ap 1841/2025/003, 1 Acdo.", "B", None),
    ("A vs. B. Oral Mercantil no Publ. del 21 de Septiembre del 2026. 1 Acdo. Núm. Exp. 326/2026.", "B", None),
    ("A vs. B. Ord. Civil P- 71. 1 Acdo. Núm. Exp. 1119/2024.", "B", None),
    ("A vs. Nieto J. Especial Familiar por Revocadas Autorizaciones. 1 Acdo. Núm. Exp. 1232/2026.", "Nieto J.", "Revocadas Autorizaciones"),
    ("HSBC vs. Montes Acevedo Gerardo. Cnpcyf - Especial Hipotecario Oral Núm. Exp. 573/2025, Acdo. Oficio Rpp. 1 Acdo.", "Montes Acevedo Gerardo", "Oficio Rpp"),
]

EXPEDIENTES = [
    ("1002/2017 Tomo II", ("1002/2017", "Tomo II")),
    ("153/2024 Inc", ("153/2024", "Inc")),
    ("39974/2026, Emplazar", ("39974/2026", None)),
    ("6403/2025, ### Debera Estar a Lo Ordenado", ("6403/2025", None)),
    ("1841/2025/003", ("1841/2025/003", None)),
    ("84-2020-001", ("84/2020/001", None)),
    ("744/2023 Principal Tomo Dos.", ("744/2023", "Principal Tomo Dos")),
    ("Sin número", ("Sin número", None)),
]

PARTES = [
    ("Guzmán García Lorena. Aud. 272-B no Publ. no Publ. del Día de su Fecha", "Guzmán García Lorena"),
    ("Pérez Vargas Saúl y Hernández Villegas Brenda Estefania. Especial Familiar Señalamiento Nueva Fecha", "Pérez Vargas Saúl y Hernández Villegas Brenda Estefania"),
    ("Pérez Espino Mildred. Ejecutivo Mercantil", "Pérez Espino Mildred"),
    ("Ecoineo S.A. de C.V. Ejecutivo Mercantil", "Ecoineo S.A. de C.V."),
    ("Casa Marzam, S.A. de C.V. Oral Mercantil Fecha de", "Casa Marzam, S.A. de C.V."),
    ("BBVA México, S.A., Institución de Banca Múltiple, Grupo Financiero BBVA México", "BBVA México, S.A., Institución de Banca Múltiple, Grupo Financiero BBVA México"),
    ("Instituto del Fondo Nacional de la Vivienda para los Trabajadores. Delegación Regional", "Instituto del Fondo Nacional de la Vivienda para los Trabajadores. Delegación Regional"),
]

if __name__ == "__main__":
    fallas = 0
    for nombre, esperado in [("NO PUBLICADO González Ayala Néstor Raúl", "González Ayala Néstor Raúl"),
                             ("MAL PUBLICADO. Pérez Juan", "Pérez Juan"), ("Notaría 5", "Notaría 5"),
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
                             ("Del Valle Pérez Juan", "Del Valle Pérez Juan"), ("Del Río Ana", "Del Río Ana"),
                             ("Sentencia", "Sentencia"),
                             ("Pon 3 no Publ. del 21 Ene 2025 Banco Santander México, S.A.", "Banco Santander México, S.A."),
                             ("SENTENCIA INTERLOCUTORIS DEL 1 DE JULIO DEL 2019 Maresi Electronics, S.A.", "Maresi Electronics, S.A.")]:
        if quitar_marca_publicacion(nombre) != esperado:
            fallas += 1
            print(f"FALLA marca: {nombre!r} -> {quitar_marca_publicacion(nombre)!r}")
    for nombre, esperado in PARTES:
        obtenido = separar_parte(nombre)[0]
        if obtenido != esperado:
            fallas += 1
            print(f"FALLA parte: esperado {esperado!r}, obtenido {obtenido!r}")
    for exp, esperado in EXPEDIENTES:
        obtenido = separar_expediente(exp)
        if obtenido != esperado:
            fallas += 1
            print(f"FALLA expediente: esperado {esperado!r}, obtenido {obtenido!r}")
    for raw, dem, esperado in DESCRIPCIONES:
        obtenido = descripcion(raw, dem)
        if obtenido != esperado:
            fallas += 1
            print(f"FALLA descripcion: esperado {esperado!r}, obtenido {obtenido!r}: {raw[:100]}")
    for raw, dem, esperado in CASOS:
        obtenido = tipo_acuerdo(raw, dem)
        if obtenido != esperado:
            fallas += 1
            print(f"FALLA: esperado {esperado}, obtenido {obtenido}: {raw[:110]}")
    total = len(CASOS) + len(DESCRIPCIONES) + len(EXPEDIENTES) + len(PARTES)
    print(f"{total - fallas}/{total} casos bien")
    raise SystemExit(1 if fallas else 0)
