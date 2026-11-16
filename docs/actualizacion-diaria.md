# Actualización diaria

`scraper/diario.py` suma a la base publicada los boletines que salieron desde el último cargado, la publica si pasa los chequeos y manda un resumen por correo. Corre como tarea programada de Coolify dentro del contenedor del sitio, que ya tiene montado `/data`.

## Qué hace

1. Lee la fecha del último boletín con avisos en `/data/boletin.sqlite`.
2. Baja con el scraper en Rust (`scraper/boletin_rs`, binario `poc`) el índice y los boletines desde el día siguiente hasta hoy, en `/data/scraper-data/<hoy>/`. Borra los PDF y deja el texto.
3. Si no hay boletines nuevos, termina y avisa ("sin boletines nuevos en el portal").
4. Copia la base a `/data/trabajo/` y corre `build_db.py --incremental` sobre la copia (el cálculo por aviso lo hace el binario `precalc` de Rust), con poca memoria (`BUILD_CACHE_MB=300`, etapas de a una, 4 procesos).
5. Chequea que ninguna cantidad baje (avisos, boletines, empresas, personas, vocabulario, sentencias con versión pública) y que haya avisos nuevos.
6. Publica con un rename: `boletin.sqlite.anterior` queda como respaldo (hardlink) y el sitio recarga la base solo en ~30 s.
7. Comprime los textos del día a `.txt.gz`.
8. Manda el resumen: boletines nuevos con su cantidad de avisos, avisos por actuación y tipo de acuerdo, cantidades antes y después, tiempo de cada paso y de cada etapa del build, tamaño de la base. Si algo falla, el asunto dice ERROR y el correo trae el error y las últimas líneas del log.

Si la corrida se corta (por ejemplo, un deploy a mitad de camino), la base publicada no se toca y la próxima corrida borra los restos de `/data/trabajo/`. Un candado impide dos corridas a la vez.

## Configuración en Coolify

En la aplicación, **Scheduled Tasks → Add**:

- Comando: `python3 /app/scraper/diario.py`
- Frecuencia: `30 14 * * *` si el server de Coolify está en UTC (8:30 en CDMX, que no tiene horario de verano). Si la zona horaria configurada es `America/Mexico_City`, `30 8 * * *`.
- Si la tarea tiene campo de timeout, ponerlo en 7200 s.

Variables de entorno de la aplicación (secretas), para el correo: `MSMTP_HOST`, `MSMTP_PORT`, `MSMTP_USER`, `MSMTP_PASSWORD`, `MSMTP_FROM` y `RESUMEN_PARA` (destinatarios, separados por coma). Qué cuenta usar y de dónde sacar los valores: `private-docs/actualizacion-diaria-config.md`.

Opcionales: `BUILD_CACHE_MB` (300), `BUILD_ETAPAS_A_LA_VEZ` (1), `BUILD_WORKERS` (4), `SIVEP_DB` (`/data/sivep.sqlite`).

## Primera vez

El incremental necesita la base armada con el código actual (v6 en adelante: guarda todas las detecciones de empresas y personas en `partes_nombres` y `partes_avisos`):

- `/data/boletin.sqlite`
- `/data/sivep.sqlite` (copia de `scraper/data/sivep/sivep.sqlite`, para los enlaces a versiones públicas)

## Probar a mano

Dentro del contenedor:

```
python3 /app/scraper/diario.py --sin-correo --sin-publicar
```

`--sin-publicar` deja la base nueva en `/data/trabajo/` sin publicarla. `--desde AAAA-MM-DD` fuerza la fecha inicial del índice.

## Espacio

Durante la corrida conviven la base publicada, la copia de trabajo y la anterior: ~50 GB.

## Pendiente

Las versiones públicas del SIVEP no se cosechan en la corrida diaria. Los enlaces se recalculan con el `sivep.sqlite` que haya en `/data`.
