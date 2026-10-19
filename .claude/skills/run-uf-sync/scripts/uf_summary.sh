#!/usr/bin/env bash
# End-of-run numbers for one UF: runs, funnel, descriptions, DLQ reasons, scores, Mar/push, cost.
#   uf_summary.sh SE
UF="${1:?UF}"
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"; cd "$ROOT" || exit 1
q(){ docker compose exec -T postgres psql -U brave -d norteia_brave -P pager=off -c "$1"; }
TA="uf='$UF' and canonical_key like 'tripadvisor:%'"
q "select source, status, to_char(started_at at time zone 'America/Sao_Paulo','DD/MM HH24:MI') inicio, to_char(ended_at at time zone 'America/Sao_Paulo','HH24:MI') fim, round(extract(epoch from ended_at-started_at)/60.0,1) min from runs_history where started_at > now() - interval '24 hours' order by started_at"
q "select split_part(canonical_key,':',1) fonte, routing, count(*), count(*) filter (where normalized->>'google_enriched'='true') places, count(*) filter (where coalesce(normalized->>'descricao_editorial','')<>'') descritos, count(*) filter (where normalized->>'ta_backfilled_at' is null and canonical_key like 'tripadvisor:%') sem_backfill, round(avg(score)::numeric,1) score_medio, max(score) score_max from rio_records where uf='$UF' group by 1,2 order by 1,2"
q "select case when coalesce(normalized->>'descricao_editorial','')<>'' then 'com_descricao' else coalesce(normalized->>'descricao_gate','sem_tentativa') end resultado, count(*), count(*) filter (where score>=65) ge65 from rio_records where $TA group by 1 order by 2 desc"
q "select case when dlq_reason like 'score=%' then 'score abaixo do limiar' else dlq_reason end motivo, routing, count(*) from rio_records where uf='$UF' and routing in ('dlq','descarte') group by 1,2 order by 3 desc"
q "select count(*) ativos, count(*) filter (where m.pushed_at is not null and m.push_error is null) pushed, count(*) filter (where m.pushed_at is null and m.push_error is null) pending, count(*) filter (where m.push_error is not null) rejected from mar_records m join rio_records r on r.id=m.rio_id where m.superseded_by_id is null and r.uf='$UF'"
q "select (select count(*) from poison_quarantine) poison, (select round(coalesce(sum(usd_cost),0)::numeric,3) from llm_generations where created_at > now() - interval '24 hours') llm_usd, (select count(*)||' / US\$ '||round(coalesce(sum(usd_cost),0)::numeric,3) from atrativo_buscas where created_at > now() - interval '24 hours') buscas"
"$(dirname "$0")/api.sh" GET /api/v1/cost | head -1 | jq -c '.places | {key_id, usd_today, daily_cap_usd, usd_total, total_cap_usd}' 2>/dev/null
