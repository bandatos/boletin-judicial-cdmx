# Notas de parseo — Boletín Judicial PJCDMX

Documentación de las estrategias de extracción y los resultados de las pruebas iniciales.

---

## Fuente

El boletín es un PDF generado en Adobe InDesign, publicado diariamente (días hábiles). Tiene dos columnas de texto por página. El acceso al índice es vía un formulario web con CSRF token; los PDFs están en un CDN externo sin autenticación.

---

## Pipeline de extracción

### 1. Índice de boletines

Se hace un GET a la página principal para obtener el CSRF token y la cookie de sesión. Luego un POST a `/consultaboletinpjcdmx/filtrar` con rango de fechas. La respuesta es HTML con una tabla donde cada fila tiene:

- ID interno del boletín (en el atributo `id` del modal: `boletinexterno{ID}`)
- Fecha
- URL del PDF, embebida en un `<embed src="...gestordocumental...pdf#toolbar=0">`

El patrón para extraer la URL del PDF:
```python
re.search(r"(https://gestordocumental[^#]+\.pdf)", src)
```

Los IDs son enteros pares que incrementan de a 2 por día hábil. Al 10 de junio de 2026 el ID más reciente es 4002. Los primeros registros disponibles (enero 2017) tienen IDs alrededor de 279.

### 2. Descarga de PDFs

Los PDFs se descargan directamente desde `gestordocumental.poderjudicialcdmx.gob.mx` (Cloudflare CDN). No requieren autenticación. Tamaño promedio: ~6.4 MB, ~376 páginas.

Los PDFs tienen cifrado AES-256 con `print:no copy:no`, pero `pdftotext` los extrae sin problema.

### 3. Extracción de texto

Se usa `pdftotext` sin flags adicionales:

```bash
pdftotext archivo.pdf -
```

**Problema encontrado**: el flag `-layout` intenta preservar la disposición de dos columnas y mezcla texto de ambas columnas en la misma línea, lo que rompe el parseo. Sin `-layout`, pdftotext lee el PDF en orden lógico (columna izquierda completa, luego columna derecha) y el texto es limpio y secuencial.

### 4. Estructura del boletín

El texto tiene una jerarquía de cuatro niveles:

```
SECCIÓN PRINCIPAL          (e.g. "JUZGADOS DE LO CIVIL")
  └─ JUZGADO               (e.g. "CUARTO DE LO CIVIL")
       └─ SECRETARÍA        (e.g. "SECRETARÍA \"A\"")
            └─ ACUERDOS DEL [fecha]
                 └─ entrada · entrada · entrada ...
```

Cada entrada corresponde a un caso con acuerdo publicado ese día.

Secciones del boletín (en orden de aparición):

| Sección | Entradas (10-jun-2026) |
|---|---|
| JUZGADOS DE LO CIVIL | 4,443 |
| JUZGADOS DE LO FAMILIAR | 2,843 |
| JUZGADOS CIVILES DE PROCESO ORAL | 2,333 |
| JUZGADOS DE PROCESO ORAL EN MATERIA FAMILIAR | 933 |
| TRIBUNALES EN MATERIA LABORAL | 266 |
| JUZGADOS DE TUTELA DE DERECHOS HUMANOS | 2 |

### 5. Parseo de entradas

#### Formato general

```
[Actora] vs. [Demandada]. [Tipo de juicio] [N] Acdo(s). Núm. Exp. [NNNN/YYYY][sufijo].
```

Ejemplo:
```
Banco Invex S.A. vs. Trejo Sánchez María de Los Ángeles Eugenia.
Especial Hipotecario Civil Acuerdo. 1 Acdo. Núm. Exp. 1437/2024.
```

Las entradas pueden ocupar de 1 a 6 líneas. El terminador siempre es `Núm. Exp.` seguido del número de expediente y un punto final.

#### Terminador de entrada

