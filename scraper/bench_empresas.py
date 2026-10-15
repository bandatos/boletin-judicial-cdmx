"""
bench_empresas.py — Benchmark de las regex de empresas.py y personas.py.

Dos partes:

1. Casos etiquetados (CASOS): campo actora/demandada tal como viene del
   boletín -> empresas esperadas (nombre visible) y si quedan otras partes.
   Incluyen los errores ya corregidos, para que un ajuste nuevo no rompa uno
   viejo. Los nombres de personas son inventados, con la misma forma que los
   reales.

2. Comparación contra otra versión de las regex (por defecto la del último
   commit) sobre una muestra fija de la base: cuántos campos cambian, y
   cuántas empresas tienen restos sospechosos (conectores, listas, nombres
   repetidos) en cada versión. Los cambios se listan para revisarlos a mano.

Uso:
    python3 scraper/bench_empresas.py                       # solo casos
    python3 scraper/bench_empresas.py --db boletin.sqlite   # + comparación con HEAD
    python3 scraper/bench_empresas.py --db X --ref HEAD~1 --mostrar 80
"""

import argparse
import importlib.util
import re
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

AQUI = Path(__file__).parent
sys.path.insert(0, str(AQUI))
import empresas as emp  # noqa: E402
import personas as pers  # noqa: E402

