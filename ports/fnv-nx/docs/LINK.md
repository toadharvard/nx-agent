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
     ctor/dtor variants are kept (`??0` -> C1/C2, `??1` -> D1/D2, `??_G`/`??_E` -> D0). The MSVC-only dynamic
     initializers and atexit destructors (`??__E`, `??__F`) that prepare rule XC1 turns into `extern "C"`
     `__fnvnx_dyninit_<addr>` / `__fnvnx_dynexit_<addr>` (docs/STARTUP.md) are bound to their address by that name
     (kind `F`; S3 then rewrites the image's `__xc` slots and `??__F` references to them).
   * **References**: each undefined symbol of an object is looked up, strict to loose, in that unit's names
     (`symbols` and `functions`): `exact` (name, params, cv) -> `l1` (`long` == `int`, `wchar_t` ==
     `unsigned short`: the ILP32 `size_t` / MSVC typedef differences) -> `l2` (cv ignored) -> `arity` -> `name`;
     undecorated names (`_f`, `_f@N`, `@f@N`, unmangled C++ variables, function-pointer variables
     `R (cc *name)(...)` too) by plain name (`cname`). If the unit has no match, all definitions (`gdef`) then
     all references (`gref`) of every unit are tried (exact/l1/name, and only when one address answers). Last resort: a Ghidra-style name that carries its address (`DAT_`, `FUN_`,
     `g_`, `UNK_`, `PTR_` + 8 hex digits); across units.json such names agree with the recorded address in 99.6 %
     of the cases. Several addresses -> arity, then cv, else `ambiguous`.
   * The reference declaration and the definition at the address are compared for AArch64 argument passing
     (below); mismatches get a bridge or are reported.
   * Addresses that are not decomp definitions (section "Platform, imports, thread-locals"): a referenced `.text`
     address S4's `platform/crt/crt_symbols.tsv` gives a provider binds to that symbol (`provider`), an IAT slot to
     the api `platform/win32/imports.tsv` + `exports.cpp` name for it, a thread-local variable at an image `.tls`
     address to the native TLS block (`tls`), a virtual function defined nowhere to a trap stub.
3. **apply** - `fnvlink.py apply out/ fixed/`: per object, objcopy adds the address symbols of its definitions,
   localizes duplicate strong names and turns natively defined variables into weak address symbols; then the
   undefined symbols are renamed (`elfsyms.rename_undefined`: objcopy `--redefine-syms` refuses several old names
   for one new name, which is the normal case here). In game objects (units and their synthesized vtables, not
   override/platform objects) `__cxa_atexit` becomes `fnvnx_xc_cxa_atexit`, so GCC's static destructors go into
   the game's onexit table (STARTUP.md). Assembles `bridges.S`, compiles `addrmap.c`.
   Between two resolve runs, **probe** - `fnvlink.py probe out/ tree/` - measures the aggregates the ABI verdicts
   need (`probe_requests.tsv` -> `layouts.tsv`, section "Layouts"); the second resolve decides on bytes. The same
   step synthesizes the vtables and typeinfo no compiled object emits (`vtable_requests.tsv` -> `vtables/`, section
   "Vtables"), which the second resolve links as extra objects; `fnvlink.py vtables out/ tree/` runs only that part,
   `FNVNX_NO_VTABLES=1` leaves it out of `probe`.
4. **check** - `fnvlink.py check out/ fixed/`: trial link of everything with `aliases.ld`; counts what is still
   undefined and any multiple definitions.

`out/` and `fixed/` of one run belong together (`aliases.ld`, `bridges.S`, `addrmap.c` and the rewritten objects
name each other's symbols): never pair files of different runs. Write each run to a new directory (seed its
caches by copying `out/cache`, `out/layouts.tsv`, `out/vtables` of the previous one) and switch a `latest` symlink
atomically (`ln -sfn run-N latest.tmp && mv -T latest.tmp latest`) once `check` has passed. Every step can be
rerun after an interruption: `compile.sh` keeps up-to-date objects, the probe keeps its per-unit compiles
(`probe/*.done.json`) and saves `cache.json` after each round, `apply` rewrites `fixed/` object by object; a driver
only needs to remember which steps finished. The probe caches (layout rows, vtable probe results, compiled probe
units, emitted slot functions) are keyed on each unit's content - its prepared source and every local header it
includes, with the probe flags (`layout.unit_key`) - so a seeded cache is reused only for text that did not change;
layout rows carry the key and are probed again when it differs (S9). Every run's summary states the branch commit its tree was prepared
from (a tree prepared before a rules change silently lacks it: f10's tree predated S1 round 7).

## Symbols and files

| Name / file | Meaning |
|---|---|
| `__fnv_<addr8>` | The original address `<addr8>` (lower-case hex). For code: global alias of the native definition, added to the defining object (`--add-symbol`) or, for COMDAT/weak definitions, in `aliases.ld` (`__fnv_X = name;`, whichever copy the linker keeps). For data: absolute symbol `= 0x<addr>` in `aliases.ld` (the data image is mapped there, D3). |
| `__fnv_<addr8>_C2`, `_D2`, ... | Another ctor/dtor variant of the same definition, when the reference asks for it. |
| `__fnvnx_br_<addr8>_<hash8>` | ABI bridge for one (address, reference declaration); hash of the MSVC reference name. |
| `out/objcopy/<obj>.redefine` | `old new` per line (the objcopy `--redefine-syms` format): undefined Itanium name -> address symbol or bridge. |
| `out/objcopy/<obj>.args` | objcopy options for the object (`--add-symbol`, `--localize-symbol`, `--weaken-symbol`, `--redefine-sym`). |
| `out/objcopy/<obj>.undefine` | Definitions `apply` turns into undefined references before renaming (import thunks: their name then follows the `.redefine` mapping to the import; a global `FUN_<addr>` defined by an unrelated body, i.e. not the native at `<addr>` (`out/colliding.tsv`, S5 class 2): its name follows the mapping to `__fnv_<addr>`, and other objects' by-name references bind by address too, never to the helper). |
| `out/objcopy/<obj>.rebind` | Address (and TLS slot) symbols of the variables the object defines at image addresses: `apply` points the object's own data pointers to them at these symbols (split globals, below). |
| `out/aliases.ld` | Linker script: absolute data address symbols, aliases of COMDAT definitions. |
| `out/refs.tsv` | Every (object, undefined symbol): class, tier, address, target, matched MSVC declaration. |
| `out/abi.tsv` | Every reference whose declaration passes arguments differently from the definition: verdict, classes, register moves, notes, both demangled declarations. |
| `out/bridges.S` | Generated bridge thunks (AArch64 asm, ILP32), the trap stubs and the native TLS block (below). |
| `__fnvnx_vtrap_<hash8>` | Trap stub for a virtual function defined nowhere (`vslot-unresolved`): `fnvnx_vslot_trap(this, "virtual function defined nowhere: <name> (slot k of _ZTV...)")`; the weak default in `bridges.S` is a `brk`, the platform may define a reporting one. |
| `__fnvnx_vslot_<vftable8>_<k>[_d0\|_d1]` | Stub for a GCC vtable slot through slot *k* of an image vftable (`vslot-image`, `--msvc-objs`, section "Vtable slots"): tail call through the loader-rewritten word, flags 1/0 for D0/D1. |
| `out/vslots.tsv`, `out/vslots_stats.txt` | Per undefined slot symbol (and implicit D1/D2) the outcome of the MSVC-vftable binding; the counts. |
| `__fnvnx_tls_<addr8>`, `__fnvnx_tls_image` | Slot of the image `.tls` address `<addr8>` in the native TLS block `__fnvnx_tls_image` (layout of the image's `.tls` template; `__fnvnx_tls_image_orig` / `_size` give its range). |
| `out/hand_vptrs.tsv` | Image vftable addresses the sources store into vptrs by hand: how they are mapped, the native vtable, the units and storing classes. |
| `out/extern_text.tsv` | `.text` addresses referenced but defined by no unit (CRT, thunks; stream S4), with the names used. |
| `out/data_defs.tsv` | Variables a unit defines natively at a data-image address. |
| `out/addrmap.tsv`, `out/addrmap.c` | The address map (next section). |
| `out/vtable_requests.tsv`, `out/rtti_defined.txt` | What resolve asks the vtable synthesis for (class, C++ spelling, why, host units), and the C++ names compiled objects define (vtables, typeinfo, thunks, functions: never synthesized or emitted again). Both from the compiled objects only, so a rerun asks the same. |
| `out/vtables/` | `synth.tsv` (host unit, synthesized object, classes), `classes.tsv` (result per requested class), `stats.txt`, `<unit>.vt.S` + `zz_vt_<unit>.o`, the probe sources and objects (`*.done.json`: compile cache), `cache.json` (probe results per host unit and source mtime), `emit_cache.json` (emitted slot functions per host unit). |
| `out/stats.txt` | The numbers below. |

Natively defined variables at data-image addresses (global objects with constructors, mostly the generated
initializer units) are weakened and renamed to their address symbol, so every unit, the defining one included, uses
the image copy, and the native static initializer constructs the object in place, as the original CRT init did.
Renaming alone misses the defining object's own references when they do not go by name (S13, J-0043 "split
globals"): GCC (`-O2`, the toolchain's default PIE) addresses the variables a unit defines from a section anchor - one
`adrp`/`add` to `.data + 0`, then immediate offsets that no relocation shows - so the unit's functions and its static
initializer kept a private copy (`GetRTTI` 0x0049bb10 returned the native `ms_kRTTI`, not 0x011c7d74). Pointing the
anchor's relocations at one variable's image address (round 3) misplaces every other variable of that anchor, and a
PC-relative reference to an absolute `__fnv_` symbol only works at the guest's link address (S5, J-0048: 200 such
relocations in 87 objects). `compile.sh` therefore compiles with `-fPIC`: a defined variable is reached through the
GOT by its own symbol, like the extern ones already were, so renaming rebinds every reference and the GOT slot holds
the image address. Not `-fno-semantic-interposition`: GCC then calls a unit's own functions through local aliases
(`f.localalias`, no relocation), past overrides, import-thunk rebinding and every other by-address binding. Plain
`-fPIC` still makes such aliases for some virtual functions defined with their vtable (423 symbols in 178 objects of
the full set, none branched to); resolve never takes them as definitions. `apply` then only rewrites absolute data
relocations (ABS32/ABS16: pointers in the object's data) that reach a rebound variable through a local symbol
(`elfsyms.retarget_local_relocs`, list in `objcopy/<obj>.rebind`) and reports code references through a local
symbol, which should be none (objects compiled without `-fPIC` keep their private copies there). The same holds for
thread-locals folded into the native TLS block. Emitted slot functions are compiled with `-fPIC` too; the probes,
which are only read, are not.

## Address map (for the data-image loader, S3)

`addrmap.tsv`, format version 1: UTF-8, `\t`-separated, `#` comment lines, sorted by address, one line per
original address (never two lines for one address):

    # fnvnx-addrmap 1
    # address	kind	link_symbol	native_symbol	object	note	offset	d1_symbol	d1_native
    00a7fc00	F	__fnv_00a7fc00	_ZN17NiRenderedTexture6CreateE...	src_NiRenderedTexture.cpp.o

* `address` - 8 lower-case hex digits, address in FalloutNV.exe 1.4.0.525.
* `kind` - `F` function, `C` constructor (complete-object variant), `X` destructor (complete-object variant),
  `G` MSVC deleting destructor (`??_G`/`??_E`, takes a flags argument) mapped to the Itanium deleting destructor
  (D0, no flags, always deletes): a code pointer to it in the image is only safe where the flags are 1;
  its complete-object destructor D1 is in `d1_symbol` and `fnvnx_addrmap_deldtors` (below);
  `V` image vftable named by some unit, primary (`??_7C@@6B@`) or secondary (`??_7C@@6B<B>@@@`,
  ``C::`vftable'{for `B'}``): native = `link_symbol` + `offset`, the Itanium address point of C's own vtable or of
  the vtable of its base-class subobject B, compiled or synthesized (section "Vtables");
  `E` referenced `.text` address that no unit defines (CRT, thunks): native = 0, the loader traps it. A CRT /
  library address with a provider in `crt_symbols.tsv` is an `F` entry naming the provider symbol itself
  (`link_symbol` = `native_symbol` = e.g. `fnvnx_iob_fopen`, note `provider: ...`); every other row of that table is
  an `E` entry, except `data`/`mid_data` rows (data symbols with a bogus address: no entry).
* `link_symbol` - the symbol to take the address of (`__fnv_<addr>`, `_ZTV...` for `V`, the provider for a CRT
  `F`, empty for `E`).
* `native_symbol`, `object` - for people; `note` - free text (`static in its unit`, the `G` caveat, which base a
  secondary `V` stands for, `synthesized`).
* `offset` (`V` only) - byte offset of the address point from `link_symbol`: 8 for a primary vftable (ILP32: after
  offset-to-top and typeinfo, the rule before this column existed), more for a secondary one.
* `d1_symbol`, `d1_native` (`G` only) - link symbol (`__fnv_<addr>_D1`, or `_D2`, identical without virtual bases)
  and Itanium name of the same class's complete-object destructor; empty when the native build has none.

Columns 7-9 were added after the first readers: they are empty where they do not apply, and readers that use the
first six columns see the same rows as before (plus more `V` rows).

The runtime form is `addrmap.c` (`fnvnx_addrmap_entry {unsigned orig; void *native;}`, sorted by `orig`,
`fnvnx_addrmap_count` entries, `fnvnx_addrmap_kinds[i]` the kind letter of entry *i*; header
`tools/link/fnvnx_addrmap.h`), linked into the program so the loader can
binary-search it when it rewrites code pointers in `.rdata`/`.data` (vtables of the image, callback tables).
`fnvnx_addrmap_deldtors[]` (`{unsigned orig; void *d1; void *d0;}`, `fnvnx_addrmap_deldtor_count`, sorted, one per
`G` entry, `d0` == the entry's native) gives both destructors behind an MSVC `??_G(this, flags)`: bit 0 set -> `d0`,
clear -> `d1`, so the kind-`G` adapter needs no vtable search (which cannot work for abstract classes: GCC stores 0
in both destructor slots of their vtables).
Addresses absent from the map are either not compiled yet, CRT/thunks (`extern_text.tsv`), or mid-function; the
loader should point them at a trap that reports the address. Bridges never appear in the map: a pointer taken
from the image is called with the convention of the definition.

## ABI model (`abi.py`)

Both declarations are MSVC signatures (the reference from the calling unit's `symbols`, the definition from
`functions`); the MSVC decoration carries calling convention, member/static, return type and
class/struct/union/enum tags, which Itanium names do not.

* **x86 slots** - thiscall: `this` in ecx, the rest on the stack; fastcall: the first two integer/pointer
  parameters of at most 4 bytes in ecx, edx, the rest on the stack; cdecl/stdcall: stack. Aggregate results add
  MSVC's hidden return-buffer pointer (after `this` for members, first for free functions; for `__fastcall` the
  first stack word, ecx and edx stay for the arguments - V1 defect 1: 0x0056fa00's bridge wrote the result over
  `*this`). Stack offsets are sums of 4/8-byte words with by-value aggregates of unknown size kept symbolically.
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
* A `float` result where the other side declares `double` (or the reverse): x86 hands either over exactly in st(0),
  AArch64 would read d0 where the callee wrote s0. The bridge calls and converts (`fcvt d0, s0` / `fcvt s0, d0`),
  class `ret-float-width` (V1 defect 2: 130 pairs were `same`); `long double` (binary128 here) is `manual`.
* An FP result read as an integer one, or the reverse (`ret-regfile`; V1 round 3 `chain_x87.py`). On x86 a function
  declared `int` that returns what an FP callee left in st(0) hands the value through untouched; a `double` caller
  reads it exactly. AArch64 shares no register between the two, so the bridges use one convention (`abi.ret_carry`,
  in abi and abi2 alike): an FP value in an integer result travels as float bits in w0 (32-bit integers,
  pointers) or double bits in x0 (64-bit integers), and the reading side converts to its own width with `fmov` /
  `fcvt`. Before, a `double` read of an `int` definition got the float's bits in the low half of d0. A double
  through a 32-bit integer is rounded to float; x86 kept all of it. On the farm link `link/9a0243c7eabe`, 32 pairs
  have such results, 3 of them `double` <- `int`.
* A member function's aggregate result (always a hidden buffer on MSVC) against a definition that returns the
  aggregate by value and takes an integer where the buffer pointer lands (V1 `F1-8-member-vs-regs`: an 8-byte POD
  in edx:eax): nothing writes the buffer. abi2 lists the pair `x86-inconsistent`, class
  `result-buffer-not-written`.
* An undecorated C name (`@f@N` fastcall, `_f@N` stdcall, `_f` cdecl) carries no parameter types or result. Resolve
  reads them from a declaration or definition in the unit's prepared source and the local headers it includes
  (`CDecls`), the first one whose x86 argument bytes equal N, so these pairs are compared like decorated ones (V1
  defect 3: 0x00ba3130's missing edx got no `edx-dummy` bridge; 76 pairs left ecx or `this` unpassed unseen).
  Only real declarations count (V1 round 3, R3-2). Comments, string literals and preprocessor lines are blanked
  first, so a decompiler's signature note is not read. The name must stand in declaration context: a type before
  it, after `;`, `{` or `}`, no `return` / `case` / operator, not inside a class body (a member). The parameter list
  is read with balanced parentheses (function-pointer parameters). Typedefs, `struct`/`class`/`enum` tags and
  integer spellings (`unsigned`, `long int`, `__int16`) are resolved. Undeclared, a stdcall/fastcall name gets a
  stand-in for its parameters only: N/4 integers, when the other side passes nothing but 32-bit integers and
  pointers, with the other side's own result type (R3-1: an int result taken by default gave a float callee a
  `fmov s0, w0` bridge that clobbered its result). Otherwise, and for a cdecl name nobody declares, the pair is not
  compared and is bound directly.
* A narrow integer the reference passes (`char`, `bool`, `short`, ...) where the definition reads a 32/64-bit value
  or a pointer in the same register: the AArch64 caller leaves the upper bits unspecified, MSVC's caller pushed the
  value extended, so the bridge extends it in place by the reference's type (`sxtb`/`sxth` for `char`/`short`,
  signed on MSVC and now in GCC too with J-0034's `-fsigned-char`; `uxtb`/`uxth` for `bool`, unsigned types,
  `wchar_t`), class `narrow-extend`. The byte-level re-decision (below) keeps these bridges.

* **Variadic pairs** (`abi.varargs_check`, S2c-abi). AArch64 Linux passes anonymous arguments in the next registers
  and stack slots exactly like named ones (AAPCS64; Apple's variant differs), and va_arg walks them in that order, so
  most pairs with a `...` side need no bridge:
  * variadic reference, fixed definition: fine when the definition reads only what the reference names. That is the
    bulk of the class: the retail exe compiled its debug loggers to `return 0` (0x005b5e40 `Warning`, 0x0040fbe0,
    0x00a29680, 0x00c3c220), defined `f(void)`. A definition parameter in the reference's anonymous part has a type
    only the call site knows: `manual` (a native variadic replacement in `tools/link/overrides/`, like 0x00703c00);
  * fixed reference, variadic definition (0x005accb0's extractor, 0x005e58f0 ApplyPerkModifiers): the reference's
    parameters behind the definition's named ones must be integers, pointers or doubles (a fixed float is 4 bytes,
    a variadic caller would have promoted it), start at the definition's anonymous x86 offset and leave the same
    AArch64 register counts. A `char`/`short`/`bool` there is extended in place (`narrow-extend`). The widths the
    body reads with va_arg are in neither signature (V2 VA2-VA4), so the plugin also records every variadic
    definition (`VADEF`): `none` (no va_start), `escape` (its va_list goes to another function, vsnprintf-like:
    the format decides) or the va_arg classes. The tail must share one width class (4-byte words, 8-byte integers,
    doubles) with every va_arg the body reads, or the body must never call va_start. Without a record, `escape`
    or mixed widths, the pair is `manual`. **Format literals** (`vacall.decide_format`): when the definition hands
    its va_list on (`escape`: 0x00406d00; the 0x00703c00 override, declared `va=escape` in overrides.tsv column 5),
    the plugin records the string-literal arguments of every call of the reference
    (`-fplugin-arg-fnvnx_vacall-want`, `FMT` lines; a readonly array initialized with a literal counts too). The
    definition's last named parameter is the format. Its conversions are parsed in MSVC 2008's syntax
    (`parse_format`: `*`, h/l/w = 4 bytes, ll/I64/q/j = 8, e/f/g/a double, s/p pointer; %n, `$` and `L` are not
    modelled). When every call site reads, word by word, the widths the fixed reference passes, the pair needs no
    bridge. When every site has the same conversions but the words regroup (two ints read as `%lld` or `%f`), the
    definition is compared as fixed with those conversions and bridged (abi2 bytes; the key includes the
    conversions). A non-literal format, sites that disagree and unmodelled conversions stay `manual`. test_vacall
    executes the %s, %lld and %f shapes under qemu;
  * both variadic: the named parts are compared like fixed ones and the anonymous parts must start at the same
    place;
  * definition parameters in the reference's anonymous part are decided **by the call sites** (`vacall.py`). Resolve 1
    lists the units (`vacall_requests.tsv`). `fnvlink.py probe` parses them with `-fsyntax-only` and the plugin
    `tools/link/vacall/fnvnx_vacall.so`, which records each call's argument classes after the default promotions
    (`out/vacalls/<unit>.tsv`). When every call of the reference in the unit passes the same classes, resolve 2
    compares the definition with that fixed signature: no bridge, a register bridge, or an abi2 byte bridge (a double
    or long long read as two ints). The bridge is keyed by the call-site signature. Calls that disagree,
    aggregates by value and units not recorded yet stay `manual`. For a variadic definition only the words its
    named parameters read must agree; 0x00406d00's snprintf-like callers pass any number of arguments. Test:
    `test_vacall.py` covers V1's shapes end to end (plugin, decision, qemu run). On link/c0d6c71516f6, replayed with
    its tree: 32 units recorded, bridge-gap pairs 70 -> 35, then 75 once V2's va_arg widths are checked, then 57 with the format literals.
  * without the call sites, such pairs are `manual`. Their x86 words come from the call site's
    argument types. A long long or a double is two words on x86 and one register on AArch64. A struct by value is
    several words, or a pointer. A struct { float } goes in an s register. V1 refuted (VA-U1..U4) an earlier rule
    that let 32-bit integer/pointer definition parameters of `int f(...)` through. A fixed definition behind named
    reference parameters is often a decompiled stack walk (0x00703c00);
  * a callee-pop side (stdcall/thiscall/fastcall) with stack arguments against a variadic (cdecl) one moved esp on
    x86: `manual`.
  Pairs decided this way carry the class `varargs-tail`. Test: `test_varargs.py` (verdicts, and the `same` and
  bridged pairs executed under qemu-aarch64). On link/c0d6c71516f6 (replayed with `abireplay.py`) 1,011 of 1,310
  varargs rows are bound directly (1,005) or bridged (6); 210 of the 298 left are the logger 0x00703c00 (override).

**Callee-pop bytes** (V1 round 3): a cdecl caller removes what it pushed, any other caller leaves that to the
callee, and a stdcall / thiscall / fastcall callee pops its own stack arguments (MSVC's hidden result pointer
included). When the bytes the definition pops differ from the bytes the reference's caller expects it to pop, x86
returned with esp moved, so one of the declarations is wrong. The pair is `x86-inconsistent` with class
`pop-mismatch`; abi2 counts the bytes with known aggregate sizes. Like `ecx-not-passed`, such a pair keeps the
bridge of its argument moves, unless something else is wrong on x86 too (`this-not-passed`, `size-differs`,
`cast-conflict`, `result-pointer-not-passed`); with nothing to move it is bound directly and listed. V1 counts
1,517 pairs / 2,196 references on f13.

Two prepare POST hooks fix the declarations behind the commonest real bugs of these classes (tools/rules/s2_bodies.py,
EQ's fuzz rows at link/0540aff0d154): **POP2** moves the words a getter's reference pushes too many to the call on
its result whose reference pushes them too few (`buffer->GetChangeFlags(2)` read as a bool ->
`buffer->GetChangeFlags().FUN_004280f0(2)`; `R(D(a, x))` -> `R(D(a), x)`), decided from the words of the
references and definitions in build/units.json; **THIS2** turns a free `__stdcall` caller that hands its own ecx to
a thiscall callee (the exe's code, listed in `THIS2_SITES`) into a member of a shell, or a member caller's call into
`((Shell*)this)->f(..)`. Both add the new decorated reference names to the unit's `symbols`, so the pairs are
compared again and bridged where the decomp's parameter types differ. What they leave is counted in
prepare_stats.tsv (`POP2-SKIP-*`, `THIS2-SKIP-*`) and listed in `<tree>/build/pop2_skipped.tsv`.

Classes in the report: `edx-dummy`, `reg-shift` (same register file, other index), `int-float-param`
(x86 tolerated an int/float swap in a 4-byte slot), `ret-regfile`, `sret-explicit` (one side returns an aggregate,
the other passes the buffer pointer explicitly), `aggregate-param` / `ret-aggregate` (aggregates of different
names, sizes unknown), `varargs`, `ref-passes-fewer`, plus the declaration shape (`thiscall-member->free-fastcall`,
`free-cdecl->static-member-cdecl`, `arity(...)`). `x86-inconsistent`: the pair cannot have worked on x86 as
declared, e.g. `void __cdecl f(void)` calling `T::f()` - the caller relied on ecx already holding `this`; on AArch64
x0 is not preserved, so the declaration has to be fixed in the source (S1 rule or patch).

### Register parameters the reference does not pass (S13 bridge campaign)

A definition parameter in **ecx** that the reference does not pass (`__fastcall f(p0, edx, p2)` defined,
`__stdcall f(void *)` referenced from a member function) is not junk: the x86 callee reads whatever the caller left
in ecx, usually its own `this`. `abi.compare` / `abi2` classify it `x86-inconsistent` (`ecx-not-passed`, like
`this-not-passed`), listed in `abi.tsv` for S1b's XINC1 patches; the arguments the reference does pass still get their
bridge moves (the ecx parameter is often unused - a decompiler artefact: 0x00a75ec0, a stream factory, and
0x00bbbe30 never read ecx - and a direct call would leave them in the wrong registers). A missing **edx** stays the `edx-dummy`
bridge (the decomp's `__fastcall f(T*, int edx_dummy, ...)` for a member function: edx is the x86 caller's scratch).
**The definition's register reads** (`resolve --msvc-code <dir>`, or with `--msvc-objs`; V1's `c3_ecx.py`, S1b's
corrections J-0102). For an `ecx-not-passed` or `this-not-passed` pair, resolve reads the definition's x86 code in
S11's pristine MSVC object (`<dir>/u<index>.obj`, `vslots.Code.entry_use`). Every path from the entry is followed,
and a switch through the function's own jump table goes to every label of the function. The register is used when:

* `read`: an operand, an address or a sub-register reads it while it still holds the incoming value;
* `pass`: it reaches a callee. It is still in the register at a call or tail jump to a callee that takes it, or it
  was pushed to a stack word that lies inside the callee's stack arguments at a call (00e89ad0 `push ecx; call
  h(int)`, 00a5d450's thiscall `Create(a, ecx)`). A callee takes a register when its own pristine code uses it,
  followed recursively through `jmp` forwarders (00a50b10, 00a98eb0, 00a8a9a0). Without code, its declaration
  decides. An indirect call or a jump the reader cannot follow takes both registers;
* a definition S1b renamed (`units_renames.tsv`, 00a43330 ...) is judged by its patched declaration: the patch gave
  it the register its unpatched, pristine code lacks. Renamed references are found under their old names.

The stack is followed by depth: writes through `[esp+k]` / `[ebp+m]` overwrite a pushed word, reads read it, and
`pop` restores it. A callee's argument bytes come from its declaration, else from the bytes pushed since the
previous call. A `push ecx` whose word is overwritten before use is a reserved local (MSVC's SEH prologues in the
bsshader `CreateShader`s): the register stays dead.

When ecx is not used, the x86 callee never looked at it, so the pair is compared again with that parameter dead
(`ecx-unused`) and gets an ordinary bridge, or none. Against the current prepared tree and S11's objects:

* used: all seven S1b cases (00e89ad0 and 00a5d450 `pass`, 00e3dfb0 `read`, 00a43330 / 00a50b10 / 00a98eb0 /
  00a8a9a0 `pass`), and all 211 of V1's TP addresses (196 read, 15 pass);
* dead, and bridged: 0x00a75ec0, 0x00bbbe30, 0x00a8f730, 0x00e92e60, plus the reserved locals 0x00b80e70,
  0x00bc10e0, 0x00bc59b0.

Without the objects nothing changes. The edx side keeps `edx-dummy`: c3 found no definition that reads an edx it
was not passed; two pass it on (0x00a62130).

Sites on the ninth-round objects (callee address: referencing units):

| Callee | Referencing units |
|---|---|
| 0x00a36f80 | `nianimation/bsblendtreadtransfinterpolator.cpp` |
| 0x00a36fd0 | `nianimation/bsblendtreadtransfinterpolator.cpp`, `nianimation/niblendcolorinterpolator.cpp` (S13: 00a564a0) |
| 0x00a4fbd0 | `niparticle/nipsysairfieldairfrictionctlr.cpp` |
| 0x00a5c5b0 | `staging/blk_00a40000_o2.cpp` |
| 0x00a5e5b0 | `nimain/nibspnode.cpp` |
| 0x00a62130 | `nimain/nialphaproperty.cpp` |
| 0x00a6cc80, 0x00a6d0f0 | `niparticle/nipsysresetonloopctlr.cpp` |
| 0x00a75ec0 | `nimain/nimainsdm.cpp` |
| 0x00b4df30 | `staging/blk_00b40000_o2.cpp` |
| 0x00bbbe30 | `bsshader/lighting30shader.cpp` |
| 0x00cac410 | `staging/blk_00ca0000_o2.cpp` |
| 0x00e89ad0 | `niconsolerenderer/nid3dhlslvertexshader.cpp` |

(Gamebryo paths under `src/gamebryo2.2/corelibs/`.) The two `this-not-passed` pairs S13 found unpatched
(0x00ba95e0 from `bsshader/bsshaderbloodsplatterproperty.cpp`, 0x00c0abd0 from `bsshader/lighting30shader.cpp`) are
in `abi.tsv` as `x86-inconsistent`; they need the S1b patch only.

Two bridge divergences are not ABI-model errors:

* **Calls through a cast to another convention** (S13: 00a1c9b0 -> 00a0c900): GetOrLoadXML calls
  `((void (__thiscall *)(void *, const char *, MenuValueDescriptor *)) &FUN_00a0c900)(&map, name, xml)`; the unit
  declares `__fastcall FUN_00a0c900(void *, const char *, MenuValueDescriptor *)`, the definition is the thiscall
  member `(const char *, int)`. On x86 the cast decides (ecx = map, stack = name, xml: the definition gets name), so
  a bridge built from the declaration (third argument -> p0) is wrong. Resolve therefore reads each compiled unit's
  source for casts of a named function to a function-pointer type, `((ret (__cc *)(params)) &NAME)` and
  `reinterpret_cast<ret (__cc *)(params)>(&NAME)` (called at once, or passed on as a callback), and compares the
  definition with the cast's convention and parameters (a `__thiscall` pointer passes its first parameter as `this`;
  no convention is cdecl) for a reference to a free function of that name (`cast-call`; the bridge is keyed by the
  cast). When the unit casts the name two ways that disagree, or also calls it directly with a declaration that
  disagrees with the cast, the pair is `x86-inconsistent` (`cast-conflict`) instead of a guess. On the ninth-round
  tree: 107 casts of 105 names in 74 compiled units; 16 of those names are called directly too, 2 are cast two ways.
* **The variadic logger 0x00703c00** (S13: 00700350): referenced as `int __cdecl (const char *, ...)`, defined as
  `void __cdecl (const char *text, const char *context)` that passes `&context` on as the argument list - an x86
  stack walk: on AArch64 `&context` is a spilled register, and float arguments travel in v registers. `manual`
  (varargs) is the right verdict; the fix is the native variadic replacement in `tools/link/overrides/` (Overrides,
  below). The
  first-fault difference in that function (x86 0, AArch64 0x18) is argument evaluation order.

### MSVC entries at a secondary base (`thisadj.py`, S2c-abi)

MSVC 2008 compiles an override of a virtual function that a non-primary base introduces with `this` pointing at
that base subobject. Callers that hold the full object reach it through an adjustor thunk. GCC compiles the same
function for the full object and emits a `_ZThn<d>_` thunk for the secondary vtable. The exe's vftables tell the
two apart. Such a function's address is a slot only of vftables whose complete object locator has offset d > 0,
never of a primary one. The player's exe has 823 of them (`FNVNX_EXE` / `PLAYER_EXE`; `FNVNX_SECONDARY_ENTRIES`, a
tsv `address<TAB>d`, stands in for tests).

For each one whose native object defines the thunk, the address map's entry names `__fnvnx_msvc_<addr>`. aliases.ld
defines that symbol as the thunk, so image code pointers rewritten through the map (vftable words, function tables)
pass through the thunk, as do x86-convention callers. `__fnv_<addr>` stays the primary entry for the units' own
references to the definition's class, which pass the full object. A member reference through another class (a
decomp shell declared at the subobject: 0x008d4870 calls 0x008805f0 with `this` at +0xa4) passes the subobject, as
the x86 caller did, so it binds to the thunk too (V2 T1: 4 addresses, 7 references at link-head). On link/c0d6c71516f6 79 addresses have a thunk. 461 have none: the decomp defines
them on a shell class that is the subobject itself, so `this` already agrees. 283 have no native definition.
S13b's `thunk-binding` rows (11 on link/0bd144db1464, `this` off by 24, 60, 136, 164, 168) are this case. The
harness has to enter through the map's link symbol to see the fix.

## Overrides (`resolve --override <tsv>`, for native replacements, J-0015)

The table (tab-separated, `#` comments) lists `address`, `native symbol`, then the object or archive that defines it
or free text (the native signature, notes), and an optional MSVC-decorated signature. An override takes precedence
over the decomp definition at that address. For S6n's `render/native/overrides.tsv` (render-001 R1):

* `address` may be `vslot:<vftable>:<slot>`: the MSVC vftable symbol (or its hex address) and a slot index. The row
  stands for the function the image vftable holds in that slot. That function comes from the joined MSVC vftables
  (`vslots.MsvcIndex.table`, so the row needs `--msvc-objs`) and is checked for the slot's existence and for an
  agreed target. The override then replaces that function at its address, everywhere, like an address row;
* `--override-lib <archive>` (S6n's `libnxrender.a`) defines the symbols of the rows whose column 3 is not an
  object path. A row whose symbol the archive's index lacks is refused and logged. The archive goes into the link
  (`overrides.list`, copied by `apply`), and the linker pulls only the members the overrides need;
* rows whose notes say `N1-pending` (planned, not built) are skipped. `stats.txt` counts pending, refused and vslot
  rows (`override rows: ...`).

* every by-address reference binds to it (`__fnv_<addr> = <symbol>;` in `aliases.ld`); the address map entry names
  it, so S3 rewrites image code pointers and vftables to it;
* the decomp definition (all ctor/dtor variants) is weakened in its object and its Itanium name is aliased to the
  override, so the defining object's own calls and every GCC vtable slot naming it reach the override too (a
  linker-script definition wins over the weak copy; COMDAT copies in other objects follow the same way). Static
  (local) decomp definitions and names that also stand for other addresses are left alone and reported;
* `apply` copies the override objects next to the rewritten ones; the ABI check compares each reference with the
  *decomp* definition, so the override must have its signature - unless the table's optional 4th column gives the
  override's own MSVC-decorated signature, which the references are then compared with (a replacement whose
  signature differs on purpose).

The port's own replacements live in `tools/link/overrides/`: `overrides.tsv` (address, symbol, source, signature)
and `build.sh <out dir>`, which compiles them for the game target and writes the table `--override` reads. The pipeline's link stage does that by default (`OVERRIDE=auto`, S2c-abi); before, farm links ran without them and the 0x00703c00 references stayed `manual` (219 rows on link/c0d6c71516f6). First
entry: **0x00703c00, the console logger** - the decomp defines it as `void (const char *text, const char *context)`
handing `&context` on as x86 stack varargs; `fnvnx_log_00703c00(const char *fmt, ...)` formats with `vsnprintf`
under the original's conditions (UI root and key flag, then an active console or `FUN_005b6f70`) and prints through
the console (`ConsoleManager::Print`, 0x0071d0a0) with `%` doubled and no argument list. Its 4th column
(`int __cdecl (const char *, ...)`) makes the variadic and the double-taking references bind to it directly.

`shadowed.tsv` reports, per shadowed definition: the decomp declaration, native name and unit, the override, units
that hold an inline (COMDAT) copy, and units that name the address in units.json but whose object no longer
references it - their callers were inlined or dropped by GCC and keep running the decomp body (rewrite or rebuild
those callers). Test: `test_override.py` (vtable slot, same-object call and alias call land on the override; the
render table's free text, library, pending, refused and vslot rows).

`resolve --extern-text <list>` adds S4's list of referenced `.text` addresses without definition (`addr_ids.txt`:
`0x<addr> names...`) as `E` entries.

## Layouts (`layout.py`) and byte-level verdicts (`abi2.py`)

For every pair that the class-level model above cannot settle, the aggregates passed or returned by value are
measured in the unit that declares them: a probe translation unit includes the unit's source and, per type, emits
`sizeof`, `alignof`, trivial-for-calls (trivially copyable and destructible), `__is_empty` and MSVC 2008's POD bit
as constants (read back from the object), and a function taking the type by value whose spills show the SIMD
registers it arrives in. The POD bit is V1's predicate, checked against MSVC 2008 on 24 kinds (J-0147,
`tests/v1/test_fastcall_nonpod.py`): `__is_aggregate` (no user constructor, no private or protected data member, no
virtual function), no direct base, a trivial destructor and a trivial copy assignment (`__is_trivially_assignable(T &,
const T &)`; a reference member deletes it). A member's own user constructor or copy constructor does not count:
MSVC returns such 8-byte structs in eax:edx, and the earlier `__is_pod && __is_aggregate && no base` called them
non-POD (3 of the 24 kinds wrong, among them the fastcall buffer in ecx). `layouts.tsv` rows carry the unit content
key (column 8), the POD bit (column 9) and the POD rule (column 10, `pod2`); a row without them, or of another rule,
is probed again.
Class: `G1`/`G2` (one/two general registers), `H<n>x<w>` (HFA/HVA: n SIMD registers of w bytes), `IND` (by
reference: over 16 bytes or not trivial for calls). Types the probe cannot name are dropped and stay `manual`.

`abi2` then works on bytes: MSVC x86 locations with sizes (hidden result pointer always for members, for free
functions unless a 1/2/4/8-byte C++03 POD - V1 adversarial A1/A2: `struct C4 { int a; C4() {} }` is trivial for
Itanium but MSVC returns it through the buffer, so `C4 f()` against `void f(C4 *)` is an `sret-explicit` bridge, not
`same`; on three large units 56 of 202 probed types are such small trivially copyable non-PODs), AArch64 chunks per
value (x/v registers, incoming stack, memory
behind a pointer or x8). Each definition value collects its bytes from the overlapping reference values; when every
chunk finds its bytes in the same container at the same offset and the results agree, the pair is `same`
(differently named but layout-identical aggregates, a 4-byte struct vs an int, an HFA vs separate floats).
Otherwise a general bridge is generated: a frame with saved x0-x7/q0-q7/x8, one buffer per definition value filled
from the saved bytes (or through a saved pointer), definition registers / outgoing stack loaded from the buffers,
by-reference parameters get a buffer pointer (or the reference's own pointer when it already points at all the
bytes), then a tail call, or a call followed by the result conversion (hidden result <-> explicit out pointer,
register <-> memory results, MSVC's returned buffer pointer in x0). No constructor or destructor runs for bridge
temporaries (x86 saw a bitwise copy too); ownership stays with the caller. `test_bridges.py` runs eight such bridges
(hidden vs out pointer both ways, 16-byte struct vs 4 ints both ways, 32-byte by-reference struct vs 8 ints,
register-result conversion, pointer vs non-trivial holder, fastcall edx dummy with stack arguments) under
qemu-aarch64 against C++ definitions, with layouts from the real probe.

**abi-manual pairs (D24 option a).** A `manual` pair with an aggregate or result-pointer class (sret-explicit,
ret-aggregate, aggregate-param) that abi2 cannot plan stays unbound (trap stub, cause `abi-manual`, the reason in the
abi.tsv notes as `abi2: ...` and in the stats as `abi_manual_why:*`). What used to keep pairs there, and now binds:
* layouts the probe could not name: a C name's declaration read from source loses its namespace
  (`CopyFromSource(FUN_00cc83b0_Class *, RefBlockArgument)` inside `namespace fnv_00cc83b0`). The probe takes the one
  namespace GCC names ("did you mean `ns::T`"; several candidates get "suggested alternatives" and stay unknown) for
  the type, its first component (`PacketMask::Bits`) or a template and its arguments (`Ref<SolverInfo>`), and spells
  a class that a function of the same name hides as `struct ::T` (`FUN_0040c0e0`). Unnameable rows carry the rule
  `pod2+ns`; older ones are probed again. `abi2.lookup` also takes a unit's unique (or identical) `ns::T` row for a
  bare `T`.
* untagged named parameter types (`?:WPARAM`, a typedef the C-name reader did not expand, or a nested enum): measured
  by the probe like aggregates, parameters only and from 4 bytes (a narrower scalar was widened by the x86 caller,
  AArch64 leaves the upper bits unspecified), never in a fastcall register (an enum goes in ecx/edx, a struct on the
  stack). An untagged result stays manual: whether MSVC returns it through a buffer depends on its being a class.
* function pointers spelled with a parameter name and no blank (`bool(__cdecl* handler)(char *)`): `P` (abi.py).
* a variadic side: compared on its named part. When that is `same`, the pair is `same` (the anonymous arguments are
  where the named ones would be); one that needs a bridge stays manual (the bridge would move the anonymous
  arguments it does not know).
* a void reference against a definition that returns an aggregate through MSVC's hidden buffer: two readings. The
  reference passes the buffer itself (`void f(unsigned char *out, ...)`, sret-explicit) when every definition byte
  comes from the reference that way; otherwise, when it is clean, the declaration lost the result and the arguments
  are matched without the buffer (`result-discarded`: R2's `void P133List::InsertAt(OutIter, P133Camera *const &)`
  against 0x00b156b0; read with the buffer the callee writes through the iterator's first word, and c4fbea9's
  pop-mismatch rule bridged it that way). A memory result (x8) gets a scratch buffer in the bridge's frame (no tail
  call), a register result is not read.

`test_bridges.py` runs these under qemu-aarch64 too (free function's hidden result against a static member's out
pointer, 16-byte ints result against an HFA, an 8-byte struct against two ints, a typedef'd struct against four ints,
void references against a hidden memory result, member and free, and against an explicit out pointer) and checks the
probe's respelling; `test_abimanual.py` the reasons, `abi2.lookup` and the function-pointer spellings. Offline, on
link 9f865b0a's inputs with the branch's sigs (abi.tsv's manual pairs and the newly paired ones, the missing layouts
probed on a prepared tree), abi-manual pairs went 16 -> 2 (aggregate-param 12 -> 1, ret-aggregate 4 -> 1,
sret-explicit 1 -> 0). Left: `std::_Vector_const_iterator<El, std::allocator<El>>` (0x00abfa90), a MSVC STL type the
definition's unit does not declare under libstdc++, and `NavMeshPtr` (0x00464fc0 called through a cast), an
untagged result.

## Vtables (`vtables.py`, kind `V`, D7)

Objects only ever hold Itanium vptrs (DATAIMAGE.md D7); an image vftable address stands for the Itanium address
point of the same class, which the address map gives S3.

**Image vftable -> address point.** units.json names vftables as `??_7C@@6B@` (``C::`vftable'``) or, for a class
with several vftables, `??_7C@@6B<B>@@@` (``C::`vftable'{for `B'}``, B the base whose subobject holds that vfptr;
the first one is the primary vftable). One decorated name can stand for different addresses in different units
(local reconstructions), so the pairs are (name, address). The native side is the vtable group `_ZTV<C>` the linker
keeps (the first definition in link order: GCC emits vtables weak/COMDAT), read with its relocations: each vtable of
the group follows an (offset-to-top, typeinfo) pair. B's offset in C comes from the typeinfo base lists
(`__si_class_type_info` / `__vmi_class_type_info`), those of the object whose vtable is used first (units do not
always agree on a class), searched breadth-first along non-virtual paths, the shallowest match; the address point is
the vtable of the group whose offset-to-top is minus that offset (the primary one for offset 0). Where several decomp
classes name one vftable (`FUN_...` shells beside the real class), the vtable with more slots at that address point
wins (a call through a rewritten image vptr must find its slot), then a compiled one over a synthesized one, then a
name other than `FUN_...`.

**Synthesized vtables and typeinfo.** Resolve asks (`vtable_requests.tsv`) for the classes that have an image vftable
but no `_ZTV` in any compiled object, and for every `_ZTV`/`_ZTI` a compiled object references and none defines (key
function not compiled or never defined, inline-only classes never constructed), each with the units that can name it
(units referencing it first). For each class a probe translation unit on such a host unit (greedy cover, up to three
hosts per class) includes the unit and declares

    namespace __fnvnx_vt { struct V<k> : C { virtual void __fnvnx_key(); }; void V<k>::__fnvnx_key() {} }

so that GCC emits V<k>'s vtable group: C's group plus the key slot at the end of the primary vtable, with V<k>'s
typeinfo and implicit destructors (D1/D0 and their thunks) where C's stand. Dropping the slot and renaming those gives
the group GCC emits for C, word for word (`test_vtables.py` compares with GCC's own output; abstract and key-function
classes alike, C need not be constructible). The typeinfo is built from `std::tr2::direct_bases<C>` (GCC's
`__direct_bases`): per base its `type_info` symbol (`typeid`), offset (the folded constant `(B *)(C *)4096`, minus
4096) and public-ness (`std::is_convertible<C *, B *>`); repeated bases in `std::tr2::bases<C>` set
`__non_diamond_repeat_mask`; GCC's choice of `__class` / `__si` (one public non-virtual base at offset 0) / `__vmi`.
A base typeinfo no object defines is probed in the next round. Anonymous-namespace classes are not synthesized.
Errors in the probe drop only the items GCC names (or the class), and the class moves to its next host.

Retries on one host (link 9f865b0a: 78 trap stubs of D23's `special-unresolved:typeinfo-for` / `vtable-for` /
`VTT-for`, all with a 4 KiB zero block standing in):
* *Units that do not compile after the probe's standard headers* (their own `std::exception` / `std::bad_alloc`,
  or a `using namespace std` swap that `<type_traits>` makes ambiguous: blk_00640000_od, blk_00ac0000_od_ob1_variant,
  blk_00b00000_od_ob1_variant, blk_00e00000_o2, frondengine_ob1_variant): probed again with `HEAD_BARE`, the same
  helpers from GCC's builtins (`__direct_bases`, `__bases`, `__is_convertible`) and a declared `std::type_info`,
  no header. The emitted slot functions of such a host use it too.
* *A class hidden by a function of the same name* (`void FUN_00405310(); struct FUN_00405310 {...};`, the decomp's
  constructor/destructor shells): probed again by its elaborated name `struct ::C`, through a typedef.
* *A class whose scope has only a placement `operator delete`* (V<k>'s implicit deleting destructor finds no usual
  one): V<k> declares its own; only V<k>'s D0, which is renamed to C's, would call it.
* *Classes the derived class cannot stand for* - virtual bases (the VTT and construction vtables of V<k> are not
  C's) or a direct base repeated in the hierarchy (the cast that gives its offset is ambiguous): the key function
  is defined in a probe of the host, `::C::~C() {}` and, for the virtual functions C declares, `auto ::C::f(T)
  const -> decltype(((const ::C *)0)->f(dv<T>())) {}`, so GCC emits C's own vtable group, `_ZTT`, `_ZTC` (hidden)
  and `_ZTI` (`key_group`). A class with only inline virtuals (no key function) gets its complete destructor
  called by qualified name and its `typeid` taken there, so the same data comes out as COMDAT copies. Only the data
  is taken; the functions keep their real names (bound like any slot). A base probed only because another class's
  typeinfo or construction vtable names it gets only its typeinfo. Virtual thunks no object defines
  (`_ZTv<fixed>_<vcall>_f`) are generated: `this += fixed`, `this += *(vptr + vcall)`, tail call. A class whose key
  function its unit defines keeps its reason.
* *What the emitted slot functions name* (`close_emitted`): their code can catch or throw a class whose key
  function is defined nowhere, or keep a COMDAT typeinfo or vtable that names a base's typeinfo or a virtual thunk.
  Such a typeinfo is probed on the same host (typeinfo only, the C++ runtime's `std::` classes excepted), thunks
  are generated, and both are linked into the host's object, round after round for the bases they name.

Output, one object per host unit (`vtables/zz_vt_<unit>.o`, linked after the compiled objects): weak `_ZTV`, `_ZTI`,
`_ZTS` for names no compiled object defines, and the non-virtual thunks (`_ZTh[n]<k>_f`: `sub`/`add w0`, tail call
`b f`) no object defines (GCC emits a thunk next to each out-of-line definition, so few are needed). The second
resolve links each as an extra object of its host unit, resolved with the host's names: slot functions bind by
address like everywhere else. Because all GCC vtables are weak too, a compiled copy wins as soon as its unit
compiles. GCC stores 0 in both destructor slots of an abstract class, genuine and synthesized alike.

**Typeinfo kept with its vtable** (V1 defect 4, round 3). `_ZTV<C>` and `_ZTI<C>` are separate weak COMDAT groups,
and the linker keeps the first copy of each independently. When units reconstruct C with different bases, the kept
vtable's offset-to-top words could name a base the kept typeinfo lacks, and `__dynamic_cast` to it fails. apply's
last pass (`pair_typeinfo`) works in three steps:

* **The copy to keep.** Each class keeps the vtable object's own typeinfo (when it has one), else the first copy in
  `check`'s order. A *view* of a typeinfo is its words and base names, plus the views of its bases as that object
  sees them: its own copy where it has one, else the kept one. Every other copy with a different view becomes a
  reference, wherever it sorts, so another link order keeps the same copy (V1 t2b: a third view sorting after the
  vtable object). `elfsyms.undefine_symbols` also clears `GRP_COMDAT` on the group the symbol signed; an emptied
  group would still win its signature and drop the real copy. Copies of the same view stay: they are
  interchangeable.
* **Base pointers** (V1 t4: C : B in two units, B's second base differs, and B's vtable is kept from the other
  unit). A kept typeinfo names its bases by global symbol, and the kept base can be another unit's view. Where the
  object holding the kept typeinfo has its own, different view of a base, that copy stays as a local
  `_ZTI<B>.fnvnx_view`, with its global definition turned into a reference. The base pointers inside the object's
  kept typeinfos, and inside those views recursively, are retargeted to it (`elfsyms.retarget_relocs`), and the
  other copies of a class that reaches such a view become references. `typeid(B)` and B's own vtable keep the
  global copy; `type_info` equality compares names, so casts between the views still match.
* **Synthesized vtables** (V1 t5) always carry their own view's typeinfo (`vtables.py`), even where a compiled
  object has a copy, so the first step keeps it with the vtable. On the farm link `link/9a0243c7eabe`, all 79 classes
  whose vtable object had no typeinfo were synthesized ones.

Measured on that farm link's objects, which had already been through the earlier pass: 115 classes still had a
copy of another view defined elsewhere, and 64 kept base pointers reached another view. After the pass, both are 0
(120 classes, 467 definitions turned into references, 50 private views in 32 objects, 78 base pointers
retargeted). V1's `c4_definers.py --audit` stays at 0 and the trial-link counts are unchanged; the pass takes 2 s.
On f13's objects the first version of the pass took V1's audit from 36 disagreeing classes (33 by size, 3 by bases)
to 0. Report: `typeinfo_pairs.tsv`.

**Slot functions no object defines.** Inline virtual functions and implicit destructors of a class that no unit
constructs or destroys are emitted nowhere (GCC emits them with the vtable). For the synthesized vtables a compiled
object references (`vtable-ref` requests: classes native code constructs), the slot symbols no compiled object
defines (`rtti_defined.txt`) are emitted from the host unit itself: it is compiled again with compile.sh's own
flags (`compile.sh --print-flags`, `layout.game_flags`: semantics, code generation, `-fPIC`, FTOI2 and the S12 order
plugin; bodies.py's synthesized bodies use the same) plus `-fkeep-inline-functions -ffunction-sections
-fdata-sections`, with a qualified destructor call per class
(`p->E::~E()`: the implicit D1/D2, and with `-fkeep-inline-functions` D0 and the inline virtuals, are defined); a
deleting destructor still missing (a destructor declared, defined in a unit that does not compile) is emulated
under its mangled name: D1, then the deallocation a delete expression would pick (the class's `operator delete`,
unsized first, else the global sized one). In that object the host's own strong definitions are turned into
undefined references (they bind to the real ones, by name or address), static initializers are removed, `ld -r
--gc-sections` with the wanted symbols as roots keeps what they reach, and every remaining global definition is
weakened (COMDAT copies dedupe with the compiled objects'). The result is linked into `zz_vt_<unit>.o`. The
vtables only the address map uses (no compiled object references them) get no emitted slot functions: like the
virtual functions declared but defined nowhere (and without an address) their missing slots stay
`vslot-unresolved`, and resolve binds such slots, in GCC's vtables and synthesized ones, to trap stubs (above)
instead of leaving the link undefined.

### classlayout: one exe decision per RTTI vftable (orchestrator 2026-11-08)

`tools/link/classlayout.py` decides every RTTI vftable of the exe in one pass (`out/classlayout.tsv`, format
fnvnx-classlayout/1; the same file, published on fnv-nx-build `recon/classlayout`, is what S1's prepare reads):

- `com`: IUnknown (or a COM-named class) in the hierarchy descriptor; exe order.
- `minus-one` at k: the first exe slot with MSVC's deleting-destructor shape (exevft.deleting_dtor, through adjustor
  thunks). MSVC places it where the class declares its virtual destructor, and GCC places D1 / D0 at the same spot, so
  native slots 0..k-1 = exe 0..k-1, D1 / D0 at k / k+1, exe slot i > k at native i + 1 (k = 4 for the TESForm family).
- `exe-order`: no deleting destructor.

`exe_vtable_copies` takes this decision first for a pair-less primary table. Two cases stay in exe order whatever it
says, as prepare does: a COM class by its slot names, and a destructor slot that code calls by name (`d22-used`).
d26_classify, family_dtor_pos and D26DS remain only as fallbacks for tables classlayout does not decide (stat
`classlayout-fallback`). `FNVNX_CLASSLAYOUT=0` restores the heuristics alone, and `FNVNX_D26T=0` still turns every
rebuild off. Stats: `classlayout-minus-one(-at-k)`, `classlayout-exe-order`, `classlayout-com`, `classlayout-d22-used`,
`classlayout-no-dtor-stub`, `classlayout-disagree`.

The gate: `classlayout.py <exe> -o <dir>/classlayout.tsv --link <link root>` checks every native table against
transform(exe table) slot by slot (classgate.tsv, causes shift / wrong / trap / pure / null / short; unmapped names
are reported apart). The number of mismatching tables is driven to 0, like traps: 1,233 of 3,269 on 4264bfd7.

The deleting-destructor test (exevft.deleting_dtor) reads the function's own first return: `ret 4` with the flags
test before it. A byte search for `ret 4` ran into the next function (MagicCaster slot 9 ends `ret 8`); 157
decisions changed with it (FNVNX_DELDTOR_STRICT=0: the old search).

### ODR stand-ins (`odr.py`)

Before resolve reads the objects, a plain global class whose COMDAT groups differ between units (vtable, typeinfo
or an inline member: the decomp's unit-local `struct SpawnedSub`, `class SpawnedObject`, ...) and that the exe has
no RTTI class of, gets per-unit vtable and typeinfo names, `<name>.fnvu<unit hash>`. Each unit's constructor then
stores its own vtable. Members keep their names, so their bindings by name are untouched. Real game classes are
left to VTFILL's single transform(exe) table. The pass writes out/odr.tsv and the copies to out/odr/; FNVNX_ODR=0
turns it off.

## Vtable slots through the MSVC vftables (`vslots.py`, D12)

`resolve --msvc-objs <dir>` (or `FNVNX_MSVC_OBJS`): `<dir>/u<index>.obj` are the pristine decomp's units compiled
by MSVC 2008 (tools/equiv; index = position in units.json). Every object that needs a vftable emits it, `??_7C@@6B@`
or ``C::`vftable'{for `B'}``, as a COMDAT behind its CompleteObjectLocator (COL: the vfptr's offset in the complete
object), one `DIR32` relocation per slot naming the MSVC function. Slot targets are followed through `??_E` -> `??_G`
(weak external) and adjustor thunks (`sub ecx, n; jmp f`, by the jump's relocation).

**Views and the join.** Each vftable is one *view*; units.json gives its image address (the unit's binding of the
`??_7` name, else the one address all units give it). Views at one address are one image vftable. A view's slot
target gets an address from the unit's own names (`def`, `ref`), then units.json's definitions or references of the
same decorated name when unique (`gdef`, `gref`), last a `FUN_<addr>` name (`name-addr`: a local class's override
keeps the name of the slot it overrides, so it ranks last). Per slot of the image vftable the candidate of the
view's own class (an override declared by that class) beats an inherited one, then the tier, then the number of
views; equal-ranked candidates that disagree are a *disagreement* (no static target).

**Pairing (Itanium order -> MSVC order).** A GCC vtable of class C at offset-to-top -o pairs with the MSVC view of
C whose COL offset is o: the unit's own view first (same declarations), else the views of other units if they stand
for one image vftable. Inside, GCC's D1 + D0 pair with MSVC's one deleting destructor; other slots pair by signature
(sigs.py keys exact, l1, l2, then name + parameter count when unique), the rest by position after the destructor
expansion. MSVC 2008, checked in test_vslots.py: the overloads one class introduces sit together at the position of
the first, in reverse declaration order (`f(int); g(); f(double); ~Ov(); f(char *)` -> `f(char *), f(double),
f(int), g, ??_E`); a derived class's new overloads of a base name are not merged into the base's group; Itanium has
an extra primary-vtable slot for an overrider of a secondary base's function (MSVC reaches it only through the
secondary vftable).

**Binding.** For an undefined symbol that a vtable of the object names and that resolve left `unresolved`,
`ambiguous` or bound by a loose tier (`arity`/`name`: these pick the wrong overload, e.g. `k(char *)` -> `k(int)`),
and for unresolved implicit D1/D2 (GCC calls a base destructor MSVC inlined): its uses in the object decide (one
symbol stands for all its relocations). All in one image vftable slot: that slot's static target if known, else
the slot itself; several classes: a static target they agree on, else the declaring class's vtable, else the most
common. No usable vtable: the declaring class's MSVC vftable, slot found by signature. A static target becomes the
native definition at the address (`addr` class, ABI check against the slot's MSVC declaration; D0 -> `__fnv_<G>`,
D1/D2 -> `__fnv_<G>_D1` of a `??_G`). Otherwise the symbol becomes `__fnvnx_vslot_<vftable>_<k>[_d0|_d1]` (class
`vslot-image`, in `bridges.S`): load the word at vftable + 4k, which the S3 loader has rewritten to the native
definition, to the deleting-destructor adapter (`(this, flags)`: D0 passes 1, D1/D2 0 in w1; the adapter takes D1
from `fnvnx_addrmap_deldtors`, never from the object's vtable, so the stubs cannot recurse) or to a trap reporting the
original address; a word still inside the image's `.text` (not rewritten) goes to `fnvnx_vslot_trap(this, text)`.
A slot is only called through its own vtable, so an image slot is exact even where a unit's declaration misses an
override. Strict bindings are only compared (`check: ...` in `vslots_stats.txt`). `vslots.tsv`: per symbol the
outcome, static target, image vftable, pairing, MSVC slot and declaration, candidate addresses.

**Validation** (`vslots.py validate <units.json> <msvc objdir> <out>/vslots.tsv`, needs capstone): A. the declared
slot's stack bytes (abi.py's x86 model) against the `ret N` of every static target; B. virtual calls `call [vptr+off]`
through `this` (followed through register copies and stack slots written once) in the thiscall members of classes
whose primary vftable the unit emits: pushed bytes since the prologue or previous call, against the declared slot and
the joined slot's target.

### Measurements (J-0042 inputs: fresh3 tree, 2,729 objects + 733 synthesized vtable objects)

MSVC side: 2,987 objects, 6,639 vftable views (all with a COL), 4,295 with an image address, 2,326 image vftables
(1,248 seen by several units), 13,795 image slots: static target known for 1,980, 4 disagreements (deleting
destructors two units claim for one vftable). View slot targets: def 329, ref 793, gdef 520, gref 2,451, name-addr 678,
pure 1,228, named nowhere 17,424 - the decomp names the bodies of most virtuals under other (shell) names, so the
image slot, not a static address, is what most bindings rest on. Pairing: 4,748 GCC vtables with the unit's own
view, 197 with another unit's, 2,016 without any MSVC view; slots paired exact 17,718, l1 1, arity 3, dtor 5,240,
position 383, unpaired 651; 38 slots MSVC orders differently from the expanded Itanium position.

| | before | with `--msvc-objs` |
|---|---:|---:|
| `vslot-unresolved` references (trap stubs) | 16,401 (7,602) | 7,296 (4,797) |
| `vslot-image` (references through an image slot; stubs) | - | 8,996 (7,260) |
| slot references bound statically (`code`, `code-pending`) | - | 189 |
| `unresolved-ctor-dtor` | 2,396 | 2,315 (15 static, 66 image) |
| loose-tier bindings replaced | - | 14 |
| trial link undefined names | 8,746 | 8,704 |

Remaining traps (7,296 references): 6,251 have no pairing - their vtable's class has no MSVC view at all (4,832;
the unit stores the vptr by hand or never constructs the class in matched code, e.g. `LowProcess`,
`MiddleLowProcess`, `TESReputation`; 1,417 of them local/shell classes), and the declaring class has none either;
1,022 are paired with a view whose vftable name units.json does not bind (no image address); 22 pair a non-destructor
slot with a `??_G` (the unit's declaration lacks the virtual destructor); 1 is pure in MSVC's view. Implicit
destructors: 2,149 of 2,230 D1/D2 belong to classes without a view with a destructor slot.

Checks: of the slot symbols resolve bound strictly by name, 1,587 have the same static target through the vftables,
2,282 none, 75 another: the derived class's image slot holds an override the unit's declaration does not have (e.g.
`TESForm::ClearData` in a `TESNPC` vtable, whose slot is `TESNPC::ClearData`); those vtables call the base function
(a source issue, `check-differs` rows). Validation A: 197 of 199 static targets pop exactly the declared stack bytes
(2 placeholder declarations `virtual void vfn_10(void)` of functions taking arguments, e.g. `BSFile::SetEndianSwap
(bool)`), and 73 of 75 `check-differs` targets. B: 197 `this`-based virtual call sites; where the joined slot's target
is known (74) it pops the declared bytes in 74; push counting matches the declaration at 114 of 184 (nested calls'
arguments pushed earlier), and there all known targets (10) match the pushes.

## Vftables from the player's exe (`exevft.py`, `resolve --exe`, S2c-vt)

`resolve --exe <FalloutNV.exe|none>` (default `FNVNX_EXE`, then `PLAYER_EXE`; build.py passes `link_exe`: FNVNX_EXE,
else a BOOT_IMAGE file, else `none`, and the exe's sha256 is part of the Link fingerprint; link_job.sh takes the
owner's copy through `job_gamedata`). The exe is agent-only (D3): it is read, never copied; its scan is cached in
`out/cache/exe_vftables.json`.

**Scan.** Every MSVC vftable with RTTI follows a pointer to its complete object locator (COL: signature 0, the
vfptr's offset in the complete object, the TypeDescriptor `.?AV<C>@@`, the class hierarchy). `exevft.scan` takes
each (data word -> COL, next word in `.text`) pair as a vftable: class, COL offset, bases, slots (the `.text` words
up to the next non-code word). On the GOG 1.4.0.525 exe: 3,269 vftables, 2,678 primary, in 0.8 s.

**Pairing.** `exe_pair` gives each exe vftable its own class's native vtable (`_ZTV<C>` at the address point whose
offset-to-top is minus the COL offset, the class named by llvm-undname of `??_7<td>6B@`). It replaces a pairing by
decomp names or hand-stored vptrs that names another class, unless the own vtable is shorter than both (a unit's
partial declaration), and adds V rows for vftables no unit names. `exe_vftables.tsv`: per exe vftable the class,
its native vtable, the pairing before and the outcome. Classes with no native vtable are requested for synthesis
(`vtable_requests.tsv`, why `exe-rtti`; hosts: units naming the address, then units naming a member).
Before it, `ctor_store_pairs` (S5 class 3) pairs each constructor-named vtable (`_ZTV<n>FUN_<ctor>`, a placeholder
class) that no unit pairs: the exe's constructor at `<ctor>` (a native `??0` definition) names its vftable, the last
`mov dword ptr [reg], imm32` of an RTTI primary vftable start before its first `ret` (`exevft.ctor_vftable`,
capstone). A free vftable gets the shell in the map; one with a vtable already takes it as a displaced vtable, so
the slot fill and the extended copies (below) reach it either way. `ctor_vftables.tsv`: each candidate and outcome.
On link 9f865b0a's inputs: 390 pairs (7 new V rows, 383 displaced), 319 of them shorter than the exe's vftable.
After `exe_pair`, `member_store_pairs` (link class 4, s5-003 round 2) does the same for the vptrs a constructor
stores at a displacement: its members' (the renderer's RendererMap<N> at +0x610.., one-slot placeholders called at
slot 1) and its secondary bases'. The exe constructor's last `mov dword ptr [reg+d], imm32` of an RTTI vftable per
offset d (0 < d < 0x100000, not through esp / ebp, before the first `ret`: `exevft.ctor_member_vftables`) is paired
with the native vtable its compiled `??0` stores at the same offset (`membervt.vptr_stores`: capstone over the
AArch64 code, `this` through copies and `this + c`, GOT and ADRP/ADD vtable addresses, the last store per offset,
or the class of the constructor called with `this + d`; a call clobbers x0..x18). The decomp's layouts are the
exe's (D11), so offsets compare as they are. A primary vtable takes a free vftable in the map or joins it as a
displaced vtable (extended by `exe_vtable_copies`); a secondary one stands for it in the slot fill only. Named
exclusions (`membervt.RULES`), never anonymous skips: `shader-factory-global-constant-map`, NiShaderFactory's (and
so NiD3DShaderFactory's) `NiTFixedStringMap<NiGlobalConstantEntry*>` at +0x8, whose extension makes WinMain's INI
load (0x005e0200) loop in GetPrivateProfileIntA (S5). `member_vftables.tsv`: per (constructor, offset) the exe
classes, base or member, the native vtable, both slot counts and the outcome. On link 9f865b0a's inputs: 651
offsets in 245 constructors (83 members, 568 secondary bases); 388 already paired (252 by the map, 136 hand-stored
image vftables), 206 new (19 displaced primaries, 187 secondaries for the slot fill), 1 excluded; 14 member vtables
shorter than the exe's (the 6 RendererMaps: 1 slot for 7).

**Slot fill.** `exe_slot_fill`, after the pairing: a native vtable slot that calls a trap stub (`vslot-unresolved`,
a virtual function defined nowhere) is bound to `__fnv_<a>`, the native definition at the address `a` the exe's
vftable holds in that slot (class `vslot-exe`, tier `exe-slot`), when the address map has one.
* The native vtable (group, address point) stands for the exe vftables the address map pairs with it and those of
  its own RTTI class at that offset; where both exist, the RTTI class's own vftable decides (a unit's decomp name
  can pair a second, unrelated vftable with the same native vtable). Image vftables the units name that have no RTTI in the exe (no COL before
  them: 213 on link 31971038913d, many of the shader classes) are read at their address too
  (`exevft.plain_vftables`), bounded by the next known vftable start; with no COL between two of them their real
  end is unknown, so they fill only slots the native vtable has and are never used to extend one.
* GCC slot k is exe slot j = k up to the complete destructor D1; D0 shares D1's slot and every later slot is one
  lower (`exevft.exe_slot_index`, "minus-one rule": MSVC has one deleting destructor). The pair is found by name
  (`D1Ev`/`D0Ev`, their address-bound forms) or, in an abstract class, as two adjacent zero words.
* One undefined symbol is renamed once per object: every vtable of the object naming it must agree on the address,
  else the declaring class's vtable decides, else it stays a trap.
* The synthesized vtable objects (`<unit>#vtables`, vtables.py) are objects of the loop like any compiled one, with
  the same evidence (the exe vftable the vtable is paired with, the exe word at the slot). refs.tsv names them by
  their own source, so the rewrite of the bound rows (`rewrite_refs`) matches that name, never the host unit's. A
  symbol whose uses are bound only per slot (the vtables disagree on the address) keeps its refs.tsv row: the
  words are in the extended copy (slot_fill.tsv `per slot`), and the row has one target column.
* ABI (`slot_abi`): every argument the slot's declaration passes and the definition reads must arrive where the
  definition reads it, in the same register file (abi.a64_locations; a free definition takes the object as its
  first pointer parameter, a free definition without parameters reads nothing). A definition that reads more
  arguments than the slot declares is bound and listed: callers reach a slot through their own class's
  declaration, and the decomp's shells declare placeholders (`FUN_00aa7400`: `virtual void v2();` for
  `ZeroOverheadHeap::Allocate(unsigned, unsigned)`, the boot's first trap). Otherwise the link's x86 model decides
  (`abi.compare` of a thiscall member with the slot's parameters against the definition, `slot_msvc_sig`): same ->
  direct, register moves -> an ABI bridge (`__fnvnx_br_<a>_<hash>`; a pop mismatch alone is accepted as resolve
  accepts it: the decomp's cdecl / stdcall stand-in for MSVC's thiscall code), anything else stays a gap. A
  placeholder slot declaring fewer parameters than the definition is compared as passing the definition's
  (its callers pass them through their own declarations). A
  destructor slot binds to a
  destructor variant at the address; where the decomp defines the exe's `??_G` as a plain function taking the
  flags (`FUN_00c2a090(unsigned int)`), GCC's D0 / D1 go through `__fnvnx_vflag_<a>_1` / `_0` (w1 = flags, then
  `b __fnv_<a>`); a placeholder slot (`virtual void v00();`) where the exe has a native `??_G` goes through
  `__fnvnx_vgdel_<a>` (bit 0 of the caller's flags picks D0 or D1, as the kind-G adapter does).
* An exe slot address with a provider in S4's `crt_symbols.tsv` (or an import thunk) binds to the provider:
  MSVC's `_purecall` at 0x00ec60fa (the pure virtual slots of 670 exe vftable slots) -> platform/crt's
  `_purecall`, the same fatal "pure virtual function call".
* An exe slot address that no compiled unit defines but holds an MSVC thunk (`sub ecx, n` / `add ecx, n`, then
  `jmp f`; or a bare `jmp f`, `exevft.thunk`) binds to f's binding, through `__fnvnx_vadj_<a>` (`sub w0, w0, #n;
  b ...`) when the native vtable's offset-to-top is -n for every vtable naming the slot (else it stays a gap: the
  native subobject layout differs). Extensions take only bare jumps (a primary vtable).
* An exe slot address that no compiled unit defines but whose x86 body is a constant leaf (`ret`, `ret N`,
  `xor eax, eax` / `mov al|eax, imm`, then a return: `exevft.leaf_constant`) gets its exact native form,
  `__fnvnx_vconst_<a>` (`mov w0, #imm; ret`). MSVC folds identical functions, so one body stands in many slots:
  0x00e7cd40 `return 0;` (defined only in nitexturingproperty.cpp, which does not compile) in 1,065 of them.
* A derived class's vtable in the same object that holds another address in that slot (an override the decomp
  does not declare), or vtables that disagree with no declaring class among them: that slot gets its own word in
  a copy of the kept vtable (next point).
* **Vtable copies** (`exe_vtable_copies`): a strong global definition of `_ZTV<C>` in bridges.S (zz_bridges.o)
  wins over GCC's weak COMDAT copies. It holds the kept copy's words with resolve's renames for that object, the
  per-slot words above, and, for a primary vtable shorter than the exe's (a single-vtable group: no secondary
  address point moves), the exe's remaining slots: `__fnv_<a>`, the deleting-destructor stub for a `??_G`, else a
  trap stub naming the exe address. A word that still names a symbol its unit leaves undefined (extern-text
  addresses nothing defines: `BaseFormComponent::Init` / `CopyFromBase` / `CompareWithBase`, whose recipe addresses
  0x00402d40/60/70 lie inside FUN_00402d30) takes the exe slot's word the same way (`xt-slots`): `_purecall` where
  the exe slot is the CRT's pure virtual (only BaseFormComponent's own vftable 0x01011d68 is: every derived class's
  vftable holds real functions there), `__fnv_<a>` of a native definition, else a trap stub for the exe address;
  only where the exe vftables the group stands for agree. Groups that hold a local or non-ABS32 relocation, or a strong (key-function)
  definition, are not copied. The V rows name zz_bridges.o; `vtext.tsv` names the object the words came from,
  which apply's typeinfo pairing takes as the vtable's object.
* Outputs: `slot_fill.tsv` (object, symbol, address, target, how chosen, ABI verdict, the uses), `slot_gaps.tsv`
  (trap slots left, per use, with the reason: no exe vftable, past the exe vftable's slots, not compiled, abi, kind
  differs, vtables disagree). Trap stubs no object uses any more are dropped. Both files are published with the
  link (`out/`).

**Metric** (`vtmetric.py <link dir> <exe>`, read-only on a published link or a build's link stage): over the V rows,
trap slots split by whether the address map has a native at the exe slot's address, primary vftables whose native
vtable is shorter than the exe's (the destructor pair counted once), and mispairings (the native class is another
RTTI class of the exe). S2d-vslot adds `slot-mismatch` (native slot k + 1 against exe slot k by address through the
map, exe slots 0 and 1 and unplaced slots not compared; `--rows` lists them) and `unpaired-<primary|secondary>-<why>`
(exe vftables with RTTI that no V row stands for, by the class's row in vtables/classes.tsv). A vtable-slot name that
resolve bound only through another unit's reference to the same name (gref / gdef tier: units.json puts the call of
a *different* function there, TESForm::IsQuestItem at 0x0046f070 where the exe's TESForm slot 37 is 0x00401190) is a
hint: when no code of the object calls the name, exe_slot_fill replaces the address with the exe vftable's function
at the slot (`hint_rebound`; slot_fill.tsv row `exe vftable over the name's address`) and keeps it where the exe has
none or the function cannot be bound (`hint_kept_*`). W20 extends this to names the unit's own recipe binds (tier
`unit/*`), by what the object's code does with the name: never called (class b, 24 of 208 holder x symbol items on link
0540aff0d154, all of them the unit's own definitions nothing calls) is a hint like the above; called directly (class a,
183 items: `Base::f()` from an override, whose exe call site reaches the bound address in the pristine object) keeps the
symbol at its address for the call and gives each vtable slot that names it, where the exe vftables of that use agree,
the exe's function as a per-slot override (`chint_slot_over`; slot_fill.tsv row `per slot (exe vftable; the name stays
at ...)`). Tests: `test_exevft.py` (synthetic PE: scan, decoys, cache, the minus-one rule),
`test_slotfill.py` (end to end: resolve, apply, trial link, vtmetric).

## Platform, imports, thread-locals and hand-written vptrs

* **CRT and library code** (S4b, RUNTIME.md): a reference to a `.text` address no unit defines that S4's
  `platform/crt/crt_symbols.tsv` gives a provider (`libc: f`, `platform/crt: f`, `fnvnx_f`, the ws2_32 thunks
  through `exports.cpp`) binds to that symbol (class `provider`), never to a libc function of the decomp's name:
  the MSVC stdio functions take the runtime's `_iobuf`. The address is an `F` entry naming the provider.
  `implicit` / `needs body` / unidentified rows stay `E`; `data` / `mid_data` rows are dropped. `resolve
  --crt-symbols <tsv|none>` (default: this tree's file).
* **Import thunks** (S6c): the decomp spells the `jmp [slot]` thunks of `.text` (6 bytes) as functions calling
  the import (`void FUN_00ee6df2(void) { g_D3DXVec3TransformNormal(); }`, `FUN_00ee6e04` with its float angles
  typed `unsigned int`); compiled, they drop or retype the caller's arguments. A decomp definition of 6 bytes whose
  compiled body references nothing but one name its unit binds to an IAT slot is bound to the slot's import
  instead: S4's implementation (`exports.cpp`) or the api name (S6/S7 provide those by name), as a provider (`F`
  entry naming it). Every object that defines the thunk has its definition turned into a reference renamed to the
  import (`objcopy/<obj>.undefine`, applied by `apply` before the renames), so its own calls follow too.
* **Imports**: a call through an IAT slot (`__imp_` reference with the slot's address) binds to the api
  `platform/win32/imports.tsv` names for the slot, through `exports.cpp`'s table (`E("dll", f)`, `K(f)`, `U(f)`,
  explicit rows such as `fnvnx_ws2_*`): the decomp names 31 slots inconsistently, the slot decides. Slots S4 does
  not implement keep the reference's name (S6/S7). `resolve --imports <tsv|none>`.
* **Thread-local variables**: the decomp's `__declspec(thread)` variables are thread-local in GCC's code too
  (initial-exec references); their addresses are in the image's `.tls` template, which S4 copies per thread for
  the code that reaches it through `fs:[0x2c]`. A TLS reference bound to the absolute image address would be one
  copy for all threads, and is no valid TLS relocation. Choice: **native TLS**. Resolve binds every TLS reference at
  an image address to `__fnvnx_tls_<addr>`, the slot of that address in one native TLS block `__fnvnx_tls_image`
  laid out like the template (`.tbss`, at least the template's page; `bridges.S`); a native TLS definition at such
  an address (a unit that defines the variable) is renamed to its slot and weakened, so every name of the variable
  shares one per-thread object. The block covers the template's page (the one of the highest TLS address); a
  thread-local the source places at an address below it (outside `.tls`) gets a native TLS variable of its own. The block starts zeroed in each thread: S4 should point its per-thread template copy
  at it (`&__fnvnx_tls_image` of the current thread, `__fnvnx_tls_image_orig` / `_size`) and copy the image's
  template there at thread start, so `fs:[0x2c]` code and GCC's TLS accesses agree.
* **Data names** (S5, J-0062): a reference to a variable the image holds must bind to its address, or the guest
  keeps a private copy the rest of the game never sees. Three gaps closed: (1) a pointer to a function pointer
  (`void (__cdecl **g_apfnA26E20CurvesCopy)(void *)`, the handler tables `FUN_00a28cb0_aTable0..11`) is parsed as
  a variable of that name; (2) `llvm-undname` drops the space after a type whose name ends in `_` (`class
  SettingT_GameSettingCollection_bKeepLowDetailTerrain`), so a plain variable's qualified name is read from its
  decoration (`?name@scope@@3...`) instead; (3) a data reference that still matches nothing is looked up by its
  identifier among every unit's data names at image addresses - the same variable in another scope (the decomp's
  per-function namespaces) or with another decorated type (an array where MSVC saw a pointer, cv) - and bound when
  exactly one address carries it (tier `gref/ident`; several addresses: `ambiguous`, not guessed). On the
  ninth-round objects: (1)+(2) bind 52 names (101 references), (3) 7 names (12 references), 1 identifier stays
  ambiguous.
* **Variables at a .text address nothing defines** (`text-addr`, S2c-abi): an array (`@@3Q`) or a char label
  (`@@3D`, the `&FUN_00a00000` idiom) the decomp places at a .text address that is no function start. The code uses
  only its address, and on x86 that address is the value. The generated initializers pass immediates this way
  (`g_011c5a4c_arg1` at 0x00a00000, `g_011d3450_arg1` at 0x006e6e6e). The reference binds to the absolute symbol
  `__fnvnx_textaddr_<addr> = 0x<addr>;` (aliases.ld), so the native code passes the same number. Before, these were
  `extern-text` and reached a named stub (S13b native-stub-data: GlobalObject2 0x0045cfe0's second argument). On
  link/c0d6c71516f6 this applies to 11 references in units, plus their copies in the vtable objects. Other variables
  at such addresses (an `int` at 0x00000065) stay `extern-text`, and so does a char label whose address the unit
  passes to a call (`label_is_argument`, a scan of the prepared source). It may be a callback: 0x00c1d8b0 hands
  `&UNK_00fdd3b0`, the static's x86 exit thunk, to `_atexit` (0x00ec658f, V2 X2). It never gets the raw x86 address. A
  thunk of a known shape becomes a native stand-in, `__fnvnx_xexit_<addr>` (`xexit.py`, decoded from the player's
  exe). `niptr` is a NiPointer static's release: decrement the refcount at +4 and, at 0, call native vtable slot
  k + 1 for MSVC slot k (DeleteThis). `thiscall` is `mov ecx, imm; jmp T`: T's native definition with the static.
  0x00fdd3b0 is `niptr`. A label without one keeps the trap; stats.txt lists them. Test: `test_xexit.py`, both
  shapes executed under qemu. A label only stored as data (`m_pDatB = &FUN_00a00000`) keeps the value.
* **Calls of an IAT slot's address**: a decomp `__declspec(dllimport)` function named after its slot
  (`FUN_00fdf1c0(void *)`) is called directly by GCC (`bl`); when its name binds to an IAT slot as data, a call of it
  binds to the slot's api (S4's implementation or the api name), never into the image's IAT words.
* **GAME1 names** (S1, D13): prepare spells a Win32-looking name a unit binds to game code `fnvnx_game_<name>`
  (`GetCurrentThreadId` -> 0x00830470, `Sleep` -> the spin lock's acquire, `EnterCriticalSection` ->
  InterlockedIncrement's IAT slot in memorymanager.cpp); resolve looks such a reference up as `<name>` in the unit's
  recipe, so the unit's own binding decides (a game function by address, or that IAT slot's api).
* **Operator new/delete**: GCC's global forms bind to the game's allocator by the MSVC names the units bind (the
  unit's own binding, else the one most units use), never to the platform's: `_Znwm` -> `??2@YAPAXI@Z`, `_Znam` ->
  `??_U@YAPAXI@Z`, `_ZdlPv` -> `??3@YAXPAX@Z`, `_ZdaPv` -> `??_V@YAXPAX@Z`, the sized forms `_Zd[la]Pv[mj]` -> the
  game's sized delete `??3@YAXPAXI@Z` (0x00aa1460; S13's oracle). Other new/delete forms (placement, extra
  parameters) are decomp declarations and resolve like any name; the nothrow / aligned forms stay runtime.
* **S1b renames** (`patches/units_renames.tsv`): the MSVC names of definitions and references an S1b patch changes.
  Until prepare rewrites units.json itself, resolve applies the rows of every unit whose prepared source carries the
  patch marker (`fnv-nx XINC1`, FTOL1, DBUG1); an unpatched unit keeps its names. Idempotent. `resolve
  --units-renames <tsv|none>` (default: this tree's file).
* **Constructors / destructors GCC needs where MSVC did not** (`unresolved-ctor-dtor`): user-declared members
  that no unit defines. 1,413 of the 1,445 the trial link missed have no body in any pristine MSVC object either
  (S11): MSVC reaches those classes through a deleting destructor `??_G` / `??_E` at an address (a delete
  expression), or never, while GCC calls the member itself (delete expressions call D1, a constructor's landing pad
  destroys the bases and members built so far, emitted slot functions call base destructors). Binding a D1 to the
  `??_G` address is wrong (the decomp defines those addresses as `void f(T *p) { delete p; }`, which always
  frees). Resolve instead binds, per object: a D1/D2 of a class whose deleting destructor address another class
  shares (MSVC's identical-code folding) to that class's destructor when a compiled object defines it
  (`ctor-dtor-alias`); a member that the object reaches only from exception cleanups (the call is followed by
  `bl _Unwind_Resume` before any `ret` / `b` / `br`), or that an emitted slot function calls, to a trap stub
  `__fnvnx_xtrap_<hash>` -> `fnvnx_special_trap(this, "constructor/destructor defined nowhere: ...")` (weak default
  `brk`; `ctor-dtor-trap`). References on normal paths of compiled code stay `unresolved-ctor-dtor`: those members
  need a body or an address (S1). Emitting them from a probe TU (as for slot functions) only works where some
  unit has the body: 32 names, mostly generic shell names (`Base`, `Item`, `Holder`) whose definitions in other
  units may be other classes, so they are left as they are.
* **Hand-written vptr stores** (S4b, DATAIMAGE.md D7 point 2): the sources store image vftable addresses into
  objects by hand (`exception.vtable = (unsigned)&DAT_010176f8;`, `*(void **)this = kVtbl_X;`). Resolve scans the
  compiled units' sources for assignments whose right side is (after casts and `&`) a name the unit binds to a data
  address and whose left side looks like a vptr (a `vtbl`/`vtable`/`vptr`/`vft...` member, `*(T **)p`,
  `((T **)p)[0]`), or whose address is a vftable some unit names. The address stands for the native vtable of the
  class constructed there: the class of the same name when a unit names that vftable (`V` above), else the class
  of the storing member function when it has a native vtable (a new `V` entry). Its data symbol `__fnv_<addr>` is
  then the native address point (`aliases.ld: __fnv_X = _ZTV<C> + off;`), so the store installs the native vptr and
  vptr comparisons match; addresses without a native class keep the image address (`hand_vptrs.tsv`). The storing
  function is the last function head before the store, member (`X::f(...) {`) or free (a column-0 definition): a
  store in a free function credits no class (S13 00404920: `InitLargePrimitiveArray`'s store of 0x0104d338 had been
  credited to the constructor of `FUN_00404920` above it, pairing that class's vtable with 0x0104d338 while the
  constructor stores 0x01012120). MSVC folds identical vftables, so one image vftable can be several classes'
  (0x01012120: `FUN_00404920`, `FUN_00404950`, `FUN_0045cfb0`, `SettingBase_01012120`); the map names one native
  vtable, `vftable_aliases.tsv` every class's own.

  **Image vtables without a named class** (S5 J-0102: on f13, 750 hand-stored image vtables had no native vtable,
  and `*(int*)this = (int)&DAT_x` stores were not seen; the first boot stop is 0x010472d0 in `bgsragdoll.cpp`):

  * A store through a single-pointer cast to a 4-byte integer or pointer at offset 0 (`*(int *)this = ...`,
    `((unsigned *)p)[0] = ...`) also counts, when the address lies within 64 KiB of the named vftables.
  * A decomp shell storing an image vtable is usually the constructor (or destructor) of the class that owns it,
    and another unit often names that address properly. The storing function's address is its `// @fnv`
    annotation, used only when the function stores a single vtable. Every name units.json gives that address
    (`??0X`, `??1X`, `??_GX`, `??_EX`, in any unit) is a candidate class. Among candidates with a native vtable
    (compiled or synthesized), the one with the most vtable words wins, and the image vtable maps to its primary
    address point (`class constructed at the storing address`).
  * Candidates that have no vtable yet go into `vtable_requests.tsv` (why `hand-vptr`, host units: the storing unit
    and the units naming the address). The probe synthesizes those that are polymorphic in a host unit, so the
    second resolve can map them (`requested for synthesis: ...` in `hand_vptrs.tsv`).

  Resolving f13's objects with the new rules gives:

  | Result | Image vtable addresses |
  |---|---|
  | stored by hand | 1,779 (was 1,705) |
  | bound to a native address point | 1,100 (was 954): 911 by a named vftable, 31 by the storing function's class, 158 by the class constructed at the storing address |
  | candidate class requested for synthesis | 363, among them 0x010472d0 |
  | no candidate | 316 |

  The synthesized share is measured on the farm, where the probe runs. A class whose slot order differs from the
  callers' declarations (an MSVC deleting-destructor slot against Itanium's two) still needs S1's class shapes.

  S5's work list (J-0109, `tests/s5/hand_vptrs.py`) added three rules:

  * **Every V entry rebinds its data symbol** (S2:mapped). A V entry's `__fnv_<addr>` is its address point in
    `aliases.ld` whether or not a vptr store was recognised, so a comparison or an unrecognised store reads the
    native vtable too.
  * **Unbound `DAT_<hex>` names count as image addresses** (S2:class). A right side that units.json does not bind
    for the unit still counts when the address is within 64 KiB of the named vftables.
  * **Stores at a nonzero offset are secondary vptrs.** `*(void **)((char *)this + 0xa8) = ...` and
    `((int *)this)[k] = ...` map to the address point whose offset-to-top is minus that offset.

  The two S2:named rows are not link rules. 0x010d1854 (`FUN_00d63cb0`{for `S_B0'}) needs the typeinfo of
  `FUN_00ddcc60`, whose key function is compiled nowhere. 0x01023ac4's `FUN_004f1460` declares only
  `virtual void Dummy()` and is waiting for synthesis.

  On f13's objects:

  | Result | Count |
  |---|---|
  | data references to image vftables rebound | 1,156 (62 of them without a recognised store) |
  | hand-stored addresses | 1,860 |
  | bound to a native address point | 1,101 |
  | candidate class requested for synthesis | 425 |
  | no candidate | 334 |

  The `why no candidate` column of `hand_vptrs.tsv` says what a candidate would need:

  * 235 storing functions are shells: no unit names their address as a constructor or destructor. A candidate
    needs S1 to give the shell a polymorphic class, or the image's RTTI, which needs the exe.
  * 99 stores have no usable storing address: the function stores several vtables at one offset (a base's, then
    its own), or it has no `@fnv` annotation.

  **D26 (c): a minus-one copy for every image vftable left at its image address** (`minus_one_copies`, on
  wip/d26). An address that stays `__fnv_<V> = 0x<V>` while it is stored by hand (`hand_vptr_stores`) or is an
  RTTI vftable referenced as data gets a strong copy `__fnvnx_vmo_<V>` in `bridges.S`, and `aliases.ld` binds
  `__fnv_<V>` to the copy's address point (+8). The copy is in D22's minus-one order:
  * offset-to-top (from the exe's COL, else the store's offset), then typeinfo 0;
  * D1/D0 at slots 0/1 for exe slot 0 when it is the deleting destructor. The address map's G pair is used as it
    is. A decomp `(this, flags)` definition gets flags stubs 0/1; it qualifies by its declaration, its `??_G` name,
    or the exe's code shape (`exevft.deleting_dtor`: `ret 4` after a test of bit 0, which finds 121 of the 132
    G rows). When exe slot 0 is not a destructor, slot 0 is a trap and exe slot 0 goes to slot 1;
  * exe slot k at slot k + 1 (`exe_slot_target`, the same choice as the extended copies).
  Every copy also gets an address-map `V` row (`vmo_vrows`: the copy's symbol, address point 8, object
  `zz_bridges.o`, note "minus-one copy of the exe vftable"), so EQ's vtable pairing and `vtmetric` see the vftables of
  classes the decomp declares without virtual members (vtmetric's `no-polymorphic-view`: 164 primary + 7 secondary on
  link 0540aff0, 163 of them referenced as data) and none keeps `__fnv_<V> = 0x<V>` while a unit stores it.
  W19 (option A): the candidates are every RTTI exe vftable no V row stands for, referenced as data or not
  (`vmo_candidates`; `FNVNX_D26C_ALL=0` restores the referenced-only selection), offset-to-top from the COL. On a
  secondary vftable a slot that is an MSVC adjustor thunk (`sub ecx, n; jmp f`, through at most two more thunks) gets
  a stub (`__fnvnx_vadj_<a>`, destructor slot 0: `__fnvnx_vadjd_<a>_0|1` into f's D1/D0 or flags stubs) only when
  the summed n is exactly the COL offset and f has a native definition (`vmo_adjustor`); any other adjusting thunk
  stays a visible trap. On link 0540aff0's inputs (simulated from addrmap.tsv and the exe's RTTI): 746 copies, all
  with exe slots, 47 beyond the referenced-only 699.
  `out/vmo.tsv` lists the copies. `FNVNX_D26C=0` turns the pass off, so a boot can be compared with and without
  it. On link 9f865b0a's inputs (offline) it makes 831 copies: 615 hand-stored + 239 RTTI-referenced candidates,
  minus 23 with no exe slots. That is 12,256 slots: 10,647 native, 614 flag-stub pairs, 67 G pairs, 96 trap
  slots.

  **A D0 in native slot 1 of a table in the exe's slot order** (W23, `d0_slot_fix` in `exe_vtable_copies`;
  `FNVNX_D0SLOT=0` turns it off). A native `delete p` through a declaration with a virtual destructor calls Itanium
  slot 1. A native vtable with no D1/D0 pair (the decomp declares a placeholder `virtual void BaseVirtual();` where
  the exe has the ??_G; `_ZTV12FUN_00e754a0`, ctor 0x00e754a0, which GraphicsRenderer also declares with a virtual
  destructor) is built or extended in the exe's order, so slot 1 holds exe slot 1. When that is
  `DeleteThis() { delete this; }` (`exevft.delete_this`: the exe's bytes, a call of vftable slot 0 with flags 1) the
  native `delete this` goes through slot 1 again, forever (S5 round 6, link 97deacd38aec). Slot 1 becomes the D0 of
  exe slot 0 (`vmo_dtor_slots`: the Itanium D0, or the flags-1 stub), which is what DeleteThis does in the exe.
  Nothing else moves: a full shift to D26's order would break every caller compiled against the same table in the exe's
  order (the call-only placeholders, e.g. the renderer's slot 54), which D26 (a)/(b) convert in S1-rules, not here.
  vtmetric's `slot-one-delete-this` counts the tables still in that shape (96 on link 0540aff0; the fix's target is 0).

  **Minus-one tables without a pair** (W30, `d26_minus_one_reason` in `exe_vtable_copies`; `FNVNX_D26T=0` turns it off).
  A table made from the exe vftable with no D1/D0 (the probe's synthesis, an extended copy) used to stay in the exe's
  order, while callers compiled against D26 (b)'s `virtual ~C()` dispatch minus-one: `_ZTV12FUN_00aa7400` and
  `_ZTV26FUN_00aa2230_BSNiAllocator` ran the wrong slot (the heap's Allocate ran an empty slot returning `this`). A table
  is now rebuilt whole as a strong copy in D26's layout (D1/D0 from exe slot 0 by `vmo_dtor_slots`, exe slot k at k + 1 by
  `exe_slot_target`, free-function slots through the `__fnvnx_vfree_` this-drop thunks) only on evidence: the exe vftable
  is in S1's D22 list (`d22.json` under FNVNX_RECON) or prepare's applied list (`build/d22_applied.tsv`), or slot 0 is
  a call-only placeholder (D26 (b) gives every call-only declaration `virtual ~X()`, so its callers are minus-one:
  the S1-rules / S2-bodies contract of 2026-11-08, D26 "minus-one everywhere"). Excluded: a slot 0 that code names
  (D22-USED keeps the exe order), and a defined, named slot 0 (BSNiAllocator::Destroy) with no list evidence. Tables
  with no evidence are
  counted as `exe-order-no-evidence`. A caller in the exe's order for the same class (BSNiAllocator's InitHolder
  0x00aa2020 loads offset 28) disagrees with a shifted table: S1 converts it first or together.
  An exe slot 0 that is a `??_G` no unit compiles (no native definition, the exact shape of
  exevft.deleting_dtor_callee) still gives the pair, as two traps for the exe address (W45, `slot0-dtor-not-compiled`,
  `FNVNX_D26U=0` turns it off). Before, such a table counted as `d26-slot0-not-a-destructor` and stayed in the exe's
  order, so `_ZTV12FUN_00aa7400` (ZeroOverheadHeap, `??_G` 0x00aa77e0) ran Deallocate where MemoryManager FUN_00aa3e40
  calls Allocate (minus-one slot 3). Deallocate returned `this`, the pool constructor wrote its name over the heap's
  vptr, and 4,551 initializers faulted at "vslot 3 of 0x063b8d58" (link 8111f58a).

  **A family whose destructor is not exe slot 0** (V2, seams triage of link 5fd996a4; `family_dtor_pos`,
  `FNVNX_D26F=0` turns it off). TESForm, the Actor / Projectile / TESActorBase family and their descendants have four
  placeholder slots (exe 0..3: two nullsubs, 0x485340, 0x4853e0) before the deleting destructor at exe slot 4, so the
  tables with a real pair (Actor, Character, PlayerCharacter, GrenadeProjectile) have D1/D0 at native 4/5 and exe slot k
  at k + 1 after it. A pair-less table of the same exe prefix (Creature, MagicProjectile, TESActorBase, ...) stayed in
  the exe's order (`d26-slot0-not-a-destructor`, or `d22-used` / `defined-slot0` from a nullsub name), so every
  `Actor` caller's slot k + 1 reached the wrong slot (about 240 vcall sites, delta +1). The evidence is a table with a
  pair at slot d whose exe slot d is a deleting destructor (`family_prefixes`: the exe slots before it are the family's
  prefix); a pair-less table with that prefix and a deleting destructor at the same slot is rebuilt like W30's, with the
  pair at d (`d26-family-dtor`). Not covered: multi-vtable groups (FlameProjectile, Character: `multi-vtable`).

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

### Second round (prepared tree with the S8 rules, layouts)

2,067 objects; 107,294 references; 91,076 bound by address (84.9 %); trial link: 0 multiple definitions, 12,832
names undefined (7,303 `__fnv_` of uncompiled units, 5,176 C++ by name, 353 runtime). 484 aggregates probed in 274
units, all named (G1 165, IND 134, H3x4 125, H4x4 21, G2 16, H1x16 15, H1x4 4, H2x4 4). Of the 622 class-level
`manual` refs: 347 `same`, 258 bridged, 2 x86-inconsistent, 15 still manual (varargs); 9 class-level
x86-inconsistent refs became bridges. Now: 384 bridges (417 refs), 68 x86-inconsistent (66 `this-not-passed`,
2 `result-pointer-not-passed`), 15 manual (varargs). Address map: F 24,817, C 895, X 496, G 41, V 446, E 138.

### Third round (vtables, S2c)

Same 2,067 objects (`--extern-text` with S4's list). Synthesis: 1,344 classes requested (584 for an image vftable,
1,217 for an undefined `_ZTV`/`_ZTI`, overlapping), 1,324 named by a host unit, 20 by none; the typeinfo closure
added 621 base classes in five more rounds. Out of 1,945 probed classes: 1,933 vtables and 1,865 typeinfos (with
`_ZTS`) synthesized, 265 non-virtual thunks generated, in 523 objects (one per host unit); left out: 1 vtable
(the derived probe class does not compile), 1 typeinfo (bases not nameable); none had virtual bases. The probe takes
about 45 minutes on 4 busy cores, 9 seconds from its cache.

Address map: `V` 446 -> 1,182 vftable addresses, primary 688 (413 with a compiled vtable, 275 synthesized) and
secondary 494 (395 compiled, 99 synthesized), every one checked in the trial-linked image (native = `_ZTV` +
offset, with the class's typeinfo and offset-to-top = -(base offset) before it). Of the 4,090 (name, address) pairs
not mapped: 1,796 primary and 428 secondary of classes without any vtable (no compiled unit sees the class), 37
secondary whose base is not on the typeinfo chain, 2 whose base has no vptr of its own in the GCC layout. `G`: all 41
with D1 and D0 in `fnvnx_addrmap_deldtors`. References: `special-unresolved:vtable-for` 661 -> 11, `typeinfo-for`
812 -> 13. Trial link: 0 multiple definitions; undefined `_ZTV` 588 -> 14, `_ZTI` 653 -> 13, but the slots of the
synthesized vtables add 2,662 undefined C++ names (1,464 implicit-destructor D0/D1, 1,198 inline or never-defined
virtuals; all `vslot-unresolved`, like the slots of GCC's own vtables) and 126 `__fnv_` of uncompiled units:
12,832 -> 14,406 undefined names.

### Fourth round (slot functions, traps, providers, TLS, hand-written vptrs; S2c)

2,590 objects (of 3,501 units: that day's S1 tree with its variants). Of their 147,688 references: `provider` 657
(489 `extern-text` stay), `import` 145 (114 bound by IAT slot where S4 implements the api), `tls` 29 (S5's
initial-exec references), `vslot-unresolved` 11,445, bound to trap stubs. For the synthesized vtables compiled objects use,
2,327 slot functions no object defines: 338 emitted from 229 host units (`-O2 -fkeep-inline-functions`), the rest
(defined nowhere, or classes only the address map uses) trap; 4,650 trap stubs for 11,445 slot references in all.
Thread-locals: 14 image `.tls` addresses (one outside the template page), 18 native definitions folded into the
block. Hand-written vptr stores: 586 image addresses in compiled sources, 292 bound to a native address point (274
named vftables, 18 by the storing class), 294 without a native class (`hand_vptrs.tsv`: shell structs, the
ios_base / basic_ios / ostream vftables the runtime has no class for). Address map: F 24,982 (165 CRT providers),
C 895, X 496, G 41 (all with D1), V 1,200 (every one checked in the linked image), E 4,447. Trial link: **9,360
undefined names** (7,430 `__fnv_` of uncompiled units, 1,513 C++ by name, 417 runtime/C/platform - the provider and
import symbols the platform objects define), 0 multiple definitions; linked text 6.1 -> 7.7 MB (1.1 MB synthesized
vtables, typeinfo and emitted slot functions, 0.6 MB trap stubs and bridges).

### Fifth round (fresh object set with the J-0034 flags; S2c)

Tree prepared with the current S1/S8 rules and S10's XC1 rewrite, compiled with J-0034's `compile.sh`: 2,729
objects of 2,978 units (249 fail), including WinMain (`FUN_0086a850`, `src/fallout/misc/main.cpp`) and the 6,265
`__fnvnx_dyninit_` / 4,692 `__fnvnx_dynexit_` functions (`F` entries at their original addresses). 182,276
references; with the 733 synthesized vtable objects of the probe, 276,801, 229,769 of them resolved by address.
Probe (22 minutes on 2 workers): 2,346 classes requested, 3,174 vtables and 3,080 typeinfos synthesized, 346
thunks, 641 slot functions emitted from 375 host units, 3,566 slot functions no object defines. References: `code`
116,394, `data` 72,210, `code-pending` 32,927, `vslot-unresolved` 16,401 (7,602 trap stubs), `provider` 1,928 (208
CRT addresses), `extern-text` 764, `import` 1,348 (1,306 bound by IAT slot), `tls` 373 (23 image `.tls` addresses,
154 native definitions folded into the block; none bound to an absolute address). ABI: 1,817 bridges (450 references
with `narrow-extend`), 931 manual, 481 x86-inconsistent. Hand-written vptr stores: 1,344 image addresses, 724 bound
to a native address point. Only 161 variables are defined natively at a data-image address (6,361 before) and 672
duplicate strong names are localized (4,514): XC1 makes the generated globals `extern`, constructed in place at
their image addresses. Import thunks: `0x00ee6df2` -> `D3DXVec3TransformNormal`, `0x00ee6e04` ->
`D3DXMatrixRotationYawPitchRoll` (`F` entries naming the import). Address map: F 52,363, C 1,768, X 1,009, G 88
(all with D1 and D0), V 1,943 (every one checked in the linked image), E 4,449. Trial link of 3,464 objects:
0 multiple definitions, **8,741 undefined names**, by the class resolve gave their references:

| Undefined names | Count | |
|---|---:|---|
| `__fnv_` | 4,859 | addresses of units not compiled yet |
| C++ `unresolved` | 1,605 | no address, no definition |
| C++ `unresolved-ctor-dtor` | 1,442 | implicit ctors/dtors GCC calls where MSVC did not |
| C++ `extern-text` | 148 | `.text` addresses no unit defines and S4 has no provider for |
| C++ `special-unresolved` | 106 | typeinfo 48, non-virtual thunks 31, vtables 27 no object emits |
| C++ runtime / other | 28 | libstdc++ names (21), names bound in one object and not in another (7) |
| C `unresolved` | 202 | C names and decomp data no unit defines |
| platform `provider` / `import` | 276 | defined by S4's platform objects (162) and the import layer (114), not in this link |
| C runtime / other | 75 | `extern-text` data 27, GCC runtime 24, others 24 |

### Sixth round (S13's findings, J-0043; S2c)

Same 2,729 objects (their tree predates GAME1 and S1b). `apply` retargets 852 relocations in 114 objects: S13's
`check_rebind.py` counted 638 code relocations into 92 rebound variables of 86 objects before, 0 after; `GetRTTI`
0x0049bb10 returned the unit's own `ms_kRTTI` (0x010a3bd0 in the trial image) and now returns 0x011c7d74. 2,044
operator new/delete references go to the game's allocator (none to the platform). C++-decorated imports
(`__imp_?X@@...`, 141 in units.json) are parsed through their inner declaration: import references bound by IAT
slot 1,306 of 1,348 -> 1,345 of 1,389. Trial link: 8,741 -> **8,709 undefined names**, 0 multiple definitions
(C++ `unresolved` 1,605 -> 1,570, C++ runtime 21 -> 18, C `unresolved` 202 -> 192; import names the platform layer
provides 114 -> 125).

GAME1 and S1b on a tree prepared with today's rules (GAME1 26 edits in 6 files, XINC1 135 in 49), its 53 compiled
GAME1 / S1b units resolved alone: the 5 `fnvnx_game_` references bind as their unit says (`GetCurrentThreadId` ->
0x00830470; memorymanager's `EnterCriticalSection` / `LeaveCriticalSection` -> the InterlockedIncrement /
InterlockedDecrement slots; one `Sleep` -> the spin lock at 0x008691b0, another -> IAT slot 0x00fdf0d0, SetEvent's,
as the pristine object calls it); `units_renames.tsv` renamed 35 definitions and added 72 reference names (989
definitions matched exactly).

### Seventh round (constructors / destructors; S2c)

Same objects. Of 2,396 `unresolved-ctor-dtor` references (1,445 names), 75 bind to the destructor of a class sharing
the deleting destructor and 1,500 to 1,018 trap stubs (655 reached only by exception cleanups, 363 called by emitted
slot functions); 821 references on normal paths stay. Trial link: 8,709 -> **8,012 undefined names** (unresolved
ctor/dtor names 1,442 -> 738), 0 multiple definitions.

### Eighth round (full object set with today's rules, `-fPIC`; S2c)

Tree prepared with the S1 round-6 rules (STATIC1 644 edits in 127 files, FALL1 12,004 in 462, GAME1, XINC1, XC1;
prepare applies `units_renames.tsv` itself now), compiled with `compile.sh` (`-Werror=return-type -Wno-narrowing`,
`-fPIC`): 2,738 objects of 2,978 units (240 fail), 16 minutes on 2 workers; probe 9 minutes. 279,507 references,
234,079 resolved by address. Definitions: GCC's 423 `.localalias` symbols (178 objects, none branched to) are no
candidates (with them 3,309 definitions had been ambiguous, 1 now). 247 variables defined natively at image addresses,
62 of them STATIC1 copies in objects whose unit never names the address; `apply` retargets nothing (0 code
references through a local symbol: every defined variable goes through the GOT); S13's `check_rebind.py`: 0; S5's
`pcrel_abs.py`: 0 PC-relative references to image symbols (55 to `__dso_handle`, which only the trial link's
`--defsym=__dso_handle=0` makes absolute). 2,074 operator new/delete references to the game's allocator, 6 GAME1
references resolved, 1 call of an IAT slot address bound to its import, 376 TLS references, 1,810 bridges, 7,761 trap
stubs for 16,563 virtual slots, ctors/dtors: 75 aliased, 1,508 to 1,023 traps, 854 left. Address map: F 52,580,
C 1,793, X 1,017, G 88 (all with D1 and D0), V 1,965 (all checked in the linked image), E 4,449. Trial link of 3,477
objects: 0 multiple definitions, **8,027 undefined names**:

| Undefined names | Count | |
|---|---:|---|
| `__fnv_` | 4,790 | addresses of units not compiled yet |
| C++ `unresolved` | 1,606 | no address, no definition |
| C++ `unresolved-ctor-dtor` | 769 | ctors/dtors defined nowhere, called on normal paths |
| C++ `extern-text` | 154 | `.text` addresses no unit defines and S4 has no provider for |
| C++ `special-unresolved` | 111 | typeinfo 48, non-virtual thunks 31, vtables 29, virtual thunks 2, VTT 1 |
| C++ runtime / other | 39 | libstdc++ names 23, names bound in one object and not in another 16 |
| C `unresolved` | 202 | C names and decomp data no unit defines |
| platform `provider` / `import` | 287 | defined by S4's platform objects (162) and the import layer (125), not in this link |
| C runtime / other | 69 | `extern-text` data 28, GCC runtime 24, others 17 |

### Ninth round (tree from branch commit 0c630cf: S1 round 7, FALL1 initializer fix; S2c)

Prepared from `0c630cf` (FALL1 1,171 edits, was 12,004; units_renames 37 + 82), compiled: 2,748 objects of 2,978
units (230 fail). WinMain's units `blk_00ae0000_od`, `blk_00c20000_od`, `blk_004a0000_od`, `nicontrollermanager`
and `main.cpp` compile; 0x00af3020 (S5's boot frontier) is an F entry now. No `__fnvnx_dyninit_` / `_dynexit_`
function contains a trap (6,265 / 4,692; in f10's tree every one did). 235,559 references resolved by address; G 95
(all with D1 and D0); S13 `check_rebind.py` 0, S5 `pcrel_abs.py` 0 image symbols (59 `__dso_handle`, the trial
link's `--defsym`). Trial link of 3,497 objects: 0 multiple definitions, **7,842 undefined names**: `__fnv_` 4,574,
C++ 2,701 (`unresolved` 1,619, `unresolved-ctor-dtor` 779, `extern-text` 152, special 112, runtime / other 39),
C / platform 567 (`unresolved` 203, `provider` 165, `import` 130, others 69).

## Undefined C++ names (J-0108, farm link `link/9844d8dd0fa4`)

The trial link at 9844d8d leaves 5,080 names undefined, 3,762 of them C++. Each was traced to a cause: its resolve
class, a global name lookup over every unit's `units.json` names, and a search of the prepared tree for a body (an
inline definition in a class, or an out-of-class one; the search keys on class and method name, not parameters).

| Cause | Names | Examples | Owner |
|---|---:|---|---|
| member function declared only: no body in the tree, no MSVC name anywhere | 1,713 (252 templates) | `BoneValues::Get(int)`, `CCFA00List::GetSize()`, `NiRefObject::IncRefCount()` (67 refcount methods) | S1: bodies or bindings |
| destructor / constructor declared only | 747 dtors, 126 ctors | `A438D0Name::~A438D0Name()`, `AddOnArray::~AddOnArray()`, `TextureMap::TextureMap()` | S1 (an empty `{}` body would do for most destructors; constructors need the vptr, so they must be compiled) |
| a class's own `operator new/delete` declared only | 501 | `A46TextKey::operator delete[](void*)`, `Attachment::operator delete(void*, unsigned long)` | **S2c, done**: the game allocator |
| extern-text: a referenced address without a definition | 210 | `GameStrcat`, `xSplitPath` | the definition's unit (pending code) |
| a body exists but no translation unit emits it | ~180 (83 dtors, 25 ctors, 34 operator new/delete, 25 members, 6 free) | `BSSimpleList<TESRegionData*>::BSSimpleList(TESRegionData*&)`, `DecodeBlock(...)` | S2c next: emit from a host that sees the body (like the slot functions) |
| free function declared only | 119 | `PipboyFree(void*)`, `consumeRay(...)` | S1 |
| special names: typeinfo / vtable / thunks / VTT of classes no unit emits | 137 | `typeinfo for Base64ce20`, `non-virtual thunk to Own_00b8dad0::FUN_00b8dad0()` | synthesis (vtables.py) or S1 key functions |
| data declared only | 36 | `fnv_00a19f70::LayoutNodeLock`, `PoolAllocatorBase<unsigned int>::ms_kPoolLock` | S1 |
| runtime (C++ library) | 30 | `std::StringBase::_Xlen()` | S4 |
| a unit-local namespaced copy of a class (`fnv_XXXXXXXX::`) | 988 of the rows above | `fnv_00650ea0::NiFixedString::operator char const*() const` | **S2c, done** where the global name has an address (~82) or a native definition (32) |
| only a looser parameter match (l2 / arity) at one address | 13 | `FindKeyInterval(int, int, unsigned char, unsigned char const*)` | **S2c, done** |
| MSVC deleting destructor vs Itanium's two (class shape) | 2 D0 | `Output538c00::~Output538c00()` | negligible: the G entries cover it |

The rules resolve now applies:

* **A class's own operator new / new[] / delete / delete[]** (sized or not) that nothing defines or binds goes to
  the game allocator, like GCC's global forms (`member_newdel_global`, then the NEWDEL table).
* **A unit-local namespaced copy of a class** (`namespace fnv_00650ea0 { struct NiFixedString ... }`): a
  reference that finds nothing is looked up again without the `fnv_XXXXXXXX::` qualifiers, in the unit's names
  and globally. Failing that, it is renamed to the native definition of the global name (`ns-alias`).
* **A looser global match** (l2: typedef / cv spellings, arity) binds when one address carries it; the ABI check
  then compares the declarations.

Expected on the next farm link: about 600 fewer undefined C++ names. Most of the rest are declarations without
a body or an address anywhere: what they need is S1's (bodies, `@fnv` bindings or class shapes), and the table
gives the counts.

## Declared-only functions (`bodies.py`, stream S2d)

`fnvlink.py bodies out/ tree/ [--msvc-objs <pristine dir>] [--exe FalloutNV.exe] [--names <guest undefined.txt>]` runs between two
resolves, on the `refs.tsv` rows still `unresolved`, `unresolved-ctor-dtor` or `ctor-dtor-trap` (the C++ names the
guest link turns into trap stubs; `vslot-unresolved` stays S2c's). Every name gets one class, first match wins:

| class | rule | fix |
|---|---|---|
| `addr` | class shells (`X_shell`) stripped, a unit-local copy (`fnv_<addr8>::X`) taken as the global `X` (never another unit's copy, never another namespace's class of the same name: `SaveManager::String` is not `ExtraSave::String`), the name and level-2 parameters match MSVC names of one address the native build defines (`addrmap.tsv` F; C for C1/C2, X for D1/D2; never G) | the next resolve binds it to `__fnv_<addr>` (`bodies/bind.tsv`, class `body-addr`); C2/D2 to the complete-object definition |
| `template-fold` | a member of a class template over pointer arguments whose other pointer instantiations units.json places at one address the native build defines (two or more there: MSVC's linker folded their identical code) | bound to `__fnv_<addr>` like `addr`; `exe-addr` still wins where the exe gives this instantiation's own address |
| `exe-addr` | (with `--exe`, the player's FalloutNV.exe) a pristine object calls the MSVC counterpart: functions are placed at their units.json address, then at the address the exe's REL32 call to a COMDAT reaches, each only where its bytes equal the exe outside relocations; the exe's displacement at every call of the name (or the exe's word where a placed function takes its address, DIR32), in every pristine object, gives one address the native build defines (F; C/X for ctors/dtors, or an F member function of the same shape; MSVC reference and definition with the same parameter count, member-ness and calling convention: no bridge is made; a constructor/destructor the decomp spells as a function `FUN_<addr>(T*)` of one pointer, or whose address an S4 provider implements (addrmap `provider:`), also binds: natively both take the object in x0) | bound to `__fnv_<addr>`, before any synthesized special member (an address beats `= default`) |
| `exe-addr` by call order | (part of `exe-addr`, with capstone) a name no byte-checked call site gives: every function units.json places is aligned by its call order, the pristine REL32 callees (unplaced callees of the same object expanded in place, as MSVC inlined them) against the exe's direct call/jmp targets in its range (`difflib`, ratio >= 0.5); in a replaced run of equal length a callee without an address takes the exe target at its position. Held out on link 9f865b0a (10 % of the known addresses hidden): 9,040 right, 6 wrong | bound like `exe-addr`, after the same kind and shape checks; a call site always wins |
| `vcall` | a virtual member function GCC calls directly (the object is a member or a local: `a->m.f()`; GCC's front end devirtualizes, MSVC 2008 called through the vptr) and the unit's pristine object's code does not reference it (a qualified `B::f()` call would; a vftable slot of the name does not count) | a stub `__fnvnx_bdvc_<hash>`: `this` adjusted, the function loaded from the object's vtable at the byte offset of GCC's own pointer-to-member constant `&C::f` (probed in the referencing unit), tail call |
| `msvc-empty` | a pristine object defines it (code COMDAT) as `ret`, `ret N` or `mov eax, ecx; ret[ N]` | weak AArch64 `ret` stub (`zz_bd_empty.o`) |
| `msvc-inline` | a pristine object `u<i>.obj` defines a real body: unit *i*'s source has it | unit *i* compiled again with `-fkeep-inline-functions`, only the wanted symbols kept (`vtables.gc_weaken`); a body under a shell's name renamed when the relaxed key pairs one symbol |
| `tree-inline` | (after `vcall`) the tree has the body inline in a unit's own source: `inline C::f() {...}` in a .cpp or an in-class body of a copy of C (`bodycause.tree_bodies`), the class of the same size there as in the referencing units where `layouts.tsv` knows both | that unit compiled again with `-fkeep-inline-functions` like `msvc-inline`, only the wanted symbol kept (`zz_bd_<unit>_t.o`); a host that does not emit it under the wanted name (or the one the relaxed key pairs, never one unit's copy for a global name) leaves it `missing` |
| `special-default` | default / copy constructor, destructor, copy assignment of a non-template class, no pristine body | out-of-line `= default` in the referencing unit (the implicit member's semantics; not a decomp body) |
| `thunk` | a this-adjusting thunk refs.tsv leaves `special-unresolved` (`_ZTh<off>_f`, `_ZTv<off>_n<vcall>_f`): GCC emits thunks only with f's definition, and f is bound by address or synthesized | weak AArch64 thunks (`zz_bd_thunks.o`): w0 adjusted (virtual: plus the vcall offset read from the vtable), tail call of f by name |
| `addr-host` | an exe address resolve binds references to (`code-defmissing`) whose placing unit emits nothing there: a `??_G` (no GCC counterpart) or an inline body GCC did not emit | `??_G`: `__fnv_<addr>(C *, unsigned)` compiled in the placing unit (destroy; delete on bit 0); else the unit compiled with `-fkeep-inline-functions`, the one emitted symbol the MSVC name's relaxed key pairs kept, and `__fnv_<addr>: b` to it (`zz_bd_addr.o`) |
| `decl-data` | a variable the units declare and nothing defines, without an exe address: a static member bodies.py finds `not a function`, or an unmangled name refs.tsv leaves `unresolved` (decomp placeholder globals) | `decltype(N::x) N::x {};` in the first referencing unit (synth_host): what the guest's 4 KiB data stub stood for, now typed and sized; functions and arrays of unknown bound fail and stay missing |
| `crt-rule` | a runtime member the C++ library defines by contract, after the address classes: the MSVC STL throwers `C::_Xlen()` / `_Xran()` / `_Xinvalid_argument()` of the decomp's string and vector copies, a class placement delete `C::operator delete(void*, void*)` | weak stubs (`zz_bd_crt.o`): the message in x0 and a tail call of libstdc++'s `std::__throw_length_error` / `__throw_out_of_range` / `__throw_invalid_argument`, as MSVC threw; placement delete `ret` |
| `missing` | the rest, and synthesized items GCC refuses (`synth-failed`); the note says why (`not virtual`, `MSVC calls it directly`, ...) | listed with the referencing units for the real-exe path (S3) |

Output in `out/bodies/` (local): `bodies.tsv`, `bind.tsv`, `synth.tsv` (objects the next resolve links as extra
objects of their host unit, like `vtables/synth.tsv`), `stats.txt`. The resolve hook is two marked blocks in
`fnvlink.py` (`# ---- S2d`); without a `bodies/` directory resolve is unchanged. The synthesis and vcall probes compile
the host unit with no prelude (`vtables.compile_probe(prelude='')`): `<typeinfo>` before a unit that defines its own
`std::exception` does not compile. Tests: `tests/s2d/test_bodies.py`.

Why a `missing` name has no body: `tools/link/bodycause.py <link out/> <tree> <link fixed/> [--traps names]` gives
each one cause, first that applies: `crt-import` (std/stdext scope, `_X*`, operator new/delete, CRT C names),
`data`, `name-mismatch` (an object of the link defines the same member, shells and unit namespaces relaxed, same
arity, under another decoration), `not-compiled` (the tree has a body: out of line or in class, under `#if 0`, in a
source that is no unit, or in a class copy the referencing unit does not emit), `no-body` (the decomp has none:
`template-special`, `msvc-direct`, `exe-shape`, `other`). `--traps` weighs each class by the names the guest binds to
trap stubs. Output `causes.tsv` (agent-only). Test: `tests/s2d/test_bodycause.py`.

**Names defined only as a GCC clone (decision, W44 on W37's fix).** `name-mismatch` with the detail `defined only as a
GCC clone`: the only definition is a local `.isra.0` / `.constprop.0`. The case in link 4264bfd7 (rows 52 / 53,
`fnvnx_vc9::string::assign(const char*)` and `assign(const string&, unsigned, unsigned)`): projectedshadow_ob1_variant.cpp
declares both as explicit specializations (`template <> ... assign(...);`, MSVC's out-of-line library instance) and
nothing defines them, so the unit references the plain names; every other unit instantiates the in-class template
body implicitly, and where GCC keeps a call out of line it keeps only its IPA clone (internal, its own signature). An
alias is impossible (the clone's parameters differ). Options measured on leafinfo_ob1_variant.cpp and
tesobject_ob1_nogf_variant.cpp: per-unit `-fno-ipa-sra -fno-ipa-cp-clone` does make GCC emit the plain names (weak),
at +184 bytes (+2.1 %) and +372 bytes (+9.2 %) of `.text` in those units, and it rests on GCC still choosing an
out-of-line call there (another tree, another inlining decision, and the name is gone again) while it changes code
generation for every function of the unit. `noipa` / `noclone` on the member would need a change to the shim header,
seen by every unit. Chosen: bodies.py's inst tier for members (W37, `member_inst_line` / `member_inst_hosts`): one
explicit instantiation `template decltype(...) C<A>::m(P);` in a host unit that reaches the body and declares no such
specialization. It always emits the plain (weak) name, costs only that one function's code, and no other unit's code
changes. No compile flag was added.

The farm link gets the exe from `link_job.sh` (`job_gamedata` -> `FNVNX_EXE`); the link stage's fingerprint holds its
sha256. On link c0d6c71516f6 (relink of its out/): 4,425 undefined without bodies, 2,474 with. The 1,002 names left
`missing` are listed by gap class on the private branch `s2d/gaps/<link12>` (agent-only).

## Open problems

* **Virtual functions behind native vtables**: the Itanium/MSVC slot correspondence and the image vftable slots
  (section "Vtable slots", `--msvc-objs`) bind 9,105 of the 16,401 trapped slot references, nearly all through
  stubs that read the loaded image's slot. Left: 6,251 slots of classes no MSVC object emits a vftable for, 1,022 of
  views without an image address. Both need the class's image vftable without a `??_7` name: by its RTTI
  (TypeDescriptor `.?AV<C>@@` -> COL -> vftable, which S3's scanner already lists) at load time or from the player's
  exe at link time (D3), plus MSVC's slot order for such classes (a `/d1reportSingleClassLayout<C>` probe on a unit
  that sees the class, as the GCC probe does for vtables).
* **Missed overrides**: 75 strictly bound slots name a base function where the image vftable has the derived class's
  override (`check-differs` in `vslots.tsv`); the slot could be retargeted per relocation to the image slot, or the
  unit's declaration fixed (S1). 22 slot-0 pairings show declarations without the virtual destructor.
* **Trapped slots**: 4,797 virtual functions (7,296 references) are still defined nowhere and trap when called.
  Emitted host code keeps local copies of the unit's `static` functions and data it reaches (none of its globals:
  those bind to the host).
* **Hand-written vftable references**: only stores are rebound (292 addresses); a raw MSVC-index read through such a
  data symbol now reads the Itanium vtable, so S1's raw-index calls must go through `FNVNX_MSVC_VCALL` (D7 point 4).
  294 stored addresses have no native class (ios_base / basic_ios / ostream: S4b's runtime has no vtable for them).
* **TLS template contents**: the native block starts zeroed; S4 must copy the image's `.tls` template into each
  thread's `__fnvnx_tls_image` (and point its `fs:[0x2c]` copy at it) for non-zero initial values to appear.
* **Varargs** pairs (15) stay manual. **Address map gaps**: `V` covers the 1,182 vftable addresses whose class some
  compiled unit sees; mid-function addresses are not emitted; `E` lists only the addresses compiled units
  reference (138); with `--extern-text` S4's 4,618 are included (4,622 E entries on the round-2 objects).
* **x86-inconsistent** pairs (74) are source bugs that MSVC got away with (ecx coincidence); hand to S1 as a list.
* **Code placement**: the trial link puts native code at the default 0x400000, inside the original image range
  (`.text` 0x401000.., data 0x00fdf000..0x0200c612); the S5 linker script must place native code and data
  outside the image (and below 4 GB for ILP32).
* **Duplicates**: 4,485 strong names defined in several objects (mostly the same `g_` variable or `Derived`
  ctors in several units); localization keeps the link clean, but where two copies differ the address decides.
* `code-defmissing` (173): the definition was inlined away; add `-fkeep-inline-functions` / `__attribute__((used))`
  to those units, or emit them from a forced instantiation.
