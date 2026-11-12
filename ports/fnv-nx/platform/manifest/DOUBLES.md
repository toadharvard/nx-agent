# Doubles of the tests of L5: rule, census, needs

Owner order of 2026-11-11 (night): a test links the real product files of its root; it defines no symbol that a product
root defines; below the product there is one boundary (GL library, deko3d, libnx, the kernel), met in one shared model, the
L4 boot machine. `check_doubles.py` is the gate. This file is the plan of record for L5's tests (accepted by the L5 lead).

## Census (check_doubles.py through `make -o hostapi-check doubles` in tests/s4, head 79f639ac + this change)

Scope: every object of every test binary that is not a product unit. Product units = the units of platform/build_guest.sh plus the
guest runtime units of toolchain/guest/build.sh (runtime, threads, fs, section_guest, tm: libfnvnx-guest.a), 75 units; the set is read
from the two scripts. 9 binaries, 9 with findings, exit 1. Measured outside the pinned env for the m32 binaries.

| finding | what |
|---|---|
| double | 5 files, 87 symbols: platform_host/os/os_posix.c 67, tests/s4/host_boundary.cpp 9, platform_host/win32/filemap_shared.cpp 9, tests/s7/host_input.cpp 1, tests/s4/m32/host_input.cpp 1 |
| boundary double (test file defines a symbol the product references and no product unit defines) | 3 files, 33 symbols: tests/s15/mtqemu/mlibc.c 24 (a libc), tests/s15/mtqemu/gstart.c 1 (fnvnx_host; the real runtime.c is the product), tests/s4/host_section.cpp 4 (fnvnx_section_*; stands for section_guest.c, whose symbols are unknown until it is built) |
| all test-side definitions | 8 files, 120 symbols |
| replaced unit | 4: boundary_guest.cpp, os_guest.c, platform_glue.cpp, filemap_private.cpp |
| unlinked unit | 6: boundary_guest.cpp, platform_glue.cpp, toolchain/guest/{fs,runtime,section_guest}.c, tm.cpp |
| unbuilt object | 8: platform/os/sock_guest.c, toolchain/guest/{fs,runtime,section_guest}.c, tm.cpp (they include libsysbase headers: sys/iosupport.h, absent in this container, FNVNX_TC missing) and tests/s4/sockwire/*.c (3, same reason) |

Decisions by rule (this change):
- toolchain/guest/threads.c (and the other four runtime files) is the product's guest runtime, not a double. There is no rt/guest copy to
  deduplicate. The mtqemu guest links the real threads.c: it is now a product unit of the census, and no longer a "boundary double".
- sock_guest.c is not dead: platform/win32/winsock.cpp calls socket/bind/connect/... (the declared network is loopback only), and
  hostapi.h still declares the services 16..26, 30, 31 that host_sock.inc serves. It stays a product unit. sockwire_test is its round trip
  with the real host_sock.inc; its guest_shim.c models libsysbase (descriptor table, errno), a boundary below the product: it moves to
  the L4 guest-image rule (R-A) together with the other doubles. Not deleted: it is the only test of sock_guest.c.
- Strong definitions by tests/s15/mtqemu/mlibc.c stand for the ILP32 libc: same rule (guest image with the real newlib on the L4 machine).

Why: the tests run natively on x86-64 Linux, and the guest units that need the target's boundary (FnvnxHostApi, the loader,
libnx) are swapped for native twins. `HOST_SIDE` of platform/build_guest.sh (input_horizon.cpp, backend_audren.c,
os_horizon.c: libnx, correctly not product; os_posix.c, filemap_shared.cpp: the host-test backends) is that swap.

## Rules (R-A .. R-D)

R-A. A test binary is built for the product ABI (aarch64 ILP32, the same unit set and flags as the product root of
verify/recipe.py; the test code is a guest image) and runs on the L4 boot machine. Its FnvnxHostApi (threads and futex, files,
FNVNX_PLAT_SECTION_*, audio_submit, pad and input poll, gpu call, exit slot) is the machine's kernel, GPU and hid model: one
model for every test, checked by L4 against its references. Then os_posix.c, filemap_shared.cpp, host_section.cpp, host_gpu.cpp,
host_input.cpp and host_boundary.cpp have no job and are deleted. Test inputs (pad states, key, mouse and touch strokes, GPU
results, kernel errors that Windows, Wine 9.0 or the console document, the docked mode) are inputs of the model, never a
replaced function.
R-B. The product has one filemap backend (private pages, and the host section services for sections whose views share
changes). What filemap_shared.cpp tests (views seeing each other's writes) is tested through the section services of the model,
with the limits and the error table of hostapi.h (stated today in host_section.cpp; the text moves into the model).
R-C. boundary_guest.cpp links with the real loader (L1: fnvnx_start_*) and a guest fixture image; the fnvnx_bnd_* calls that
host_boundary.h counts become observations of the model. `fnvnx_reach_violation` has one product body, the platform's
(platform/win32/platstat.cpp); tests link it and the model's exit slot records the fact code.
R-D. host_hooks.cpp is linked into the binary that links the lifter runtime (L3a), its only referencer. Until that runtime
is in a test binary the unit has its own test on the real unit and the real clock, no replacement:
tests/s4/test_host_hooks.cpp (the slot is per thread and zero in every new thread; the ticks lie between two reads of
clk_read_ns). Done: the unit is in PLAT_CXX of tests/s4/Makefile; s4: 481 tests, 0 failed.

Until R-A, os_posix.c and filemap_shared.cpp stay product-listed HOST_SIDE and are counted, not hidden.

## Needs of the tests (what the L4 model must serve)

Measured per test in all four test binaries of the doubles census: s4_tests (485 cases), s7_tests (89), s7_tests_vclock
(its 5 vc_ cases under the virtual clock) and s4_m32 (5): 584 test runs. Method: `-finstrument-functions` on the host twins of
the boundary (os_posix.c, host_boundary.cpp, m32/host_input.cpp), hooks.cpp counts the calls of each entry point, one run per
test (the case alone, exact name) minus a run of no test (the start-up cost), doubles_needs.py groups the entry points by the
sections of os.h (and its headers). Raw tables: doubles_needs.tsv (s4), doubles_needs_s7.tsv, doubles_needs_s7_vclock.tsv,
doubles_needs_m32.tsv (calls per need and test). Every case of s4 passes alone (ccb10ff2), so no row is partial.
The s4 table has no gpu row now: the gpu test no longer exists in s4 (it was one test, 2 calls).

| need | tests s4 / s7 / vclock / m32 = all | calls (all) | entry points used |
|---|---|---|---|
| locks (futex words, game locks) | 363 / 75 / 5 / 5 = 448 | 4 563 590 | os_lock/unlock, os_glock/gunlock/gtrylock (5) |
| threads | 138 / 30 / 4 / 0 = 172 | 14 632 011 | 15 of the os_thread_* / os_cpu_* / os_yield family |
| condition variables | 90 / 11 / 3 / 0 = 104 | 58 910 | os_cond_* (4) |
| world stop/resume | 8 / 0 / 0 / 0 = 8 | 57 197 | os_raw_pause/resume, os_thread_wslot |
| files and directories | 154 / 6 / 0 / 2 = 162 | 495 776 | 20 os_file_*, os_dir_*, os_mkdir, os_remove, os_rename, os_stat, ... |
| time (clocks, time zone) | 131 / 32 / 4 / 0 = 167 | 364 299 | os_monotonic_ns, os_sleep_ns, os_ticks, os_tick_freq, os_wall_time_us, os_tz_* (7) |
| vm (reserve/commit/protect) | 25 / 0 / 0 / 0 = 25 | 7 247 | os_vm_* (5) |
| section services | 5 / 0 / 0 / 0 = 5 | 102 | os_shm_create/free/map/unmap |
| process and host info | 37 / 2 / 0 / 0 = 39 | 3 413 | os_cpu_count, os_debug_print, os_memory_info, os_process_id, os_random32 |
| boundary of the game (loader, threads) | 150 / 36 / 0 / 0 = 186 | 455 837 | 8 of fnvnx_bnd_* |
| input poll | 3 / 10 / 0 / 0 = 13 | 902 | fnvnx_input_pump |
| docked mode (a model input) | 6 / 0 / 0 / 0 = 6 | 23 | os_posix_set_docked |
| exit slot | 0 | 0 | (fnvnx_reach_violation is not reached by any test, os_exit_process neither) |
| audio_submit | 0 | 0 | the host tests have no host audio entry point: the mixer is exercised without a backend |
| gpu | 0 | 0 | no test of the four binaries calls nxd3d_gpu_call now |
| test observation (lock counting) | 485 / 89 / 5 / 5 = 584 | 3 994 005 | fnvnx_lockcount_note, fnvnx_bnd_locks_held: the harness's own checks, not a need of the model |

Sizing for the runner: every one of the 584 runs needs the lock counter (the harness), 448 need locks; 172 need threads (the heaviest
by calls); 162 need the file system; text entry (os_text_*), os_show_message and os_exit_process are used by no test in s4, s7 or m32.
