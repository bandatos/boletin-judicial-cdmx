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

## Casos pendientes de mejorar

- **Ruido en tipo_juicio**: algunas entradas incluyen fragmentos de nombre de empresa al inicio (`de C.V. Ord. Civil`). Ocurre cuando el nombre de la demandada termina en abreviatura sin punto claro.
- **`Ord. Civil Acuerdo.`**: el "Acuerdo." al final del tipo de juicio es parte de la descripción del acuerdo, no del tipo. Se puede limpiar con una lista de sufijos conocidos.
- **Entradas con `Acdo. en Expedientillo`**: notación especial donde el acuerdo está en un cuaderno separado. El regex no captura el conteo en estos casos.
- **Variantes de encabezado de sección**: el regex de juzgado cubre ordinales hasta Quincuagésimo; si existen juzgados con numeración mayor habrá que ampliarlos.
