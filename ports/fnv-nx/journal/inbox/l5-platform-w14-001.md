## L5 PLATFORM w14 — 001 (2026-11-11): global census of test doubles (no fix yet)

Re-runnable: `make -C tests/s4 doubles S4LOG=<s4 log> S7LOG=<s7 log>` (not in the default target) runs
`platform/manifest/check_doubles.py` on the link maps of all test binaries; exit 1 while any count is nonzero.
Measured at the commit that carries this note (branch `claude/fnv-l5-platform-w14`), base 8f72beff. Baseline: s4 498 tests, s7 84 tests, 0 failed.

Product = the unit set of `platform/build_guest.sh` (read from the script: 73 units = depth-1 of win32,crt,audio,input,boundary,com,os minus HOST_SIDE, clock_rt/real/virtual, os_guest.c, platform_glue.cpp).
Definition = GLOBAL/WEAK/UNIQUE symbol of default visibility (`readelf -sW`; hidden `__x86.get_pc_thunk.*` excluded). C++ symbols compare by demangled name without
parameter list (LP64 host and ILP32 guest mangle `uint64_t` differently: `PKm` vs `PKy`). A double is a strong definition in a test file of a symbol a product unit defines (strong or weak);
weak/weak overlap (655 symbol instances: inline/template) is not a replacement and is only printed. Guest objects of all 73 units are compiled with aarch64 g++ 13.3 `-mabi=ilp32` (`guest-objs`).

### Numbers (global)
| | now | predecessor eb7bcf0b |
|---|---|---|
| test binaries | 9 (s4_tests, _vclock, _private, probe_dynsites, s4_m32, s7_tests, s7_tests_vclock, sockwire_test, mtqemu_guest) | (s4+s7 counted as one) |
| binaries with findings | 9 of 9 | all |
| tests in binaries with findings | 582 of 582 (s4 498 + s7 84) | 582/582 |
| double files / symbols | 6 / 88: os_posix.c 67, filemap_shared.cpp 9, tests/s4/host_boundary.cpp 9, host_gpu.cpp 1, tests/s4/m32/host_input.cpp 1, tests/s7/host_input.cpp 1 | 6 / "87" (its own list sums to 88) |
| replaced units | 4: os_guest.c, platform_glue.cpp, boundary_guest.cpp, filemap_private.cpp | — |
| unlinked units (linked in no test binary) | 3: boundary_guest.cpp, host_hooks.cpp, platform_glue.cpp | 7 |
| boundary-double files | 4: tests/s4/host_section.cpp, tests/s15/mtqemu/{mlibc.c,gstart.c}, toolchain/guest/threads.c | 7 |
| unbuilt (census incomplete) | 4: product unit sock_guest.c and the 3 test files of sockwire_test (need the guest toolchain's newlib/libnx headers; absent here) | — |

Differences, explained:
- unlinked 7 → 3: the predecessor counted per s4_tests only. Now: filemap_private.cpp is linked by s4_tests_private, combridge_exports.gen.cpp by s7/m32, os_guest.c by mtqemu_guest, sock_guest.c by sockwire_test (still unbuilt here). The 3 left are linked nowhere (`host_hooks.cpp` and `boundary_guest.cpp`: the guest side of the boundary; `platform_glue.cpp`: the product's glue).
- 87 → 88 symbols: same 6 files with the same per-file counts; the predecessor's total is an addition slip (67+9+9+1+1+1 = 88).
- boundary doubles 7 → 4: kdouble.h is a header (no object), sockwire guest_shim.c/host_glue.c are unbuilt here (found as "unbuilt", not as doubles), `linhost.c` is a separate host program (not in a guest link map), `hb.c` (TSan happens-before runtime) defines nothing the product references. New: `tests/s15/mtqemu/gstart.c` (fnvnx_host), `toolchain/guest/threads.c` — the latter is the guest runtime (real product of toolchain/guest/build.sh, outside build_guest.sh's list): classification artefact, belongs to the product set once the recipe has it.
- `render/nine/d3dx/*` objects in s4_m32 are L6's product, not test files, and are skipped. `toolchain/game/null_d3d9.cpp` (the "null device") defines no L5 product symbol; as a path it is the D3D device double of L6, not counted here.
- Pre-existing break fixed on the way (one line): `platform/clock/clock_virtual.cpp` lacked `#include "../os/os_clock.h"`, so `make vclock`, the vclock binaries and FNVNX_CLOCK=virtual did not compile.
- mtqemu's `build.sh` link of guest.elf does not complete at base (os_world_register/unregister, ExitProcess undefined); its objects exist, the script now writes `linkmap.tsv` before the link.
- Environment note: `gcc-multilib` and `g++-aarch64-linux-gnu` conflict in apt on this image (installing one removes the other); build the m32 objects first, then install the cross compiler.

### What each double needs instead
| file | replaces | needs |
|---|---|---|
| platform/os/os_posix.c (67) | os_guest.c (os_* services over the host API) | (a) the real os_guest.c linked: needs the host API (`fnvnx/hostapi.h` ext_call) behind it → (b) L4 model of the kernel/libnx boundary executing hostapi services 1..N (threads, memory, time, sockets); os_guest.c is ILP32 and ext_call-based, no host compile today |
| platform/win32/filemap_shared.cpp (9) | filemap_private.cpp (fm_* backend) | (a) real filemap_private.cpp linked now: it compiles on the host and is linked in s4_tests_private; what it calls (`fnvnx_section_*`) is host_section.cpp → (b) L4 model of the section/svcMapMemory services; then filemap_shared goes |
| tests/s4/host_boundary.cpp (9 + 1) | boundary_guest.cpp (`fnvnx_bnd_*`) and what lies below (module load, guest invoke, thread create) | (b) L4 boot machine's loader/guest-call model; boundary_guest.cpp is a guest-side file |
| tests/s4/host_gpu.cpp (1) | platform_glue.cpp `nxd3d_gpu_call` | (b) L4 model of the deko3d/GPU command boundary (nxgpu ABI) |
| tests/s4/m32/host_input.cpp, tests/s7/host_input.cpp (1 each) | platform_glue.cpp `fnvnx_input_pump` (polled HID) | (b) L4 model of libnx hid (padGetState etc.); the real input_horizon.cpp is libnx-only |
| tests/s4/host_section.cpp (boundary) | libnx/kernel section services (`fnvnx_section_create/map/unmap/free`) | (b) L4 kernel model (svcMapMemory family) |
| tests/s15/mtqemu/mlibc.c, gstart.c | guest libc / start code (calloc, exit, __cxa_*, fnvnx_host) | (b) fnvnx-a64's real newlib + crt0 and the L4 host; mtqemu is a qemu-user prototype |
| toolchain/guest/threads.c (13) | not a double: the guest runtime of the product; once it is in the recipe's unit set it is a product unit and the finding goes | (a) add to the product set |
| tests/s4/sockwire/guest_shim.c, host_glue.c | libsysbase fd table + errno, ext_call dispatch (not measured here: unbuilt) | (b) L4 kernel/libsysbase model; sock_guest.c is the product unit |
| tests/s4/backends/kdouble.h + host_*_test.c | libnx calls (header-level stub; no object, not measured) | (b) L4 libnx model; a header scan is not in this census |

Not measured here (needs the guest toolchain): sockwire_test objects, and the symbol set of sock_guest.c.
