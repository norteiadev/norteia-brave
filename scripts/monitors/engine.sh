#!/usr/bin/env bash
# Monitor 2/7 — engine lifecycle: state/mode/UF/sync_phase/pause_reason, producers in
# flight, run end, and the runs_history row. Silence here while state=running is normal;
# the run is DONE only when inflight=0 and last_run_ended=1 (ufs_done counts dispatches).
TAG=engine; . "$(dirname "$0")/_lib.sh"
emit "watching engine status + runs_history (every ${INTERVAL}s)"
while true; do
  s=$(api /api/v1/engine/status)
  if [ -z "$s" ]; then on_change status "down" "engine/status UNREACHABLE (api down or bearer wrong)"; sleep "$INTERVAL"; continue; fi
  line=$(jq -r '"state=\(.state) mode=\(.mode) source=\(.source) depth=\(.depth) uf=\(.current_uf // "-") ufs=\(.ufs_done)/\(.ufs_total) sync=\(.sync_phase)"' <<<"$s")
  on_change status "$line"
  pr=$(jq -r '.pause_reason // empty | tostring' <<<"$s")
  on_change pause "$pr" "PAUSE_REASON: ${pr:-cleared}"
  be=$(jq -r '[.beat_errors[]? | tostring] | join("; ")' <<<"$s")
  on_change beat_err "$be" "beat_errors: ${be:-none}"
  inf=$(rds GET brave:engine:producers_inflight); fail=$(rds GET brave:engine:producers_failed)
  dd=$(rds GET brave:engine:dispatch_done); end=$(rds GET brave:engine:last_run_ended)
  on_change prod "$inf/$fail/$dd/$end" "producers inflight=${inf:-0} failed=${fail:-0} dispatch_done=${dd:-0} last_run_ended=${end:-0}"
  r=$(sql "select id, status, source, depth, ufs::text, coalesce(max_per_uf::text,'-'), coalesce(to_char(ended_at,'HH24:MI:SS'),'-') from runs_history order by started_at desc limit 1")
  on_change run "$r" "runs_history: ${r:-none}"
  sleep "$INTERVAL"
done
