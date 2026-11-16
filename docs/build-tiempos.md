# Tiempos del build de la base

Registro de corridas de `scraper/build_db.py` para comparar optimizaciones. Misma máquina local (24 hilos) salvo que se diga otra cosa. Desde la v6 el build usa como máximo el 80 % (19 núcleos, `BUILD_CPU`) y corre con `nice 10`. Agregar una fila por corrida nueva.

## Corridas completas

| Fecha | Versión | Flags | Avisos | Carga | Total | Carga por aviso | Total por aviso | Tamaño | Notas |
|---|---|---|---|---|---|---|---|---|---|
| 2026-09-21 | previa | sin `--indices-server` ni `--empresas` | ~34M | | ~9 min | | ~16 µs | | Dato de memoria, sin log |
| 2026-09-28 | previa | `--indices-server --empresas` | ~34M | | 51 min 36 s | | 91 µs | 14.3 GB | Máquina cargada. Recalcular empresas aparte: 21 min |
| 2026-10 | previa | personas en el server | | | ~1.5 h | | | | Con `nice`, en el server de producción |
| 2026-11-15 | v3 | `--indices-server --empresas --estadisticas --sivep` + `--personas` aparte | 35.07M | 1372 s | 75.3 min (51.5 + 23.7) | 39 µs | 129 µs | 19.3 GB | En producción. Vocabulario sin personas (81,402 palabras) |
| 2026-11-15 | v4 | v3 + personas en la carga, pcre, sin `idx_anio`, VACUUM al final | 35.07M | 1735 s | 55.6 min | 49 µs | 95 µs | 16.3 GB | Mismo contenido que v3. Vocabulario completo (216,778). La carga incluye detectar personas |
| 2026-11-15 | v5 1ª | v4 + precálculo + etapas en paralelo | 35.07M | 1946 s | 46.7 min | 55 µs | 80 µs | 16.3 GB | Primera vez: genera el precálculo. Más un estado de 5.5 GB para el incremental |
| 2026-11-15 | v5 2ª | ídem, con precálculo | 35.07M | 311 s | 18.6 min | 9 µs | 32 µs | 16.3 GB | |
| 2026-11-15 | v5 inc | `--incremental` sumando la corrida de noviembre | +524,930 | 106 s | 10.8 min | 202 µs (agregados) | 1,238 µs (agregados), 19 µs (sobre 35M) | 16.6 GB | Base previa sin `2026-11-15`: 18 min. Sin VACUUM |
| 2026-11-15 | v6 1ª | v5 + partes en la base (sin estado), precálculo SQLite, agregados en una pasada, sin `idx_anio_tipo` ni `docsize` | 35.07M | _pendiente_ | _pendiente_ | | | | Genera el precálculo. 80 % de CPU |
| 2026-11-15 | v6 2ª | ídem, con precálculo | 35.07M | _pendiente_ | _pendiente_ | | | | |
| 2026-11-15 | v6 inc | `--incremental` con agregados incrementales, configuración del server (300 MB de caché, etapas de a una, 4 procesos) | +524,930 | _pendiente_ | _pendiente_ | | | | Base previa sin `2026-11-15` |

Por aviso = tiempo / cantidad de avisos de la base, en microsegundos. En el incremental se divide por los avisos agregados.

## Por etapa (segundos)

En v5 FTS, empresas (con agregados), estadísticas y personas corren a la vez: cuenta el tiempo de pared de la etapa paralela, no la suma.

| Etapa | v3 | v4 | v5 1ª | v5 2ª | v5 inc |
|---|---|---|---|---|---|
| Carga (parseo y derivados) | 1372 | 1735 (incluye detectar personas) | 1946 (genera precálculo) | 311 | 106 |
| Índices | 468 | 226 | 208 | 209 | 3 (rangos y FTS de lo nuevo) |
| Empresas | 214 | 131 | 556 en paralelo | 515 en paralelo | 449 en paralelo |
| Agregados de empresas | 233 | 137 | dentro de empresas | dentro de empresas | dentro de empresas |
| Estadísticas | 406 | 350 | 484 en paralelo | 425 en paralelo | 362 en paralelo |
| FTS + optimizar | 249 | 204 | 470 en paralelo | 449 en paralelo | (arriba) |
| Personas (agrupar) | 1424 (build aparte) | 140 | 221 en paralelo | 177 en paralelo | 163 en paralelo |
| Copiar etapas a la base | | | 43 | 36 | 44 |
| **Etapa paralela (pared)** | | | **642** | **592** | **536** |
| SIVEP + vocabulario | 1 + (en compactar) | 2 + 7 | 5 | 4 | 4 |
| Compactar (VACUUM) | 148 | 402 | no hace falta | no hace falta | no hace falta |
| **Total** | **4515** | **3334** | **2801** | **1116** | **650** |

