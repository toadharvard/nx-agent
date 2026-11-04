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
result.

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
