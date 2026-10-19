#!/usr/bin/env bash
# Monitor 7/7 — the receiving side: norteia-api reachability + ingest auth probe
# (empty POST must be 422; 401 = token expired/wrong, 404 = wrong branch), territorial
# row counts in the API's MySQL, and new ERROR lines from Laravel.
#
# Target follows BRAVE_NORTEIA_API_URL:
#   *.ddev.site  → local ddev (ddev mysql + storage/logs/laravel.log)
#   anything else → DEV VPS over ssh "$NORTEIA_API_SSH" (default norteia-dev): MySQL in
#                   dev-norteia-mysql, logs on stderr of dev-norteia-api + dev-norteia-queue
TAG=api-ingest; . "$(dirname "$0")/_lib.sh"
API_DIR="${NORTEIA_API_DIR:-$ROOT/../norteia-api}"
SSH_HOST="${NORTEIA_API_SSH:-norteia-dev}"
URL="${BRAVE_NORTEIA_API_URL%/}"
INTERVAL="${MONITOR_INTERVAL:-30}"
COUNTS_SQL="select (select count(*) from destinations),(select count(*) from attractions),(select count(*) from attraction_place_details)"
case "$URL" in *ddev.site*) MODE=ddev;; *) MODE=ssh;; esac

rows() {
  if [ "$MODE" = ddev ]; then
    (cd "$API_DIR" && ddev mysql -N -e "$COUNTS_SQL" 2>/dev/null)
  else
    echo "$COUNTS_SQL" | ssh -o ConnectTimeout=10 "$SSH_HOST" \
      'docker exec -i dev-norteia-mysql sh -c "mysql -uroot -p\"\$MYSQL_ROOT_PASSWORD\" -N \"\$MYSQL_DATABASE\"" 2>/dev/null'
  fi | tr '\t' '|'
}

log="$API_DIR/storage/logs/laravel.log"
lines=$( [ -f "$log" ] && wc -l <"$log" || echo 0 )
since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
new_errors() {  # prints new ERROR/CRITICAL lines since the previous call
  if [ "$MODE" = ddev ]; then
    [ -f "$log" ] || return
    local now; now=$(wc -l <"$log")
    [ "$now" -lt "$lines" ] && lines=0   # rotated/truncated
    [ "$now" -gt "$lines" ] && sed -n "$((lines+1)),${now}p" "$log" | grep -E '\.(ERROR|CRITICAL|EMERGENCY)'
    lines=$now
  else
    local next; next="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    ssh -o ConnectTimeout=10 "$SSH_HOST" \
      "for c in dev-norteia-api dev-norteia-queue; do docker logs --since $since \$c 2>&1 | sed \"s/^/\$c: /\"; done" 2>/dev/null \
      | grep -E '\.(ERROR|CRITICAL|EMERGENCY)|"level_name":"(ERROR|CRITICAL)"'
    since=$next
  fi
}

emit "watching $URL ($MODE) ingest + rows + errors (every ${INTERVAL}s)"
while true; do
  h=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$URL/api/v1/health" 2>/dev/null)
  p=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -X POST "$URL/api/internal/territorial/attractions" \
      -H "Authorization: Bearer ${BRAVE_NORTEIA_API_SERVICE_TOKEN:-}" -H 'Accept: application/json' \
      -H 'Content-Type: application/json' -d '{}' 2>/dev/null)
  v="health=$h ingest_probe=$p"
  case "$p" in 422) v="$v (ok)";; 401) v="$v (TOKEN EXPIRED/WRONG)";; 404) v="$v (WRONG BRANCH)";; *) v="$v (API DOWN?)";; esac
  on_change probe "$v" "norteia-api: $v"
  n=$(rows)
  IFS='|' read -r dst atr apd <<<"$n"
  on_change rows "$n" "api rows: destinations=${dst:-?} attractions=${atr:-?} place_details=${apd:-?}"
  new_errors | cut -c1-300 | while read -r l; do emit "laravel: $l"; done
  sleep "$INTERVAL"
done
