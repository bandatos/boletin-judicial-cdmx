"""
personas.py — Detecta las personas físicas que son parte en un aviso del
boletín y normaliza su nombre.

Lo usa build_db.py (tabla personas para la sección /personas del server).

Regla de producto: /personas y /personas/:slug llevan noindex y no entran a
ningún sitemap. Nombran personas en juicios (muchos familiares): el boletín
es público, pero que un buscador las indexe hace que buscar a alguien
devuelva sus juicios.

Es lo complementario de empresas.py: de cada campo actora/demandada, los
trozos que no son empresa y que tienen forma de nombre propio ("García
Pérez Juan", "Chávez Trejo Demetrio su Sucesión"). Se prefiere perder un
nombre antes que listar como persona a un juzgado, una dependencia o un
resto del tipo de juicio.

No hay CURP en el boletín: el "núcleo" (nombre sin acentos ni puntuación,
en el orden publicado) es la única llave. Dos personas con el mismo nombre
quedan juntas, y una misma persona publicada en otro orden ("Juan García
Pérez") queda separada.
"""

import re
import unicodedata

import empresas as emp

# Restos que el parser deja pegados al nombre: "1 Acdo." al inicio, el tipo
# de juicio después de un punto ("López René. Proced"), "su Sucesión".
RE_ACDO_INICIAL = re.compile(r"^[\s,.;:-]*(?:Pon\s+\d+\s*,\s*)?(?:\d+\s+(?:Acdos?|Sents?|Aud)\.?\s*)?", re.IGNORECASE)
# "Juárez del Valle Antonio y de la Rosa Nieto Susana": empresas.py solo
# corta antes de mayúscula, acá también antes de una partícula de apellido.
RE_SEPARADOR_PARTICULA = re.compile(r"\s+y\s+(?=(?:(?:de|del|la|las|los)\s+)+[A-ZÁÉÍÓÚÑ])")
RE_Y_FINAL = re.compile(r"\s+(?:y|e)$")
RE_SUCESION = re.compile(r"\s+(?:su\s+)?suc(?:esi[oó]n|\.)?\s*$", re.IGNORECASE)
RE_SUCESION_PREVIA = re.compile(r"^(?:la\s+)?sucesi[oó]n\s+(?:testamentaria\s+|intestamentaria\s+)?(?:a\s+bienes\s+)?de\s+",
                                re.IGNORECASE)
RE_FIN_NOMBRE = re.compile(
    r"\.(?:\s|$)|\s+-\s+|\(|,|\s+y/o\s+"
    r"|\s+(?:y\s+)?(?:por\s+(?:su\s+)?(?:propio|s[ií]\b)|en\s+(?:su\s+)?representaci|quien\s)",
    re.IGNORECASE,
)

PALABRA = r"[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ'´]+"
# Partículas de apellido o de nombre compuesto ("de la Rosa", "del Valle").
PARTICULAS = {"de", "del", "la", "las", "los", "y", "e", "san", "van", "von", "di", "da", "mc"}
RE_PALABRA_NOMBRE = re.compile(rf"^(?:{PALABRA}(?:-{PALABRA})?|[A-ZÁÉÍÓÚÑ]\.?)$")

