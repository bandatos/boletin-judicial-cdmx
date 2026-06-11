#!/usr/bin/env bash
# deploy.sh — Genera el SQLite y despliega en GitHub Pages + Releases.
# Corre esto después de que poc.py haya terminado.
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

SINCE="${1:-2026-01-01}"
DB="boletin.sqlite"
DB_GZ="boletin.sqlite.gz"
RELEASE_TAG="data"
REPO="bandatos/boletin-judicial-cdmx"

# ── Build DB ──────────────────────────────────────────────────────────────────
echo "=== Build DB ==="
python scraper/build_db.py \
  --dir scraper/data/ \
  --output "$DB" \
  --since "$SINCE"

# ── Compress ──────────────────────────────────────────────────────────────────
echo ""
echo "=== Compress ==="
gzip -k -f "$DB"
ls -lh "$DB_GZ"

# ── Upload to GitHub Releases ─────────────────────────────────────────────────
echo ""
echo "=== Upload to GitHub Releases ==="
gh release create "$RELEASE_TAG" \
  --repo "$REPO" \
  --title "Datos — $(date +%Y-%m-%d)" \
  --notes "Base de datos generada localmente al $(date +%Y-%m-%d)." \
  2>/dev/null || true
gh release upload "$RELEASE_TAG" "$DB_GZ" --repo "$REPO" --clobber
echo "Release: https://github.com/$REPO/releases/tag/$RELEASE_TAG"

# ── Deploy GitHub Pages (gh-pages branch) ─────────────────────────────────────
echo ""
echo "=== Deploy GitHub Pages ==="

PAGES_TMP=$(mktemp -d)
trap "rm -rf $PAGES_TMP" EXIT

git -C "$PAGES_TMP" init -q
git -C "$PAGES_TMP" checkout -b gh-pages

cp web/index.html "$PAGES_TMP/"
cp web/search.js  "$PAGES_TMP/"

# GitHub Pages necesita un archivo en la raíz
touch "$PAGES_TMP/.nojekyll"

git -C "$PAGES_TMP" add -A
git -C "$PAGES_TMP" -c user.name="Bandatos Bot" \
    -c user.email="bot@bandatos.github.io" \
    commit -m "Deploy $(date +%Y-%m-%d)"

# Obtener la URL SSH del remote
REMOTE_URL=$(git remote get-url origin)
git -C "$PAGES_TMP" remote add origin "$REMOTE_URL"
git -C "$PAGES_TMP" push origin gh-pages --force

echo ""
echo "=== Listo ==="
echo "Sitio:   https://bandatos.github.io/boletin-judicial-cdmx/"
echo "DB:      https://github.com/$REPO/releases/download/$RELEASE_TAG/$DB_GZ"
