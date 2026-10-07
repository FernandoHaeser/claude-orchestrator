#!/usr/bin/env python3
"""Multi-agent orchestrator with live CLI dashboard.

Flow: brief -> context -> plan (contract-first) -> wave 1 (parallel, isolated worktrees)
      -> merge -> wave 2 (reviewer on merged code) -> summary.

Only the standard library and `rich` are used. Agents are `claude -p` processes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import signal
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

ROOT = Path(__file__).resolve().parent.parent
ORCH = ROOT / "orch"
WAVE_1 = ["implementer", "tester", "docs", "architect"]
WAVE_2 = ["reviewer"]
ALL_ROLES = WAVE_1 + WAVE_2
GIT_ID = ["-c", "user.name=orchestrator", "-c", "user.email=orchestrator@localhost"]

import state  # noqa: E402  (sibling module)

console = Console()


# --------------------------------------------------------------------------- model
@dataclass
class Agent:
    role: str
    tools: str
    owns: str
    prompt: str
    workdir: Path = Path(".")
    status: str = "queued"  # queued | running | done | failed
    activity: str = "-"
    tool_calls: int = 0
    cost_usd: float = 0.0
    started: float = 0.0
    ended: float = 0.0
    report: str = ""
    log: list[str] = field(default_factory=list)

    @property
    def elapsed(self) -> float:
        if not self.started:
            return 0.0
        return (self.ended or time.time()) - self.started


def load_agent(role: str) -> Agent:
    text = (ORCH / "roles" / f"{role}.md").read_text()
    meta = dict(re.findall(r"^(tools|owns): (.+)$", text, re.M))
    body = "\n".join(line for line in text.splitlines() if not re.match(r"^(role|tools|owns):", line))
    return Agent(role=role, tools=meta["tools"], owns=meta["owns"], prompt=body.strip())


# --------------------------------------------------------------------------- git
def git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", *GIT_ID, *args], cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout.strip()


def ensure_repo(project: Path) -> str:
    """Make sure project is a git repo, hide .orch/ locally, return the base branch."""
    project.mkdir(parents=True, exist_ok=True)
    if not (project / ".git").exists():
        git(project, "init", "-q", "-b", "main")
        git(project, "add", "-A")
        git(project, "commit", "-q", "--allow-empty", "-m", "chore: baseline")
    exclude = project / ".git" / "info" / "exclude"
    if ".orch/" not in exclude.read_text() if exclude.exists() else True:
        exclude.parent.mkdir(exist_ok=True)
        with exclude.open("a") as f:
            f.write(".orch/\n")
    if git(project, "status", "--porcelain"):
        console.print("[yellow]warning: uncommitted changes are NOT visible to agents (they branch from HEAD)[/]")
    return git(project, "rev-parse", "--abbrev-ref", "HEAD")


def make_worktree(project: Path, run_dir: Path, run_id: str, role: str, base: str) -> Path:
    path = run_dir / "wt" / role
    git(project, "worktree", "add", "-q", "-b", f"orch/{run_id}/{role}", str(path), base)
    return path


# --------------------------------------------------------------------------- claude process
def claude_cmd(model: str, tools: str, system: str, budget: float, permission: str) -> list[str]:
    return [
        "claude", "-p",
        "--output-format", "stream-json", "--verbose",
        "--model", model,
        "--permission-mode", permission,
        "--allowedTools", tools,
        "--max-budget-usd", str(budget),
        "--append-system-prompt", system,
        "--no-session-persistence",
    ]


def describe_tool(block: dict) -> str:
    inp = block.get("input", {})
    target = inp.get("file_path") or inp.get("pattern") or inp.get("command") or inp.get("path") or ""
    return f"{block['name']} {str(target)[:60]}".strip()


def apply_event(agent: Agent, ev: dict) -> None:
    """Update agent state from one stream-json event."""
    kind = ev.get("type")
    if kind == "assistant":
        for block in ev.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                agent.tool_calls += 1
                agent.activity = describe_tool(block)
                agent.log.append(agent.activity)
            elif block.get("type") == "text" and block.get("text", "").strip():
                agent.activity = "thinking: " + block["text"].strip().splitlines()[0][:60]
    elif kind == "result":
        agent.cost_usd = ev.get("total_cost_usd", 0.0) or 0.0
        agent.report = ev.get("result", "") or ""
        agent.status = "failed" if ev.get("is_error") else "done"
        agent.activity = "finished" if agent.status == "done" else f"error: {ev.get('subtype', '')}"


async def run_process(agent: Agent, cmd: list[str], stdin_text: str, cwd: Path) -> None:
    agent.status, agent.started = "running", time.time()
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        limit=16 * 1024 * 1024,
    )
    proc.stdin.write(stdin_text.encode())
    await proc.stdin.drain()
    proc.stdin.close()
    events_file = (cwd.parent.parent / "agents" / f"{agent.role}.jsonl").open("w")
    async for raw in proc.stdout:
        line = raw.decode().strip()
        events_file.write(line + "\n")
        try:
            apply_event(agent, json.loads(line))
        except json.JSONDecodeError:
            continue
    err = (await proc.stderr.read()).decode().strip()
    await proc.wait()
    events_file.close()
    if agent.status == "running":  # no result event arrived
        agent.status, agent.activity = "failed", (err[:80] or f"exit {proc.returncode}")
    agent.ended = time.time()


async def run_fake(agent: Agent, speed: float) -> None:
    """Dry-run: simulates an agent so the dashboard can be tested without spending tokens."""
    agent.status, agent.started = "running", time.time()
    target = (agent.owns.split() or ["plan.json"])[0]
    steps = ["Read CONTRACT.md", "Glob **/*", f"Write {target}", "Bash run checks", f"Edit {target}"]
    for step in steps:
        agent.tool_calls += 1
        agent.activity = step
        agent.cost_usd += 0.013
        await asyncio.sleep(speed * (0.6 + 0.2 * (len(agent.role) % 5)))
    if agent.role.startswith("reviewer"):
        rnd = int(agent.role.split("-r")[1]) if "-r" in agent.role else 1
        findings = [] if rnd > 1 else [
            {"path": "src/app.py", "line": 12, "severity": "high", "problem": "unvalidated input", "fix": "validate"},
            {"path": "tests/test_app.py", "line": 3, "severity": "medium", "problem": "no failure-path test", "fix": "add one"}]
        (agent.workdir / "REVIEW.json").write_text(json.dumps({"verdict": "fix" if findings else "ship", "findings": findings}))
        (agent.workdir / "REVIEW.md").write_text(f"# Review (round {rnd})\n")
    elif "-fix" in agent.role:
        (agent.workdir / f"FIXED_{agent.role}.txt").write_text("dry-run fix\n")
    agent.report = f"[dry-run] {agent.role} finished."
    agent.status, agent.activity, agent.ended = "done", "finished", time.time()


# --------------------------------------------------------------------------- dashboard
STYLE = {"queued": "dim", "running": "yellow", "done": "green", "failed": "bold red"}
ICON = {"queued": "·", "running": "●", "done": "✔", "failed": "✘"}


def stage_status(phase: str, agents: list[Agent]) -> str:
    """Aggregate status for a group of agents."""
    states = {a.status for a in agents}
    if "failed" in states:
        return "failed"
    if states == {"done"}:
        return "done"
    return "running" if states & {"running", "done"} else "queued"


LINK_STYLE = {"queued": "dim", "running": "yellow", "done": "green", "failed": "bold red"}
LINK_RANK = {"queued": 0, "running": 1, "done": 2, "failed": 3}
LABEL = {"queued": "QUEUED", "running": "RUNNING", "done": "OK", "failed": "FAILED"}


class Canvas:
    """Character grid with per-cell style, rendered to a rich Text."""

    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.cells = [[(" ", "")] * width for _ in range(height)]

    def put(self, x: int, y: int, text: str, style: str = "") -> None:
        for i, ch in enumerate(text):
            if 0 <= x + i < self.width and 0 <= y < self.height:
                self.cells[y][x + i] = (ch, style)

    def hline(self, x1: int, x2: int, y: int, style: str, glyph: str = "─") -> None:
        self.put(x1, y, glyph * max(0, x2 - x1 + 1), style)

    def vline(self, x: int, y1: int, y2: int, style: str, glyph: str = "│") -> None:
        for y in range(min(y1, y2), max(y1, y2) + 1):
            self.put(x, y, glyph, style)

    def node(self, x: int, y: int, width: int, name: str, status: str, info: str, detail: str) -> None:
        st = LINK_STYLE[status]
        inner = width - 2
        self.put(x, y, "╭" + "─" * inner + "╮", st)
        self.put(x + 2, y, f" {ICON[status]} {name} ", f"bold {st}")
        for row in (1, 2):
            self.put(x, y + row, "│" + " " * inner + "│", st)
        self.put(x + 2, y + 1, LABEL[status], f"bold {st}")
        self.put(x + 3 + len(LABEL[status]), y + 1, info[: max(0, inner - len(LABEL[status]) - 3)], "dim")
        self.put(x + 2, y + 2, detail[: inner - 2], "")
        self.put(x, y + 3, "╰" + "─" * inner + "╯", st)

    def to_text(self) -> Text:
        out = Text(no_wrap=True, overflow="crop")
        for row in self.cells:
            for ch, style in row:
                out.append(ch, style)
            out.append("\n")
        out.rstrip()
        return out


def render_map(phase: str, lead: Agent, w1: list[Agent], w2: list[Agent], width: int = 100) -> Text:
    """Zabbix-style topology: lead fans out to wave 1, which joins into merge, then reviewer."""
    lead_w, merge_w, rev_w = 20, 12, 22
    g1, g2, g3 = 7, 11, 5
    agent_w = max(18, min(36, width - (lead_w + merge_w + rev_w + g1 + g2 + g3)))
    reviews = [a for a in w2 if a.role.startswith("reviewer")]
    node_h, gap, n = 4, 1, len(w1)
    height = n * node_h + (n - 1) * gap
    xa = lead_w + g1
    xm = xa + agent_w + g2
    xr = xm + merge_w + g3
    c = Canvas(xr + rev_w, height)
    rows = [i * (node_h + gap) + 1 for i in range(n)]
    mid = (rows[0] + rows[-1]) // 2
    bus1, bus2 = lead_w + 3, xm - 3
    blink = int(time.time() * 3) % 2
    hz = lambda st: "╌" if st == "running" and blink else "─"
    vt = lambda st: "╎" if st == "running" and blink else "│"
    order = sorted(range(n), key=lambda i: LINK_RANK[w1[i].status])  # worst/most-advanced drawn last

    # fan-out: lead -> bus -> agents
    if phase in ("plan", "wave 1: parallel"):
        merge_state = "queued"
    elif phase == "merge":
        merge_state = "running"
    elif phase in ("failed", "killed"):
        merge_state = "done" if stage_status(phase, w1) == "done" else "queued"
    else:
        merge_state = "done"
    best = w1[order[-1]].status
    c.hline(lead_w, bus1 - 1, mid, LINK_STYLE[lead.status], hz(lead.status))
    for i in order:
        st, row = w1[i].status, rows[i]
        c.vline(bus1, mid, row, LINK_STYLE[st], vt(st))
        c.hline(bus1 + 1, xa - 2, row, LINK_STYLE[st], hz(st))
        c.put(xa - 1, row, "▶", LINK_STYLE[st])
    for i in order:
        glyph = "┌" if i == 0 else ("└" if i == n - 1 else "├")
        c.put(bus1, rows[i], glyph, LINK_STYLE[w1[i].status])
    c.put(bus1, mid, "┤", LINK_STYLE[best])

    # fan-in: agents -> bus -> merge (link label = agent cost)
    for i in order:
        st, row = w1[i].status, rows[i]
        c.hline(xa + agent_w, bus2 - 1, row, LINK_STYLE[st], hz(st))
        c.put(xa + agent_w + 1, row, f" ${w1[i].cost_usd:.2f} ", LINK_STYLE[st])
        c.vline(bus2, mid, row, LINK_STYLE[st], vt(st))
    for i in order:
        glyph = "┐" if i == 0 else ("┘" if i == n - 1 else "┤")
        c.put(bus2, rows[i], glyph, LINK_STYLE[w1[i].status])
    merge_link = "done" if stage_status(phase, w1) == "done" else "queued"
    c.put(bus2, mid, "├", LINK_STYLE[merge_link])
    c.hline(bus2 + 1, xm - 2, mid, LINK_STYLE[merge_link])
    c.put(xm - 1, mid, "▶", LINK_STYLE[merge_link])

    # merge -> reviewer
    c.hline(xm + merge_w, xr - 2, mid, LINK_STYLE[merge_state], hz(merge_state))
    c.put(xr - 1, mid, "▶", LINK_STYLE[merge_state])
    if len(reviews) > 1:
        c.put(xm + merge_w + 1, mid, f" r{len(reviews)} ", "bold yellow")

    # nodes drawn last so they sit on top of links
    c.node(0, mid - 1, lead_w, "lead", lead.status, f"{lead.elapsed:.0f}s", "planner (opus)")
    for i, a in enumerate(w1):
        c.node(xa, rows[i] - 1, agent_w, a.role, a.status, f"{a.tool_calls}t {a.elapsed:.0f}s", a.activity)
    c.node(xm, mid - 1, merge_w, "merge", merge_state, "", "git")
    rev = reviews[-1]
    c.node(xr, mid - 1, rev_w, rev.role, rev.status, f"{rev.tool_calls}t {rev.elapsed:.0f}s", rev.activity)
    return c.to_text()


def render(run_id: str, phase: str, agents: list[Agent], note: str, view: str = "both") -> Group:
    lead, w1, w2 = agents[0], agents[1:1 + len(WAVE_1)], agents[1 + len(WAVE_1):]
    table = Table(expand=True, header_style="bold cyan", border_style="dim")
    for col, kw in [("", {"width": 2}), ("agent", {}), ("status", {}), ("activity", {"ratio": 1, "overflow": "ellipsis", "no_wrap": True}),
                    ("tools", {"justify": "right"}), ("time", {"justify": "right"}), ("cost", {"justify": "right"})]:
        table.add_column(col, **kw)
    for a in agents:
        s = STYLE[a.status]
        table.add_row(Text(ICON[a.status], style=s), Text(a.role, style="bold"), Text(a.status, style=s),
                      a.activity, str(a.tool_calls), f"{a.elapsed:5.1f}s", f"${a.cost_usd:.3f}")
    total = sum(a.cost_usd for a in agents)
    footer = Text(f"phase: {phase}   total: ${total:.3f}   {note}", style="dim")
    parts = []
    if view in ("map", "both"):
        parts.append(Panel(render_map(phase, lead, w1, w2, console.width - 4), title=f"[bold]map[/] run {run_id}", border_style="cyan"))
    if view in ("table", "both"):
        parts.append(Panel(table, title="[bold]agents[/]", border_style="cyan"))
    return Group(*parts, footer)


# --------------------------------------------------------------------------- phases
def extract_json(text: str) -> dict:
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S) or re.search(r"(\{.*\})", text, re.S)
    if not match:
        raise ValueError("lead returned no JSON plan")
    return json.loads(match.group(1))


def build_context(brief: str, project: Path, run_dir: Path) -> Path:
    """Pre-context shared (read-only) by every agent."""
    standards = (ORCH / "standards.md").read_text()
    existing = "\n".join(sorted(str(p.relative_to(project)) for p in project.rglob("*")
                                if p.is_file() and ".git" not in p.parts and ".orch" not in p.parts))[:2000]
    local = ""
    for name in ("CLAUDE.md", "README.md"):
        f = project / name
        if f.exists():
            local += f"\n## Project {name}\n\n{f.read_text()[:6000]}\n"
    ctx = run_dir / "CONTEXT.md"
    ctx.write_text(f"# Feature brief\n\n{brief}\n\n# Project conventions (take precedence over standards)\n{local or '(none)'}\n"
                   f"# Existing project files\n\n{existing or '(empty project)'}\n\n{standards}")
    return ctx


PLAN_PROMPT = """You are the lead of a team of 5 agents (implementer, tester, docs, architect, reviewer).
Read the context below. Design the work so 4 agents can run IN PARALLEL without seeing each other's output, then a reviewer checks the merged result.

