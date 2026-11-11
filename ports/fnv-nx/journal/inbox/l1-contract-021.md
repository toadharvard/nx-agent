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
