#!/usr/bin/env bash
# Monitor 6/7 — TripAdvisor session. A dead session switches the engine to DESLIGADO
# (sweep_tripadvisor_session_fail_fast), so this is the first thing to watch on a TA run.
# Never GETs brave:ta:session (it holds live cookies) — TTL only.
TAG=ta-session; . "$(dirname "$0")/_lib.sh"
emit "watching brave:ta:session TTL + needs_bootstrap + keepalive failures"
while true; do
  ttl=$(rds TTL brave:ta:session); nb=$(rds GET brave:ta:needs_bootstrap); kf=$(rds GET brave:ta:keepalive_failures)
  case "$ttl" in
    -2|'') st="ABSENT — inject a fresh cURL (scripts/ta_bootstrap.py or painel > Origem)";;
    -1)    st="present (no TTL)";;
    *)     if [ "$ttl" -lt 300 ]; then st="EXPIRING (<5 min)"; else st="alive"; fi;;
  esac
  on_change sess "$st" "session: $st (ttl=${ttl}s)"
  # Same rule as GET /tripadvisor/session/status: the marker only means "dead" while the
  # session is absent (nothing clears it on re-inject, so it goes stale under a live session).
  if [ "${nb:-0}" = 1 ] && { [ "$ttl" = -2 ] || [ -z "$ttl" ]; }; then nbs="SESSION DEAD — re-inject before (re)starting the engine"; else nbs="ok"; fi
  on_change nb "$nbs" "needs_bootstrap: $nbs"
  on_change kf "${kf:-0}" "keepalive_failures=${kf:-0}"
  sleep "$INTERVAL"
done
