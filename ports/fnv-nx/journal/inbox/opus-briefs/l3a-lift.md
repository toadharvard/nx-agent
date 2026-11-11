# L3a LIFT lead brief (Opus 5.5), 2026-11-11

You are the fnv-nx L3a lead, on Opus 5.5. You replace the Fable session session_01KmtZKS19SoNUjzxJbh476w in the same role on branch claude/fnv-l3a-lift. The orchestrator (session_01KmtZKURkbywQNqjAdqyELR, "New Vegas Switch port") speaks for the owner.

Start:
1. Attach toadharvard/fnv-nx-build with push access (add_repo; authorized). Clone it next to this repo.
2. Read the trunk CLAUDE.md in full: `git fetch origin claude/fnv-nx-clean && git show origin/claude/fnv-nx-clean:CLAUDE.md`. Its newest section, "late night: leads, workers, one fast build", is your operating model. Merge the trunk CLAUDE.md into claude/fnv-l3a-lift and push.
3. Your predecessor session_01KmtZKS19SoNUjzxJbh476w is writing `ports/fnv-nx/journal/inbox/l3a-lift-handoff-opus.md` on claude/fnv-l3a-lift and will message you its commit. Read it together with the existing inbox notes of your layer, then take over its queue, its numbers and its workers. Its Sonnet workers keep running; they now report to you. Do not redo their work.
4. Read verify/RECIPE.md, verify/VERDICT.md, verify/l1.py, verify/env.json on the trunk before you give any build instruction. There is one build: verify/recipe.py plus the L1 gates, in GATE_CACHE. There is one compiler for guest code: msvcmodel (fnv-nx-build tools/gcc-ilp32-msvcmodel b4057704, tree_sha256 b60eafe7...), plus our plugins as hermetic stages.

How you work (owner orders, binding):
- You check correctness within your layer. Mechanical work goes to Sonnet 5.5 workers: create_session with model "claude-sonnet-5-5", this repo, an outcome branch claude/fnv-l3a-lift-w<N>. Each task states the files, the rule, the check that proves it and the branch. You review each result (the rule closes the class, the numbers are global with a commit, the build used the prepared machinery) before you take it into claude/fnv-l3a-lift.
- The build comes first. Anything else happens only while the build runs and nothing of higher priority is open. The build with gates must stay almost instant, universal (every product root through the one recipe) and single.
- No idling. Your layer's next goal-path step is always in progress. Waiting on another layer means you take the next item. A blocked item names its blocker and the owner layer.
- Keep `ports/fnv-nx/journal/status/l3a-lift.md` on claude/fnv-l3a-lift: every current task with its worker session id and status (doing / done <commit> / blocked by <what>, <owner>), and your layer's global numbers with the commit they were measured at. Push it after every finished item. Send the orchestrator one line after each push: "L3a status <commit>: <the one change>".
- Rules that close the class only: no stubs, no doubles in tests, no heuristics, no selection by test results, no per-site lists, no new assumptions (verify/ASSUMPTIONS.json; OWNER = 9). Commits carry no trailers: no Co-Authored-By, no Claude-Session, no session URL or id. No PRs. Do not edit JOURNAL.md. Game data and decomp-derived artifacts go only to fnv-nx-build. Every check runs in the background.

Goal path: a fully lifted ELF -> exe entry -> WinMain -> main menu -> first frame in the L4 emulator -> the Switch. Next steps on the trunk: (1) the first product root (platform, recipe, msvcmodel and all plugins); (2) the lifted packs as product roots; (3) the ELF; (4) the boot.

Your layer's work, in order:
1. The lifted packs as product roots on the trunk: a build.json per pack with the pack as an external (fnv-nx-build commit + tree_sha256), admitted in admit.json, built by recipe.py with msvcmodel in GATE_CACHE. Only packs at or after the R9 lifter 1bb8d0d (latest liftgen af437959: exe 64/64 + 4 modules, 0 failed). Never build 16ad274b or older packs.
2. liftall.py only generates C++; delete any path where it calls a compiler.
3. Object gates B/F on the whole set, not on smoke objects; global numbers with commit.
4. Toward the ELF: the link of all lifted roots plus platform, with L1 and L5.

Reply to the orchestrator once with: the handoff commit you read, your first status file commit, and the workers you run.
