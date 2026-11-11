# L4 VERIFY: handoff note (2026-11-11)

For the next L4 lead. Branch `claude/fnv-l4-verify`; everything on it is in the trunk `claude/fnv-nx-clean` (verdict
green, 7 gates, about 130 s). Merges: send `merge claude/fnv-l4-verify` to the MERGER session.

## What the stream owns

- **Verdict machinery**: `verify/l1.py`, `verify/gates.json` (pure, selftests, admit, build, objstub, linkstrict,
  determinism), `verify/VERDICT.md` (classes of run-time failure and their gates), `verify/admit.json`,
  `verify/RECIPE.md`, `verify/env.json`.
- **Boot stage** (task A12): `verify/boot.py`, `bootmachine.py`, `boothost.py`, `bootlayout.py`, `bootmiles.py`,
  `bootinputs.py`, `bootgpu.py`, `bootpath.py`; farm job `farm/boot_job.sh <elf_id>`.
- **Farm fleet**: farms are cloud sessions (Sonnet) created per job and archived when they report.

## State of the boot stage

- Serves all 35 platform services of the runtime header at `claude/fnv-l1-contract` 0eb49039 (fixture copy:
  `verify/tests/guest/hostapi.h`). Where the header does not determine a result in the stage's environment (no network,
  UTC) the run ends with a reason naming the service. Open questions sent to L1: `SOCKET` with a known domain and type,
  `GAI_OPEN` with a node, the range of `TZ_AT`, `THREAD_ACTIVITY` on a `THREAD_SELF` record.
- First ELF `509809b3b809`: 1,075,895,420 instructions, 1 thread, 342 host calls, exit 0xC000001D after
  `init d3dx9_38.dll` (no lifted module bodies). Equal to L1's own run to the instruction. Results: fnv-nx-build
  `l4-verify/boot/509809b3b809`. Details: `l4-verify-007.md`.
- Milestones: the contract's `milestones.tsv` (exe entry, static module entries, Present import keys) plus the private
  stage table fnv-nx-build `l4-verify/milestones` (WinMain, main-menu). The grammar the stage accepts is in the
  `bootinputs.py` docstring; the columns of `module_entry` and `present_key` are a guess until L1's next file exists.
- GPU: `--gpu` loads `libnxgl4.so` (fnv-nx-build `l6-render/nxgl4/b0be70a6f2d6`, needs pip meson 1.12.1 to rebuild).
  Not used yet: the guest has not reached module 10.
- How to run by hand (under 1 minute): `bootinputs.py join|reserved|keys|milestones`, `icount.py spec`, `boot.py run`;
  the exact command line is in `meta.json` of the result branch. Game files: `farm/gamedata_fetch.sh --no-data`.

## Next steps, in order

1. When L1 publishes the ELF with the L3a pack `977dacc958f4` (`l1-contract/elf/<id>`): run the stage, report the
   milestones and the stopping point to the orchestrator at once, with numbers.
2. Each later stop: decide whether it is a stage defect against the header (fix the stage) or a guest finding (report
   to the owner of the code). Never add a tolerance for a guest fault.
3. After the first frame: the `oracle` gate (class 10), sanitizer builds in the stage runs (classes 14, 15), the `oom`
   gate, the Wine-side virtual clock (fnv-nx-build `l4-verify/vclock-wip`, never built), L2 certification and L3b proof
   shards on farms (both paused by the orchestrator).

## Things that cost time

- Never run `git stash` while workers are writing in the checkout.
- A change is verified only by the `selftests` gate in a clean worktree, as the verdict runs it; tests leave about
  900 MB per run in `/tmp/linkstrict-*`.
- Farm prompts must name the commit, the script, both report addresses, and forbid edits; a farm attaches
  fnv-nx-build itself with add_repo.
- The account rate limit resets every 5 hours; workers cut by it are resumed with SendMessage.
