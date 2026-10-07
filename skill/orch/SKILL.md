---
name: orch
description: Run a parallel multi-agent build (implementer, tester, docs, architect, reviewer) for a feature in the current project, with a live map in a new terminal window. Use when the user types /orch or asks to run the orchestrator / parallel agents on a feature. Do NOT use for small fixes or single-file changes.
---

# /orch — parallel multi-agent feature build

Orchestrator repo = `ORCH_HOME`, resolved from this skill's symlink: `ORCH_HOME="$(cd "$(readlink ~/.claude/skills/orch)/../.." && pwd)"` (docs: `$ORCH_HOME/README.md`). Dashboard needs a real terminal, so you interview, write the brief, and open a window. Never run the foreground dashboard through Bash; always `--detach`.

## 1. Gate
Multi-agent only pays off when the feature splits into independent parts (module + tests + docs). If it is a bug, a small tweak or a one-file change, say so and offer a normal session instead.

## 2. Pre-context interview
Read the project's `CLAUDE.md`, `README.md` and top-level layout first. Ask only what you cannot infer (use AskUserQuestion, max 4 questions, skip answered ones):
- Goal and success criteria (what "done" means)
- Stack / constraints (follow the project's; if empty project, propose one)
- Out of scope / files that must not change
- Quality bar: scale expectations, security-sensitive areas, test depth

## 3. Write the brief
Write `<project>/.orch/brief.md` (create the dir): goal, acceptance criteria, constraints, out of scope, scale/security notes, pointers to relevant existing files. Concrete and short. No code.

## 4. Check the repo
Project must be a git repo with a clean tree (agents branch from HEAD and do not see uncommitted work). If dirty, tell the user and stop. Never commit for them.

## 5. Launch
Start the run detached (it survives closing the window), then open the live map:
```
"$ORCH_HOME/orch.sh" run "<project-dir>/.orch/brief.md" --project "<project-dir>" --detach
# prints RUN_ID=<id>
"$ORCH_HOME/orch/open-window.sh" <id>
```
The script opens ONE window in the user's current terminal (tmux, Warp, Terminal.app, iTerm2) and never twice for the same run. For any other terminal it opens nothing and prints `orch watch <id>`: relay that line to the user. Do not open windows any other way.
Optional extra args on `run`: `--model sonnet|opus`, `--budget <usd per agent>`, `--fix-rounds <n>` (default 2, 0 disables the review→fix loop). Tell the user the run id, the budget cap (default US$ 2 per agent, 6 agents), and that `orch` in any terminal lists every active session (arrows + enter to attach, `x` to stop).

## 6. Wait and review
Watch for `<project>/.orch/runs/<id>/SUMMARY.md` to exist (`"$ORCH_HOME/orch.sh" ls` shows live status; phase `failed`/`killed` also ends the run). When done:
- The run already did the review→fix loop on its own: blocking findings (blocker/high/medium) went back to the agents that own the files (src→implementer, tests→tester, docs→docs, ADRs→architect) and the reviewer re-ran, up to `--fix-rounds`. Read SUMMARY.md and the final `REVIEW.md` in the integration worktree; say how many rounds ran and whether blocking findings are still open (`unresolved`).
- Report briefly: verdict, merge conflicts, per-agent status and cost, top review findings.

## 7. Ask what to do next (mandatory)
Never merge, commit, push or delete on your own. After the report, ask with AskUserQuestion (single choice):
1. **Merge into current branch** — `git merge --no-ff orch/<id>/integration` in the project (show the diffstat first).
2. **Push the branch / open a PR** — push `orch/<id>/integration` only after a second explicit confirmation; PR text has no agent attribution footer.
3. **Keep for manual review** — leave branch and worktree, give the path (`.orch/runs/<id>/wt/integration`).
4. **Fix remaining findings** (only if some are still open) — start a normal session on the integration worktree, or re-run with a higher `--fix-rounds`.
5. **Discard** — `"$ORCH_HOME/orch.sh" cleanup <id>` (removes worktrees and `orch/<id>/*` branches; confirm before running).

Commit rules when a commit is needed (agent output is already committed on `orch/*` branches by the orchestrator): author is the user's git identity, no `Co-Authored-By`, and in `~/work/*` the message is `{BRANCH-NAME}-{ACTION}`. After a merge, offer `cleanup` (and push, as a separate question).
