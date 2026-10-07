#!/usr/bin/env python3
"""`orch` CLI: start runs, and browse/attach to every running session (like /resume, but live)."""
from __future__ import annotations

import os
import select
import shutil
import signal
import subprocess
import sys
import termios
import time
import tty
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import orchestrator  # noqa: E402
import state  # noqa: E402
from rich.console import Console, Group  # noqa: E402
from rich.live import Live  # noqa: E402
from rich.markdown import Markdown  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402
from rich.text import Text  # noqa: E402

console = Console()
STATUS_STYLE = {"running": "yellow", "done": "green", "failed": "bold red", "dead": "red"}
STATUS_ICON = {"running": "●", "done": "✔", "failed": "✘", "dead": "☠"}
HELP = """usage: orch [command]

  (no command)            interactive session picker: every run, live
  run <brief> [opts]      start a run in the foreground (--detach to run in background)
  ls [-a]                 list runs (active + recent; -a for all)
  watch [id]              live map of one run (default: newest active)
  show [id]               print the final SUMMARY.md of a run
  kill [id]               stop a running run (SIGTERM to its process group)
  cleanup [id]            remove a run's worktrees and orch/<id>/* branches (after merging or discarding)
  clean                   forget finished runs older than 24h or whose folder is gone
  doctor                  check the install
  <brief>                 same as `orch run <brief>`

ids are prefixes of the run id (e.g. 20261007-14). Run options: orch run -h"""


# --------------------------------------------------------------------------- helpers
def elapsed(run: dict) -> float:
    end = run.get("updated", time.time()) if run["status"] != "running" else time.time()
    return max(0.0, end - run["started"])


def fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if h else (f"{m}m{s:02d}s" if m else f"{s}s")


def run_progress(run: dict) -> str:
    agents = run.get("agents", [])
    return f"{sum(a['status'] == 'done' for a in agents)}/{len(agents)}" if agents else "-"


def run_cost(run: dict) -> float:
    return sum(a["cost_usd"] for a in run.get("agents", []))


def runs_table(runs: list[dict], selected: int | None = None) -> Table:
    table = Table(expand=True, header_style="bold cyan", border_style="dim", box=None, pad_edge=False)
    for col, kw in [("", {"width": 2}), ("run", {"no_wrap": True}), ("project", {"no_wrap": True}), ("phase", {"no_wrap": True}),
                    ("agents", {"justify": "right"}), ("cost", {"justify": "right"}), ("time", {"justify": "right"}),
                    ("brief", {"ratio": 1, "no_wrap": True, "overflow": "ellipsis"})]:
        table.add_column(col, **kw)
    if not runs:
        table.add_row("", Text("no runs yet. start one with: orch run \"feature brief\"", style="dim"))
    for i, r in enumerate(runs):
        st = STATUS_STYLE[r["status"]]
        marker = Text("▶" if i == selected else STATUS_ICON[r["status"]], style="bold white" if i == selected else st)
        row_style = "reverse" if i == selected else ""
        table.add_row(marker, r["run_id"], Path(r["project"]).name, Text(r.get("phase", "?"), style=st),
                      run_progress(r), f"${run_cost(r):.2f}", fmt_duration(elapsed(r)), r.get("brief", ""), style=row_style)
    return table


def pick_run(prefix: str | None) -> dict | None:
    run = state.find_run(prefix)
    if not run:
        console.print(f"[red]no run found{' for ' + prefix if prefix else ''}[/]. try: orch ls")
    return run


# --------------------------------------------------------------------------- keyboard
class RawKeys:
    """cbreak mode + non-blocking key reads; restores the terminal on exit."""

    def __enter__(self):
        self.fd = sys.stdin.fileno()
        self.old = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        return self

    def __exit__(self, *exc):
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)

    def read(self, timeout: float) -> str | None:
        if not select.select([sys.stdin], [], [], timeout)[0]:
            return None
        data = os.read(self.fd, 8).decode(errors="ignore")
        return {"\x1b[A": "up", "\x1b[B": "down", "\x1b": "esc", "\r": "enter", "\n": "enter"}.get(data, data)


