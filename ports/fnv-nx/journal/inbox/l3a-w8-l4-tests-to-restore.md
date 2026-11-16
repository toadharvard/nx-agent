# l3a w8 -> L4: lift tests that need the shared model (restore on it, no doubles)

The lift tests no longer define any platform, libc or runtime symbol (commit 0b8493df and follow-up). Each exits 3 (BLOCKED) at link with the list of undefined product symbols.
Coverage that the removed doubles gave, to restore on the L4 boot machine's shared model (the platform root linked, its kernel/GPU/clock model, no per-test replacement):

1. msvcrttest `uh` (a throw with no frame): UnhandledExceptionFilter must be called exactly once with the EXCEPTION_POINTERS whose record code is 0xE06D7363, then ExitProcess(0xE06D7363).
   Observe it through the model (the platform's own UnhandledExceptionFilter / ExitProcess), not through a test function of those names. The test now stops at the real `ExitProcess` symbol.
2. msvcrttest `td1` (type_info destructor): `free` is reached by `fnvnx_win32_lookup_export("msvcrt.dll", "free")` with the name pointer 0x12340000; assert the argument through the model's heap
   (an allocation the test made and the model observes being freed), not a recording free.
3. imptest `half` (c3): the LOOKUP-bound import path (`bind lookup`, cdecl double -> double in ST0, result 4.5 -> fistp 4, + fmix 7.5 -> 8): now bound by `sym`, so the path through
   `fnvnx_win32_lookup_export` is untested. Restore with a real platform export of the shape double(double) (e.g. a libm export of the platform) as the target.
4. imptest `boom` (c8): `t_boom` calls the platform's `w32_raise` (platform/win32/thread.cpp:642, w32.h:304) and relies on the backend `fnvl_runtime_install` registers
   (`fnvnx_win32_set_raise_backend`); the test no longer installs a backend of its own.
5. citest: errno through the platform's `_errno` (preset 7, "unchanged" visible); the errno rule of rt/msvcrt_ci.cc for domain / range errors.
6. instest rdtsc: the platform clock is `fnvl_host_ticks_ns` (platform/boundary/host_hooks.cpp), ticks = ns * 21 / 10 (rt/cpumodel.c). The executor that runs a state must return
   c['clk'] = (ns read before, ns read after the form) from the model's clock; the test then asserts conv(lo) <= t <= conv(hi) and that successive reads do not go backwards. Without c['clk'] the
   test exits 3.
7. instest/disptest: the lifted code's host hooks (`fnvl_host_tls_slot`, `fnvl_host_ticks_ns`) and libc (memcpy, memset, memmove) come from the platform root / fnvnx-a64, not from `rt.S`.
