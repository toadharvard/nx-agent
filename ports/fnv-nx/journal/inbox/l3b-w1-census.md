# l3b-w1 census: direct toolchain calls and test doubles over ports/fnv-nx/lift

Measured at commit 6385fff6 (origin/claude/fnv-l3b-lift), whole tree ports/fnv-nx/lift/. Script: lift/tools/census_doubles.py (pure function of the tree; `sites`, `doubles`, `report`). No fixes made.

## Headline

- **Part A:** 46 direct toolchain sites: 45 subprocess calls (40 resolved from the AST + 5 resolved by hand, see below) + 1 recipe JSON that names the stock compiler. 0 of them use the msvcmodel external. Every site is a compiler/assembler/linker/nm call of the stock `aarch64-linux-gnu-*` cross tools or the host `gcc`/`g++`.
- **Part B:** 9 of 35 test/bench programs (23 test/*.py, 5 bench/*.py, 7 proof/tests/*.py) have doubles. 6 distinct symbols collide with a symbol a product unit (lift/rt/*.c) defines; 13 distinct boundary doubles; 19 distinct symbols in total; 67 (file, symbol) pairs.

## Method and deviations (read first)

- Part A: Python `ast`: `subprocess.run/call/check_call/check_output/Popen`, `os.system/popen` calls; first argv element a string constant or a name/list bound to one. Plus a literal scan of every .sh and .json under lift/ (comments skipped). 17 calls have a non-constant argv head (`cmd + [src, ...]` job tables); I resolved them by hand from the job lists (rows marked "hand"). The other 12 of those 17 run python, isla, or test-built host binaries and are not toolchain calls.
- Part B, DEVIATION: this container has no `aarch64-linux-gnu-*` cross tools (no `aarch64-linux-gnu-nm`; only clang-18 and llvm-nm). I did not build objects. The sources each test writes (module-level string constants and every constant-evaluable `.write()` argument, found by AST) are analysed with clang's AST (host target, errors in function bodies tolerated): every function with a body and every non-extern, non-static, non-const variable definition, by mangled name; asm sources by `.globl/.global/.weak` + label. Product side: the same pass over every lift/rt/*.c (as C++ for seh, enter, unhandled, msvcrt_*), plus nothing the lifter emits is global: the lifter emits `L_%08x` functions and the dispatcher table (`fnvl_disp_table`, `fnvl_g`, `fnvl_phf_params`, from bench/phf.table_asm, the same generator tools/liftgen.py uses), so tab.S of the tests is not a double.
- Product-collision = symbol defined by a test-written source AND by a rt/*.c unit. Boundary double = symbol a test source defines that no lift unit defines but that the product declares (extern in rt/*.h, rt/*.c, or the lifter's CALLX target fnvl_call) or that is libc (memcpy, memset, memmove): the libc/libnx/L4 boundary or the runtime owner's contract. test-own = test-only names (main, t_*, drivers), not doubles.
- Not covered: symbols in the lifted texts themselves (generated, product); libc.S/rt.S symbols in tests that only inherit them (mmxtest, x87dyn: marked "inherits"); the host-built shims (hostshim.c in instest counts under instest).

## Part A: direct toolchain sites

Columns: site, tool, what it builds/links, owner test/bench (= the file in the site).

| site | tool | builds/links (source files named in the call) | via |
|---|---|---|---|
| bench/chain.py:137 | aarch64-linux-gnu-g++ | aarch64-linux-gnu-g++ .cpp | subprocess.run  |
| bench/chain.py:148 | aarch64-linux-gnu-gcc | %s/%s.o aarch64-linux-gnu-gcc | subprocess.check_call  |
| bench/chain.py:150 | aarch64-linux-gnu-ld | aarch64-linux-gnu-ld aarch64elf32 0 /rt.o /x87.o /rcptab.o /tab.o /libc.o /harness.o | subprocess.run  |
| bench/chrun.py:64 | gcc | gcc rt rcptab.c .%d | subprocess.check_call  |
| bench/chrun.py:99 | gcc | gcc .%d | subprocess.check_call  |
| bench/ehtest.py:207 | aarch64-linux-gnu-g++ | c++ aarch64-linux-gnu-g++ /rt /rt/seh.c | subprocess.run  |
| proof/backends.py:49 | g++ | g++ /gen.cpp /gen | subprocess.run  |
| proof/tests/test_fpexact.py:59 | aarch64-linux-gnu-as | aarch64-linux-gnu-as | subprocess.check_call  |
| proof/tests/test_fpexact.py:65 | g++ | g++ .. toolchain f2i_check fnvnx_f2i_check.cc | subprocess.check_call  |
| proof/tests/test_fpexact.py:74 | aarch64-linux-gnu-g++ | .f2i.json | subprocess.check_call  |
| proof/tests/test_fpexact.py:170 | gcc | gcc /c /tools/rcpcert.c /rt/rcptab.c | subprocess.check_call  |
| test/addrwrap.py:243 | aarch64-linux-gnu-g++ | aarch64-linux-gnu-g++ .cpp .o | subprocess.run  |
| test/bthost.py:68 | gcc | gcc | subprocess.run  |
| test/citest.py:71 | aarch64-linux-gnu-nm | aarch64-linux-gnu-nm | subprocess.run  |
| test/citest.py:74 | aarch64-linux-gnu-ld | aarch64-linux-gnu-ld aarch64elf32 test_main | subprocess.run  |
| test/citest.py:77 | aarch64-linux-gnu-nm | aarch64-linux-gnu-nm /msvcrt_ci.o | subprocess.run  |
| test/dsl_emit_test.py:76 | aarch64-linux-gnu-g++ | aarch64-linux-gnu-g++ | subprocess.run  |
| test/fmathtest.py:107 | gcc | gcc /fmath.c /driver.c /fmath_t.so | subprocess.check_call  |
| test/imptest.py:145 | aarch64-linux-gnu-ld | aarch64-linux-gnu-ld aarch64elf32 test_main | subprocess.run  |
| test/instest.py:468 | aarch64-linux-gnu-g++ | aarch64-linux-gnu-g++ | subprocess.run  |
| test/instest.py:479 | aarch64-linux-gnu-ld | aarch64-linux-gnu-ld aarch64elf32 0 /rt.o /x87.o /cpumodel.o /hostticks.o /fmath.o /rcptab.o | subprocess.run  |
| test/instest.py:521 | aarch64-linux-gnu-gcc | aarch64-linux-gnu-gcc /rt.S /rt.o | subprocess.check_call  |
| test/instest.py:522 | aarch64-linux-gnu-gcc | aarch64-linux-gnu-gcc rt .o .c / | subprocess.check_call  |
| test/instest.py:523 | aarch64-linux-gnu-gcc | aarch64-linux-gnu-gcc rt rt x87.c /x87.o | subprocess.check_call  |
| test/instest.py:527 | aarch64-linux-gnu-gcc | aarch64-linux-gnu-gcc /hostticks.c /hostticks.o | subprocess.check_call  |
| test/instest.py:528 | aarch64-linux-gnu-gcc | aarch64-linux-gnu-gcc rt cpumodel.c /cpumodel.o | subprocess.check_call  |
| test/instest.py:544 | gcc | gcc /cpumodel_host.so rt cpumodel.c /hostshim.c | subprocess.check_call  |
| test/instest.py:547 | aarch64-linux-gnu-g++ | aarch64-linux-gnu-g++ /off.cpp | subprocess.check_call  |
| test/msvcrttest.py:344 | aarch64-linux-gnu-nm | aarch64-linux-gnu-nm | subprocess.run  |
| test/msvcrttest.py:361 | aarch64-linux-gnu-ld | aarch64-linux-gnu-ld aarch64elf32 test_main | subprocess.run  |
| test/r9test.py:102 | aarch64-linux-gnu-gcc | aarch64-linux-gnu-gcc /rt.S /rt.o | subprocess.check_call  |
| test/r9test.py:142 | aarch64-linux-gnu-g++ | x.cpp aarch64-linux-gnu-g++ | subprocess.run  |
| test/rcptest.py:86 | gcc | gcc /rcpcmp /rcpcmp.c /rt/rcptab.c | subprocess.check_call  |
| test/rothost.py:60 | gcc | gcc | subprocess.run  |
| test/sehcodes.py:29 | aarch64-linux-gnu-g++ | .o aarch64-linux-gnu-g++ / | subprocess.check_call  |
| test/sehcodes.py:30 | aarch64-linux-gnu-gcc | aarch64-linux-gnu-gcc /stubs.S /stubs.S.o | subprocess.check_call  |
| test/sehcodes.py:31 | aarch64-linux-gnu-ld | aarch64-linux-gnu-ld aarch64elf32 0 /drv.elf /%s.o drv.cpp seh.c stubs.S | subprocess.check_call  |
| test/x87test.py:89 | gcc | gcc /../rt /../rt/x87.c driver.c x87t.so | subprocess.check_call  |
| test/x87test.py:91 | gcc | gcc native.c nat.so | subprocess.check_call  |
| tools/rcpgen.py:6 | gcc | gcc /rcpgen.c /g | subprocess.check_call  |
| test/citest.py:67 (hand) | aarch64-linux-gnu-g++ ×2, -gcc ×2 | driver.cpp, rt/msvcrt_ci.c (g++); rt/fmath.c, rt/x87.c (gcc) | subprocess.run |
| test/imptest.py:142 (hand) | aarch64-linux-gnu-g++ ×5, -gcc ×5 | guest.cpp, imp.cpp, rt/enter.c, rt/seh.c, rt/unhandled.c (g++); rt/rt.c, rt/rcptab.c, rt/x87.c, tab.S, native.c (gcc) | subprocess.run |
| test/msvcrttest.py:340 (hand) | aarch64-linux-gnu-g++ / -gcc | rt/seh.c, enter.c, unhandled.c, msvcrt_eh.c, msvcrt_fp.c (g++); rt/rt.c, rcptab.c, x87.c, fmath.c (gcc) | subprocess.run |
| test/msvcrttest.py:358 (hand) | aarch64-linux-gnu-g++ / -gcc | guest.cpp, imp.cpp (g++); tab.S, native.c (gcc) | subprocess.run |
| proof/tests/test_fpexact.py:74 (hand) | aarch64-linux-gnu-g++ (CXX list, line 26) | each test's generated .cpp with the f2i plugin | subprocess.check_call |
| tools/liftgen.py:181 (recipe JSON) | compiler.cc/cxx = aarch64-linux-gnu-gcc-13 / g++-13 | emits lift/pack/build.json for the lifted pack: names the stock compiler, not the msvcmodel external | json |

Totals: 40 AST-resolved + 5 hand = 45 subprocess sites, + 1 recipe = 46. By tool over the 40 AST-resolved sites: host gcc 11, host g++ 2, aarch64-linux-gnu-g++ 9, aarch64-linux-gnu-gcc 8, aarch64-linux-gnu-ld 6, aarch64-linux-gnu-nm 3, aarch64-linux-gnu-as 1; the 5 hand rows are all aarch64-linux-gnu-g++/gcc. No os.system, no shell=True. The .sh files (tools/farm_liftgen.sh, farm_packgen.sh) invoke no compiler (comment: "no compiler runs here"). No .json under lift/ names a compiler except what liftgen.py:181 emits (the checked-in ASSUMPTIONS.l3b.json hits were prose). tools/census_doubles.py itself calls llvm-nm (excluded from the counts).
Non-aarch64 (host gcc/g++) sites: bench/chrun.py:64,99; proof/backends.py:49; proof/tests/test_fpexact.py:65,170; test/bthost.py:68; test/fmathtest.py:107; test/instest.py:544; test/rcptest.py:86; test/rothost.py:60; test/x87test.py:89,91; tools/rcpgen.py:6.

## Part B: doubles per file

| file | defined by test AND by a product unit | boundary doubles | test-own (not doubles) |
|---|---|---|---|
| test/bthost.py | - | - | main |
| test/citest.py | - | fnvnx_win32_lookup_export, memcpy, memset | t_errno, test_main |
| test/fmathtest.py | - | - | bigflag, dbg_eval, run_h, run_m, set_nstart |
| test/imptest.py | - | ExitProcess, UnhandledExceptionFilter, fnvl_host_ticks_ns, fnvl_host_tls_slot, fnvnx_win32_lookup_export, fnvnx_win32_set_raise_backend, memcpy, memset | name, t_add2, t_boom, t_call1, t_each, t_fmix, t_meth, t_mul64, t_vsum, test_main |
| test/instest.py | fnvl_dispatch, fnvl_raise, fnvl_raise_av | fnvl_call, fnvl_host_ticks_ns, fnvl_trace, fnvl_world_resume, fnvl_world_stop, memcpy, memmove, memset | - |
| test/mmxtest.py (inherits test/instest.py) | fnvl_dispatch, fnvl_raise, fnvl_raise_av | fnvl_call, fnvl_host_ticks_ns, fnvl_trace, fnvl_world_resume, fnvl_world_stop, memcpy, memmove, memset | - |
| test/msvcrttest.py | - | ExitProcess, UnhandledExceptionFilter, fnvl_host_ticks_ns, fnvl_host_tls_slot, fnvnx_win32_lookup_export, fnvnx_win32_set_raise_backend, memcpy, memset | g_free_arg, g_free_n, g_ue_addr, g_ue_code, g_ue_n, t_abort, t_free, test_main |
| test/r9test.py | - | fnvl_world_resume, fnvl_world_stop | - |
| test/rcptest.py | - | - | main |
| test/rothost.py | - | - | main |
| test/sehcodes.py | fnvl_dispatch, fnvl_os_unhandled | memcpy, memset | drv_av, drv_ctx, drv_raise, drv_seg, g_ctx, g_rec |
| test/x87dyn.py (inherits test/instest.py) | fnvl_dispatch, fnvl_raise, fnvl_raise_av | fnvl_call, fnvl_host_ticks_ns, fnvl_trace, fnvl_world_resume, fnvl_world_stop, memcpy, memmove, memset | - |
| test/x87test.py | - | - | nat_run, run |
| bench/chain.py | fnvl_nobody, fnvl_os_unhandled, fnvl_raise, fnvl_raise_av, fnvl_raise_ud | fnvl_trace, memcpy, memmove, memset | fnvl_enter, fnvl_trace_sw |

files with doubles 9; distinct product-collision symbols 6; distinct boundary symbols 13; (file,symbol) pairs 67; distinct symbols 19

product units (rt/*.c) define: cpumodel.c: 3; enter.c: 6; fmath.c: 23; msvcrt_ci.c: 15; msvcrt_eh.c: 11; msvcrt_fp.c: 5; rcptab.c: 2; rt.c: 1; seh.c: 7; unhandled.c: 3; x87.c: 16
product expects from its boundary (declared, defined by no unit): ExitProcess, UnhandledExceptionFilter, _ZL17cxx_frame_handlerjjjjjjPj, fnvl_call, fnvl_disp_table, fnvl_g, fnvl_host_ticks_ns, fnvl_host_tls_slot, fnvl_phf_params, fnvl_trace, fnvl_world_resume, fnvl_world_stop, fnvnx_lift_cpu_bytes, fnvnx_lift_raise_backend, fnvnx_win32_lookup_export, fnvnx_win32_set_raise_backend

Files with no row (addrwrap, dsl_emit_test, instest_extra, r9equal, x87dyn's own sources, bench/chrun, ehtest, nltest, phf, all of proof/tests, and the data-only test/*.py) write no C/asm harness source: 0 doubles of their own. Of the 35 programs, 14 write harness sources (rows above, 2 of them inherit), 9 have doubles.

Product collisions by symbol: fnvl_dispatch (rt/rt.c): instest rt.S, sehcodes drv.cpp; fnvl_raise and fnvl_raise_av (rt/seh.c): instest rt.S, bench/harness.S via chain, (raise_av also chain libc.S weak); fnvl_os_unhandled (rt/unhandled.c): sehcodes, harness.S; fnvl_raise_ud (rt/seh.c) and fnvl_nobody (rt/enter.c): harness.S.
instest rt.S is 10 symbols (fnvl_call, fnvl_dispatch, fnvl_raise, fnvl_raise_av, fnvl_trace, fnvl_world_stop, fnvl_world_resume, memcpy, memset, memmove) + hostticks.c fnvl_host_ticks_ns = the lead's 11; of the 11, 3 collide with rt units, 8 are boundary.
Open point for the lead: fnvnx_win32_lookup_export / fnvnx_win32_set_raise_backend / ExitProcess / UnhandledExceptionFilter / fnvl_host_tls_slot are classed boundary because lift/rt declares them; the real definitions live outside lift/ (platform), not checked here.
