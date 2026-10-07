"""Generate README screenshots (SVG) from the real renderers. Run: .venv/bin/python docs/gen_screenshots.py"""
import io
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "orch"))

import cli  # noqa: E402
import orchestrator as orch  # noqa: E402
from rich.console import Console, Group  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.text import Text  # noqa: E402

OUT = ROOT / "docs" / "img"
NOW = time.time()


def recorder(width: int) -> Console:
    return Console(record=True, width=width, force_terminal=True, file=io.StringIO(), color_system="truecolor")


def agent(role, status, activity, tools, secs, cost):
    a = orch.Agent(role, "", "", "", status=status, activity=activity, tool_calls=tools, cost_usd=cost)
    if status != "queued":
        a.started, a.ended = NOW - secs, NOW
    return a


def save(console: Console, name: str, title: str) -> None:
    console.save_svg(str(OUT / name), title=title)
    print("wrote", OUT / name)


# 1. live map, mid-run
rec = recorder(112)
orch.console = rec
agents = [
    agent("lead", "done", "finished", 7, 142, 0.64),
    agent("implementer", "done", "finished", 11, 168, 0.71),
    agent("tester", "running", "Bash pytest tests/ -q", 9, 97, 0.38),
    agent("docs", "running", "Write docs/api.md", 13, 97, 0.29),
    agent("architect", "done", "finished", 15, 62, 0.29),
    agent("reviewer", "queued", "-", 0, 0, 0.0),
]
rec.print(orch.render("20261007-141138", "wave 1: parallel", agents, "project: billing-api  model: sonnet", "map"))
save(rec, "map.svg", "orch watch")

# 2. map + table, finished
rec = recorder(112)
orch.console = rec
done = [agent("lead", "done", "finished", 7, 142, 0.64), agent("implementer", "done", "finished", 11, 168, 0.71),
        agent("tester", "done", "finished", 18, 190, 0.52), agent("docs", "done", "finished", 13, 120, 0.29),
        agent("architect", "done", "finished", 15, 62, 0.29), agent("reviewer", "done", "finished", 12, 85, 0.44)]
rec.print(orch.render("20261007-141138", "done", done, "project: billing-api  model: sonnet", "both"))
save(rec, "finished.svg", "orch watch")

# 3. session picker
rec = recorder(112)


def fake(run_id, project, status, phase, done_n, cost, secs, brief):
    return {"run_id": run_id, "project": f"/work/{project}", "status": status, "phase": phase, "started": NOW - secs,
            "updated": NOW, "brief": brief,
            "agents": [{"status": "done" if i < done_n else "queued", "cost_usd": cost / 6} for i in range(6)]}


runs = [
    fake("20261007-153012", "billing-api", "running", "wave 1: parallel", 1, 1.18, 240, "Add invoice export (CSV + PDF)"),
    fake("20261007-151540", "web-dashboard", "running", "wave 2: review", 5, 3.02, 780, "Role-based access for admin pages"),
    fake("20261007-143201", "billing-api", "done", "done", 6, 3.41, 1260, "Idempotent webhook receiver"),
    fake("20261007-120455", "mobile-app", "failed", "failed", 2, 0.77, 95, "Offline sync queue"),
    fake("20261006-171120", "web-dashboard", "dead", "wave 1: parallel", 1, 0.40, 300, "Migrate charts to canvas"),
]
rec.print(Group(Panel(cli.runs_table(runs, 1), title="[bold]orchestrator sessions[/]  2 running / 5 total", border_style="cyan"),
                Text("↑/↓ move   enter watch   s summary   x kill   c clean   q quit", style="dim")))
save(rec, "picker.svg", "orch")
