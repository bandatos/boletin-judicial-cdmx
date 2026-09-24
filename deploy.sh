#!/usr/bin/env bash
# build.sh — Genera la DB publicable (boletin.sqlite.gz partido en trozos)
# desde los CSVs del scraper.
#
# NO descarga PDFs ni hace git push. Asume que el scraper (poc.py) ya
# corrió y dejó los .txt y entradas.csv en scraper/data/YYYY-MM-DD/.
#
# El sitio se sirve desde GitHub Pages en la raíz de la rama main:
# index.html, search.js, boletin.sqlite.json y db/ viven en la raíz del repo.
# GitHub rechaza archivos de más de 100 MB, así que el .gz se parte en
# trozos de 90 MB (db/boletin.sqlite.gz.000, .001, ...) y
# boletin.sqlite.json lista los trozos y la versión. search.js los baja
# y los concatena antes de descomprimir.
# Para publicar: commitea db/ y boletin.sqlite.json y haz git push manualmente.
#
# Uso:
#   ./deploy.sh [since]
#   ./deploy.sh 2026-01-01
#
# EXCLUDE: runs a omitir, separados por espacio. Por defecto el run
# histórico 2026-07-02 (ago. 2022 a jul. 2026, ~13M entradas): no entra
# en la DB del browser y además repite los boletines del run 2026-06-10.
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

SINCE="${1:-2026-01-01}"
EXCLUDE="${EXCLUDE-2026-07-02}"
DB="boletin.sqlite"
DB_GZ="boletin.sqlite.gz"
PARTS_DIR="db"
MANIFEST="boletin.sqlite.json"
PART_SIZE="90M"

# ── 1. Re-parsear los .txt existentes (sin descargar PDFs) ────────────────────
# Si necesitas re-parsear tras cambiar la lógica de poc.py:
#   python scraper/reparse.py scraper/data/<fecha>
# (se omite por defecto; el CSV ya debería estar generado por el scraper)

# ── 2. Construir SQLite con FTS5 ──────────────────────────────────────────────
echo "=== Build DB ==="
EXCLUDE_ARGS=()
for run in $EXCLUDE; do EXCLUDE_ARGS+=(--exclude "$run"); done
python scraper/build_db.py \
  --dir scraper/data/ \
  --output "$DB" \
  --since "$SINCE" \
  "${EXCLUDE_ARGS[@]}"

# ── 3. Comprimir y partir ─────────────────────────────────────────────────────
echo ""
echo "=== Compress ==="
gzip -k -f "$DB"
ls -lh "$DB_GZ"

rm -rf "$PARTS_DIR"
mkdir -p "$PARTS_DIR"
split -b "$PART_SIZE" -d -a 3 "$DB_GZ" "$PARTS_DIR/$DB_GZ."

VERSION="$(sha256sum "$DB_GZ" | cut -c1-16)"
python - "$PARTS_DIR" "$MANIFEST" "$VERSION" <<'EOF'
import json, sys
from pathlib import Path
parts_dir, manifest, version = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
parts = sorted(parts_dir.iterdir())
json.dump({
    "version": version,
    "size": sum(p.stat().st_size for p in parts),
    "parts": [str(p) for p in parts],
}, open(manifest, "w"), indent=2)
EOF
ls -lh "$PARTS_DIR"
cat "$MANIFEST"

echo ""
echo "=== Listo ==="
echo "Para publicar, commitea y pushea manualmente:"
echo "  git add -A $PARTS_DIR $MANIFEST"
echo "  git commit -m 'Actualizar DB'"
echo "  git push origin main"
echo ""
echo "Sitio: https://bandatos.org/boletin-judicial-cdmx/  (GitHub Pages, rama main, raíz)"