# --------------------------------------------------------------------------- commands
def cmd_ls(args: list[str]) -> int:
    runs = state.load_runs()
    if "-a" not in args:
        runs = [r for r in runs if r["status"] == "running"] + [r for r in runs if r["status"] != "running"][:10]
    console.print(runs_table(runs))
    return 0


def watch_frame(run: dict, hint: str) -> Group:
    fresh = next((r for r in state.load_runs() if r["registry_file"] == run["registry_file"]), run)
    agents = orchestrator.agents_from_snapshot(fresh)
    if not agents:
        return Group(Text("waiting for state...", style="dim"))
    phase = fresh.get("phase", "?")
    note = f"{fresh.get('note', '')}  [{fresh['status']}]"
    parts = [orchestrator.render(fresh["run_id"], phase, agents, note, "both")]
    if fresh.get("error"):
        parts.append(Text(f"error: {fresh['error']}", style="bold red"))
    parts.append(Text(hint, style="dim"))
    return Group(*parts)


def watch(run: dict, back_hint: str = "q quit") -> None:
    hint = f"{back_hint}   x kill   s summary"
    with RawKeys() as keys, Live(watch_frame(run, hint), console=console, refresh_per_second=6, screen=True) as live:
        while True:
            live.update(watch_frame(run, hint))
            key = keys.read(0.15)
            if key in ("q", "esc"):
                return
            if key == "x":
                kill_run(run, quiet=True)
            if key == "s":
                break
    show_summary(run)
    input("\npress enter to go back ")


def show_summary(run: dict) -> None:
    f = Path(run["run_dir"]) / "SUMMARY.md"
    console.clear()
    if not f.exists():
        console.print("[dim]no SUMMARY.md yet (run not finished)[/]")
        return
    console.print(Markdown(f.read_text()))
    review = Path(run["run_dir"]) / "wt" / "integration" / "REVIEW.md"
    if review.exists():
        console.print(Panel(Markdown(review.read_text()), title="REVIEW.md", border_style="cyan"))


