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

**difffuzz-12 (J-0138).** The game allocators (operator new 0x00401000, 0x00aa3e40, 0x00aa13e0, and the native
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

## Cause refinement and verdict policy (`causes.py`, S13b)

`report.reclassify` hands what the probes left at `unclassified`, `call-flow` or `signedness?` to `causes.refine`,
rules on the saved record alone (no replay; a probe's definite cause is never overridden):

| record shape | cause | class |
|---|---|---|
| native `exc:N` (brk/udf), oracle returns | `native-trap` | real-bug S1 (FALL1 trap / GCC unreachable on a path the exe takes) |
| native `stack-protector` | `stack-protector` | real-bug S1 |
| native data fault below 64 KiB, oracle fine | `native-null-read` | real-bug S1 |
| ... and the native function calls a game allocator (operator new 0x00401000, 0x00aa3e40, 0x00aa13e0; from `fixed/<unit>.o` or `FNVNX_CAUSES_OBJ`) | `new-null` | artifact (canned null from an allocator: GCC drops MSVC's null test after a throwing new) |
| native data access in the stub range; stub address passed/stored | `native-stub-data` | real-bug S2c |
| native image address where the oracle has an image-window one (arg, result, store) | `native-address` | real-bug S2c (unrebound variable/table; check the pointee for literals) |
| call argument the runner marked `arm_garbage` | `native-garbage-arg` | real-bug S1b |
| `sret` argument differs | `sret-mismatch` | real-bug S1b |
| float argument within tolerance / NaN on both | `x87-precision` / `nan-bits` | by-design |
| float result equal to the oracle's unrounded x87 value | `x87-precision` | by-design |
| pointer to a pinned-pool literal (0x02f00000) vs the native .rodata copy: argument / result / store | `literal-arg` / `literal-ret` / `literal-store` | artifact |
| both sides store image vftables (link `out/addrmap.tsv` kind V, or `FNVNX_CAUSES_ADDRMAP`), different ones: in a destructor / elsewhere | `vptr-store` / `vptr-differs` | by-design / triage S2c |
| result of a `??_G` / `??_E` deleting destructor (MSVC returns this, Itanium D0 is void) | `deleting-dtor-ret` | by-design |
| pointer result with an unreadable pointee on one side | `literal-ret` | artifact |
| oracle `exc:3` (0xCC filler) | `oracle-trap` | artifact |
| `no-native-entry` | `no-native-entry` | artifact |
| oracle-only data fault in the AArch64 private range | `oracle-native-address` | artifact (a native address leaked to the x86 machine) |
| oracle-only data fault | `oracle-fault` | triage |
| call-target: native calls a named stub `name:_Z...` | `native-undefined` | real-bug S1/S2c (fallback without the link's providers) |
| call-target: oracle calls an import, native jumps to the IAT slot address | `import-slot-called` | real-bug S2c |
| call-target: the native function never names the oracle's callee (from its object) | `native-inlined` | triage |
| one side's store covers bytes the other leaves | `store-width` / `x86-only-store` / `native-only-store` | triage |
| native calls a prefix of the oracle's calls / the oracle's calls and more | `native-early-exit` / `native-extra-calls` | triage |

With `summary --effects <difffuzz.py effects output>`, a record still in triage is refined by its first effect:
`width` -> `field-width` (real-bug S1), an image vftable stored where the native stores its own `_ZTV` address
point -> `vptr-unpaired` (S2c), a native named stub stored or passed as data -> `native-stub-data` (S2c).

`causes.POLICY` gives every cause a class (by-design, artifact, real-bug, triage), an owner and a reason;
`report.py` takes its by-design causes as soft and its artefacts as oracle gaps (`uninit-both` moves from hard to
oracle gap: both machines read garbage). `python3 causes.py summary <dirs> [--recheck-tag t] --out causes.json`
writes `fnvnx-causes/1`: per cause divergences, functions, functions whose worst cause it is (`worst_of`), kinds,
five example reproducers, plus `functions_by_class` (each diverging function by its worst class, real-bug first)
for R0. `causes.py merge <reports/fz<k> .md | causes.json>... --out all.json` adds the shards up
(classes from this checkout's policy). Input layout (any campaign or `fuzz/cache/<k>of<N>` checkout): `results.jsonl`
(address, source, status; only `ok` rows count), `div/<addr>-<n>.json` (divergence, hint, case), optional
`recheck[_<tag>].jsonl`. `causes.py explain <div.json>...` prints hint, refined cause and class. Test: `tests/s13/test_causes.py`.

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
out); TLS (`SECREL`) relocations leave a function unresolved; callee side effects through pointer arguments are not
modelled (canned results only); x87 transcendental helpers beyond sin/cos/sqrt are intercepted, not executed.
