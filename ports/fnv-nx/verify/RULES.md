# Rules referenced by the gates

The ids that gate code and docstrings cite (`N1`, `R5`, `K16`, ...). Source: the plan of record MASTER_PLAN4 (private
plan repository; section 1 "Rules (with proof and assumptions)") and, for K16/K2, MASTER_PLAN2. The statements are
carried over in our own words; no fact about the exe is stored here. Verdict and gate list: `VERDICT.md`.
Principle (MASTER_PLAN4): the solver must be sound and static; incompleteness costs speed, never correctness.
Whatever is not proved gets the lifted body. Fuzzing, boots and farms only verify a rule.

## R1 BODYSEL-PROOF

`NATIVE(f)` iff N1 and N2 and N3 and N4 hold for `f`; otherwise `LIFTED(f)`.
Proof argument: N1 gives "exe = MSVC(source)"; N2 gives "the GCC source = the same source"; N3 and N4 give "GCC and MSVC
agree on the subset P". Floating-point arithmetic inside P is not excluded (rule R8): its reference is the source under IEEE.
Assumptions: A1 (the list of GCC/MSVC divergences that defines P is complete; there is no formal semantics of MSVC 2008),
A2 (GCC is correct), A10 (accepted by the owner, see R9). Proof level is N1-N4 only (owner decision); no SMT translation
validation for native bodies.
Gate: `bodysel` (`contract/bodysel.py`); it takes N1-N4 as inputs and decides statically before any run.

## N1 identity with the exe

The function as MSVC compiled it from the pristine decomp tree (the COFF object of its unit) is byte-equal to the exe bytes
at its address, up to relocations, and the relocation targets are consistent over the whole set (greatest fixed point of
`symbol -> address`). The address map is a certificate: how it was found does not matter, it is checked as a whole.
Gate: `n1` (`contract/n1.py`, gate file `n1.json`); condition for per-unit symbol scope: `xunit` (`contract/gate_xunit.py`).

## N2 prepare rules

Every prepare rule has a lemma of one of two kinds. Neutral: the tree's MSVC object after the neutral rules is byte-equal to
the pristine one, per function, without sampling. Targeted (SDD1, conventions, shim): changes only the representation at
the contract boundary; the lemma is discharged by GCC-object gates for `f` at all sites: vcall offsets equal the exe, vptr
stores equal exe addresses (R6), the set of data references equals the exe relocations, call targets equal the exe.
Declaration equality (`n2_call`): for every cross-boundary edge of a native body (call, tail, bridge, data reference to an exe
address Y) the body's object has a `.fnvnx.sympair` row of direction `ref` whose `msvc_name` is byte-equal to the pristine
COFF symbol name of Y, and that name is decorated (`?...`). No row, an `unnamed:*` row, no pristine symbol at Y, or an
undecorated one (extern "C", every asm/x87/register CRT helper: `_ftol2`, `_CI*`, `_alloca_probe`, with no name table) is
not provable: the caller is not N2. Proof: R5 derives convention, stack words, varargs and sret from the decorated name;
byte-equality of the full names gives equality of all of them. Assumptions: the `names` plugin ran on every native unit
(recipe rule: guest compiler plus plugins); the pristine cl names are the exe's contract (N1).
Gates: `n2_data`, `n2_call`, `n2_vcall`, `xc` (`contract/gate_data.py`, `gate_call.py`, `gate_vcall.py`, `gate_xc.py`;
rows come from `contract/registry.py`; a function with a row is not N2 and becomes LIFTED in `bodysel`).

## N3 portability

The GIMPLE of the function after inlining (GCC plugin) lies in the subset P under flags F (`-fwrapv -fno-strict-aliasing
-fno-delete-null-pointer-checks -fno-lifetime-dse -ffp-contract=off -fno-builtin -fno-devirtualize-speculatively`).
Outside P: division or shift with a non-constant operand without the x86 semantics inserted by the plugin; a read of a local
without a proved assignment; two or more arguments with side effects (evaluation order); varargs definitions, `setjmp`,
`volatile`, asm; a type whose layout is outside R7; a FuncInfo or scopetable at the address (an exe fact); a local read
replaced by zero (`uninit=zero`, a stand-in); `delete[]` of a polymorphic type while `__sdd` does not implement `??_E`.
Gate: `bodysel` reads the L2 plugin verdict (`address <tab> ok|ok+E|reason <tab> inlined callees`, via
`contract/l2sections.py`); no verdict means not N3.

## N4 closure under inlining

