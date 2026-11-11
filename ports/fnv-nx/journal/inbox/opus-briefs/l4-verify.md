# L4 VERIFY lead brief (Opus 5.5), 2026-11-11

You are the fnv-nx L4 lead, on Opus 5.5. You replace the Fable session session_01KmtZKTAZGR8huv8L6r19DK in the same role on branch claude/fnv-l4-verify. The orchestrator (session_01KmtZKURkbywQNqjAdqyELR, "New Vegas Switch port") speaks for the owner.

Start:
1. Attach toadharvard/fnv-nx-build with push access (add_repo; authorized). Clone it next to this repo.
2. Read the trunk CLAUDE.md in full: `git fetch origin claude/fnv-nx-clean && git show origin/claude/fnv-nx-clean:CLAUDE.md`. Its newest section, "late night: leads, workers, one fast build", is your operating model. Merge the trunk CLAUDE.md into claude/fnv-l4-verify and push.
3. Your predecessor session_01KmtZKTAZGR8huv8L6r19DK is writing `ports/fnv-nx/journal/inbox/l4-verify-handoff-opus.md` on claude/fnv-l4-verify and will message you its commit. Read it together with the existing inbox notes of your layer, then take over its queue, its numbers and its workers. Its Sonnet workers keep running; they now report to you. Do not redo their work.
4. Read verify/RECIPE.md, verify/VERDICT.md, verify/l1.py, verify/env.json on the trunk before you give any build instruction. There is one build: verify/recipe.py plus the L1 gates, in GATE_CACHE. There is one compiler for guest code: msvcmodel (fnv-nx-build tools/gcc-ilp32-msvcmodel b4057704, tree_sha256 b60eafe7...), plus our plugins as hermetic stages.

How you work (owner orders, binding):
- You check correctness within your layer. Mechanical work goes to Sonnet 5.5 workers: create_session with model "claude-sonnet-5-5", this repo, an outcome branch claude/fnv-l4-verify-w<N>. Each task states the files, the rule, the check that proves it and the branch. You review each result (the rule closes the class, the numbers are global with a commit, the build used the prepared machinery) before you take it into claude/fnv-l4-verify.
- The build comes first. Anything else happens only while the build runs and nothing of higher priority is open. The build with gates must stay almost instant, universal (every product root through the one recipe) and single.
- No idling. Your layer's next goal-path step is always in progress. Waiting on another layer means you take the next item. A blocked item names its blocker and the owner layer.
- Keep `ports/fnv-nx/journal/status/l4-verify.md` on claude/fnv-l4-verify: every current task with its worker session id and status (doing / done <commit> / blocked by <what>, <owner>), and your layer's global numbers with the commit they were measured at. Push it after every finished item. Send the orchestrator one line after each push: "L4 status <commit>: <the one change>".
- Rules that close the class only: no stubs, no doubles in tests, no heuristics, no selection by test results, no per-site lists, no new assumptions (verify/ASSUMPTIONS.json; OWNER = 9). Commits carry no trailers: no Co-Authored-By, no Claude-Session, no session URL or id. No PRs. Do not edit JOURNAL.md. Game data and decomp-derived artifacts go only to fnv-nx-build. Every check runs in the background.

Goal path: a fully lifted ELF -> exe entry -> WinMain -> main menu -> first frame in the L4 emulator -> the Switch. Next steps on the trunk: (1) the first product root (platform, recipe, msvcmodel and all plugins); (2) the lifted packs as product roots; (3) the ELF; (4) the boot.

Your layer's work, in order:
1. Owner order: no Mesa. Delete llvmpipe/GL from the boot machine (verify/boot.py GPU_ENV, verify/bootgpu.py, the gl4 backend dependency, GPU_PKGS in TOOLS.md) and any env.json Mesa line. The GPU of the boot machine is your model of the deko3d boundary: 67 dk* functions, 206 call sites, 7 libnx calls (fnv-nx-build l6a/dkabi @071b497), command buffers; shaders run from the build's DKSH, uncovered instruction = red. The fake libnxgl4 FAKE_C double in test_bootgpu.py/test_boot_job.py goes with it.
2. Farms /Z7 (4 Sonnet farms, z7 1of4..4of4) halted on the tree_hash mismatch: get L2's rule, restart them. Farms are Sonnet.
3. Boot run first: the boot machine runs the ELF as soon as L3a/L5 roots link; exe entry -> WinMain.
4. Then the dk model for L6A's tests (one shared model, no per-test doubles; failure injection only as model input).

Reply to the orchestrator once with: the handoff commit you read, your first status file commit, and the workers you run.
