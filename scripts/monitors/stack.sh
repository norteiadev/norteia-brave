#!/usr/bin/env bash
# Monitor 1/7 — stack: containers (state/health/restarts), API health, Celery workers + queue.
TAG=stack; . "$(dirname "$0")/_lib.sh"
emit "watching docker compose services + $API (every ${INTERVAL}s)"
while true; do
  for svc in postgres redis api worker beat dashboard; do
    cid=$(docker compose ps -q "$svc" 2>/dev/null)
    if [ -z "$cid" ]; then on_change "svc_$svc" "absent" "$svc: NOT RUNNING"; continue; fi
    st=$(docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}} restarts={{.RestartCount}}' "$cid" 2>/dev/null)
    on_change "svc_$svc" "$st" "$svc: $st"
  done
  h=$(curl -fsS --max-time 5 "$API/api/v1/health" 2>/dev/null || echo "DOWN")
  on_change health "$h" "api health: $h"
  w=$(api /api/v1/workers | jq -r '"broker=\(.broker_reachable) workers=\(.workers|length) active=\([.workers[].active_count]|add // 0) reserved=\([.workers[].reserved_count]|add // 0)"' 2>/dev/null); w=${w:-"workers endpoint unreachable"}
  on_change workers "$w" "celery: $w"
  q=$(rds LLEN celery); q=${q:-?}
  # queue depth is noisy: report only the bucket (0 / 1-9 / 10-99 / 100+)
  case "$q" in 0) b=0;; ?) b="1-9";; ??) b="10-99";; '?') b="?";; *) b="100+";; esac
  on_change queue "$b" "broker queue 'celery' depth: $q ($b)"
  sleep "$INTERVAL"
done
