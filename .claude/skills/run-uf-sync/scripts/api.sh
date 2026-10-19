#!/usr/bin/env bash
# Authenticated call to the local Brave API.
#   api.sh GET  /api/v1/engine/status
#   api.sh POST /api/v1/engine/start '{"source":"tripadvisor","depth":"nascente_rio","ufs":["SE"]}'
# Bearer comes from the repo .env (BRAVE_DASHBOARD_BEARER_TOKEN); prints body + HTTP status.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
set -a; . "$ROOT/.env"; set +a
method="${1:?method}"; path="${2:?path}"; body="${3:-}"
args=(-sS --max-time 120 -X "$method" -H "Authorization: Bearer ${BRAVE_DASHBOARD_BEARER_TOKEN}" -w '\nHTTP %{http_code}\n')
[ -n "$body" ] && args+=(-H 'Content-Type: application/json' -d "$body")
curl "${args[@]}" "http://localhost:${BRAVE_API_PORT:-8000}${path}"
