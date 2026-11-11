# Orchestrator: every run-time failure class, its gate, where the gate is now (2026-11-11 21:50)

Trunk claude/fnv-nx-clean gates.json runs 7: pure, selftests, admit, build, objstub, linkstrict, determinism.

## Classes in VERDICT.md

| class | gate | where it is | on trunk gates.json | owner -> wires |
|---|---|---|---|---|
| 1 trap/abort | objstub T | trunk verify/gate_objstub.py | yes | - |
| 2 no-op / fake success (+ Wine citation rows) | objstub V | trunk | yes | - |
| 3 unresolved symbol | linkstrict | trunk | yes | - |
| 4 import without body | imports | L5 platform/manifest/gate_imports.py | NO | L5 -> L1 |
| 5 code address without dispatcher | disp | L1 link/gate_disp.py | NO | L1 |
| 6 form without certificate | forms | L3a lift/tools/gate_forms.py AND L3b lift/proof/formgate.py (two copies) | NO | L3a+L3b pick one -> L1 |
| 7 D3D9 enum / SM op | d3d9total | L6 render/total/gate_d3d9total.py | NO | L6 -> L1 |
| 8 layout / calling contract | n1, n2_data, n2_call, n2_vcall, xc, xunit, rtti, pcrel, bodysel | trunk contract/*.py (files present) | NO | L1 |
| 8 | abs | L1 link/gate_abs.py | NO | L1 |
| 8 | R7 layout predicate | L2 tools/equiv/r7.py (not in VERDICT) | NO | L2 -> L1 |
| 8 | MSVC names, arg order | L2 tools/link/namegate.py, argordergate.py (not in VERDICT) | NO | L2 -> L1 |
| 8 | ABI model: abi, thisadj, vtables, vslots, exevft, odr | tools/link on L2/L3/L5/L6, not trunk | NO | L2 -> L1 |
| 9 determinism | determinism | trunk | yes | - |
| 10 test without independent reference | oracle | nowhere | NO | L4 writes |
| 11-13 pure, selftests, admit | | trunk | yes | - |
| 14-15 memory fault, hang (verification) | ASan/UBSan runs | farm runner.sh missing on fnv-nx-build | n/a | L4 |
| 16 reachable "unreachable" branch | reach | L1 contract/gate_reach.py | NO | L1 |
| 16 precondition: every dynamic-name GetProcAddress resolved | gpa_names | L1 contract/gpa_names.py (not in VERDICT) | NO | L1 |
| 17 oom | oom | after first frame | - | - |

## Classes from owner orders that VERDICT.md does not list at all

| class (owner order) | pieces that exist | gate missing | owner |
|---|---|---|---|
| A wrong data model: guest code not built by msvcmodel GCC with all required plugins | msvcmodel (old branch toolchain/), w4 plugin stages | admit/build reject a product root whose compiler is not the msvcmodel external or that lacks fnvnx_order, fnvnx_ftoi, addr, dispatch, names, sdd, pi | L1 |
| B memory ordering (threads, R9: TSO, /volatile:ms) | fnvnx_order plugin; tools/equiv/order_check.py; tools/perf/tso_scan.py; lifter R9 | object-level check: every guest access is ldar/stlr per R9 map, native and lifted | L2 + L3a -> L1 |
| C exact FP by rule: _ftol/fistp, NaN compares, rcp/rsqrt table, cpuid answer | fnvnx_ftoi; lift/rt/rcptab.c, rcpgen (L3a/L3b) | no FP int conversion outside the _ftol rule; rcp table hash = pinned farm table | L1 + L3a |
| D C++ EH and SEH through lifted frames, proven against exe EH tables | lift/tools/ehtables.py, lift/rt/seh.c, bench/ehtest.py (L3a/L3b) | every exe EH table entry covered by a lifted landing/handler | L3a/L3b -> L1 |
| E exe identity | tools/check_exe.py (fingerprint part) | exefacts refuses another exe | L1 |
| F 32-bit guest address (ILP32) | addr plugin (L2); lift/test/addrwrap.py (L3a) | no integer-to-pointer outside the 32-bit rule, native and lifted | L2 + L3a -> L1 |
| G declared machine: registry keys from call sites, no network, fixed TZ | trunk contract/registry.py; VERDICT section | registry hive = exactly keys found at call sites (check is it a gate) | L1 + L5 |

## DROP still on branches
layoutgate.py + tests/s1/test_layoutgate.py (clang stand-in, on 8 branches), tools/compare/drawgate.py and render/nine/test/gate.py if they decide from results; objcache.py/linkcache.sh.
