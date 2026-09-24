# boletin-judicial-cdmx

El Poder Judicial de la Ciudad de México publica diariamente el Boletín Judicial: cientos de notificaciones sobre acuerdos en juzgados civiles, familiares, laborales y de derechos humanos. Es el único mecanismo oficial para que ciudadanos y litigantes se enteren de que su caso tuvo movimiento.

El problema: solo existe como PDF. No hay búsqueda por nombre, expediente ni tipo de juicio.

Este proyecto construye un pipeline que descarga, parsea y estructura el historial completo del boletín (2017 a la fecha), lo hace buscable por nombre y tipo de juicio, y lo correlaciona con el dataset de Carpetas de Investigación de la FGJ-CDMX.

## Demo

**[bandatos.org/boletin-judicial-cdmx](https://bandatos.org/boletin-judicial-cdmx/)** — búsqueda y navegación por fecha de los boletines de 2026 (~2.3 millones de entradas), 100% en el browser.

## Estado

Demo desplegada con 157 boletines de 2026 (del 7 de enero al 22 de septiembre). El histórico completo (oct. 2016 en adelante) se scrapea en local para el server. Ver [PROYECTO.md](PROYECTO.md) para el diseño completo, [ARQUITECTURA.md](ARQUITECTURA.md) para el deploy y [scraper/PARSEO.md](scraper/PARSEO.md) para las notas técnicas de parseo.

## Correr el scraper

Requisitos: Python 3.10+, `pdftotext` (`poppler-utils`), y las dependencias de Python.

```bash
# Dependencias del sistema (Debian/Ubuntu)
sudo apt install poppler-utils

# Dependencias de Python
pip install requests beautifulsoup4

# Correr el scraper
cd scraper
python poc.py [fecha_inicio] [fecha_fin] [max_boletines]

# Ejemplos
python poc.py 2026-06-10 2026-06-10        # un boletín
python poc.py 2026-06-01 2026-06-10 5      # hasta 5 boletines del rango
python poc.py 2026-01-01 2026-06-10 9999   # todo el rango
```

La salida se guarda en `data/YYYY-MM-DD/`:

```
data/2026-06-10/
├── index.json      # lista de boletines del rango consultado
├── pdfs/           # PDFs y su versión .txt
│   ├── boletin_4002.pdf
│   └── boletin_4002.txt
├── entradas.csv    # entradas parseadas
└── resumen.json    # métricas de cobertura del parseo
```

Si cambias la lógica de parseo, re-parsea sin volver a descargar:

```bash
python reparse.py data/2026-06-10
```

## Construir la base de datos y desplegar

```bash
# Genera boletin.sqlite (FTS5), lo comprime y lo parte en db/ + boletin.sqlite.json
./deploy.sh 2026-01-01

# Publicar: commitea los trozos y el manifiesto a main; GitHub Pages (rama main, raíz) los sirve
git add -A db boletin.sqlite.json
git commit -m "Actualizar DB"
git push origin main
```

El argumento de `deploy.sh` es la fecha de publicación del primer boletín que entra en la DB del browser (el histórico completo no entra). Toma los boletines de cualquier run en `scraper/data/` y, si un boletín está en varios, usa el del run más nuevo.

El sitio (`index.html`, `search.js`, `boletin.sqlite.json`, `db/`) vive en la raíz de `main`. GitHub rechaza archivos de más de 100 MB, así que el `.gz` se parte en trozos de 90 MB y `search.js` los concatena. GitHub Pages está configurado como *Deploy from a branch* → `main` / `/ (root)`. La DB se sirve desde el mismo origen (no Releases) para evitar problemas de CORS.

## Fuentes

- [Boletín Judicial PJCDMX](https://consultabpj.poderjudicialcdmx.gob.mx:2096/consultaboletinpjcdmx)
- [Carpetas de investigación FGJ-CDMX](https://datos.cdmx.gob.mx/dataset/carpetas-de-investigacion-fgj-de-la-ciudad-de-mexico)

## Parte de

[Bandatos](https://bandatos.github.io) — comunidad de datos abiertos de la Ciudad de México.
