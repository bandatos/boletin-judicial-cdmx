# Plan: medir intención y captar usuarios

Boletín Judicial CDMX, 4 de noviembre de 2026. Fases 1 (eventos y log) y 2 (pregunta de intención, con invitación a una entrevista por formulario de Google) implementadas el 5 de noviembre. El resto, para revisar.

## De dónde parte

Datos de GA (propiedad `560451546`) y del log del server, del 23 de octubre al 4 de noviembre:

- 366 usuarios, 435 sesiones. Crece: de 16 a 36 usuarios por día la primera semana a 86 el 3 de noviembre.
- Origen: ChatGPT 183 sesiones (la mayoría desde el celular, 6 páginas y 6 minutos por sesión), Google 140 (sube cada semana), directo 96.
- Búsquedas: unas 950 hechas por personas del 26 de octubre al 3 de noviembre. 75 % nombres, 23 % números de expediente, 2 % empresas.
- PDF del boletín: unas 100 aperturas, 64 en los últimos dos días. Es la señal más clara de intención: la persona encontró el aviso y va a verificarlo.
- Mis expedientes: 10 visitas a la página y ningún uso real. Los 678 pedidos a `/api/expedientes` del log son todos de un script del 4 de noviembre a las 00:05.
- Ese script hizo además unas 1,000 búsquedas en 27 minutos, con picos de 9 por segundo.

Lo que no sabemos: para qué busca la gente, si guarda expedientes, y qué hace ChatGPT con el sitio cuando lo lee por su cuenta.

## Fase 1: medir lo que pasa (sin datos personales)

Objetivo: saber qué funciona antes de cambiar la interfaz. Todo va a GA como eventos, sin nombres ni términos nuevos.

1. **Eventos que faltan** (`server/static/js/behavior.js`):
   - `guardar_expediente` y `quitar_expediente` al tocar el botón de la ficha (`button.boton-guardar`), con la cantidad de expedientes que quedan en la lista.
   - `ver_pdf` cuando se abre el PDF en el panel lateral (`a.ver-pdf`), con el tipo de página desde la que se abrió (expediente, búsqueda, empresa, persona, día). Hoy solo lo ve la medición mejorada como `file_download`, y no cuando se abre en el panel.
   - `mis_expedientes_con_lista` al cargar `/mis-expedientes` con al menos un expediente guardado. Separa a quien entra a curiosear de quien la usa.
2. **Dimensiones personalizadas en GA** (las crea la persona con acceso de administración): `seccion` (ya se manda con `search`), `tipo_pagina` y `cantidad`. Solo cuentan desde que se crean.
3. **Un informe de exploración** en GA con el embudo: búsqueda → ficha de expediente → PDF → guardar. Ahí se ve dónde se cae la gente.
4. **Log del server** (`server/lib/logger.js`): agregar el dominio de origen (solo el dominio del `Referer`, no la URL) y una marca de bot según el user-agent (`ChatGPT-User`, `OAI-SearchBot`, `Googlebot`, `bingbot` y similares). Así se ve cuántas veces ChatGPT lee el sitio para responder, que GA no registra.

Esfuerzo: medio día. Riesgo: bajo.

## Fase 2: preguntar la intención

Una sola pregunta, una vez por navegador, después de la primera búsqueda con resultados:

> ¿Para qué estás buscando?
> Mi propio juicio · El de alguien que conozco · Una empresa o un inmueble · Trabajo (abogacía, periodismo, investigación) · Otro

- Se muestra como una franja debajo del conteo de resultados, con un botón para cerrarla. No es un modal, para no tapar resultados.
- La respuesta va a GA como evento `intencion` con el valor elegido. No se guarda en el server.
- Se recuerda en `localStorage` que ya se respondió o se cerró, y no vuelve a aparecer.
- Se puede cruzar con el origen: ¿quien llega de ChatGPT busca su propio juicio y quien llega de Google busca empresas?

Esfuerzo: medio día. Riesgo: bajo. Conviene tenerla un mes antes de decidir qué construir después.

## Fase 3: que Mis expedientes se use

Hoy el botón de guardar solo está en la ficha del expediente y nadie lo usa.

1. **Ofrecerlo en el momento de mayor intención**, justo después de abrir el PDF: en el panel lateral, "¿Querés seguir este expediente? Guardalo y te mostramos cuándo sale un acuerdo nuevo." Con el mismo botón de la ficha.
2. **Botón de guardar en cada tarjeta de resultado** (`+resultCard` en `mixins.pug`), no solo en la ficha.
3. **Explicar la sección** en `/mis-expedientes` cuando está vacía: qué es, que vive solo en este navegador y que no hace falta cuenta.
4. **Medir** con los eventos de la fase 1, y comparar la tasa de guardado antes y después.

