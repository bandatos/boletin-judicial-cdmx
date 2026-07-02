#!/usr/bin/env bash
# build.sh — Genera boletin.sqlite.gz desde los CSVs del scraper.
#
# NO descarga PDFs ni hace git push. Asume que el scraper (poc.py) ya
# corrió y dejó los .txt y entradas.csv en scraper/data/YYYY-MM-DD/.
#
# El sitio se sirve desde GitHub Pages en la raíz de la rama main:
# index.html, search.js y boletin.sqlite.gz viven en la raíz del repo.
# Para publicar: commitea boletin.sqlite.gz y haz git push manualmente.
#
# Uso:
#   ./deploy.sh [since]
#   ./deploy.sh 2026-01-01
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

SINCE="${1:-2026-01-01}"
DB="boletin.sqlite"
DB_GZ="boletin.sqlite.gz"

# ── 1. Re-parsear los .txt existentes (sin descargar PDFs) ────────────────────
# Si necesitas re-parsear tras cambiar la lógica de poc.py:
#   python scraper/reparse.py scraper/data/<fecha>
# (se omite por defecto; el CSV ya debería estar generado por el scraper)

# ── 2. Construir SQLite con FTS5 ──────────────────────────────────────────────
echo "=== Build DB ==="
python scraper/build_db.py \
  --dir scraper/data/ \
  --output "$DB" \
  --since "$SINCE"

# ── 3. Comprimir ──────────────────────────────────────────────────────────────
echo ""
echo "=== Compress ==="
gzip -k -f "$DB"
ls -lh "$DB_GZ"

echo ""
echo "=== Listo ==="
echo "Para publicar, commitea y pushea manualmente:"
echo "  git add -f $DB_GZ"
echo "  git commit -m 'Actualizar DB'"
echo "  git push origin main"
echo ""
echo "Sitio: https://bandatos.org/boletin-judicial-cdmx/  (GitHub Pages, rama main, raíz)"
