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
| 5 | Code address without a dispatcher entry | `disp` (`link/gate_disp.py`; also `abs`, `link/gate_abs.py`) | dispatcher table against the R3 universe | L1 | wired, red until its inputs are declared |
| 6 | x86 form without a certificate | `forms` | form universe against published proofs of the current DSL hash | L3b | required with the lift root |
| 7 | D3D9 enum value or SM1-3 op without a translation | `d3d9total` | generated tables against the enum and op universes | L6 | required with the render root |
| 8 | Layout or calling-contract mismatch of a native body | `n1`, `n2_data`, `n2_call`, `n2_vcall`, `xc`, `xunit`, `rtti`, `pcrel`, `bodysel` (`gate_contract.py`); R7 (`tools/equiv/r7.py`, L2) enters with L2's merge | exe facts against object facts | L1 | wired in `gates.json`; red until `contract_inputs.json` declares the exe-derived externals |
| 9 | Non-deterministic build | `determinism` | two cold builds of every recipe in the sandbox, compared bit for bit | L4 | required |
| 10 | Test that can pass on a wrong body: the expected value comes from the code under test | `oracle` | the declared reference of every test file (`tests/oracle.json`): a dump of the exe under Wine 9.0, a cited text, a contract table or an independent model, outside the import closure of the code under test | L4 | runs |
| 14 | Memory fault in a hand-written native body: null or wild pointer, access out of bounds, stack exhaustion | none (verification) | AddressSanitizer and UndefinedBehaviorSanitizer in the stage runs and tests; the sound analyzer for information | L4 | covered by verification, not a required gate |
| 15 | Hang or deadlock | none (verification) | the same stage runs and tests (AddressSanitizer and UndefinedBehaviorSanitizer; the sound analyzer for information) | L4 | covered by verification, not a required gate |
| 17 | Memory exhaustion: an allocation path without a contract result | `oom` | not chosen yet | the owners of the code | after the first frame |
| 16 | A branch treated as unreachable that can be reached | `reach`, `reachlink` (`contract/gate_reach.py`; writes `GATE_CACHE/reach/<tree>/reach.facts.tsv` for `objstub`) | call-graph facts of the exe and the modules; one certificate per `fact_id`; the references in the linked objects | L1 | wired, red until its inputs are declared |
| 16 | A dynamic-name GetProcAddress site not statically resolved (the certificates are void) | `gpa` (`contract/gpa_names.py`) | argument flow into every GetProcAddress site | L1 | wired, red until its inputs are declared |
| A | Data model: the product root is not built by the game's compiler (msvcmodel external, ILP32) with every plugin | `datamodel` | the root's compiler is the `msvcmodel` external, pinned by commit and `tree_sha256`; `fnvnx_order`, `fnvnx_ftoi`, `addr`, `dispatch`, `names`, `sdd`, `pi` are loaded declared inputs | L1 | required; the external and plugins are implemented in `recipe.py` and `admit.json` on `claude/fnv-l1-contract-w4` |
| B | Memory ordering (R9): x86 TSO or MSVC volatile compiled without acquire/release | `order` | native and decomp C++ through `fnvnx_order`; lifted C++ against the memory-ordering the lifter emits | L2, L3a | required |
| C | Floating point not exact where the rule demands it: `_ftol`/`fistp`, NaN comparisons, the rcp/rsqrt table, cpuid | `fpexact` | every float-to-int site through `fnvnx_ftoi`; the table against the pinned reference CPU | L1, L3a | required |
| D | C++ EH or SEH through lifted frames that does not match the exe's EH tables | `eh` | lifted frames against the exe's own EH tables | L3a, L3b | required |
| E | The exe that was analysed is not the exe that is declared | `exepin` | exe hash against the declared identity (`contract/exepin.py` on the w8 branch) | L1 | required |
| F | A guest address that does not fit the 32-bit guest address space | `addr32` | every address constant and pointer width of a product root | L2, L3a | required |
| G | Registry key read by the exe or a module with no value in the declared machine, or a value with no reading site | `machine` | the hive against the registry call sites found statically | L1, L5 | required |
| 11 | Tool that is not a pure function of its declared inputs | `pure` | AST of every stage tool | L4 | runs |
| 12 | Tool defect covered by its own tests | `selftests` | every test file, in the declared environment | L4 | runs |
| 13 | Code in the trunk that no gate judges | `admit` | tracked files against `admit.json` | L4 | runs |

