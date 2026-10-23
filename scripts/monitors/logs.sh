#!/usr/bin/env bash
# Monitor 4/7 — worker/beat/api log events that mark a stage boundary or a failure.
# Per-record chatter (nascente_ingerido, registro_roteado, places_*_ok, Celery
# "succeeded") is dropped on purpose; stage + failure signals are kept, including
# crash signatures (Traceback, Killed, WorkerLostError), so silence ≠ success only
# when the engine monitor also says the run is still going.
TAG=logs; . "$(dirname "$0")/_lib.sh"
PATTERN='engine_|ta_atrativos_producer|ta_paginated_gql_page|sweep_tripadvisor_|ta_rate_limited|ta_keepalive_(session_expired|error)|ta_session_(injected|ttl_slide_error|writeback_error)|mark_needs_bootstrap|places_[a-z_]*_error|places_locate_failed|places_enrich_(failed|hard|dlq)|places_enrichment_disabled|inline_enrichment_build_failed|nominatim_geocode_failed|task_provider_balance|task_daily_budget|cost_guard|provider_balance|describe_uf|description_attempts_exhausted|copywriter_failed|ta_backfill_(batch|started|auto_resumed|schema_error|consecutive_failures|record_failed|disabled)|publish_|norteia_api_down|repush_pending_mar_dispatched|promote_bulk|Traceback|ERROR|CRITICAL|[A-Za-z]+Error:|raised unexpected|Retry in|Killed|SIGKILL|WorkerLostError|TimeLimitExceeded|exited with|celery@.* ready'
emit "following worker/beat/api logs (stage + failure events only)"
since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
while true; do
  docker compose logs -f --no-color --since "$since" worker beat api 2>&1 \
    | grep -E --line-buffered "$PATTERN" \
    | grep -Ev --line-buffered 'GET /api/v1/(health|engine/status|workers|metrics|logs|cost)' \
    | perl -ne 'BEGIN { $| = 1; use POSIX qw(strftime) } s/\e\[[0-9;]*m//g; chomp; print strftime("%H:%M:%S", localtime), " [logs] ", substr($_, 0, 320), "\n"'
  since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  emit "log stream ended (containers stopped/restarted?) — reattaching in 10s"
  sleep 10
done
