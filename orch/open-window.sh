#!/bin/sh
# Open `orch watch <run-id>` in the terminal you are already using. Usage: open-window.sh <run-id> [--force]
# Opens at most one window per run. Unknown terminal: opens nothing and prints the command instead.
set -e
RUN_ID="$1"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
ORCH="$HERE/orch.sh"
MARKS="${ORCH_STATE_HOME:-$HOME/.local/state/claude-orchestrator}/windows"
MARK="$MARKS/$RUN_ID"
mkdir -p "$MARKS"

if [ -e "$MARK" ] && [ "$2" != "--force" ]; then
  echo "window for $RUN_ID already opened (use --force to open another)"
  exit 0
fi

hint() { echo "no auto-open for this terminal. open a tab/split and run: orch watch $RUN_ID"; }
esc() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
CMD="'$ORCH' watch '$RUN_ID'"

if [ -n "$TMUX" ]; then
  tmux split-window -h "$CMD"
else
  case "${TERM_PROGRAM:-}" in
    WarpTerminal)
      DIR="$HOME/.warp/launch_configurations"
      mkdir -p "$DIR"
      cat > "$DIR/orch-watch.yaml" <<YAML
---
name: orch-watch
windows:
  - tabs:
      - title: orch $RUN_ID
        layout:
          cwd: $HOME
          commands:
            - exec: $ORCH watch $RUN_ID
YAML
      open "warp://launch/orch-watch" ;;
    Apple_Terminal)
      # `do script` only: a separate `activate` first makes Terminal open an extra default window
      osascript -e 'tell application "Terminal"' -e "do script \"$(esc "$CMD")\"" -e 'activate' -e 'end tell' >/dev/null ;;
    iTerm.app)
      osascript -e 'tell application "iTerm"' -e "create window with default profile command \"$(esc "$CMD")\"" -e 'end tell' >/dev/null ;;
    *) hint; exit 0 ;;
  esac
fi
touch "$MARK"
echo "opened live map for $RUN_ID"
