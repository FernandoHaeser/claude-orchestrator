#!/bin/sh
# Open `orch watch <run-id>` in a visible terminal. Usage: open-window.sh <run-id>
set -e
RUN_ID="$1"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CMD="'$HERE/orch.sh' watch '$RUN_ID'"
if [ -n "$TMUX" ]; then
  tmux split-window -h "$CMD"
else
  osascript -e "tell application \"Terminal\" to activate" \
            -e "tell application \"Terminal\" to do script \"$(printf '%s' "$CMD" | sed 's/"/\\"/g')\""
fi
