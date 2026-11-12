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
| 4 | Import without a body | `imports` | manifest of reachable imports against defined symbols | L5 | wired (per root, `gate_build.quantify`); a root without `imports.in.json` is red |
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
| G | Registry key read by the exe or a module with no value in the declared machine, or a value with no reading site | `machine` | the hive against the registry call sites found statically (`gate_machine.py`: regsites.tsv, an `external`, against declared_registry.tsv, the tracked seed include and the symbol tables of the objects; a declared row nobody reads, a seed that is not the generator's output, a writer outside the seed unit, or a site whose key or value is not statically resolved (a measurement build) is red) | L1, L5 | wired per root (`gate_build.quantify`); a root without `registry.in.json` is red |
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
their leads' branches that enter with those merges: `imports` (4; L5), `forms` (6; L3a/L3b, one copy to be chosen), `d3d9total` (7; L6), `r7`, `namegate`
and the ABI tools (8; L2). `argordergate` (class 8, argument order; verify/gate_argorder.py, wired): the universe is the C++ units of the native guest roots (`target` guest, `cxx_origin` native), the certificate is the rule's plugin list in the rule's load order with `fnvnx_order` and its arguments, its .so in the build result (so in the CC-stage key of each unit) and the unit's object; no such root: universe 0, green. A name in `REQUIRED_PRODUCT` must also be an entry of `gates.json` (admit rule 3), so the root stays out until the gate exists.
**Universe rule of the per-root gates.** Every per-root gate (`build`, `objstub`, `linkstrict`, `determinism` and the 14 contract gates `n1`, `n2_*`, `xc`, `xunit`, `rtti`, `pcrel`, `bodysel`, `abs`, `disp`, `reach`, `reachlink`, `gpa`) quantifies over the admitted product roots of `admit.json` (contract gates through the one function `gate_build.quantify`), and with no product root its universe is 0 and it is green. Admission (`gate_admit` rule 3 plus `REQUIRED_PRODUCT`) is what forces every gate to exist and run on a root, and a root that is admitted but lacks objects, an ELF or an input is red with the reason, never skipped.

**Universe rule of the per-root gates for roots that are not a game tree.** The contract gates have two kinds of items. The game gates (`n1`, `n2_data`, `n2_call`, `n2_vcall`, `xc`, `xunit`, `rtti`, `pcrel`, `bodysel`) judge the functions of the units under the root's `game` directory. The link gates (`abs`, `disp`, `reach`, `reachlink`, `gpa`) judge the root's linked guest ELF.
- A root whose `link` is null has no ELF: its universe is 0 for the link gates. A root without a `game` key has no game functions: its universe is 0 for the game gates. Both are green, not "inputs" red. Such a root (L3a's `lift/rt` runtime root, a host root) needs no inputs file.
- `game` is required, and red when missing, exactly for a root whose link produces the guest image (`link` non-null: on the trunk the only link is the guest ELF; the recipe has no separate `kind`). A root with `game` and no link is red: its game units have no image to be judged on.
- Argument that nothing escapes: every object of the product reaches the contract gates through the one image that links it. A non-image root's objects are judged on the image root that names it (`link.roots`; the field is not in the trunk's recipe yet, so the gate does not read it and the rule rests on `link` alone). An object that no image links is not part of the guest ELF, so it has no run-time role for the gates to cover, and an image root that links such objects is judged on them as its own units.

The contract gates (class 8, plus the link-side `abs`, `disp`, `reach`, `reachlink`, `gpa`) run through `gate_contract.py --gate NAME`, one `gates.json` entry each. Per-root inputs are outputs of the root's own build and link, read beside `objects.json` (`root_results` of `contract_inputs.json`): `units.json`, `addrmap.tsv`, `aliases.ld` and, for `disp`, `entries.tsv`; a file the link stage does not emit yet makes the gate red for that root naming it (owner: L1 link stage). The game's units are the directory `build.json` declares as `game` (no default). Shared inputs are the five true externals of fnv-nx-build (commit plus `tree_sha256`: `exe`, `redist`, `msvc`, `facts`, `manifest`), `reach_requests`, `dynsites`, optional `keys`, `bridge`, and `ranges` (where an image's range facts are). The image set of `gpa`, `reach`, `reachlink` is not an input: it is derived by the rule in the section "The guest image set" below. An image of the set without range facts is a reported red item of those gates (owner L3a/L5). `n2_*` and `xc` judge that every function they flag is turned into LIFTED by `bodysel`. Outputs of the chain are cached in `GATE_CACHE/contract/<key>`.

## The guest image set

Used by `gpa`, `reach`, `reachlink` (not an input of `contract_inputs.json`) and, through the same function, by `contract/modules.py` (the module table of the loader). The rule is `contract/dllkind.py`, one pure function with no named list. A dll name is resolved case-insensitively against the files of the externals `exe` and `redist` and against the rows of `bridge.tsv`:
- **bridge**: the dll has bridge rows. The platform implements its entries. It is never a guest image, even when a file is found, and the walk does not continue through it.
- **image** (lifted): found, no bridge row, and its whole normal import closure resolves to images or bridge dlls (the greatest such set; a delay-load import is bound at run time and does not decide it).
- **declared**: found, no bridge row, not an image: the declared machine cannot load it, because its closure needs a dll that is neither found nor bridged. Its entries must be served by bridge rows, and a reached export without a bridge row is red, owner L5. The walk does not continue through it.
- **platform**: neither found nor bridged; a platform import of gate `imports`, owner L5.
The image set is the least set that contains the exe and is closed under (i) the normal and delay-load imports of its members and (ii) the LoadLibrary targets of its members whose names are statically resolved (`contract/loadlib.py`, below); a bridge or declared dll stops the walk. A dll file reached by neither is in no image and gets no base. `modules.py` (`--loads loadlib.tsv`) writes the images to `modules.tsv` (they alone get an address range) and the bridge and declared dlls to `modules_declared.tsv` (name, kind `bridge` or `unloadable`, sha256, size, missing imports).
`modules_declared.tsv` records for every missing import of an unloadable dll its provider: `wine` when the dll is in `verify/declared_dlls.tsv`, `absent` otherwise. That list is the declared machine's dll set, derived by `contract/declareddlls.py` from the Wine 9.0 `dlls/*/*.spec` files (fnv-nx-build mirror `tools/wine-specs`, pinned in `contract_inputs.json` as external `wine`); a dll is on the machine iff its `.spec` exists, never by name. A `wine` gap is L5's (bridge rows); an `absent` one is not on the declared machine.

Bases are a pure function of the image set in name order: the images are sorted by lowercase name and packed from the end of the exe image, each aligned up to 64 KiB (`modules.assign_bases`), so an image moves only when an image with a smaller name is added or removed. `ssce5432.dll` sorts after `libvorbis.dll` and `libvorbisfile.dll`, so if L5 bridges `comdlg32.dll` and `ssce5432` becomes an image, no existing base moves (`test_images.py` checks this property).

### LoadLibrary names and the image set

`contract/loadlib.py` is the `loadlib` stage of the root (before `gpa`). It computes the image set and the LoadLibrary facts together, as a fixpoint: the members that have range facts are derived once (every LoadLibrary reference of the image, `argflow.uses_of_slot_ref`, argument 0 by `argflow.derive_arg` with the may-write rule), their resolved names enter the walk, and the walk can add members. The set only grows, so the loop ends. It writes `loadlib.tsv` (module, site, calls, api, status, names, breaks) and `images.tsv` (the set with the pe and ranges path, `-` for a member without range facts); `gpa_names.py` and `gate_reach.py` read the set from `images.tsv` (`--images-file`). A site is the relocated slot reference, the address that L5's `dynsites.tsv` names.
- A site is **closed** when every leaf of its argument is a string constant; it loads exactly those names. A site whose argument is not derived (a writable global that may be written, a pointer through an unresolved object, an escaped string) is **open**: it may add an image, and the set is final only when no site is open.
- `dynsites.tsv` and `loadlib.py` must find the same LoadLibrary sites for every image that has facts: a site in only one list is an uncertified item of gate `gpa`. One rule, two finders that agree or the gate is red.
- The gate `gpa` counts the `loadlib` sites (universe = sites + disagreements, certified = closed sites) with its own rows, and `gpa`, `reach`, `reachlink` count a member without range facts as one uncertified item (owner L3a/L5).
- The facts of an image are at the base the module table assigns (`# base=0x..` header of `ranges.tsv`); the image, its relocations and `dynsites.tsv` are at the preferred base. `argflow.Image` shifts the facts by (preferred - assigned), one coordinate system (`argflow.rebased`); a fact that does not shift into the address space is an error. Before this rule every range fact of a dll was outside its image and every site of a dll read as unreached (a closed row with no pairs); the shift is a precondition of every gate that derives a call argument in a dll.

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
