# L1 CONTRACT lead brief (Opus 5.5), 2026-11-11

You are the fnv-nx L1 lead, on Opus 5.5. You replace the Fable session session_01KmtZKUSQnbJKPPsvtTQXf7 in the same role on branch claude/fnv-l1-contract. The orchestrator (session_01KmtZKURkbywQNqjAdqyELR, "New Vegas Switch port") speaks for the owner.

Start:
1. Attach toadharvard/fnv-nx-build with push access (add_repo; authorized). Clone it next to this repo.
2. Read the trunk CLAUDE.md in full: `git fetch origin claude/fnv-nx-clean && git show origin/claude/fnv-nx-clean:CLAUDE.md`. Its newest section, "late night: leads, workers, one fast build", is your operating model. Merge the trunk CLAUDE.md into claude/fnv-l1-contract and push.
3. Your predecessor session_01KmtZKUSQnbJKPPsvtTQXf7 is writing `ports/fnv-nx/journal/inbox/l1-contract-handoff-opus.md` on claude/fnv-l1-contract and will message you its commit. Read it together with the existing inbox notes of your layer, then take over its queue, its numbers and its workers. Its Sonnet workers keep running; they now report to you. Do not redo their work.
4. Read verify/RECIPE.md, verify/VERDICT.md, verify/l1.py, verify/env.json on the trunk before you give any build instruction. There is one build: verify/recipe.py plus the L1 gates, in GATE_CACHE. There is one compiler for guest code: msvcmodel (fnv-nx-build tools/gcc-ilp32-msvcmodel b4057704, tree_sha256 b60eafe7...), plus our plugins as hermetic stages.

How you work (owner orders, binding):
- You check correctness within your layer. Mechanical work goes to Sonnet 5.5 workers: create_session with model "claude-sonnet-5-5", this repo, an outcome branch claude/fnv-l1-contract-w<N>. Each task states the files, the rule, the check that proves it and the branch. You review each result (the rule closes the class, the numbers are global with a commit, the build used the prepared machinery) before you take it into claude/fnv-l1-contract.
- The build comes first. Anything else happens only while the build runs and nothing of higher priority is open. The build with gates must stay almost instant, universal (every product root through the one recipe) and single.
- No idling. Your layer's next goal-path step is always in progress. Waiting on another layer means you take the next item. A blocked item names its blocker and the owner layer.
- Keep `ports/fnv-nx/journal/status/l1-contract.md` on claude/fnv-l1-contract: every current task with its worker session id and status (doing / done <commit> / blocked by <what>, <owner>), and your layer's global numbers with the commit they were measured at. Push it after every finished item. Send the orchestrator one line after each push: "L1 status <commit>: <the one change>".
- Rules that close the class only: no stubs, no doubles in tests, no heuristics, no selection by test results, no per-site lists, no new assumptions (verify/ASSUMPTIONS.json; OWNER = 9). Commits carry no trailers: no Co-Authored-By, no Claude-Session, no session URL or id. No PRs. Do not edit JOURNAL.md. Game data and decomp-derived artifacts go only to fnv-nx-build. Every check runs in the background.

Goal path: a fully lifted ELF -> exe entry -> WinMain -> main menu -> first frame in the L4 emulator -> the Switch. Next steps on the trunk: (1) the first product root (platform, recipe, msvcmodel and all plugins); (2) the lifted packs as product roots; (3) the ELF; (4) the boot.

Your layer's work, in order:
1. The merge queue through MERGER (session_01KmtZKSxPHjVrmy1iFKiW3g): w8 exepin a6753733, w4 msvcmodel compiler + plugins (claude/fnv-l1-contract-w4, head 3255899), w5 python pins/TMPDIR (red on 4 TMPDIR-leaking tests), L5 imports+machine f15935a9. Trunk a3e9f0a9 + CLAUDE.md 58a79b3e0, 22 gates.
2. Build speed and unity: measure the incremental verdict (no change; one comment-only header; one .cc) on the trunk, publish the numbers, set the budget in VERDICT.md and make it a gate. Every product root goes through recipe.py; find and delete any second build path in verify/.
3. The recipe's external compiler = msvcmodel, plugins as hermetic stages whose .so is in the key (with L2).
4. The assumptions gate over verify/ASSUMPTIONS.json (counts with commit), w11 (test-defined symbols ∩ product-root symbols = ∅, in objstub; report "0 of 0, no roots" while there are no roots), w12 (delete hostapi services 27,28,29,32,33 that the platform does not call).

Reply to the orchestrator once with: the handoff commit you read, your first status file commit, and the workers you run.
