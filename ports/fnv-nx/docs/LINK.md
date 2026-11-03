# Link by address (stream S2-link, decision D4)

The decomp's units refer to each other by **original address**: one function is reached through many
differently decorated declarations (`*_shell` classes, local re-declarations, other calling conventions), and
`build/units.json` records, per unit, the address behind every decorated name it references. GCC objects carry
Itanium names, so the plain linker would bind by name and miss or mis-bind most of these references. The link step
(`tools/link/`, usage in its README) rebinds every undefined symbol through its address.

Everything it writes is derived from the decomp and stays local (`build/`, never committed).

## Pipeline

1. **compile** - `tools/link/compile.sh` builds one ELF object per unit (census flags, `-c -O2`); units listed twice
   in units.json (two flag sets) are one object. Incremental: grows as S1 makes more units compile.
2. **resolve** - `fnvlink.py resolve units.json objects.tsv out/`:
   * MSVC names are demangled with `llvm-undname`, Itanium names with `c++filt` (both cached in `out/cache/`), and
     parsed into signatures (`sigs.py`): canonical qualified name (tags dropped, `{anon}`, `{ctor}`/`{dtor}`),
     canonical parameter types (MSVC `__int64` -> `long long`, top-level cv dropped, blanks normalized), cv of the
     method, calling convention, member/static, return type.
   * **Definitions**: each unit's `functions` are matched against the symbols its object defines (same keys);
     ctor/dtor variants are kept (`??0` -> C1/C2, `??1` -> D1/D2, `??_G`/`??_E` -> D0).
   * **References**: each undefined symbol of an object is looked up, strict to loose, in that unit's names
     (`symbols` and `functions`): `exact` (name, params, cv) -> `l1` (`long` == `int`, `wchar_t` ==
     `unsigned short`: the ILP32 `size_t` / MSVC typedef differences) -> `l2` (cv ignored) -> `arity` -> `name`;
     undecorated names (`_f`, `_f@N`, `@f@N`, unmangled C++ variables) by plain name (`cname`). If the unit has
     no match, all definitions (`gdef`) then all references (`gref`) of every unit are tried (exact/l1/name, and
     only when one address answers). Last resort: a Ghidra-style name that carries its address (`DAT_`, `FUN_`,
     `g_`, `UNK_`, `PTR_` + 8 hex digits); across units.json such names agree with the recorded address in 99.6 %
     of the cases. Several addresses -> arity, then cv, else `ambiguous`.
   * The reference declaration and the definition at the address are compared for AArch64 argument passing
     (below); mismatches get a bridge or are reported.
3. **apply** - `fnvlink.py apply out/ fixed/`: per object, objcopy adds the address symbols of its definitions,
   localizes duplicate strong names and turns natively defined variables into weak address symbols; then the
   undefined symbols are renamed (`elfsyms.rename_undefined`: objcopy `--redefine-syms` refuses several old names
   for one new name, which is the normal case here). Assembles `bridges.S`, compiles `addrmap.c`.
4. **check** - `fnvlink.py check out/ fixed/`: trial link of everything with `aliases.ld`; counts what is still
   undefined and any multiple definitions.

## Symbols and files

| Name / file | Meaning |
|---|---|
| `__fnv_<addr8>` | The original address `<addr8>` (lower-case hex). For code: global alias of the native definition, added to the defining object (`--add-symbol`) or, for COMDAT/weak definitions, in `aliases.ld` (`__fnv_X = name;`, whichever copy the linker keeps). For data: absolute symbol `= 0x<addr>` in `aliases.ld` (the data image is mapped there, D3). |
| `__fnv_<addr8>_C2`, `_D2`, ... | Another ctor/dtor variant of the same definition, when the reference asks for it. |
| `__fnvnx_br_<addr8>_<hash8>` | ABI bridge for one (address, reference declaration); hash of the MSVC reference name. |
| `out/objcopy/<obj>.redefine` | `old new` per line (the objcopy `--redefine-syms` format): undefined Itanium name -> address symbol or bridge. |
| `out/objcopy/<obj>.args` | objcopy options for the object (`--add-symbol`, `--localize-symbol`, `--weaken-symbol`, `--redefine-sym`). |
| `out/aliases.ld` | Linker script: absolute data address symbols, aliases of COMDAT definitions. |
| `out/refs.tsv` | Every (object, undefined symbol): class, tier, address, target, matched MSVC declaration. |
| `out/abi.tsv` | Every reference whose declaration passes arguments differently from the definition: verdict, classes, register moves, notes, both demangled declarations. |
| `out/bridges.S` | Generated bridge thunks (AArch64 asm, ILP32). |
| `out/extern_text.tsv` | `.text` addresses referenced but defined by no unit (CRT, thunks; stream S4), with the names used. |
| `out/data_defs.tsv` | Variables a unit defines natively at a data-image address. |
| `out/addrmap.tsv`, `out/addrmap.c` | The address map (next section). |
| `out/stats.txt` | The numbers below. |

