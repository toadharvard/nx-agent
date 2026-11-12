# fnv-nx build pipeline (stream S9-build)

One entry point, `ports/fnv-nx/Makefile`, runs the whole build from the pinned decomp to an SD-card tree, then the
host gates, and writes `build/report.md`. The Makefile forwards its variables to `tools/pipeline/build.py`, which
runs the stages. Each stage calls the documented command-line interface of the stream that owns the step
(`tools/fetch.sh`, `tools/prepare.py`, `tools/census.sh`, `tools/link/compile.sh`, `tools/link/fnvlink.py`,
`toolchain/game/build.sh`, `tests/s5/emu_guest.py` and `frontier.py`, `tests/s5/hello/build.sh`,
`render/nine/build.sh`, `toolchain/tools/*.py`, the test runners). Where the interface does not exist yet, a thin
adapter in `tools/pipeline/` stands in (see "Adapters").

The stages do what the hand-driven runs of S2c (`full.sh`: prepare, compile, resolve / probe / resolve / apply /
trial link) and S5 (guest link, emulated boot, boot frontier) do, with the same tools, flags and order; "The hand
flow and the pipeline" below lists every step and what still differs. Every output derived from the prepared tree
carries the commit it was prepared from, and a stage refuses inputs that are not from the current head (J-0054,
"Provenance").

Everything under `build/` is derived from the decomp or the player's game. It stays on the developer's machine and
is never committed (`build/` is git-ignored).

## Quick start

```sh
cd ports/fnv-nx
make BOOT=1                                                    # a fresh machine: everything from pinned sources
make FNVNX_TC=/opt/fnvnx-a64 DEVKITPRO=/opt/fnvnx-dkp          # reuse a toolchain; checkouts go to ext/
make FNVNX_TC=... DEVKITPRO=... EXT=/path/to/ext               # reuse existing pinned checkouts (read only)
make ... PROGRAM_ID=01xxxxxxxxxxxxxx                           # title whose exefs the build overrides
make ... NINE=1                                                # Gallium Nine in the host module
make ... BOOT=1 PRISTINE_OBJS=<S11 pristine objects>           # the boot on S14's synthesized data image
cat build/report.md
```

The full run S2c and S5 drove by hand (J-0056, J-0057), as one command, on the dev machine (its toolchain, its
seeds; the extern-text list is made by the pipeline):

```sh
nice -n 19 make EXT=$S/ext FNVNX_TC=$S/s5/tc/fnvnx-a64 DEVKITPRO=$S/s5/dkp \
    LINK_SEED=$S/s2c/latest/out JOBS=2 BOOT=1 [PRISTINE_OBJS=$S/s11/obj/pristine]
```

Run it from a checkout (or worktree) at the commit to build: the tree is prepared from that commit and every later
stage checks it. `UNITS=<list>` restricts census and compile (and so the link and guest) to a few units for a quick
check of the pipeline itself (see "Subset run"). See "A fresh machine" for what the stages fetch and build.

`DEVKITPRO` alone is enough when `$DEVKITPRO/devkitA64` points at the fnvnx-a64 prefix (as
`toolchain/devkita64/setup.sh` makes it). Without either, the toolchain stage builds both (`TC_DL=auto`, about an
hour on 4 cores; docs/TOOLCHAIN.md §3) into `build/toolchain/prefix` and `build/toolchain/dkp`.

Other goals: `make <stage>` runs that stage and every stage it needs. Several stage goals are one run
(`make link tests`). `make report` re-renders the report, `make status` prints the stage table, `make help` lists
the variables, `make clean` removes `build/`, and `make clean-<stage>` removes one stage directory.

## Stages

