# l5-platform-w15-001: L1's hostapi.h taken; removed socket services deleted

Branch claude/fnv-l5-platform-w15. Code commit 96c0c61a (this note is the next commit).

## Header
toolchain/include/fnvnx/hostapi.h = verify/tests/guest/hostapi.h of origin/claude/fnv-l1-contract-w12 @ 6766d18a, byte for byte.
sha256 2e7295fcd8b90f6f32cc63be46f96c1a7e50bbacc4b5e290ef324e0398713f97.
It carries THREAD_SELF = 35 and TEXT_ENTRY = 36; no "L5 ADDITIONS" block remains (the "L5 ADDITION" markers in host_platform.inc are removed; one comment is left in host_v2.inc:132).

## Wrappers removed (platform/os/sock_guest.c)
shutdown (27), getsockname + getpeername (28/29, helper name_of), getaddrinfo (32/33), freeaddrinfo (its pair, no service of its own);
the AI/EAI/Addrinfo wire definitions only they used are gone from sockwire.h. grep for SOCK_SHUTDOWN|GETSOCKNAME|GETPEERNAME|GAI_OPEN|GAI_FETCH outside hostapi.h: 0.

## Dispatch sites changed
- toolchain/host/host_sock.inc: new host_sock_nargs(fn) (switch over the 13 declared SOCK_* names, -1 otherwise); host_sock_call answers FNVNX_EXT_NO_SERVICE for -1; GAI tickets, SHUTDOWN, GETSOCKNAME/GETPEERNAME code deleted.
- toolchain/host/host_platform.inc: platform_ext routes by host_sock_nargs(fn) >= 0, not by a range; everything else = g_plat table default, NO_SERVICE.
- tests: sockwire_test.c / host_glue.c / Makefile (no getsockname: ports come from the host libc; raw 27..29,32,33 -> NO_SERVICE), host_total_test.c (declared list, socket loop).
- os_horizon.c, os_posix.c, boundary/ serve no host service numbers (only call sites): nothing to change.
- platform/manifest/check_hostapi.py check D: served set (g_plat rows + host_sock_nargs cases) == declared set with the header's counts; a range dispatch (SOCK_FIRST/LAST) in the host files fails.

## check_hostapi.py now FAILS with 2 violations (real, not hidden)
- TEXT_ENTRY (36, n=5): the platform CALLS it (toolchain/guest/os_guest.c os_text_entry, platform/os/os.h; contract docs/AUDIO_INPUT.md "Text entry") but no host serves it: host_textentry.inc, which the header cites, does not exist in the tree. No server invented. Needs the swkbd host server (L5 work item) .
- ACCESS_FAULT_HANDLER (10, n=2): no platform call site, no server (host_fault.inc does not exist); only tests/s15/mtqemu (L4) installs its own handler. L1 must mark it NOT SERVED or L4 serves it.

## The six services of zero use
| service | platform declares/serves/calls? | case |
|---|---|---|
| console_flush | slot filled in toolchain/host/host.c (h_console_flush); no call site anywhere | (c) platform-owned, unused. NOT removed: removing a FnvnxHostApi slot is an ABI change of L1's header and L4's slot list (tests/s5/emu_guest.py SLOTS, tests/s15/mtqemu/linhost.c). Request to L1. |
| main_loop | host.c h_main_loop; called by render/nine/test/nine_test.cpp:681 | (b) owner L6 (render/nine) |
| buttons_down | host.c h_buttons_down; called by nine_test.cpp:682 | (b) owner L6 |
| tz_offset_minutes | host_v2.inc h_tz_offset_minutes; no call site; superseded by TZ_AT (34), which the platform uses | (c) platform-owned, unused; same ABI request as console_flush |
| ACCESS_FAULT_HANDLER | no declaration use, no server, no call (see above) | (b) owner L4 (fault delivery) / L1 |
| UNMAP_FIXED (15) | served by the platform (pf_unmap_fixed -> h_unmap_fixed, host_vm.inc); no guest call site in platform/ or toolchain/guest; its pair map_fixed is called by toolchain/game/boot.cpp | (b) loader/boot owner (undo of map_fixed); not platform-called |

## Results (build machine: restore without toolchain build; pip capstone, pefile)
- s7: 84 tests, 3239 checks, 0 failures.
- s4: 498 tests, 406195 checks, 0 failed, run with `make -o hostapi-check test`, because hostapi-check fails on the 2 violations above (it is a prerequisite of `test`).
- sockwire test (needs the newlib toolchain, not built here): not run.
