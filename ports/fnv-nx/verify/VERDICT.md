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
| 14 | Memory fault in a hand-written native body: null or wild pointer, access out of bounds, stack exhaustion | `memsafe` | a static proof per native function; the means is not chosen yet (L4 with L5, L6, L1) | L4 | required, no means yet |
| 15 | Hang or deadlock | `nodeadlock` | a static rule; none is known yet | L4 | required, no rule yet |
| 16 | A branch treated as unreachable that can be reached | `reach` | call-graph facts of the exe and the modules; one certificate per `fact_id` | L1 | required with the first root that calls `fnvnx_reach_violation` |
| 11 | Tool that is not a pure function of its declared inputs | `pure` | AST of every stage tool | L4 | runs |
| 12 | Tool defect covered by its own tests | `selftests` | every test file, in the declared environment | L4 | runs |
| 13 | Code in the trunk that no gate judges | `admit` | tracked files against `admit.json` | L4 | runs |

Gate rules decide on what the program is (machine code, symbol tables, syntax trees, content hashes). No gate decides
on a word in a name, a comment or a string.

## Proven-unreachable branches (owner decision of 2026-11-11, option iii)

A branch that the call graph proves unreachable goes to `fnvnx_reach_violation(fact_id)`, which exits through the
host exit slot with the fact code. It is an assertion of a proven build invariant, not a trap path. The rule for the
gates:

- `reach` (class 16) produces every `fact_id` from the exe and module facts, each with a certificate of
  unreachability. Certificates are valid only when every `GetProcAddress` site with a non-constant name is
  statically resolved; until then a build that relies on them is a measurement build and stays out of the trunk.
- `objstub` (class 1) accepts a call of `fnvnx_reach_violation` only if the argument is a constant at the call site
  and that constant is a `fact_id` with a certificate of the same verdict run. A call with a non-constant argument,
  an unknown `fact_id`, or a certificate without a call site (stale) is a finding. There is no hand-written list.
- `fnvnx_reach_violation` itself is the one function, besides the process-exit import, whose body may end in the
  host exit slot.

## State of the product gates

- `objstub` is written and tested (`objstub.py`, `gate_objstub.py`), and is NOT in `gates.json` yet.
  - Class 1 decides on instruction encodings and relocations. The set of terminating routines is derived by a GCC
    plugin from the declarations the compiler itself sees (8 names from the C headers of the cross toolchain).
  - Class 2 flags a function unless the analysis proves a call, an access outside its own frame, or an input
    register live at entry. Its register read and write sets come from the capstone decoder, and three encodings
    were found where those sets are wrong (`cmp`, `fmov` with an immediate, `ldadd`). A spurious read lets a
    vacuous function pass, so the gate is not sound until the sets are derived from Arm's machine-readable
    specification. Until then it is not accepted as a gate.
  - Measured cost: about 3 s per MB of code, so about 6 minutes for a 120 MB text. This is over the 5 minute
    budget and needs sharding by object with a content-keyed cache.
- `build`, `determinism`, `oracle`, `linkstrict`, `memsafe`, `nodeadlock`: not written.

## Where no closing rule is known

Reported to the orchestrator; none of these is patched around.

- **Classes 14 and 15** are required gates without a means. Until one exists no product root can be admitted.
  Sanitizers, boots and tests verify these classes; by the owner's rule a run never decides, so they are not
  accepted in place of a proof. Lifted code faults exactly where the exe faults (simulation argument of R2), so
  class 14 concerns the hand-written native code of the platform, the renderer, the loader and the lift runtime.

Accepted assumptions that cannot be removed (MASTER_PLAN4 section 4):

- **A8: Win32 and D3D9 are given by documentation, not by a formal model.** A body that is not vacuous but
  implements its contract only in part is therefore closed by conformance evidence per function. Class 10 makes
  that evidence honest; it does not make it complete.
- **Behaviour of the console** (Horizon, memory limits on hardware) stays an assumption until a run on the console.

## Assumptions

- A2: the compiler is correct. The gates judge its output, not its internals.
- The set of terminating routines for class 1 is derived from the toolchain's own headers (the `noreturn`
  attribute); a terminating routine without that attribute would be missed.
- The verdict runs gates in a clean worktree of the commit, in the environment declared by `env.json`.
