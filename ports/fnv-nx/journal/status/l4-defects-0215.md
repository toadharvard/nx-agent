# L4 defects pass R (CLAUDE.md "Owner order of 2026-11-15: defects impossible by construction")

Written by L4 worker w6 on `claude/fnv-l4-verify-w6-r`. Nothing is fixed in this pass.

Method: every file below was read and grepped for tables, literals and tool calls. Counts are findings (rows), measured at the commit that carries this file (see the commit message of the push, `git log -1 -- journal/status/l4-defects-0215.md`). Line numbers are for that tree. "path" = blocks 4.1/4.2 (bootguest link and boot) or 4.3 (gl4 root); "after" = otherwise.

## Universe (rule: files L4 owns on w6-r's head)

Rule, over `git ls-files` of `ports/fnv-nx`: `verify/boot*.py`, `verify/bootrt/**`, `verify/icount*`, `verify/oracle*`, `verify/lockobs.py`, `verify/tests/test_{boot,icount,oracle,lockobs}*`, `verify/tests/oracle.json`, `tests/bootguest/**`, `farm/boot_job.sh`. L4 docs: only `tests/bootguest/README.md` and the docstrings of the files above (no other doc names the boot machine except `verify/TOOLS.md`, which L1 owns).

**43 files**, 12551 lines: 12 `verify/boot*.py`, `icount.py`, `lockobs.py`, `oraclecheck.py`, `oracle_pins.json`, `verify/tests/oracle.json`, 3 `verify/bootrt` files, 19 `verify/tests/test_*` files, 3 `tests/bootguest` files (README.md, build.json, guest.c), `farm/boot_job.sh`. The list is the output of the rule above over `git ls-files`.

## Totals

| class | rows | path | after |
|---|---|---|---|
| A: hand-written set/table defining behaviour or coverage | 27 | 1 (A25) | 26 |
| B: second copy of a fact that lives elsewhere | 12 | 3 (B1, B2, B6) | 9 |
| C: second path | 6 | 2 (C1, C2) | 4 |
| all | 45 | 6 | 39 |

(A rows that are tuples of the same kind in one file are one row.) Detail follows; every row has a replacement.

## A. Hand-written tables (derive from the named single source)

