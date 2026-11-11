# L4 VERIFY: handoff to the Opus lead (2026-11-11, 22:40Z)

From `session_01KmtZKTAZGR8huv8L6r19DK` (stopped) to `session_01KmtZKUcrakBqKb1mC8iXp4`.
Branch `claude/fnv-l4-verify`, head = the commit that adds this file; code head 99f1b8d (5d106a8 + trunk CLAUDE.md 58a79b3).
Trunk `claude/fnv-nx-clean` holds L4 up to 4f1f1e3. Not yet merged: 93477f2 and 5d106a8 (requests below).
The older note `l4-verify-handoff.md` is stale where it differs from this one.

## Numbers (measured at 99f1b8d unless a commit is named)

- L4 test files: 34 (at 9634457). Local runs at 5d106a8, all rc=0: test_bootmachine 145 cases, test_bootgpu 28,
  test_boot_job 43, test_boot 23, test_gates, test_oracle, test_oraclecheck, test_verdict, test_admit; admit 401/401
  files, 9 roots.
- Tests with doubles: 2 files of 34 (test_bootgpu.py, test_boot_job.py), 1 double (the fake `libnxgl4`, `FAKE_C` in
  test_bootgpu.py), 4 symbols. Module-attribute replacements: 0 (were 3 places, 2 names; removed in 5d106a8).
- oraclecheck on the whole test set: not run on a farm yet. Expected 44 of 47 checked; test_envcheck, test_gates and
  test_opentrace cannot be checked (one tracer per process) and are reported as not passing, not as passing.
- Boot of the lifted ELF: not run. There is no ELF id from L1 yet.

## Queue

| # | Item | Status |
|---|------|--------|
| 1 | Merge 5d106a8 (supersedes 93477f2: per-test TMPDIR in gate_tests; boot tests without attribute replacement) | request to MERGER not sent for 5d106a8; 93477f2 was requested, verdict not reported back |
| 2 | **No Mesa (owner).** Remove the nxgl4/llvmpipe path: `verify/bootgpu.py`, `--gpu/--frames` in boot.py, `--gpu-branch` in farm/boot_job.sh, test_bootgpu.py, the gpu cases of test_boot_job.py, TOOLS.md sections, oracle.json entries. This also takes the doubles count to 0 | not started |
| 3 | dk boundary model: `verify/bootdk.py`, `boot.py --dk`. ABI: fnv-nx-build `l6a/dkabi` @071b497 (`dkabi.tsv`: 67 dk* symbols, 206 call sites, +7 libnx; fn = row number). Earlier plan was a host `libdkmodel.so` from L6A; check it against the new rule (model of dk* functions, libnx calls, command buffers; shaders run from the build's DKSH; an uncovered instruction is red). Module id FNVNX_EXT_DK comes from L1 (w12) | not started; blocked on the id (L1) |
| 4 | Boot run (A12): `farm/boot_job.sh <elf_id>` with full Data, on a farm; report milestones, stop point, per-thread instruction counts, wall time | blocked: ELF id (L1) |
| 5 | Remove services 27, 28, 29, 32, 33 (GAI/protocol model, A3/A4) from bootsock.py and its tests, in one merge with L1's new hostapi.h | blocked: header hash (L1 w12) |
| 6 | `farm/runner.sh <trunk commit> oraclecheck` on a farm after item 1 merges; report to the orchestrator | not started |
| 7 | `boot.py test --elf T.elf --root DIR --out OUT.json` and `--inject FILE` (service or symbol, call ordinal, documented result) | not started; needed when L5 has a recipe-built test ELF |
| 8 | Sanitizer job in runner.sh (flags variant of the product root; shadow memory and `__asan_*` in the boot machine) | waits for the first native product root (L5) |
| 9 | ASM-NNN ids in L4 files | L1 w10 does it; do not edit until told |
| 10 | `journal/status/l4.md` (new rule) | not created |
| 11 | After the first frame: fanotify observer for oraclecheck, difffuzz/lockstep/scenario/threads/compare carry, oom gate, vclock, multi-core model, loopback traffic model (if L5 asks) | deferred |

## Sessions I started (all children of my session; told to take START/RUN from the new lead)

| Session | Job | State |
|---------|-----|-------|
| session_01KmtZKSuxC7ks3kferbyC6D | farm z7 1of4 (pristine MSVC /Z7 objects for L2) | reported: fnv-nx-build `msvc/z7/8f324ecde2a7/1of4` @ a8ff7fd5358e, 749 units, 742 built, 7 failed. Can be archived |
| session_01KmtZKS3a468SDWPQW1Drrm | farm z7 2of4 | idle, no result; halted on the tree hash mismatch (L2 is debugging it) |
| session_01KmtZKU9urUtsHPMAyeK4X9 | farm z7 3of4 | same |
| session_01KmtZKTm9tSnKho4mw4Ehyw | farm z7 4of4 | running the tree prepare; same hash question |
| session_01KmtZKSzcic61GPMwx3wqug | farm tc-repro (reproducibility of the msvcmodel compiler build; reports a `TC-REPRO` line to L5 and L4) | blocked on a Bash permission prompt in the session; needs a person or a restart |

All other farms of mine (liftall lf1-5, liftgen G1-4 and H1-4, d3dx9 F-a..c, fontstate, msvcmodel mirror) are archived.
Local: worktree `/home/user/wt-carry` (branch `l4-carry` @931b73f, carry work, deferred) lives only in my container.
Scheduled reminder trig_01RtG4aC5Hj85afRJUUR7zut is deleted with this handoff. No Sonnet worker is running.

## Open questions

1. L6 never answered the request for the nxgl4 commit, tree_sha256 and packages. It no longer matters (item 2).
2. Does the dk model run as a host library (L6A's `libdkmodel.so`) or inside the boot machine in Python? The new
   rule says the model is L4's; L6A (new lead session_01KmtZKSgdFAv1Sd3kGuQtHk) had agreed to write the library.
3. bootsock A1 (SOCKET values) is a model rule; the static search of the exe for a dependence on SOCKET values is
   not done (orchestrator: not now).
4. `rm` of `/tmp/linkstrict-*`, `objstub-*` in my container was denied twice by the permission classifier. Not retried.
5. Game dir for the boot run exists only in my container (`.../scratchpad/game-full`); a farm takes Data from
   fnv-nx-build `gamedata/*`.

## Facts worth keeping

- Kernel codes: MAKERESULT(module, desc) = module | desc<<9, Module_Kernel = 1 (libnx b156b4774fb2 result.h).
  svcSetThreadActivity on the current thread gives Busy, 0xF401 (Atmosphere kern_svc_activity.cpp).
- `verify/gate_tests.py` gives each test file its own TMPDIR and removes it; a red verdict from ENOSPC came from
  leaked test directories before 93477f2.
- Use `get_session`, not `list_events`, on farms.
