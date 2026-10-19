#!/usr/bin/env bash
# Monitor 3/7 — pipeline funnel straight from Postgres: Nascente → Rio (routing,
# Places-enriched, described) → Mar (pushed / pending / rejected) + DLQ reasons + poison.
TAG=funnel; . "$(dirname "$0")/_lib.sh"
UFC="${MONITOR_UF:+and uf = '$MONITOR_UF'}"
emit "watching Nascente/Rio/Mar counts ${MONITOR_UF:+(UF=$MONITOR_UF) }(every ${INTERVAL}s)"
while true; do
  f=$(sql "select
     (select count(*) from nascente_records),
     count(*),
     count(*) filter (where routing='in_progress'),
     count(*) filter (where routing='mar'),
     count(*) filter (where routing='dlq'),
     count(*) filter (where routing='descarte'),
     count(*) filter (where normalized->>'google_enriched'='true'),
     count(*) filter (where coalesce(normalized->>'descricao_editorial','')<>'')
   from rio_records where true $UFC")
  IFS='|' read -r nas rio inp mar dlq desc gplaces descr <<<"$f"
  on_change funnel "$f" "nascente=$nas rio=$rio [in_progress=$inp mar=$mar dlq=$dlq descarte=$desc] places_enriched=$gplaces described=$descr"
  m=$(sql "select count(*) filter (where superseded_by_id is null),
     count(*) filter (where superseded_by_id is null and pushed_at is not null and push_error is null),
     count(*) filter (where superseded_by_id is null and pushed_at is null and push_error is null),
     count(*) filter (where push_error is not null)
   from mar_records")
  IFS='|' read -r act pushed pend rej <<<"$m"
  on_change mar "$m" "mar active=$act pushed=$pushed pending=$pend REJECTED=$rej"
  e=$(sql "select left(push_error,160) from mar_records where push_error is not null order by push_attempted_at desc nulls last limit 1")
  on_change push_err "$e" "last push_error: ${e:-none}"
  d=$(sql "select string_agg(reason||'='||n, ' ') from (select regexp_replace(coalesce(dlq_reason,'?'),'score=[0-9.]+ ','score<thr ') reason, count(*) n from rio_records where routing='dlq' $UFC group by 1 order by 2 desc limit 5) x")
  on_change dlq "$d" "dlq reasons: ${d:-none}"
  p=$(sql "select count(*)||' '||coalesce(string_agg(distinct task_name, ','),'') from poison_quarantine")
  on_change poison "$p" "poison_quarantine: $p"
  sleep "$INTERVAL"
done
