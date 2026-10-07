#!/bin/sh
# Remove the `orch` command and the /orch skill link (repo, venv and run history are left alone).
rm -f "$HOME/.local/bin/orch" "$HOME/.claude/skills/orch"
echo "removed ~/.local/bin/orch and ~/.claude/skills/orch"