# (campo, empresas esperadas, hay otras partes)
CASOS = [
    # ── Ya corregidos antes (no romper) ──
    ("Olivo Martínez Jaime antes HSBC México, S.A.", ["HSBC México, S.A."], False),
    ("Gutiérrez Olvera José Miguel Amparo Indirecto 885/2016 Promov. por Hipotecaria Nacional S.A.",
     ["Hipotecaria Nacional S.A."], False),
    ("Banco Santander (México), S.A., Institución de Banca Múltiple, Grupo Financiero Santander México",
     ["Banco Santander (México), S.A., Institución de Banca Múltiple, Grupo Financiero Santander México"], False),
    ("BBVA Bancomer, Sociedad Anónima, Institución de Banca Múltiple, Grupo Financiero BBVA Bancomer",
     ["BBVA Bancomer, Sociedad Anónima, Institución de Banca Múltiple, Grupo Financiero BBVA Bancomer"], False),
    ("Constructora Ejemplo S.A. de C.V. y Pérez Gómez Ana", ["Constructora Ejemplo S.A. de C.V."], True),
    ("Pérez Gómez Ana en su carácter de apoderada de Inmobiliaria Norte S.A. de C.V.",
     ["Inmobiliaria Norte S.A. de C.V."], False),
    ("Inmobiliaria Norte S.A. de C.V. Representada por López Ruiz Pedro", ["Inmobiliaria Norte S.A. de C.V."], False),
    ("Club Universidad Nacional A.C.", ["Club Universidad Nacional A.C"], False),
    ("Soto A.C.", [], True),  # "A.C." con menos de dos palabras antes: pueden ser iniciales
    ("Secreto", [], True),
    ("Mercova S.A. de C.V. y Otros", ["Mercova S.A. de C.V."], False),
    ("Amextra Sofinco S.A. de C.V. S. F. C", ["Amextra Sofinco S.A. de C.V."], False),
    ("Red contra Incendio Joperz S.A. de C.V", ["Red contra Incendio Joperz S.A. de C.V"], False),
    ("Centro contra la Discriminación A.C", ["Centro contra la Discriminación A.C"], False),
    ("Concesionaria Mexiquense S.A. de C.V", ["Concesionaria Mexiquense S.A. de C.V"], False),
    ("Tomo Books Mx S de R.L. de C.V", ["Tomo Books Mx S de R.L. de C.V"], False),
    ("Ixe Banco, S.A., Institución de Banca Múltiple", ["Ixe Banco, S.A., Institución de Banca Múltiple"], False),
    ("Corporativo Laudex, S.A. P. I. de C.V., Sofom, E. N. R",
     ["Corporativo Laudex, S.A. P. I. de C.V., Sofom, E. N. R"], False),

    # ── Dos nombres pegados sin separador ──
    ("Santander Consumo Banco Santander México Sa", ["Banco Santander México Sa"], False),
    ("Banco Santander Serfín Banco Santander Serfín S.A. Institución de Banca Múltiple Grupo Financiero Santander Serfín",
     ["Banco Santander Serfín S.A. Institución de Banca Múltiple Grupo Financiero Santander Serfín"], False),
    ("Banco Banco Santander (México), Sociedad Anónima, Institución de Banca Múltiple",
     ["Banco Santander (México), Sociedad Anónima, Institución de Banca Múltiple"], False),
    ("Banco Bch Banco de Cedulas Hipotecarias Sa", ["Banco de Cedulas Hipotecarias Sa"], False),
    ("Tomo Ii Banco Santander México S.A. Institución de Banca Múltiple Grupo Financiero Santander México",
     ["Banco Santander México S.A. Institución de Banca Múltiple Grupo Financiero Santander México"], False),
    ("Segundo Tomo HSBC México, S.A., Institución de Banca Múltiple, Grupo Financiero HSBC",
     ["HSBC México, S.A., Institución de Banca Múltiple, Grupo Financiero HSBC"], False),

    # ── Colas que se comían una palabra de más ──
    ("Banco Actinver Sociedad Anónima Institución de Banca Múltiple Grupo Financiero Actinver como Fiduciario del Fideicomiso Irrevocable Número 1401",
     ["Banco Actinver Sociedad Anónima Institución de Banca Múltiple Grupo Financiero Actinver"], False),

    # ── Conectores de rol entre una parte y la empresa ──
    ("Robles Vega Luis Alberto como Cesionario de Consultoría Empresarial, S de R. L. de C.V. como Cedente",
     ["Consultoría Empresarial, S de R. L. de C.V."], True),
    ("Méndez Ortiz Laura Patricia como Cesionaria Santander Hipotecario, S.A. de C.V. Sociedad Financiera de Objeto Múltiple Entidad No Regulada",
     ["Santander Hipotecario, S.A. de C.V. Sociedad Financiera de Objeto Múltiple Entidad No Regulada"], True),
    ("Navarro Cruz María del Rosario su Cesionario Sunbelt Servicios Legales, S. C",
     ["Sunbelt Servicios Legales, S. C"], True),
    ("Integrante del Grupo Financiero Banamex, su Cesionario Csck 12 Mexco I, S. de R. L. de C.V.",
     ["Csck 12 Mexco I, S. de R. L. de C.V."], True),
    ("Cesionaria Banco Mercantil del Norte S.A. Institución de Banca Múltiple Grupo Financiero Banorte",
     ["Banco Mercantil del Norte S.A. Institución de Banca Múltiple Grupo Financiero Banorte"], False),
    ("Fuentes Lara Andrea Causahabiente de Banco Invex S.A. Institución de Banca Múltiple",
     ["Banco Invex S.A. Institución de Banca Múltiple"], True),
    ("Banco Mercantil del Norte como Causahabiente de Banco Interacciones Sa Institución de Banca Múltiple Grupo Financiero Interacciones",
     ["Banco Interacciones Sa Institución de Banca Múltiple Grupo Financiero Interacciones"], True),
    ("Mandatario Banco Nacional de México, S.A", ["Banco Nacional de México, S.A"], False),
    ("Banco de México Atraves de su Mandatario Banco Nacional de México, S.A.",
     ["Banco Nacional de México, S.A."], True),
    ("Ace Fianzas Monterrey S.A. como Fusionante de Chubb de México Compañía Afianzadora S.A. de C.V",
     ["Ace Fianzas Monterrey S.A.", "Chubb de México Compañía Afianzadora S.A. de C.V"], False),
    ("Torres Gil Rodrigo Anteriormente Inmobic Capital S.A. P I de C.V", ["Inmobic Capital S.A. P I de C.V"], True),
    ("Anteriormente Unifin Financiera Sociedad Anónima", ["Unifin Financiera Sociedad Anónima"], False),
    ("Banco Santander México S.A. (Institución de Banca Múltiple Grupo Financiero Santander México)",
     ["Banco Santander México S.A. (Institución de Banca Múltiple Grupo Financiero Santander México"], False),
    ("Cibanco, S.A., Institución de Banca Múltiple, en su Carácter de Fiduciaria en el Fideicomiso Irrevocable F/00385 (Anteriormente The Bank Of New York Mellon S.A. Institución de Banca Múltiple)",
     ["Cibanco, S.A., Institución de Banca Múltiple", "The Bank Of New York Mellon S.A. Institución de Banca Múltiple"], False),
    ("Banco Santander México Sa Anteriomente Banco Mexicano Somex Sa",
     ["Banco Santander México Sa", "Banco Mexicano Somex Sa"], False),
    ("Banco del Atlántico S N C y/o Banco del Atlántico S.A",
     ["Banco del Atlántico S N C", "Banco del Atlántico S.A"], False),
    ("Salas Ríos Jorge por Derecho Propio y como Socios Accionistas de Servicios de Autotransporte Urbano Ruta 86 S.A. de C.V",
     ["Servicios de Autotransporte Urbano Ruta 86 S.A. de C.V"], True),
    ("Seguros Banorte S.A. de C.V. Grupo Financiero Banorte, Exp. 548/2022, Acumulado Con el Juic. Promov. por Lara Díaz Mónica contra Seguros Banorte S.A. de C.V., Grupo Financiero Banorte",
     ["Seguros Banorte S.A. de C.V. Grupo Financiero Banorte", "Seguros Banorte S.A. de C.V., Grupo Financiero Banorte"], True),

    ("Ruiz Vega Ana y Mora Paz Luis Menor Hija G. S. S.A. en Representación de su Hija", [], True),
    ("Méndez Ortiz Laura como Subrogatario de Banca Serfín, S.A. Institución de Banca Múltiple, Grupo Financiero Serfín",
     ["Banca Serfín, S.A. Institución de Banca Múltiple, Grupo Financiero Serfín"], True),
    ("Banco Santander México Sa Quien También Se Ostenta como Santander Consumo S.A. de C.V. Sociedad Financiera de Objeto Múltiple Entidad Regulada",
     ["Banco Santander México Sa", "Santander Consumo S.A. de C.V. Sociedad Financiera de Objeto Múltiple Entidad Regulada"], False),
    ("Persona Moral BBVA Bancomer Sociedad Anónima Institución de Banca Múltiple Grupo Financiero BBVA Bancomer",
     ["BBVA Bancomer Sociedad Anónima Institución de Banca Múltiple Grupo Financiero BBVA Bancomer"], False),
    ("Babco Mercantil del Norte S.A. Institución de Banca Múltiple Grupo Financiero Banorte Cesionario",
     ["Babco Mercantil del Norte S.A. Institución de Banca Múltiple Grupo Financiero Banorte"], False),
    ("Notario Público Nuero 221 de la Ciudad de México Licenciado Pedro Gómez Rey Santander Hipotecario S.A. de C.V.",
     [], True),

    # ── Persona delante de la empresa, con coma o "y la" ──
    ("Robles Vega Luis Alberto, BBVA Bancomer, S.A. Institución de Banca Múltiple, Grupo Financiero BBVA Bancomer",
     ["BBVA Bancomer, S.A. Institución de Banca Múltiple, Grupo Financiero BBVA Bancomer"], True),
    ("Olmos Duarte Rafael Antonio, Ficrea, S.A. de C.V", ["Ficrea, S.A. de C.V"], True),
    ("Jesús de la Peña Gómez y la Latinoamericana Seguros S.A", ["Latinoamericana Seguros S.A"], True),
    ("Méndez Ríos Laura y por el Bienestar Social Cedros Asociación Civil", ["Bienestar Social Cedros Asociación Civil"], True),
    ("Torres Gil Ana Patricia, Sociedad Hipotecaria Federal S.N.C. Institución de Banca de Desarrollo",
     ["Sociedad Hipotecaria Federal S.N.C. Institución de Banca de Desarrollo"], True),
    ("Campos Rey Ana Lucía Secretario del Consejo de Administración de Banco del Bajío, Sociedad Anónima",
     ["Banco del Bajío, Sociedad Anónima"], False),
    ("Vega Ruiz Sergio Socio Accionista de la Moral Yomoga S.A. de C.V", ["Yomoga S.A. de C.V"], False),
    # No cortar: despachos con nombre de personas
    ("Jiménez Aguirre Abogados, S. C", ["Jiménez Aguirre Abogados, S. C"], False),
    ("Mejía Salazar & Sáenz Abogados S. C", ["Mejía Salazar & Sáenz Abogados S. C"], False),
    ("Inmobiliaria García Espinosa S.A. de C.V.", ["Inmobiliaria García Espinosa S.A. de C.V."], False),
    ("Sólida Administradora de Portafolios, S.A. de C.V.", ["Sólida Administradora de Portafolios, S.A. de C.V."], False),
    ("Maja Administradora de Edificios S.C", ["Maja Administradora de Edificios S.C"], False),
    ("Administradora General de Sociedades S.A. de C.V", ["Administradora General de Sociedades S.A. de C.V"], False),
    ("Vega Ruiz Sergio en su Carácter de Presidente del Consejo de Administración de la Sociedad Denominada Grupo de Desarrollos Fasse, S.A. de C.V",
     ["Grupo de Desarrollos Fasse, S.A. de C.V"], False),
    ("Mercader Financial, S.A. de Sofom E. R", ["Mercader Financial, S.A. de Sofom"], False),
    ("Integradora Estatal de Productores, de Chih, S.A. de C.V", ["Integradora Estatal de Productores, de Chih, S.A. de C.V"], False),
    ("Lara Soto Juan Carlos y Productos Químicos y de Servicios, S.A. de C.V",
     ["Productos Químicos y de Servicios, S.A. de C.V"], True),
    ("Autobuses de Oriente, A. D. O., S.A. de C.V. y Ricardo Gómez Rojas", ["Autobuses de Oriente, A. D. O., S.A. de C.V"], True),

    # ── Personas pegadas por erratas: red de seguridad ──
    ("Rivas Paredes Ricardo Anteshsbc México Sociedad Anónima Institución de Banca Múltiple", [], True),
    ("Morales Pinto José AntonioSinergia Consultores de Negocios, S. C", [], True),
    ("Figueroa Vilchis Elvira Ycustompiec, S.A. de C.V", [], True),
    ("Guzmán García Roberto y 2ar Desarrollos Inmobiliarios Sociedad Anónima de Capital Variable", [], True),
    ("Grupo Ser 1 y 2 S.A. de C.V.", ["Grupo Ser 1 y 2 S.A. de C.V."], False),
    ("Inmobiliaria Lm y 3p S.A. de C.V. y Ruiz Soto Florencio", ["Inmobiliaria Lm y 3p S.A. de C.V."], True),
    ("Lara Soto Sergio y el Responsable Médico del Hospital Ángeles Quien Opera Bajo la Razon Social Operadora de Hospitales Ángeles S.A. de C.V.",
     ["Operadora de Hospitales Ángeles S.A. de C.V."], True),
    ("Lara Rivas Fiona Ntes Santander Hipotecario Sociedad Anónima de Capital Variable",
     ["Santander Hipotecario Sociedad Anónima de Capital Variable"], True),
    ("Rodríguez Díaz Mitzy (Antes)Santander Hipotecario S.A. de C.V. Sociedad Financiera de Objeto Múltiple Entidad Regulada",
     ["Santander Hipotecario S.A. de C.V. Sociedad Financiera de Objeto Múltiple Entidad Regulada"], True),
    # No perder: nombres de persona dentro de un nombre de negocio
    ("Industrias Vinícolas Pedro Domecq, S.A. de C.V", ["Industrias Vinícolas Pedro Domecq, S.A. de C.V"], False),
    ("Centro Pedagógico María Montessori de Ecatepec S.C", ["Centro Pedagógico María Montessori de Ecatepec S.C"], False),
    ("Transportes Juan Pablo II S.A. de C.V", ["Transportes Juan Pablo II S.A. de C.V"], False),
    ("Administraciones José Luis Ordóñez S C", ["Administraciones José Luis Ordóñez S C"], False),
    ("Sanatorio Santa Teresa Zacatecas, S.A. de C.V", ["Sanatorio Santa Teresa Zacatecas, S.A. de C.V"], False),

    # ── Listas numeradas y notarios ──
    ("1) Campos Rey Ernesto Abel, 2) Apjusto, S.A. P. I. de C.V., Sofom, Enr 3) Notario Público Número 17 Licenciado Pedro Gómez Rey",
     ["Apjusto, S.A. P. I. de C.V., Sofom, Enr"], True),
    ("Ortega, Notario Público Número 99 del Estado de México, HSBC México S.A. Institución de Banca Múltiple Grupo Financiero HSBC)",
     ["HSBC México S.A. Institución de Banca Múltiple Grupo Financiero HSBC"], True),

    # ── Paréntesis sin cerrar: nombre comercial (razón social) ──
    ("Rosas Peña Olivia, Hospital Médica Sur, S.A. B. de C.V. y Hospital San Ángel Inn (Operadora Coyoacan de Centros de Salud, S.A. de C.V. )",
     ["Hospital Médica Sur, S.A. B. de C.V", "Operadora Coyoacan de Centros de Salud, S.A. de C.V"], True),
]