```python
RE_ENTRY_END = re.compile(
    r"Núm\.\s+Exp\.\s+"
    r"(\d+/\d+"
    r"(?:\s+(?:Segundo|Tercer|Cuarto|Quinto|Sexto|Séptimo|Octavo|Noveno)\s+Tomo"
    r"|\s+Tomo\s+[IVXLC]+"
    r"|\s+(?:Legajo|Amparo|Expedientillo)"
    r")?)"
    r"\s*\.",
    re.IGNORECASE,
)
```

Sufijos observados después del número de expediente:
- Tomos ordinales: `Segundo Tomo`, `Tercer Tomo`, `Séptimo Tomo`
- Tomos romanos: `Tomo II`, `Tomo III`, `Tomo VI`
- Otros: `Legajo`, `Amparo`, `Expedientillo`

#### Separación actora / demandada

```python
RE_VS = re.compile(r"\s+vs\.\s+", re.IGNORECASE)
partes = RE_VS.split(texto_pre_expediente, maxsplit=1)
```

#### Tipo de juicio y número de acuerdos

El tipo de juicio está entre el último punto de la demandada y el conteo de acuerdos. El regex busca al final del texto (antes de `Núm. Exp.`):

```python
RE_TIPO_ACDOS = re.compile(
    r"\.\s+(.+?)\s+(\d+)\s+(?:Acdos?|Audiencias?)\.\s*$",
    re.IGNORECASE | re.DOTALL,
)
```

Se captura tanto `Acdos?` como `Audiencias?` porque algunas entradas son de audiencias programadas, no acuerdos publicados.

#### Entradas con sub-notas de amparo (`//`)

Algunas entradas intercalan notas de amparo separadas por `//`, por ejemplo:

```
Pérez Palma Guadalupe vs. Franco López. //Amparo Indirecto N°. 1307/2025- I,
Interpuesto por María de la Luz Méndez Sánchez... // Manuel. Ord. Civil
Difiere Aud. Incidental. 1 Acdo. en Cuaderno_Amparo_Dem. Núm. Exp. 380/2025.
```

Sin tratamiento especial, el regex de tipo de juicio capturaba la nota de amparo completa (`//Amparo Indirecto N°...`) como `tipo_juicio`, lo cual era incorrecto. La regla:

- La **demandada** real está en el segmento anterior al primer `//`.
- El **tipo de juicio** real está en el último segmento (después del último `//`).

```python
if '//' in resto:
    segments = resto.split('//')
    demandada_raw = segments[0].strip().rstrip('.')
    last_seg = segments[-1].strip()      # aquí se busca RE_TIPO_ACDOS
```

#### Casos sin demandada

El 21.4% de las entradas no tiene `vs.`. Son casos legítimamente unilaterales:
- Jurisdicción Voluntaria
- Sucesiones (`Sucesión a Bienes...`)
- Providencias Precautorias
- Casos secretos (`Secreto.`)
- Extinción de dominio

Para estos, se guarda toda la parte inicial como `actora` y `demandada` queda vacío.

#### Líneas ignoradas

Algunas líneas son encabezados o anotaciones que no forman parte de entradas:

```python
SKIP_LINES = {
    "SOLO CONSULTA", "SIN ACUERDOS", "BOLETÍN JUDICIAL",
    "DEL PODER JUDICIAL DE LA CIUDAD DE MÉXICO",
    "NO PUBLICADOS", "AUDIENCIA",
}
```

---

## Resultados del PoC (10 de junio de 2026)

Boletín ID 4002, 376 páginas, 6.4 MB.

| Métrica | Valor |
|---|---|
| Entradas totales extraídas | 10,820 |
| Expediente encontrado | 10,820 (100%) |
| Actora encontrada | 10,820 (100%) |
| Con demandada | 8,507 (78.6%) |
| Con tipo_juicio | 8,052 (74.4%) |
| Entradas completamente estructuradas | 8,052 (74.4%) |

El 21.4% sin demandada corresponde a casos unilaterales (ver arriba), no son fallas de parseo.

---

## Re-parseo sin descargar (`reparse.py`)

Tras cambiar la lógica de parseo no hace falta volver a bajar los PDFs. `reparse.py` lee los `.txt` ya generados en `scraper/data/<fecha>/pdfs/`, aplica `parse_pdf` y reescribe `entradas.csv`:

