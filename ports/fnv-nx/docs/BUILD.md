# fnv-nx build pipeline (stream S9-build)

One entry point, `ports/fnv-nx/Makefile`, runs the whole build from the pinned decomp to an SD-card tree, then the
host gates, and writes `build/report.md`. The Makefile forwards its variables to `tools/pipeline/build.py`, which
runs the stages. Each stage calls the documented command-line interface of the stream that owns the step
(`tools/fetch.sh`, `tools/prepare.py`, `tools/census.sh`, `tools/link/compile.sh`, `tools/link/fnvlink.py`,
`tests/s5/hello/build.sh`, `render/nine/build.sh`, `toolchain/tools/*.py`, the test runners). Where the interface
does not exist yet, a thin adapter in `tools/pipeline/` stands in (see "Adapters").

Everything under `build/` is derived from the decomp or the player's game. It stays on the developer's machine and
is never committed (`build/` is git-ignored).

## Quick start

```sh
cd ports/fnv-nx
make FNVNX_TC=/opt/fnvnx-a64 DEVKITPRO=/opt/fnvnx-dkp          # checkouts go to ext/ via tools/fetch.sh
make FNVNX_TC=... DEVKITPRO=... EXT=/path/to/ext               # reuse existing pinned checkouts (read only)
make ... PROGRAM_ID=01xxxxxxxxxxxxxx                           # title whose exefs the build overrides
make ... NINE=1                                                # Gallium Nine in the host module
cat build/report.md
```

`DEVKITPRO` alone is enough when `$DEVKITPRO/devkitA64` points at the fnvnx-a64 prefix (as
`toolchain/devkita64/setup.sh` makes it). To build the toolchain itself, pass `TC_DL=<dir with the binutils, gcc
and newlib tarballs>` and have `buildscripts` and `devkita64-rules` in `EXT` (docs/TOOLCHAIN.md §3, about 45 min).
The output prefix is `build/toolchain/prefix`, and the DEVKITPRO tree is `build/toolchain/dkp`.

Other goals: `make <stage>` runs that stage and every stage it needs. Several stage goals are one run
(`make link tests`). `make report` re-renders the report, `make status` prints the stage table, `make help` lists
the variables, `make clean` removes `build/`, and `make clean-<stage>` removes one stage directory.

## Stages

| stage | output | what runs | needs |
|---|---|---|---|
| `fetch` | `build/fetch/pins.tsv` | Checks that `fnv_decomp`, `sse2neon` and `libnx` are at the commits `tools/fetch.sh` pins. It reads the checkouts' HEAD files and never runs git on them. Without `EXT`, it runs `tools/fetch.sh ext/` when a pin is missing. | - |
| `toolchain` | `build/toolchain/{tools.tsv,toolchain.env}` | Validates fnvnx-a64 (version, the ILP32 multilib with libc/libsysbase/libm/libgcc/libstdc++/libsupc++), DEVKITPRO (libnx, switch.specs, packers) and the host tools each later stage uses. It chooses the game compiler. It builds the toolchain when `TC_DL` is given. | - |
| `prepare` | `build/prepare/tree/` | `tools/prepare.py <decomp> build/prepare/staging`, then a sync into `tree/` that keeps unchanged files, mtimes included. The compile step then sees only real changes. Also writes `prepare_stats.tsv` and `changed.txt` (A/M/D). | fetch |
| `census` | `build/census/` | `tools/census.sh` (`-fsyntax-only` of every unit) with the game compiler. `CENSUS=0` skips it. | prepare |
| `compile` | `build/compile/{obj/,objects.tsv,failed.tsv}` | `tools/link/compile.sh` with `CXX="<game compiler> -MD"`. Before the call, an object is deleted when a header in its `.o.d` changed, since compile.sh checks only the unit's own source. Units that fail are listed with their first error in `failed.tsv` and excluded from the link. If the compiler, `OPT` or compile.sh changes, every object is rebuilt. | prepare |
| `link` | `build/link/{out/,fixed/,check.txt,apply.log}` | `fnvlink.py resolve`, then `probe`, then `resolve` again if the layouts changed, then `apply` and `check`. `OVERRIDE` and `EXTERN_TEXT` pass `--override` and `--extern-text`. The probe uses the game compiler. Objects that apply cannot rewrite are listed and left out. | compile |
| `guest` | `build/guest/{fnvnx.elf,guest.nso,undefined.txt,undefined_by_category.tsv}` | The full ILP32 guest link: S2's fixed objects, `aliases.ld`, bridges, addrmap, the guest runtime, `platform/`, `loader/`, `guest.specs`/`guest.ld`. It uses S5's `toolchain/game/build.sh` when present, otherwise the adapter. Undefined symbols are sorted by category and owner (`tools/pipeline/undef.py`). | link, toolchain |
| `nine` | `build/nine/{work/,proxy/}` | NINE=1 only. `render/nine/build.sh` (Mesa st/nine + nouveau, LP64) and `render/nine/gen_proxy.py`. The guest proxy is compiled for ILP32. | toolchain |
| `host` | `build/host/{host.elf,main,main.npdm}` | The LP64 host module, built with S5's `tests/s5/hello/build.sh` recipe (`host.c` + libnx). With NINE=1 the adapter `host_nine.py` links render/nine and Mesa into it. Then elf2nso (`main`) and npdmtool (`main.npdm`). | toolchain |
| `package` | `build/package/{exefs.nsp,sd/}` | `build_pfs0.py` makes `exefs.nsp` (main + main.npdm). The SD tree holds `atmosphere/contents/<PROGRAM_ID>/exefs.nsp` and `fnvnx/guest.nso`. | host (guest optional) |
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
  output (for example identical objects) leaves the stages after it cached.