| id | file:line | finding | replacement (D = derivation, G = generation, X = deletion) | tag |
|---|---|---|---|---|
| A1 | verify/bootmachine.py:112-120 | `SLOTS`: the 45 host-API slot names | D: one reader of hostabi/include/fnvnx/hostapi.h (struct `FnvnxHostApi` member order; `parse_totality` of tests/test_bootmachine.py already parses it: move it into a verify module and import it in the machine) | after |
| A2 | bootmachine.py:121 | `API_SIZE = 0x30 + 8*len(SLOTS)` | D: from the same parse (offsetof of the last slot + 8) | after |
| A3 | bootmachine.py:128-131 | `PLATFORM_FNS`: module-11 services 1..38 with names and least nargs, by hand | D: hostapi.h TOTALITY TABLE, same reader as A1 | after |
| A4 | bootmachine.py:132-134 | `SOCK_SERVICES`: sockets 16..26, 30, 31 | D: same table | after |
| A5 | bootmachine.py:122-125 | `FNVNX_EXT_PLATFORM`, `EXT_NO_MODULE/NO_SERVICE/BAD_NARGS`, `FNVNX_EXT_MODULES`, `FNVNX_TEXT_ENTRY_MAX` | D: `header_define` (exists, bootmachine.py:161) over hostapi.h; the EXT_* values from the header's macro expression | after |
| A6 | bootmachine.py:111 | `REACH_VIOLATION = 0x52` | D: fnvnx/reach.h | after |
| A7 | bootmachine.py:157,173,174,179-182 | error codes by hand: `FNVNX_HE_*`, `KERNEL_*`, `SEC_E_*`, `LIBNX_*`, `ERR_*`; three of them (0xCA01, 0xCC01, 0xD401) appear twice (lines 174 and 181) | D: the libnx result.h (pinned, Horizon headers) through MAKERESULT; hostapi.h names the rest | after |
| A8 | bootmachine.py:175-176,156 | `SEC_MAX_SIZE`, `SEC_SECTIONS`, `SEC_VIEWS`, `VM_ALIGN`, `VM_FLOOR`, `MAP_FIXED_MAX` copied from L5 host_section.inc / host_vm.inc | D: L5 exports them in a header (hostabi or placement.h) and the machine reads it by name; until then it is the L5 .inc | after |
| A9 | bootmachine.py:154 | `TZ_VC1 = (0, 0, b"UTC")` | D: the declared-machine record (fixed time zone: UTC in the emulator) | after |
| A10 | bootmachine.py:155 | `WINDOW = (0x00400000, 0x02100000)` image range of the exe | D: the exe's facts (PE ImageBase / SizeOfImage; verify/bootstart.py already reads the PE) | after |
| A11 | bootmachine.py:159 | `FNVNX_DEFAULT_CORE = 0`, `FNVNX_CPU_KEEP = 0xFF` (only `FNVNX_APP_CORES` is read by name) | D: `header_define` over placement.h / hostapi.h | after |
| A12 | verify/bootsock.py:68-75 | `WE_*` (14 wire errnos), `WIRE_AF`, `WIRE_SOCKTYPE`, `WIRE_AI_MASK`, `WEAI_*` copied from sockwire.h / sockwire_errno.h | D: parse the two headers at import (the test parses them to compare; the comparison and the copy become one reader). `WIRE_AI_MASK`/`WEAI_*` are dead: X (already planned) | after |
| A13 | bootsock.py:76-82 | `WPOLLNVAL`, `POLL_MAX`, `WO_*`, `MSG_*`, `SOCKADDR`, `FIRST_HANDLE` | D: sockwire.h | after |
| A14 | bootsock.py:83-90 | option table `OPTS`, `SIZE`, `BOOLEAN`, `LEVEL_FAMILY`, `LEVEL_TYPE`, `UNDECIDED_PROTOCOLS` | D: the pinned newlib/Horizon `sys/socket.h` + `netinet/*.h` (vendor headers) and sockwire.h option shapes; one stage generating a table | after |
| A15 | verify/bootpath.py:23-26 | `GAME_MOUNTS`, `PORT_NAMES` (13 names), `PORT_PREFIXES` | D: verify/boot_inputs.json mounts + the L5 platform's own port-file record (platform defines which names it writes); a hand list of the port's files is a second copy | after |
| A16 | verify/bootlayout.py:43-50 | `PAGE`, `ALIGN`, `IMAGE_MIN`, `STACK_END`, `HOST_PAGE`, `HEAP_BASE`, `TOP`, `MIN_ADDR`, `CRT0_MAGIC`, `DF_1_PIE`, `REGION_NAMES` | D: placement.h (regions) and the crt0 header definition (rt/guest/crt0.s `.crt0.hdr` words); ELF constants from `elf.h` of the pinned toolchain | after |
| A17 | verify/bootstart.py:39-45 | `PAGE`, `SENTINEL`, `RESERVED`, `MEM_*`, `TEB_HEAD`, `LIFT`, `START` (names of the entry symbols) | D: loader facts (L5 loader headers; the entry symbol names from the lifted pack record) | after |
| A18 | bootstart.py:242 | `FAILURE`: 13 loader message fragments by hand | G: from the loader's message table (one list in the loader source, generated into a header) | after |
| A19 | verify/icount.py:38-57 | `OPCODE_NAMES`, `OPCODES` (7 Arm encodings with masks), `N_FIXED`, `SLOT` | D: the Arm A64 encoding source that L1 already pins (Sail model, ASSUMPTION 9): one generated decode table for ldar/stlr, ldapr, ldaxr/stlxr, dmb, dsb, isb | after |
| A20 | verify/bootmiles.py:45-46; verify/bootinputs.py:240-241,267; verify/bootinput.py:28-29,164-167 | grammar tables: `KINDS`, `ADDR_KINDS`, `ADDR_PARTS`, `OLD_KINDS`, `SOURCES`, `EXPECT_KEYS`, `FIELDS`, `NO_PAD/KBD/MOUSE/TOUCH` (the same milestone kinds in two files) | G: one grammar record that generates the parsers and the docstrings; `OLD_KINDS` and the `stage-table --old` converter (bootinputs.py:362-395) belong to the retired S5 table: X | after |
| A21 | verify/bootdecl.py:29 | `KEYS` of a declared input pin | D: the schema in verify/boot_inputs.json (one record) | after |
| A22 | verify/boot.py:75-77,136 | `HOST_EVENTS`, `INPUT_ERRORS`, `MEM_MAX` | D: events registered by the modules that emit them (bootgpu); errors by a base class | after |
| A23 | verify/bootgpu.py:19-20 | `SIZES = {handheld: 1280x720, docked: 1920x1080}`, `MODULE = 10` | D: the declared-target record (Switch product decisions) and hostapi.h module ids | after |
| A24 | verify/lockobs.py:28-30 | `DEFAULT_NAMES`, `TRYLOCK_OK`, `MASK` | D: platform/os/os.h API names and the trylock value, read from the header | after |
| A25 | tests/bootguest/guest.c, build.json (45 variants), README.md:5 | the MODE set: 45 hand-written variants m0..m92, each with the whole flag set repeated; the MODE numbers are repeated in comments, in tests and in test_bootmachine.py:126 | G: the variant list is generated from the modes of guest.c by one rule (e.g. every `MODE == N` / `case MODE` label) with the one guest flag profile; tests take "every variant of the root" instead of a number list | path |
| A26 | verify/tests/test_bootmachine.py:126,225-226,384,455-458; test_bootcores.py:30,313; test_boot.py:196; test_bootgpu.py:95-109,166; test_lockobs.py:19-20 | the tests restate the tables as literals: the mode tuple (36 entries), `45` slots, `31` services, `SERVED = [...]`, fn lists `1..9, 11..14, 34..38`, `SLOT` offsets, "3 cores", `4+45+4` event counts, "presents == 4", `RUNNERS = [...]` | D: expected values computed from the same readers as A1-A5/A11 (so a header change moves test and machine together) and from the scenario file; the runner list by the `case_*` rule over guest.c; presents count from the scenario | after |
| A27 | verify/tests/test_boot_job.py:55-68; test_bootgpu.py:31-36 | `MODULES` header line, `FONT_FILES`, `NXGL4`/`BRIDGE` pins and `NXGL4_SHA256` retyped in the tests | D: read from verify/boot_inputs.json and the artifact pin records; the table header from bootmiles' grammar record (A20) | after |

