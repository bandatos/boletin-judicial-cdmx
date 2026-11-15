# Versiones públicas de sentencias (SIVEP)

15 de noviembre de 2026. Feedback de un abogado: "versión pública de la sentencia por transparencia, buscar en PNT". Este documento junta lo que se aprendió al cosechar y leer una muestra de sentencias, y cómo quedó el enlace desde la ficha del expediente.

## Decisión

Por ahora, la ficha enlaza a la versión pública de la sentencia cuando el emparejamiento es único. No se hace OCR ni se analiza el contenido. Lo de "Qué se puede sacar del texto" queda para después.

## Dónde están las sentencias

- **PNT:** la consulta pública pide captcha. En el OpenSearch de TeseoETL no hay sentencias del Tribunal Superior de Justicia de la CDMX: de ese sujeto obligado hay sueldos, directorio y contratos (`pnt_backup_full`), y las únicas "Sentencias, ejecutorias, suspensiones" de `pnt_sipot` (174) son del INAI.
- **SIVEP**, el sistema propio del Poder Judicial: `http://sivepj.poderjudicialcdmx.gob.mx:819/consulta/`. Es la fuente usada.
- Base legal: art. 69 fr. II de la Ley General de Transparencia de 2025 (versiones públicas de todas las sentencias), según un compendio, no el DOF `[sin verificar]`.

## Cómo funciona el SIVEP

- Página PHP con xajax. Se consulta por materia, juzgado, ejercicio (2019 en adelante) y trimestre, con un POST `xajax=searchSentencia`. Sin captcha ni sesión. Las listas de materias y juzgados salen de `soap.php` (POST `action=materia`) y `soapjuz.php?materia=PIC`.
- Cada fila trae: ejercicio, período informado, materia, tipo de juicio, "fecha de emisión", número de expediente (siempre vacío), órgano, juez o jueza, área y fecha de actualización, más el enlace al PDF.
- **El número de expediente está testado a propósito.** El aviso del sitio lo explica: publicarlo permitiría vincular la sentencia "con las publicaciones realizadas mediante Boletín Judicial" y hacer identificables a las partes.
- **El enlace al PDF vence a los 10 minutos.** Es una URL del gestor documental (`gestordocumental.poderjudicialcdmx.gob.mx/preview/document?...&documentPath=...&expires=...&signature=...`) con firma HMAC. No se puede generar: hay que repetir la consulta. El `documentPath` es estable y sirve de id.
- El PDF baja sin cookies ni referer.
- **Los PDF son escaneos.** La capa de texto solo trae la evidencia de firma electrónica (unas 470 palabras repetidas por página). Para leerlos hace falta OCR en español (tesseract `spa` local, o Tika con OCR en chocolatefactory).
- **La "fecha de emisión" no es la de la sentencia.** En los emparejamientos con OCR casi siempre coincide con la fecha del boletín que publicó el aviso, un día hábil después de la sentencia. 56 de 784 tienen una fecha posterior al fin del período informado (errores de carga, hay casos con el año corrido).

## Muestra

784 versiones públicas: juzgados 1°, 8° y 14° civil (2023 y 2024, más el 8° en 2025 T01) y 1° familiar (2023 T01). Media de 5 a 19 páginas por sentencia según el juzgado. 79 formas distintas de escribir el nombre del juez o la jueza.

Cobertura, una sola prueba: el 8° civil tiene 24 versiones públicas en 2025 T01, y el boletín publicó 46 avisos de sentencia definitiva de ese juzgado entre enero y marzo de 2025. `[hipotesis]` solo se publican las que quedan firmes, y con meses de demora. Se prueba comparando por juzgado y trimestre con la cosecha completa.

## Emparejamiento con el boletín

Dos métodos:

1. **Sin OCR** (`scraper/sivep_enlace.py`, el que se usa): solo metadata del SIVEP contra el boletín.
2. **Con OCR de las 2 primeras páginas** (`scraper/sivep_match.py`): fecha de la sentencia del texto ("a veinticuatro de noviembre de dos mil veintitrés"), fecha de la demanda (el año del expediente suele ser ese), tipo de juicio de "VISTOS los autos del JUICIO...", definitiva o interlocutoria, y palabras de las partes que el testado dejó visibles. Sobre 216 sentencias con OCR da 146 únicos, que se usan como referencia para medir el método sin OCR. No es una verdad absoluta: en al menos un caso el OCR leyó mal la fecha.

### La fecha de emisión tiene dos convenciones

Medido sobre los 146 únicos con OCR:

- En la mayoría, la "fecha de emisión" es la fecha del boletín que publicó el aviso de sentencia.
- En otros, es la fecha de la sentencia. Coincide con el "ACUERDOS DEL ..." del aviso (`fecha_acuerdo`), que se publica de 1 a 3 días hábiles después.
- Se mezclan dentro de un mismo juzgado y año. En el 1° civil, 2023 es casi todo fecha de boletín. En 2024 hay 21 de una y 27 de la otra.
- Ningún campo de la metadata separa las dos convenciones: probé juez, área, trimestre, fecha de actualización, tipo de juicio y los segmentos del `documentPath`.
- 56 de 784 tienen una fecha posterior al fin del período informado. Algunos son el año corrido (2024-11-27 en vez de 2023-11-27).

