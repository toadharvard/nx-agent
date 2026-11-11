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