Gate rules decide on what the program is (machine code, symbol tables, syntax trees, content hashes). No gate decides
on a word in a name, a comment or a string.

## Bodies that do what Wine 9.0's simplified implementation does (owner decision)

Where Wine 9.0 implements an API in a simplified way (it does nothing, or returns a fixed value), the port may do exactly what
Wine 9.0 does, with one log line at the call. The source of the behaviour is Wine 9.0's code for that function. The rule for the gates:

- The log function is `fnvnx_wine_fixme(id)` (C linkage, defined by the runtime's owner; it writes one line and returns).
- `objstub` accepts such a body only if `verify/wine_fixme.tsv` has the row of the `id` it passes, the function passes exactly one
  `id` on every path, the `id` is a constant at the call, the body is otherwise vacuous (class 2), and its result equals the row's kind
  and value. Each row names the contract method and its Wine 9.0 reference `wine-9.0:<path>:<function>`: data, not a list of places in
  code. A body that also does real work is `wine-fixme-in-real-body`; a wrong or unknown `id`, a non-constant `id` and a taken address
  are findings.
- Result kinds: `void`, `u32`, `u64`, `addr` (the address of an ELF symbol, as in the constants table) and `u32nz<N>` (the result depends only on whether the pointer argument x<N> is zero, `this`
  being 0; value `<result when zero>,<result otherwise>`). The first rows are the two real ones from the Wine 9.0 source,
  `SetClipStatus` and `GetClipStatus` (`0x8876086C,0`). `ProcessVertices` is implemented for real in Wine;
  only its branch FIXMEs are under kind `branch`.
- Kind `branch` (value `-`): a FIXME on one branch inside a real implementation, as Wine 9.0 has. The `id` is a constant naming
  a row; the function must not be vacuous (class 2, call transparent; else `wine-fixme-branch-in-vacuous-body`); several `branch`
  ids in one function are fine; `branch` with another kind in one function is `wine-fixme-branch-mixed`; the result is
  unconstrained. It proves the log exists only in real code; that the branch behaves as Wine's is conformance evidence (class 10).
- The gate checks the form of the reference only. Checking it against a pinned Wine 9.0 source tree is the oracle class (10), not done.
- The gate result reports the call sites per `id` (`wine_fixme`), so growth is visible. The renderer's owner fills the
  table from the Wine 9.0 source.

## Declared-machine bodies (owner decision of 2026-11-11, CLAUDE.md "The declared machine")

An environment query answered from the declared machine is a complete implementation, not a stub: no network (every network call returns
the documented error for that state), a fixed time zone, registry values from static reads (an undeclared key is absent). The rule is the
Wine rule with a second citation function, one rule for every function, no list of functions:

- A body calls `fnvnx_declared_machine(id)` (C linkage, writes nothing, returns) with a constant `id`; `verify/declared_machine.tsv` has the row
  `id<TAB>contract_name<TAB>fact<TAB>source<TAB>kind<TAB>value`. `fact` is `no-network`, `time-zone` or `registry`; `source` is the documented
  origin of the value, `win-doc:<documentation page or header path>:<function>` or `wine-9.0:<path>:<function>`; `kind`/`value` as for Wine rows.
- `objstub` accepts the body iff it is otherwise vacuous, passes exactly one id of one citation function on every path, and its result equals the
  row. Findings: `declared-machine-{unknown-id,mismatch,nonconstant,in-real-body,address-taken}`. A bare constant body without the citation stays V.
- Limit (stated): the gate cannot tell which return value of a function is success, so it cannot reject a row that says success for a network
  call; it rejects a body whose result differs from its row. Row against documentation or Wine source is the oracle class (10), not done.
  A body that calls something or stores (SetLastError, a filled TIME_ZONE_INFORMATION) is not V and needs no citation.

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

Run now (in `gates.json`, after `admit`, in this order). With no product root in `admit.json` each is green with universe 0, which is what
every merge pays today; with a product root each judges it (tested on a toy root, `tests/test_recipe.py` fixtures). Roots always come from
`admit.json`, never from gate arguments. Shared state goes through `GATE_CACHE` (`gate_build.ensure`: stage store, `objects/<tree>/<root>`).

- `build` (all roots build in the sandbox), `determinism` (two cold builds and the warm one equal bit for bit): class 9 and the precondition
  of the others.
- `objstub` (classes 1, 2 and the reach rule of 16): `gate_objstub.py`, rules in `objstub.py`.
  - Terminator set derived from the recipe's compiler by a GCC plugin run over the preprocessed text of every unit (exactly the
    declarations the compiler sees, headers included), cached per unit; assumption: a terminating routine is declared noreturn.
    A terminator ends the process. Unwinding is not: the EH-resume builtins of GCC's own EH lowering (`_Unwind_Resume`) and, in C++, a
    noreturn function that is not nothrow (`__cxa_throw`) are unwinders, so a landing pad or a `throw` is no class 1 finding
    (rule and limits: `objstub.py`). Objects are ELF32 (ILP32) or ELF64; both ABIs are tested.
  - Constants table keyed (unit, symbol), kinds void/u32/u64/addr and `exit` (the owner's process-exit imports). A call of
    `fnvnx_reach_violation` is accepted only with a constant argument that is a fact of `GATE_CACHE/reach/<tree>/reach.facts.tsv`; until the
    gate `reach` writes that file every such call is `reach-uncertified`. An indirect call ending a function is `indirect-noreturn-tail`
    unless `fnvnx_reach_violation` or an `exit` row, and then on every path.
  - Class 2 flags a function unless the analysis proves a call, an access outside its own frame, or an input register live at entry.
    Its register read and write sets come from LLVM 18's instruction descriptions (`a64access.cc`, built against the LLVM libraries);
    nothing rests on the capstone decoder. Remaining assumption: LLVM's descriptions are correct, and the memory operand fields
    (base, index, displacement, size) are derived by the encoding rules listed in `a64access.cc`.
  - Cost: per object cache by content; uncached objects in parallel processes (`--jobs`, default all cores). Measured numbers are in the
    report of the change that introduced the gate (30 MB of code in 64 objects: see `gate_objstub.py` history); a warm verdict re-reads only
    object hashes.
- `linkstrict` (class 3): `gate_linkstrict.py`, on symbol tables only: no undefined or weak undefined symbol in a linked ELF; across all
  product roots every undefined reference has exactly one strong definition (weak-only, duplicate strong, common and IFUNC are findings);
  contract names (recipe key `contract_names`) are defined and no two with different body ids share an address. C++ vague linkage: a weak
  definition in a COMDAT group is a definition when all copies of the group (same signature, across the objects of all roots) are
  identical after normalising relocations; differing copies are `comdat-differs` (an ODR violation). A symbol the link script defines
  is a definition when the root links an ELF and the ELF defines it GLOBAL. Both ABIs are tested.

Required by `gate_admit.REQUIRED_PRODUCT`, so no product root is admitted without a result of each: `oracle` (10, L4, wired) and the gates written on
their leads' branches that enter with those merges: `imports` (4; L5), `forms` (6; L3a/L3b, one copy to be chosen), `d3d9total` (7; L6); L2's `abi`, `namegate`, `r7` (8) are wired, below. L2 gates of class 8, owner L2, one `gates.json` entry each, per-root convention (no native guest root: universe 0, green):
- `abi` (`verify/gate_abi.py`; rule `link/abi.py`): a reference and the definition it binds to have one calling contract. Universe: the rows `abi.py` defines, over the C++ objects of every native guest root, plus the input facts its proof rests on as items: every `odr` COMDAT signature in two or more objects and every shared `.fnvnx.layout` key (`layout:`). Red iff an item is uncertified, the build is not green, or an object lacks `.fnvnx.sympair` (red item naming the object).
- `namegate` (`verify/gate_namegate.py`; rule `link/gate_names.py`): a native body that stands for an exe function carries its MSVC name, certified against the cl object of the same source. Universe: the `rows()` whose symbol is the NATIVE body of an exe function, by the join `addrmap.tsv` (object, symbol -> address) with `bodysel.tsv` (address -> native/lifted); a symbol with no exe address or LIFTED is outside it. Red iff an item is uncertified or the root lacks the bodysel verdict (owner L1) or the cl reference objects (owner L2). A root without `game`: universe 0.
- `r7` (`verify/gate_r7gate.py`; rule `link/gate_r7.py`): every type a native exe-function body uses has the exe's layout, certified against the cl /Z7 CodeView types. Universe: the `rows()` of the native-bearing units (a unit with at least one native exe-function body; its complete records bound the types the body uses). Red rule as `namegate`. Stated rule of the universe: **a row is in `r7` iff its unit has at least one native exe-function body** (a sound over-approximation: it is stricter than "the types a native body uses", never looser). `gate_r7.rows()` rows carry no symbol, so the per-body narrowing is not possible yet; once rows carry their symbol (type -> functions that use it), the universe can narrow to those types, after the first frame. A record whose key the plugin could not name (`nokey:`) is `undecided`, hence red, inside this universe.
Stated limit: the `bodysel` stage of `gate_contract.py` passes no `--n3` yet, so `bodysel.tsv` has no NATIVE and the universes of `namegate` and `r7` stay 0 until the N3 verdicts are wired (owner L1/L2).
`argordergate` (class 8, argument order; verify/gate_argorder.py, wired): the universe is the C++ units of the native guest roots (`target` guest, `cxx_origin` native), the certificate is the rule's plugin list in the rule's load order with `fnvnx_order` and its arguments, its .so in the build result (so in the CC-stage key of each unit) and the unit's object; no such root: universe 0, green. A name in `REQUIRED_PRODUCT` must also be an entry of `gates.json` (admit rule 3), so the root stays out until the gate exists.
**Universe rule of the per-root gates.** Every per-root gate (`build`, `objstub`, `linkstrict`, `determinism` and the 14 contract gates `n1`, `n2_*`, `xc`, `xunit`, `rtti`, `pcrel`, `bodysel`, `abs`, `disp`, `reach`, `reachlink`, `gpa`) quantifies over the admitted product roots of `admit.json` (contract gates through the one function `gate_build.quantify`), and with no product root its universe is 0 and it is green. Admission (`gate_admit` rule 3 plus `REQUIRED_PRODUCT`) is what forces every gate to exist and run on a root, and a root that is admitted but lacks objects, an ELF or an input is red with the reason, never skipped.

The contract gates (class 8, plus the link-side `abs`, `disp`, `reach`, `reachlink`, `gpa`) run through `gate_contract.py --gate NAME`, one `gates.json` entry each. Per-root inputs are outputs of the root's own build and link, read beside `objects.json` (`root_results` of `contract_inputs.json`): `units.json`, `addrmap.tsv`, `aliases.ld` and, for `disp`, `entries.tsv`; a file the link stage does not emit yet makes the gate red for that root naming it (owner: L1 link stage). The game's units are the directory `build.json` declares as `game` (no default). Shared inputs are the five true externals of fnv-nx-build (commit plus `tree_sha256`: `exe`, `redist`, `msvc`, `facts`, `manifest`), `reach_requests`, `dynsites`, optional `keys`, `bridge`, and `ranges` (where an image's range facts are). The image set of `gpa`, `reach`, `reachlink` is not an input: it is derived by the rule in the section "The guest image set" below. An image of the set without range facts is a reported red item of those gates (owner L3a/L5). `n2_*` and `xc` judge that every function they flag is turned into LIFTED by `bodysel`. Outputs of the chain are cached in `GATE_CACHE/contract/<key>`.

## The guest image set

Used by `gpa`, `reach`, `reachlink` (not an input of `contract_inputs.json`). `contract/images.py` derives it as the transitive closure of the normal and delay-load import directories from the exe, each dll name resolved case-insensitively against the files of the externals `exe` and `redist` (a dll found there is a guest image and is walked further; a dll not found is a platform import of gate `imports`, owner L5, not an image). Stated assumption: dynamic LoadLibrary names are facts of the `gpa` gate, not of this rule.

## References of tests (class 10, gate `oracle`)

Every test file of the `selftests` universe has one entry in `DIR/tests/oracle.json` ("fnvnx-oracle/1"): the files under
test (`subject`) and the references its expected values come from. The rule, its argument and the reference forms are in
the `gate_oracle.py` docstring. In short:

- a reference is a dump of the pinned exe under Wine 9.0 (a file of a commit of `fnv-nx-build`, fetched as an external and
  checked by `tree_sha256`), a cited text (a tracked file plus an anchor that resolves in it: a heading line or a line
  range), a contract table, or an independent model;
- a text without a file and an anchor is not a reference;
- no cited text, contract table or model may be a file the subject can import (closure over the syntax trees, a superset
  of what python can load): a tool cannot cite itself. The stated rules of the tools are `verify/TOOLS.md`;
- a test of product code needs a Wine dump or a cited text, and none of its references may lie in a product root.

The pinned exe is `verify/oracle_pins.json`. Assumption: the declaration is true. It is checked by verification, not by the
gate: `verify/oraclecheck.py` runs every test under strace and compares the tracked files the run opened with the import
closure of the subject plus the references (a file opened outside them, or a subject file never opened, fails the test's
check). It is a job of the after-merge verifier.

## Where no closing rule is known

Reported to the orchestrator; none of these is patched around.

- **Classes 14 and 15** (memory faults, hangs): by the owner's revised decision of 2026-11-11 the memory-safe subset is suspended
  (the suspended design is `verify/MEMSAFE.md`). They are covered by verification, not by required gates: AddressSanitizer and
  UndefinedBehaviorSanitizer in the stage runs and tests, and the sound analyzer for information. Recorded assumptions: native code is
  checked, not proved; a wild access of the game into host pages is not excluded by any gate.
- **Class 17** (memory exhaustion: an allocation path without a contract result) has gate `oom`, owned by the owners of the code, to be
  written after the first frame.

Accepted assumptions that cannot be removed (MASTER_PLAN4 section 4):

- **A8: Win32 and D3D9 are given by documentation, not by a formal model.** A body that is not vacuous but
  implements its contract only in part is therefore closed by conformance evidence per function. Class 10 makes
  that evidence honest; it does not make it complete.
- **Behaviour of the console** (Horizon, memory limits on hardware) stays an assumption until a run on the console.

## Assumptions

The rules cited by the gates (N1-N4, R1, R3, R5, R6, R12, R13, K16 and others) are defined in `RULES.md`.

- A2: the compiler is correct. The gates judge its output, not its internals.
- The set of terminating routines for class 1 is derived from the toolchain's own headers (the `noreturn`
  attribute); a terminating routine without that attribute would be missed.
- The verdict runs gates in a clean worktree of the commit, in the environment declared by `env.json`.

## Compiler plugins (`build` gate)
A product root with native or decomp C++ is compiled with `fnvnx_order` and `fnvnx_ftoi` (RECIPE.md, "Plugins"); a root whose C++ is all lifted
(declared `cxx_origin` of its admit.json entry, consistency checked against the recipe) needs none. A missing plugin, a changed source or argument, or
an undeclared origin makes the recipe invalid and the `build` gate red. Tests: `verify/tests/test_plugins.py`.