# Limitaciones conocidas: se informan, no hacen fallar el benchmark.
CONOCIDOS = [
    # "y" + mayúscula separa partes, también dentro de un nombre.
    ("Sky Grupo Asesor, Sociedad Civil y Sistemas y Desarrollos Rack, Sociedad Anónima de Capital Variable",
     ["Sky Grupo Asesor, Sociedad Civil", "Sistemas y Desarrollos Rack, Sociedad Anónima de Capital Variable"], False),
]

CONOCIDOS += [
    ("Ortiz Ortiz y Asociados, S.C.", ["Ortiz Ortiz y Asociados, S.C."], False),
    ("Banco Nacional de Obras y Servicios Públicos S N C por Propio Derecho y Otros",
     ["Banco Nacional de Obras y Servicios Públicos S N C"], False),
]

# Personas: campo -> nombres esperados (personas.personas)
CASOS_PERSONAS = [
    ("García Pérez Juan", ["García Pérez Juan"]),
    ("Chávez Trejo Demetrio su Sucesión", ["Chávez Trejo Demetrio"]),
    ("Juárez del Valle Antonio y de la Rosa Nieto Susana", ["Juárez del Valle Antonio", "de la Rosa Nieto Susana"]),
    ("Constructora Ejemplo S.A. de C.V. y Pérez Gómez Ana", ["Pérez Gómez Ana"]),
    ("Robles Vega Luis Alberto como Cesionario de Consultoría Empresarial, S de R. L. de C.V.", ["Robles Vega Luis Alberto"]),
    ("Torres Gil Rodrigo Anteriormente Inmobic Capital S.A. P I de C.V", ["Torres Gil Rodrigo"]),
    ("1) Campos Rey Ernesto Abel, 2) Apjusto, S.A. P. I. de C.V.", ["Campos Rey Ernesto Abel"]),
    ("Secreto", []),
    ("Fuerza Aérea", []),
    ("Robles Vega Luis Alberto, BBVA Bancomer, S.A. Institución de Banca Múltiple", ["Robles Vega Luis Alberto"]),
    ("Jesús de la Peña Gómez y la Latinoamericana Seguros S.A", ["Jesús de la Peña Gómez"]),
]

