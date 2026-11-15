# l1-contract-w14-001: incremental L1 verdict measurement and second-build-path census

Branch claude/fnv-l1-contract-w14. Measured after `farm/setup_verdict_env.sh` + `envcheck.py check` (OK, no mismatch).
Machine: 4 cores (nproc 4), 15 GiB RAM, Linux 6.18, root. `l1.py verdict --repo . --commit C --verdicts /home/user/vd`, one at a time, GATE_CACHE = vd/cache.

## Base commit (finding 1)
The task named trunk head 58a79b3e. A verdict at 58a79b3e on this machine is `blocked: env`:
`env: tree /usr/include: want 85bf5c8d51f2, have 88c6068d952d` (its env.json differs from the one of e90a7e4, which the machine matches).
Trunk head at the time of measuring was e90a7e4 (a2453fe later); all scenarios are on top of e90a7e4.

## Table
Every verdict is RED: gate `selftests` 49/51 certified, uncertified ports/fnv-nx/verify/tests/test_argorder.py and test_guest.py. 23 gates, over_budget False.
No product root is admitted (admit.json has none), so every recipe gate is 0/0 and no recipe stage runs: stages run/hit = 0/0 in all scenarios.

| Sc | Commit measured | What | Total s (L1 line / wall) | Top 5 gates (seconds) | Stages run/hit |
|---|---|---|---|---|---|
| S0 cold | e90a7e4 | empty VDIR | 134.1 / 135.9 | per-gate json not retained (overwritten by S1, same tree); the L1 line gives 134.093 s | 0/0 |
| S1 warm no change | e90a7e4 | same commit | run1 143.09 / 145.1; run2 149.5 / 151.3 | selftests 146.4, oracle 1.35, pure 0.61, reachlink 0.06, build 0.058 (run2) | 0/0 |
| S2 header comment | 0601cdde79ad | one comment line in tests/guest/hostapi.h | run1 151.7 / 153.6; run2 140.8 / 142.5 | run1: selftests 148.8, oracle 1.07, pure 0.62, n2_data 0.075, determinism 0.061 | 0/0 |
| S3 one .cc | 75d656d7eb32 | comment in verify/a64access.cc | run1 142.5 / 144.3; run2 140.7 / 142.9 | run1: selftests 139.4, oracle 1.34, pure 0.57, rtti 0.09, disp 0.08 | 0/0 |
| S4 tool only | cda85c25e06c | comment in verify/gate_admit.py | run1 141.4 / 143.1; run2 138.9 / 140.8 | run1: selftests 138.8, oracle 1.04, pure 0.68, build 0.053, bodysel 0.047 | 0/0 |