Cuello de botella en v5: empresas (8 a 9 min), que en paralelo compite por disco y CPU con FTS. En el incremental se recalcula entera, igual que estadísticas y personas.

La v5 completa dio el mismo contenido que la v4, fila por fila: las 26 tablas (35,073,842 avisos, empresas, personas, estadísticas, vocabulario, enlaces SIVEP) y las mismas cantidades en búsquedas de prueba sobre FTS. Solo cambia `meta.built_at`.

La corrida diaria (`scraper/diario.py`, con 300 MB de caché y etapas de a una) sumó 2 boletines (28,788 avisos) en 14.8 min en esta máquina, con un pico de 1.8 GB de memoria: build incremental 873 s (empresas 375, estadísticas 284, personas 103).

## Cambios de la v6

| Cambio | Efecto medido o estimado |
|---|---|
| Detecciones de empresas y personas en la base (`partes_nombres`, `partes_avisos`, enteros) en vez del archivo de estado | Subconjunto: base + estado 1,014 MB → 798 MB (−21 %). Completo, estimado: 21.8 GB → ~16 GB |
| Perfil de persona desde `partes_avisos` en vez de buscar el nombre en FTS | 208 personas: 530 ms → 66 ms en total. "García Hernández Alejandro": 20-26 s → milisegundos |
| `resumen_anio_tipo` / `resumen_juzgado` en vez de `idx_anio_tipo` | −1.23 GB, el server ya no recorre la tabla al abrir la base |
| FTS sin `docsize` (`columnsize=0`, no se usa bm25 sobre avisos) | −0.46 GB |
| `idx_anio` vuelve (búsquedas filtradas solo por año) | +0.39 GB |
| Precálculo en SQLite ya en formato de la base; la carga copia rangos de filas | Sin parsear CSV ni JSON ni escribir cada aviso dos veces |
| Empresas: datos de cada aviso juntados una vez, recorriendo en orden de entrada | Antes: 5 consultas que saltaban por la tabla de 8.7 GB |
| Estadísticas: una sola lectura de `entradas` (tabla temporal angosta) | Antes: 5 lecturas completas |
| Incremental: solo las empresas, personas, meses y expedientes que tocan los avisos nuevos | Antes rehacía todo (8 de los 15 min de la corrida diaria) |
| Límite de 80 % de CPU y `nice` | La máquina sigue usable durante el build |

Verificación en el subconjunto: el incremental da lo mismo que el build completo en las 25 tablas (entradas, empresas y sus agregados, personas, partes, estadísticas, duraciones, resúmenes, vocabulario) y en las búsquedas. Los agregados nuevos dan lo mismo que las consultas de la v5 aplicadas a la misma base. Cambia a propósito el conteo de avisos de una persona que aparece de los dos lados de un mismo aviso (antes 2, ahora 1): 159 personas del subconjunto quedan bajo el mínimo.

## Subconjunto de prueba (1.38M avisos, corridas 2026-09-23 y 2026-11-15)

| Variante | Carga | Total | Carga por aviso | Total por aviso |
|---|---|---|---|---|
| Base (como v3) | 63 s | 169 s | 46 µs | 122 µs |
| v4 (pcre, personas en la carga, sin `idx_anio`, VACUUM al final) | | 142 s | | 103 µs |
| Precálculo, primera vez (lo genera) | | 214 s | | 155 µs |
| Precálculo, segunda vez | 13 s (antes 124 s) | 72 s | 9 µs | 52 µs |
| Precálculo + etapas en paralelo | 13 s | 48 s | 9 µs | 35 µs |
| Incremental: base de septiembre (30 s) + sumar noviembre | | 41 s | | |
| v6, primera vez (genera el precálculo SQLite) | | 138 s | | 100 µs |
| v6, con precálculo | 13 s | 57 s | 9 µs | 41 µs |
| v6 incremental: sumar noviembre (agregados incrementales) | | 53 s | | |

