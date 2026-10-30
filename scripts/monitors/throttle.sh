#!/usr/bin/env bash
# 429 monitor — arm it next to sweep.sh. Reads the worker's httpx request log
# ("HTTP Request: GET <url> "HTTP/1.1 429 Too Many Requests""), so it covers every client
# (TripAdvisor, Wikimedia, Places, LLMs) with no code change. Quiet: one line per window
# that saw 429s, by host, and a RELATÓRIO with the total every MONITOR_REPORT seconds.
#   429 query.wikidata.org ×2 · www.tripadvisor.com.br ×1 (total 3)
TAG=429; . "$(dirname "$0")/_lib.sh"
INTERVAL="${MONITOR_INTERVAL:-60}"
REPORT="${MONITOR_REPORT:-600}"
PATTERN='HTTP Request: [A-Z]+ https?://[^ ]+ "HTTP/[0-9.]+ 429'

since=$(date -u +%Y-%m-%dT%H:%M:%SZ); total=0; last_report=$(date +%s)
emit "vigiando 429 no worker (por host, a cada ${INTERVAL}s)"
while true; do
  sleep "$INTERVAL"
  until=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  hits=$(docker compose logs --no-color --since "$since" --until "$until" worker 2>/dev/null \
    | perl -pe 's/\e\[[0-9;]*m//g' | grep -E "$PATTERN" \
    | sed -E 's#.*https?://([^/ ]+).*#\1#' | sort | uniq -c | sort -rn)
  since=$until
  if [ -n "$hits" ]; then
    total=$((total + $(awk '{s += $1} END {print s}' <<<"$hits")))
    emit "429 $(awk '{printf "%s%s ×%s", (NR > 1 ? " · " : ""), $2, $1}' <<<"$hits") (total $total)"
  fi
  if [ $(( $(date +%s) - last_report )) -ge "$REPORT" ]; then
    emit "RELATÓRIO 429 total $total"; last_report=$(date +%s)
  fi
done
