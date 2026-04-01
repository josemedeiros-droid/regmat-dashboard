#!/bin/bash
# Copia demands.json do scanner para o repo e faz push ao GitHub Pages.
# Chamado automaticamente pelo scanner após cada scan.

SCANNER_DIR="$HOME/Cursor/regulatory-matters-pilot/03-automacao/scanner"
REPO_DIR="$HOME/regmat-dashboard"

cp "$SCANNER_DIR/demands.json" "$REPO_DIR/data.json" 2>/dev/null || exit 0

cd "$REPO_DIR" || exit 1

if git diff --quiet data.json 2>/dev/null; then
  exit 0
fi

git add data.json
git commit -m "update $(date '+%Y-%m-%d %H:%M')" --quiet
git push --quiet 2>/dev/null