def kill_run(run: dict, quiet: bool = False) -> bool:
    if run["status"] != "running":
        if not quiet:
            console.print("[dim]not running[/]")
        return False
    try:
        os.killpg(os.getpgid(run["pid"]), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        os.kill(run["pid"], signal.SIGTERM)
    if not quiet:
        console.print(f"[yellow]stopped[/] {run['run_id']}")
    return True


def cmd_watch(args: list[str]) -> int:
    run = pick_run(args[0] if args else None)
    if not run:
        return 1
    watch(run)
    return 0


def cmd_show(args: list[str]) -> int:
    run = pick_run(args[0] if args else None)
    if not run:
        return 1
    show_summary(run)
    return 0


def cmd_kill(args: list[str]) -> int:
    run = pick_run(args[0] if args else None)
    return 0 if run and kill_run(run) else 1


def cmd_cleanup(args: list[str]) -> int:
    """Remove a run's worktrees and orch/<id>/* branches (run logs and SUMMARY stay)."""
    ids = [a for a in args if not a.startswith("-")]
    run = pick_run(ids[0] if ids else None)
    if not run:
        return 1
    if run["status"] == "running":
        console.print("[red]run still active; stop it first (orch kill)[/]")
        return 1
    project, run_id = Path(run["project"]), run["run_id"]
    if "--yes" not in args and input(f"remove worktrees and branches orch/{run_id}/* in {project.name}? [y/N] ").lower() != "y":
        return 1
    wt = Path(run["run_dir"]) / "wt"
    for d in sorted(wt.iterdir()) if wt.exists() else []:
        subprocess.run(["git", "worktree", "remove", "--force", str(d)], cwd=project, capture_output=True)
    subprocess.run(["git", "worktree", "prune"], cwd=project, capture_output=True)
    branches = subprocess.run(["git", "branch", "--list", f"orch/{run_id}/*", "--format=%(refname:short)"],
                              cwd=project, capture_output=True, text=True).stdout.split()
    for b in branches:
        subprocess.run(["git", "branch", "-D", b], cwd=project, capture_output=True)
    console.print(f"removed {len(branches)} branches and the worktrees of {run_id}")
    return 0


def cmd_clean(_: list[str]) -> int:
    console.print(f"removed {state.clean(max_age_hours=24)} stale entries")
    return 0


def cmd_doctor(_: list[str]) -> int:
    skill = Path.home() / ".claude" / "skills" / "orch"
    on_path = shutil.which("orch")
    checks = [
        ("python >= 3.9", sys.version_info >= (3, 9)),
        ("git", bool(shutil.which("git"))),
        ("claude CLI (real runs)", bool(shutil.which("claude"))),
        ("orch on PATH", bool(on_path)),
        ("skill /orch linked", skill.exists()),
        (f"state dir {state.STATE_HOME}", True),
    ]
    for name, ok in checks:
        console.print(f"[{'green' if ok else 'red'}]{'✔' if ok else '✘'}[/] {name}")
    if not on_path:
        console.print("[yellow]add ~/.local/bin to PATH:[/] export PATH=\"$HOME/.local/bin:$PATH\"")
    return 0 if all(ok for _, ok in checks) else 1


def cmd_run(args: list[str]) -> int:
    if "--detach" not in args:
        return orchestrator.main(args)
    argv = [a for a in args if a != "--detach"] + ["--headless"]
    parsed = orchestrator.build_parser().parse_args(argv)
    project = Path(parsed.project).expanduser().resolve()
    log_dir = project / ".orch"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdin_data = sys.stdin.read() if parsed.brief == "-" else None
    with open(log_dir / "detached.log", "ab") as log:
        proc = subprocess.Popen([sys.executable, str(Path(orchestrator.__file__)), *argv],
                                stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
                                stdout=log, stderr=log, start_new_session=True, cwd=project)
        if stdin_data is not None:
            proc.stdin.write(stdin_data.encode())
            proc.stdin.close()
    for _ in range(100):  # wait until the child registers itself
        run = next((r for r in state.load_runs() if r["pid"] == proc.pid), None)
        if run:
            print(f"RUN_ID={run['run_id']}")
            print(f"watch with: orch watch {run['run_id']}   (or just: orch)")
            return 0
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    console.print(f"[red]run failed to start[/]; see {log_dir / 'detached.log'}")
    return 1


# --------------------------------------------------------------------------- interactive picker
def picker() -> int:
    if not sys.stdin.isatty():
        return cmd_ls([])
    sel = 0
    msg = ""

    def frame(runs: list[dict]) -> Group:
        active = sum(r["status"] == "running" for r in runs)
        keys = "↑/↓ move   enter watch   s summary   x kill   c clean   q quit"
        return Group(Panel(runs_table(runs, sel if runs else None),
                           title=f"[bold]orchestrator sessions[/]  {active} running / {len(runs)} total", border_style="cyan"),
                     Text(msg or keys, style="yellow" if msg else "dim"))

    with RawKeys() as keys, Live(frame([]), console=console, refresh_per_second=4, screen=True) as live:
        while True:
            runs = state.load_runs()
            sel = max(0, min(sel, len(runs) - 1))
            live.update(frame(runs))
            key = keys.read(0.25)
            if key in ("q", "esc"):
                return 0
            if key in ("up", "k"):
                sel -= 1
            elif key in ("down", "j"):
                sel += 1
            elif runs and key == "enter":
                live.stop()
                keys.__exit__(None, None, None)
                watch(runs[sel], back_hint="q back")
                keys.__enter__()
                live.start()
                msg = ""
            elif runs and key == "s":
                live.stop()
                keys.__exit__(None, None, None)
                show_summary(runs[sel])
                input("\npress enter to go back ")
                keys.__enter__()
                live.start()
            elif runs and key == "x":
                msg = f"stopped {runs[sel]['run_id']}" if kill_run(runs[sel], quiet=True) else "not running"
            elif key == "c":
                msg = f"cleaned {state.clean(max_age_hours=24)} entries"


COMMANDS = {"ls": cmd_ls, "watch": cmd_watch, "show": cmd_show, "kill": cmd_kill, "clean": cmd_clean, "cleanup": cmd_cleanup,
            "doctor": cmd_doctor, "run": cmd_run}


def main(argv: list[str]) -> int:
    if not argv:
        return picker()
    if argv[0] in ("-h", "--help", "help"):
        print(HELP)
        return 0
    if argv[0] in COMMANDS:
        return COMMANDS[argv[0]](argv[1:])
    return cmd_run(argv)  # `orch "brief"` shortcut


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(130)