### Reglas (sin OCR)

Candidatos: avisos de sentencia definitiva (o de sala) del mismo órgano, con tipo de juicio compatible, que cumplen **cualquiera** de las dos convenciones: publicados en el boletín de la fecha de emisión, o con `fecha_acuerdo` igual a la fecha de emisión y publicados en los 3 días hábiles siguientes. Además:

- Si entre varios candidatos uno solo tiene el tipo de juicio exacto, queda ese.
- Si la fecha cae después del fin del período, se prueba con el año anterior.
- Solo se enlaza si queda un único expediente.

| Reglas | Enlaces | Coinciden con OCR | No coinciden |
|---|---|---|---|
| Solo fecha del boletín (primera versión) | 331 | 76 | 12 |
| + día hábil siguiente si no hay candidatos | 383 | 94 | 13 |
| Fecha del boletín o del acuerdo | 273 | 72 | 4 |
| Fecha del boletín o del acuerdo, + tipo exacto, + año anterior (**la que se usa**) | 301 | 76 | 4 |
| Lo mismo, + descartar candidatos ya enlazados a otra sentencia | 283 | 82 | 5 |

Sobre los 784: 301 únicos (38 %), 360 ambiguos y 123 sin candidato. En familiar rinde menos (38 de 136), porque un juzgado familiar publica muchas sentencias de divorcio por día.

De los 4 que no coinciden con la regla elegida:

- En uno, la `fecha_acuerdo` del aviso enlazado coincide con la fecha de la sentencia que leyó el propio OCR. Lo más probable es que el que se equivoca sea el OCR.
- Otro tiene una fecha de OCR (2020) incompatible con la de la metadata (2022).
- Los 2 errores claros son casos donde la fecha de emisión es la del día hábil siguiente al boletín, y ese día hubo otra sentencia del mismo tipo.

Precisión estimada: unos 77 de 80 (96 %).

### Lo que no funcionó

- **Corrimiento de un día:** sube la cobertura, pero baja la precisión a 86 % (`+1` y `-1`).
- **Segmentos del `documentPath`:** se pensó que alguno podía ser la secretaría del juzgado. No se relaciona con la secretaría del aviso.
- **Texto del PDF sin OCR:** ninguno de 120 PDF tomados al azar tiene capa de texto con el contenido. Todos son escaneos.

### Pistas para después

- **Expediente en la metadata:** una sentencia trae el número de expediente en el tipo de juicio ("ORDINARIO MERCANTIL EXP. 308/2021"). Es 1 de 784, pero cuando pasa el enlace es directo.
- **OCR de la primera página:** resolvería casi todos los ambiguos. Con 2 candidatos (133 casos), el año de la demanda suele decidir.
- **Interlocutorias:** el SIVEP casi no las publica. En el OCR aparecen 2 de 216.

## Cómo se enlaza en el sitio

1. `python scraper/sivep.py cosechar --sin-pdf [--materias ...] [--juzgados ...] [--anios ...]`: solo metadata, sin bajar PDF. Escribe `scraper/data/sivep/sivep.sqlite` (fuera del repo).
2. `python scraper/sivep_enlace.py`: tabla `enlaces`.
3. `python scraper/build_db.py ... --sivep scraper/data/sivep/sivep.sqlite` en el build completo, o `--solo-sivep --sivep ... --output base.sqlite` sobre una base armada. Crea `sentencias_publicas`. Los id de `entradas` cambian en cada build, así que el enlace se guarda por órgano, expediente y fecha del boletín, y se resuelve en el build.
4. Server: la ficha muestra "Ver sentencia (versión pública)" en el recuadro de la sentencia, y "Ver sentencia" en el aviso. Se abre en el mismo panel lateral que el boletín, cuyos enlaces ahora dicen "Ver boletín" para distinguirlos. `/sentencia-publica/:id` repite la consulta al SIVEP (`server/lib/sivep.js`, con caché de 8 minutos por consulta) y redirige a la URL vigente. Tarda unos 5 s. Si el SIVEP no responde, explica cómo buscarla a mano. Está en `robots.txt` para que los bots no disparen consultas.
5. La ficha aclara que la relación es probable y no segura, y ofrece avisar si no corresponde.

Pendiente: la cosecha completa de metadata (todas las materias que cubre el boletín, 2019 a hoy). Son 4 consultas por juzgado y año (una por trimestre), con pausa de 1 s, y no baja PDF. Los juzgados familiares tardan más por consulta (unos 2 a 3 minutos por trimestre en la muestra, con PDF incluido).

## Riesgo

