# Differential fuzzing per function (stream S13-difffuzz, J-0038 D14)

Behavioural verification of the port without the game executable: every native AArch64 ILP32 function is run against
the **oracle**, the same function of the pristine decomp compiled with MSVC 2008 (byte-identical to FalloutNV.exe
1.4.0.525 by the decomp's guarantee), in two emulators that share one memory image. Tools: `tools/difffuzz/`.
Everything they read (decomp, MSVC objects, our objects) is local and derived from the decomp; they write
addresses, counts and divergence facts only.

## Inputs

| Side | What | Where (defaults, `config.py`) |
|---|---|---|
| oracle | MSVC 2008 object per unit, `u<index in units.json>.obj` (S11's pristine build, J-0038) | `FNVNX_X86OBJ` |
| candidate | AArch64 ILP32 object per unit from the prepared tree, current flags (`tools/link/compile.sh`, J-0034) | `FNVNX_A64OBJ`, `FNVNX_TREE` |
| link | S2's `fnvlink.py resolve` + `apply` on that object set (`a64prep.py link`) | `FNVNX_DFWORK/../link` |

## How one function runs

**x86 (Unicorn x86-32).** `coffobj.py` loads every function of the unit from the COFF object at its original
address and resolves each relocation to its original target exactly like the decomp's compare rule (units.json
`symbols`/`functions`, labels inside known extents). String literals and `__real@`/`__xmm@` constants the code reads
are recovered from the object and pinned at their original data addresses. Flat GDT (32-bit SS), FS -> a TEB (SEH
head), x87 control word 0x027F (MSVC CRT default, 53-bit). Arguments per the MSVC convention of the decorated name
(`conv.py` over S2's `sigs.py`/`abi.py`/`abi2.py`: ecx/edx/stack, hidden result pointer, eax / edx:eax / ST0
results). The return address is an epilogue stub; a float result is stored with the caller's rounding
(`fstp dword/qword`) and as the full x87 80-bit value.

**AArch64 (Unicorn ARM64).** `a64image.py` links the unit's rewritten object (+ bridges) at 0x0a000000 with the
absolute data symbols of `aliases.ld` (natively defined image variables at their original addresses, like the game
link) and gives every still-undefined symbol a stub: `__fnv_<X>` -> 0x08000000 + X, other names -> a named slot;
stub pages hold `brk #0`. Arguments per AAPCS64 ILP32 from the same decorated name (x0..x7, v0..v7, stack, x8).

**Shared world (`world.py`).** Identical pages on both machines, mapped on first touch: null region (< 64 KiB)
faults, .text range 0xCC + oracle code, data window 0x00fdf000..0x02100000, argument buffers at 0x03000000 (each
pointer/`this`/by-reference argument and the hidden result get the same address on both sides), heap 0x03800000,
background zero or seeded random. Each machine's private range (stubs/image, GDT/TEB) faults on both machines when
touched as data, so a wild pointer behaves the same; an AArch64 address >= 4 GiB faults (ILP32 check).

**Unit-closed execution.** All functions of the unit run for real on both machines (GCC and MSVC inline different
same-unit callees); calls leaving the unit are intercepted by ORIGINAL address: on x86 a block starting in .text
outside the loaded code, on AArch64 a fetch in a stub. Each records (target, arguments), returns the case's canned
value k (eax/edx, ST0 via `fld` for float callees, x0/v0, x8 memory), pops per the callee definition's convention
(stdcall/thiscall `ret N`) and clobbers caller-saved registers. CRT functions and compiler helpers run with their
real semantics and are not traced (`libcalls.py`: mem*/str*, `_ftol2` as x87 code, `_CIsqrt/_CIsin/_CIcos`,
`__security_check_cookie`, `_chkstk`, `_allmul/_alldiv/_allshl/_allshr`); runtime names map to the MSVC function
with the same role (`_Znw*`/`_Zdl*` -> 0x00401000/0x00401030, `__dynamic_cast` -> `__RTDynamicCast` 0x00ec43fb).

**Inlined callees.** An oracle callee outside the unit that the native object never names (GCC inlined a header
inline function MSVC called out of line) is preloaded from its defining unit's MSVC object and runs for real, so both
sides do the same work (`engine.preload`); a remaining call-target mismatch whose oracle target has code loads that
function too and replays the case (adaptive follow, `runner.Pair.run`). Literals units.json does not name get
addresses in a pinned pool (0x02f00000); literals of every unit whose code is loaded are pinned. MSVC's
`eh vector constructor/destructor iterator` (0x00ec782f / 0x00ec5fce) run as x86 loops, the platform's
`fnvnx_eh_vector_*` as an AArch64 helper object (`a64helpers.cpp`) linked into each unit image. The prepared tree's
declaration of a definition (S1b XINC1-corrected) places the arguments on both machines.

**Object sets.** `FNVNX_DFPROFILE=<file>` gives defaults for the variables of `config.py` (`KEY=VALUE` lines), e.g.
S2c's f10 link: `FNVNX_DFLINK=<run>/f10`, `FNVNX_TREE`, `FNVNX_A64OBJ`, `FNVNX_DFA64`. `recheck --tag <t>` replays
saved divergences on another object set (`recheck_<t>.jsonl`), `recheck_summary.py <t> <dirs>` tabulates gone /
soft / still per original cause. **Vtables (D7).** A native store of an Itanium address point is compared as
the image vftable it stands for (S2 kind-V rows, else `??_7C@@6B@` paired with `_ZTV<C>`+8 by class name).
**Code pointers as data.** A native code address stored to memory (a `__fnv_X` stub, a native entry of the image)
is compared as the original address it stands for, at any byte offset of the store and through `memcpy`-like
builtins too (tables of initializers, callbacks in objects).

**Imports.** Each IAT slot the oracle reads (0x00fdf000..) is pinned to an x86 import stub (0x0e010000 + 16 per
import); a call through it and a native call of the same import name are traced as `imp:<name>` on both machines,
stdcall imports pop their arguments. The Interlocked family runs as builtins on both sides.
**Runtime equivalences.** `_Znw*`/`_Zdl*` (sized delete included) -> 0x00401000/0x00401030 or the unit's MSVC
allocator (0x00aa1460), `__dynamic_cast` -> 0x00ec43fb, `atexit` -> 0x00ec658f.
**Dynamic initializers.** `??__E`/`??__F` and the platform's `__fnvnx_dyn*` run as `void(void)`.

**Canned results (difffuzz-5).** An intercepted callee declared `bool` returns 0/1 (MSVC callers test al, GCC
bit 0). A callee MSVC returns through a hidden result pointer gets the same canned bytes on both machines (x86: in
the caller's buffer, eax = its address; AArch64: wherever AAPCS64 puts that type, x8 memory or registers). An
intercepted MSVC constructor (`??0`) returns `this`. Native `sincos`/`sincosf` run as builtins. A pointer result
into the native unit's own .rodata is compared by its pointee (C string); a native code address passed or
returned as data compares as the original function; a bool result with the same truth value but another byte is
soft `bool-repr`; call arguments that are NaN on both sides are soft `nan-bits`.

**Argument filters.** A call argument is not compared when the callee's declaration only carries it for MSVC's
register placement (S2 `abi.tsv` "edx dummy" notes), or when the oracle's own value is the case's planted garbage
(an uninitialised stack or register word: `world.garbage_words`); a native argument equal to native garbage is
reported as `arm_garbage` (the port passes something uninitialised).

**MSVC custom register conventions.** `classify.entry_register_reads` lists the registers the oracle reads on its
entry path before writing them that the declared convention does not pass (eax, edx, edi...: whole-program
optimisation in Havok and elsewhere); `Conv.with_registers` assigns the declared register-eligible parameters to
them in declared order, the native side keeps AAPCS64. A function so modelled is fuzzed only if three cases that
differ only in the planted garbage give the same oracle outcome; otherwise its status is `oracle-gap:custom-cc`.

## Comparison (`runner.compare`, first difference wins)

`error` (one side faults: div0, unmapped, ILP32 high address, stack protector), `call-target` / `call-count`
(the sequence of original addresses), `call-args` (logical arguments decoded per the callee DEFINITION's signature on
both sides, so an S2 bridge must deliver what the definition expects; pointers into either stack compare equal),
`return` (bit-exact; float/double also ulp/relative tolerance and the x87 value), `mem-write` (effective writes outside
the stacks, hidden result buffer excluded), `callee-saved` (ebx/esi/edi/ebp, x19..x29/sp). Timeouts are inconclusive.
Soft verdicts: `local-static` (MSVC keeps a function-local static in the image, GCC in the unit's .bss).

## Fuzzing (`fuzz.py`) and root causes (`classify.py`)

Inputs: scalar arguments (edge values, bool only 0/1), argument buffers (zeros, random, small ints, pointers into
buffers/window/heap, float words), the globals the oracle references, canned call results, background mode. Coverage =
basic blocks of both machines; new coverage joins the corpus. Each divergence kind is minimised (overlays dropped, bytes
zeroed, zero background) and probed: `division`, `ilp32-pointer`, `uninit-x86`/`uninit-arm` (result depends on
garbage: stack fill, non-argument registers), `float->int`, `x87-precision`/`float`, `bool-repr` (a byte other than
0/1 read as bool), `signedness` (clearing a top bit removes the divergence), `split-global`, `vptr-store`,
`call-flow`. Saved as `<campaign>/div/<addr>-<n>.json` with the minimal case; `difffuzz.py repro [--trace] <file>`
replays it with both instruction traces. Results are one line per function in `<campaign>/results.jsonl`, written when
the function is done: `--resume` continues after a restart.

Static checks found through divergences: `check_rebind.py` (references that bypass S2's image-variable rebinding:
GAS resolves a reference to a global of the same object to `.bss+off`, so the defining unit keeps a native copy),
`check_statics.py` (unit-local variables the oracle keeps at an image address, top-level `_ZL` and namespace-scope
`_ZN<ns>L..E` statics; one GCC folded away has no symbol left), `check_falloff.py` (native functions that run off
their end: a non-void function without `return`, GCC drops the epilogue).

**Triage.** `difffuzz.py triage <dirs> [--tag t] [--only cause] [--reads]` replays saved divergences that still
reproduce and, with `--reads`, runs the read-set probe (`readset.py`): the addresses one machine reads and the other
does not (argument buffers, image window), and a constant offset between them (`shift`: a struct layout difference).
Groups: oracle-only / native-only reads of image globals or object fields, layout shift, same reads with a different
result. `difffuzz.py effects <div files> [--list f] [--out f]` (`effects.py`) replays each with every data access
of both machines recorded and reports the first effect they disagree on: bytes read with another width, the first
byte read with another value, the final stores that differ (with both pcs), where a diverging argument or result
comes from (a load, a stack or native image address, a stub, planted garbage, a constant), and the last
instructions of both machines before that point.

**Unit membership (difffuzz-6).** A function of the oracle's unit that the native object does not define and calls
through its code stub (defined in another object, e.g. moved by S1b) is intercepted on the oracle side too, not run
for real. Adaptive follow never loads a function the native unit calls out of line: a call-target mismatch there
is an order difference, not inlining. Two adjacent calls in swapped order (`x86_next`/`arm_next` of a call-target
divergence) are the soft cause `call-order`: C++ leaves the evaluation order of operands open (S13 00740980:
`SetField628(FUN_00740960() + 1)` next to `FUN_00740940()`).

**Runtime equivalences (difffuzz-8, from the first farm pass).** A native call of a name the link gives a provider
(addrmap kind F `provider:` rows: `free`, `_invalid_parameter_noinfo`, `memmove_s`, `fnvnx_rt_dynamic_cast`, ...)
traces as that CRT address; a native call through an IAT slot traces as the import, as on x86; the allocation and
release families (operator new 0x00401000 = MemoryManager::Allocate 0x00aa3e40; operator delete 0x00401030 =
sized delete 0x00aa1460 = CRT free 0x00ecd291) compare equal, arguments not compared. Adaptive follow loads the
oracle's callee X unless the native trace makes X's call later (then the two orders differ: `call-order`). Report
causes `msvc-inlined` (oracle gap: the native calls X out of line where the oracle ran X's body inline, the oracle's
next call being one X makes), `runtime-equiv` (oracle gap, records from before difffuzz-8) and `array-new-check`
(soft: GCC checks new[]'s count, MSVC 2008 does not).

**Canned results by declaration (difffuzz-9).** An intercepted call's canned result follows both the callee's
definition and the calling unit's declaration of it: a reference result (`T&`) is a valid non-null heap address
(`world.ref_result`), a bool result (either declaration) is 0/1. The bytes an intercepted callee pops are read from
its oracle code (`ret N`) when known, not from its declaration (a declaration with a parameter too many or too few
left the oracle's esp off by 4: `fault:x:00000000`).

**Virtual calls (difffuzz-13, vtmodel.py, J-0138).** With the player's exe (`FNVNX_EXE`; the farms take it through
`job_gamedata`) an object the fuzzer gives a class gets a planted vptr (`Case.vptrs`): `this` of a member function
and pointer / reference parameters to a class, at offset 0 of their buffer. The class's vftables come from the
exe's RTTI (complete object locators: class, offset, hierarchy), by the decomp's `??_7C@@6B@` name of the class,
the RTTI class name, or (for decomp shell classes) the vftables whose slots hold the function itself, plus up to 6
classes derived at offset 0. x86 reads the vftable V there, its slots pinned to the exe's words (original
addresses); AArch64 reads the native address point the kind-V row pairs with V: a copy of that Itanium vtable in
the private range 0x0bc00000, its relocated slots resolved for the unit image (the unit's own definition,
`CODE_STUB + X` for `__fnv_<X>`, the named stub of a D0/D1 that names the `??_G`). A virtual call is then a call
event of the same original address on both machines, compared like a direct one; an MSVC deleting destructor
`??_G(this, flags)` compares `this` only (the flags are D0 / D1 natively). Where the link pairs V with a native
vtable shorter than the exe's (decomp shell classes), the copy is padded with `__s13_vtpad_<V>_<k>` stubs: a call
through one is `vptr-unpaired` (soft, S2c). A differing call that is neither a direct target of the oracle code
nor a planted slot went through memory the case did not plant (`vcall: unplanted`): `oracle-vcall`, an oracle gap.
The farms fuzz functions with indirect calls and jumps only with the exe (`not_modelled_indirect` without it);
tail jumps and in-function switch tables (relocated with the oracle code) are fuzzed always. Shard 26 on link
aedcf24: targets / functions with a native object 64.4 % -> 98.4 % (21 functions with unresolved relocations
remain); of 110 newly fuzzed functions 106 have a verdict (16 hard, 6 soft, 19 oracle gap).

**Vtable pairs (`vtmodel.py pairs`).** A kind-V pair is short when the native vtable has fewer slots than the exe's
vftable (an exact Itanium pair has as many, or one more with a virtual destructor); the harness's copy always
carries spare `__s13_vtpad` slots up to the exe's count + 2, which J-0145 counted as short (1,082 of 1,628).
Link c0d6c71516f6: primary 1,870 paired, short 1,380, exact 449, longer 41; secondary 515: short 330. Of the 1,380
short primary pairs, the RTTI class's own `_ZTV` is defined in no linked object for 765, also short for 122,
already the row's vtable for 103, long enough for 31 (e.g. MagicItemObject: row `_ZTV12FUN_00402b40` with 1 slot,
`_ZTV15MagicItemObject` 99 of 97), no RTTI name 359. Pairing to the static class's `_ZTV` alone therefore closes
~2 %; the padding stays (a call through it is `vptr-unpaired`) until S2c's link gives those classes complete
vtables. Verdicts are unchanged (the count is only reported). Test: `tests/s13/test_vtpairs.py`.

**S1b renames (J-0141, harness still difffuzz-13).** `patches/units_renames.tsv` rows of the units whose prepared
source carries an S1b patch (`config.units_renames`, fnvlink's `load_renames`; `FNVNX_UNITS_RENAMES=<tsv|none>`)
apply on top of the tree's units.json (`engine.apply_renames`): a `def` row gives the patched definition its new
MSVC name, which places the arguments on both machines (0x00eacd00 `bool __stdcall f(void*)`, made a member that
passes ecx on: `this:P/4, p0:P/4` on the oracle too), and a `ref` row is the patched caller's own declaration of
a callee (`caller_conv`: canned results by declaration). Prepare's POST hook already rewrites the farm trees'
units.json (all 239 `def` rows of the 254 patched units of tree efb9808e7d542f00), so this matters for a tree
prepared without it or a local `FNVNX_TREE`, and for the `ref` rows. **Cache:** a unit's applicable rows go into
its functions' keys (`fnkeys.py`), so only those units are fuzzed again: 9,669 of 68,667 functions (254 units)
on that tree, no HARNESS bump; a later change of a unit's rows refreshes that unit only. 0x00eacd00 and 0x00a89b00
(S1b's two XINC1 call-args artefacts) are ok on link c0d6c71516f6. Test: `tests/s13/test_renames.py`.

**difffuzz-14: scoped harness changes (fnkeys.CHANGES, SCOPED = d14; HARNESS stays difffuzz-13).** A harness
change whose effect is confined to identifiable functions no longer bumps HARNESS (a full refresh of all 66,141
keyed functions). It is a `CHANGES` row that says in words what it invalidates and decides it with `selects` (static
facts: the target's MSVC name, the original addresses its oracle code calls through the same-unit functions it runs
for real, the names and absolute addresses its native code and what it reaches in its object refer to: the key of
a selected function gets the change's tag) and `recheck` (a saved divergence it explains differently: fuzzcache.py
`todo` sends that verdict back once; rows record the `changes` they were fuzzed under). `fnkeys.py explain
<targets> <catalog> [<cache>...]` counts them; the farms stop for a newer HARNESS or SCOPED on the integration head.
On link c0d6c71516f6 (all 26 caches):
| change | what it does | invalidates |
|---|---|---|
| d14-dtor | an MSVC deleting destructor target `??_G` / `??_E` (this, flags) runs with flags = 1 only (`runner.deleting_flags`: the Itanium D0 it stands for always deletes; flags 0 has no native counterpart), and its return value (this vs void) is not compared, so the stores are (S1-fuzzq: 41 `falloff?` records "x86 0 calls, native 1") | 103 keys |
| d14-noreturn | `_CxxThrowException` 0x00ec7894, `std::_Xlen` 0x00ec30b0, `exit`, and the imports ExitProcess/ExitThread/RaiseException... on the oracle; `__cxa_throw`, `__cxa_rethrow`, `fnvnx_cxx_throw_exception`, `__cxa_throw_bad_array_new_length`, `std::__throw_*`, `__cxa_pure_virtual`, `abort`, `exit`... natively (or a code stub of a listed CRT address): the call is traced, then the run ends on that machine (`Result.noreturn`; result and callee-saved registers not compared, earlier stores are). The native throws map to 0x00ec7894 (compared by target only); `__cxa_allocate_exception` is a builtin returning a private block (MSVC builds the object in its stack frame; neither is compared). Before, the canned return ran into GCC's udf after the call (exc:1: 00b43490, 00a63ae0, 00e89460) | 555 keys |
| d14-math | the CRT math as builtins with C99 / IEEE results on both machines is S1-fuzzB's `libcalls.CRT_MATH` / `crt_math` (37b1157: log10 no longer through `fyl2x`, which Unicorn leaves undone for x <= 0, so log10(0) gave 0.0: S1-fuzzq 0082ffd0); this change adds the CRT addresses of docs/RUNTIME.md beside the link's provider rows and the `_CIpow/_CIfmod/_CIexp/_CIatan/_CIasin/_CIacos/_CItan` x87-stack forms, run through a stub that spills ST1/ST0 into a cdecl call of the same builtin. Of the 54 cached records whose traces touch these, 36 no longer reproduce, 18 diverge later | 317 keys |
| d14-vslot | a native call of a vtable slot the link filled with its trap (`fnvnx_vslot_trap` brk #0xf5, `fnvnx_special_trap` #0xf6, via `__fnvnx_vtrap_<X>`, which loads x1 = "virtual function defined nowhere: <name> (slot k of <_ZTV>)") is the error `trap:<stub>:<text>` and the cause `native-undefined` (classify.py), not S1's native-trap: S1-fuzzq's "exc:7 at shared PCs" (0x0a069914 in 54 functions of highprocess.cpp) are each unit's trap stub | 175 cached records rechecked |

**difffuzz-15 (d15-vcall, SCOPED = d15).** The largest artifact class, `oracle-vcall` (2,439 divergences in 2,195
functions over the 26 caches), had two harness causes. (1) An intercepted x86 callee of unknown convention (a call
through a pointer the case filled: no oracle code, no declaration) popped 0 bytes; where the MSVC caller does not
clean up (stdcall/thiscall callee), esp stayed off by the arguments and the oracle returned to an argument word
(`fault:x:00000000`; e.g. 00cf75f0: `allocator->vtbl[4](p, n)` twice). `Pair._learn_pop` now searches, for up to 3
such call sites jointly, the pushes before each site (most first, then 0) for a clean run: no fault, a balanced
stack at the target's return (`Result.esp_ok`), every intercepted call returning into loaded oracle code (a wrong
count returns to an argument word, which then reads as a wild call); the counts are kept per call site for later
cases. (2) An oracle call through memory the case did not plant (neither a direct target of the oracle code nor a
planted vftable slot) ended the comparison; its callee is unknown and each machine reads its own slot layout from
the fuzz bytes, so it now matches whatever the native calls at that index (both get the same canned result) and the
later calls and the rest are compared. A planted slot or a direct target is compared as before. On a random sample
of 120 cached oracle-vcall records (link c0d6c71516f6): 65 no longer reproduce (5 before), 31 still end on the oracle
(15 reads, 11 executes, 5 unplanted call-targets after an oracle fault), 24 now reach an ordinary comparison
(call-target 9, mem-write 6, call-count 5, ...). d15-vcall selects the functions whose oracle closure makes an
indirect call: 14,448 of 66,141 keys. Test: `tests/s13/test_d15.py` (9 checks).

**difffuzz-16 (thread-local variables; catalog version 2).** The 835 functions with a native object the harness
did not fuzz on link c0d6c71516f6 (coverage 98.76 %) all had SECREL relocations, 1,994 of them to 32 MSVC
`__declspec(thread)` variables (memory contexts, allocator tags, per-thread script data). units.json gives those
variables image addresses in the exe's .tls template, which starts at 0x01272000 (`?__tls_index@@3PAEA`), so a SECREL
is the variable's offset from there (`coffobj.TLS_IMAGE_START`). On the oracle, fs:[0x2c] (`__tls_array`) points to a
slot array in the TEB page whose slot 0 is a zeroed private block (`x86emu.TLS_*`, not compared, like the native
`tpidr_el0` block), and `__tls_index` (0x0126fd98) is pinned to 0. Catalog rows carry `cv`; fuzz_job rebuilds a
cached catalog of an older version (about a minute). 40 such functions fuzzed locally: all 40 get a verdict, 18
without a divergence; the divergences are ordinary causes (TlsGetValue/TlsAlloc call shapes among them), none in the
TLS block. A function whose closure reaches one of them gets a new oracle hash (it runs that callee for real now), so
keys refresh without a CHANGES row. Test: `tests/s13/test_d16.py`.

**difffuzz-17 (d17-statics, SCOPED = d17).** `local-static` (599 artifact divergences, 298 functions) came from
comparing a function-local static only on the oracle: MSVC keeps it at an image address with a guard bit word, GCC as
`_ZZ<function>E<len><name>` in the unit's .bss with a guard byte, never compared, and with a random background the
oracle's guard was often already set while the native one was not. `engine.static_pairs` pairs a static by its
function (the native names at the function's entry; the function's address from units.json `functions`) and its
name; the oracle's value and its MSVC guard word are pinned to 0 (GCC's .bss starts zero), the native stores into
the pair count as stores at the oracle address (`Arm64Engine._on_static_write`), its reads are no longer private
reads, and the guards (an implementation detail on both sides) are not compared (`compare(ignore=...)`). Unpaired
statics keep the soft `local-static`. Random sample of 60 cached local-static records: 41 gone, 3 still
local-static, 16 reach a later comparison (several are initialisation differences: the oracle's static has a
constant initialiser where GCC runs a guarded one and registers an atexit destructor). d17-statics selects the
functions whose oracle closure refers to a local static or guard, or whose native code names one: 3,713 keys.
Test: `tests/s13/test_d17.py`.

**difffuzz-18 (d18-uninit, SCOPED = d18).** `uninit-both` (578 artifact divergences) is the probe's verdict that
both machines' results change with the planted garbage: both read uninitialised state (the decomp's own reads MSVC
also makes), but each from its own frame layout, so the two read different garbage. The probe now runs the case once
more with `world.UNIFORM`: every planted garbage word is 0 on both machines (stack fill, non-argument and clobbered
registers). `uniform: agree` means nothing else differs: fuzz.py keeps no record of it (`uninit_dropped` counts them;
after 3 of one kind that kind is not probed again for the function) and goes on fuzzing; otherwise `uniform` names
the divergence that remains and the record stays uninit-both. Random sample of 40 cached records (triage on link
c0d6c71516f6): 17 agree, 18 still differ, 4 gone, 1 call-flow; 8 of the agreeing functions fuzzed again: all 8 with no
divergence. d18-uninit rechecks every cached uninit-both verdict once. Test: `tests/s13/test_d18.py`.

**difffuzz-19: a full refresh (HARNESS = difffuzz-19, orchestrator-approved in S1-fuzzB-002).** S1-fuzzB's untyped
float results (`x86emu.x87_result_use`), its CRT_MATH builtins and engine's preference for a decorated spelling
change verdicts that no key reflects, so every key changes once; the scoped rows d14..d18 are folded into it (their
`changes` tags still go into the rows, so later partial refreshes keep working). It also carries three changes:
- a call nothing describes (neither the definition nor the calling unit's declaration: wild and unplanted indirect
  calls) gets a canned result whose low byte is 0/1 on both machines (`world.canned_kind` -> `untyped`): the call
  site may test it as a bool, MSVC `al`, GCC bit 0 (S1-fuzzA: 17 native-null-read records; S13b's hypothesis for
  the 413 native-early-exit records at indirect calls through fuzzed memory);
- the native entry of an override of a secondary base's virtual is the link's `__fnvnx_msvc_<addr>` (GCC's
  `_ZThn<d>_` thunk; S2c thisadj, 79 addresses on link c0d6c71516f6), where MSVC enters it with `this` at that base;
- a call through a `__s13_vtpad_` slot is `vptr-unpaired` before the `unplanted` test makes it `oracle-vcall`
  (S13b);
- an intercepted constructor `??0`, or a member function returning a pointer or reference to its own class,
  returns `this` on both machines (`world.returns_this`; before, only the oracle's constructors did, and the
  native got a canned word: S13b ctor-this 79, canned-this 32). Random sample of 40 of those 119 cached records:
  26 gone; most of the rest are results through a hidden pointer of callees no declaration describes;
- the parts of each machine's private range it never uses are plain background there, as on the other machine,
  and a jump there is a wild call (`world.A64_HOLES` / `X86_HOLES`, and per unit image `Arm64Engine.holes`: code
  stubs outside .text, named stubs above the last one assigned, the space after the image). The same garbage
  address faulted on one machine only (S13b native-wild-private 52, oracle-private-address 6; S1-fuzzB
  native-private-address 5). Of S1-fuzzB's 7 records, 5 are gone. Used stub pages still fault as data, so
  native-stub-data (S2c) stays visible;
- a fault on one machine is compared after the calls both made before it: a differing call in the common prefix is
  reported first (`runner.compare`). A native call through a vtpad slot where the oracle called the exe's slot and
  then faulted on the canned result was an oracle-side error (oracle-vcall), and is now the call-target that
  classify files as vptr-unpaired (S2c). A fresh vtable-copy stub for a slot function another object defines is
  that function's original address (`vtmodel.native_addrs`, the link's addrmap F rows), not an anonymous stub.
  120-record oracle-vcall sample with all of difffuzz-19: 70 gone, oracle-side errors 26 -> 17, 5 vtpad
  call-targets.
**Probe-cause audit (S13b s13b-002, V2 L1; difffuzz-19).** A probe-assigned artifact or by-design cause rests on
evidence in the record or a replay, else the record goes on to the other probes:
- `local-static` only for a mem-write whose differing bytes are all oracle-only stores to the unit's local statics,
  or one extra native call followed by the oracle's call (the native's guarded initializer; not when the oracle's
  next call is the native's: two calls swapped) - the runner's own `local-static` kind covers the rest; the native's
  local-static read flag alone filed 344 call/error records (V2 L1: 0x00837550's swapped call pair; C1 the
  initializer control, tests/v2/test_s2c_local_static.py: both ok);
- `float->int` only when an aligned x86 word is 0x80000000 and the native word differs (was a substring test);
- `vptr-store` only in a destructor (`classify.is_dtor_name`), elsewhere `vptr-differs` (triage, S2c);
- `x86-custom-cc`: the case's own garbage probe (above); report.py's static entry-register override is gone;
- compare no longer stops at by-design differences: trailing oracle-only frees of a function taking an aggregate by
  value (MSVC destroys it in the callee: `byvalue-arg-dtor`), and a call where both machines branched to the same
  garbage word that lies in the native's stub range (`vcall-stub-alias`, `arm64emu.Call.addr`). The deleting
  destructor shapes (flags, return) stopped compare before d14; they no longer do.
- `call-order`, `bool-repr`, `uninit-*` and `array-new-check` rest on the record (two adjacent calls swapped in
  both traces; a replay; the native's own overflow throw), `msvc-inlined` on the oracle code (adaptive follow loads
  the callee whenever its oracle code exists; what stays is the case without it).
Dynamic evidence for causes.py: every divergence carries `arm_entered`, the original addresses of the unit's
functions whose native entry the run executed (up to 64; S13b's oracle-callee-unloaded can rest on it instead of a
traced call the callee makes).
Test: `tests/s13/test_audit.py`.

Test: `tests/s13/test_d19.py`.

Test: `tests/s13/test_d14.py` (30 checks). Metric: `tools/difffuzz/metric.py` (artifact-class divergences + functions not fuzzable + unpublished shards, from reports/fz<k>, the newest layout only).

**difffuzz-12 (J-0138). The game allocators (operator new 0x00401000, 0x00aa3e40, 0x00aa13e0, and the native
names libcalls.EQUIV maps to them) return a fresh heap block (`world.alloc_result`, HEAP + 2 MiB + 4 KiB k), never
the canned 0: GCC drops the null test after a throwing new that MSVC keeps (S13b `new-null`, 178 farm records). A
machine's own private range faults when unmapped; the other machine's is plain background, so a garbage pointer
there no longer faults on the oracle only (S13b `oracle-native-address`, 62 records). Of 60 of S13b's reproducers
replayed on link aedcf24, 43 no longer diverge (28 before), the rest diverge later. Bounds: each machine's run stops
after `FNVNX_DF_RUN_S` (2 s, Unicorn's emu_start timeout; one farm run of 004b6220 took 48 min, the step count did
not bound it), each function after `fuzz.fn_limit` (6 x --time + 30 s, at least 60; `FNVNX_DF_FN_S`), and --time
starts after the setup. A function out of its bound keeps its executions as the verdict (`truncated`), or with none
is `run:slow` (a harness failure, fuzzed again after everything else).

**difffuzz-11.** CRT addresses the link gives a provider whose name is a harness builtin (every address of the
name: memmove 0x00ec44d0 and 0x00ec7230, ...) run as that builtin on the oracle, untraced like the native call.
Adaptive follow also answers a call-count divergence: the first oracle callee the native run never calls was inlined
natively and is loaded. Report causes: `native-undefined` (hard, S1 / S2c: the native code calls a name the link
binds to nothing, e.g. a declared-only destructor `_ZN15Entry2_00832010D1Ev` or `NiPointer<NiGeometry>::operator
NiGeometry*`), `vptr-unpaired` (soft, S2c: the oracle stores an image vftable, the native an address point of its
own vtable that no kind-V row pairs, decomp-local class names such as `Primary_504570`, `FUN_00591200_cls`).

**difffuzz-10.** `_byteswap_ulong` (0x00ec3bad) and `__RTDynamicCast` (0x00ec43fb) run as builtins on the oracle,
`fnvnx_byteswap_ulong` / `fnvnx_rt_dynamic_cast` / `__dynamic_cast` natively (a cast succeeds: the object
pointer; GCC folds the casts whose type it knows). Call arguments the caller did not push are not compared: the
x86 caller's cleanup after the call (`add esp, N`, `pop ecx`) or the callee's `ret N` gives the bytes pushed, and a
stack parameter of the definition beyond them is a read past the caller's arguments (FUN_005accb0's callers push 8
words; ExtractArgs reads a ninth only when the fuzzed script data declares more parameters than the command).