A: 27 rows. Path: A25 only (the bootguest variant list and flags are the copy that broke 4.1 twice: `plugin_cxx`, the plugin inputs). A1-A4, A12-A14, A26 "after" only because header and tables agree today (test_bootmachine checks them); the rule says derive, so the row stays.

## B. Second copies of a fact that lives elsewhere

| id | file:line | finding | replacement | tag |
|---|---|---|---|---|
| B1 | tests/bootguest/build.json: flags `-std=gnu++20 -DFNVNX_SDD=1 -D__LINUX_ERRNO_EXTENSIONS__ -O2 -g -ffunction-sections ...` in all 45 variants (about 40 lines each), the plugin list and `link.roots` | the guest profile (flags, plugins, plugin args and inputs) is restated from platform/build.json / link/guest.json / DATAMODEL | D: the root declares only what is its own (units, MODE); the flags and plugins come from the one guest profile record (L1/L2 own the record) | path |
| B2 | farm/boot_job.sh:35-36,106 | requires the package `gcc` for "the callback shim of verify/bootgpu.py"; that shim is gone (verify/bootrt, recipe-built) | X: delete `gcc` from the check and the comment | path (4.3) |
| B3 | farm/boot_job.sh:40-45 (comments), 98-101 | frame sizes (1280x720 = 3.7 MB, 1920x1080 = 8.3 MB), "GL 4.5 on Mesa llvmpipe" restated in prose | D: the sizes from A23's record; or X (prose) | after |
| B4 | verify/boot.py:76 | `GPU_ENV = {LIBGL_ALWAYS_SOFTWARE, GALLIUM_DRIVER=llvmpipe, EGL_PLATFORM=surfaceless}` | D: from the declared boot-machine environment in verify/env.json (the GPU library's environment is part of the declared environment) | after |
| B5 | verify/boot.py:468; bootmachine.py:302 | "docked 1, handheld 0" restated for `FNVNX_PLAT_DISPLAY_DOCKED` | D: hostapi.h comment/define | after |
| B6 | verify/icount.py:248,252 | tool names `aarch64-linux-gnu-readelf`, `aarch64-linux-gnu-nm` typed in the code | D: the binutils path from the recipe's pinned tool root (env.json); see C2 | path |
| B7 | verify/bootsock.py:75,77 and bootmachine.py:149-151 (import list) | `WIRE_AI_MASK`, `WEAI_*` dead after the getaddrinfo removal | X (and what only they use) | after |
| B8 | verify/bootmachine.py:30-100 (module docstring) | restates the service numbers, wire rules, error codes and slot list (`@SERVED@`/`@HOLES@` are already generated; the rest is prose copy) | G: generate the tables of the docstring from the readers of A1-A5/A12, or X the numbers from prose | after |
| B9 | tests/bootguest/README.md:3-9 | `os_lock` / `os.h:42` / `os_guest.c:47-49` line numbers restated | X (the line numbers) or D from os.h | after |
| B10 | farm/boot_job.sh:82 | `handheld\|docked` profile names | D: from A23's record | after |
| B11 | farm/boot_job.sh, verify/test_boot_job.py | the six file names `entries.tsv modules.tsv keys.tsv milestones.tsv run.txt ...` of the artifacts branch and their columns | D: the one grammar record of A20 | after |
| B12 | verify/oracle_pins.json | exe pin restated in the oracle layer | D: contract/pins (L1's single pin file) read by name | after |

B: 12 rows. Path: B1 (4.1/4.2: bootguest), B2 (4.3), B6 (4.2: ELF facts for icount spec).

## C. Second paths

| id | file:line | finding | replacement | tag |
|---|---|---|---|---|
| C1 | farm/boot_job.sh:106-111 | host-package check by `dpkg-query` against env.json for `gcc libegl1 libegl-mesa0`; the check duplicates verify/envcheck.py (which the same job runs at line 103) and the `gcc` entry is dead (B2) | X: delete the loop; envcheck is the one check of the declared environment | path (4.3) |
| C2 | verify/icount.py:241-252 | direct `subprocess` calls of `readelf -S -W` and `nm --defined-only` on a guest ELF to build SPEC.json: a tool call outside the recipe | D: a recipe stage (or the ldcheck facts of the ELF) produces section ranges and symbol addresses as cached facts; icount reads them | path (4.2: boot_job.sh runs `icount spec`) |
| C3 | verify/bootdecl.py:136-142 | own `git fetch` + `git archive` + `tar -x` of fnv-nx-build pins (declared port inputs): a second external mechanism beside the recipe's `external` (commit + tree_sha256) | D: the recipe external/cas fetch with a tree_sha256 | after |
| C4 | verify/oraclecheck.py:74-82 | own runner of tests under `strace` (a second test runner) | D: one runner for tests (the gate's own); oraclecheck consumes its open-trace record | after |
| C5 | verify/tests/test_*.py: 14 files (bootcores, bootheap, bootinputs, bootlayout, bootmachine, bootmiles, bootpath, bootrt, bootsock, bootstart, bootthreadcpu, bootunmap, icount, lockobs) each define their own `case()`/`FAILS` helper; `tb` (test_boot*, lockobs) is a fifteenth | one harness retyped 14 times | G/D: one test harness module (`tb`) imported by all | after |
| C6 | farm/boot_job.sh:278-290 (own orphan-commit publisher with `GIT_INDEX_FILE`) beside farm/runner.sh (the after-merge job runner) | two job runners/publishers for L4 results | D: boot_job becomes a job of runner.sh (the one publisher); needs L1/farm owner confirmation: `runner.sh` is not an L4 file, so this row may be a non-finding | after |

C: 6 rows. Path: C1 (4.3), C2 (4.2).

Not findings (checked): `verify/bootgpu.py` loads `libnxgl4rt.so` through `gate_build.ensure` in `$GATE_CACHE` (the one cache); no direct compiler or ld call exists in the universe; no pip/apt calls; the Unicorn use is the L4 boot machine itself (the one shared model), not a second harness; `splitmix64` and the other algorithm constants are code, not tables.

## Causes (rule 8)

The defect class of this file is the same as the owner's list: facts typed once from memory and kept alongside the header (A1-A13), a profile copied into each of 45 variants (A25/B1), and tools/packages named in code (B6, B2/C1, C2). The proposed closures are the derivations above; the order that matters for the goal is B1/A25 (bootguest), C2/B6 (icount spec for the boot job), then B2/C1 (gl4 root).
