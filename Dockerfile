# Imagen del server (Node + Pug + SQLite).
#
# La base (~15 GB) y los textos de los boletines (~1.5 GB) NO entran en la
# imagen: se montan en /data como volumen persistente.
#   /data/boletin.sqlite   base armada con build_db.py --indices-server --empresas --personas
#   /data/scraper-data/    <run>/pdfs/boletin_<id>.txt(.gz), para el texto original
# Otras rutas con DB_PATH y SCRAPER_DATA_DIR.

FROM node:24-bookworm-slim AS deps
WORKDIR /app
# Herramientas por si better-sqlite3 no tiene binario precompilado.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 make g++ \
 && rm -rf /var/lib/apt/lists/*
COPY server/package.json server/package-lock.json ./
RUN npm ci --omit=dev

FROM node:24-bookworm-slim
ENV NODE_ENV=production \
    PORT=3000 \
    HOST=0.0.0.0 \
    DB_PATH=/data/boletin.sqlite \
    SCRAPER_DATA_DIR=/data/scraper-data
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY server/ ./
# static-server.js escribe los .min.css/.min.js al lado de los originales.
RUN chown -R node:node /app/static
USER node
EXPOSE 3000
# Al arrancar se calculan los agregados (~20 s con la base completa).
# Coolify espera el start-period entero antes del primer chequeo: con 10 min
# cada deploy tardaba más de 10 min y se encolaban.
HEALTHCHECK --interval=10s --timeout=5s --start-period=90s --retries=6 \
  CMD node -e "fetch('http://127.0.0.1:'+process.env.PORT+'/health').then(r=>process.exit(r.ok?0:1),()=>process.exit(1))"
CMD ["node", "litigios-cdmx.js"]
