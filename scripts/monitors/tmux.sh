#!/usr/bin/env bash
# Opens the 7 monitors as tiled panes in a tmux session (default "brave-mon") and
# tees every pane to .run/monitors/<name>.log for the post-run review.
#   scripts/monitors/tmux.sh            # attach (creates the session if needed)
#   MONITOR_UF=SE scripts/monitors/tmux.sh
#   tmux kill-session -t brave-mon      # stop all monitors
DIR="$(cd "$(dirname "$0")" && pwd)"
SESSION="${MONITOR_SESSION:-brave-mon}"
LOGDIR="$DIR/../../.run/monitors"; mkdir -p "$LOGDIR"
if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  first=1
  for m in engine funnel logs stack ta-session cost api-ingest; do
    cmd="MONITOR_UF='${MONITOR_UF:-}' '$DIR/$m.sh' 2>&1 | tee -a '$LOGDIR/$m.log'"
    if [ $first = 1 ]; then tmux new-session -d -s "$SESSION" -n monitors "$cmd"; first=0
    else tmux split-window -t "$SESSION:monitors" "$cmd"; tmux select-layout -t "$SESSION:monitors" tiled >/dev/null; fi
  done
  tmux set-option -t "$SESSION" pane-border-status top >/dev/null
fi
[ -n "$TMUX" ] && tmux switch-client -t "$SESSION" || tmux attach -t "$SESSION"
