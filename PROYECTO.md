# Bandatos

## Boletín Judicial CDMX

10 de junio de 2026

**Estado:** En la mesa
**Responsables:**
**Participantes:**

---

## Resumen de contexto

El Poder Judicial de la Ciudad de México publica diariamente el Boletín Judicial del PJCDMX: un documento con todas las notificaciones y acuerdos del día en juzgados civiles, familiares, laborales y de derechos humanos. Es el único mecanismo oficial para que ciudadanos y litigantes se enteren de que su caso tuvo movimiento.

El problema: el boletín solo existe como PDF. El portal oficial permite filtrar por fecha, pero no tiene búsqueda por nombre, expediente ni tipo de juicio. Para saber si apareces, hay que descargar un PDF de 376 páginas y buscar manualmente. El sistema lleva publicando boletines desde 2017 y no hay forma de consultar el historial de manera estructurada.

El dataset de Carpetas de Investigación de la FGJ-CDMX registra todos los delitos denunciados desde 2016, con tipo de delito, fecha, fiscalía y alcaldía. Combinado con los datos del boletín, permite entender la brecha entre lo que se denuncia y lo que llega a los juzgados.

---

## Objetivos

- Hacer buscable el historial del Boletín Judicial por nombre de persona o empresa y tipo de juicio.
- Correlacionar los expedientes judiciales con el dataset de carpetas de investigación de la FGJ para visualizar la relación entre delitos reportados y casos procesados por alcaldía y período.

---

## Descripción general

El proyecto tiene tres partes:

**1. Pipeline de datos**
Scraper que recorre el índice del portal TSJ (filtro por fechas vía POST) para obtener la lista de boletines y sus URLs de PDF. Descarga todos los PDFs y los parsea con `pdftotext`. Un parser basado en expresiones regulares extrae de cada boletín las entradas estructuradas: partes actora y demandada, tipo de juicio, juzgado, sala, número de expediente y número de acuerdos. Las entradas van a PostgreSQL con un índice de búsqueda de texto completo en español (`tsvector`). Paralelo a esto, un loader descarga los CSVs anuales de carpetas FGJ y los carga en la misma base de datos.

**2. API**
FastAPI con tres endpoints: búsqueda por nombre o texto libre, consulta por expediente, y estadísticas de correlación por alcaldía y período.

**3. Frontend**
Interfaz de búsqueda accesible: caja de texto, filtro por tipo de juicio, resultados con expediente, partes, juzgado, fecha y liga al PDF original. Panel secundario con contexto FGJ para la alcaldía y período del caso.

El corpus inicial es de aproximadamente 2000 boletines (2017-2026), con un promedio de 376 páginas y cientos de entradas por día.

---

## Habilidades necesarias

- Python (scraping, parsing de PDFs, ETL)
- PostgreSQL (full-text search en español)
- FastAPI o equivalente
- HTML/CSS o framework frontend ligero
- Periodismo de datos (para afinar la capa de correlación y darle sentido editorial)

---

## Necesidades materiales

- Servidor o VPS con PostgreSQL y almacenamiento para PDFs (~15 GB estimado para el histórico completo)
- Acceso al portal del TSJ durante el scraping inicial

---

## Actualizaciones semanales

### 10 de junio de 2026

Sesión inicial de diseño. Se exploró el portal del TSJ: el índice es accesible vía POST con rango de fechas, los PDFs están en un CDN externo sin autenticación. `pdftotext` extrae el texto a pesar del cifrado AES-256 del PDF. La estructura de las entradas es consistente y parseable con regex. Se identificó el dataset FGJ como fuente para correlación estadística por alcaldía y tipo de delito. Se definió arquitectura C (parsing estructurado + full-text fallback, sin OpenSearch). Decisiones pendientes: tecnología frontend, hosting, modelo de participación en Bandatos.