Natively defined variables at data-image addresses (global objects with constructors, mostly the generated
initializer units) are weakened and renamed to their address symbol, so every unit, the defining one included, uses
the image copy, and the native static initializer constructs the object in place, as the original CRT init did.

## Address map (for the data-image loader, S3)

`addrmap.tsv`, format version 1: UTF-8, `\t`-separated, `#` comment lines, sorted by address, one line per
original code address the native build defines:

    # fnvnx-addrmap 1
    # address	kind	link_symbol	native_symbol	object	note
    00a7fc00	F	__fnv_00a7fc00	_ZN17NiRenderedTexture6CreateE...	src_NiRenderedTexture.cpp.o

* `address` - 8 lower-case hex digits, address in FalloutNV.exe 1.4.0.525.
* `kind` - `F` function, `C` constructor (complete-object variant), `X` destructor (complete-object variant),
  `G` MSVC deleting destructor (`??_G`/`??_E`, takes a flags argument) mapped to the Itanium deleting destructor
  (D0, no flags, always deletes): a code pointer to it in the image is only safe where the flags are 1.
* `link_symbol` - the symbol to take the address of (`__fnv_<addr>`), stable across builds.
* `native_symbol`, `object` - for people; `note` - free text (`static in its unit`, the `G` caveat).

The runtime form is `addrmap.c` (`fnvnx_addrmap_entry {unsigned orig; void *native;}`, sorted by `orig`,
`fnvnx_addrmap_count` entries; header `tools/link/fnvnx_addrmap.h`), linked into the program so the loader can
binary-search it when it rewrites code pointers in `.rdata`/`.data` (vtables of the image, callback tables).
Addresses absent from the map are either not compiled yet, CRT/thunks (`extern_text.tsv`), or mid-function; the
loader should point them at a trap that reports the address. Bridges never appear in the map: a pointer taken
from the image is called with the convention of the definition.

## ABI model (`abi.py`)

Both declarations are MSVC signatures (the reference from the calling unit's `symbols`, the definition from
`functions`); the MSVC decoration carries calling convention, member/static, return type and
class/struct/union/enum tags, which Itanium names do not.

* **x86 slots** - thiscall: `this` in ecx, the rest on the stack; fastcall: the first two integer/pointer
  parameters of at most 4 bytes in ecx, edx, the rest on the stack; cdecl/stdcall: stack. Aggregate results add
  MSVC's hidden return-buffer pointer (after `this` for members, first for free functions). Stack offsets are
  sums of 4/8-byte words with by-value aggregates of unknown size kept symbolically.
* **AArch64 slots** - AAPCS64: `this` and integers/pointers/enums in x0-x7, float/double in v0-v7, then 8-byte stack
  slots; the hidden result pointer is x8, not an argument; a by-value aggregate of unknown size makes later slots
  symbolic.
* Each definition parameter is located in the reference by its x86 slot (same slot, same size). The pair is
  x86-consistent when all are found; a definition parameter in edx that the reference does not pass is the
  `edx-dummy` of `__fastcall f(T*, int, ...)`, other missing ones are `ref-passes-fewer` (junk on both machines).
  Then each definition parameter's AArch64 slot is compared with the reference's slot of the same value: equal
  everywhere -> no bridge; register moves only -> generated bridge (parallel moves sequentialized through
  x16/v16, then a tail call `b`; a result in the wrong register file -> call, `fmov`, return); anything needing an
  aggregate's size or a stack destination -> `manual`.

Classes in the report: `edx-dummy`, `reg-shift` (same register file, other index), `int-float-param`
(x86 tolerated an int/float swap in a 4-byte slot), `ret-regfile`, `sret-explicit` (one side returns an aggregate,
the other passes the buffer pointer explicitly), `aggregate-param` / `ret-aggregate` (aggregates of different
names, sizes unknown), `varargs`, `ref-passes-fewer`, plus the declaration shape (`thiscall-member->free-fastcall`,
`free-cdecl->static-member-cdecl`, `arity(...)`). `x86-inconsistent`: the pair cannot have worked on x86 as
declared, e.g. `void __cdecl f(void)` calling `T::f()` - the caller relied on ecx already holding `this`; on AArch64
x0 is not preserved, so the declaration has to be fixed in the source (S1 rule or patch).