| stage | output | what runs | needs |
|---|---|---|---|
| `fetch` | `build/fetch/pins.tsv` | Checks that `fnv_decomp`, `sse2neon` and `libnx` are at the commits `tools/fetch.sh` pins. It reads the checkouts' HEAD files and never runs git on them. Without `EXT`, it runs `tools/fetch.sh ext/` when a pin is missing. | - |
| `toolchain` | `build/toolchain/{tools.tsv,toolchain.env}` (+ `cross/`, `prefix/`, `dkp/` when it makes them) | Provides what is not given, then validates fnvnx-a64 (version, the ILP32 multilib with libc/libsysbase/libm/libgcc/libstdc++/libsupc++), DEVKITPRO (libnx, switch.specs, packers) and the host tools each later stage uses, and chooses the game compiler. Provides: the AArch64 cross tools (`CROSS=auto`: the system's when they are the pinned Ubuntu build, else `toolchain/cross/setup.sh` unpacks the pinned packages, no root); fnvnx-a64 and the DEVKITPRO tree with the pinned libnx (`TC_DL=auto`: `toolchain/sources.sh` fetches the pinned tarballs and devkitPro checkouts, then `toolchain/gcc/build.sh` and `toolchain/devkita64/setup.sh`, ~1 h on 4 cores, rebuilt only when those scripts change). Given `FNVNX_TC` / `DEVKITPRO` / `CROSS_BIN` are used as they are and never modified. | fetch (soft) |
| `prepare` | `build/prepare/{tree/,prepared_from.txt,prepared_from.json}` | `tools/prepare.py <decomp> build/prepare/staging`, then a sync into `tree/` that keeps unchanged files, mtimes included. The compile step then sees only real changes. Also writes `prepare_stats.tsv`, `changed.txt` (A/M/D) and the provenance record: the commit of ports/fnv-nx (first line of `prepared_from.txt`, the format of S2c's driver), the decomp commit, uncommitted changes to the prepare inputs. Reruns when the head or any prepare input changes (`tools/prepare.py`, `tools/rules/`, `patches/`, `shim/include/fnvnx_win32.h`, `render/native/drop_units.txt`). `PREPARED=<tree>` adopts a tree another run prepared (read only) with the provenance its `prepared_from.txt` states. | fetch |
| `census` | `build/census/` | `tools/census.sh` (`-fsyntax-only` of every unit, or of the `UNITS` list) with the game compiler. `CENSUS=0` skips it. | prepare |
| `compile` | `build/compile/{obj/,objects.tsv,failed.tsv}` | `tools/link/compile.sh` (its flag set: `-O2 -fPIC`, J-0034's MSVC semantics, `-Werror=return-type`) with `CXX="<game compiler> -MD"`, the `UNITS` list or every unit. Objects a restart left truncated are deleted first (S2c's objcheck); an object is deleted when a header in its `.o.d` changed, since compile.sh checks only the unit's own source. Units that fail are listed with their first error in `failed.tsv` and excluded from the link. If the compiler, `OPT`, compile.sh, its `FLAGS=(...)` array or the tree's path changes, every object is rebuilt (the flag text is in the cache key and in the report). | prepare |
| `link` | `build/link/{out/,fixed/,summary.txt,check.txt,apply.log,check_rebind.txt}` | S2c's order: `fnvlink.py resolve`, `probe` (layouts and synthesized vtables/typeinfo), `resolve` again, `apply`, `check` (trial link), then S13's `check_rebind.py` on the rewritten objects (want 0). `EXTERN_TEXT`, `OVERRIDE`, `MSVC_OBJS`, `UNITS_RENAMES` pass `--extern-text`, `--override`, `--msvc-objs`, `--units-renames`; `LINK_OVERRIDES=render` passes S6n's table and libnxrender.a; the player's exe (`FNVNX_EXE`, else a `BOOT_IMAGE` file) goes to both resolves as `--exe` (S2c-vt: RTTI pairing, slot fill), else `--exe none`. Probes compile with the game compiler and `layout.flags()`. `LINK_SEED` seeds the demangle cache. `summary.txt` starts with `tree prepared from branch commit: <commit>` like S2c's run summaries. Objects that apply cannot rewrite are listed and left out. | compile |
| `guest` | `build/guest/{fnvnx.elf,guest.nso,undefined.txt,undefined_by_category.tsv,dynbrk.txt}` | The full ILP32 guest link: S2's fixed objects, `aliases.ld`, bridges, addrmap, the guest runtime, `platform/`, `loader/`, S10's startup objects, boot code. NINE=0 uses S5's `toolchain/game/build.sh` with `D3D9=null` (headless null device) and `GAME_NEW` (default `runtime`), a relocatable guest (`GUEST=pie`, `PIN=auto`). NINE=1 uses S6c's `render/nine/game/build.sh` with `GUEST_ONLY=1` (`D3D9=external`, Nine proxy guest, D3DX). The adapter is used only when neither script exists, or with `GUEST_LINK=adapter`. Then S5's checks: PC-relative references to absolute image symbols (`pcrel_abs.py`, want 0) and initializers / finalizers holding a trap (`tests/s5/dynbrk.py`, want 0). Undefined symbols are sorted by category and owner (`tools/pipeline/undef.py`). | link, toolchain |
| `boot` | `build/boot/{emu.log,summary.txt,frontier.txt,frontier.tsv,frontier_units.tsv}` | Optional: with `BOOT=1` or `make boot`. S5's emulated boot of the game guest: `tests/s5/emu_guest.py guest.nso --elf fnvnx.elf --skip-faulting-init --expect "fnvnx boot: calling WinMain"` (Unicorn, at most `EMU_TIMEOUT` s; `BOOT_IMAGE` goes to `sdmc:/fnvnx/game/FalloutNV.exe`, e.g. S14's synthesized exe), `tests/s5/emu_summary.py`, then the boot frontier `tests/s5/frontier.py` (every untranslated address hit, its units, their status in this link and in the census: this build's, or `BOOT_CENSUS`). Informational: `ok` when WinMain is entered, `warn` when not; it fails only when the emulator itself breaks. | guest (census optional) |
| `nine` | `build/nine/{work/,proxy/}` | NINE=1 only. `render/nine/build.sh` (Mesa st/nine + nouveau for Switch, LP64). `render/nine/gen_proxy.py` supplies the proxy method statistics. | toolchain |
| `host` | `build/host/{host.elf,main,main.npdm}` | The LP64 host module. NINE=0: S5's `tests/s5/hello/build.sh` recipe (`host.c` + libnx + S10's window reservation). NINE=1: S6c's `render/nine/switch_host.sh` (host.c without console + `render/nine/host` + Mesa + libnx). Then elf2nso (`main`) and npdmtool (`main.npdm`). | toolchain (nine) |
| `package` | `build/package/{exefs.nsp,sd/,nro/,nx1.json}` | `build_pfs0.py` makes `exefs.nsp` (main + main.npdm); `toolchain/nro/build.sh` the launcher `fnv.nro`. The SD tree is `switch/fnv/{fnv.nro,fnv-exefs.nsp,guest.nso,README.txt}` (`fnvnx/sdlayout.h`, docs/NRO.md); no program id is needed, the launcher installs the override for the title it runs under. Then `tests/s5/check_package.py` (`check_package_rc`) and the NX1 device-readiness checks `tests/nx1/run.py` (`nx1_open`, `nx1.json`); full_job.sh publishes the package to the private branch `nro/<commit12>`. | host (guest optional) |
| `tests` | `build/tests/<suite>.log`, `results.json` | All host gates (below). Each suite is cached on a hash of its own inputs. | - |
| `report` | `build/report.md`, `report.json`, `history/<run>.json` | Every run, at the end. | - |

Each stage records `build/<stage>/stage.json`. The record holds the status, the input fingerprint, metrics,
artifacts, notes and `stage.log` (every command with its output and exit code). `build/pipeline.log` has the
progress lines of all runs.

### Status, incrementality, partial failure

* `ok`, `warn` (it produced its output, but something is incomplete, e.g. failed units, undefined symbols or a
  placeholder PROGRAM_ID), `fail`, `skipped` (a stage it needs failed), `disabled` (by configuration).
* A stage runs again only when its fingerprint changes. The fingerprint hashes:
  * the owning tools' files;
  * the variables;
  * the output ids of the stages it reads;
  * a version tag of the stage's own logic in build.py (`compile-v2`, ...).

  A stage also reruns when its last status was not ok/warn, when its output is missing, or when it is listed in
  `FORCE=` (`FORCE=all` reruns everything). Output ids are content-based. A stage that reran but produced the same
  output (for example identical objects) leaves the stages after it cached. `fetch`, `toolchain` and `tests` are
  cached the same way: fetch on the checkouts' HEADs, toolchain on the resolved path, size and mtime of every tool
  it checks, tests on every selected suite's fingerprint. A rerun with nothing changed shows every stage `cached`.
* Inside a stage, the tools' own rules apply too:
  * compile.sh keeps up-to-date objects; the pipeline adds header dependencies.
  * A unit that failed is retried only if its source or a dependency in its `.o.d` is newer than its log (GCC
    writes the `.d` when the error comes after preprocessing), if it has no `.d`, or if files were added to or
    removed from the prepared tree.
  * fnvlink keeps its demangle cache, layouts and vtable caches in `build/link/out/`; its probe caches are keyed
    on each unit's content (`layout.unit_key`: prepared source and headers), so only units that changed are
    probed again (subset: probe 119 s cold, 0.5 s warm).
  * Test suites are cached only when they passed; a failing suite runs again on every run.
* Checkpoints (a killed run resumes; finished stages are skipped): a stage records `running` before it starts and
  its result only at the end, so an interrupted stage reruns and the finished ones stay cached. Inside the long
  stages:
  * census keeps every unit's result in `build/census/cache/`, under the stage fingerprint, through a caching
    compiler wrapper passed to census.sh as `CXX`;
  * compile keeps every finished object; a unit interrupted mid-compile (left `.o.tmp`, or a log without an error)
    is compiled again;
  * the link caches stay in `build/link/out/`;
  * tests write `results.json` after every suite.

  On a shared machine, use `JOBS=2`. render/shaders/build.sh (suite s6b) runs ninja with its own default
  parallelism.
* The pipeline never stops at a failing stage. Stages that need it are skipped, and the independent ones run,
  including the tests. The exit code is 1 when any stage failed, including a failing test suite. Warnings do not
  set it.
* Measured on 4 shared cores, decomp `87eb9373`, port `e4d2abb`:
  * full run from an empty `build/`: 60 min, of which census 25 min, compile 19 min, link 6 min, guest link
    50 s and tests 8 min (s3 3 min; s6b 5 min, including cloning and building MojoShader and uam);
  * nothing changed: 1 s;
  * one header of 4 units touched: 4 min (compile 22 s, then link 2 min and guest 1.5 min, which redo the whole
    image);
  * NINE=1 adds the Mesa build: 16 min the first time, 40 s when nothing changed.
* The numbers above predate J-0034 and the merge of S5's game link (port `e4d2abb`). The final run's numbers are
  in `build/report.md`.

### Provenance (J-0054)

* The prepare stage records where the tree comes from: `build/prepare/prepared_from.txt` (first line: the commit
  of ports/fnv-nx, i.e. the checkout's HEAD, read from its git files; then `decomp <commit>`, the mode and any
  uncommitted changes to the prepare inputs) and `prepared_from.json`. A copy without git metadata (a `git
  archive` snapshot like S2c's `snap_<commit>`) states its commit with `PORT_COMMIT`. With uncommitted changes
  to the prepare inputs the stage warns: the commit alone does not describe the tree.
* Every stage that consumes tree-derived outputs (census and compile: the tree; link: the objects; guest: the
  link; boot: the guest; package: the guest when it is packaged) copies that record into its `stage.json`
  (`prepared_from`); the report prints it per stage and in its "Provenance" section, and the link's
  `summary.txt` starts with `tree prepared from branch commit: <commit>`.
* Before it runs or is reused from the cache, such a stage compares the record of its inputs with the current
  head. When they differ (or the record is missing, unknown, or mixed between its inputs) the stage **refuses**:
  status `fail`, error `stale inputs: inputs prepared from X, the head is Y`, and the stages after it are
  skipped. `FORCE_STALE=1` lets it consume them; it is then `warn` and records `stale`.
* Since the prepare fingerprint holds the head, a new commit prepares the tree again. When the new tree is
  identical, the stages after it stay cached (their fingerprints hold the upstream output ids, which are
  content-based) and take the new tree's provenance: their outputs are exactly what this commit gives.

## A fresh machine

`make BOOT=1` needs nothing but this repository and an Ubuntu 24.04 host with `git python3 make curl jq dpkg-deb
gcc g++ llvm-18` (llvm-undname), `qemu-user` for the S2 tests, `libgmp-dev libmpfr-dev libmpc-dev texinfo bison
flex` to build fnvnx-a64, and the Python modules `unicorn` and `pyelftools`. Everything else the full run uses is
made from pinned sources by the pipeline:

| input | made by | pinned by |
|---|---|---|
| decomp, sse2neon, libnx checkouts | `tools/fetch.sh` (fetch stage, without `EXT`) | commits in `tools/fetch.sh` |
| AArch64 cross tools (S2c's `s2c/xtc`: Ubuntu's `g++-13-aarch64-linux-gnu` unpacked by hand, the rest from the installed packages) | `toolchain/cross/setup.sh` (toolchain stage, `CROSS=auto` when the host's are not the pinned build) | `toolchain/cross/packages.tsv`: package, version, pool path, SHA-256 (archive.ubuntu.com, or a snapshot.ubuntu.com date) |
| fnvnx-a64 + DEVKITPRO with libnx | `toolchain/sources.sh`, `toolchain/gcc/build.sh`, `toolchain/devkita64/setup.sh` (toolchain stage, `TC_DL=auto`) | tarball SHA-256s and devkitPro commits in `toolchain/sources.sh` |
| the extern-text list (S4's `addr_ids.txt`) | `tools/pipeline/extern_text.py` (link stage, `EXTERN_TEXT=auto`) | the tree's `units.json`; byte-identical to S4's file for the f11/f12 tree and for the pristine decomp |
| boot data image | S14's `loader/synth/synth_image.py` (boot stage, `BOOT_IMAGE=auto`) when `PRISTINE_OBJS` is given, else the all-zero image | the pristine decomp's `units.json` + S11's MSVC objects |

Still machine-specific (not made by the pipeline):

* **S11's MSVC 2008 objects of the pristine decomp** (`PRISTINE_OBJS`, `MSVC_OBJS`): Visual C++ 2008 Express from
  Microsoft's ISOs, unpacked as the decomp README describes, under Wine (`tools/equiv/README.md`). Only S14's
  synthesized data image needs them in the full run (and S2b's `--msvc-objs`, which f11/f12 did not use); without
  them the boot runs on the all-zero data image, as S5's "zero" boots did.
* The host packages above (`tools/farm/full_job.sh` installs them with `FARM_APT=1`). Some test suites need more:
  `s6b` / `s6d` / `nine-gate` cmake, meson, ninja and network clones of MojoShader / Mesa; suites whose tools are
  missing are `skip`, which makes the tests stage `warn`.
* Network access to archive.ubuntu.com (or snapshot.ubuntu.com), ftp.gnu.org (or mirrors.kernel.org),
  sourceware.org and github.com (decomp, sse2neon, libnx, devkitPro, Xiph). Everything fetched is checked
  against a pinned commit or SHA-256.
* Disk: about 1 GB for the toolchain (fnvnx-a64 0.4 GB, cross packages 0.23 GB, libnx, tarballs), several GB
  more while GCC builds (its build tree is removed afterwards), and 1-2 GB per full build.
* The player's game files (the real data image, assets): never on these machines.

`tools/farm/full_job.sh <commit> <report.md>` is the farm job for this (`tools/farm/README.md`). On the farms it
takes the prepare farm's tree (`PREPARED`) and the compile farms' objects (`OBJECTS`) instead of preparing and
compiling, and publishes the link outputs as branch `link/<commit12>` of the private artifacts repository for the
fuzz farms.

## The hand flow and the pipeline

What the hand-driven runs did (S2c's `full.sh` for f11/f12, S5's `game4.sh`, `emu4.sh`, `frontier5.sh`), what the
pipeline did before this round, and what it does now:

| step | hand flow | pipeline before | pipeline now |
|---|---|---|---|
| tools | from a snapshot copy `s2c/snap_<commit>` (no `.git`) or a worktree; the operator passes `COMMIT=` | from the checkout; no commit recorded | from the checkout, its HEAD recorded (`PORT_COMMIT` for a copy) |
| prepare | `rm -rf tree; tools/prepare.py decomp tree`; `prepared_from.txt` = `$COMMIT` | staging + sync; fingerprint missed `patches/` and `shim/include/fnvnx_win32.h`, which prepare reads; no provenance | same tree content; full input fingerprint + head; provenance record; `PREPARED=` adopts another run's tree |
| compile | `objcheck.py` (damaged objects), then `compile.sh tree obj "" 2` with `CXX` unset = `aarch64-linux-gnu-g++` (`s2c/xtc/bin` first on PATH), `-O2`, `SSE2NEON=$S/ext/sse2neon` | `GAME_CXX=auto`: fnvnx-a64 `aarch64-none-elf-g++` whenever the toolchain was there | `GAME_CXX=standin` default; `CROSS_BIN` for a private copy of the cross tools; damaged-object check; same compile.sh flags (`-fPIC`, J-0034 set) |
| seed | copies `cache/`, `layouts.tsv`, `vtables/` of an earlier run into the new run dir | none (keeps `build/link/out/`) | `LINK_SEED` copies the same (without the probe work dir) into a new `build/link/out/`; the content-keyed caches make it safe across trees |
| resolve 1 | `--extern-text $S/s4/addr_ids.txt`; default `--units-renames`; no `--override`, no `--msvc-objs` (f12) | `EXTERN_TEXT`, `OVERRIDE` | + `MSVC_OBJS`, `UNITS_RENAMES` (defaults = the hand flow) |
| probe | `probe out tree 2`, `CXX` unset: `aarch64-linux-gnu-g++` + `layout.flags()` | through `cxx-probe` = game compiler + every compile.sh `-f` option (`-fPIC`, `-fpermissive`, ...) | the game compiler + `layout.flags()` (which carries J-0034's set since S2's fix) |
| resolve 2 | always | only when `layouts.tsv` changed (missed new synthesized vtables) | always |
| apply, check | `apply out fixed`, `check out fixed` | same | same |
| summary | `summary.txt`: commit, WinMain units' status, `f3check.py`, `check_rebind.py`, `undefcat.py`; `s2c/latest` switched | report only | `build/link/summary.txt` (same first line), `check_rebind.py` metric; the report has the trial-link classes |
| guest | `DEVKITPRO=s5/dkp D3D9=null toolchain/game/build.sh latest/out latest/fixed g4`, `JOBS=3`, `GAME_NEW` default `runtime` | same script, `D3D9` from `NINE`, `GAME_NEW` from the environment | `GAME_NEW` explicit (default `runtime`); `dynbrk.py` and `pcrel_abs` metrics |
| boot | `emu_guest.py --skip-faulting-init --expect "fnvnx boot: calling WinMain"` (zero image and S14's synthesized exe), `timeout 3000`; `frontier.py` with S1's latest census | informational suite `emu-game` (no frontier, 1800 s) | `boot` stage (emulator, summary, frontier; `BOOT_IMAGE`, `BOOT_CENSUS`, `EMU_TIMEOUT=3000`) |
| provenance | by hand: the summary names the commit; boots use links of the current head (J-0054) | none | enforced (above) |

Still different (by design, or left for later):

* The pipeline keeps one tree and syncs it (unchanged files keep their mtimes), S2c re-prepares into a fresh
  directory; the content is the same.
* S2c versions its runs (`runs/fN`, `latest` switched atomically after the trial link); the pipeline has one
  `build/` with a report snapshot per run in `build/history/`. Keep an older link with another `BUILD_DIR`.
* S2c's summary scripts `f3check.py` and `undefcat.py` (scratch) are not part of the pipeline; the report's link
  section (address map kinds, ABI verdicts, trial-link classes, split-global check) covers the same numbers except
  the per-unit status lines of the WinMain units and the trial ELF symbol checks.
* S5 boots twice per guest (zero image, synthesized image); the pipeline boots once per build (`BOOT_IMAGE`).
* `JOBS` is one value for every stage (S2c compiled and probed with 2, S5 linked with 3).
* `-MD` in the compile `CXX` (dependency files, no effect on the objects).
* `--extern-text`: the hand flow passes `$S/s4/addr_ids.txt`, made by S4's scratch scripts; the link stage now
  makes the same file from the tree (`EXTERN_TEXT=auto`, byte-identical for the f11/f12 tree).
* Not run in full since this alignment: the pipeline's own prepare of the whole tree, the full compile and link
  and the boot on them (this round ran a 31-unit subset on an adopted tree only, see "Subset run"). The next
  full run with `make` is that test; compare it with S2c's run of the same commit with `linkdiff.py`.

## Subset run

`UNITS=<file>` (unit sources as in `units.json`, one per line, `#` comments) limits census and compile to those
units, so the link, guest and boot see only them: a quick end-to-end check of the pipeline without a full build.
`tools/pipeline/linkdiff.py <reference out> build/link/out --objects <ref objects.tsv> build/compile/objects.tsv
--units <tree>/build/units.json --fixed <ref fixed> build/link/fixed` compares the link with a full run on the
units both compiled: the objects, refs.tsv rows, ABI verdicts, resolve's objcopy lists and the rewritten objects.
Differences that follow from the units the subset lacks are counted per reason (`addr-outside`: the definition of
the address is in a unit the subset does not compile, so `code` becomes `code-pending` and no bridge, D1/D2 alias
or import-thunk provider is made; `name-outside`: a by-name native definition the subset lacks; `dup-outside`: a
duplicate strong name localized only because a unit outside the subset defines it too); anything else is printed
(exit 1). Synthesized vtable objects depend on the whole set and are only counted.

Verification of this round (2026-11-04, port `be08cb3`/`7b7e48c`, light: `nice -n 19`, `JOBS=1`): 31 units
(`src/fallout/misc/main.cpp` with WinMain, the WinMain path units `blk_00ae0000_od`, `blk_00c20000_od`,
`blk_004a0000_od`, `nicontrollermanager`, four more staging blocks, GAME1 units `memorymanager`,
`falloutaudiomedia`, `bsspinlock_console_unit`, the XINC1-patched `nipointlight` and
`niblendtransforminterpolator`, the TLS unit `tesscript`, the two f11 failures `interface` and
`blk_00aa0000_od_ob1_variant`, and 14 others), on S2c's
fresh8 tree (`PREPARED`, prepared from `0c630cf`, the tree of f11/f12, hence `FORCE_STALE=1`), against S2c's f12:

* without `FORCE_STALE`: census and compile refused (`stale inputs: inputs prepared from 0c630cfffb, the head is
  be08cb3a0d`), link / guest / boot skipped, also when they were cached;
* census 31 units, 29 clean; compile 29 objects in 52 s, **29/29 byte-identical to f11's objects** (the same 2
  units fail, as in f11);
* link (resolve, probe, resolve, apply, check: 258 s): 13,495 references, 12,000 by address, 42 bridges; trial
  link 2,276 undefined, 0 multiple definitions; split-global check 0. Against f12 on the 29 units: refs.tsv
  rows 5,677 identical, 2,599 explained (2,597 addr-outside, 2 name-outside), **0 unexplained**; ABI verdicts 80
  identical (42 bridge, 32 manual, 6 x86-inconsistent), 102 explained, 0 unexplained; objcopy lines args 2,321 +
  7 dup-outside, redefine 7,736 + 176 explained, rebind 60, undefine 59, 0 unexplained; rewritten objects 9
  identical, 20 differ only by explained lines;
* guest (S5's script, `D3D9=null GAME_NEW=runtime`): relocatable, 0 PC-relative absolute references, 2,012
  undefined symbols bound to traps; boot: WinMain entered, stop at the first untranslated call (2 frontier keys,
  both in units outside the subset); host and package built;
* rerun with nothing changed: all 11 enabled stages `cached`, 0 s. The same cached stages without
  `FORCE_STALE` (after the S9 commit moved the head to `7b7e48c`): refused; back with `FORCE_STALE=1` they ran
  again from their own caches: census 2 s, compile 0 s, link 58 s (11 layouts reused, 0 invalidated), guest
  32 s, boot 3 s. `FORCE=link` relinked to a byte-identical `guest.nso`, so boot and package stayed cached.

## Variables

| variable | default | meaning |
|---|---|---|
| `EXT` | (empty: `ext/` via fetch.sh) | Directory holding the pinned checkouts (`fnv_decomp`, `sse2neon`, `libnx`; optional `buildscripts`, `devkita64-rules`). It is only read. |
| `ALLOW_UNPINNED` | 0 | 1: a checkout at another commit is a warning, not a failure. |
| `FNVNX_TC`, `DEVKITPRO` | | Existing toolchain prefix and DEVKITPRO tree (reused, never modified). |
| `TC_DL` | auto | Without `FNVNX_TC` / `DEVKITPRO`: `auto` fetches the pinned sources (`toolchain/sources.sh`) and builds fnvnx-a64 and the DEVKITPRO tree in `build/toolchain/`; a directory takes the tarballs from there; `none` never builds. |
| `CROSS` | auto | AArch64 cross tools: `auto` = the host's when they are the pinned build (`toolchain/cross/packages.tsv`), else the pinned packages unpacked into `build/toolchain/cross`; `pinned` = always those; `system` = whatever is on PATH (a mismatch warns). |
| `GAME_CXX` | standin | Compiler of the decomp units. `standin` = `aarch64-linux-gnu-g++` (glibc headers; every link, guest and boot measured so far was built with it). `tc` = fnvnx-a64 `aarch64-none-elf-g++` (newlib headers, the ones the guest links against; not yet run end to end). `auto` = `tc` when the toolchain stage found it. Any other value is used as the command. |
| `CROSS_BIN` | | Directory put first on PATH for the `aarch64-linux-gnu-*` tools (S2c's private driver copy `s2c/xtc/bin`, made when another agent's package install removed the system ones). |
| `OPT` | -O2 | compile.sh `$OPT`. |
| `UNITS` | | List file of units for census and compile (see "Subset run"); empty: every unit. |
| `PREPARED` | | Adopt this prepared tree (read only) instead of running prepare; its provenance is read from `prepared_from.txt` / `.json` next to it or in it. |
| `OBJECTS` | | `objects.tsv` of objects another job compiled from that tree (the compile farms, `tools/farm/fetch_objs.sh`, which checks the tree hash), used in place instead of compiling. |
| `FORCE_STALE` | 0 | 1: consume inputs prepared from another commit than the head (recorded, `warn`). |
| `PORT_COMMIT` | | The commit of a ports/fnv-nx copy without git metadata. In a checkout it must equal HEAD. |
| `CENSUS` | 1 | 0 skips the census. |
| `OVERRIDE` | `auto` | fnvlink resolve `--override` file. `auto`: the port's own replacements (`tools/link/overrides/`, built by its `build.sh` into `build/link/overrides/`); `none` or empty: off. |
| `LINK_OVERRIDES` | `auto` | `render`: S6n's `render/native/overrides.tsv` with `--override-lib` libnxrender.a, added to `OVERRIDE`'s rows (one table, `build/link/overrides_all.tsv`); the guest stage then links the archive (`EXTRA_OBJS`) and the engine's D3D9 entry points go to the native renderer. `auto`: `render` when the toolchain stage has DEVKITPRO and fnvnx-a64 (a build that links a guest), else `none`. `none`: the pristine path (link_job.sh passes it for the fuzz and equivalence farms; full_job.sh passes `render`). |
| `RENDER_LIB` | | libnxrender.a for `LINK_OVERRIDES=render`; empty: the link stage builds it with `render/native/n1/build.sh` (the toolchain stage's DEVKITPRO and fnvnx-a64; `UAM` from the environment, else `toolchain/uam/build.sh`, cached in `build/uam`). |
| `EXTERN_TEXT` | auto | fnvlink resolve `--extern-text`: `auto` = made from the tree's `units.json` (`tools/pipeline/extern_text.py`, S4's `addr_ids.txt`), `none` = off, or a file. |
| `MSVC_OBJS` | | fnvlink resolve `--msvc-objs` (S11's pristine MSVC objects, S2b's vtable slots). Not used by f11/f12. |
| `MSVC_CODE` | | fnvlink resolve `--msvc-code` (the same objects, only for the definitions' register reads: ecx-not-passed / this-not-passed judged on the MSVC code; needs python capstone). The link farm passes S11's pristine objects. |
| `UNITS_RENAMES` | | fnvlink resolve `--units-renames`; empty: fnvlink's default (`patches/units_renames.tsv`), `none`: off. |
| `LINK_SEED` | | fnvlink out dir of an earlier run; its demangle cache, `layouts.tsv` and `vtables/` seed a new `build/link/out/`. |
| `GUEST_LINK` | auto | `auto`: S5's `toolchain/game/build.sh` when present, else the adapter. `adapter` forces the adapter. Any other value is a script with the same CLI. |
| `GAME_NEW` | runtime | toolchain/game/build.sh `GAME_NEW`: `runtime` (the game objects' global new/delete weakened, malloc stand-ins) or `game`. |
| `BOOT` | 0 | 1: the `boot` stage runs with `all` (it always runs when named: `make boot`). |
| `BOOT_IMAGE` | auto | Data image for the emulated boot (`sdmc:/fnvnx/game/FalloutNV.exe`): `auto` = S14's synthesized image when `PRISTINE_OBJS` is given, else none; `none` = the all-zero image; or an exe file. |
| `PRISTINE_OBJS` | | S11's MSVC 2008 objects of the pristine decomp (`u<index>.obj`) for the synthesized image. |
| `BOOT_CENSUS` | | Census `units.tsv` the frontier takes the current compile status from (default: this build's census). |
| `NINE` | 0 | 0: no D3D9 backend in the host. The guest links D3D9 as `null` (S5's null device when its script is used; the adapter links none). 1: the `nine` stage, the Nine host link and the guest proxy (`D3D9=external`). |
| `HOST_BACKEND` | native | The host module: `native` = host.c with `-DFNVNX_HOST_NO_CONSOLE` + the native deko3d renderer (`render/native/host`, `toolchain/host/build_native.sh`, libdeko3d pinned by `toolchain/deko3d/build.sh`; owner decision, NX1); `plain` = host.c with its text console (opt-in). `NINE=1` overrides both. |
| `NINE_SRC` | | Directory with `mesa/` and `libdrm_nouveau/` checkouts. They are copied instead of cloned. |
| `PROGRAM_ID` | 0100000000000000 | Title id for the SD path. The placeholder makes `package` a warning. |
| `TESTS` | all | Comma list of suite names, `all` or `none`. |
| `EMU_TIMEOUT` | 3000 | Seconds for the emulated boot (S5's value). |
| `EMU_BUDGET` | auto | Instructions per scheduling slice of the emulated boot. auto: 0 when tests/s5/emu_guest.py has `--hc-slice` (S5 620572bc: threads switch at host calls, deterministic, about 10x faster than a count), else 6000000000. An instruction count makes unicorn hook every instruction. |
| `EMU_SLICE_MS` | (empty) | emu_guest.py `--slice-ms`, its fallback timer for a thread making no host call; empty: emu_guest.py's default (60 s). A short timer makes boots nondeterministic (S5: 2d1afaeb..6da559aa). |
| `JOBS` | nproc | Parallel jobs (compile, census, probe, guest link). `JOBS=1` with `nice -n 19` is a light run. |
| `BUILD_DIR` | build | Output root. |
| `FORCE` | | Stages to rerun. |

## Host gates (`tests` stage)

| suite | commands | owner |
|---|---|---|
| `s2-link` | every `tools/link/test_*.py` (link, override, bridges under qemu-aarch64, vtables, startup) | S2 |
| `s3` | `tests/s3/test_s3.py` (FNVNX_S3_OUT under build/tests), `make -C loader ilp32-check` | S3 |
| `s4`, `s7` | `make -C tests/<s> test` and `ilp32` (LIBNX = the pinned libnx) | S4, S7 |
| `s5-tools` | `tests/s5/test_tools.py` | S5 |
| `s5-emu-hello` | `tests/s5/hello/build.sh`, then `tests/s5/emu_guest.py` on its guest.nso (Unicorn) | S5 |
| `s5-rt` | `tests/s5/rt/build.sh` when present | S5 |
| `s6b` | `render/shaders/build.sh --tests` (deps cloned under build/tests/s6b), `tests/s6b/run.py` | S6b |
| `s6d` | `render/nine/host/build_host.sh` (x86-64 Nine shader path; Mesa cloned, or the nine stage's tree with NINE=1), then `tests/s6d/run.py` with s6b's assembler; runs after s6b passes | S6d |
| `s8` | `tests/s8/run.sh` | S8 |
| `s9` | `tests/s9/test_provenance.py`: the provenance rule and the stage cache on toy stages (refused / FORCE_STALE / re-prepared identical tree / missing record) | S9 |
| `s10` | `tests/s10/run.sh` with `FNVNX_A64` (cross part: ILP32 startup objects, e2e guest under Unicorn) and DEVKITPRO (host part against libnx, hardware probe) | S10 |
| `nine-gate` | NINE=1 only: `render/nine/test/run_gate.sh` (Mesa st/nine + softpipe for x86-64, the D3D9 proxy path end to end) | S6c |
| any other `tests/<x>/` | `make test`, or `run.sh`, or `run.py`, or every `test_*.py` (in that order of preference) | its stream |

A suite reruns when the files it tests change. Its inputs are hashed: `tests/<s>` plus the directories under test.
It also reruns when the toolchain or the pins change. The emulated boot of the game guest (formerly the
informational suite `emu-game`) is the `boot` stage.

## Report

`build/report.md` has these sections:

* the provenance: the head, the commit and decomp the tree was prepared from, stages that refused or consumed
  stale inputs;
* the stage table (status, ran/cached, time, the commit its inputs were prepared from, one-line summary);
* the pins and toolchain;
* census and compile counts with the top error kinds and failed units by directory;
* the link numbers: references, resolved share, bridges, ABI verdicts, address map kinds, and the trial link's
  undefined names by class;
* the guest link: method, D3D9 / GAME_NEW, PC-relative absolute references, trapping initializers, platform
  sources excluded, undefined symbols by category with the owning stream;
* the emulated boot (BOOT=1): milestones, stop reason, trapped keys, skipped initializers, the frontier;
* the host, Nine and package, with the SD tree file sizes;
* the test suites;
* artifact sizes;
* the diff against the previous run, with metric deltas and stage/suite status changes;
* open items by owner.

`report.json` holds the same data flat. Metric keys follow the journal (`census_clean`, `link_resolved`,
`guest_undefined`, ...). `history/` keeps one snapshot per run.

Undefined-symbol categories (`tools/pipeline/undef.py`, owners in brackets):

* image code addresses: `code_uncompiled` (its unit does not compile, S1); `code_notemitted` (the unit compiled
  but the definition was not emitted, S1/S2); `code_crt`, `code_eh`, `code_nobody`, `code_unknown` (no unit
  defines the address; split by `crt_symbols.tsv` (deleted), S4);
* `data_addr` (S2 bug);
* `bridge` (S2);
* C++ symbols: `vtable_typeinfo`, `cxx_thunk`, `cxx_ctor_dtor`, `cxx_by_name` (S1/S2), `cxx_stdlib` (S5);
* `ghidra_name` (S2);
* `win32_import`, `crt_msvc`, `steam` (S4);
* `libc_glibc` (units compiled against glibc headers: `GAME_CXX=standin`);
* `libc` (S5);
* `os_backend`, `fnvnx_platform` (S4/S5);
* `d3d` (S6);
* `audio_input` (S7);
* `sse` (S8);
* `other`.

## Adapters (thin stand-ins for missing interfaces)

| adapter | stands in for | goes away when |
|---|---|---|
| `tools/pipeline/guest_link.py` + `guest_stub.c` | Fallback only since S5's script landed (`GUEST_LINK=adapter`): the full guest link of S2 objects + platform + loader + runtime. It compiles the guest runtime, `platform/` (without the libnx-side files) and `loader/src` with fnvnx-a64. Sources that fail are listed and left out. It links the whole image (`--no-gc-sections`, since there is no boot code). Objects that binutils 2.45 refuses are left out and listed with the reason (`error adding symbols`; a relocation against a *defined* symbol that does not fit, mapped to the referencing object with nm), and the link is retried. Every undefined symbol is bound to a trap stub that reports it, and the result is packed as `guest.nso`. Its numbers count every compiled object; S5's script links from its boot code with gc. | S5's `toolchain/game/build.sh` is in the tree; `GUEST_LINK=auto` picks it automatically |
| `build.py` link stage | Removes `build/link/out/objcopy/` before each `fnvlink.py resolve`. resolve writes one `.args`/`.redefine`/`.src` per object but never removes old ones, so `apply` would rewrite objects of units that no longer compile. | S2's resolve clears its per-object outputs |
| `build.py` compile stage | Header dependencies: `-MD` in `CXX`, and objects deleted when a header changed. compile.sh compares the object with the unit's source only. | compile.sh tracks dependencies |
| `build.py` link stage | The `cxx-probe` wrapper of the first runs is gone: `layout.flags()` carries J-0034's semantics flags since S2's fix. A wrapper is written only when `GAME_CXX` is a command with arguments (probes take one executable in `CXX`). | - |

## Findings of the hand-flow alignment (2026-11-04)

* **S9 (fixed)**: the prepare fingerprint did not cover `patches/` (S1b's `*.s1b` and `units_renames.tsv`, read
  by prepare's POST hook) nor `shim/include/fnvnx_win32.h` (read by DECL1's platform names): a patch change left
  the tree stale. The link fingerprint missed S4's provider tables, the default `units_renames.tsv`, the shim and
  the sse2neon pin; resolve 2 was skipped when only the synthesized vtables changed.
* **S2 (fixed since by S2c: content keys, column 8 of `layouts.tsv`; the pipeline's own invalidation is gone)**: `layout.py` cached layouts per (unit, type) with no content key and never probes a cached row
  again; `vtables.py` keys its probe and emit caches on the host unit's source mtime, not its headers. S2c's
  driver copies `layouts.tsv` and `vtables/` from an earlier run (`SEED`, e.g. r13 for f3): layouts measured on
  an older tree (before D15's 24-byte CRITICAL_SECTION, for instance) stay in every later run. The pipeline drops
  them per unit / per tree (see "Adapters"); the subset check found no layout that differs from f12's, so f12 is
  not known to be affected.
* **S2c / S5 (process)**: the hand flow states its tree's commit only as `COMMIT=` typed by the operator, and the
  guest took `s2c/latest` whatever it was prepared from; the pipeline reads the commit from the checkout and
  refuses stale inputs (J-0054).

## Findings of the first runs (2026-11-03)

These are interface problems the pipeline found, recorded here for their owners. The first three were found on
port `a0f2c18`; S1's round 4 (J-0025) has since fixed the shim/platform conflict.

* **S2 (after J-0034)**: `tools/link/layout.py` `flags()` lacks `-fsigned-char` (and the other D10 options), so
  `fnvnx_compat.h`'s `#error` stops every probe and vtable-synthesis unit, and `test_bridges.py` fails all 10 pairs.
  With the flags added (the pipeline's `cxx-probe` wrapper) the test passes, with 8 bridges executed. (Fixed since:
  `layout.SEMANTICS`.)

* **S2**: TLS-model references bound to absolute image addresses. `src/fallout_shared/tesscript.cpp` and
  `tesconditionfunctions.cpp` declare `__declspec(thread)` variables at image data addresses (`0x0126fd98`,
  which other units name `_tls_index`, and `0x01272000`/`0x01272268`). resolve binds those references to the
  absolute `__fnv_<addr>` like any data reference. The host binutils' trial link (`fnvlink check`) accepts this;
  fnvnx-a64's ld 2.45 refuses it ("TLS reference ... mismatches non-TLS reference", "TLSLE relocation against
  *ABS*"). The same happens with S5's guest script ("used with TLS symbol"). Also: `g_out` is defined by one
  Havok unit and referenced with a larger alignment by another (a name shared by two units).
* **S1/S4**: `platform/win32/exports.cpp` and `sync.cpp` do not compile against the current shim. The errors are
  an overloaded address in `K(...)` and `Interlocked*` declared with C linkage. The same shim breaks the host
  tests `tests/s4` and `tests/s7`: `STDMETHODCALLTYPE` is not defined outside the game flags, and the LP64
  `static_assert(sizeof(CRITICAL_SECTION) == 24 ...)` fails.
* **S6c**: with NINE=1, `nine_proxy_host.c` includes the missing `nine_proxy_host.h`, and
  `nine_proxy_guest.cpp` needs declarations of the `nx_manual_*` bodies. The Mesa libraries and
  `nine_adapter.c` link into the host with 0 undefined symbols; the host NSO is 2.1 MiB.

## Notes

* The pin check reads HEAD. Local edits in a shared `EXT` checkout are not detected.
* `GAME_CXX=standin` (the default) is the compiler of every object set linked so far (f3 ... f12) and of every
  emulated boot; the units see glibc headers while the guest links newlib. `GAME_CXX=tc` compiles them against
  newlib, as the guest links them; its census and compile numbers differ slightly and it has not been linked and
  booted end to end yet.
* The emulator gate covers the S5 hello guest. The game guest runs under the emulator only as an informational
  check, and only with S5's boot code.