`NATIVE` is the greatest set closed under "everything inlined into `f` is NATIVE". Unproved functions are visible to GCC
only as external declarations.
Gate: `bodysel` (`contract/bodysel.py`, greatest fixed point over N3's inline lists).

## R3 CODEMAP

Every byte of `.text` of every module belongs to exactly one class: an instruction of a function, alignment or a table named
by a relocation. Anything else is a build error. Indirect jumps go through a dispatcher over the set of code addresses from
`.reloc`; the set is complete if a static taint check proves there is no arithmetic over a code address, and where it does
not, that function statically gets a dispatcher over all instruction boundaries. `call` pushes the exe return address on the
guest stack; a `ret` to another address goes to the same dispatcher.
Assumption A6: the code does not modify itself; `.text` is mapped read-only and a write raises the exception Windows raises.
Gate: `disp` (VERDICT.md class 5, dispatcher table against the R3 universe; not written yet); resolution of code pointers in
`link/tables.py`.

## R5 CALL

Every code address has a guest entry: a lifted body or a back bridge to a native one. lifted-to-lifted needs no prototype.
The prototype of the native side is read from the MSVC decorated name of the symbol whose bytes equal the exe (N1):
convention, types, sret, ST0 return. A direct AAPCS call is allowed only as an optimisation when both sides name the same
decorated symbol. LTCG conventions (eax/edi) do not pass N1 and go to lifted. Arity inference, "first read of ecx" and
"first `ret N`" are removed.
Enforced by: `contract/proto.py` (a function that is not N1, or whose declaration is not fully decoded, has no native
prototype and is LIFTED, reached through its guest entry).

## R6 VT-ID

The address of a vftable comes from a relocation: in a byte-equal function (N1) a COFF relocation names `??_7C@@6B...@` and
the exe stores the vftable address at the same offset; the mapping is a function by construction. `_ZTV + offset` ->
(class, base path) -> `??_7` is supplied by the plugin from the hierarchy. No training, no "order/offset" heuristics.
Enforced by: `contract/vtid.py`, `contract/n1.py` (the map; one decorated name, one vftable), `link/tables.py`.

## R12 BUILD-PURE

A stage runs in a sandbox that holds only the declared inputs; clocks and network are closed; the order is deterministic.
The key is the hash of the inputs and of the tool code. Then warm == cold is a theorem (assumption A9: SHA-256). A release is
always built cold; the shadow farm stays a check.
Gates: `pure` (`verify/gate_pure.py`, `verify/puregate.py` = rule R12-T, policy in `puregate_policy.py`), `determinism`
(`verify/gate_determinism.py`); `verify/sandbox.py`, `verify/cas.py`.

## R13 GATE

A gate accepts completeness of certificates, not a run count. The input of the BODYSEL solver hermetically excludes fuzz and
boot outputs (by the R12 key).
Enforced by: `contract/bodysel.py` (its argument list is the whole key of the stage).

## K2 identity of object and table (basis of K16)

The vptr value in an object equals the vftable address of the exe in the data image. Every relocation `_ZTV...+8` is rebound
to that address by key (unit, symbol). Assumption: the key is unambiguous. (Implemented as R6.)

## K16 RTTI

Follows from K2: `dynamic_cast` and `typeid` read the exe's RTTI through vptr[-4] (`__RTDynamicCast`, `__RTtypeid`). GCC's
own RTTI is never used by game code: no relocation naming `_ZTI*`/`_ZTS*` and no call of `__dynamic_cast`,
`__cxa_bad_cast`, `__cxa_bad_typeid`, `type_info::operator==`/`before` in any code section of any game object.
Gate: `rtti` (`contract/gate_rtti.py`, gate file `rtti.json`).

## R4 EH (cited by N3 and by the suspended MEMSAFE.md)

Full MSVC semantics: FuncInfo, scopetable and `fs:[0]` are executed from the exe's tables. Hardware faults in lifted frames
are modelled exactly; a native frame without EH state is unwound by resetting the host stack (the exe function has no
cleanups; this follows from N1).

## R7 LAYOUT (cited by N3)

A predicate per type: a native function is admissible only if every record type it touches (field, sizeof, new, pass by
value) equals the layout dump of MSVC `cl` itself (`/d1reportAllClassLayout`), not clang's.

## R9 TSO-MAP (cited by MEMSAFE.md; ordering of shared accesses)

Guest threads run in parallel. Every memory access gets an ordering class by a static rule: (a) thread-local (a guest stack
slot whose address provably does not leave the function; a native automatic variable with no address taken) uses plain
`ldr`/`str`; unproved counts as shared; (b) all other accesses use `ldar*`/`stlr*`, with an unaligned address taking the
second complete path (`ldr` + `dmb ishld`, `dmb ishst` + `str`); (c) native bodies get the same mapping from the plugin;
(d) `Interlocked*` and `xchg` with memory are `ldaxr`/`stlxr` loops. Argument: when all shared events are acquire or
release, program order between them is preserved, so executions are sequentially consistent and SC is within TSO.
Assumptions: A7 (the ARMv8 model matches the A57), A10 (the lifter keeps the exe's access order exactly; a native body runs
the source's accesses in GCC's order, so at a race site it gives the source's behaviour under SC, not necessarily the exe's).
