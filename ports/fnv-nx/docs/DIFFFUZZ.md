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
0/1 that the oracle reads by itself, a 1-byte access, read as bool), `signedness` (clearing a top bit removes the divergence), `split-global`, `vptr-store`,
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

**d19-bool (S1-rules, SCOPED = d19).** The bool-repr probe set each input byte above 1 to 1 and called the record
bool-repr when the divergence went away. In shard 4 of 06f45fbb, 128 of the 161 bool-repr records had their hit byte
inside a word with other nonzero bytes, mostly the 0x03 top byte of a planted pointer (0x030000ec -> 0x010000ec moves
the pointer, it is not a bool). The probe now first runs the case once with `world.BYTE_READS` (x86emu records each
read of planted memory with its size) and tries only bytes the oracle read with a 1-byte access and no wider access
over them. Other records go on to the next probes. d19-bool rechecks every cached bool-repr verdict once. Test:
`tests/s13/test_boolrepr.py`.
Measured on 8b233f99 (link 9f865b0a4037, all 8 shards): bool-repr 1,424 -> 290 cumulative, hard 6,314 -> 7,212.
bool-repr is soft, so the records that left it count as hard now; they were hidden before. Most of them landed in
signedness (53 -> 252), x86-only-store, store-width and native-stub-data.

**d20-sign (S1-rules, SCOPED = d20).** The signedness probe had the same flaw: on 8b233f99, 434 of its 461
memory hits were bytes inside a word the oracle read whole. A memory byte now counts only when the oracle read it
as the top byte of a narrow access: a 1-byte read of it, or a 2-byte read whose high byte it is, and no wider
access over it. Argument bytes are probed as before. d20-sign rechecks every cached signedness verdict once. Test:
`tests/s13/test_boolrepr.py`.

