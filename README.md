# boletin-judicial-cdmx

El Poder Judicial de la Ciudad de México publica diariamente el Boletín Judicial: cientos de notificaciones sobre acuerdos en juzgados civiles, familiares, laborales y de derechos humanos. Es el único mecanismo oficial para que ciudadanos y litigantes se enteren de que su caso tuvo movimiento.

El problema: solo existe como PDF. No hay búsqueda por nombre, expediente ni tipo de juicio.

Este proyecto construye un pipeline que descarga, parsea y estructura el historial completo del boletín (2017 a la fecha), lo hace buscable por nombre y tipo de juicio, y lo correlaciona con el dataset de Carpetas de Investigación de la FGJ-CDMX.

## Estado

En desarrollo activo. Ver [PROYECTO.md](PROYECTO.md) para el diseño completo.

## Fuentes

- [Boletín Judicial PJCDMX](https://consultabpj.poderjudicialcdmx.gob.mx:2096/consultaboletinpjcdmx)
- [Carpetas de investigación FGJ-CDMX](https://datos.cdmx.gob.mx/dataset/carpetas-de-investigacion-fgj-de-la-ciudad-de-mexico)

## Parte de

[Bandatos](https://bandatos.github.io) — comunidad de datos abiertos de la Ciudad de México.
