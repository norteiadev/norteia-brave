#!/usr/bin/env bash
# Sweep monitor — the one to arm in Claude Code (also the describe run: MONITOR_SOURCE=descricao) (the other seven stay for tmux/debug).
# Quiet by design: a RELATÓRIO line every MONITOR_REPORT seconds (default 600) and when
# the run ends, plus one FALHA line per NEW failure. Exits after the final report, so the
# Monitor stream ends with the run.
#   RELATÓRIO run=1a2b3c4d running · SC pág 81/134 · atrativos 1781 (+320) · destinos 120 (+12) · falhas 0
# Page "x" is the TA listing size, read from the first page's totalResults
# (Redis brave:ta:sweep:uf_pages, written by the per-UF producer).
TAG=sweep; . "$(dirname "$0")/_lib.sh"
INTERVAL="${MONITOR_INTERVAL:-30}"
REPORT="${MONITOR_REPORT:-600}"
SOURCE="${MONITOR_SOURCE:-tripadvisor}"  # descricao = the describe run
START_EPOCH=$(date +%s)
START_SQL=$(date -u +%Y-%m-%dT%H:%M:%SZ)
LOG_PATTERN='\[(error|critical) *\]|: (ERROR|CRITICAL)/|Traceback|WorkerLostError|SIGKILL|TimeLimitExceeded|exited with'

fails=0
fail() { fails=$((fails + 1)); emit "FALHA $*"; }
fail_once() { # fail_once KEY VALUE message — only when VALUE is new and non-empty
  local f="$_STATE/fail_$1"
  if [ -n "$2" ] && { [ ! -f "$f" ] || [ "$(cat "$f")" != "$2" ]; }; then
    printf '%s' "$2" > "$f"; fail "$3"
  elif [ -z "$2" ]; then rm -f "$f"; fi
}

counts() { # "<atrativos>|<destinos>" for the run's UFs
  sql "select count(*) filter (where entity_type='attraction'), count(*) filter (where entity_type='destination') from rio_records where uf in ($UFS_SQL)"
}

