"""Run state + global registry, shared by the orchestrator (writer) and the CLI (reader)."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

STATE_HOME = Path(os.environ.get("ORCH_STATE_HOME", Path.home() / ".local" / "state" / "claude-orchestrator"))
REGISTRY = STATE_HOME / "runs"


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def register(run_id: str, project: Path, run_dir: Path, brief: str) -> None:
    key = hashlib.sha1(str(project).encode()).hexdigest()[:6]
    first_line = next((ln.strip("# ").strip() for ln in brief.splitlines() if ln.strip()), "")
    _atomic_write(REGISTRY / f"{run_id}-{key}.json", {
        "run_id": run_id, "project": str(project), "run_dir": str(run_dir),
        "pid": os.getpid(), "started": time.time(), "brief": first_line[:120],
    })


def write_state(run_dir: Path, snapshot: dict) -> None:
    _atomic_write(run_dir / "state.json", snapshot)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def load_runs() -> list[dict]:
    """All registered runs, newest first, each merged with its state.json and a derived `status`."""
    runs = []
    for f in REGISTRY.glob("*.json") if REGISTRY.exists() else []:
        try:
            entry = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        entry["registry_file"] = str(f)
        state_file = Path(entry["run_dir"]) / "state.json"
        snap = {}
        if state_file.exists():
            try:
                snap = json.loads(state_file.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        entry.update(snap)
        alive = pid_alive(entry["pid"])
        if entry.get("finished"):
            entry["status"] = "failed" if entry.get("error") else "done"
        else:
            entry["status"] = "running" if alive else "dead"
        entry["alive"] = alive
        runs.append(entry)
    return sorted(runs, key=lambda r: r["started"], reverse=True)


def find_run(prefix: str | None) -> dict | None:
    """Resolve a run by id prefix; without prefix, the newest running run (else newest)."""
    runs = load_runs()
    if not runs:
        return None
    if not prefix:
        return next((r for r in runs if r["status"] == "running"), runs[0])
    matches = [r for r in runs if r["run_id"].startswith(prefix)]
    return matches[0] if len(matches) >= 1 else None


def clean(max_age_hours: float = 0) -> int:
    """Drop registry entries that are not running and whose run dir is gone or older than max_age_hours."""
    removed = 0
    for r in load_runs():
        gone = not Path(r["run_dir"]).exists()
        old = time.time() - r["started"] > max_age_hours * 3600
        if r["status"] != "running" and (gone or old):
            Path(r["registry_file"]).unlink(missing_ok=True)
            removed += 1
    return removed