# Si el nombre trae alguna de estas palabras no es una persona: es un
# órgano, una dependencia, una institución sin forma societaria o un resto
# del aviso. Normalizadas (minúsculas, sin acentos).
NO_PERSONA = set("""
secreto secretaria secretario gobierno policia bancaria industrial instituto institucion universidad
banco bancomer banamex santander scotiabank hsbc inbursa banorte afirme
comision procuraduria delegacion alcaldia ayuntamiento consejo tribunal juzgado juez ministerio
fideicomiso fiduciario fiduciaria infonavit fovissste imss issste sindicato condominio condominos
asociacion sociedad colegio escuela hospital clinica club comite grupo empresa compania cia
servicios inmobiliaria constructora desarrollos desarrolladora comercializadora promotora
operadora corporativo corporacion administradora arrendadora financiera hipotecaria
federal nacional mexico mexicana mexicano distrito ciudad estado estados republica municipio
cnpcyf juicio juic oral ejecutivo mercantil especial civil familiar proced procedimiento
providencias precautorias amparo exhorto otros otras quien resulte responsable responsables
sucesion sucesorio intestamentario testamentario bienes herederos albacea interventor
direccion general registro publico propiedad notario notaria agente fiscalia
apoderado representante legal parte actora demandada tercero tercera llamado llamada
ordinario hipotecario arrendamiento divorcio incausado alimentos controversia jurisdiccion
voluntaria diligencias medios preparatorios vs contra promueve promovido acdo acdos
tomo cuaderno principal incidente expediente recurso apelacion toca sala
company projects international holdings capital group solutions systems investments partners
trust inc llc ltd corp
fuerza aerea armada ejercito fuente pago existe aplica no sin estaciones cambio guarda pension alimenticia
actuando unica diligenciar inventarios pagadas pagados irrevocable administracion excepciones
procesales patrimonio beneficencia publica publico emprendimientos fabrica hilados cuotas
convenio pagare pagares cheque cheques credito creditos adeudo pesos moneda renta rentas
garantia accion acciones cumplimiento rescision nulidad terminacion contrato prescripcion
usucapion reivindicatoria plenaria posesion entrega desocupacion vencimiento otorgamiento
firma escritura perdida patria potestad custodia convivencia regimen visitas reconocimiento
paternidad rectificacion acta adopcion tutela interdiccion ausencia presuncion muerte
segunda primera tercera cuarta quinta seccion audiencia sentencia definitiva interlocutoria
autos auto vista termino plazo dias fecha lugar domicilio conocido desconocido ignorado
ingresos maquinaria fraccionamiento colonia puentes
abogados asociados consultores constructores arquitectos ingenieros contadores hermanos hijos
sucesores socios despacho bufete editores autos seguros inmobiliarios
centro sanatorio consorcio corredor pasaje producciones promociones industrias transportes
autotransportes administraciones sociales electronica camionero
""".split())


