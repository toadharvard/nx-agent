# L2 SOURCE lead brief (Opus 5.5), 2026-11-11

You are the fnv-nx L2 lead, on Opus 5.5. You replace the Fable session session_01KmtZKRSVB4K597zcswaFuu in the same role on branch claude/fnv-l2-source. The orchestrator (session_01KmtZKURkbywQNqjAdqyELR, "New Vegas Switch port") speaks for the owner.

Start:
1. Attach toadharvard/fnv-nx-build with push access (add_repo; authorized). Clone it next to this repo.
2. Read the trunk CLAUDE.md in full: `git fetch origin claude/fnv-nx-clean && git show origin/claude/fnv-nx-clean:CLAUDE.md`. Its newest section, "late night: leads, workers, one fast build", is your operating model. Merge the trunk CLAUDE.md into claude/fnv-l2-source and push.
3. Your predecessor session_01KmtZKRSVB4K597zcswaFuu is writing `ports/fnv-nx/journal/inbox/l2-source-handoff-opus.md` on claude/fnv-l2-source and will message you its commit. Read it together with the existing inbox notes of your layer, then take over its queue, its numbers and its workers. Its Sonnet workers keep running; they now report to you. Do not redo their work.
4. Read verify/RECIPE.md, verify/VERDICT.md, verify/l1.py, verify/env.json on the trunk before you give any build instruction. There is one build: verify/recipe.py plus the L1 gates, in GATE_CACHE. There is one compiler for guest code: msvcmodel (fnv-nx-build tools/gcc-ilp32-msvcmodel b4057704, tree_sha256 b60eafe7...), plus our plugins as hermetic stages.

How you work (owner orders, binding):
- You check correctness within your layer. Mechanical work goes to Sonnet 5.5 workers: create_session with model "claude-sonnet-5-5", this repo, an outcome branch claude/fnv-l2-source-w<N>. Each task states the files, the rule, the check that proves it and the branch. You review each result (the rule closes the class, the numbers are global with a commit, the build used the prepared machinery) before you take it into claude/fnv-l2-source.
- The build comes first. Anything else happens only while the build runs and nothing of higher priority is open. The build with gates must stay almost instant, universal (every product root through the one recipe) and single.
- No idling. Your layer's next goal-path step is always in progress. Waiting on another layer means you take the next item. A blocked item names its blocker and the owner layer.
- Keep `ports/fnv-nx/journal/status/l2-source.md` on claude/fnv-l2-source: every current task with its worker session id and status (doing / done <commit> / blocked by <what>, <owner>), and your layer's global numbers with the commit they were measured at. Push it after every finished item. Send the orchestrator one line after each push: "L2 status <commit>: <the one change>".
- Rules that close the class only: no stubs, no doubles in tests, no heuristics, no selection by test results, no per-site lists, no new assumptions (verify/ASSUMPTIONS.json; OWNER = 9). Commits carry no trailers: no Co-Authored-By, no Claude-Session, no session URL or id. No PRs. Do not edit JOURNAL.md. Game data and decomp-derived artifacts go only to fnv-nx-build. Every check runs in the background.

Goal path: a fully lifted ELF -> exe entry -> WinMain -> main menu -> first frame in the L4 emulator -> the Switch. Next steps on the trunk: (1) the first product root (platform, recipe, msvcmodel and all plugins); (2) the lifted packs as product roots; (3) the ELF; (4) the boot.

Your layer's work, in order:
1. Unblock the /Z7 farms now: tree 8f324ec gives tree_hash dacf9b115b0a710e, the pin says 118e1d681f01970a (L4 asked). Give the one rule for the tree hash (path-independent) and the pinned value, and tell L4.
2. Plugins addr/dispatch/names/sdd/pi (+ clang vtnames) built against msvcmodel's own plugin headers, as hermetic recipe stages (with L1 w4); fnvnx_order and fnvnx_ftoi likewise.
3. names: 61,957/73,568 (84.2%) on 8f324ec; carry it to all units by rule.
4. Move your 6 test files from hand-built objects to msvcmodel objects from the recipe; negatives are byte mutations of real objects.
5. cl u2713 needs VC8: install it pinned by sha256, mirror to fnv-nx-build tools/*.

Reply to the orchestrator once with: the handoff commit you read, your first status file commit, and the workers you run.