```bash
python scraper/reparse.py scraper/data/2026-06-10
```

---

## Normalización de tipo_juicio (resuelto)

El boletín pega directo, sin ningún marcador, el verbo de la descripción del
acuerdo detrás del tipo de juicio real (`Oral Mercantil Girar oficio...`,
`Ordinario Civil Ratificar...`, `Ord. Civil Acuerdo.`) — antes esto generaba
76,652 valores distintos de `tipo_juicio` sobre ~830k entradas, para
apenas ~40 tipos de juicio reales.

La solución: `normalize_tipo.py` define `CANONICAL_TIPOS`, una lista cerrada
de los tipos de juicio reales (verificada contra el "Catálogo de Juicios y
Procedimientos del TSJDF", Consejo de la Judicatura, marzo 2013, y contra el
Código de Procedimientos Civiles CDMX), ordenada del más largo al más corto.
`poc.py` (`_match_canonical_tipo`, usado en `_split_tipo_juicio` y en el
fallback de `RE_TIPO_ACDOS`) expande abreviaciones (`Ord.` → `Ordinario`) y
matchea el prefijo más largo de esa lista contra el texto capturado — si
matchea, corta ahí sin importar qué verbo o código de actuario venga después.
Esto se aplica en el momento del parseo, no como limpieza posterior: el
`tipo_juicio` crudo pasó de 76,652 a 2,842 valores distintos.

`build_db.py` además calcula `tipo_juicio_norm` (columna aparte, usada por
el filtro/dropdown/gráfico del frontend) aplicando `normalize_tipo.normalize()`
sobre el `tipo_juicio` crudo, como red de seguridad para lo que el extractor
no cubre — deja 1,656 valores distintos, de los cuales el top 60 cubre 98.6%
de las entradas. El resto es ruido residual sin agrupar (código de
actuario/secretaría no catalogado, texto realmente incompleto en el boletín
original) — se deja tal cual en vez de forzarlo a una categoría inventada.

## Formato "Toca" (Salas y Sentencias) (resuelto)

El boletín usa un **segundo formato de terminador de entrada**, sin `Núm.
Exp.` en absoluto, en dos contextos:

1. **Toda la sección "SALAS"** (segunda instancia/apelación) — tanto sus
   acuerdos normales como sus sentencias.
2. La subsección **"SENTENCIAS"** dentro de cada Juzgado (sentencias
   definitivas).

Formato real:

```
[Actora] Vs. [Demandada]. [Tipo] T. [código] NNN/AAAA/NNN[, Cuad. Amp. ...][ Sent. Pon N,] N Acdo(s)./Audiencia(s)./Sent.
```

Ejemplos:
```
Controv. de Arrendamiento T. Ap 849/2024/002, 1 Acdo.
Esp. Hip. T. Qu 191/2026/007 Sent. Pon 2, 1 Sent.
Ord. Civ. T. 319/2022/003 Cuad. Amp. Iv. del Exp. 387/2021 del Juzg,
  69° de Lo Civil de Proceso Escrito de la Cdmx, 2 Acdos.
```

Antes de este fix, **toda la sección Salas se perdía en silencio** (nunca
contiene `Núm. Exp.`, así que `RE_ENTRY_END` nunca matchea) — no había
error ni log, simplemente no se generaban entradas. Impacto medido sobre
el corpus local: +49,631 entradas solo con el terminador Toca, sobre un
total previo de 833,536 (+5.9%).

`RE_TOCA_END` (en `poc.py`) es el segundo terminador, independiente del de
`Núm. Exp.` (cero riesgo de regresión en ese camino). El código de 1-4
letras tras "T." (`Ap`, `In`, `Qu`, `Cc`, `Rc`...) se deja genérico, sin
enumerar, mismo criterio que `RE_ENTRY_END`. La "T." va sin
`IGNORECASE` — permitir minúscula generaba falsos positivos con
abreviaturas ajenas que también terminan en "t." (`Amparo Directo Dt.
245/2025.`).