# Restos que no deberían quedar en el nombre visible de una empresa.
SOSPECHOSOS = {
    "conector de rol": re.compile(r"\b(?:cesionari[oa]s?|causahabientes?|mandatari[oa]s?|endosatari[oa]s?|fusionante)\b", re.I),
    "como / y/o": re.compile(r"\bcomo\b|\by/o\b", re.I),
    "anteriormente": re.compile(r"\banteri?o?r?mente\b", re.I),
    "lista numerada": re.compile(r"(?:^|\s)\d{1,2}\)"),
    "tomo": re.compile(r"^(?:segundo\s+|primer\s+)?tomo\s+[ivxl\d]+\b", re.I),
    "notario": re.compile(r"\bnotari[oa]\s+p[uú]blic", re.I),
    "contra (no incendio)": re.compile(r"\scontra\s+(?!(?:incendios?|fuegos?|el|la|los|las|todo)\b)", re.I),
    "banco repetido": re.compile(r"^(?=.*\S\s+Banco\s+\w).*\bBanco\b"),
    "paréntesis sin cerrar": re.compile(r"\([^)]*$"),
}


def _sin_punto(nombres):
    # El punto final de la forma ("S.A.", "C.V.") se saca siempre.
    return [n.rstrip(".") for n in nombres]


