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

Static checks found through divergences: `check_rebind.py` (references that bypass S2's image-variable rebinding),
`check_falloff.py` (native functions that run off their end).

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
```

## Limits

Indirect calls (vtable calls through fuzzed objects) are not modelled yet (functions with indirect calls are left
out); TLS (`SECREL`) relocations leave a function unresolved; callee side effects through pointer arguments are not
modelled (canned results only); x87 transcendental helpers beyond sin/cos/sqrt are intercepted, not executed.
