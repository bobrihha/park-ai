#!/usr/bin/env bash
set -euo pipefail

# Simple rsync deploy with safe excludes.
SRC="${1:-.}"
DEST="${2:-root@95.81.99.32:/root/jungle_bot/}"

rsync -az --delete \
  --exclude '.git' \
  --exclude '.env' \
  --exclude 'data/bot.db' \
  --exclude 'data/amocrm_tokens.json' \
  --exclude 'data/chroma' \
  --exclude 'venv' \
  --exclude 'node_modules' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  "${SRC%/}/" "${DEST}"
