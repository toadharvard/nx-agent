# l1-contract-021: hostapi.h holds only what the product calls

Branch claude/fnv-l1-contract-w12 (from c9765781). Facts measured on claude/fnv-l5-platform @ 2951f7ae, whole platform tree.
Call sites: SHUTDOWN(27) 0, GETSOCKNAME(28) 0, GETPEERNAME(29) 0, GAI_OPEN(32) 0, GAI_FETCH(33) 0. All other services have at least one
reachable call site, except the zero-use slots console_flush, main_loop, buttons_down, tz_offset_minutes and ACCESS_FAULT_HANDLER/UNMAP_FIXED
(reported, not removed: other leads own them).

Done: the five services are removed from verify/tests/guest/hostapi.h (numbers 27, 28, 29, 32, 33 stay unassigned, answer NO_SERVICE).
FNVNX_EXT_DK = 12 added (machine model of deko3d, host-test ELFs only; no FNVNX_PLAT_ number).
Gate `hostapi` (gate_hostapi.py, gates.json, gate_admit.REQUIRED_PRODUCT): declared = used per admitted root; PP stage now runs -dU.
Universe 0 until a root exists: green, no exception list. Limit: uses inside unreachable functions count (reach/reachlink own that).

L4 must delete: bootsock.py SHUTDOWN/GETSOCKNAME/GETPEERNAME/GAI model, test_bootsock.py cases for them, TOOLS.md bootsock section for them,
the dead NotDetermined path in bootmachine.py. I changed only SOCK_SERVICES and the test_bootmachine.py cases that kept the trunk green.
L5 must delete the sock_guest.c wrappers of the five, sync toolchain/include/fnvnx/hostapi.h, host dispatch range 16..31.

## Report
sha256 hostapi.h (verify/tests/guest/hostapi.h): 2e7295fcd8b90f6f32cc63be46f96c1a7e50bbacc4b5e290ef324e0398713f97
Service -> call sites (platform @ 2951f7ae): 27 SHUTDOWN 0, 28 GETSOCKNAME 0, 29 GETPEERNAME 0, 32 GAI_OPEN 0, 33 GAI_FETCH 0 (all removed); every other FNVNX_PLAT_* service >= 1.
FNVNX_EXT_DK = 12.
Gate numbers: hostapi 0/0 green (universe 0); admit green 404/404; pure, oracle green; test_gate_hostapi, test_admit, test_bootmachine (147), test_bootsock (7), test_unitstage pass.
Selftests gate: 44 of 48 before a last fix; test_gates pinned the gate list (fixed: hostapi added), test_objstub passes on rerun, test_envcheck and the rest of test_gates and test_recipe fail on "blocked: env" (tree /usr/include differs, expected here).
Not finished: that diagnosis; dead-function limit of the evidence; zero-use slots console_flush, main_loop, buttons_down, tz_offset_minutes,
ACCESS_FAULT_HANDLER, UNMAP_FIXED are left to their leads.