**d20-ilk (S1-rules).** The shim's inline Interlocked* (fnvnx_win32.h) calls `fnvnx_interlocked_slow(op, p, a,
b)` for a misaligned operand. The fuzzer often plants misaligned pointers. The oracle's `lock` instruction is not a
call, but the native slow path was traced as one: 767 cached call-flow records on 8b233f99 had
`name:fnvnx_interlocked_slow` in the native trace. arm64emu now runs it as the Interlocked function `op` names
(libcalls.INTERLOCKED_SLOW_OPS). d20-ilk selects the functions whose native code names it. Test:
`tests/s13/test_ilkslow.py`.

**d20-malloc (S1-rules).** MSVC units that call the CRT malloc (0x00ecd1c7) got a canned garbage result for it,
while the native's operator new / MemoryManager::Allocate got a fresh heap block (world.ALLOCATORS). malloc was
also missing from the `new` family, although free is in `delete`. On 8b233f99, about 160 live call-flow records had
malloc in the oracle against new / Allocate / a constructor natively. malloc is now an allocator and in the `new`
family. d20-malloc selects the functions whose oracle code calls it. Test: `tests/s13/test_crtmalloc.py`.

**d25-sg (S1-rules, SCOPED = d25).** MSVC numbers a unit's string literals `$SG<n>` by an internal symbol
counter. The oracle objects (msvc/pristine, built with S11's force-included header) count 43 higher in every C++
unit than the build that wrote units.json. synth_image.py already corrected for that; coffobj did not. It looked
object names up unshifted, so each literal was pinned and relocated at a neighbour's address. EQ measured 3,334 of
15,143 pinned literals differing from the exe, and 37 overlapping groups (one of them five `$SG` names at
0x01012058 in inventorychanges.cpp). `coffobj.sg_shift` finds the delta per unit. With the exe it picks the delta
under which the most literals equal the exe's bytes at their units.json address: inventorychanges 68 / 68 at +43
against 1 / 68 at 0, caravanmenu 205 / 205. Without the exe it takes the name delta that maps at least 90 %.
UnitImage then maps object `$SG<n>` to units.json's `$SG<n-d>`. d25-sg selects the functions whose oracle
closure refers to a `$SG` address (catalog data_refs). Test: `tests/s13/test_sgshift.py`.
EQ's count after the shift (all 2,988 units): shift 0 in 2,684, +43 in 299, +1 in 4. Overlaps 37 -> 0. Of the
literals still differing from the exe, EQ's "bin-at-str" rows (91) were read-only tables pinned without their
relocations: listbox.cpp's `??_7FUN_00722880` held 00000000 where the exe has 0x00722b50, its slot 0 (`??_E`). The
tables' DIR32 relocations are now applied (`UnitImage._relocated`): a target the unit places gets its address,
else the exe's word when the exe loads. Measured on five units: listbox 10 -> 0 differing, chargenmenu 2 -> 0,
caravanmenu 2 -> 1, startmenu 6 -> 2. d25-sg also selects functions that refer to a units.json symbol in the exe's
read-only sections.

**d21-f3mem (F3, the memory class; SCOPED = d21).** Four harness artifacts behind cached hard verdicts of
native-null-read, callee-saved, store-width / x86-only-store, native-stub-data and float, found by replaying a random
sample of 30 reproducers per cause on link 0540aff0d154 and reading the traces:
- *builtins mapped the null page.* `libcalls.Mem` mapped any page a CRT builtin touched, so the oracle's
  `memcpy(dst, NULL, 8)` (0x00401460) or `InterlockedDecrement((long*)0x84)` read zeros where GCC's inlined load or
  the shim's `ldxr` faulted (00433b70, 00bb0de0). The real CRT faults there too. A builtin's access now raises
  `libcalls.Fault` where the machine's own load or store would (`x86emu/arm64emu._bmem`: a page mapped already or
  `world.mappable`), and the run ends with that fault on that machine;
- *a lost callee-saved register did not trigger pop learning.* An intercepted call of unknown convention (wild or
  unplanted) that pops its arguments, in an `ebp` frame: `mov esp, ebp` repairs esp, so nothing faulted, but the
  `pop esi/edi/ebx` before it read the argument words (callee-saved x86=False arm=True; 0070f4e0, 00d55bd0).
  `runner.Pair.run` now also learns the pops when the oracle's run is clean except `saved_ok`, and a learnt
  combination must keep the callee-saved registers too (it need not repair an esp difference the run already had);
- *the code-stub rewrite took data for code.* `arm64emu._codeptr` turned any stored word in [0x08000000,
  0x09000000) into its original address, so a canned result 0x0805ddf1 that both machines stored became 0x0005ddf1
  natively (store-width / x86-only-store with an 0x08 top byte: the InitializeRuntimeCallbackStatus* family). Only
  a stub the image binds (`__fnv_<X>` in its symbols) is rewritten now;
- *canned results pointed into a private range.* A canned eax / x0 word in 0x08000000..0x0c000000 (the native
  image, its stubs) or 0x0e000000..0x0e100000 (the oracle's stub pages) read the native image's own bytes on the
  native machine and background on the oracle (00ad7ab0: a float field through such a pointer). `Case.ret_bytes`
  flips bit 30 of such a word (`world.canned_out_of_private`, mirrored in `causes._ret_bytes`), the same on both
  machines.
d21-f3mem rechecks the cached divergences these can explain differently (`fnkeys._f3_mem`: one machine faulted,
callee-saved, a differing word in the code-stub range, a canned result word in a private range).
Test: `tests/s13/test_f3mem.py` (each of the four fails on the harness before it).

**d21: virtual calls and vtables (F2, SCOPED = d21).** A random sample of 240 cached records of the F2 causes
(40 each of vptr-unpaired, vcall-garbage, vcall-stub-alias, deleting-dtor-flags, deleting-dtor-ret, oracle-vcall)
replayed on link 0540aff0d154: 134 still diverge, none of the five triage causes under its own name any more. Three
mechanisms, each fixed as a class:
- **d21-dtorvptr** (47 of the 134). Every level of an MSVC destructor stores its class's image vftable, GCC's D1/D0
  its own vtable address point, and either compiler drops the stores it proves dead. The final vptr word of a destroyed
  object is representation: the oracle's vftable against the native's address point (unpaired: vptr-unpaired), or
  against nothing (vptr-store, or x86-only-store when units.json does not name the vftable), or nothing against the
  native's address point (native-only-store, hard), and the first difference hid every later one (a second vptr at
  +0x6c, fields). In a destructor (`??1`, `??_G`, `??_E`, D0/D1/D2) compare drops those words
  (`runner.dtor_vptr_bytes`: each side left the word or stored a whole vtable word, the oracle an exe RTTI vftable or
  a kind-V one, the native a `_ZTV` address point, a synthesized group or a kind-V vftable) and compares the rest; the
  words alone are soft `vptr-store`. Rechecks cached mem-write records of destructors.
- **d21-holes** (18 + 2 of the 134, all of the live vcall-stub-alias). difffuzz-19 made the unused parts of the stub
  range background memory with a wild-call hook, but the stub hook covers the whole range: a call into a hole was
  traced twice natively (`name:<hex>` or the code stub's original address, then the raw word) and once on x86, so the
  next call compared against the duplicate (call-flow, native-undefined, native-inlined, dyninit-no-atexit, call-count).
  The stub hook leaves holes to the wild hook, and `world.A64_HOLES`, in both hook lists, is hooked once. Rechecks
  cached records whose native trace has such a pair.
- **d21-vslot** (8 of the 40 vptr-unpaired records). A call through the planted vftable where the native reaches
  another slot of it was followed (adaptive follow loaded the oracle's slot function, the oracle ran it untraced), and
  the record became `call-count x86 0` = falloff?. Such a call-target is not followed: compare marks the native's callee
  `arm_vcall: planted`, `Pair.vslots` numbers both callees in the exe's slots, and classify gives `vslot-shift` (the
  native's slot is the oracle's minus one: a declaration numbering its virtuals from a slot-0 placeholder or a
  hand-indexed `vtbl[k]`, D26 b; 7 records, e.g. 0x00a4d210's call-only `NiObjectNET { Unk0; Unk1; GetRTTI; }` reaches
  NiRefObject::DeleteThis 0x00cfcc20 for GetRTTI) or `vslot-differs` (another shift; 1, two slots). A call-count whose
  native extra call is slot k while the oracle ran slot k+1 (unit code) is vslot-shift too (`vslots_run`, 0x00ee8690's
  `vtbl[49]`). Both are real bugs of fix class `vtable-slots` (S1-rules with D26 b; F2 in owners.py). Rechecks cached
  call records of cases with planted vptrs.
Same sample after d21: still diverging 134 -> 120, hard 66 -> 43, of which 13 are now named real bugs (vslot-shift
11, vslot-differs 2; 2 of them were "gone" before: adaptive follow ran the oracle's slot function for real and the
runs then agreed); soft 40 -> 49 (vptr-store 46); oracle gaps 28 -> 28 (oracle-vcall 20: oracle-only faults at an
indirect call through an unplanted object, each machine reading its own slot layout from fuzz bytes; uninit-both 7).
Test: `tests/s13/test_d21.py`.

**difffuzz-20: a full refresh (HARNESS = difffuzz-20, orchestrator decision on EQ's eq-003).** The d21 rows (F2: destructor vptr words, stub holes, planted slots; F3: builtin faults, callee-saved pop learning, bound-stub rewrite, canned results out of the private ranges) and d24-pop (F1: pop learning on a derail without a fault, IAT slots by import-library spelling, exe import thunks) change how every function is fuzzed, so every cache from before them is refreshed; the rows stay for later partial refreshes. F3b (native-stub-data) is part of this refresh: the AArch64 stub range reads and writes as background memory, as the same addresses do on the oracle, except the 16-byte slots of the named and vtable stubs the image binds, where a data access still faults (only a data symbol the link leaves unbound becomes a named stub; a pointer chase through background words that lands on a code-stub page, even exactly on a bound `__fnv_<X>`, is no longer a native fault). F1b (oracle-fault, native-early-exit): `classify.entry_register_reads` no longer takes MSVC padding (`lea ecx, [ecx]`, `mov edi, edi`) or value-free writes (`or r, -1`, `and r, 0`, `sbb r, r`) for custom register reads, `engine.calibrate_custom` pairs each custom register with the parameter whose pairing agrees on at least 7 of 8 fixed cases where the declared order does worse (0x00d58d40 takes its index in eax), the adaptive follow no longer loads an oracle callee that the native function (or a unit function it ran) calls out of line itself, or where the native called a vtable padding slot (it skipped the call or dispatched it, it did not inline it: bool bytes read as oracle faults inside the followed body) and runs a case again after its last load (it stopped after five, returning the run from before the fifth), the bool-repr probe also plants 1 in a background byte the oracle reads by itself and accepts a trial that leaves only a NaN sign or payload store, an oracle-only fault after a store into the oracle's own return-address slot is `oracle-frame-smash` (by design: a local array overrun by an out-of-range fuzzed count), and a trailing oracle-only copy constructor into the result buffer from a stack local plus that local's destructor is `nrvo-copy` (by design: MSVC /Od without NRVO; the local is the native's result buffer in the earlier calls). The signedness probe clears an argument byte's top bit only when the oracle can widen it (`classify.arg_narrow_top`: the top byte of a 1- or 2-byte parameter, or of an int parameter the exe reads through a 1- or 2-byte `[ebp+d]` access; a float's sign bit or a whole-read integer is no sign extension: 0x00e30fb0, 0x00afd390, 0x00abb700, 0x006e9830), and an uninit-arm case whose native outcome becomes stable and equal once the never-stored stack words of buffers it passed to a stubbed callee are zeroed (`world.STUB_OUT`) is `stub-outparam` (artifact: GCC passes a temporary that the real callee fills and copies it back, where MSVC passed its destination; tests/s13/test_signargs.py, test_stuboutparam.py). Stores class (`tests/s13/test_stores.py`): `classify.entry_register_reads` follows every path of the oracle function (a register first read after a branch is a custom register: 0x00db0e80 stores through edi after a `jp`), the oracle gets ST0 for an intercepted call whose callee definition says void but whose calling unit declares float (0x007fb530), a native-only store of zero bytes into the object a constructor builds or a fresh allocation is `native-zero-init` (by design: GCC value-initialises `new T()` / `: Base()`, MSVC 2008 runs the constructor only), an oracle-only store of zero bytes into the `this` of a destructor or scalar-deleting wrapper is `dtor-dead-store` (by design), and a store divergence of a function with its own `_ZThn<d>_` thunk is `thunk-binding`. Float class (EQ, `float`: 22 of a 39-record sample hard before): `x86emu.x87_result_use` follows a `jmp` to the shared epilogue of a `return call()` and knows the other ST0 readers (fidiv/fiadd m32, fist/fistp, fcomi/fucomi, fucom, ftst, fxam, fsqrt .. fcos: the oracle read an empty x87 stack where the native had the canned bytes), pinned literals that overlap are replaced by the exe's bytes over their union when the exe image is available (`coffobj.exe_literals`; with no exe nothing changes: units.json maps several `$SG` strings of one object to one address, so a 64-byte wrong string covered the `__real@` constants behind it; 20 units, 37 overlaps in 29 groups; a lone literal that differs from the exe is only counted: 3334 of 15143 in 933 units), and a float difference beyond the tolerance now runs the probes before it stays `float`: the case-seed-free garbage probe for random backgrounds (`world.GARBAGE_XOR`, `uninit-x86` / `uninit-arm`), the bool-byte probe, the oracle rerun with the x87 control word at 24 bits (`x87-precision`: every x87 operation rounds to a float as on AArch64, however many ulps a cancellation shows), the other width for the float an untyped callee returns (`callee-float-width`, artifact) and the x87 register exponent (`x87-range`, by design: an oracle intermediate beyond the float range, or beyond the double range, where the native result is inf/NaN; the record keeps the exponent and the instruction), and a stride or other input byte that merely changes which memory is read no longer reads as `signedness` for a float result. **x87-range is a declared compiler class (orchestrator decision), next to X87-EXCESS-PRECISION:** MSVC's x87 registers keep a 15-bit exponent at every precision, AArch64 float/double arithmetic overflows to inf and underflows to zero. It applies per record, only where the oracle rerun with every x87 register value beyond the type's range flushed (`world.X87_CLAMP`) agrees with the native; anything else stays hard. The 8 records of the seed-3102 sample (x86 value, native value, clamp, direction): 0x00a6dc30 (3.4e38, NaN; f32 overflow), 0x00c4da80 (0.0, NaN; f32 overflow), 0x00c5d2b0 (-2.7e29, NaN; f32 overflow), 0x00dea450 (NaN, 0.0; f32 overflow), 0x00e442e0 (inf, NaN; f32 underflow of a double product the native computes in double: the constant at 0x1011588 is a denormal), 0x00eaf310 (NaN, 8.04e29; f32 overflow), 0x00eb50f0 (NaN, inf; f32 overflow), 0x00eb5450 (0.0, 1.4e14; f32 overflow). None needed the f64 clamp. F-uncls (the records F1b left unclassified, and the call-flow residue): the garbage probe of `classify.hint` also compares the raw argument a `call-args` divergence names (an uninitialised local passed on, the out-parameter of an intercepted callee, is `uninit-x86` when it moves with the stack fill), and probes random-background cases with `world.BG_SEED` pinned, looking only at the value the divergence is about (the argument, the result, the stored bytes); `runner._learn_pop` tries the pushes right before a call first (three call sites over branches put the true counts beyond its 27-run bound: 0x00e68870); an integer result of a function with x87 code is `x87-precision` when the oracle rerun with the x87 control word at 24 bits matches the native (the same probe as a float result; the distance in float ulps is only recorded, 0x00603fc0); `readset.layout_shift` names a consistent shift of the read offsets `layout-offset` (0x00e3cb10); a run that entered a function of the native unit whose whole body returns 0 (`runner.zero_stubs`, a local static the relocations do not show) is `native-zero-stub` (0x00d57b20, 0x0065f820); and stack words the exe function reads past the parameters its declaration has are `param-count` (0x00d32b40); a result whose last eax write in the oracle was an al / ax write (`world.EAX_TRACK`) and that agrees with the native's in those bytes is `partial-return` (by design: the exe function returns a byte; 0x008b3c70's only caller, 0x9ce2ca, does `movzx ecx, al`, and no data reference holds its address). R4 uncls (EQ, `tests/s13/test_r4uncls.py`, the rows F-uncls left open): the MSVC intrinsics `__movsd` / `__movsb` that Havok units call out of line run as builtins (`rep_movsd` / `rep_movsb`, element by element) and are no call, as the oracle executes the string instruction (0x00dc9db0); a call of operator delete with a null pointer, and the delete that follows the destructor half F(p, 0) of the exe's scalar deleting destructor (`exeempty.deleting_wrapper`: it tests bit 0 of its argument and then calls the deallocator; MSVC calls F(p, 1), GCC the destructor and a separate delete), are left out of both traces by both engines (`libcalls.delete_fold`, `null-delete` / `split-delete`, by design; the exe's delete returns at once for null: 0x00aa4060 `cmp [ebp+8], 0`, 0x00aa1460 `test eax, eax`; 0x00430920, 0x0044c480, 0x008001b0, 0x005e6cd0); a block of calls that is exactly reversed between the traces, with the same calls before and after it, is paired by callee when the exe shows it is the argument list of one call (`runner._perm_align`, `exeempty.arg_blocks`: every call's result eax is pushed or moved before the consuming call; MSVC evaluates arguments right to left, GCC left to right: 0x0060bb40) and is `call-order` (by design); any other permutation, such as a reordered pair of statements, stays a divergence; an adjacent swap of two calls, and uncls2's equal-results probe, are `call-order` only when the same exe check shows the pair is an argument block (`runner._is_arg_block`), else `call-order-unproven` (triage; 0x00456aa0 stores both getter results); and a difference found after the pairing is `call-order-args` (triage: the canned results are numbered by call index); a destructor under another name gets the vptr-reset rule when the exe shows it is one (`exeempty.deleting_wrapper`, or `exeempty.dtor_chain`: the code stores a derived class's vftable and then a base class's, read from the RTTI locators; 0x00cc07f0, 0x00d0adc0, 0x00601370, 0x00e05bb0); and bool-repr may take several bytes (`classify._bool_chain`: every round plants 1 in the 1-byte reads the oracle makes of a byte above 1, the set is minimised, and a divergence the early exit hid must be named by the other probes as a soft cause, else the record stays; 0x006141f0, 0x008c7aa0). Native-null-read class (EQ, `tests/s13/test_nullread.py`): a native fault inside the unit's own `FUN_<X>` that the oracle only called out (X outside the oracle code) runs the oracle's X for real and compares again, a synthesized native vtable group's page and the page after it read the world background past the group's last slot (as the oracle's memory does past its vftable; refilled per case), and an oracle call of a planted vftable slot answered by a named native stub (a virtual the link binds to no definition) is not an inlined X, so it is not followed and stays a `call-target` (`native-undefined`); `difffuzz.py repro --trace` now traces the engines `Pair.run` builds (it traced stale ones). Virtual-call class (F2b, `tests/s13/test_virt.py`): a call-target where the native called planted slot k and the oracle's call went to slot k+1, a unit function it ran as code (no call event), gets `Pair.vslots_run`'s evidence and is `vslot-shift` (0x005c12a0, 0x007aecb0; call-flow before); a call-target whose native callee is in no exe slot but sits in the native copy of a planted vftable, at the slot where the exe's vftable holds a function the oracle called or ran, is `native-vslot-differs` (real bug, S2c vtables: the native vtable calls another exe function than the exe's slot; 0x0058c550's native TESForm slot 76 holds 0x00891170 where the exe holds 0x00401280). Small address classes (EQ, `small`: 135-record sample, 60 hard before, 22 after; the 22 are other causes or real bugs listed in the report): `runner.literal_store_words` compares the pointee (C string, else 8 raw bytes) of a stored pinned-literal address against the native copy (`literal-store`, 15 of 15 gone), `retsrc.ret_source` reads the exe callee's `ret` paths so the canned eax of a callee that returns `this` or its first pointer argument is that input on both machines (`canned-this`), the address of a by-value aggregate parameter (oracle stack slot, native hidden pointer) is equal at the same offset (`arg-stack-vs-object`), a hidden result buffer against a frame local is `nrvo-elision` (by design, soft: the buffer is compared at the end), and a jump into the native vtable region 0x0bc00000.. is the wild call the oracle makes (`native-wild-private`); tests/s13/test_litstore.py, test_canned.py, test_wildvt.py. Flow2 (call-flow family on a fresh sample, seed 3203): an incremental-link thunk (`jmp rel32`, the decomp's `void F(void)` placeholder) pops what the function it jumps to pops (`engine.oracle_ret_pop`; unknown, so learned, when the chain ends in no single `ret N`: 0x00aa0ad0 -> 0x00a81fe0), an intercepted call into a function no unit defines pops the first `ret N` of the exe's function there (`engine.exe_ret_pop`; function starts only), a clean run whose last call is a tail `jmp` returns into the epilogue stub and is no derail (`Pair._returns_into_code`, 0x00e971d0), the entry prologue's `push ebx/ebp/esi/edi` are no arguments in `_pushes_before`, a merged cdecl `add esp, N` marks the earlier call sites cdecl (`_delayed_cleanup`), and `_learn_pop` searches the first three sites and gives the rest their own push count (tests/s13/test_flow2.py). native-zero-stub is not raised when every placeholder the native ran stands for an exe function that is itself a trivial leaf (`retsrc.trivial_leaf`: straight-line to its `ret`, no call, no store outside its frame; S2 W6, 0x0065f820: 0x0065e750 returns an uninitialised al, 0x0065fd70 its argument); the record goes on to the other rules and stays hard (`tests/s13/test_zerostub.py`). R4 flow (`tests/s13/test_r4flow.py`, flow residue): the scalar-deleting wrapper shape takes an int flag and an oracle-only store of the 0x80000000 flag word (hkArray DONT_DEALLOCATE_FLAG) is as dead as a zero store in a destructor (`dtor-dead-store`, 0x00bd8480, 0x00d08e80, 0x00cd1970), `nrvo-copy` also takes a struct's copy constructor (`ABU0`, 0x00aaed70), `classify.entry_register_reads` takes a `push ecx` for a call argument when the call's `add esp, N` consumes it (0x00d409b0), a one-byte argument above 1 is probed as a bool like a memory byte (bool-repr, 0x00572a50), a `call-args` divergence reruns with `world.STUB_OUT` on both machines (the oracle zeroes the unwritten words of a buffer it passes on too: stub-outparam, 0x00a57670), an import's canned eax gets the native's untyped low byte (0x00d45c50), an `extern "C" bool` declaration of the calling unit types the canned result (`engine._c_bool_decl`, 0x0059ba80) and a native free after the D1 binding of a scalar deleting wrapper the oracle called once is `dtor-split-delete` (by design, 0x009edf40). Oracle-fault / empty-callee class (EQ, `tests/s13/test_ofault.py`): call traces that are equal once the calls to exe functions proven effect-free (`exeempty.effect_free`: `push ebp; mov ebp, esp; pop ebp; ret [N]`, a bare `ret`, or a `jmp` thunk to one; no store, call, branch or register but ebp/esp written) are removed from both are `oracle-empty-call` (by design: 0x004103b0 and 0x004120b0 call 0x0040fbe0, GCC drops the call), an oracle-only fault where the native function is itself a bare `ret` / constant return is `native-empty-body` (real bug: 0x00cbc8f0), and `engine.CALIB_MAX` is 120 (a function with five register-eligible parameters and three custom registers has 60 pairings, calibration skipped it: 0x00cb4080 gave edi and esi the wrong parameters). Uncls2 (EQ, `tests/s13/test_uncls2.py`): what the other probes leave gets one rerun per mechanism, each naming its cause only when the two machines then agree: the all-zero garbage fill (`world.UNIFORM`: a bool test of an unwritten stack byte is 1 for 255 of 256 fills, so two random fills agree; uninit-x86 / uninit-arm, also for a call-count divergence: 0x00c32840, 0x00a82d90), equal results for two adjacent calls of one callee (both values; call-order, 0x00456aa0 — one value alone could move a pointer, 0x00480000), the 64-bit shift helpers taking the count modulo 64 (`world.SHIFT_MASK`, shift-count, 0x0062a3b0), fsin / fcos / fsincos / fptan of an infinity giving the indefinite NaN (`world.TRIG_INF`, x87-trig-inf, artifact: unicorn leaves the infinity in st0, the Intel manual says #IA; 0x00524ac0), the native address point in a planted vptr word on the oracle too (`world.VPTR_NATIVE`, vptr-bytes, artifact; 0x0043c470, 0x00480000's byte), a result that differs in the low two bits only and agrees with 4-aligned arguments (ptrdiff-unaligned, by design: GCC folds (end - begin) / 4 * 4; 0x00b24960), the oracle's pool address of its object's unnamed vftable section against an exe address whose first word is the pool literal's (`classify.pool_vftable_stores`, vptr-pool, artifact; 0x006ad660), and `coffobj.UnitImage._exe_constants` pins the exe's bytes of a data symbol the object holds in a writable section where the exe keeps it read-only (`_INVALID_FLOAT`, 0x00a3c6d0). Args class (R4, `tests/s13/test_args.py`): `retsrc.ret_source` follows copies through esi / edi / ebx / edx (an unnamed constructor keeps `this` in esi, 0x00a75650; 0x00dba6a0 keeps its stack argument in edx over a leaf call), `retsrc.input_reads` / `unread_labels` drop from the comparison a `this` in ecx or a stack parameter the callee's exe code cannot read (0x00a89af0 is a bare `ret 4`, 0x0065e750 and 0x0040fc90 read no argument; where the registers at the stub then follow the exe's layout because a link adapter dropped the unread `this`, 0x00d315d0, they are equal), trailing oracle-only calls whose `this` is a by-value parameter's slot are the callee-side destruction `byvalue-arg-dtor` already names (0x00ac6a30), a virtual-call thunk `mov / jmp [reg]` (0x00c91270) has no `ret N` of its own so its pop is learned, a named stub whose original address is a noreturn exe function ends the run (`fnvnx_xlength_error` for std::_Xlen 0x00ec30b0), an unnamed scalar-deleting destructor (`FUN_<X>(this, flags)` calling a destructor, 0x004facf0) gets the destructor vptr rule, and `classify.cpuid_dependent` makes a store or result that changes when the oracle's `cpuid` answers change `cpuid-env` (by design: the port answers with the fixed values of docs/ASM.md; the cpuid instruction is shown by its address). Memory residue (EQ, `tests/s13/test_memdiv.py`, a 30-record sample of the stores / signedness leftovers): `engine.calibrate_custom` counts a calibration case that differs only by float noise (`engine.calib_float_noise`: NaN payload or sign, an ulp, a cancellation residue under 1e-6, never an integer word) as agreeing and runs fresh cases on pairings that tie at the top score (0x00dacf10, 0x00d5b440, 0x00cd6120, 0x00da9210, 0x00d6c3c0 kept the declared register order), a function-local static leaves the diff on its own and the rest of the diff stays (`local-static-mixed`, 0x00c206b0), a word that is -0.0 on one machine and +0.0 on the other (`zero_sign`) is probed as x87-range (an x87 intermediate beyond the float range keeps its sign until the store rounds it), the float-word check reads the raw stores rather than the effective maps (0x006e9830: a ulp whose low byte is the initial byte), and canned results that point into the argument buffers are moved to the heap, equal afterwards being `canned-alias` (artifact: 0x0043a810). R5 order (EQ, `tests/s13/test_r5order.py`): calls in another order than the oracle's are paired when the exe shows them to be the operands or arguments of one expression (`exeempty.expr_deps`: a straight-line walk that follows each call's result through registers, the x87 top, pushes and dead `[ebp-N]` temporaries to the end of the expression; a dropped result, a variable read again, a store through a pointer or a branch refuse it), with the exe's data dependences kept and a wild target paired with a wild one by position (`runner._order_pairs`), and the native is run again with its canned results numbered by the oracle's counterpart call (`Pair._order_probe`, accepted only if that run makes the oracle's calls, a wild call on the same `this`): then the arguments and the rest are compared (`call-order`, by design, `expr_order`); a run where both machines fault inside such an expression is compared up to it (`fault_in_expr`; 0x007134d0, 0x00713fb0, 0x008287e0, 0x009b36f0, 0x00af28f0). pairing / stack bytes (EQ, `tests/s13/test_r5pair.py`, 7 functions four rounds left open): `engine.calibrate_custom` no longer lets a case where both machines fault count as agreeing when the faults differ in kind or address, and when the declared pairing agrees on all 8 cases or nothing agrees, compares what each pairing makes the oracle read and write in the argument buffers with what the native (AAPCS, the declared order) reads and writes (`calib_touch_pick`: 0x00dbcdd0 takes its object in edi and its count in eax; 0x00d6e280's four registers are fixed by the buffer accesses alone), a calibration case that differs only in a byte copied out of a planted vptr word agrees (`calib_vptr_noise`, 0x00d409b0); a call-count / call-target divergence that disappears when the unwritten words of a buffer passed to an intercepted callee are zero on both machines, under two garbage seeds and with no fault, is `stub-outparam` (0x00a3d370: the key count is read into the dead slot of the stream argument), the oracle's STUB_OUT zeroes only the still-planted words of its own frame (the stack stores were not in the written set, so pushed arguments and initialised locals were zeroed) plus the one dead incoming-argument word a pointer designates, and under STUB_OUT both machines restore the stack below the stack pointer to the planted fill at every function entry that runs for real, so a local the stub should have filled does not read the residue of an earlier frame (`world.scrub_dead_frames`, 0x006a0c50: the `Handle` local of 0x006a0ef0 held a pointer left by a returned frame); `Pair.run` runs a call divergence found after the pair followed a callee once more on the oracle code as it was before the follow and takes an agreeing result (GCC inlined 0x00a23d40 on one path and calls it on another, 0x00a24280, which then reaches the bool-repr probe); and a stack word the callee's exe code reads only as one byte, directly or by handing the word to a callee that does, is compared by its low byte (`retsrc.byte_only_args`, 0x00b6d170: `cmp byte ptr [esp+0x2c], 0` at 0x00b6b434 reads the bool that 0x00b6d2e3 pushes with the upper bytes of a dead slot). R5 design (EQ, `tests/s13/test_r5design.py`, `r5design.py`: nine records earlier workers called by design without a probe; each cause needs the exe's own code to show the mechanism and a rerun without it to agree): `dead-result` (the exe has no caller that reads eax: every direct call is followed by a write of eax, or is the bare tail of a wrapper whose callers do the same, and no word of the image holds the address; the rerun does not compare the result: 0x00cde920 and 0x00e5c420, whose only callers are the wrappers 0x00cde940 / 0x00e5c440, called at 0x00cde409, 0x00cde435, 0x00e5b9c1 and 0x00e5ba08, each followed by a write of eax), `dead-init-call` (a call `lea ecx, [ebp-D]; call C` whose callee only stores through `this` and whose caller then stores every one of those bytes before any read or escape of the object; the rerun leaves the call out: 0x00b134d0 drops the identity constructor 0x00b12af0, the nine floats at [ebp-0x2c] are all stored at 0x00b13541..0x00b1356e), `local-index-range` (a frame-relative address with a variable index, `[esp + r*4 + d]` or `lea [esp + r + d]`, that the oracle forms outside its own frame; the integer parameters at small values keep the index in the frame and at least three reruns that reach the same call all agree: 0x00db1d90 `fst [esp + eax*4 + 0x1c]` with an index of 15 writes its own argument slot, 0x00e552f0), `ptrdiff-unaligned` generalised (`sub r, [m]; cdq; mov rK, K; idiv rK` with K not a power of two, a dividend that is not a multiple of K, and a rerun with the subtrahend word rounded so that it is: 0x00abb700, K = 52 at 0x00abb74d, 0x00abb7d0, 0x00abb80a), `x87-range` for a float argument of an intercepted call (`r5design.x87_args`: the flush rerun, which also leaves the garbage of a stubbed out-parameter that the all-zero fill removes: 0x00a62be0's z axis is an x87 product beyond the float range, the angle `fcom` compares are not involved), `canned-null-deref` (an oracle-only read fault at a small address right after an indirect call whose canned result is 0 and whose next instruction loads through eax; the indirect calls whose result the exe code dereferences at once get an object holding the canned bytes on both machines, `world.REF_CALLS`, and the native sees the same object in x0 and s0..s3: 0x005c0bf0's first fault; the record stays hard, the run goes on to a wild call whose result the exe passes on as a pointer and the native receives by value), and the oracle may leave a destructor's vptr word alone only where the exe shows its destructor ends by calling a base destructor that stores a vftable (`exeempty.dtor_defers_vptr`, `runner.dtor_vptr_bytes`: 0x006e5890 -> 0x006e58d0 -> 0x006e2620). R5 misc (EQ, `tests/s13/test_r5misc.py`, 14 functions four rounds left open): `x86emu.x87_result_use` steps over the `push ecx` / `sub esp, 4` that reserves the slot of `fstp dword [esp]`, so an untyped virtual call's float result reaches the oracle's ST0 as it does the native's v0 (0x00886cb0, `kRotate.FUN_004a0c90(FUN_vt2bc(0))`); the builtin copies (memcpy, memmove, strcpy ..) record the bytes they copy, the native code-pointer scan reading copied data at 4-aligned addresses only (planted data 00 00 00 0a read as the code stub 0x0a000000 at an odd offset: 0x00c7f900); the intercepted native call behind an `sret-explicit` bridge (tools/link abi2: the bridge consumes the explicit result-pointer parameter) is decoded without that parameter (`conv.Conv.bridged_view`, 43 hard call-args of the 2,951 bridges: 20 gone, 12 soft; 0x00d984b0); and a call-target gets the all-zero-fill `uninit-*` probe a call-count already had (0x006e1670); the destructor-vptr rule (F2 d21-dtorvptr) also takes a frameless scalar deleting destructor that tests its flags at `[esp+4]` and stores its vftable before the `je` (`exeempty.deleting_wrapper`, 0x00d60bd0, 0x00dc9fd0), a thin wrapper whose only call is a destructor chain and a body that stores its vftable and jumps to the base destructor (`exeempty.dtor_via_callee`, 0x00b43c50, 0x00a3d120). EQ rest class (tests/s13/test_eqrest.py): a pointer argument that is the address of a function-local static (the oracle's image address against the native's .bss copy), and a call-count where the oracle's static guard is set while the native's own guard runs the initializer, are local-static; entry_register_reads follows the cases of a switch jump table inside the function, so a register a case body reads (edx at 0x00e03170) is a custom register. Leaf registers: an intercepted call to an exe function that is a straight-line leaf writing neither ecx nor edx leaves them as the caller held them (retsrc.preserved), as MSVC's optimiser relies on (0x00d3ab60 writes only eax and cl; 0x00d3abc0 keeps its argument in edx over the call). A unit image also links in, for each C++ name its object leaves undefined, the one definition another object of the link has (zz_bd_<unit> bodies, placeholders of a class another unit defines, instantiations; the provider keeps only the wanted symbols global), so a name the game link binds is no longer a trap stub (native-undefined). glibc's C23 redirect `__isoc23_strtoul` is the same C function as the CRT `strtoul` (libc-variant, not native-undefined). Flow rows (EQ, `tests/s13/test_flowfloat.py`, `test_flowthunk.py`, `test_flowthrow.py`): the exe's float libm wrappers (ceilf 0x004e4430, floorf 0x00406cc0, cosf, acosf) run as untraced builtins with a float argument (`x86emu._builtin`; GCC inlines them to frintp / frintm, 11 native-inlined rows, and 0x00406cc0 was read as the double floor), the native entry of a secondary-base override is the link's `__fnvnx_msvc_<addr>` alias from aliases.ld even when the unit's image does not reference it (`arm64emu.entry`: 0x0093ac20 `sub ecx, 0xa4` entered the primary body; 10 thunk-binding / native-extra-calls / native-null-read rows), and a native call of a temporary's destructor right before the throw the oracle ends the run at is `throw-temp-dtor` (by design) when the exe calls it right after its `_CxxThrowException` (`exeempty.calls_after_noreturn`). EQ memory group: a function-local static of a class type, or whose function is spelled with other back-references in the static's name, pairs with the native `_ZZ` object (0x00aff100), the address of a paired static compares as the same object, and a paired static's native guard is no private read (split-global). EQ thunk-forward: a call-args divergence on a native garbage word at the jump target of an exe function that is exactly one `jmp rel32` (an incremental-link thunk the decomp declares `void F(void)`: the case defines none of the arguments it forwards) is the artifact `thunk-forward` (0x00aa0ab0, 11 farm rows). Oracle-callee-unloaded also reads the base-object entries `__fnv_<Y>_C2` / `_D2` that a unit-local constructor X reaches as the traced call Y (the 71 global-object initializers calling the constructor 0x0045cfe0, whose first exe call is 0x00404920 at 0x0045d013; tests/s13/test_basector.py). EQ bh_C (tests/s13/test_bhc.py): the platform's lock-free close stands for the exe's _fclose 0x00ec9907 (libcalls.EQUIV), MemoryManager::Deallocate 0x00aa4060 is the family of the native operator delete, a synthesized vtable's compiler D0 after the exe deleting destructor's D1 stub stands for that destructor (vtmodel.deleting_d0), `call [..]; pop ecx; ret` closes a frame for pop learning, a call-count where the oracle ran the exe's slot function as code and the native entered another function through the same slot of its vtable copy is native-vslot-differs (runner.vslot_entered), canned-null-deref follows a copy of the result (`mov esi, eax`) across a later call and learns the pops of the sites the faulting run left untried, an x87 float store off beyond the tolerance runs the x87 probes too and the x87-range probe has a denormal-rounding mode ('f32den': the float product in the denormal range keeps all its bits on the x87 stack, 0x00f92520), and a native fetch from 0 through an unplanted table where the oracle calls a wild word is native-vcall-unplanted (0x0078a3e0).

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
**Coverage first (owner rule D21).** `runner.compare` no longer ends at a by-design or harness-made difference: it
keeps the first one (`soft`), goes on with the rest of the function (calls, arguments, result, stores, callee-saved
registers) and returns a hard difference found later, else the soft one. Covered: two adjacent calls in swapped
order (call-order; their arguments compared crosswise), NaN-on-both call arguments (nan-bits), a bool result with the
same truth value (bool-repr), a float result within the tolerance, stores only to function-local statics
(local-static); earlier: the deleting-destructor flags and result (d14), unplanted oracle calls (d15), trailing frees
of by-value aggregates and stub-range garbage calls (audit). Test: `tests/s13/test_audit.py`.

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
| `this`/pointer argument: the native's is the oracle's + d, both in the literal pool, case buffers, heap or image, and the function or a direct callee in its unit has a `_ZThn<d>_` thunk by exactly d (checked before `layout-offset`) | `thunk-binding` | real-bug S2c (`__fnv_<addr>` bound to the GCC primary entry, not the secondary-base thunk the MSVC entry corresponds to); without the thunk `layout-offset`  |
| call argument (I32/F32/F64) the native passes equal to the canned result of the call just before, the oracle another value | `native-stale-result` | real-bug S1b (an argument the native caller never set: its declaration of the callee differs) |
| native `fault:x` in the AArch64 private range, oracle fine, case background `rand` | `native-wild-private` | triage S13b (a random code pointer, or a wrong vtable slot: indistinguishable in the record; V1 A4) |
| first differing store: every differing byte in a 4-byte window where the oracle word w is in [0x08000000, 0x09000000) and the native word is exactly w - 0x08000000 (`causes._stub_rewrite`) | `stub-normalized` | artifact (arm64emu `_codeptr` rewrites any such stored word, data included; a 0x08 flag byte elsewhere stays open, V1 A1) |
| stored / returned word that is one of the planted garbage words (`world.garbage_words`): both / native / oracle | `uninit-both` / `uninit-arm` / `uninit-x86` | artifact / real-bug S1b / by-design |
| native 0 where the oracle has a value; the native function or a direct callee in its unit is `mov w0, #0; ret` | `native-zero-stub` | real-bug S1 (a placeholder body) |
| call flow, the native function's own body is `ret` / return-a-constant (or it calls such a decomp-local `..FUN_<X>..` stand-in for an original X another object defines) and the oracle makes calls | `native-empty-body` | real-bug S1 |
| the oracle's first missing call is CRT `_atexit` (0x00ec658f), the native function names no `atexit` | `dyninit-no-atexit` | real-bug S10 (soft: the destructor is lost at exit only) |
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
`recheck[_<tag>].jsonl`. `causes.py explain <div.json>...` prints hint, refined cause and class. `causes.py handoffs <dirs> --out-dir d [--effects e.jsonl]` writes the owners' lists, one row per counted divergence (owner, cause, address, unit, reproducer `fuzz/cache/<k>of<N>:div/<file>`, first effect, fix class): `handoffs_<owner>.tsv` (real-bug), `triage_<owner>.tsv`, `harness_<owner>.tsv` (artifacts with an owner) and `index.tsv`. With `--ref-by-head` the reproducer is `fuzz/<head12>/<k>of<N>:div/<file>`, the branch of the head that fuzzed the function (results `commit`) in the shard it was fuzzed in (results `shard`: `fuzzcache merge` records it, `seed` keeps the source shard's, V1 RH-2), which the farms never rewrite (the rolling `fuzz/cache/` branches are force-updated on every re-fuzz); they go to the private repository, never here. The object rules need pyelftools: without it the summary's `native_objects.reader` is `unavailable` and a warning is printed. Test: `tests/s13/test_causes.py`.

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
| `vtable-slots` | S1-rules | `vslot-shift`, `vslot-differs` | every virtual call reaches the exe's slot: each declaration a caller dispatches through numbers its virtuals in D26's one minus-one order (d21-vslot) |
| `vtables` | S2c | `vptr-unpaired`, `native-calls-data`, `thunk-binding`, `vslot-trap`, `native-vslot-differs` | every image vftable pairs with a full native vtable of the static class (no short vtables, overrides bound to their this-adjusting thunks) |

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

