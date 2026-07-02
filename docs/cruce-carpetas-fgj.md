# Por qué no se puede cruzar el Boletín Judicial con las carpetas de investigación FGJ

## Objetivo que se evaluó

Todo Derecho Vecino busca ayudar a vecinos a rastrear causas judiciales relacionadas con terrenos/predios que cuestionan (construcciones irregulares, obras en disputa, etc.). La pregunta concreta era: ¿se puede ir de "este terreno está en conflicto" a "esta es la causa judicial correspondiente"?

Existe un dataset abierto de "mapa de causas" — las [Carpetas de Investigación FGJ de la Ciudad de México](https://datos.cdmx.gob.mx/dataset/carpetas-de-investigacion-fgj-de-la-ciudad-de-mexico) — que sí tiene ubicación. Se evaluó si se puede cruzar con este Boletín Judicial (que tiene expediente y nombres de partes, pero no ubicación).

## Qué tiene cada fuente

| | Boletín Judicial (este repo) | Carpetas de Investigación FGJ |
|---|---|---|
| Nombres de personas/empresas | Sí (actora, demandada) | No (removido por privacidad) |
| Número de expediente/carpeta | Sí | No |
| Ubicación (colonia, alcaldía, coordenadas) | No | Sí |
| Dirección exacta (calle) | No | No (se quitó `calle_hechos` en 2022 por privacidad) |
| Materia | Civil, familiar, mercantil | Penal (delitos) |

No hay ninguna columna en común entre ambas fuentes que sirva de llave de cruce exacto: una tiene identidad de las partes sin ubicación, la otra tiene ubicación sin identidad.

## Por qué un cruce aproximado (fecha + jurisdicción) tampoco funciona

**Jurisdicción no es geografía en los juzgados civiles/familiares de la CDMX.** Los casos se asignan por **sorteo electrónico** entre más de 30 juzgados de la materia correspondiente, vía la Oficialía de Partes Común — no por la alcaldía donde está el domicilio o el predio en disputa. El nombre del juzgado (p. ej. "TRIGÉSIMO DE LO FAMILIAR") no dice nada sobre en qué alcaldía ocurrieron los hechos. No existe la correspondencia juzgado → alcaldía que haría falta para este cruce.

Fuente: [Mapa Interactivo de Juzgados Familiares CDMX](https://abogadosfamiliarescdmx.com/herramientas/mapa-juzgados/); [Acuerdo General 3/2013 del Pleno del CJF](https://apps.cjf.gob.mx/normativa/Recursos/2013-3-0-AC_V217.html) (sistema de turnos análogo a nivel federal).

**Fecha sola no acota lo suficiente.** Tanto el boletín (miles de acuerdos por día en toda la ciudad) como las carpetas FGJ (volumen diario alto también) tienen tanto volumen que emparejar solo por fecha produce decenas o cientos de "candidatos" sin ninguna señal real de que correspondan al mismo caso. Combinar fecha + "jurisdicción" no ayuda porque el segundo criterio no existe de forma utilizable (ver punto anterior).

**Son dominios legales distintos.** El boletín cubre materia civil/familiar/mercantil. Las carpetas FGJ son investigaciones penales (delitos). Una disputa de terreno (construcción irregular, invasión, etc.) normalmente es un asunto civil o administrativo, no penal — solo generaría una carpeta FGJ si hay un delito asociado (despojo, daño en propiedad ajena, etc.), que es un subconjunto chico y no garantizado de los casos que a los vecinos les importan.

## Conclusión

Con las fuentes disponibles hoy, **no hay una forma confiable de ir de "terreno en disputa" a "causa judicial"** por ubicación, ni exacta ni aproximada. El único camino que funciona con lo que tenemos es buscar por el nombre de la persona o empresa involucrada (inmobiliaria, desarrollador, dueño) — que es lo que ya hace este buscador.

Nota al margen: en `scraper/build_db.py` ya existe una tabla `carpetas_fgj` (fecha_inicio, fecha_hecho, delito, categoria_delito, fiscalia, alcaldia, colonia, lat, lon) que no se está poblando — parece que alguien había anticipado este cruce antes de este análisis. Dado lo de arriba, esa tabla no resuelve el objetivo de cruce con el boletín; si se mantiene, es útil solo como capa de datos independiente (ej. un mapa de incidencia delictiva), no como puente hacia expedientes civiles.

## Qué haría falta para resolverlo de verdad

Una fuente que junte **nombre de la parte + ubicación del predio** en el mismo registro. El candidato más directo es el **Registro Público de la Propiedad y de Comercio (RPP) de la CDMX** — si tiene datos abiertos de propietarios/desarrolladores por predio, ahí sí habría una llave real (nombre) para cruzar contra actora/demandada del boletín. Pendiente de investigar si el RPP de CDMX publica esto como dato abierto y bajo qué condiciones de acceso.
