#!/usr/bin/env bash
# E2E of the image lane: 5 destinos + 20 atrativos, Brave → norteia-api (DEV) → front (dev-app).
#
#   scripts/e2e_images.sh run                lane + photo push (Brave), then the API check
#   scripts/e2e_images.sh verify             Brave status + API check (conversions, public resource)
#   scripts/e2e_images.sh cleanup [--apply]  remove the test photos from the API and the Brave;
#                                            dry-run without --apply
#
# Needs the docker stack up and `ssh norteia-dev` (see the deploy-watch skill). The entity
# list lives in scripts/e2e_images.py; the API side is scripts/e2e_images_api.php, piped to
# `php` in the DEV api container (nothing is installed on the server).
set -euo pipefail
cd "$(dirname "$0")/.."

brave() { docker compose exec -T worker /app/.venv/bin/python -m scripts.e2e_images "$@"; }
api() { ssh norteia-dev "cd /opt/norteia-dev && docker compose exec -T api php -- $*" < scripts/e2e_images_api.php; }

keys=$(brave keys)
case "${1:-}" in
  run) brave run; echo "--- norteia-api"; api verify "$keys" ;;
  verify) brave status; echo "--- norteia-api"; api verify "$keys" ;;
  cleanup)
    # API first: if it fails, the Brave still holds the rows that say what was sent.
    echo "--- norteia-api"; api cleanup "$keys" "${2:-}"
    echo "--- brave"; brave cleanup "${2:-}" ;;
  *) sed -n '2,10p' "$0"; exit 1 ;;
esac
