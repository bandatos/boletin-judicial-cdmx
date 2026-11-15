# Plan: formulario de opinión dentro del sitio

15 de noviembre de 2026. Aprobado el mismo día: reenvío a Google, link en el pie y reoferta con el formulario adentro. Implementado.

## Por qué

Del 9 al 15 de noviembre alrededor de una de cada siete personas que vieron la pregunta de intención la respondió, pero el formulario de Google no recibió ninguna. Con el texto nuevo ("Sí, les cuento") hubo 2 clics y 0 envíos: la gente abre Google Forms en otra pestaña y lo abandona. El paso que se pierde es el salto a Google, no la invitación.

## Qué cambia para quien usa el sitio

Después de responder "¿Para qué estás buscando?", la misma franja se convierte en el formulario, sin salir de la página:

```
¡Gracias! ¿Nos cuentas más sobre tu experiencia?      ✕
Es anónimo. Puedes llenar uno solo de los dos.

¿Qué te sirvió?
[                                              ]
¿Qué te faltó o qué cambiarías?
[                                              ]

▸ ¿Platicamos 20 minutos? (opcional)
    Nombre [          ]   Correo o WhatsApp [          ]
    ☐ Acepto que Bandatos use estos datos para contactarme ...

[Enviar]   No, gracias
```

- Dos campos de texto, ninguno obligatorio, pero hay que llenar al menos uno para enviar.
- La entrevista queda plegada (`details`) para que no parezca que hay que dar datos. Si se llena nombre o contacto, el consentimiento pasa a ser obligatorio.
- La respuesta a la pregunta de intención va adjunta sin que la persona la vea ni la repita.
- Al enviar: "¡Gracias! Lo leemos todo." y la franja se cierra a los pocos segundos.
- La franja de la reoferta (a los 3 días) y el panel del PDF usan el mismo formulario. En el panel del PDF el link "Sí, les cuento" despliega el formulario debajo del PDF en vez de abrir Google.
- Página `/opinion` con el mismo formulario, linkeada desde el pie ("Danos tu opinión"). Es además el destino sin JavaScript.

Se sacan "¿Encontraste lo que buscabas?" y la preferencia de formato de la entrevista: se pregunta en la entrevista misma.

## Dónde se guardan las respuestas

**A. El server reenvía al Google Form (recomendada).** El sitio manda a `POST /opinion` y el server lo reenvía a `formResponse` del formulario actual con los `entry.*` de cada pregunta. Las respuestas siguen cayendo en la hoja de cálculo y llegan las notificaciones que ya se configuren ahí. El server no guarda nada, y los datos personales no tocan el disco de la máquina.

- Riesgo: Google puede rechazar envíos desde un server (captcha, cambios de formato). Se prueba antes con un envío real marcado como prueba.
- Si Google falla, el server responde error y el sitio muestra "No se pudo enviar, intenta de nuevo". No se pierde en silencio.
- Los IDs de campo se recalculan si alguien borra y vuelve a crear una pregunta del formulario: quedan en un solo lugar (`config/opinion.json`) con un test que avisa si cambian.

**B. Guardar en el server.** Una base `opiniones.sqlite` en el volumen `/data`, con una página protegida o un script para exportar. Independiente de Google, pero agrega datos personales al server, permisos de escritura en el volumen, borrado al terminar el estudio y una forma de leerlas.

Recomiendo A ahora y B solo si Google rechaza los envíos.

## Cambios técnicos

| Archivo | Cambio |
|---|---|
| `server/lib/router.js` | Aceptar `POST` en rutas marcadas `"method": "POST"`. Leer el cuerpo con tope de 8 KB. Sin caché de HTML para POST. |
| `server/lib/opinion.js` (nuevo) | Validar (largo máximo por campo, al menos un texto, consentimiento si hay contacto), honeypot, límite por IP (5 por hora, en memoria), reenvío a Google con `fetch` y timeout de 8 s. |
| `server/config/opinion.json` (nuevo) | URL de `formResponse` y mapa campo → `entry.*`. |
| `server/config/routes.json` | `GET /opinion` (página) y `POST /opinion` (envío, JSON o form). |
| `server/lib/views.js` | Handler de la página y del envío. Sin JS el POST redirige a `/opinion?enviado=1`. |
| `server/templates/mixins.pug` | `mixin formOpinion(lugar)` reemplaza a `invitacionEntrevista`. Lo usan la pregunta, la reoferta, el panel del PDF y `/opinion`. |
| `server/templates/opinion.pug` (nuevo) | Página `/opinion`. |
| `server/static/js/behavior.js` | Envío con `fetch` y estados de enviando, enviado y error. Se saca `OPCIONES_FORM` y `conRespuesta`. |
| `server/static/css/main.css` | Estilos del formulario dentro de la franja. Mismas reglas de pre-pintado para no mover la página (CLS). |
| `server/lib/logger.js` | Nada: ya loguea solo método, URL y estado, nunca el cuerpo. Se agrega un test que lo asegura. |
| `server/test/http.test.js` | Render del formulario en las tres páginas, validaciones, honeypot, límite, reenvío con un Google simulado, redirección sin JS. |
| `CHANGELOG.md` | Una línea. |

### Medición (GA)

| Evento | Cuándo | Parámetros |
|---|---|---|
| `opinion_empezada` | primer foco en un campo | `lugar` |
| `opinion_enviada` | envío aceptado por el server | `lugar`, `con_contacto` (sí/no) |
| `opinion_error` | el server o Google falló | `lugar` |
| `entrevista_rechazada` | "No, gracias" o ✕ | `lugar` (se mantiene) |

Nunca se manda el texto ni los datos de contacto a GA. Se deja de usar `entrevista_click`.

### Seguridad y privacidad

- El texto se trata como dato: se reenvía tal cual a Google, no se renderiza en el sitio.
- Sin cookies nuevas. El estado "ya opinó" sigue en `localStorage` (`boletin-entrevista=si`) para no volver a mostrar la franja.
- El aviso de privacidad del formulario se muestra en el sitio, junto al consentimiento, con el correo para pedir el borrado.

## Pasos

1. Envío de prueba a `formResponse` desde la máquina local para confirmar que Google lo acepta y que llegan los campos. Es un envío hacia afuera: se confirma antes.
2. Router con POST y `lib/opinion.js` con sus tests.
3. Mixin, página `/opinion`, JS y CSS.
4. Revisión en el navegador: escritorio y celular, con y sin JS, CLS en la búsqueda.
5. Deploy y un envío real desde producción.
6. A la semana, comparar `opinion_enviada` / `intencion` contra los 2 clics y 0 envíos de ahora.

## A decidir

1. Dónde se guardan: A (reenvío a Google) o B (base propia).
2. Si la página `/opinion` va linkeada en el pie de todas las páginas o solo existe como destino sin JS.
3. Si se mantiene la segunda oportunidad a los 3 días con el formulario adentro o se saca.
