# boletin-judicial-cdmx

El Poder Judicial de la Ciudad de México publica diariamente el Boletín Judicial: cientos de notificaciones sobre acuerdos en juzgados civiles, familiares, laborales y de derechos humanos. Es el único mecanismo oficial para que ciudadanos y litigantes se enteren de que su caso tuvo movimiento.

El problema: solo existe como PDF. No hay búsqueda por nombre, expediente ni tipo de juicio.

Este proyecto construye un pipeline que descarga, parsea y estructura el historial completo del boletín (2017 a la fecha), lo hace buscable por nombre y tipo de juicio, y lo correlaciona con el dataset de Carpetas de Investigación de la FGJ-CDMX.

## Estado

PoC del scraper funcionando. Ver [PROYECTO.md](PROYECTO.md) para el diseño completo y [scraper/PARSEO.md](scraper/PARSEO.md) para las notas técnicas de parseo.

## Correr el PoC

Requisitos: Python 3.10+, `pdftotext` (`poppler-utils`), y las dependencias de Python.

```bash
# Dependencias del sistema (Debian/Ubuntu)
sudo apt install poppler-utils

# Dependencias de Python
pip install requests beautifulsoup4

# Correr el PoC
cd scraper
python poc.py [fecha_inicio] [fecha_fin] [max_boletines]

# Ejemplos
python poc.py 2026-06-10 2026-06-10        # un boletín
python poc.py 2026-06-01 2026-06-10 5      # hasta 5 boletines del rango
```

La salida se guarda en `data/YYYY-MM-DD/`:

```
data/2026-06-10/
├── index.json      # lista de boletines del rango consultado
├── pdfs/           # PDFs descargados
│   └── boletin_4002.pdf
├── entradas.csv    # entradas parseadas
└── resumen.json    # métricas de cobertura del parseo
```

## Fuentes

- [Boletín Judicial PJCDMX](https://consultabpj.poderjudicialcdmx.gob.mx:2096/consultaboletinpjcdmx)
- [Carpetas de investigación FGJ-CDMX](https://datos.cdmx.gob.mx/dataset/carpetas-de-investigacion-fgj-de-la-ciudad-de-mexico)

## Parte de

[Bandatos](https://bandatos.github.io) — comunidad de datos abiertos de la Ciudad de México.
