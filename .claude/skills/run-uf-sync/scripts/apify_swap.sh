#!/usr/bin/env bash
# Swap the live Apify key to the next one in .env, while the engine is paused (provider_balance).
#   apify_swap.sh BRAVE_APIFY_API_KEY_FOUR        # then: docker compose up -d api worker beat
# - BRAVE_APIFY_API_KEY gets NEXT_VAR's value; the old value stays as BRAVE_APIFY_API_KEY_PREV_<ts>.
# - The Brave Apify counter (brave:cost:apify:total) is global, not per key, so
#   APIFY_TOTAL_USD_CAP = counter + the new key's real credit left - MARGIN (default 0.30).
# - A key with < ~US$0.50 left is useless: each run asks maxTotalChargeUsd>=0.50 and Apify 402s it.
# Never prints key values. Backup of .env goes to $TMPDIR.
set -euo pipefail
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
next="$1"
val() { grep -E "^$1=" .env | head -1 | cut -d= -f2- | tr -d "\"'"; }
new=$(val "$next"); old=$(val BRAVE_APIFY_API_KEY)
[ -n "$new" ] || { echo "no $next in .env"; exit 1; }
left=$(rtk proxy curl -s -H "Authorization: Bearer $new" https://api.apify.com/v2/users/me/limits \
  | python3 -c "import sys,json; d=json.load(sys.stdin)['data']; print(d['limits']['maxMonthlyUsageUsd']-d['current']['monthlyUsageUsd'])")
python3 -c "import sys; sys.exit(0 if $left >= 0.5 else 1)" || { echo "$next has only US\$$left left (< 0.50): skip it"; exit 1; }
counter=$(docker compose exec -T redis redis-cli get brave:cost:apify:total | tr -d '\r')
cap=$(python3 -c "print(round($counter + $left - ${MARGIN:-0.30}, 2))")
cp .env "${TMPDIR:-/tmp}/brave-env.bak.$(date +%s)"
NEW="$new" OLD="$old" CAP="$cap" TS="$(date +%Y%m%d%H%M)" python3 - <<'PY'
import os, re
p = ".env"; s = open(p).read()
s = re.sub(r"^BRAVE_APIFY_API_KEY=.*$", f"BRAVE_APIFY_API_KEY={os.environ['NEW']}\nBRAVE_APIFY_API_KEY_PREV_{os.environ['TS']}={os.environ['OLD']}", s, count=1, flags=re.M)
s = re.sub(r"^APIFY_TOTAL_USD_CAP=.*$", f"APIFY_TOTAL_USD_CAP={os.environ['CAP']}", s, count=1, flags=re.M)
open(p, "w").write(s)
PY
echo "swapped to $next: credit left US\$$left, counter $counter, APIFY_TOTAL_USD_CAP=$cap"
