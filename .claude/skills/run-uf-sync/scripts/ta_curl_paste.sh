#!/usr/bin/env bash
# Save the TA cURL in the clipboard and, for the paginação one, inject it (canary included).
#   Chrome (CAPTCHA solved) > DevTools > Network > POST /data/graphql/ids > Copy as cURL (bash), then:
#   ta_curl_paste.sh paginacao   # the request whose payload has routeParameters.pagee → saved + injected
#   ta_curl_paste.sh brazil      # any graphql/ids POST of the Brazil page → saved only (fallback)
# Never prints the cURL (it carries live cookies).
set -euo pipefail
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
kind="${1:?usage: ta_curl_paste.sh paginacao|brazil}"
case "$kind" in paginacao|brazil) ;; *) echo "usage: ta_curl_paste.sh paginacao|brazil"; exit 1;; esac
c=$(pbpaste)
[[ $c == curl* && $c == *"/data/graphql/ids"* ]] || { echo "clipboard is not a TA graphql/ids cURL"; exit 1; }
[[ $kind != paginacao || $c == *pagee* ]] || { echo "no routeParameters.pagee: copy the request fired by changing listing page"; exit 1; }
out="scripts/single_attraction/ta_${kind}.curl"
(umask 077; printf '%s\n' "$c" > "$out")
echo "saved $out"
[ "$kind" = paginacao ] || exit 0
set -a; . ./.env; set +a
.venv/bin/python scripts/ta_bootstrap.py --curl "$out" --endpoint http://localhost:8000 2>&1 | grep -v '^Parsed'
