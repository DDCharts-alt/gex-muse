#!/bin/bash
# Push updated data.json to GitHub Pages repo
# Called after update_data.py refreshes the data

REPO_DIR="$HOME/workspace/gex-muse"
TOKEN_FILE="$REPO_DIR/.github-token"

cd "$REPO_DIR" || exit 1

# Check if data.json changed
if git diff --quiet data.json; then
    echo "No changes to push"
    exit 0
fi

TOKEN=$(cat "$TOKEN_FILE")
git add data.json
git commit -m "Auto-update GEX data $(date -u +%Y-%m-%dT%H:%M:%SZ)" --quiet
git push "https://x-access-token:${TOKEN}@github.com/DDCharts-alt/gex-muse.git" main 2>&1 | head -5
echo "Pushed at $(date)"
