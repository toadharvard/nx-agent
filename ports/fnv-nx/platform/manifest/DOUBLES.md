# Doubles of the tests of L5: rule, census, needs

Owner order of 2026-11-11 (night): a test links the real product files of its root; it defines no symbol that a product
root defines; below the product there is one boundary (GL library, deko3d, libnx, the kernel), met in one shared model, the
L4 boot machine. `check_doubles.py` is the gate. This file is the plan of record for L5's tests (accepted by the L5 lead).

## Census (check_doubles.py through `make -o hostapi-check doubles` in tests/s4, commit 3950cd01)

Measured outside the pinned env for the m32 binaries (`libc6-dev-i386`, `g++-13-multilib` are not in verify/env.json yet).
9 binaries, 69 product units, 9 binaries with findings, exit 1.

| finding | rows | what |
|---|---|---|
| double | 596 | 6 double files, 88 symbols: platform/os/os_posix.c 67, tests/s4/host_boundary.cpp 9, platform/win32/filemap_shared.cpp 9, tests/s4/host_gpu.cpp 1, tests/s7/host_input.cpp 1, tests/s4/m32/host_input.cpp 1 |
| replaced unit | 30 | os_guest.c (7 binaries), filemap_private.cpp (6), boundary_guest.cpp (7), platform_glue.cpp (7 + probe/m32) |
| boundary double | 11 | host_boundary.cpp (fnvnx_reach_violation, 7), host_section.cpp (4 symbols), and the mtqemu files of the s15 stream |
| unlinked unit | 3 | boundary_guest.cpp, platform_glue.cpp, platform/boundary/host_hooks.cpp |
| unbuilt object | 4 | platform/os/sock_guest.c; tests/s4/sockwire/*.c (3) |

Not complete: tests/s15/mtqemu/build.sh fails (its link map is missing), sockwire_test is not built.

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

Measured per test, in the s4_tests binary (the s7 binaries are not measured): `-finstrument-functions` on the host twins of the
boundary (os_posix.c, host_boundary.cpp, host_gpu.cpp, m32/host_input.cpp), hooks.cpp counts the calls of each entry point,
one run per test (the case alone, exact name) minus a run of no test (the start-up cost), doubles_needs.py groups the entry
points by the sections of os.h (and its headers). Raw table: platform/manifest/doubles_needs.tsv (480 tests, calls per need).
Global totals, 480 of the 481 cases measured (one case cannot be named alone by the harness; 5 of the 480 are not
independent of the run order and their counts are partial: file_attributes_move_copy_dirs, file_create_dispositions_and_errors,
file_read_write_seek, procenv_current_directory exit 1 alone, vm_alloc_flags crashes alone):

| need | tests | calls | entry points used |
|---|---|---|---|
| locks (futex words, game locks) | 357 | 3 543 174 | os_lock/unlock, os_glock/gunlock/gtrylock (5) |
| threads | 136 | 14 628 305 | 15 of the os_thread_* / os_cpu_* / os_yield family |
| condition variables | 89 | 58 651 | os_cond_* (4) |
| world stop/resume | 8 | 57 197 | os_raw_pause/resume, os_thread_wslot |
| files and directories | 150 | 495 367 | 20 os_file_*, os_dir_*, os_mkdir, os_remove, os_rename, os_stat, ... |
| time (clocks, time zone) | 129 | 359 893 | os_monotonic_ns, os_sleep_ns, os_ticks, os_tick_freq, os_wall_time_us, os_tz_* (7) |
| vm (reserve/commit/protect) | 24 | 7 230 | os_vm_* (5) |
| section services | 5 | 102 | os_shm_create/free/map/unmap |
| process and host info | 34 | 3 402 | os_cpu_count, os_debug_print, os_memory_info, os_process_id, os_random32 |
| boundary of the game (loader, threads) | 148 | 33 797 | 8 of fnvnx_bnd_* |
| input poll | 3 | 821 | fnvnx_input_pump |
| gpu | 1 | 2 | nxd3d_gpu_call |
| docked mode (a model input) | 6 | 23 | os_posix_set_docked |
| exit slot | 0 | 0 | (fnvnx_reach_violation is not reached by any test, os_exit_process neither) |
| audio_submit | 0 | 0 | the host tests have no host audio entry point: the mixer is exercised without a backend |
| test observation (lock counting) | 479 | 2 556 625 | fnvnx_lockcount_note, fnvnx_bnd_locks_held: the harness's own checks, not a need of the model |

Sizing for the runner: every test needs locks and (through the harness) the lock counter; 136 need threads (the heaviest
by calls); 150 need the file system; text entry (os_text_*), os_show_message and os_exit_process are used by no test in s4.
