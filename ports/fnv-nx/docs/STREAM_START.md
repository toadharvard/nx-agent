# Stream start (orchestrator brief, common to every stream session)

You work for the fnv-nx orchestrator; the owner has delegated to it (CLAUDE.md). Its messages are binding.

Project: a native Nintendo Switch port of Fallout: New Vegas. Repository toadharvard/nx-agent. This session starts on the integration branch claude/port-nsw-un6t9s, so /CLAUDE.md and the settings are loaded.

BRANCH: run `git fetch origin BRANCH BASE` and `git checkout -B BRANCH origin/BRANCH`. If origin/BRANCH does not exist yet, create it from origin/BASE. Then merge origin/claude/port-nsw-un6t9s into it (merge, never rebase or force-push). Push with `git push -u origin BRANCH` after every finished item.

READ: /CLAUDE.md, ports/fnv-nx/docs/MACHINES.md, docs/ARCHITECTURE.md (Legal), docs/HANDOFF.md, JOURNAL.md from J-0137 to the end, your inbox note(s) (named below, on your branch under ports/fnv-nx/journal/inbox/), and your stream's docs.

SETUP:
- Attach toadharvard/fnv-nx-build with add_repo (access: push) and clone it to /home/user/fnv-nx-build. It holds trees t/<c12>, objects c/<c12>/<k>of4, links link/<c12> and link-head, msvc/pristine/87eb9373a0bb, toolchain/msvc9, fuzz caches, reports/<farm>, and the owner's game data on gamedata/exe and gamedata/shaders (agent-only; HANDOFF.md section 5).
- Run `ports/fnv-nx/tools/fetch.sh` for the pinned decomp, sse2neon and libnx.
- Run `apt-get update`, then install what your tests need.

CONVERGENCE (owner rule):
- Measure your METRIC before you start and after every commit.
- Every commit body states `metric: <before> -> <after>`.
- Each commit must reduce the metric, or be a named prerequisite that says which reduction it enables.
- Never trade one error class for an unmeasured one. Run tests/s1 and tests/s11 whenever you touch rules, and always run your stream's tests.
- Coverage first, then optimization (owner). Close a problem as a class with one general mechanism that covers every
  instance (all families, all units, all slots, all services) before you tune any single instance. Example for
  render: every program draws correctly through the general path (Translated + correct D3D9 state emulation),
  skipped draws = 0 and the scenes match the captures; only then hand-written GLSL and GPU tuning, family by family.
  A one-off fix for one instance is acceptable only when no class exists, and the commit says so.
- When your queue is empty, take the largest remaining bucket of your metric.
- Targets: 60 fps at 720p handheld, at the 1.02 GHz retail CPU clock; parity with the original game, both in translation (fuzz/equivalence) and in rendering.

RULES:
- Commit messages end with exactly one trailer line, `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. No session ids or URLs anywhere.
- Jobs that take more than ~10 minutes run on the farms: they watch the integration branch, and try farms census `wip/*` heads. Locally, test on subsets.
- Use subagents in worktrees to parallelize independent items.
- Open no PRs. Draft journal entries go to ports/fnv-nx/journal/inbox/<stream>-<nnn>.md, never JOURNAL.md itself.
- Keep working without pausing for confirmation.