**Por qué no hay un límite ciego de caracteres entre el número de toca y
su cola**: el `Cuad. Amp.` que sigue al número puede ser tan corto como
`1,` o tan largo como una cláusula completa (`del Exp. NNN/AAAA del Juzg.
NN° de Lo Civil de Proceso Escrito de la Cdmx,`, +90 caracteres). Un
límite de caracteres no distingue "cláusula legítima larga" de "salto de
página que separa a un registro de su propia cola" (en ese caso el regex
se "come" el siguiente registro completo buscando la primera cola válida
que encuentra, fusionando dos avisos en uno). En cambio, se prohíbe que
el hueco contenga frases que solo aparecen en encabezados/pies de página
o separadores de sección (`SOLO CONSULTA`, `BOLETÍN JUDICIAL`, `ACUERDOS
DEL`, `SALA(S)`, `SECRETARIO/A DE ACUERDOS`, `MAL/NO PUBLICADOS`,
`AUDIENCIA`) — eso sí distingue ambos casos sin sacrificar cobertura.

`_split_demandada_tipo` (extraída de la lógica de `_split_tipo_juicio`,
ahora reusada por ambos formatos) recibe el límite derecho explícito en
vez de calcularlo internamente, para poder aplicarse tanto al caso
`Núm. Exp.` (el límite es la posición de "N Acdo.") como al caso Toca (el
límite es el final del segmento, no hay "N Acdo." dentro de él).

`RE_TIPO_START` ahora acepta que la palabra de arranque sea la ÚLTIMA de
la cadena (`(?:\s|$)` en vez de `\s` a secas) — sin esto, un tipo como
"Alimentos" al final de un segmento (sin nada después) nunca se detectaba
como palabra de arranque.

### Variante con conteo después de "Núm. Exp." (resuelto)

Descubierto durante la verificación del fix de arriba: en variantes más
nuevas del boletín (`Cnpcyf - Alimentos Núm. Exp. 4803/2026, S/T. 1
Acdo.`) el conteo de acuerdos va DESPUÉS de "Núm. Exp." en vez de antes
(lo usual es `[Tipo] N Acdo. Núm. Exp. NNNN/AAAA.`). Sin consumir ese "N
Acdo." al determinar el fin de la entrada, quedaba pegado al principio de
la SIGUIENTE entrada (se colaba en su actora). `RE_ENTRY_END` ahora tiene
un segundo grupo opcional que lo consume cuando está presente;
`_parse_entry` lo usa como `num_acdos` cuando no encontró uno antes de
"Núm. Exp.".

## Casos pendientes de mejorar

- **Ruido en tipo_juicio**: algunas entradas todavía incluyen fragmentos de nombre de empresa al inicio (`de C.V. Ord. Civil`) cuando el nombre de la demandada termina en abreviatura sin punto claro. (Las sub-notas de amparo con `//` ya se manejan, ver arriba.)
- **Entradas con `Acdo. en Expedientillo`**: notación especial donde el acuerdo está en un cuaderno separado. El regex no captura el conteo en estos casos.
- **Variantes de encabezado de sección**: el regex de juzgado cubre ordinales hasta Quincuagésimo; si existen juzgados con numeración mayor habrá que ampliarlos.
- **CANONICAL_TIPOS incompleta**: cubre los ~90 tipos más frecuentes: si aparecen tipos de juicio legítimos pero poco frecuentes que no están en la lista, quedan sin normalizar (visibles tal cual, no se pierden, solo no se agrupan).
- **Tercer formato de terminador, sin cubrir**: incidentes de "cuaderno de amparo" listados sin "Vs." usan un formato compacto propio, sin `Acdo.`/`Sent.`/`Audiencia.` como palabra clave (`Div. Sin Causa Inc. de Las Consecuencias de Div. No Convenidas 23152024- 00 Aut del 4`). Como ninguno de los dos terminadores actuales matchea, estas notas se acumulan sin cortarse hasta la próxima entrada válida — hoy terminan fusionadas dentro de la entrada previa o siguiente (`actora` anormalmente larga, con varias repeticiones del mismo caso). Bajo volumen, no se abordó en esta ronda.
