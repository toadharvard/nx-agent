# The verdict contract: what "green" means on the trunk

Owner order of 2026-11-11: the trunk `claude/fnv-nx-clean` is green at every commit, and green means that what the
compiler built does not fail at run time. This file states which classes of run-time failure exist, which gate
closes each one statically, and where no rule is known. `gates.json` is the list of gates that run; `admit.json` maps
every directory of the trunk to the gates that judge it.

## How the trunk stays green

- A merge moves the trunk only on a green verdict of the merge result (`l1.py merge`). A gate is green only when
  every item of its universe is certified; there is no "not worse than before" and no allow-list.
- **Admission rule (gate `admit`).** Every tracked file lies under exactly one root of `admit.json`. A root of class
  `product` (code compiled into the shipped binary) is admitted only if it names every required product gate and
  those gates exist in `gates.json`. So code cannot enter the trunk before the gates that close its failure classes.
- Code that does not pass is not in the trunk. It lives on its lead's branch and returns as a whole directory, with
  its build recipe for the declared environment (`env.json`) and green gates.
- Measurement builds (uncertified forms, for example) are made from lead branches. They never enter the trunk.

## Classes of run-time failure and their gates

Status: **runs** = in `gates.json` now; **required** = named in `REQUIRED_PRODUCT` of `gate_admit.py` or required
below, not written yet, so no product root can be admitted; **no rule** = reported to the orchestrator.

| # | Class of run-time failure | Gate | Decides on | Owner | Status |
|---|---|---|---|---|---|
| 1 | Trap or abort path: a trap instruction or a call of a terminating routine outside the one exit through the host | `objstub` (T) | machine code of every function of the built objects | L4 | required |
| 2 | No-op or fake success: a function with no store, no call and a result that depends on no input, not generated from contract data | `objstub` (V) | data flow over the machine code; the table of constant answers | L4 | required |
| 3 | Unresolved or substitutable symbol: undefined, weak undefined, two contract names on one body | `linkstrict` | the linked ELF's symbol table and the link command | L1 | required |
| 4 | Import without a body | `imports` | manifest of reachable imports against defined symbols | L5 | required with the platform root |
| 5 | Code address without a dispatcher entry | `disp` | dispatcher table against the R3 universe | L1 | required with the lift root |
| 6 | x86 form without a certificate | `forms` | form universe against published proofs of the current DSL hash | L3b | required with the lift root |
| 7 | D3D9 enum value or SM1-3 op without a translation | `d3d9total` | generated tables against the enum and op universes | L6 | required with the render root |
| 8 | Layout or calling-contract mismatch of a native body | `n1`, `n2_data`, `n2_call`, `n2_vcall`, `xc`, `xunit`, `rtti`, `pcrel`, `bodysel` | exe facts against object facts | L1 | required with the first native root |
| 9 | Non-deterministic build | `determinism` | two cold builds of every recipe in the sandbox, compared bit for bit | L4 | required |
| 10 | Test that can pass on a wrong body: the expected value comes from the code under test | `oracle` | provenance of every expected value (exe, Wine dump, contract, independent model) | L4 | required |
| 11 | Tool that is not a pure function of its declared inputs | `pure` | AST of every stage tool | L4 | runs |
| 12 | Tool defect covered by its own tests | `selftests` | every test file, in the declared environment | L4 | runs |
| 13 | Code in the trunk that no gate judges | `admit` | tracked files against `admit.json` | L4 | runs |

Gate rules decide on what the program is (machine code, symbol tables, syntax trees, content hashes). No gate decides
on a word in a name, a comment or a string.

## Where no closing rule is known

Reported to the orchestrator; none of these is patched around.

- **Memory faults in hand-written native code** (null or wild pointer, overflow, misalignment, stack exhaustion).
  Lifted code faults exactly where the exe faults (simulation argument of R2). For native C and C++ of the platform,
  the renderer and the loader no static proof of memory safety is known here.
- **A body that is not vacuous but implements its contract only in part.** Class 2 closes the bodies that do nothing.
  A body that does something wrong is closed only by conformance evidence per function (class 10 makes that
  evidence honest; it does not make it complete). Win32 and D3D9 have no formal model (assumption A8).
- **Hangs and deadlocks.** No static rule; the boot stage reports them, it does not decide.
- **Failures of the console itself** (Horizon behaviour, memory limits on hardware) until a run on the console.

## Assumptions

- A2: the compiler is correct. The gates judge its output, not its internals.
- The set of terminating routines for class 1 is derived from the toolchain's own headers (the `noreturn`
  attribute); a terminating routine without that attribute would be missed.
- The verdict runs gates in a clean worktree of the commit, in the environment declared by `env.json`.
