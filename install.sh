#!/bin/sh
# Install claude-orchestrator: private venv, `orch` command (~/.local/bin) and the /orch skill.
# Usage: ./install.sh [--no-skill]
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HOME/.local/bin"
command -v python3 >/dev/null || { echo "python3 (3.9+) required"; exit 1; }
command -v git >/dev/null || { echo "git required"; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' || { echo "python 3.9+ required"; exit 1; }
python3 -m venv "$HERE/.venv"
"$HERE/.venv/bin/pip" install -q --disable-pip-version-check -r "$HERE/requirements.txt"
chmod +x "$HERE/orch.sh" "$HERE/orch/open-window.sh"
mkdir -p "$BIN"
ln -sfn "$HERE/orch.sh" "$BIN/orch"
if [ "$1" != "--no-skill" ]; then
  mkdir -p "$HOME/.claude/skills"
  ln -sfn "$HERE/skill/orch" "$HOME/.claude/skills/orch"
fi
echo "installed:"
echo "  command  $BIN/orch"
[ "$1" != "--no-skill" ] && echo "  skill    ~/.claude/skills/orch  (restart Claude Code, then /orch)"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo; echo "add to your shell rc:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac
command -v claude >/dev/null || echo "note: claude CLI not found (needed for real runs; --dry-run works)"
echo; echo "check: orch doctor    try: orch run 'demo' --dry-run"