## Measurements (decomp `87eb9373`, 2026-11-03)

2,029 objects compiled (of 2,978 units; S1 census flags, `-O2`). 106,043 undefined (object, symbol) references:

| Class | Refs | |
|---|---:|---|
| `data` | 41,037 | data-image address -> absolute `__fnv_` |
| `code` | 23,379 | bound to a native definition (2,344 with the same Itanium name, 21,791 other names) |
| `code-pending` | 23,676 | address of a function whose unit does not compile yet |
| `code-bridge` | 146 | through 136 generated bridges |
| `code-manual` | 610 | bound, but the ABI difference needs layout information (518 pairs) |
| `code-defmissing` | 173 | the defining unit compiled but emitted no symbol (inlined / not instantiated) |
| `extern-text` | 877 | `.text` address no unit defines (CRT, thunks: S4), name kept |
| `import` | 124 | Win32 import (`__imp_`), name kept for the platform layer |
| `runtime` | 6,143 | GCC runtime / libc (`__cxa_*`, `_Unwind_Resume`, `__stack_chk_fail`, ...) |
| `native` | 583 | no address, but defined by name in another object (ordinary link) |
| `vslot-unresolved` | 6,088 | referenced only from a vtable GCC emitted (virtuals the decomp never names at an address) |
| `unresolved-ctor-dtor` | 749 | ctor/dtor GCC calls implicitly where MSVC did not (base-class ctors of implicit ctors) |
| `special-unresolved` | 1,462 | `typeinfo for` 794, `vtable for` 643, thunks 25: emitted by no compiled object (key function not compiled or never defined) |
| `unresolved` | 995 | no address, no definition |
| `ambiguous` | 1 | several addresses after arity/cv |

Resolved by address: 90,022 (84.9 %). Tiers: unit/exact 45,045, unit/cname 33,498, gref/cname 6,859,
unit/name 1,621, gref/exact 1,521, gdef/exact 384, unit/l1 328, name-addr 323, others 444 (123 of them imports).
Definitions: 25,956 of the compiled units' functions matched to a native symbol (25,834 exact), 48 missing,
18 ambiguous, 11,144 MSVC-only specials (dynamic initializers/atexit destructors, vftables).
6,361 natively defined variables live at data-image addresses; 4,485 strong names are defined by more than one
object (localized after the first). Trial link of all objects: no multiple definitions; 12,780 names still
undefined - 7,311 `__fnv_` addresses of units not compiled yet, 5,116 C++ names (the unresolved classes above),
353 runtime/C/platform.

ABI verdicts over references (distinct pairs in brackets): bridge 146 (136), manual 610 (518), x86-inconsistent
74 (74). By class: reg-shift 264 (bridge 121), aggregate-param 236, sret-explicit 206, ret-aggregate 159,
edx-dummy 38, int-float-param 27, varargs 15, ret-regfile 2.

## Open problems

* **Virtual functions behind native vtables** (6,088 refs, plus 643 undefined vtables): GCC emits vtables whose
  slots name the decomp's `Unk_xx` virtuals; units.json has no address for them because MSVC vtables live in
  `.rdata`. Resolution: slot *k* of `_ZTV<Class>` -> the MSVC `??_7<Class>@@6B@` address (when some unit names
  it) -> dword *k'* read from the player's `FalloutNV.exe` (D3: allowed locally, never stored) -> code address ->
  `__fnv_`. Needs the image reader of S3 and the Itanium/MSVC slot correspondence (two destructor slots vs one,
  RTTI/offset-to-top header).
* **Aggregate layouts**: `aggregate-param`, `ret-aggregate`, `sret-explicit` (~600 refs) need size,
  trivially-copyable and HFA of each type; plan: a probe TU per unit that includes the unit and emits
  `sizeof`/`__is_trivially_copyable` as constants, read back from the object.
* **x86-inconsistent** pairs (74) are source bugs that MSVC got away with (ecx coincidence); hand to S1 as a list.
* **Code placement**: the trial link puts native code at the default 0x400000, inside the original image range
  (`.text` 0x401000.., data 0x00fdf000..0x0200c612); the S5 linker script must place native code and data
  outside the image (and below 4 GB for ILP32).
* **Duplicates**: 4,485 strong names defined in several objects (mostly the same `g_` variable or `Derived`
  ctors in several units); localization keeps the link clean, but where two copies differ the address decides.
* `code-defmissing` (173): the definition was inlined away; add `-fkeep-inline-functions` / `__attribute__((used))`
  to those units, or emit them from a forced instantiation.
