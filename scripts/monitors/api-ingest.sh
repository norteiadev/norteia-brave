#!/usr/bin/env bash
# Monitor 7/7 — the receiving side: norteia-api (ddev) reachability + ingest auth probe
# (empty POST must be 422; 401 = token expired/wrong, 404 = wrong branch), territorial
# row counts in the API's MySQL, and new ERROR lines in laravel.log.
TAG=api-ingest; . "$(dirname "$0")/_lib.sh"
API_DIR="${NORTEIA_API_DIR:-$ROOT/../norteia-api}"
URL="${BRAVE_NORTEIA_API_URL%/}"
INTERVAL="${MONITOR_INTERVAL:-30}"
emit "watching $URL ingest + $API_DIR (every ${INTERVAL}s)"
log="$API_DIR/storage/logs/laravel.log"
lines=$( [ -f "$log" ] && wc -l <"$log" || echo 0 )
while true; do
  h=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$URL/api/v1/health" 2>/dev/null)
  p=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -X POST "$URL/api/internal/territorial/attractions" \
      -H "Authorization: Bearer ${BRAVE_NORTEIA_API_SERVICE_TOKEN:-}" -H 'Accept: application/json' \
      -H 'Content-Type: application/json' -d '{}' 2>/dev/null)
  v="health=$h ingest_probe=$p"
  case "$p" in 422) v="$v (ok)";; 401) v="$v (TOKEN EXPIRED/WRONG)";; 404) v="$v (WRONG BRANCH)";; *) v="$v (API DOWN?)";; esac
  on_change probe "$v" "norteia-api: $v"
  n=$(cd "$API_DIR" && ddev mysql -N -e "select (select count(*) from destinations),(select count(*) from attractions),(select count(*) from attraction_place_details)" 2>/dev/null | tr '\t' '|')
  IFS='|' read -r dst atr apd <<<"$n"
  on_change rows "$n" "api rows: destinations=${dst:-?} attractions=${atr:-?} place_details=${apd:-?}"
  if [ -f "$log" ]; then
    now=$(wc -l <"$log")
    if [ "$now" -lt "$lines" ]; then lines=0; fi   # log rotated/truncated
    if [ "$now" -gt "$lines" ]; then
      sed -n "$((lines+1)),${now}p" "$log" | grep -E '\.(ERROR|CRITICAL|EMERGENCY)' | cut -c1-300 | while read -r l; do emit "laravel: $l"; done
      lines=$now
    fi
  fi
  sleep "$INTERVAL"
done
