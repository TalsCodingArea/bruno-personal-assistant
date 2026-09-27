#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

if [[ ! -f .env ]]; then
  echo "Missing $project_dir/.env. Copy .env.example and add secrets locally first." >&2
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Tracked files have local changes. Commit or restore them before updating." >&2
  exit 1
fi

git pull --ff-only origin main
docker compose config --quiet
docker compose build --pull
docker compose up -d --remove-orphans
docker compose ps
