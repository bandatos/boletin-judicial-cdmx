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
abdiel abelina abigail abimael abisai abraham abril adair adalberta adali adareli adelaida adelfa
adelina adelita adilene adolfo adoracion adrian adriana agapita aglae agripina agustin agusto
ahtziri aide aidee ailed aime aimee aislinn aketzali alberta albertina alberto albina aldahir aldair
aldegunda aleida alejandra alejandrina alejandro alejanro aleli alessandra alessandro alethia alexa
alexander alexandra alexei alexia alexis alfonsa alfonsina alfonso alfredo alheli alicia alida alim
alin aline alinne alitzel alma alondra altagracia alvaro amadeo amairani amairany amalia amando
amaranta amaury amayrani amed amelia ameyali ana anabel anabell anahi anahid anahy anai anaid anais
anakaren anali analleli anallely analy anay anayansi anayeli anayelli anayely ancelmo andrea andrei
andreina andres anel anette angel angelica angelina angelo anilu annel anotnio anselma antonia
antonieta antonina antonio antono apolonia aquileo aquilina aquilino araceli aracelly aracely aramis
arantza aranzazu arath arceli arcelia areceli areli arely aremi argelia argenis ariadna ariadne
ariana arianna aridai ariel ariosto arisbet arisbeth arizbeth arlen arlene arlet arleth arlett
arlette armando arminda arsenio artemia artemisa arturo asael astrid asucena asuncion atocha atzimba
atzin atziri atziry audelia audrey augusto aurelia aurelio aurora austreberta austreberto
auxiliadora avelina axel ayde aydee ayerim aylin azael azalea azeneth azucena baruch basilisa
baudelio beatriz belem belen benito benjamin berenice bernardita berta betariz betsabe betzabe
betzabel betzabet betzabeth bibiana bladimir blanca braulia brenda brigitte briseida camerina camila
candelaria caren caridad carina caritina carlo carlos carlota carmelina carmen carmina carola
carolina casandra casimira cassandra catalina cecilia celene celerina celeste celestina celina
cenobia cesar chantal cirenia citlali citlalli citlally citlaly clarisa clarissa claudia clemencia
cliserio clotilde columba concepcion consepcion constantina consuelo corina cornelia crisanta
cristel cristell cristian cristina cristobal cupertino dalila damaris danae danahe daniel daniela
daniella daphne daria darinka dario david dayana dayanara dayanira dejanira delcarmen delfina delia
demetria deni denice denise deniss denisse dennis dennise dennys denys desire desiree deyanira diana
diego dinora dinorah dionisia dolores dominga dominique domitila donaciano donaji donaldo edelmira
edgar edilia edit edith eduardo eduviges eduwiges eduwigis efigenia efrain elaine elena eleonor
eleonora eleuteria elfego eliazar eliceo elide elideth elidia eliel eliezer elihu elisa elisabeth
eliseo eliu eliud eliuth elizaberth elizabet elizabeth elizbeth elizeth elodia eloina eloisa elpidia
elpidio elsa elvira emanuel emelia emilia emiliana emilio emmanuel emmanuelle emperatriz enedina
enedino eneyda engracia enid enoc enrique enriqueta epifania erandi erendira erica erick erika
ernestina ernesto esmeralda esmirna esperanza esteban estefana estefani estefania estefanie estefany
estela estephani estephania estephanie estephany ester esthela esther estuardo etelvina eucario
eufemio eufracia eufrocina eufrosina eugenia eugenio eulalia eulises eulogia eunice euridice eusebia
eustolia eustolio eustorgio evangelina evelia evelina evelyn evencio evodia evodio ezequiel fabiana
fabiola fabricio faustina fausto favian faviola febe federico feliciana felicitas felipa felipe
felisa fernado fernanda fernando fidel fidela filiberta filiberto florentina floriberta fortunata
francelia francisca francisco frine froylan gabina gabriel gabriela gael galdina gamaliel garciela
gardenia gaudalupe gaudencia genoveva genovevo georgette georgina geovana geovani geovanni geovanny
geovany geraldine gerarda gerardo gertrudis getsemani getzemani giancarlo gibran gicela gilberta
gilberto giovana giovanna giovanni giovanny giovany gisel gisela gisele gisell giselle gissel
gisselle gloria godofredo gonzala gonzalo graciana graciela gregoria gregorio gricel gricelda grisel
griselda grisell grissel guadalupe guadlaupe guadlupe gualberto gudalupe gudelia guillermina
guillermo gustavo haide haidee harumi hayde haydee hazael hazel hector helena heleodoro heriberta
heriberto herlinda hermelinda hermenegilda hermila hermilia herminia higinia hilaria hilario
hildeberto hipolita honoria hortencia hortensia hugo humberto ibet ibeth icela idali idalia idalid
ignacia ignacio ilda ileana ilian iliana imanol imelda ines inez inocencia iracema irais iraiz iram
iran irasema irazema irazu irene ireri iridian irma isaac isabel isabela isadora isael isai isaid
isamar isarel isaura isay isela isidora isidra ismael isolina israel issac itandehui itzayana itze
itzel ivan ivana ivet iveth ivett ivette ivon ivone ivonn ivonne ixchel ixel izamar izchel jaciel
jacinta jackeline jacoba jacobo jacquelin jacqueline jacquelinne jael jafet jahaziel jahel jahir
jaime jair janet janete janeth janett janette janine jannet jannete janneth jannette jaquelin
jaqueline jared jaret jasmin jatziri javier jazmin jeanett jeanette jeannette jenifer jeniffer
jennyfer jeronima jessica jesus jetzabel jezabel jhoana jhonathan jhovana jhovanny jhovany jimena
joana joanna joaquin joaquina jocabed jocelin joceline jocelyn jocelyne johana johanna johnatan
jonathan jorge josafat josafath jose josefa josefina joselin joseline joselyn joshua josimar
josselin josseline josselyn josue jovan jovana jovani jovanna jovanni jovanny jovany jovita joyce
juan judit judith julian juliana julieta julio julisa julissa julita juventina kalid karely karen
karim karime karina karla kassandra keren korina kristel larissa lariza laura laurencia leilani
leobarda leocadia leonardo leonila leonor leopoldo leticia lidia liduvina lilia lilian liliana
lilibeth lillian lily lisbet lisbeth liset liseth lisette lisset lissete lisseth lissette livier
lizbet lizbeth lizet lizete lizeth lizett lizette lizzet lizzete lizzeth lizzette lorely lorena
lorenzo lourdes lucero lucia lucila lucrecia ludivina luis luisa mabel macarena maclovia madai
magadalena magali magalli magally magaly magdalena magdiel magnolia maleni manuel manuela marcela
marco marcos marely margarita margot maria mariana mariano maribel maricarmen maricruz mariel
mariela marilu mario marisa marisela marisol marlem marlen marlene marlenne marta martha martin
martina marycarmen marycruz marysol massiel matilde mauricio maximiliano mayanin mayela mayeli mayte
maythe medardo melania melany melida melina melisa melissa merari mercedes micaela michele michell
michelle midori migdalia miguel miguelina milagros milena minerva mirel mirella mirelle mireya
mirian miroslava misael mishel missael mizael mizraim moises moncerrat monica monserrat monserrath
monsserrat montserrat montserrath monzerrat nahiely nahomi nain nalleli nallely nanci nancy naomi
napoleon narcisa natali natalia natalie nataly natanael nathali nathalia nathalie nathaly nathanael
natividad nayeli nayelli nayelly nayely nazaret nazareth nazaria neftali neli nemorio nereida
nereyda nicolas nicolasa nicole nicte noelia noemi nohemi norma nury obdulia obed octavio odet odeth
odette odilia odin ofelia olivia olmo omar oralia orlando orquidea ortencia osbelia oscar osiel
osiris osvelia otilia oyuki oziel pablo paloma pamela paola pascual patricia patricio patrocinio
paula paulette paulina pedro penelope petrona petronila pilar placida polet pompeyo porfiria
porfirio prisca priscila prisciliana priscilla procoro pueblito quetzalli radames rafael rafaela
ramiro ramon ramses raquel raul raymunda raziel rebeca refugio regina remedios reynalda reynaldo
ricarda ricardo rigoberto rivelino roberta roberto rocio rodrigo rogelio rolando romina romualda
rosa rosalia rosalva rosario roselia rosio ruben rubicelia ruby rufina ruth sabina sabrina sacnite
sadot sagrario sahid sajid salvador samanta samantha samara samira samuel sandra santiago sara
sarahi sarahy sarai saraid saray saul sayuri scarlett sebastian sebastiana selena selene senen
senorina serafina sergio severa shantal sherlyn silvana silvestre silvia silvina simon sinai sinue
sinuhe sirenia sixta socorro sofia soledad soraya stefania stefanie stefany stephani stephania
stephanie stephany steve sugey sujey suriel susana talia tamara tatiana taurino teodora teodoro
teresa thalia tiburcio tomas tonathiu tonatiuh tonatzin tulio ulices ulises uriel usiel uziel uzziel
valentin valentina valeria valery vanesa vanessa vannesa verenice veronica vianey vianney vicenta
vicente victor victorina vinicio violeta virgina virginia viridiana viviana vladimir wendolin
wendoline wendolyn wendolyne xavier ximena xiomara xochilt xochiquetzal xochitl yadira yael yahaira
yahel yahir yair yamel yamil yamile yamilet yamileth yaneli yanelli yanelly yanely yanet yaneth
yanett yanin yanina yanira yannet yaquelin yaqueline yared yareli yarely yareni yaret yareth yasmin
yatziri yazmin yeli yeni yenifer yenny yeraldin yered yesenia yesica yessenia yisel yoali yocelin
yocelyn yolanda yolotzin yoloxochitl yoselin yoseline yoselyn yosselin yovana yovani ysabel yudith
yuliana yulissa yunuen yuriana yuridia yuridiana yuriko yuritzi yvette yvonne zaid zeferina zeltzin
zenaida zoe zoraida zuleica zuleima zulema zuleyma zuriel zurisadai
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
