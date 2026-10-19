#!/usr/bin/env bash
# Monitor 5/7 — spend: LLM+search daily counter (cost guard), Places per-key daily and
# lifetime credit. Emits on every US$ COST_STEP (default 0.25) crossed, and always when a
# counter passes 50% / 80% / 100% of its cap.
TAG=cost; . "$(dirname "$0")/_lib.sh"
STEP="${COST_STEP:-0.25}"
LLM_CAP="${BRAVE_LLM_USD_DAILY_BUDGET:-50}"
emit "watching cost counters (step US\$$STEP, LLM daily cap US\$$LLM_CAP)"
bucket() { awk -v v="${1:-0}" -v s="$STEP" 'BEGIN{printf "%d", v/s}'; }
pct() { awk -v v="${1:-0}" -v c="$2" 'BEGIN{p=(c>0)?v/c*100:0; print (p>=100)?100:(p>=80)?80:(p>=50)?50:0}'; }
while true; do
  day=$(date -u +%Y-%m-%d)
  llm=$(rds GET "brave:cost:daily:$day"); llm=${llm:-0}
  on_change llm_b "$(bucket "$llm")" "llm+search today (UTC $day): US\$$(printf %.3f "$llm")"
  on_change llm_p "$(pct "$llm" "$LLM_CAP")" "llm+search at $(pct "$llm" "$LLM_CAP")%+ of daily cap US\$$LLM_CAP"
  c=$(api /api/v1/cost)
  if [ -n "$c" ]; then
    pl=$(jq -r '.places | "\(.usd_today // 0)|\(.daily_cap_usd // 0)|\(.usd_total // 0)|\(.total_cap_usd // 0)|\(.key_id // "-")"' <<<"$c")
    IFS='|' read -r ptoday pcap ptot ptcap pkey <<<"$pl"
    on_change pl_b "$(bucket "$ptoday")" "places key $pkey today US\$$(printf %.3f "$ptoday")/$pcap  credit used US\$$(printf %.2f "$ptot")/$ptcap"
    on_change pl_p "$(pct "$ptot" "$ptcap")" "places credit at $(pct "$ptot" "$ptcap")%+ of US\$$ptcap"
  fi
  g=$(sql "select coalesce(round(sum(usd_cost)::numeric,2),0)||' usd / '||count(*)||' calls' from llm_generations where created_at::date = current_date")
  on_change gen "$(bucket "${g%% *}")" "llm_generations today: $g"
  sleep "$INTERVAL"
done
