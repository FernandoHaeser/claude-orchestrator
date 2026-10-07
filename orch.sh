#!/bin/sh
# Entry point (`orch`). Resolves symlinks so it works from ~/.local/bin/orch.
SRC="$0"
while [ -h "$SRC" ]; do
  DIR="$(cd "$(dirname "$SRC")" && pwd)"
  SRC="$(readlink "$SRC")"
  case "$SRC" in /*) ;; *) SRC="$DIR/$SRC" ;; esac
done
HERE="$(cd "$(dirname "$SRC")" && pwd)"
exec "$HERE/.venv/bin/python" "$HERE/orch/cli.py" "$@"
