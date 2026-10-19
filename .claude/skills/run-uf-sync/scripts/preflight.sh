#!/usr/bin/env bash
# Pre-flight for a UF sync. One line per check: OK / WARN / FAIL. Exit 1 on any FAIL.
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"; cd "$ROOT" || exit 1
set -a; . ./.env; set +a
API="http://localhost:${BRAVE_API_PORT:-8000}"; H="Authorization: Bearer ${BRAVE_DASHBOARD_BEARER_TOKEN}"
fails=0
ok(){ echo "OK    $*"; }; warn(){ echo "WARN  $*"; }; fail(){ echo "FAIL  $*"; fails=$((fails+1)); }
rds(){ docker compose exec -T redis redis-cli --raw "$@" 2>/dev/null; }
sql(){ docker compose exec -T postgres psql -U brave -d norteia_brave -At -c "$1" 2>/dev/null; }

for s in postgres redis api worker beat; do
  st=$(docker inspect -f '{{.State.Status}}' "$(docker compose ps -q $s 2>/dev/null)" 2>/dev/null)
  [ "$st" = running ] && ok "container $s running" || fail "container $s: ${st:-absent} (docker compose up -d)"
done
h=$(curl -s -m 10 "$API/api/v1/health"); echo "$h" | grep -q '"status":"ok"' && ok "brave api health" || fail "brave api health: ${h:-no answer}"

st=$(curl -s -m 30 -H "$H" "$API/api/v1/engine/status")
state=$(echo "$st" | jq -r .state 2>/dev/null); mode=$(echo "$st" | jq -r .mode 2>/dev/null)
[ "$state" = idle ] && ok "engine idle ($mode)" || fail "engine state=$state mode=$mode (another run active?)"
pr=$(echo "$st" | jq -c '.pause_reason // empty' 2>/dev/null); [ -z "$pr" ] && ok "no pause_reason" || warn "pause_reason $pr"

ttl=$(rds TTL brave:ta:session)
if [ "${ttl:--2}" -gt 300 ] 2>/dev/null || [ "$ttl" = -1 ]; then ok "TA session alive (ttl=${ttl}s)"
elif [ "${ttl:--2}" -gt 0 ] 2>/dev/null; then warn "TA session expiring (ttl=${ttl}s)"
else fail "TA session ABSENT: inject a fresh listing cURL (references/events.md › TA session)"; fi
kf=$(rds GET brave:ta:keepalive_failures); [ "${kf:-0}" = 0 ] && ok "keepalive failures 0" || warn "keepalive failures ${kf}"

U="${BRAVE_NORTEIA_API_URL%/}"
ah=$(curl -s -o /dev/null -w '%{http_code}' -m 15 "$U/api/v1/health")
ap=$(curl -s -o /dev/null -w '%{http_code}' -m 15 -X POST "$U/api/internal/territorial/attractions" -H "Authorization: Bearer ${BRAVE_NORTEIA_API_SERVICE_TOKEN}" -H 'Accept: application/json' -H 'Content-Type: application/json' -d '{}')
[ "$ah" = 200 ] && ok "norteia-api health ($U)" || fail "norteia-api health $ah ($U)"
case "$ap" in 422) ok "ingest probe 422 (token valid)";; 401) fail "ingest probe 401: brave-ingest token expired (renew, references/events.md)";; *) fail "ingest probe $ap";; esac

cfg=$(curl -s -m 30 -H "$H" "$API/api/v1/config")
for k in run_real_externals places_enrichment_enabled description_enrichment_enabled atrativo_description_cascade_enabled; do
  v=$(echo "$cfg" | jq -r "(.effective // .).$k" 2>/dev/null); [ "$v" = true ] && ok "$k=true" || warn "$k=$v"
done
v=$(echo "$cfg" | jq -r '(.effective // .).atrativo_description_batch_enabled' 2>/dev/null); [ "$v" = false ] && ok "batch lane off" || warn "atrativo_description_batch_enabled=$v (describe run refuses)"

c=$(curl -s -m 30 -H "$H" "$API/api/v1/cost")
pu=$(echo "$c" | jq -r '.places.usd_total // 0'); pc=$(echo "$c" | jq -r '.places.total_cap_usd // 300')
awk -v u="$pu" -v c="$pc" 'BEGIN{exit !(c-u>50)}' && ok "Places credit used US\$$pu of $pc" || warn "Places credit low: US\$$pu of $pc (~US\$0.05 per atrativo)"
llm=$(rds GET "brave:cost:daily:$(date -u +%F)"); ok "LLM+search today US\$${llm:-0} (cap \$${BRAVE_LLM_USD_DAILY_BUDGET:-50})"

ram=$(docker compose exec -T redis sh -c 'awk "/MemAvailable/{print int(\$2/1024)}" /proc/meminfo' 2>/dev/null)
[ "${ram:-0}" -gt 2048 ] && ok "Docker VM RAM available ${ram} MB" || warn "Docker VM RAM ${ram} MB (stop other ddev projects or raise Docker memory)"

m=$(sql "select count(*) from municipios"); [ "$m" = 5571 ] && ok "municipios 5571" || fail "municipios=$m (re-seed reference data)"
echo; [ $fails = 0 ] && echo "PREFLIGHT PASSED" || { echo "PREFLIGHT FAILED: $fails"; exit 1; }
