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
only needs to remember which steps finished.

## Symbols and files

| Name / file | Meaning |
|---|---|
| `__fnv_<addr8>` | The original address `<addr8>` (lower-case hex). For code: global alias of the native definition, added to the defining object (`--add-symbol`) or, for COMDAT/weak definitions, in `aliases.ld` (`__fnv_X = name;`, whichever copy the linker keeps). For data: absolute symbol `= 0x<addr>` in `aliases.ld` (the data image is mapped there, D3). |
| `__fnv_<addr8>_C2`, `_D2`, ... | Another ctor/dtor variant of the same definition, when the reference asks for it. |
| `__fnvnx_br_<addr8>_<hash8>` | ABI bridge for one (address, reference declaration); hash of the MSVC reference name. |
| `out/objcopy/<obj>.redefine` | `old new` per line (the objcopy `--redefine-syms` format): undefined Itanium name -> address symbol or bridge. |
| `out/objcopy/<obj>.args` | objcopy options for the object (`--add-symbol`, `--localize-symbol`, `--weaken-symbol`, `--redefine-sym`). |
| `out/objcopy/<obj>.undefine` | Definitions `apply` turns into undefined references before renaming (import thunks: their name then follows the `.redefine` mapping to the import). |
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
(`f.localalias`, no relocation), past overrides, import-thunk rebinding and every other by-address binding (and
definition matching sees two candidates; resolve warns and counts them). `apply` then only rewrites absolute data
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
* A narrow integer the reference passes (`char`, `bool`, `short`, ...) where the definition reads a 32/64-bit value
  or a pointer in the same register: the AArch64 caller leaves the upper bits unspecified, MSVC's caller pushed the
  value extended, so the bridge extends it in place by the reference's type (`sxtb`/`sxth` for `char`/`short`,
  signed on MSVC and now in GCC too with J-0034's `-fsigned-char`; `uxtb`/`uxth` for `bool`, unsigned types,
  `wchar_t`), class `narrow-extend`. The byte-level re-decision (below) keeps these bridges.

Classes in the report: `edx-dummy`, `reg-shift` (same register file, other index), `int-float-param`
(x86 tolerated an int/float swap in a 4-byte slot), `ret-regfile`, `sret-explicit` (one side returns an aggregate,
the other passes the buffer pointer explicitly), `aggregate-param` / `ret-aggregate` (aggregates of different
names, sizes unknown), `varargs`, `ref-passes-fewer`, plus the declaration shape (`thiscall-member->free-fastcall`,
`free-cdecl->static-member-cdecl`, `arity(...)`). `x86-inconsistent`: the pair cannot have worked on x86 as
declared, e.g. `void __cdecl f(void)` calling `T::f()` - the caller relied on ecx already holding `this`; on AArch64
x0 is not preserved, so the declaration has to be fixed in the source (S1 rule or patch).

## Overrides (`resolve --override <tsv>`, for native replacements, J-0015)

The table (tab-separated, `#` comments) lists `address`, `native symbol`, `object or archive`. An override takes
precedence over the decomp definition at that address:

* every by-address reference binds to it (`__fnv_<addr> = <symbol>;` in `aliases.ld`); the address map entry names
  it, so S3 rewrites image code pointers and vftables to it;
* the decomp definition (all ctor/dtor variants) is weakened in its object and its Itanium name is aliased to the
  override, so the defining object's own calls and every GCC vtable slot naming it reach the override too (a
  linker-script definition wins over the weak copy; COMDAT copies in other objects follow the same way). Static
  (local) decomp definitions and names that also stand for other addresses are left alone and reported;
* `apply` copies the override objects next to the rewritten ones; the ABI check still compares each reference with
  the *decomp* declaration, so the override must have the decomp definition's signature.

`shadowed.tsv` reports, per shadowed definition: the decomp declaration, native name and unit, the override, units
that hold an inline (COMDAT) copy, and units that name the address in units.json but whose object no longer
references it - their callers were inlined or dropped by GCC and keep running the decomp body (rewrite or rebuild
those callers). Test: `test_override.py` (vtable slot, same-object call and alias call land on the override).

`resolve --extern-text <list>` adds S4's list of referenced `.text` addresses without definition (`addr_ids.txt`:
`0x<addr> names...`) as `E` entries.

## Layouts (`layout.py`) and byte-level verdicts (`abi2.py`)

For every pair that the class-level model above cannot settle, the aggregates passed or returned by value are
measured in the unit that declares them: a probe translation unit includes the unit's source and, per type, emits
`sizeof`, `alignof`, trivial-for-calls (trivially copyable and destructible) and `__is_empty` as constants (read
back from the object), and a function taking the type by value whose spills show the SIMD registers it arrives in.
Class: `G1`/`G2` (one/two general registers), `H<n>x<w>` (HFA/HVA: n SIMD registers of w bytes), `IND` (by
reference: over 16 bytes or not trivial for calls). Types the probe cannot name are dropped and stay `manual`.

`abi2` then works on bytes: MSVC x86 locations with sizes (hidden result pointer always for members, for free
functions unless a trivial 1/2/4/8-byte aggregate), AArch64 chunks per value (x/v registers, incoming stack, memory
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
A base typeinfo no object defines is probed in the next round. Classes with virtual bases (no constant offset; VTT,
construction vtables, virtual thunks) and anonymous-namespace classes are not synthesized. Errors in the probe drop
only the items GCC names (or the class), and the class moves to its next host.

Output, one object per host unit (`vtables/zz_vt_<unit>.o`, linked after the compiled objects): weak `_ZTV`, `_ZTI`,
`_ZTS` for names no compiled object defines, and the non-virtual thunks (`_ZTh[n]<k>_f`: `sub`/`add w0`, tail call
`b f`) no object defines (GCC emits a thunk next to each out-of-line definition, so few are needed). The second
resolve links each as an extra object of its host unit, resolved with the host's names: slot functions bind by
address like everywhere else. Because all GCC vtables are weak too, a compiled copy wins as soon as its unit
compiles. GCC stores 0 in both destructor slots of an abstract class, genuine and synthesized alike.

**Slot functions no object defines.** Inline virtual functions and implicit destructors of a class that no unit
constructs or destroys are emitted nowhere (GCC emits them with the vtable). For the synthesized vtables a compiled
object references (`vtable-ref` requests: classes native code constructs), the slot symbols no compiled object
defines (`rtti_defined.txt`) are emitted from the host unit itself: it is compiled again (`-O2
-fkeep-inline-functions -ffunction-sections -fdata-sections`) with a qualified destructor call per class
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
  vptr comparisons match; addresses without a native class keep the image address (`hand_vptrs.tsv`).

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