Enlazar deshace el testado que el Tribunal hizo a propósito. Con el enlace, el texto de la sentencia (montos, inmueble, notaría, resultado) queda a un clic de los nombres de las partes que publica el boletín. Lo que lo acota hoy:

- Solo emparejamientos únicos.
- La ficha del expediente ya es `noindex` y está en `robots.txt`, y `/sentencia-publica/` también.
- No se copia ni se indexa el texto de la sentencia: el PDF lo sirve el Poder Judicial.

## Qué se puede sacar del texto (para después)

Visto con OCR de las 5 últimas páginas de 12 sentencias (hipotecario, divorcio, arrendamiento, ordinario civil y mercantil, ejecutivo mercantil).

**Estructura.** Encabezado con el lema del año y el juzgado. "Ciudad de México, a [fecha en letras]". "VISTOS los autos del JUICIO [tipo] seguido por [testado] en contra de [testado]". RESULTANDOS (la demanda y su fecha de presentación, lo reclamado, el emplazamiento), CONSIDERANDOS y los puntos resolutivos ("RESUELVE" o "SE RESUELVE", luego PRIMERO, SEGUNDO...). Firma del juez o la jueza y del secretario o secretaria de acuerdos, con nombre. Al final, la hoja de "EVIDENCIA CRIPTOGRAFICA - TRANSACCION" (firmantes, fechas de firma, folio) y la leyenda de versión pública con el fundamento.

**Lo que sale de los resolutivos**, con fórmulas que se repiten mucho:

- Resultado: "el actor probó su acción", "probó parcialmente", "no acreditó su acción", "se absuelve", "se condena".
- Rebeldía: "el demandado se condujo en rebeldía" (6 de las 12).
- Montos: suerte principal (por ejemplo $517,047.11 en un hipotecario, $77,939.63 en un ejecutivo mercantil), intereses y tasas. A veces el juez reduce la tasa: "4.43% mensual, reducida prudencialmente por esta juzgadora".
- Lo que se ordena: remate del inmueble (hipotecario), desocupación con apercibimiento de lanzamiento (arrendamiento), escrituración (ordinario civil), divorcio y liquidación de la sociedad conyugal.
- Costas: casi siempre "no se hace condena en costas". Sí en el ejecutivo mercantil.
- Divorcio: "inapelable", "causa ejecutoria por ministerio de ley", oficio al Registro Civil.
- Edictos: a veces ordena publicar los resolutivos en un periódico (por ejemplo "El Heraldo de México").

**Fechas útiles.**

- Demanda (resultandos) y sentencia: dan la duración real del juicio, mejor que "desde el primer aviso publicado".
- Firma de la versión pública: en un caso, la sentencia es de septiembre de 2021 y la firma de enero de 2023. Mide la demora en publicar.

**Nombres.** Juez o jueza y secretario o secretaria, en el texto y en la evidencia de firma, con fecha. Sirve para el pendiente de nombres de jueces de `propuesta-feedback-2026-10.md`. En la metadata del SIVEP el nombre del juez viene en 79 formas distintas en 4 juzgados: hay que normalizar.

**Testado.** Las partes aparecen como códigos ("MN", "PA", "MS"). Quedan visibles la notaría y el notario, la fecha de la escritura, los montos exactos, el municipio del Registro Civil y, a veces, nombres de empresas. Con eso, más el juzgado y la fecha, la reidentificación es más fácil de lo que el Tribunal supone.

**OCR.**

- Calidad: el cuerpo sale limpio con tesseract `spa` a 150 a 200 dpi. Los sellos y la evidencia de firma salen con basura y caracteres invertidos, y hay que cortar el texto antes de "EVIDENCIA CRIPTOGRAFICA" y de la hoja de firmas.
- Costo medido acá: unos 3.6 s de CPU por página a 150 dpi.
- Alcanza con OCR parcial: las 2 primeras páginas (demanda, tipo, fecha) y las 3 o 4 anteriores a la hoja de firmas (resolutivos).
- Con unas 136 mil sentencias (cifra de un comunicado del PJCDMX de 2023 `[sin verificar]`) y unas 6 páginas por sentencia, son unas 800 horas de CPU. `[hipotesis]` En chocolatefactory, en paralelo, serían días.

**Usos posibles**, de más a menos valor:

1. Resultados por tipo de juicio y juzgado: cuánto gana el actor, cuánto se litiga en rebeldía, montos típicos y duración. No depende del emparejamiento.
2. Transparencia del Tribunal: qué parte de las sentencias que avisa el boletín llega al SIVEP, por juzgado, y con cuánta demora.
3. En la ficha: un resumen en lenguaje llano de los resolutivos. Pesa más el riesgo de arriba, solo con emparejamiento verificado.
4. Casos de arrendamiento para Todo Derecho Vecino.

Un resumen con LLM sería mejor que las reglas, pero manda los documentos a un tercero, aunque sean públicos y estén testados.