# Nombres de pila frecuentes (normalizados). Salen de la tabla personas: las
# palabras que más aparecen al final del nombre ("García Pérez Juan") y casi
# nunca al principio ni en empresas, más los nombres comunes que también
# aparecen primero ("Juan García Pérez"). Los usa empresas.py para decidir si
# lo que va delante de una empresa ("Fulano, BBVA Bancomer, S.A.") es una
# persona: no es un nombre de persona en particular, es un diccionario.
NOMBRES_PILA = set("""
abigail abraham abril adelaida adelina adolfo adrian adriana agripina agustin aide aidee alberta
alberto aldair alejandra alejandrina alejandro alexander alexandra alexis alfonso alfredo alicia
aline alma alondra altagracia alvaro amairani amalia amelia ana anabel anahi anai anaid anayeli
andrea andres anel angel angelica angelina antonia antonieta antonio araceli aracely arcelia areli
arely argelia ariadna ariana ariel arlette armando arturo astrid asuncion augusto aurelia aurelio
aurora avelina axel azael azucena beatriz belem belen benito benjamin berenice betsabe betzabe
bibiana blanca brenda camila candelaria caridad carina carlo carlos carlota carmen carmina carolina
catalina cecilia celeste celina cesar citlali citlalli claudia clemencia clotilde columba concepcion
consuelo corina cristian cristina cristobal dalila damaris danae daniel daniela dario david dayana
delfina delia denise denisse deyanira diana diego dinorah dolores dominga domitila donaji edelmira
edgar edith eduardo efrain elena elisa eliseo eliud elizabeth elodia eloisa elpidio elsa elvira
emanuel emelia emilia emilio emmanuel enedina engracia enrique enriqueta epifania erendira erica
erick erika ernestina ernesto esmeralda esperanza esteban estefani estefania estefany estela esthela
esther eugenia eugenio eulalia eunice eustolia evangelina evelia evelyn ezequiel fabiola fabricio
fausto federico felicitas felipa felipe felisa fernanda fernando fidel filiberto florentina
francisca francisco froylan gabina gabriel gabriela gamaliel genoveva georgina geovanni geraldine
gerardo gibran gilberto giovanna giovanni gisela giselle gloria gonzalo graciela gregoria gregorio
grisel griselda guadalupe gudelia guillermina guillermo gustavo haydee hector heriberto herlinda
hermelinda hermila herminia hilaria hilario hortencia hortensia hugo humberto ibeth idalia ignacia
ignacio ileana iliana imelda ines inocencia irais irasema irene irma isaac isabel isai isaura isela
isidra ismael israel itzel ivan ivet iveth ivette ivon ivone ivonne ixchel jacobo jacqueline jael
jaime jair janet janeth janette jaquelin jaqueline javier jazmin jessica jesus jimena joana joaquin
joaquina jocelyn johana jonathan jorge josafat jose josefa josefina joselin joselyn josue jovita
juan judith julian juliana julieta julio karen karina karla laura leonardo leonila leonor leopoldo
leticia lidia lilia lilian liliana lisbeth lisset lizbeth lizet lizeth lorena lorenzo lourdes lucero
lucia lucila lucrecia luis luisa mabel magali magaly magdalena manuel manuela marcela marco marcos
margarita maria mariana maribel maricarmen maricruz mariel mariela marilu mario marisa marisela
marisol marlen marlene marta martha martin martina matilde mauricio maximiliano mayela mayte melina
melissa micaela michelle miguel milagros minerva mireya miroslava misael moises monica monserrat
montserrat nalleli nallely nancy natali natalia nataly natividad nayeli nayely neftali nicolas
nicolasa noemi nohemi norma obdulia obed octavio odette ofelia olivia omar oralia orlando oscar
osiris otilia pablo paloma pamela paola pascual patricia patricio paula paulina pedro penelope pilar
porfirio priscila rafael rafaela ramiro ramon raquel raul rebeca refugio regina remedios reynalda
reynaldo ricarda ricardo rigoberto roberta roberto rocio rodrigo rogelio rolando rosa rosalia
rosalva rosario ruben rufina ruth sabina sagrario salvador samanta samantha samuel sandra santiago
sara sarahi sarai saul sebastian selene sergio silvestre silvia simon socorro sofia soledad
stephanie susana tamara tatiana teodora teodoro teresa thalia tiburcio tomas ulises uriel valentin
valentina valeria vanesa vanessa veronica vianey vicente victor viridiana viviana vladimir xavier
ximena xochitl yadira yael yair yanet yaneth yareli yasmin yazmin yesenia yesica yolanda yoselin
yuliana yuridia zenaida zulema
""".split())

def normalizar(nombre):
    s = unicodedata.normalize("NFD", str(nombre or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    s = re.sub(r"[^a-z0-9ñ ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def limpiar(trozo):
    """Saca del trozo lo que no es el nombre. Devuelve "" si no queda nada."""
    t = emp.RE_Y_OTROS.sub("", trozo.strip())
    t = RE_ACDO_INICIAL.sub("", t)
    m = RE_FIN_NOMBRE.search(t)
    if m:
        t = t[:m.start()]
    t = RE_SUCESION_PREVIA.sub("", t.strip())
    t = RE_SUCESION.sub("", t)
    return RE_Y_FINAL.sub("", t.strip(" ,.;:-"))


def es_nombre(t):
    palabras = t.split()
    if not 2 <= len(palabras) <= 8:
        return False
    propias = 0
    for p in palabras:
        if p.lower() in PARTICULAS:
            continue
        if not RE_PALABRA_NOMBRE.match(p):
            return False
        if len(p.rstrip(".")) > 1:
            propias += 1
    if propias < 2:
        return False
    return not (set(normalizar(t).split()) & NO_PERSONA)


def personas(campo):
    """Personas físicas de un campo actora/demandada: lista de
    (núcleo, nombre tal cual en el boletín, limpio)."""
    if not campo:
        return []
    salida = []
    trozos = (t for parte in emp.trozos(campo) for t in RE_SEPARADOR_PARTICULA.split(parte))
    for trozo in trozos:
        t = emp.limpiar(trozo)
        if not t or emp.es_empresa(t):
            continue
        t = limpiar(t)
        if t and es_nombre(t):
            n = normalizar(t)
            if len(n) >= 8:
                salida.append((n, t))
    return salida


def slugify(texto, largo=80):
    return emp.slugify(texto, largo) if texto else "persona"