## Exe-image oracle (FNVNX_ORACLE=exe; D23 b)

A function the decomp lacks has no pristine MSVC object, so the default oracle cannot run it. With `FNVNX_ORACLE=exe`
(`tools/difffuzz/exeimage.py`) the x86 side runs the function's bytes straight from the player's FalloutNV.exe
(`FNVNX_EXE`, sha256 pinned), at its address, under the same emulator, seeds, probes and comparison. Extents come
from the prepared tree's build/units.json (`FNVNX_TREE`), where REC1 appended the recon functions, and
`catalog.py` and `engine.UnitCache` use that table in this mode. The bytes are final, so nothing is relocated.
Relocation records for the harness's own uses (seeds from referenced data, callee discovery, preload) are recovered
from the image: DIR32 from its base-relocation table, REL32 for each `call` / `jmp` rel32 out of the function
(capstone). Read-only targets are pinned on both machines (up to 4 KiB each), writable ones seed the fuzzer.
Check: on 1,493 decomp functions of 150 random units, the image's code equals the MSVC-object oracle's relocated
code. The only differences are the 64 functions whose string-literal operands the COFF oracle moves into its
synthetic pool (0x2f0xxxxx); in the image they are the real .rdata addresses. Every relocation the COFF side
resolves to an image address is recovered.
`tools/farm/recon_fuzz_job.sh <commit> <recon branches|all>` (runner job `recon_fuzz`) fuzzes the recons'
functions this way against `<commit>`'s link and reports per function clean / hard / soft / not run, and per recon
`fuzz-clean` (the fnvnx-recon-fuzz/1 record that recon.json's `"accept": "fuzz"` cites; `recon_fuzz.py`). Tests:
tests/s13/test_exe_oracle.py (a synthetic PE32). Pops class (EQ, `tests/s13/test_pops.py`): `runner._learn_pop` fixes the unknown-pop call sites one at a time in run order, however many (the count that takes the run farthest before a fault or an intercepted call returning outside the loaded code; only a clean run with a balanced stack ends it), and `_pushes_before` counts a `sub esp, N` argument block as N/4 words (0x00818310, 0x005c0bf0 pass a struct by value that way).
