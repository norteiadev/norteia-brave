#!/usr/bin/env bash
#
# app.sh — start/stop/restart the full norteia-brave local stack.
#
# Services: api (FastAPI/uvicorn), worker (Celery), beat (Celery beat/RedBeat),
#           dashboard (Next.js dev). Postgres + Redis are external dependencies
#           (must already be running — Homebrew/Docker); this script only checks them.
#
# Usage:
#   scripts/app.sh start [service...]     # default: all
#   scripts/app.sh stop  [service...]
#   scripts/app.sh restart [service...]
#   scripts/app.sh status
#   scripts/app.sh logs <service> [-f]
#
# PIDs + logs live under .run/ (gitignored). Env is loaded from ./.env.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RUNDIR="$ROOT/.run"
PIDDIR="$RUNDIR/pids"
LOGDIR="$RUNDIR/logs"
mkdir -p "$PIDDIR" "$LOGDIR"

VENV="$ROOT/.venv/bin"
API_HOST="${BRAVE_API_HOST:-127.0.0.1}"
API_PORT="${BRAVE_API_PORT:-8000}"
DASH_PORT="${BRAVE_DASH_PORT:-3000}"
ALL_SERVICES=(api worker beat dashboard)

c_red=$'\033[31m'; c_grn=$'\033[32m'; c_yel=$'\033[33m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
say()  { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$c_grn" "$c_off" "$*"; }
warn() { printf '%s!%s %s\n' "$c_yel" "$c_off" "$*"; }
err()  { printf '%s✗%s %s\n' "$c_red" "$c_off" "$*"; }

load_env() {
  if [ -f "$ROOT/.env" ]; then
    set -a; . "$ROOT/.env"; set +a
  else
    warn "no .env at repo root — services may fail auth/DB (see README)."
  fi
}

# Launch command per service (run from the given dir).
svc_cmd() {
  case "$1" in
    api)       echo "$VENV/uvicorn brave.api.main:app --host $API_HOST --port $API_PORT" ;;
    worker)    echo "$VENV/celery -A brave.tasks.celery_app:app worker --loglevel=info" ;;
    beat)      echo "$VENV/celery -A brave.tasks.beat_schedule beat --loglevel=info" ;;
    dashboard) echo "bun run dev" ;;
    *) return 1 ;;
  esac
}
svc_dir() { [ "$1" = dashboard ] && echo "$ROOT/dashboard" || echo "$ROOT"; }
# Fallback match to reap orphan/ad-hoc processes not started via this script.
svc_pattern() {
  case "$1" in
    api)       echo "uvicorn brave.api.main:app" ;;
    worker)    echo "celery -A brave.tasks.celery_app:app worker" ;;
    beat)      echo "celery -A brave.tasks.beat_schedule beat" ;;
    dashboard) echo "next dev" ;;
  esac
}

pidfile() { echo "$PIDDIR/$1.pid"; }
logfile() { echo "$LOGDIR/$1.log"; }

is_running() {
  local pf; pf="$(pidfile "$1")"
  [ -f "$pf" ] || return 1
  local pid; pid="$(cat "$pf" 2>/dev/null)"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

preflight() {
  # Redis + Postgres reachability via the venv (no redis-cli/pg_isready needed).
  "$VENV/python" - <<'PY' 2>/dev/null && ok "Redis reachable" || warn "Redis NOT reachable — start it (brew services start redis / docker)."
import os, redis
redis.from_url(os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")).ping()
PY
  if [ -n "${BRAVE_DB_URL:-}" ]; then
    "$VENV/python" - <<'PY' 2>/dev/null && ok "Postgres reachable" || warn "Postgres NOT reachable — start it and check BRAVE_DB_URL."
import os
from sqlalchemy import create_engine, text
create_engine(os.environ["BRAVE_DB_URL"]).connect().execute(text("SELECT 1"))
PY
  else
    warn "BRAVE_DB_URL unset — cannot check Postgres."
  fi
}

start_one() {
  local svc="$1"
  if is_running "$svc"; then
    warn "$svc already running (pid $(cat "$(pidfile "$svc")"))"; return 0
  fi
  local cmd dir log
  cmd="$(svc_cmd "$svc")" || { err "unknown service: $svc"; return 1; }
  dir="$(svc_dir "$svc")"; log="$(logfile "$svc")"
  ( cd "$dir" && nohup bash -c "exec $cmd" >"$log" 2>&1 & echo $! >"$(pidfile "$svc")" )
  sleep 0.4
  if is_running "$svc"; then ok "$svc started (pid $(cat "$(pidfile "$svc")")) → $c_dim$log$c_off"
  else err "$svc failed to start — see $log"; tail -n 5 "$log" 2>/dev/null; fi
}

stop_one() {
  local svc="$1" pf pid pat
  pf="$(pidfile "$svc")"
  if [ -f "$pf" ]; then
    pid="$(cat "$pf" 2>/dev/null)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null
      for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done
      kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
    fi
    rm -f "$pf"
  fi
  # Reap orphans (ad-hoc launches not tracked here) by command pattern.
  pat="$(svc_pattern "$svc")"
  [ -n "$pat" ] && pkill -f "$pat" 2>/dev/null
  ok "$svc stopped"
}

status_one() {
  local svc="$1" port=""
  [ "$svc" = api ] && port="$API_PORT"; [ "$svc" = dashboard ] && port="$DASH_PORT"
  if is_running "$svc"; then
    printf '  %s%-10s%s up   pid %-7s %s\n' "$c_grn" "$svc" "$c_off" "$(cat "$(pidfile "$svc")")" "${port:+(:$port)}"
  else
    printf '  %s%-10s%s down %s\n' "$c_red" "$svc" "$c_off" "${port:+(:$port)}"
  fi
}

resolve_services() { if [ "$#" -eq 0 ]; then printf '%s\n' "${ALL_SERVICES[@]}"; else printf '%s\n' "$@"; fi; }

case "${1:-}" in
  start)
    shift; load_env; preflight
    for s in $(resolve_services "$@"); do start_one "$s"; done
    say ""; say "API:  http://$API_HOST:$API_PORT/api/v1/health    Dashboard: http://localhost:$DASH_PORT/painel"
    ;;
  stop)
    shift
    for s in $(resolve_services "$@"); do stop_one "$s"; done
    ;;
  restart)
    shift; svcs="$(resolve_services "$@")"
    for s in $svcs; do stop_one "$s"; done
    load_env; preflight
    for s in $svcs; do start_one "$s"; done
    ;;
  status)
    say "norteia-brave services:"
    for s in "${ALL_SERVICES[@]}"; do status_one "$s"; done
    ;;
  logs)
    svc="${2:-}"; [ -z "$svc" ] && { err "usage: app.sh logs <service> [-f]"; exit 1; }
    lf="$(logfile "$svc")"; [ -f "$lf" ] || { err "no log for $svc ($lf)"; exit 1; }
    if [ "${3:-}" = "-f" ]; then tail -f "$lf"; else tail -n 60 "$lf"; fi
    ;;
  *)
    say "usage: scripts/app.sh {start|stop|restart|status|logs} [service...]"
    say "services: ${ALL_SERVICES[*]}  (default: all)"
    exit 1
    ;;
esac
