# Shared helpers for scripts/monitors/*.sh — sourced, not run.
#
# Every monitor prints ONE line per event ("HH:MM:SS [tag] message") and only when
# something changed, so the same scripts feed a tmux pane (human) or Claude Code's
# Monitor tool (each stdout line = one notification). No host psql/redis-cli needed:
# Postgres and Redis are reached through `docker compose exec`.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT" || exit 1

# .env gives the bearer token / norteia-api URL. Values with "|" must be quoted there.
set -a
# shellcheck disable=SC1091
[ -f .env ] && . ./.env
set +a

API="http://localhost:${BRAVE_API_PORT:-8000}"
INTERVAL="${MONITOR_INTERVAL:-15}"

emit() { printf '%s [%s] %s\n' "$(date +%H:%M:%S)" "$TAG" "$*"; }

sql() {  # sql "select ..." -> unaligned, '|'-separated rows
  docker compose exec -T postgres psql -U brave -d norteia_brave -AtX -F'|' -c "$1" 2>/dev/null
}

rds() {  # rds GET key
  docker compose exec -T redis redis-cli --raw "$@" 2>/dev/null
}

api() {  # api /api/v1/engine/status -> JSON body ("" on failure)
  curl -fsS --max-time 15 -H "Authorization: Bearer ${BRAVE_DASHBOARD_BEARER_TOKEN:-}" "$API$1" 2>/dev/null
}

# on_change KEY VALUE [message] — emit message (default VALUE) only when VALUE differs
# from the last value seen for KEY in this process. File-backed: macOS bash 3.2 has no
# associative arrays.
_STATE="$(mktemp -d)"
trap 'rm -rf "$_STATE"' EXIT
on_change() {
  local f="$_STATE/$1" val="$2" msg="${3:-$2}"
  if [ ! -f "$f" ] || [ "$(cat "$f")" != "$val" ]; then
    printf '%s' "$val" > "$f"
    emit "$msg"
  fi
}