En el subconjunto la v6 con precálculo tarda algo más que la v5 (57 s contra 48 s): la etapa de partes ordena todas las detecciones antes de que arranquen empresas y personas. A cambio no hay archivo de estado y el incremental no rehace todo.

Las tres variantes v5 dan el mismo contenido que el build completo: entradas, boletines, personas, empresas, estadísticas, vocabulario y FTS (con `integrity-check`).

## Micro-mediciones

| Cambio | Antes | Después |
|---|---|---|
| `descripcion()` expandiendo una vez por inicio (60k avisos) | 8.8 s | 3.9 s |
| Caché en `normalize_tipo` | | −22 % |
| `personas.normalizar` con `translate` + filtros previos | 12.2 s | 10.3 s (7.4 s con pcre) |
| Regex de empresas, `re` vs pcre (40k campos) | 1.8 s | 1.2 s |
| Módulo `regex` (mrab) en vez de `re` | 8.7 s | 18.2 s (descartado) |

## Qué hace cada optimización

- **pcre:** `import pcre as re` si está instalado (`pip install pypcre`), si no `re`. Solo acelera, el resultado es igual.
- **Precálculo:** la carga guarda por corrida todo lo derivado de cada aviso (tipo de acuerdo, descripción, empresas, personas). Hasta la v5 en `entradas.precalc.csv`, desde la v6 en `entradas.precalc.sqlite` ya con el formato de la base, y la carga copia rangos de filas por boletín. La versión (hash del código del parser) lo invalida si cambia `acuerdos.py`, `empresas.py`, `personas.py`, `normalize_tipo.py` o `derivar`.
- **Etapas en paralelo:** FTS, resumen, estadísticas y partes se arman cada una en su archivo SQLite leyendo la base en solo lectura, y después se copian a la base. Empresas y personas arrancan cuando termina partes. La tabla de staging va en un archivo aparte que se borra al final, así no hace falta VACUUM.
- **Incremental:** `--incremental` suma solo los boletines que faltan sobre una base existente. Hasta la v5 necesitaba un archivo de estado aparte (5.5 GB) con todas las detecciones. Desde la v6 están en la base (`partes_nombres` y `partes_avisos`, como enteros) y `scraper/agregados.py` rehace solo lo que tocan los avisos nuevos: empresas y personas que aparecen en ellos, los meses de esos boletines (leyendo solo esos boletines por rango de ids), los expedientes con avisos nuevos para el tiempo hasta sentencia (`duracion_expedientes`), y suma a `tipo_organo` y a los resúmenes.

## Etapas y uso de la app

Vistas de página desde el 15/10/2026 (GA4, 5,196 vistas):

| Ruta | % vistas | Etapas que la alimentan |
|---|---|---|
| `/buscar` | 42.8 | carga, índices, FTS, vocabulario (sugerencias) |
| `/empresas` | 12.9 | empresas, agregados de empresas |
| `/` portada | 12.3 | carga, empresas, estadísticas (enlaces) |
| `/expediente` | 8.8 | carga, SIVEP, estadísticas ("¿Cómo trabaja este juzgado?") |
| `/fecha` | 7.8 | carga |
| `/juzgado` | 5.9 | carga, estadísticas |
| `/personas` | 5.2 | personas |
| `/juzgados`, `/tipos`, `/salas` | 2.5 | estadísticas |
| `/mis-expedientes` | 1.0 | carga |

Las dos etapas caras con poco uso:

- **Personas** (24 min en v3, la mayor parte dentro de la carga desde v4): solo alimenta `/personas` (5.2 % de las vistas, `noindex`) y el vocabulario de sugerencias. Con el precálculo deja de costar en cada build.
- **Estadísticas** (6 a 7 min): `/juzgados`, `/tipos`, `/salas` y el enlace "¿Cómo trabaja?" suman cerca del 2.5 %, más algo de `/juzgado`. En paralelo queda oculta detrás de FTS.