report() { # report [final]
  local p pages="" uf c a d
  p=$(rds HGETALL brave:ta:sweep:uf_pages | paste - -)
  [ "$SOURCE" = descricao ] || for uf in $UFS; do  # pages are the sweep's, stale for describe
    pages="$pages · $uf pág $(awk -v u="$uf" '$1==u{print $2}' <<<"$p" | grep . || echo '-')"
  done
  c=$(counts); a=${c%|*}; d=${c#*|}
  emit "RELATÓRIO${1:+ final} run=${RUN_ID:0:8} $RUN_STATUS$pages · atrativos $a (+$((a - A0))) · destinos $d (+$((d - D0))) · falhas $fails"
}

# Wait for the run: the newest runs_history row that is running or started after us.
emit "aguardando run $SOURCE (relatório a cada ${REPORT}s, falhas na hora)"
while true; do
  r=$(sql "select id, status, ufs::text from runs_history where source='$SOURCE' and (status='running' or started_at >= '$START_SQL') order by started_at desc limit 1")
  [ -n "$r" ] && break
  sleep "$INTERVAL"
done
IFS='|' read -r RUN_ID RUN_STATUS UFS_JSON <<<"$r"
# engine/start does not validate ufs: keep only 2-letter codes before they reach the SQL.
UFS_JSON=$(jq -c '[.[]? | strings | select(test("^[A-Z]{2}$"))]' <<<"$UFS_JSON" 2>/dev/null || echo '[]')
UFS=$(jq -r 'join(" ")' <<<"$UFS_JSON")
UFS_SQL=$(jq -r 'map("'"'"'" + . + "'"'"'") | join(",") | if . == "" then "NULL" else . end' <<<"$UFS_JSON")
c=$(counts); A0=${c%|*}; D0=${c#*|}
PF0=$(rds GET brave:engine:producers_failed); PF0=${PF0:-0}
emit "run ${RUN_ID:0:8} ufs=[$UFS] — base atrativos $A0, destinos $D0"

last_report=$START_EPOCH; last_logs=$START_SQL; log_hold=0
while true; do
  s=$(api /api/v1/engine/status)
  if [ -z "$s" ]; then
    fail_once api "down" "api: engine/status inacessível (api caiu ou bearer errado)"
  else
    fail_once api "" ""
    fail_once pause "$(jq -r '.pause_reason // empty | tostring' <<<"$s")" "motor pausado: $(jq -r '.pause_reason | tostring' <<<"$s")"
    # Since this monitor started (the keys live 7 days), keyed without "at": a beat that
    # keeps failing the same way is one FALHA, not one per run of the beat.
    be=$(jq -r --arg s "${START_SQL:0:19}" '[.beat_errors[]? | select((.at // "")[0:19] >= $s) | "\(.task): \(.error_type)"] | join("; ")' <<<"$s")
    fail_once beat "$be" "beat_errors: $be"
    fail_once napi "$(jq -r 'if .norteia_api.up == false then (.norteia_api.reason // "down") else empty end' <<<"$s")" "norteia-api fora: $(jq -r '.norteia_api.reason // "down"' <<<"$s")"
  fi
  pf=$(rds GET brave:engine:producers_failed); pf=${pf:-0}
  [ "$pf" -gt "$PF0" ] && { fail "produtor falhou (producers_failed $PF0 → $pf)"; PF0=$pf; }

  if [ "$SOURCE" != descricao ]; then  # describe runs need no TA session
    ttl=$(rds TTL brave:ta:session); nb=$(rds GET brave:ta:needs_bootstrap)
    dead=""; { [ "$ttl" = -2 ] || [ -z "$ttl" ]; } && dead="sessão TA ausente${nb:+ (needs_bootstrap=$nb)} — reinjetar cURL"
    fail_once sess "$dead" "$dead"
  fi

  down=$(for svc in postgres redis api worker beat; do docker compose ps --status running -q "$svc" 2>/dev/null | grep -q . || printf '%s ' "$svc"; done)
  fail_once svc "$down" "serviço parado: $down"

  pq=$(sql "select count(*) || '|' || coalesce((select task_name || ': ' || left(error_message,140) from poison_quarantine where quarantined_at >= '$START_SQL' order by quarantined_at desc limit 1),'') from poison_quarantine where quarantined_at >= '$START_SQL'")
  npq=${pq%%|*}; [ "$npq" = 0 ] && npq=""  # 0 rows = no failure (and "" clears the key)
  fail_once poison "$npq" "quarentena +$npq (última: ${pq#*|})"

  # Error lines in the logs: one aggregated FALHA per 5 min at most.
  now_sql=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  if [ "$(date +%s)" -ge "$log_hold" ]; then
    errs=$(docker compose logs --no-color --since "$last_logs" worker beat api 2>/dev/null \
      | perl -pe 's/\e\[[0-9;]*m//g' | grep -E "$LOG_PATTERN")
    last_logs=$now_sql
    if [ -n "$errs" ]; then
      fail "logs: $(wc -l <<<"$errs" | tr -d ' ') linha(s) de erro; 1ª: $(head -1 <<<"$errs" | cut -c1-220)"
      log_hold=$(( $(date +%s) + 300 ))
    fi
  fi

  st=$(sql "select status from runs_history where id='$RUN_ID'")
  # Empty = postgres unreachable, not a finished run: keep watching.
  fail_once db "$([ -z "$st" ] && echo down)" "postgres: runs_history ilegível"
  [ -n "$st" ] && RUN_STATUS=$st
  if [ -n "$st" ] && [ "$st" != running ]; then
    report final; emit "FIM run ${RUN_ID:0:8} status=$RUN_STATUS"; exit 0
  fi
  if [ $(( $(date +%s) - last_report )) -ge "$REPORT" ]; then report; last_report=$(date +%s); fi
  sleep "$INTERVAL"
done