**Native runtime state (difffuzz-8).** The AArch64 machine has a zeroed thread-local block at `tpidr_el0`
(0x0b900000, private, not compared): the platform's `thread_local` state read as `[tpidr_el0 + off]` faulted at
small addresses before. A native indirect call through the value of an IAT slot (pinned to the x86 import stub)
is that import, as on x86.

**Wild calls.** A jump to memory without code on either machine (an indirect call through a pointer the case
planted, e.g. a vtable slot of a fuzzed object) is an intercepted call of that address on both machines
(`world.WILD_X86` / `WILD_A64`), never an execution of the world's bytes.

**Float results of untyped calls and CRT math (S1-fuzzB, after difffuzz-13; verdicts refreshed by difffuzz-19).** An intercepted call whose callee
nothing types (a wild or unplanted indirect call, a CRT name with only a C spelling in the unit's symbols) always
leaves the canned bytes in v0 natively; the oracle now gets the same value in ST0 when the code the call returns to
consumes ST0 (`x86emu.x87_result_use`: fst/fstp m32 -> f32, m64 -> f64, x87 arithmetic; the caller's cleanup and
epilogue are stepped over, and a `ret` takes the fuzzed function's own result kind). Before, the oracle read an
empty x87 stack (0.0) and the native the canned bytes: 12 of the 21 actor.cpp float divergences. A unit's symbols
prefer a decorated spelling for an address (`?FUN_00eca920@@YANN@Z` over `_FUN_00eca920`). The CRT's double
functions the link gives libm providers (log, log10, exp, pow, atan2, tan, atan, asin, acos, fmod, floor, ceil,
sin, cos: `libcalls.CRT_MATH`) run as builtins on both machines with C99 results at the domain edges (log(0) =
-inf, log(-1) = NaN, pow poles and overflow, signed zeros of floor/ceil); the x87 `fyl2x` stub for log10 gave 0.0
for log10(0) under Unicorn. Replayed on link c0d6c71516f6: float records 51 -> 20 still diverging (2 gone before).

## Cause refinement and verdict policy (`causes.py`, S13b)

`report.reclassify` hands what the probes left at `unclassified`, `call-flow` or `signedness?` to `causes.refine`,
rules on the saved record alone (no replay; a probe's definite cause is never overridden; `oracle-vcall`, a tag of
the runner and a fault probe rather than a decision, is split too, `causes.HINT_REFINABLE`):

| record shape | cause | class |
|---|---|---|
| native `exc:N` (brk/udf), oracle returns | `native-trap` | real-bug S1 (FALL1 trap / GCC unreachable on a path the exe takes) |
| ... and the instruction at that pc of the unit image (`FNVNX_CAUSES_A64`, default the run's a64 directory) is the link's `fnvnx_vslot_trap` / `fnvnx_special_trap` (`brk #0xf5` / `#0xf6`) | `vslot-trap` | real-bug S2c (a virtual defined nowhere; the exe vftable has a function in that slot) |
| native `stack-protector` | `stack-protector` | real-bug S1 |
| native data fault below 64 KiB, oracle fine | `native-null-read` | real-bug S1 |
| ... and the native function calls a game allocator (operator new 0x00401000, 0x00aa3e40, 0x00aa13e0; from `fixed/<unit>.o` or `FNVNX_CAUSES_OBJ`) and the case holds a canned result below 0x10000 (an allocator null; since difffuzz-12 allocators return heap blocks) | `new-null` | retired (S13b audit): since difffuzz-12 the allocators return heap blocks, so a canned 0 is another call's valid result and the native null access is `native-null-read` (real bug S1) |
| native data access in the stub range; stub address passed/stored | `native-stub-data` | real-bug S2c |
| native image address where the oracle has an image-window one (arg, result, store) | `native-address` | real-bug S2c (unrebound variable/table; check the pointee for literals) |
| call argument the runner marked `arm_garbage` | `native-garbage-arg` | real-bug S1b |
| `sret` argument differs | `sret-mismatch` | real-bug S1b |
| float argument within tolerance (both values normal, same sign) / NaN on both with one side the machine's generated NaN (x87 0xffc00000, AArch64 0x7fc00000) | `x87-precision` / `nan-bits` | by-design (0.0 vs a denormal, +0 vs -0, NaN-shaped ints stay open: S13b audit) |
| float result equal to the oracle's unrounded x87 value, which differs from its rounded one | `x87-precision` | by-design |
| pointer to a pinned-pool literal (0x02f00000) vs the native .rodata copy: argument / result / store | `literal-arg` / `literal-ret` / `literal-store` | literal-ret: artifact (only a pool literal vs a native image address whose x86 pointee is unreadable); literal-arg / literal-store: triage S13b (the strings differ, or stores never compare the pointees: a wrong literal looks the same; S13b audit). A native return of a named-stub address is `native-stub-data` (S2c) |
| both sides store image vftables (link `out/addrmap.tsv` kind V, or `FNVNX_CAUSES_ADDRMAP`), different ones | `vptr-differs` | triage S2c (in a destructor too: that the two vftables share the class chain needs the exe's RTTI, S13b audit) |
| result of a `??_G` / `??_E` deleting destructor (MSVC returns this, Itanium D0 is void) | `deleting-dtor-ret` | triage S13 (the difference is by design, but runner.compare stops there and never compares the destructor body; until the harness skips that return) |
| call-count of a `??_G` fuzzed with flags bit 0 clear: the native makes the oracle's calls plus deallocator calls only (D0 always frees) | `deleting-dtor-flags` (report.py, S1-fuzzB) | triage S13 (by design, but the difference hides the destructor body; see the causes.py row) |
| call-count, the oracle makes no call, the native's first / a later call is a named stub | `native-undefined` / `after-native-undefined` | real-bug S2c/S2d (was `falloff?`) |
| call-count, the oracle makes no call, the native calls addresses only | `falloff?` | real-bug S1 |
| probe `signedness` on a by-design shape (the top bit is a float sign, a bool byte, INT_MIN, the top of the address space): float argument within tolerance / bool result 1 vs another nonzero byte / x86 #DE / native access across 4 GiB | `x87-precision` / `bool-repr` / `division` / `ilp32-pointer` | by-design |
| pointer result with an unreadable pointee on one side, the oracle a pool literal and the native an image address | `literal-ret` | artifact (any other pointer result stays open; S13b audit) |
| oracle `exc:3` (0xCC filler) | `oracle-trap` | artifact |
| `no-native-entry` | `no-native-entry` | artifact only when the address map names another unit's object defining it (the image subset left it out); otherwise `native-undefined` (real bug S1, S13b audit) |
| oracle-only data fault in the AArch64 private range | `oracle-native-address` |  triage S13b (S13b audit: no record evidence separates the harness from a real bug) |
| oracle-only data fault | `oracle-fault` | triage |
| oracle-only fault (`oracle-fault`, or an `oracle-vcall` error) at a garbage address in the x86 private range 0x0e000000 | `oracle-private-address` |  triage S13b (S13b audit: no record evidence separates the harness from a real bug) |
| ... a dword access at 0xfffffffd..0xffffffff, oracle only | `oracle-fault` | triage (the native may never have made the access; S13b audit) |
| probe `ilp32-pointer` (the native touched >= 4 GiB, the oracle did not fault) where the address mod 2^32 is memory the oracle maps (image, pool, case buffers, heap, stack) | `ilp32-wrap` | real-bug S1 (a base plus a wrapping 32-bit index: GCC ILP32 forms the address in 64 bits on an integer-constant base, tests/s13/ilp32_wrap); near null or unmapped stays `ilp32-pointer` |
| ... and the native function calls a function its own object defines as a bare `ret` (decomp stub, empty destructor) | `native-empty-callee` | real-bug S1 (the exe's work there is skipped natively) |
| `oracle-vcall` call-target (`vcall: unplanted`): the native trace passes a `__s13_vtpad_` slot | `vptr-unpaired` | real-bug S2c, soft (classify.py tests `unplanted` first) |
| ... the oracle calls a garbage word in the AArch64 stub range, the native trace names that word (word - 0x08000000, or `name:<hex>`) | `vcall-stub-alias` |  triage S13 (S13b audit: no record evidence separates the harness from a real bug) |
| ... the native calls a C++ name nothing defines or binds, no vptr planted | `native-undefined` | real-bug S1 |
| ... neither target is code on either machine (not .text, an import, a named stub or the AArch64 private range) | `vcall-garbage` |  triage S13b (S13b audit: no record evidence separates the harness from a real bug) |
| ... anything else (the native calls an exe function directly while the oracle reads unplanted memory; oracle-only faults near an indirect call or at an unmapped pc) | `oracle-vcall` | artifact (kept) |
| call-target: native calls a named stub `name:_Z...` | `native-undefined` | real-bug S1/S2c (fallback without the link's providers) |
| call-target: oracle calls an import, native jumps to the IAT slot address | `import-slot-called` | real-bug S2c |
| call-target: the native function never names the oracle's callee (from its object) | `native-inlined` | triage |
| one side's store covers bytes the other leaves | `store-width` / `x86-only-store` / `native-only-store` | triage |
| native calls a prefix of the oracle's calls / the oracle's calls and more | `native-early-exit` / `native-extra-calls` | triage |
| only for records that would stay `unclassified`: one side has the block a game allocator returned (`world.alloc_result(j)`), the other the canned result of a later call `m > j` (argument, result or stored word) | `ctor-this` | triage S13b (harness artefact when the later call is the constructor on the block, real bug when the native drops a factory/Init result: the record names neither; V1 A2) |
| pointer argument: the oracle's is the canned eax of an earlier intercepted call, the native's is in its own stack frame | `canned-this` | triage S13b (a callee returning its result pointer, or a by-value declaration in the native: the record lacks the earlier call's arguments; V1 A3) |
| `this`/pointer argument `d` bytes below the first buffer on the oracle side (pinned-pool range) and the buffer on the native side, the native object has a `_ZThn<d>_` thunk for the function or a direct callee | `thunk-binding` | real-bug S2c (`__fnv_<addr>` bound to the GCC primary entry, not the secondary-base thunk the MSVC entry corresponds to); without the thunk `layout-offset` |
| call argument (I32/F32/F64) the native passes equal to the canned result of the call just before, the oracle another value | `native-stale-result` | real-bug S1b (an argument the native caller never set: its declaration of the callee differs) |
| native `fault:x` in the AArch64 private range, oracle fine, case background `rand` | `native-wild-private` | triage S13b (a random code pointer, or a wrong vtable slot: indistinguishable in the record; V1 A4) |
| first differing store: every differing byte in a 4-byte window where the oracle word w is in [0x08000000, 0x09000000) and the native word is exactly w - 0x08000000 (`causes._stub_rewrite`) | `stub-normalized` | artifact (arm64emu `_codeptr` rewrites any such stored word, data included; a 0x08 flag byte elsewhere stays open, V1 A1) |
| stored / returned word that is one of the planted garbage words (`world.garbage_words`): both / native / oracle | `uninit-both` / `uninit-arm` / `uninit-x86` | artifact / real-bug S1b / by-design |
| native 0 where the oracle has a value; the native function or a direct callee in its unit is `mov w0, #0; ret` | `native-zero-stub` | real-bug S1 (a placeholder body) |
| call flow, the native function's own body is `ret` / return-a-constant (or it calls such a decomp-local `..FUN_<X>..` stand-in for an original X another object defines) and the oracle makes calls | `native-empty-body` | real-bug S1 |
| the oracle's first missing call is CRT `_atexit` (0x00ec658f), the native function names no `fnvnx_xc_atexit` / `atexit` | `dyninit-no-atexit` | real-bug S10 (soft: the destructor is lost at exit only) |
| `??_G` / `??_E` called with flags bit 0 clear; the native (D0) makes the oracle's calls plus deallocator calls only | `deleting-dtor-flags` | triage S13 (by design, but the call-count difference hides the destructor body; until the harness runs D1 for flags 0) |
| the function takes a class by value (`AGG:` parameter); the oracle's calls after the native stops are deallocators only | `byvalue-arg-dtor` | triage S13 (by design only for a non-trivial native destructor, and the difference hides the return and writes; until the harness drops the trailing argument frees) |
| call-target: the native calls an image-window (data) address where the oracle calls a function | `native-calls-data` | real-bug S1 (virtual slot past a decomp class's shorter vtable) |
| call-target: oracle an unnamed CRT entry (addrmap E, x87 `_CI*`) or `f`, native the libm (float) function; `sprintf` vs `__sprintf_chk`; `atof` vs `strtod` | `libc-variant` | artifact; for an unnamed CRT entry only the matching C function or its float form by the table causes.CI_ENTRIES (_CIacos vs cos is another function, S13b audit) |
| the oracle's callee X is defined by the native function's own object and called by it (runs natively, intercepted on x86), **and** the native's calls from the divergence on include a call X reaches that leaves the unit and the caller does not make itself (`causes.native_ran`: evidence the native ran X) | `oracle-callee-unloaded` | artifact (X not in the oracle's code set); never over an unbound name; an early exit before X, or X with no traced call, stays `native-early-exit` / `call-flow` (V1 tests/v1/test_s13b_callee_unloaded.py U1/U2); call-target only, and the evidence call must not be reachable through another unit-local callee (S13b audit U4) |
| the oracle frees (free / delete / Deallocate), the native goes straight on to the oracle's next call (or returns) and reaches no deallocator through its own object | `native-missing-free` | real-bug S1 |
| call-target: native indirect call to a non-code address where the oracle calls a .text function | `native-vcall-oracle-direct` | triage |

With `summary --effects <difffuzz.py effects output>`, a record still in triage is refined by its first effect:
`width` -> `field-width` (real-bug S1), an image vftable stored where the native stores its own `_ZTV` address
point -> `vptr-unpaired` (S2c), a native named stub stored or passed as data -> `native-stub-data` (S2c). A first differing store made only by the oracle at a pc
outside the function (a callee the oracle runs, the native calls out of line and the harness intercepts) ->
`oracle-callee-store` (artifact), only with evidence of the interception: the pc lies in a function of the address
map (`causes.function_at`) that the native calls out of line through `__fnv_<addr>` or its link name and that its own
object does not define (the unit's own functions run natively). Never for `native-inlined`, `native-early-exit` or
`x86-only-store` (`causes.NOT_INTERCEPTED`: the native never made the call or ran the callee's code itself; V1
tests/v1/test_s13b_callee_store.py C1-C3); without the object or the map the record stays in triage. The record must also be a call-count / call-target record whose native trace holds that callee more often than the oracle's (the oracle runs it untraced, and calls are compared before writes: any other kind means the native never made the call; S13b audit C5). A triage record in a function that calls a C++ name neither its object defines nor
the address map binds -> `after-native-undefined` (real-bug S1: the native side got a canned result).

`causes.POLICY` gives every cause a class (by-design, artifact, real-bug, triage), an owner and a reason;
`report.py` takes its by-design causes as soft and its artefacts as oracle gaps (`uninit-both` moves from hard to
oracle gap: both machines read garbage). `python3 causes.py summary <dirs> [--recheck-tag t] --out causes.json`
writes `fnvnx-causes/1`: per cause divergences, functions, functions whose worst cause it is (`worst_of`), kinds,
five example reproducers, plus `functions_by_class` (each diverging function by its worst class, real-bug first)
for R0. `causes.py merge <reports/fz<k> .md | causes.json>... --out all.json` adds the shards up
(classes from this checkout's policy). Input layout (any campaign or `fuzz/cache/<k>of<N>` checkout): `results.jsonl`
(address, source, status; only `ok` rows count), `div/<addr>-<n>.json` (divergence, hint, case), optional
`recheck[_<tag>].jsonl`. `causes.py explain <div.json>...` prints hint, refined cause and class. `causes.py handoffs <dirs> --out-dir d [--effects e.jsonl]` writes the owners' lists, one row per counted divergence (owner, cause, address, unit, reproducer `fuzz/cache/<k>of<N>:div/<file>`, first effect, fix class): `handoffs_<owner>.tsv` (real-bug), `triage_<owner>.tsv`, `harness_<owner>.tsv` (artifacts with an owner) and `index.tsv`. With `--ref-by-head` the reproducer is `fuzz/<head12>/<k>of<N>:div/<file>`, the branch of the head that fuzzed the function (results `commit`), which the farms never rewrite (the rolling `fuzz/cache/` branches are force-updated on every re-fuzz); they go to the private repository, never here. The object rules need pyelftools: without it the summary's `native_objects.reader` is `unavailable` and a warning is printed. Test: `tests/s13/test_causes.py`.

### Fix classes (D21)

Every real-bug cause belongs to exactly one fix class (`causes.FIX_CLASSES`). A class is one general mechanism, and its owner closes it for all instances at once, not record by record. The cause's owner is the class owner. `summary` and `merge` add `fix_classes` (per class: divergences, functions, worst_of), and `causes.py classes <causes.json> [--tsv f]` prints the table, largest worst_of first. Test: `test_fix_classes`.

| class | owner | causes | mechanism |
|---|---|---|---|
| `callee-bodies` | S1 | `native-undefined`, `after-native-undefined`, `native-empty-body`, `native-empty-callee`, `native-zero-stub` | every call from a prepared unit reaches the original function's real body: emit all inline/template/declared-only bodies, no decomp-local stand-in (empty or return-0) shadows an original function |
| `object-layout` | S1 | `native-null-read`, `layout-offset`, `field-width`, `signedness` | every field is read at the exe's offset, width and signedness (struct layouts and member types from the exe, base subobject placement) |
| `control-flow` | S1 | `native-trap`, `falloff?`, `stack-protector` | no path the exe takes ends in a trap, a fall-off or a stack-protector abort (no UB GCC exploits: missing returns, unreachable, out-of-bounds locals) |
| `arithmetic` | S1 | `float`, `ilp32-wrap` | integer and float arithmetic as MSVC x86 computes it: 32-bit address wraparound (ilp32-wrap), float results beyond x87 tolerance |
| `ownership` | S1 | `native-missing-free` | the native frees exactly what the exe frees |
| `static-init` | S10 | `dyninit-no-atexit` | every dynamic initializer registers its destructor as the exe does (atexit) |
| `call-abi` | S1b | `uninit-arm`, `native-garbage-arg`, `native-stale-result`, `callee-saved`, `sret-mismatch` | every call passes and preserves registers by the callee's MSVC convention (argument slots written, results not reused, callee-saved kept, sret as MSVC) |
| `data-binding` | S2c | `native-stub-data`, `native-address`, `split-global`, `import-slot-called`, `call-args` | every data symbol and import binds to the exe object (no stub as data, no unit copy, no native address where the exe has an image address, bridges deliver the bytes) |
| `vtables` | S2c | `vptr-unpaired`, `native-calls-data`, `thunk-binding`, `vslot-trap` | every image vftable pairs with a full native vtable of the static class (no short vtables, overrides bound to their this-adjusting thunks) |

## Lockstep (`lockstep.py`, D14)

`difffuzz.py lockstep <addr> [--depth N]` runs a call tree once on both machines: the units of the functions reachable
from the root through direct oracle calls (at most N, each with an MSVC and a native object) are all loaded (x86: every
function at its address; AArch64: one image of all their rewritten objects), calls leaving them are intercepted, the
world starts all zero. Both function-entry traces (original addresses) are aligned tolerating inlining (an entry one
side never makes is skipped); the first position where both enter known functions but different ones is reported,
then the usual result comparison. Output `<out>/<addr>.json`.

## Verdicts (`report.py`)

Per stream: S0-compile (body unchanged by prepare), S1-rules:<family> / S1-sse / S8-asm (`attrib.py rules`: the
function's body differs between pristine and prepared source, family from the changed tokens), S2-link (bridged
callers, link causes). Hard vs soft causes; `difffuzz.py recheck <dir>` first replays every saved divergence on the
current engine and the report drops those that no longer reproduce (harness limits fixed since).

## Usage

```sh
cd tools/difffuzz
python3 a64prep.py objects ../../build/s13/link/objects.tsv     # units with a finished AArch64 object
python3 a64prep.py link ../../build/s13/link                     # S2 resolve + apply on that subset
python3 catalog.py                                               # static facts of every oracle function
python3 pick.py --leaf --max-size 200 > leaf.jsonl               # campaign targets
python3 difffuzz.py fuzz leaf.jsonl --out <dir> --iters 150 --time 20 --resume
python3 attrib.py rules rules.json; python3 attrib.py bridges bridges.json
python3 report.py <dir> [...]                                    # per-stream verdict table + catalogue
python3 difffuzz.py one <source> <addr>                          # one zero-world run
python3 difffuzz.py repro --trace <dir>/div/<addr>-0.json
FNVNX_DFPROFILE=<profile> python3 difffuzz.py recheck <dir> --tag f11  # replay on another object set
python3 recheck_summary.py f11 <dir> [...]                       # gone / soft / still per cause
FNVNX_DFPROFILE=<profile> python3 difffuzz.py triage <dir> --tag f11 --only unclassified --reads
FNVNX_DFPROFILE=<profile> python3 difffuzz.py lockstep <dyninit addr> --out <dir>
```

## Farms (D17)

Whole-decomp campaigns run on farm machines: `tools/farm/fuzz_job.sh <commit> <k> <N> <report.md>` (header: inputs,
artifacts branches). Shard = crc32(unit source) mod N. Incremental: `fnkeys.py` gives each function a key (oracle
code and that of the same-unit functions it calls, from `catalog.py`'s `ohash`; the rewritten AArch64 function's
bytes and relocations, targets named by what they resolve to, plus every function of its object it reaches and the
bridges it calls; `fnkeys.HARNESS`, bumped when the harness changes verdicts). `fuzzcache.py` keeps one verdict row
and the reproducers per function on branch `fuzz/cache/<k>of<N>`; a head fuzzes only the functions whose key
changed, then those never fuzzed, and reports both this head's and the cumulative verdicts. The report ends with the
cumulative per-cause table and a ```` ```json fnvnx-causes ```` block (`causes.py summary`; full file `causes_cum.json`
in `fuzz/<c12>/<k>of<N>`), which R0 reads from `reports/fz<k>`.

## Oracle gaps

`x86-custom-cc`: MSVC's whole-program optimisation gave some static functions (many in Havok) a register convention
units.json does not record (e.g. the first pointer in eax). The model above covers a function whose extra registers
carry its declared parameters; one whose oracle outcome still depends on the planted garbage (a register that is
not a parameter of the declaration, a caller-computed value) stays `oracle-gap:custom-cc` and its divergences are
reported apart (neither hard nor soft). A function returning a pointer to a literal compares the pointer, not the
pointee (the native literal has another address): reported under `unclassified`.

## Limits

Indirect calls (vtable calls through fuzzed objects) are not modelled yet (functions with indirect calls are left
out); callee side effects through pointer arguments are not
modelled (canned results only); x87 transcendental helpers beyond the d14-math set are intercepted, not executed.