Esfuerzo: un día. Riesgo: bajo.

## Fase 4: proteger el server de scripts

El script del 4 de noviembre hizo 1,000 búsquedas en 27 minutos sobre un server compartido. Algunas búsquedas tardan 1 a 3 segundos.

1. **Límite de búsquedas por IP** en el server: por ejemplo 30 por minuto en `/buscar` y `/api/`. Al superarlo, responde 429 con un mensaje claro. Las personas nunca llegan a ese ritmo.
2. **`/api/` en `robots.txt`**: ya está.
3. **Revisar el user-agent del script** cuando esté el log de la fase 1, para decidir si se bloquea o si se le ofrece una forma ordenada de acceder (por ejemplo, si es un periodista o una organización).

Esfuerzo: medio día. Riesgo: bajo, si el límite es holgado.

## Fase 5: avisos por correo (requiere decisiones previas)

Es la forma más fuerte de captar usuarios: quien sigue su expediente deja su correo para enterarse de los acuerdos nuevos. La maqueta existe en `/alertas/ejemplo`. Depende de tres cosas que hoy no están:

1. **Actualización diaria del boletín en el server.** La base llega al 22 de septiembre porque el scraper no corre desde entonces. Sin datos nuevos no hay nada que avisar. Es el modo incremental de `build_db.py` más un contenedor que corra el scraper cada día hábil (ver la propuesta de octubre).
2. **Aviso de privacidad y consentimiento**, revisados por el abogado. Se guardaría un correo junto con los expedientes que sigue: es un dato personal asociado a juicios. El marco legal de México que tenemos documentado en abriamos es una referencia general sin verificar, así que esta parte no puede salir sin revisión.
3. **Envío de correos**: con qué servicio, desde qué dominio (bandatos.org) y quién lo opera.

Recién con eso: formulario de alta con confirmación por correo (doble opt-in), un aviso cuando aparece un acuerdo nuevo en un expediente seguido, y baja con un clic.

Esfuerzo: varios días, más el tiempo del abogado. Riesgo: medio (datos personales y operación de correo).

## Fase 6: conectar con Todo Derecho Vecino

En la ficha del expediente, debajo del aviso, un bloque breve: "¿Necesitás orientación sobre este juicio?" con un link al canal de Todo Derecho Vecino (WhatsApp o formulario). Es la misión del proyecto, y quien llega hasta ahí tiene un problema concreto.

Depende de que el equipo de Todo Derecho Vecino defina el canal y quién responde.

## Orden propuesto

| Paso | Qué | Depende de | Cuándo |
|---|---|---|---|
| 1 | Fase 1: medir | nada | antes de la demo |
| 2 | Fase 4: límite por IP | nada | antes de la demo |
| 3 | Fase 2: pregunta de intención | fase 1 | antes o justo después de la demo |
| 4 | Fase 3: Mis expedientes visible | fase 1 | después de la demo |
| 5 | Actualización diaria del boletín | modo incremental de `build_db.py` | en paralelo |
| 6 | Fase 5: avisos por correo | paso 5, abogado, servicio de correo | cuando estén las tres |
| 7 | Fase 6: Todo Derecho Vecino | canal definido por TDV | cuando lo definan |

## Decisiones pendientes

- ¿Se muestra la pregunta de intención a todos o solo a una parte (por ejemplo, la mitad) para comparar?
- Límite por IP: ¿30 búsquedas por minuto está bien, o preferís empezar más alto y bajarlo?
- Avisos por correo: ¿quién opera el envío y desde qué dominio? ¿Cuándo se puede consultar al abogado?
- Todo Derecho Vecino: ¿qué canal y quién responde?
- Términos de búsqueda en GA: hoy se mandan completos, con nombres. ¿Se mantiene así después de hablar con el abogado?

## Cómo sabremos si funcionó

- Fase 1: en una semana tenemos el embudo completo y la cantidad de lecturas de ChatGPT.
- Fase 2: al menos 100 respuestas en el primer mes, para ver la distribución de intenciones.
- Fase 3: pasar de 0 a al menos un 5 % de las sesiones con PDF que terminan guardando un expediente.
- Fase 4: ningún pico de más de 30 búsquedas por minuto desde una misma IP.
- Fase 5: correos registrados con confirmación, y avisos que llegan el mismo día que sale el acuerdo.