def correr_casos():
    fallas = 0
    for campo, esperadas, otras in CASOS:
        lista, hay_otras = emp.partes(campo)
        obtenidas = [n for _, n in lista]
        if _sin_punto(obtenidas) != _sin_punto(esperadas) or hay_otras != otras:
            fallas += 1
            print(f"FALLA empresas: {campo!r}\n   esperado {esperadas} otras={otras}\n   obtenido {obtenidas} otras={hay_otras}")
    for campo, esperadas, otras in CONOCIDOS:
        lista, hay_otras = emp.partes(campo)
        if _sin_punto([n for _, n in lista]) != _sin_punto(esperadas) or hay_otras != otras:
            print(f"(conocido) {campo[:90]!r} -> {[n for _, n in lista]}")
    for campo, esperadas in CASOS_PERSONAS:
        obtenidas = [n for _, n in pers.personas(campo)]
        if obtenidas != esperadas:
            fallas += 1
            print(f"FALLA personas: {campo!r}\n   esperado {esperadas}\n   obtenido {obtenidas}")
    total = len(CASOS) + len(CASOS_PERSONAS)
    print(f"Casos: {total - fallas}/{total} OK")
    return fallas


def cargar_ref(ref):
    """empresas.py de otra versión de git, como módulo aparte."""
    codigo = subprocess.run(["git", "show", f"{ref}:scraper/empresas.py"], cwd=AQUI,
                            capture_output=True, text=True, check=True).stdout
    tmp = Path(tempfile.mkdtemp()) / "empresas_ref.py"
    tmp.write_text(codigo)
    spec = importlib.util.spec_from_file_location("empresas_ref", tmp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def persona_delante(nombre):
    """El nombre visible empieza con algo que parece una persona: las dos
    primeras palabras con forma de nombre, un nombre de pila entre las
    cuatro primeras y al menos dos palabras más antes de la forma."""
    m = emp.RE_FORMA_FUERTE.search(nombre) or emp.RE_FORMA_CORTA_FINAL_POS.search(nombre)
    antes = (nombre[:m.start()] if m else nombre).split()
    palabras = [w for w in antes if w.lower() not in pers.PARTICULAS]
    if len(palabras) < 4:
        return False
    norm = [pers.normalizar(w) for w in palabras]
    return (all(pers.RE_PALABRA_NOMBRE.match(w) for w in palabras[:2])
            and not set(norm[:4]) & pers.NO_PERSONA
            and bool(set(norm[:4]) & pers.NOMBRES_PILA))


def sospechas(nombres):
    c = Counter()
    for n in nombres:
        for k, rx in SOSPECHOSOS.items():
            if rx.search(n):
                c[k] += 1
        if persona_delante(n):
            c["persona delante"] += 1
    return c


def comparar(db, ref, cada, mostrar):
    viejo = cargar_ref(ref)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    # Muestra fija y repartida en todo el histórico: un aviso de cada `cada`.
    campos = [c for fila in conn.execute(f"SELECT actora, demandada FROM entradas WHERE id % {cada} = 0")
              for c in fila if c]
    print(f"\nMuestra: {len(campos):,} campos (1 de cada {cada} avisos) de {db}")
    antes_nombres, despues_nombres, cambios = [], [], []
    for campo in campos:
        a = [n for _, n in viejo.partes(campo)[0]]
        d = [n for _, n in emp.partes(campo)[0]]
        antes_nombres += a
        despues_nombres += d
        if a != d:
            cambios.append((campo, a, d))
    print(f"Empresas detectadas: {len(antes_nombres):,} ({ref}) -> {len(despues_nombres):,} (actual)")
    print(f"Campos con resultado distinto: {len(cambios):,}")
    sa, sd = sospechas(antes_nombres), sospechas(despues_nombres)
    print(f"\n{'resto sospechoso':24} {ref:>8} {'actual':>8}")
    for k in [*SOSPECHOSOS, "persona delante"]:
        print(f"{k:24} {sa[k]:8} {sd[k]:8}")
    perdidas = [x for x in cambios if len(x[2]) < len(x[1])]
    print(f"\nCampos que pierden alguna empresa: {len(perdidas)}")
    for campo, a, d in perdidas[:mostrar]:
        print(f"  - {campo[:160]}\n      {a}\n   -> {d}")
    print(f"\nOtros cambios (primeros {mostrar}):")
    for campo, a, d in [x for x in cambios if x not in perdidas][:mostrar]:
        print(f"  - {campo[:160]}\n      {a}\n   -> {d}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", help="Base para comparar contra otra versión de las regex")
    ap.add_argument("--ref", default="HEAD", help="Versión de git de referencia (default HEAD)")
    ap.add_argument("--cada", type=int, default=500, help="Tomar 1 de cada N avisos (default 500)")
    ap.add_argument("--mostrar", type=int, default=40)
    args = ap.parse_args()
    fallas = correr_casos()
    if args.db:
        comparar(args.db, args.ref, args.cada, args.mostrar)
    sys.exit(1 if fallas else 0)


if __name__ == "__main__":
    main()
