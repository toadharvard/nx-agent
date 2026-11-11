# L4 VERIFY: handoff note (2026-11-11)

For the next L4 lead. Branch `claude/fnv-l4-verify`; everything on it is in the trunk `claude/fnv-nx-clean` (verdict
green, 8 gates, about 135 s). Merges: send `merge claude/fnv-l4-verify` to the MERGER session.

## What the stream owns

- **Verdict machinery**: `verify/l1.py`, `verify/gates.json` (pure, selftests, admit, oracle, build, objstub,
  linkstrict, determinism), `verify/VERDICT.md` (classes of run-time failure and their gates), `verify/admit.json`,
  `verify/RECIPE.md`, `verify/env.json`.
- **Boot stage** (task A12): `verify/boot.py`, `bootmachine.py`, `boothost.py`, `bootlayout.py`, `bootmiles.py`,
  `bootinputs.py`, `bootgpu.py`, `bootpath.py`; farm job `farm/boot_job.sh <elf_id>`.
- **Oracle gate** (class 10, in the trunk): `verify/gate_oracle.py`; every test file is declared in
  `DIR/tests/oracle.json` (subject + references). A `spec` reference is a tracked file plus an anchor (a heading or
  `L<m>-L<n>`) outside the import closure of the subject; a tool's stated rule is its section of `verify/TOOLS.md`.
  A `wine-exe` reference is a fnv-nx-build commit + `tree_sha256` + path, fetched like a recipe external; the exe
  sha256 is pinned in `verify/oracle_pins.json`. A new test file without an entry makes the trunk red.
- **After-merge verification**: `farm/runner.sh <commit> <job>...` runs in a detached checkout of the commit, in the
  declared environment only, and publishes fnv-nx-build `l4-verify/after-merge/<commit12>`. Job `oraclecheck`
  (`verify/oraclecheck.py`): each test runs under strace; the tracked files it opens must lie in the closure of its
  declared subject, its references or its tests directory, and every subject file must be opened. It reports and
  decides nothing.
- **Farm fleet**: farms are cloud sessions (Sonnet) created per job and archived when they report.

## State of the boot stage

- Serves all 36 platform services of the runtime header (module 11, header c56e20ff; fixture copy
  `verify/tests/guest/hostapi.h`), including `TEXT_ENTRY`. The earlier questions to L1 (`SOCKET`, `GAI_OPEN`, `TZ_AT`,
  `THREAD_ACTIVITY`) are answered and implemented.
- Inputs of a run, all checked by sha256 before the first instruction (`farm/boot_job.sh <elf_id>`):
  - the contract ELF branch fnv-nx-build `l1-contract/elf/<id>` (parts, `manifest.json`, `entries.tsv`, `keys.tsv`,
    `modules.tsv`, `milestones.tsv`);
  - the game files, with every `gamedata/data/*` archive by default (`BOOT_DATA_ITEMS` narrows the list; `--no-data`
    in it means none); `meta.json` records `data_items`;
  - with `--gpu-branch`: the renderer pair. The manifest's `render` field names the bridge branch and its archive
    sha256; `bootinputs.py pair` requires manifest == `build.json` == archive bytes, `meta.json` sha256 == library
    bytes, and bridge `nx_agent_commit` == nxgl4 `nx_agent_commit`. Any mismatch ends the job before the run
    ("renderer pair: ...").
- Current renderer pair (one head, 13c4573c49d7): `l6-render/bridge/13c4573c49d7` and `l6-render/nxgl4/13c4573c49d7`.
  L6's own gate d3d9total is 332/332.
- Milestones: the contract's `milestones.tsv` (WinMain, DllMain, menu addresses come from L1) plus the stage table
  fnv-nx-build `l4-verify/milestones`. The grammar is final and is in the `bootinputs.py` docstring.
- Last finished run: ELF `509809b3b809`, 1,075,895,420 instructions, 1 thread, 342 host calls, exit 0xC000001D after
  `init d3dx9_38.dll` (no lifted module bodies), equal to L1's own run to the instruction. Results: fnv-nx-build
  `l4-verify/boot/509809b3b809`.
- Relift under lifter af437959275a (claude/fnv-l3a-lift): exe 64/64 shards, d3dx9_38 32/32, three small modules,
  failed 0; every LIFTGEN line went to the L3a lead. L3a builds the pack with the msvcmodel compiler, L1 links it
  with bridge 13c4573c49d7 and sends the new ELF id.
- The game's compiler mirror: fnv-nx-build `tools/gcc-ilp32-msvcmodel` commit b4057704c0ba, tree_sha256
  b60eafe7a5b0ae3d... (full values in the L1 recipe), built twice on farms, byte-equal.
- L5's font reference: fnv-nx-build `tools/fonts` commit fcf99a121a05 (`farm/fontstate_job.sh`, Wine 9.0).
- How to run by hand: `bootinputs.py join|reserved|keys|milestones|render-branch|pair`, `icount.py spec`,
  `boot.py run`; the exact command line is in `meta.json` of the result branch.

## Next steps, in order

1. When L1 sends the new ELF id: `farm/boot_job.sh <id> --gpu-branch l6-render/nxgl4/13c4573c49d7` with full Data.
   Report milestones, the stopping point, per-thread instruction counts and wall time to the orchestrator at once;
   send L6 the first d3d9/nxgpu failure with its log; send L1 any contract discrepancy.
2. Each later stop: decide whether it is a stage defect against the header (fix the stage) or a guest finding (report
   to the owner of the code). Never add a tolerance for a guest fault.
3. `farm/runner.sh` jobs still to add: the sanitizer job for classes 14 and 15 (waits for the first native product
   root in the trunk; the guest's libc is newlib on libnx and the pinned cross packages carry the sanitizer
   runtimes for LP64 Linux only, so the form of the sanitized build is a question sent to the orchestrator), then the
   verifiers difffuzz, lockstep, scenario, threads, compare (verifiers only: no causes policy, crosscheck, ratchet).
4. After the first frame: the `oom`
   gate, objstub on the L6 bridge, the Wine-side virtual clock (fnv-nx-build `l4-verify/vclock-wip`, never built), L2
   certification and L3b proof shards on farms (both paused by the orchestrator).

## Things that cost time

- Never run `git stash` while workers are writing in the checkout.
- A change is verified only by the `selftests` gate in a clean worktree, as the verdict runs it; tests leave about
  900 MB per run in `/tmp/linkstrict-*`.
- Farm prompts must name the commit, the script, both report addresses, and forbid edits; a farm attaches
  fnv-nx-build itself with add_repo.
- Commit messages carry no trailers (owner, 2026-11-11): no Co-Authored-By, no session id, in any commit or file;
  `verdict.py` and `boot_job.sh` write none.
- `list_events` on a farm session returns a very large output: use `get_session` and wait for the farm's message.
- The account rate limit resets every 5 hours; workers cut by it are resumed with SendMessage.