## Finish (w12, merged with trunk 41c9eca, which contains 58a79b3e)
Merge conflicts resolved: gates.json (argordergate, hostapi), test_gates (gate list), TOOLS.md (both sections), test_bootmachine (trunk's fixed-UTC tz values; GAI/fetch cases stay removed). CLAUDE.md is the trunk's.
Env: setup_verdict_env.sh then envcheck: OK (the first run was a partial install; two packages were missing until the script ran to its end).
Selftest diagnosis (gate_tests, 3 runs, same env, whole set):
 (a) trunk 41c9eca: 49 of 51 pass. (b) branch 13e876d: 50 of 52. (c) branch with -dU removed from PP_TAIL: 50 of 52.
 test_gates, test_objstub, test_recipe pass in all three: the red of my earlier note was the broken env ("blocked: env"), not -dU. The 2 failing files are the same on trunk:
 test_argorder (toy root build red) and test_guest (external gcc-msvcmodel not fetchable: needs the private fnv-nx-build repo). Not caused by this work.
 No change to the rule or to the PP command was needed. Tests added in test_gate_hostapi.py (25 checks pass):
 - a unit.ii with -dU output compiles in the CC stage (-fpreprocessed, pinned cross g++-13) to the same object bytes as the plain unit.ii;
 - (i) FACT: a header whose static inline wrapper expands a FNVNX_PLAT_ macro inside the header makes every includer "use" that service: the rule is vacuous for such a header. The real hostapi.h has no such wrapper (fnvnx_hcallN take a number argument), so it is not hit today; a rule that closes it needs call-level evidence (reach/reachlink), reported to the lead, no list added;
 - (ii) SOCK_FIRST=16 aliases SOCK_SOCKET, SOCK_LAST=31 aliases SOCK_SETFL; a loop FIRST..LAST certifies only 16 and 31, the services 17..26 and 30 stay unused; a marker with a changed value is red; a marker equal to no service is its own item.
## Final numbers
Full verdict (l1.py, commit ce44ef6; the verdict line names 3ef9a30): 23 of 24 gates green, 184.5 s, over_budget False. Red: selftests 50/52 (test_argorder, test_guest, both need fnv-nx-build / msvcmodel; same on trunk). hostapi 0/0, admit 427/427, pure 35/35, oracle 52/52.
sha256 hostapi.h (verify/tests/guest/hostapi.h): 2e7295fcd8b90f6f32cc63be46f96c1a7e50bbacc4b5e290ef324e0398713f97

## Slots with no product use (lead order after the merge at L5 3bf9c4d6)
Rule applied: a slot exists iff a product unit's expansion uses it; tests do not count. Facts measured on origin/claude/fnv-l5-platform (product = everything outside tests/, verify/, docs/, journal/):
 console_flush, main_loop, buttons_down: only render/nine/test/nine_test.cpp (a test) and test doubles; tz_offset_minutes: no product unit reads the slot (platform/os/os_*.c define their own os_tz_offset_minutes and call TZ_AT); ACCESS_FAULT_HANDLER 10: no product unit (only the table and tests); UNMAP_FIXED 15: no product unit calls it (loader's fnvnx_os.unmap_fixed is a hook interface with no product implementation in the tree).
Deleted from hostapi.h: struct slots console_flush, main_loop, buttons_down, tz_offset_minutes (sizeof(FnvnxHostApi) 0x1C0 -> 0x1A0, all later offsets move up; version field unchanged), services 10 and 15 (reserved holes, not renumbered, answer NO_SERVICE), FnvnxFaultFrame, their table rows. TEXT_ENTRY 36 stays.
RED, not fixed here (L4/L5 own it): verify/bootmachine.py (SLOTS, fn 15 row, _plat_unmap_fixed, ACCESS_FAULT_HANDLER delivery), verify/boothost.py (h_console_flush, h_main_loop, h_buttons_down, h_tz_offset_minutes), verify/tests/test_bootmachine.py (guest source uses FnvnxFaultFrame, FNVNX_PLAT_UNMAP_FIXED, H->tz_offset_minutes; modes 12..22, 37, 38, 44, 45 and the totality cases) no longer compile against the header; TOOLS.md/TOOLCHAIN.md text. L5: toolchain/host (host.c, host_v2.inc, host_fault.inc, host_vm.inc h_unmap_fixed, host_platform.inc rows 10 and 15), toolchain/include/fnvnx/hostapi.h sync, tests/s4 host_total/host_prot/fault tests, tests/s5 emu_guest.py SLOTS, tests/s15 mtqemu linhost.c F(...) lines.
sha256 hostapi.h after this change: 33d8afeebce7c82e2c0c0f1fd687b173af7e4ef5f06a4b9cc8421f3975356548

## Phase 3 (w12, head f3fa1fc + inbox commit)
Lead correction applied: SOCK services are deleted only if unused. Measured on claude/fnv-l5-platform @ 041f62a (platform/os/sock_guest.c, token count; no product root exists yet, so -dU is not applicable): all 13 (16..26, 30, 31) are used. They stay. Deleted: SOCK_FIRST, SOCK_LAST (markers aliasing services).
Slots deleted (numbers kept as holes): console_flush, tz_offset_minutes, ACCESS_FAULT_HANDLER 10, main_loop/buttons_down, UNMAP_FIXED 15. Header struct 0x1A0, 46 slots, 29 services.
Rule (i) in gate_hostapi.py: a FNVNX_PLAT_ identifier outside a `#define FNVNX_PLAT_X <integer>` directive in hostapi.h is red (wrapper use). Limit: only hostapi.h text is checked, not other included headers.
Selftests gate: 40 of 40 green. sha256 hostapi.h (verify/tests/guest/hostapi.h): f44636586dc74ce10bb899f607f2275bd06079e92b5613c701ba8f8d4bd5e778