Rules:
- First write CONTRACT: stack choice (justify briefly), file layout, public interfaces/signatures, data shapes, error behavior, run/test commands. Everything the parallel agents need to agree on.
- Then one task per role: objective, expected output, files owned, boundaries, done criteria. Roles must not touch the same files.
- Be concrete. Keep the scope small enough to finish in one session.

Reply with ONLY a ```json block: {{"contract": "<markdown>", "tasks": {{"implementer": "...", "tester": "...", "docs": "...", "architect": "...", "reviewer": "..."}}}}

# CONTEXT
{context}
"""


async def plan_phase(args, context: Path, project: Path, lead: Agent, run_dir: Path) -> dict:
    if args.dry_run:
        await run_fake(lead, args.speed)
        return {"contract": "# CONTRACT\n(dry-run)", "tasks": {r: f"dry-run task for {r}" for r in ALL_ROLES}}
    cmd = claude_cmd(args.lead_model, "Read,Glob,Grep", "You are the lead planner. Read-only.", args.budget, "default")
    (run_dir / "wt").mkdir(exist_ok=True)
    scratch = run_dir / "wt" / "lead"
    scratch.mkdir(exist_ok=True)
    await run_process(lead, cmd, PLAN_PROMPT.format(context=context.read_text()), scratch)
    return extract_json(lead.report)


def worker_prompt(agent: Agent, task: str, contract: str, context: Path) -> str:
    return (f"# Your role: {agent.role}\n{agent.prompt}\n\nFiles you own: {agent.owns}\n\n"
            f"# Your task\n{task}\n\n# CONTRACT.md\n{contract}\n\n# Shared context\n{context.read_text()}")


async def wave(args, agents: list[Agent], contract: str, tasks: dict[str, str], context: Path, project: Path,
                run_dir: Path, run_id: str, base: str) -> None:
    """Run agents in parallel, each in its own worktree branched from `base`."""
    async def one(agent: Agent) -> None:
        agent.workdir = make_worktree(project, run_dir, run_id, agent.role, base)
        (agent.workdir / "CONTRACT.md").write_text(contract)
        if args.dry_run:
            await run_fake(agent, args.speed)
            return
        system = f"You are the {agent.role} agent. Own only: {agent.owns}. Do not git commit."
        cmd = claude_cmd(args.model, agent.tools, system, args.budget, "acceptEdits")
        await run_process(agent, cmd, worker_prompt(agent, tasks[agent.role], contract, context), agent.workdir)

    await asyncio.gather(*(one(a) for a in agents))


def merge_agents(run_dir: Path, run_id: str, agents: list[Agent], label: str = "agent output") -> list[str]:
    """Commit each finished agent's worktree and merge its branch into the integration worktree."""
    integ_path = run_dir / "wt" / "integration"
    conflicts: list[str] = []
    for a in agents:
        if a.status != "done":
            continue
        git(a.workdir, "add", "-A")
        git(a.workdir, "commit", "-q", "--allow-empty", "-m", f"{a.role}: {label}")
        try:
            git(integ_path, "merge", "-q", "--no-ff", "-m", f"merge {a.role}", f"orch/{run_id}/{a.role}")
        except RuntimeError:
            git(integ_path, "merge", "--abort")
            conflicts.append(a.role)
    return conflicts


def commit_and_merge(project: Path, run_dir: Path, run_id: str, agents: list[Agent], base: str) -> tuple[str, list[str]]:
    """Create the integration branch/worktree and merge the first wave into it."""
    integ = f"orch/{run_id}/integration"
    git(project, "worktree", "add", "-q", "-b", integ, str(run_dir / "wt" / "integration"), base)
    return integ, merge_agents(run_dir, run_id, agents)


# --------------------------------------------------------------------------- review -> fix loop
OWNERSHIP = [("docs/adr/", "architect"), ("tests/", "tester"), ("test/", "tester"),
             ("docs/", "docs"), ("README", "docs"), ("src/", "implementer")]
BLOCKING = {"blocker", "high", "medium"}


def owner_of(path: str) -> str:
    clean = path.lstrip("./")
    return next((role for prefix, role in OWNERSHIP if clean.startswith(prefix)), "implementer")


def read_review(workdir: Path) -> dict | None:
    try:
        return json.loads((workdir / "REVIEW.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None


def blocking_findings(review: dict | None) -> list[dict]:
    return [f for f in (review or {}).get("findings", []) if str(f.get("severity", "")).lower() in BLOCKING]


def fix_task(findings: list[dict]) -> str:
    lines = [f"- {f.get('path', '?')}:{f.get('line', '?')} [{f.get('severity')}] {f.get('problem', '')} -> {f.get('fix', '')}"
             for f in findings]
    return ("Fix ONLY these review findings, in files you own. Do not refactor anything else. "
            "If a finding belongs to a file you do not own, say so in your report.\n" + "\n".join(lines))


async def fix_loop(args, ctl: dict, everyone: list[Agent], reviewer: Agent, plan: dict, context: Path,
                   project: Path, run_dir: Path, run_id: str, integ: str) -> tuple[Agent, list[str]]:
    """review -> owners fix -> re-review, up to args.fix_rounds. Returns the last reviewer and merge conflicts."""
    conflicts: list[str] = []
    for rnd in range(1, args.fix_rounds + 1):
        todo = blocking_findings(read_review(reviewer.workdir)) if reviewer.status == "done" else []
        if not todo:
            break
        by_owner: dict[str, list[dict]] = {}
        for f in todo:
            by_owner.setdefault(owner_of(str(f.get("path", ""))), []).append(f)
        fixers = []
        for role in by_owner:
            fx = load_agent(role)
            fx.role = f"{role}-fix{rnd}"
            fixers.append(fx)
        everyone.extend(fixers)
        ctl["phase"] = f"fix round {rnd}"
        tasks = {fx.role: fix_task(by_owner[fx.role.rsplit("-fix", 1)[0]]) for fx in fixers}
        await wave(args, fixers, plan["contract"], tasks, context, project, run_dir, run_id, integ)
        conflicts += merge_agents(run_dir, run_id, fixers, "review fixes")

        ctl["phase"] = f"re-review {rnd}"
        reviewer = load_agent("reviewer")
        reviewer.role = f"reviewer-r{rnd + 1}"
        everyone.append(reviewer)
        task = (plan["tasks"]["reviewer"] + f"\n\nThis is re-review round {rnd + 1}. Verify the previous findings are fixed, "
                "look for regressions, then rewrite REVIEW.md and REVIEW.json from scratch.")
        await wave(args, [reviewer], plan["contract"], {reviewer.role: task}, context, project, run_dir, run_id, integ)
        conflicts += merge_agents(run_dir, run_id, [reviewer], "re-review")
    return reviewer, conflicts


def snapshot(run_id: str, project: Path, ctl: dict, note: str, agents: list[Agent], started: float) -> dict:
    """Serializable view of a run, read by the `orch` CLI."""
    return {
        "run_id": run_id, "project": str(project), "phase": ctl["phase"], "note": note,
        "finished": ctl["finished"], "error": ctl["error"], "integration": ctl["integration"],
        "unresolved": ctl.get("unresolved", 0),
        "pid": os.getpid(), "started": started, "updated": time.time(),
        "agents": [{"role": a.role, "status": a.status, "activity": a.activity, "tool_calls": a.tool_calls,
                    "cost_usd": a.cost_usd, "started": a.started, "ended": a.ended} for a in agents],
    }


def agents_from_snapshot(snap: dict) -> list[Agent]:
    """Rebuild Agent objects for rendering. Frozen clocks when the run is no longer alive."""
    freeze = snap.get("updated") if (snap.get("finished") or not snap.get("alive", True)) else None
    agents = []
    for d in snap.get("agents", []):
        a = Agent(role=d["role"], tools="", owns="", prompt="", status=d["status"], activity=d["activity"],
                  tool_calls=d["tool_calls"], cost_usd=d["cost_usd"], started=d["started"], ended=d["ended"])
        if freeze and a.started and not a.ended:
            a.ended = freeze
        agents.append(a)
    return agents


class NullLive:
    """Stand-in for rich Live when running headless (detached)."""
    def __enter__(self): return self
    def __exit__(self, *exc): return False
    def update(self, *a, **k): pass


async def main_async(args) -> int:
    project = Path(args.project).expanduser().resolve()
    base = ensure_repo(project)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = project / ".orch" / "runs" / run_id
    (run_dir / "agents").mkdir(parents=True)
    (project / ".orch" / "latest").write_text(run_id)
    brief = sys.stdin.read() if args.brief == "-" else (Path(args.brief).read_text() if Path(args.brief).is_file() else args.brief)
    context = build_context(brief, project, run_dir)

    lead = Agent("lead", "", "", "")
    w1 = [load_agent(r) for r in WAVE_1]
    w2 = [load_agent(r) for r in WAVE_2]
    everyone = [lead, *w1, *w2]
    note = f"project: {project.name}  model: {args.model}{'  DRY-RUN' if args.dry_run else ''}"
    ctl = {"phase": "plan", "finished": False, "error": None, "integration": None, "unresolved": 0}
    started = time.time()
    state.register(run_id, project, run_dir, brief)

    def save() -> None:
        state.write_state(run_dir, snapshot(run_id, project, ctl, note, everyone, started))

    def on_term(*_) -> None:
        ctl.update(phase="killed", error="killed by user", finished=True)
        save()
        os._exit(143)

    signal.signal(signal.SIGTERM, on_term)
    save()
    conflicts: list[str] = []
    frame = lambda: render(run_id, ctl["phase"], everyone, note, args.view)  # noqa: E731
    live_cm = NullLive() if args.headless else Live(frame(), console=console, refresh_per_second=8)

    with live_cm as live:
        async def ticker():
            n = 0
            while True:
                live.update(frame()) if not args.headless else None
                if n % 4 == 0:
                    save()
                n += 1
                await asyncio.sleep(0.12)
        tick = asyncio.create_task(ticker())
        try:
            plan = await plan_phase(args, context, project, lead, run_dir)
            (run_dir / "plan.json").write_text(json.dumps(plan, indent=2))
            if lead.status == "failed":
                raise RuntimeError("planning failed")
            ctl["phase"] = "wave 1: parallel"
            await wave(args, w1, plan["contract"], plan["tasks"], context, project, run_dir, run_id, base)
            ctl["phase"] = "merge"
            integ, conflicts = commit_and_merge(project, run_dir, run_id, w1, base)
            ctl["integration"] = integ
            ctl["phase"] = "wave 2: review"
            await wave(args, w2, plan["contract"], plan["tasks"], context, project, run_dir, run_id, integ)
            conflicts += merge_agents(run_dir, run_id, w2, "review")
            last_reviewer, more = await fix_loop(args, ctl, everyone, w2[0], plan, context, project, run_dir, run_id, integ)
            conflicts += more
            if last_reviewer.status == "done":
                ctl["unresolved"] = len(blocking_findings(read_review(last_reviewer.workdir)))
            ctl["phase"] = "done"
        except Exception as exc:  # noqa: BLE001 - surface any failure in state, not just a traceback
            ctl.update(phase="failed", error=f"{type(exc).__name__}: {exc}")
            (run_dir / "error.log").write_text(traceback.format_exc())
        finally:
            tick.cancel()
            ctl["finished"] = True
            save()
            if not args.headless:
                live.update(frame())

    summary = [f"# Run {run_id}\n"]
    if ctl["error"]:
        summary.append(f"**FAILED:** {ctl['error']} (see error.log)\n")
    if ctl["integration"]:
        summary.append(f"Integration branch: `{ctl['integration']}` (worktree: `.orch/runs/{run_id}/wt/integration`)\n")
    if conflicts:
        summary.append(f"Merge conflicts (resolve manually): {', '.join(conflicts)}\n")
    if ctl["unresolved"]:
        summary.append(f"**{ctl['unresolved']} blocking finding(s) still open after {args.fix_rounds} fix round(s)**: see REVIEW.md in the integration worktree.\n")
    for a in everyone:
        summary.append(f"## {a.role} [{a.status}] ${a.cost_usd:.3f}\n\n{a.report}\n")
    (run_dir / "SUMMARY.md").write_text("\n".join(summary))
    if not args.headless:
        console.print(f"\nsummary: [bold]{run_dir / 'SUMMARY.md'}[/]\nbranch:  [bold]{ctl['integration']}[/] in {project}")
    return 1 if ctl["error"] else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="orch run", description="Parallel multi-agent orchestrator")
    p.add_argument("brief", help="brief text, path to a brief file, or '-' for stdin")
    p.add_argument("--project", default=".", help="target repo (default: current directory)")
    p.add_argument("--model", default="sonnet")
    p.add_argument("--lead-model", default="opus")
    p.add_argument("--budget", type=float, default=2.0, help="max USD per agent")
    p.add_argument("--fix-rounds", type=int, default=2, help="max review->fix->re-review rounds (0 disables)")
    p.add_argument("--view", choices=["map", "table", "both"], default="both")
    p.add_argument("--dry-run", action="store_true", help="simulate agents, spend nothing")
    p.add_argument("--speed", type=float, default=0.8, help="dry-run step delay in seconds")
    p.add_argument("--headless", action="store_true", help="no UI; state only (used by --detach)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.dry_run and not shutil.which("claude"):
        console.print("claude CLI not found")
        return 1
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
