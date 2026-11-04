# Farm jobs

A farm machine is a separate cloud session that runs heavy jobs and does no development (`docs/MACHINES.md`). The
development agents stay in the orchestrator session. A farm runner:

1. watches the integration branch `claude/port-nsw-un6t9s` for new commits;
2. runs its job scripts from this directory on each new commit, in the background;
3. commits the text reports they write to its own branch `claude/port-nsw-farm<N>`, under
   `ports/fnv-nx/reports/farm<N>/`, and pushes.

Development agents read the reports with
`git fetch origin claude/port-nsw-farm<N> && git show origin/claude/port-nsw-farm<N>:ports/fnv-nx/reports/farm<N>/<file>`.

| job | script | needs | report |
|---|---|---|---|
| full compile census | `census_job.sh <commit> <report.md>` | git, python3, jq, `g++-aarch64-linux-gnu` | counts, gained/lost against the previous report, failing units with their first error |
| static gate, shard k of N | `static_job.sh <commit> <k> <N> <report.md>` | git, python3, $ARTIFACTS (tree via fetch_tree.sh, prepare as fallback); checks each needed file against its package and installs (or reinstalls) clang-18, libclang-18-dev, llvm-18-dev (clang-18 does not pull it in), lib32stdc++-13-dev, libc6-dev-i386, `g++-aarch64-linux-gnu`, jq, rsync (never g++-multilib: apt removes the cross compiler for it) | shard, timings, coverage, sites per class, every site as `file line CLASS` (no detail text); `<report>.tsv` (class, file, line) merges across shards with `sort -u`; a FAILED report and exit 2/3 when a tool is missing, fnvcheck does not build, GCC compiles nothing or clang writes no rows |
| link objects, shard k of N | `compile_job.sh <commit> <k> <N> [report.md]` | + rsync; the private repository `toadharvard/fnv-nx-build` attached with push | objects on branch `c/<commit12>/<k>of<N>` of the artifacts repository (meta.json: commit, tree hash, counts) |
| MSVC equivalence gate, shard k of N | `equiv_job.sh <commit> <k> <N> <report.md>` | root (installs Wine; VC 2008 SP1/RTM from branch `toolchain/msvc9`, else from Microsoft's ISOs, ~6 GB disk), curl; the artifacts repository with push | units identical / differ / no-build, differing functions by kind (EH-only, retarget, code) and rule, per-rule table (docs/EQUIVALENCE.md); pristine MSVC objects on branch `msvc/pristine/<decomp12>` (built once, as `msvc/pristine-part/<decomp12>/<k>of<N>` parts until all N exist); the unpacked compilers on branch `toolchain/msvc9` (published by the first farm that unpacked the ISOs; `equiv_job.sh --toolchain` does only that step) |
| differential fuzzing, shard k of N | `fuzz_job.sh <commit> <k> <N> <report.md>` | + g++/binutils-aarch64-linux-gnu, python3 venv (unicorn, capstone); the newest `link/<commit12>` (link_job.sh) on the commit or an ancestor, waited for up to 30 min, with that link's tree (fetch_tree.sh) and its rewritten objects; once per decomp commit Wine + MSVC 2008 (root) unless branch `msvc/pristine/<decomp12>` exists | names the link used; incremental: only functions whose key (oracle code, native code + relocations of the function and what it reaches, harness version) changed are fuzzed again, the verdict cache is branch `fuzz/cache/<k>of<N>`; verdicts by stream and cause new this head and cumulative, cumulative hard divergences (address, cause, stream, unit); this head's reproducers on branch `fuzz/<commit12>/<k>of<N>`; fuzzing stops in time to report before runner.sh's `FARM_DEADLINE` (the 2 h background limit), the rest stays pending for the next run; a machine-readable summary `<head12>-fuzz.json` (fnvnx-fuzz-summary/1: functions, coverage %, hard divergences by cause, verdict classes, status complete/partial) beside the report on `reports/<farm>`, the same object in the report's last ```json fnvnx-fuzz-summary``` block |
| V1-link full-set audits | step 4d of `full_job.sh` on its own link (nothing fetched; `FARM_V1=0` skips it), or `v1_job.sh <commit> <report.md>` alone | qemu-user (the bridge runs and qemu reproducers are skipped without it), llvm-undname, c++filt, `g++/binutils-aarch64-linux-gnu` (ILP32), python3 (pyelftools, capstone); branch `link/<commit12>` (out/ + fixed/; trial.elf or a relink in the job's directory), `t/<commit12>`; optional: the compile shards (C1 sampler), `msvc/pristine/<decomp12>` (C3) | exit status, seconds and summary counts per audit (C1-C5, bridge execution, reproducers) within `V1_BUDGET` seconds (default 1200; about 6 min needed, audits that no longer fit are skipped and marked so); in `full_job.sh` the audit table goes into its report and the detail tables to `link/<commit12>/v1/`, alone to branch `v1/<commit12>` |
| link by address | `link_job.sh <commit> <report.md>` (runner job `link`) | git, python3, make, curl, jq, rsync, dpkg-deb, gcc, llvm-18 (llvm-undname), python pyelftools and capstone (`FARM_APT=1` installs them); the artifacts repository with push. The tree from the prepare farm (`fetch_tree.sh`: `t/<commit12>` or the rolling tree with the same prepare inputs); the objects of the newest commit C (the head or an ancestor) whose four compile shards `c/<C12>/<i>of4` exist and whose compile inputs equal the head's; the pristine MSVC objects (`msvc/pristine/<decomp12>`) for `resolve --msvc-code`; the player's exe (`gamedata/exe`, sha256 pinned, `job_gamedata`; `FARM_REAL_EXE=0` leaves it out) as `FNVNX_EXE` for `fnvlink bodies`' exe-addr class, read only and in the link stage's fingerprint; the cross compiler from pinned Ubuntu packages when the host's is not that build | summary numbers (references, by address, bridges, ABI verdicts, address map, trial link, split-global check, seconds per step: resolve, probe with `-j$(nproc)` as layouts and vtables, resolve, apply, trial link) and the commit whose objects were linked; branch `link/<commit12>`: `out/{aliases.ld,refs.tsv,abi.tsv,addrmap.tsv,layouts.tsv,typeinfo_pairs.tsv,cache/msvc.tsv,vtables/}`, `fixed/*.o`, `summary.txt`, `report.md`, `meta.json` (`objects_commit`); every publish also on branch `link-head` |
| full build + emulated boot | `full_job.sh <commit> <report.md>` (runner job `full`) | `link_job.sh`'s, plus g++, python unicorn; once: libgmp/mpfr/mpc-dev, texinfo, bison, flex to build fnvnx-a64. fnvnx-a64, DEVKITPRO/libnx and Ogg Vorbis from pinned sources (`docs/BUILD.md`, "A fresh machine") | `link_job.sh`, then guest, emulated boot (S14's synthesized image from the pristine MSVC objects) and package: provenance, stage table, undefined names by class, boot reached + first fault, skipped initializers by fault, boot frontier top 20, package check (`full_report.py`); `link/<commit12>` again with `guest/fnvnx.elf`, `boot/{summary.txt,frontier.txt,frontier.tsv,emu.log}` (raw boot), `boot/stub/` (S5's stub-fault boot, `tests/s5/farm_boot.sh`: the frontier past `tests/s5/known_faults.txt`) `boot/synth.exe` + `boot/synth_summary.txt` (the data image, sha256 in `meta.json`) and `v1/` (V1-link's audits of that link, `v1_job.sh`, their table also in the report) |
| readiness scorecard (R0) | `gates_job.sh <commit> <report.md>` (runner job `gates`; `RUNNER_REPEAT_S=1800` scores the same head again as reports arrive) | git, python3; its own bare partial clone of the artifacts repository, `$FARM_HOME/gates-artifacts.git` | `tools/gates/ready.py`'s scorecard (`docs/READINESS.md`): `<c12>-gates.md` and the `<c12>-gates.json` sidecar |

Consumers collect a commit's objects with `tools/farm/fetch_objs.sh <commit> <N> <obj dir> [prepared tree]`. The
result is the same layout as `tools/link/compile.sh` output (`objects.tsv` + objects). With a prepared tree, the
script also checks that the farm compiled exactly that tree (tree_hash.sh). The compile job is incremental: a commit
that does not change the prepared tree recompiles nothing, and a changed unit source recompiles only that unit.
Headers, flags or the compiler changing rebuilds the shard. The session git proxy refuses tag pushes and ref
deletions, so artifacts live on branches.

| prepared tree for a commit | `prepare_job.sh <commit> [report.md]` | the artifacts repository with push | branch `t/<commit12>` (tree, meta.json); reused when no prepare input changed |
| whole-program ordering keys | `keys_job.sh <commit> [report.md]` (runner job `keys`) | the prepare farm's tree (fetch_tree.sh, prepare as fallback), `g++-aarch64-linux-gnu`, gcc-13-plugin-dev-aarch64-linux-gnu, g++, jq, rsync; the artifacts repository with push | branch `k/<commit12>` and rolling `keys` (order.keys, meta.json with tree and inputs hash); compile_job.sh waits for it (`KEYS_WAIT`) and computes the same list itself when it does not come (docs/ORDERING.md "Whole-program keys") |

**Running a farm machine.** `tools/farm/runner.sh <name> <job> [args]` is the whole loop in one process:
- it watches the public integration branch (read-only, no credentials);
- it runs the job at each new head;
- it publishes the report to branch `reports/<name>` of the artifacts repository, with its `.json` sidecar
  (`<report>.json`) when the job wrote one;
- it exits by itself before a 2 h background limit, and is then started again.

A farm session is created from the private artifacts repository at branch `main` (`source_revision: main`). It
needs no repository attach. The agent runs exactly `bash /home/user/fnv-nx-build/farm/start.sh <name> <job> [args]`
in the background, and `main`'s `.claude/settings.json` allows that command; the rule was set at the owner's request
(J-0079). `start.sh` clones the public nx-agent, checks it out at the integration head and runs this `runner.sh`.
Artifacts and reports go to a separate clone at `$FARM_HOME/artifacts`. The agent only keeps the runner alive, with
a check-in every hour. That check-in is a scheduled task of the owner's account bound to the farm session, so it fires server-side even after an account rate-limit stop; a farm comes back within an hour of a reset (J-0101). Farm sessions run on Haiku, since they only keep one script running. Fuzzing runs as 26 shards (`start.sh fz<k> fuzz <k> 26`), so all farms fit under the concurrent-session cap of about 50. Reports of
runners are read from the artifacts repository:
`git -C /home/user/fnv-nx-build fetch -q origin reports/<name> && git -C /home/user/fnv-nx-build show origin/reports/<name>:<file>`.

`link_job.sh` links in one build directory, `$FARM_HOME/link/build`, through the pipeline (`make link`), so
fnvlink's content-keyed demangle, layout and vtable caches carry over between commits: only changed units are
probed again (31-unit subset: probe 119 s cold, 0.5 s warm). It waits for the tree (`FARM_TREE_WAIT`, default
30 min) and for objects (`FARM_OBJ_WAIT`, default 1 h; `FARM_OBJ_SHARDS`, default 4; `FARM_OBJ_DEPTH` commits
back, default 300), and publishes `link/<commit12>` only for a complete link prepared from that commit. A
branch named `link` cannot exist next to `link/<commit12>` (git refs are paths), so the rolling branch is
`link-head`; each publish is a child of its newest commit, so a push sends only what changed. `full_job.sh`
runs `link_job.sh` in its own build directory (`$FARM_HOME/full/build`) and keeps one toolchain per toolchain
version in `$FARM_HOME/toolchain-<id>` (built the first time, about an hour on 4 cores and resumable, then
reused). `FARM_MAKE_ARGS="UNITS=<list>"` runs either job on a few units to check it (not published).

**Try farms** check work before it is merged. The orchestrator runs `tools/farm/push_wip.sh 120`, which pushes every
agent worktree head that is ahead of the integration branch to `wip/<agent>` of the artifacts repository. A try farm
runs the census on those commits. Naming a farm `try<k>of<n>` switches the runner to that mode
(`farm/start.sh try2of4 census`). It publishes `reports/try<k>of<n>/wip_<agent>-<commit12>-census.md`. An agent reads its own report
there and sees the full-tree effect of its rule change in about 10 minutes, without a merge.

Rules for every job:

* Work trees live under `$FARM_HOME` (default `~/fnvnx-farm`), never inside the repository checkout.
* Reports hold numbers, unit names and short diagnostics only. Prepared source, objects, address lists, game files
  and Microsoft binaries are never committed (the repository is public).
* A job must be runnable by someone who knows nothing about it, so its script installs or fetches what it needs,
  or says what it needs in its header.
