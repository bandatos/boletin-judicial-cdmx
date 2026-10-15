# Boletines con link roto en el portal

Registro de boletines cuyo PDF no se pudo bajar desde el link que publica el
portal del PJCDMX (`consultabpj.poderjudicialcdmx.gob.mx:2096`). Sirve para
saber qué pedir por solicitud de acceso a la información si algún día el
archivo deja de estar disponible.

Revisión: 23 de septiembre de 2026, sobre el índice completo del portal
(3 de octubre de 2016 al 22 de septiembre de 2026, 2,035 boletines).

## Estado: ninguno falta

Los 9 boletines que dieron 404 están en el portal con otro nombre de archivo.
Se bajaron de la URL corregida y se verificó en la primera página que la fecha
y el número de boletín coinciden. `index.json` guarda la URL que funciona en
`pdf_url` y la del portal en `pdf_url_portal`.

| Fecha | ID interno | Boletín | Link del portal (404) | Archivo real |
|---|---|---|---|---|
| lunes 11 mar 2019 | 992 | No. 45 | `110320091.pdf` | `110320191.pdf` |
| martes 19 mar 2019 | 1002 | No. 50 | `190320091.pdf` | `190320191.pdf` |
| martes 26 mar 2019 | 1012 | No. 55 | `260320091.pdf` | `260320191.pdf` |
| martes 2 abr 2019 | 1022 | No. 60 | `020420091.pdf` | `020420191.pdf` |
| martes 9 abr 2019 | 1032 | No. 65 | `090420091.pdf` | `090420191.pdf` |
| martes 23 abr 2019 | 1042 | No. 70 | `230420091.pdf` | `230420191.pdf` |
| martes 30 abr 2019 | 1052 | No. 75 | `300420091.pdf` | `300420191.pdf` |
| martes 28 ene 2020 | 1370 | No. 16 | `280120201.pdf` | el mismo (404 transitorio, bajó al reintentar) |
| lunes 31 ene 2022 | 2018 | No. 15 | `310120221.pdf` | `310120222.pdf` |

Todas las URLs van bajo `https://consultabpj.poderjudicialcdmx.gob.mx:2096/pdf/boletines/`.

Causas:

- Marzo y abril de 2019: el link del portal tiene el año mal escrito (2009 en
  vez de 2019), siempre cada 5 boletines.
- 31 de enero de 2022: el archivo publicado tiene sufijo 2 y el link apunta al
  sufijo 1.

`poc.py` ahora prueba estas variantes solo (`urls_alternativas`) cuando un
link con formato `DDMMAAAA + N.pdf` da 404, así que una corrida desde cero los
baja sin intervención.

## Qué pedir si vuelven a faltar

Si alguno de estos archivos desaparece del portal, la solicitud debería pedir
el PDF íntegro del Boletín Judicial del Poder Judicial de la Ciudad de México
de las fechas de la tabla, con su número de boletín.

## No revisado

- Si el índice del portal omite días hábiles con boletín publicado. Solo se
  revisaron los boletines que el índice lista.
- Boletines anteriores al 3 de octubre de 2016, que el índice del portal no
  incluye.