S2 header: no tracked .h is compiled by the trunk's verdict (no product root). The only tracked headers are test fixtures
tests/guest/hostapi.h (included by tests/test_bootmachine.py's generated C) and tests/guest/fnvnx/sockwire.h (included by hostapi.h): 1 includer each.
a64access.cc includes only system LLVM headers (not tracked), so hostapi.h was used. Scenario commits are local only (branches w13-s2/s3/s4 in the worktree /home/user/wt), not pushed.

## Findings
1. selftests is 138-149 s of 139-152 s in every scenario, warm or not, whatever the change (even S4, a comment in a gate): the gate is red, so the two uncertified files
   (test_argorder.py, test_guest.py) rerun every time and the green ones also take most of the time; the other 22 gates together take about 3 s.
   The incremental verdict is limited by that one gate; a red test is not cached, so a red trunk is also slow.
2. Warm == cold within noise: S1 143-150 s vs S0 134 s. GATE_CACHE does not shorten selftests here.
3. With no product root the recipe machinery is not exercised by the verdict at all (all stages 0/0).

## Census: C/C++/asm/link invocations outside recipe.py and unitstage.py (read from the argv construction)
| file:line | builds | gate runs it | GATE_CACHE | could go through recipe.py |
|---|---|---|---|---|
| verify/objstub.py:120-158 (A64_BUILD, tool()) | `a64access` (host g++ + llvm-config-18, LLVM MC disassembler) | `objstub` (gate_objstub -> objstub.run_check -> tool(cache)) | yes, GATE_CACHE/objstub/a64access-<hash> (a bare file, not GATE_CACHE/store) | yes: a host product root / tool root with an external llvm toolchain; keys on LLVM headers/libs |
| verify/objstub.py:1127-1141 (plugin_so) | `objstub_plugin.so` with host g++ against the recipe compiler's plugin headers | `objstub` | yes, GATE_CACHE/objstub/plugin-<hash>/ | yes: same as the recipe's plugin stage (recipe.plugins builds guest plugins; this one is built outside it) |
| verify/objstub.py:1164-1179 (derive_terminators, CLI `terminators`) | same plugin, one TU compiled with the recipe compiler (-S, plugin) | no gate (CLI only); unit_terminators (line ~1150) runs the compiler `-S -fplugin` per unit .ii from `pp_key` | derive_terminators: none (tempdir); unit_terminators: GATE_CACHE/objstub/t-<key>.txt | unit_terminators is a CC-like stage over unit.ii: yes as a recipe stage per unit |
| verify/bootgpu.py:42-47 (_load_shim) | callback shim .so with host `gcc -O2 -fPIC -shared` | no L1 gate (L4 boot, farm/boot_job.sh --gpu); selftests run it through tests | none: tempdir per process | yes (tool root); but L1 order says one render backend deko3d, this GL shim is slated for removal |
| link/ld.py:429-440, 473, 506 (command(), subprocess.run) | the guest ELF with the AArch64 ILP32 `ld` (pinned linker path given by --ld) | not a gate: the gates `abs`/`disp`/`ld` read its outputs; run by the link stage by hand / farm | none | partly: recipe.py:661 already links in the recipe for `link` roots; ld.py is the second linker path (adds pull-in closure model, gate file ld.json) |
| verify/envcheck.py:174, 187, 193 | none built: `g++ -M`, `clang -m32 -M`, `gcc -print-file-name` probes to list header/lib files | via every verdict (envcheck.check) | none | no: environment probes, not builds |
| verify/icount.py:219,223 | none: aarch64 readelf/nm | no | none | n/a |
| contract/gate_xunit, gate_rtti, gate_pcrel (nm/readelf by name) | none: binutils reads | gates xunit, rtti, pcrel | contract/<key> under GATE_CACHE | n/a (not compilers) |
| toolchain/msvcmodel/build.sh:~50-70, test_msvcmodel.py:31 | the msvcmodel GCC 13.3.0 itself (configure/make from the sha256 tarball); the test compiles with it `-S` | no gate (selftests runs test_msvcmodel.py if it has CXX) | none | the compiler build is a tool root, 30 min: belongs on a farm, mirrored as fnv-nx-build tools/gcc-ilp32-msvcmodel (already how recipe consumes it) |
| farm/fontstate_job.sh:109 | fontdump.exe with `i686-w64-mingw32-gcc` (Windows exe run under wine on a farm) | no | none | not a guest/host product; farm job |
| farm/boot_job.sh:31,88 | installs `gcc` for the bootgpu shim only | no | none | same as bootgpu |
No other invoker of a C/C++ compiler, assembler or linker exists under verify/, link/, contract/, farm/, toolchain/ (the toolchain/*/ plugin directories hold only sources: fnvnx_*.cc, built by recipe.plugins).

## Cache directories other than GATE_CACHE/store
Under GATE_CACHE (VDIR/cache): objects/<tree>/<root>/ (gate_build), contract/<key>/ (gate_contract), external/ (recipe externals, gate_oracle, gate_determinism), objstub/ (a64access, plugin-*, t-*.txt, per-object <key>.json), reach/<tree>/ (reach facts, written by a gate that does not exist yet), selftests: <hash>.json at the top of GATE_CACHE (4 files here).
Outside GATE_CACHE: verify/cas.py (store inside whatever store path the caller gives, normally GATE_CACHE/store); gate_determinism creates mkdtemp stores (gate-det-*), gate_build/linkstrict/objstub/argorder/contract/oracle create mkdtemp caches when GATE_CACHE is unset; bootgpu mkdtemp shim dir; no ~/.cache or XDG writes found.

## Recipe items sent later by the lead (w14), commits on this branch
See the commit list: failed_units, -Werror=attributes for guest roots, envcheck before any stage.