* Inside a stage, the tools' own rules apply too:
  * compile.sh keeps up-to-date objects; the pipeline adds header dependencies.
  * A unit that failed is retried only if its source or a dependency in its `.o.d` is newer than its log (GCC
    writes the `.d` when the error comes after preprocessing), if it has no `.d`, or if files were added to or
    removed from the prepared tree.
  * fnvlink keeps its demangle and layout caches in `build/link/out/`.
  * Test suites are cached only when they passed; a failing suite runs again on every run.
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

## Variables

| variable | default | meaning |
|---|---|---|
| `EXT` | (empty: `ext/` via fetch.sh) | Directory holding the pinned checkouts (`fnv_decomp`, `sse2neon`, `libnx`; optional `buildscripts`, `devkita64-rules`). It is only read. |
| `ALLOW_UNPINNED` | 0 | 1: a checkout at another commit is a warning, not a failure. |
| `FNVNX_TC`, `DEVKITPRO` | | Existing toolchain prefix and DEVKITPRO tree (reused, never modified). |
| `TC_DL` | | Builds the toolchain from these tarballs when no prefix is given. |
| `GAME_CXX` | auto | Compiler of the decomp units. `tc` = fnvnx-a64 `aarch64-none-elf-g++` (newlib headers, the ones the guest links against). `standin` = `aarch64-linux-gnu-g++` (glibc headers; the compiler S1/S2 measured with, for comparable numbers). Any other value is used as the command. `auto` = `tc` when the toolchain stage found it. |
| `OPT` | -O2 | compile.sh `$OPT`. |
| `CENSUS` | 1 | 0 skips the census. |
| `OVERRIDE`, `EXTERN_TEXT` | | fnvlink resolve `--override` / `--extern-text` files. |
| `GUEST_LINK` | auto | `auto`: S5's `toolchain/game/build.sh` when present, else the adapter. `adapter` forces the adapter. Any other value is a script with the same CLI. |
| `VORBIS` | | ILP32 libogg/libvorbis prefix, passed to S5's guest link script. |
| `NINE` | 0 | 0: no D3D9 backend in the host. The guest links D3D9 as `null` (S5's null device when its script is used; the adapter links none). 1: the `nine` stage, the Nine host link and the guest proxy (`D3D9=external`). |
| `NINE_SRC` | | Directory with `mesa/` and `libdrm_nouveau/` checkouts. They are copied instead of cloned. |
| `PROGRAM_ID` | 0100000000000000 | Title id for the SD path. The placeholder makes `package` a warning. |
| `TESTS` | all | Comma list of suite names. |
| `EMU_TIMEOUT` | 900 | Seconds for the informational emulator run of the game guest. |
| `JOBS` | nproc | Parallel jobs. |
| `BUILD_DIR` | build | Output root. |
| `FORCE` | | Stages to rerun. |

## Host gates (`tests` stage)

| suite | commands | owner |
|---|---|---|
| `s2-link` | `tools/link/test_link.py`, `test_override.py`, `test_bridges.py` (qemu-aarch64) | S2 |
| `s3` | `tests/s3/test_s3.py` (FNVNX_S3_OUT under build/tests), `make -C loader ilp32-check` | S3 |
| `s4`, `s7` | `make -C tests/<s> test` and `ilp32` (LIBNX = the pinned libnx) | S4, S7 |
| `s5-tools` | `tests/s5/test_tools.py` | S5 |
| `s5-emu-hello` | `tests/s5/hello/build.sh`, then `tests/s5/emu_guest.py` on its guest.nso (Unicorn) | S5 |
| `s5-rt` | `tests/s5/rt/build.sh` when present | S5 |
| `s6b` | `render/shaders/build.sh --tests` (deps cloned under build/tests/s6b), `tests/s6b/run.py` | S6b |
| `s8` | `tests/s8/run.sh` | S8 |
| `emu-game` | informational, not a gate: `emu_guest.py` on build/guest/guest.nso with `--expect "fnvnx boot"`, only when S5's guest script built it (the adapter has no boot code) | S5 |
| any other `tests/<x>/` | `make test`, or `run.sh`, or `run.py`, or every `test_*.py` (in that order of preference) | its stream |

A suite reruns when the files it tests change. Its inputs are hashed: `tests/<s>` plus the directories under test.
It also reruns when the toolchain or the pins change.

## Report

`build/report.md` has these sections:

* the stage table (status, ran/cached, time, one-line summary);
* the pins and toolchain;
* census and compile counts with the top error kinds and failed units by directory;
* the link numbers: references, resolved share, bridges, ABI verdicts, address map kinds, and the trial link's
  undefined names by class;
* the guest link: method, platform sources excluded, undefined symbols by category with the owning stream;
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
  defines the address; split by `platform/crt/crt_symbols.tsv`, S4);
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
| `tools/pipeline/guest_link.py` + `guest_stub.c` | The full guest link of S2 objects + platform + loader + runtime (S5). It compiles the guest runtime, `platform/` (without the libnx-side files) and `loader/src` with fnvnx-a64. Sources that fail are listed and left out. It links the whole image (`--no-gc-sections`, since there is no boot code). Objects that binutils 2.45 refuses are left out and listed with the reason (`error adding symbols`; a relocation against a *defined* symbol that does not fit, mapped to the referencing object with nm), and the link is retried. Every undefined symbol is bound to a trap stub that reports it, and the result is packed as `guest.nso`. Its numbers count every compiled object; S5's script links from its boot code with gc. | S5's `toolchain/game/build.sh` is in the tree; `GUEST_LINK=auto` picks it automatically |
| `tools/pipeline/host_nine.py` | The host-module build with Nine (render/nine README, "Host build integration"). It compiles `host.c`, `render/nine/host/*.c` and the generated `nine_proxy_host.c` against the Mesa tree, and links them with the Nine/nouveau libraries and libnx. Nine's entry points are forced into the link; whatever is still undefined is reported. | render/nine has its own host build script |
| `build.py` link stage | Removes `build/link/out/objcopy/` before each `fnvlink.py resolve`. resolve writes one `.args`/`.redefine`/`.src` per object but never removes old ones, so `apply` would rewrite objects of units that no longer compile. | S2's resolve clears its per-object outputs |
| `build.py` compile stage | Header dependencies: `-MD` in `CXX`, and objects deleted when a header changed. compile.sh compares the object with the unit's source only. | compile.sh tracks dependencies |

## Findings of the first runs (2026-11-03)

These are interface problems the pipeline found, recorded here for their owners:

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
* `GAME_CXX=tc` (the default with a toolchain) compiles the units against newlib, as the guest links them.
  Census and compile numbers can then differ slightly from journal entries measured with the Ubuntu stand-in;
  `GAME_CXX=standin` reproduces those.
* The emulator gate covers the S5 hello guest. The game guest runs under the emulator only as an informational
  check, and only with S5's boot code.
