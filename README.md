# claude-orchestrator

Run a feature through **5 parallel Claude agents** (implementer, tester, docs, architect, reviewer) with a live map in your terminal. Install once, use `/orch` inside Claude Code in any project.

Portuguese guide with the full rationale: [README.pt-BR.md](README.pt-BR.md).

## Install (any machine)

Requirements: `python3` (3.9+), `git`, [`claude` CLI](https://claude.com/claude-code) (only for real runs).

```sh
git clone <this-repo> ~/github/claude-orchestrator
~/github/claude-orchestrator/install.sh
```

This creates a private `.venv` (installs `rich`), puts `orch` in `~/.local/bin` (add it to `PATH` if the installer says so) and links the skill to `~/.claude/skills/orch` (`--no-skill` to skip). Check with `orch doctor`. Uninstall: `./uninstall.sh`. Update: `git pull` (symlinks follow).

## Commands

```
orch                 interactive picker of ALL sessions (live): ↑/↓ move, enter attach, s summary, x stop, q quit
orch run "brief"     start a run (foreground dashboard); add --detach to run in background
orch ls [-a]         list runs
orch watch [id]      live map of a run (default: newest active)
orch show [id]       final SUMMARY.md + REVIEW.md
orch kill [id]       stop a run
orch clean           forget old finished runs
orch doctor          check the install
```

Runs write their state to `<project>/.orch/runs/<id>/state.json` and register in `~/.local/state/claude-orchestrator/runs/` (override with `ORCH_STATE_HOME`), so any terminal can see them. Detached runs survive closing the window.

## Use

Inside Claude Code, in a clean git repo: `/orch`.

1. Claude interviews you (goal, constraints, quality bar) and reads the project's `CLAUDE.md`/README.
2. Writes `<project>/.orch/brief.md`.
3. Starts the run detached and opens a Terminal window (or tmux split) with the live map.
4. When finished, Claude reads `SUMMARY.md` and `REVIEW.md` and reports.

Result lands on branch `orch/<run>/integration`; your branch is never touched. Run data lives in `<project>/.orch/` (excluded from git locally).

CLI without Claude: `orch run "feature brief" --project <repo> [--detach] [--dry-run] [--view map|table|both] [--model sonnet] [--budget 2]`

## How it works

```
brief -> CONTEXT.md -> lead plans CONTRACT + tasks
      -> wave 1 (parallel, one git worktree each): implementer | tester | docs | architect
      -> merge -> wave 2: reviewer -> SUMMARY.md
```

Pattern: orchestrator-worker with contract-first decomposition and worktree isolation. Only worth it when the feature splits into independent parts.

## Customize

| File | Effect |
|---|---|
| `orch/standards.md` | engineering standards injected into every agent |
| `orch/roles/*.md` | prompt, tools and file ownership per role |
| `skill/orch/SKILL.md` | the `/orch` interview and launch flow |

## Notes

- Cost: default cap US$ 2 per agent (`--budget`), 6 agents including the lead.
- The orchestrator commits only on ephemeral `orch/*` branches, author `orchestrator`.
- Real mode (`claude -p`) is young: validate with a small feature first. `--dry-run` simulates everything for free.
