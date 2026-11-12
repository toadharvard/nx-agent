# Data image (stream S3)

Decision D3 (`ARCHITECTURE.md`): the decomp defines code only. The data it references (globals, tables, strings,
vftables of the image, the IAT) is served by the `.rdata`/`.data`/`.bss` of the player's own `FalloutNV.exe`, mapped
at its original virtual addresses. Data-to-data pointers inside it then stay valid as they are, and only the code
pointers stored in that data have to change. This file describes the loader (`loader/`), how it finds those code
pointers, the formats it shares with S2, and what the native (GCC, Itanium ABI) vtables mean for objects that live in
the image.

Numbers marked *(units.json)* were measured on decomp `87eb9373` with the throwaway scripts of this stream. No
address lists derived from the decomp or the exe are committed; the tools write them under `build/`.

## Startup sequence

| Step | Call | Notes |
|---|---|---|
| 1 | `fnvnx_pe_open` | PE32/i386 only, section alignment 4 KiB, sorted non-overlapping sections, every offset checked, the code span must not cover a data section. A `.bind` section (SteamStub) only sets `pe->wrapped` and is left out of the code span. |
| 2 | `fnvnx_pe_check_probes` | Code fingerprints: the decomp's `build/image_check.json`, 16 SHA-256 of code ranges in `.text`; at least 12 must match, the decomp's own rule (`tools/build_and_match.py`). Generated locally by `loader/tools/mkprobes.py` (decomp-derived, never committed). The Steam exe fails here (encrypted `.text`): see "Steam executable". |
| 3 | `fnvnx_image_map` | Every section that is neither code nor discardable is mapped at `load_base + RVA` through the `map_fixed` hook, its file bytes copied and the rest zero-filled (`.bss`, the `.data` tail beyond `SizeOfRawData`). `.text` (and `.bind`) are not mapped. Optionally the header page. Everything must fit the window. |
| 3a | `fnvnx_pe_section_stats`, `fnvnx_image_check_bytes` | Data checks: every mapped section but `.rsrc` must look plain; data fingerprints (string literals and RTTI names at their addresses, `loader/tools/mkdataprobes.py`) must match at >= 90 % when the code probes do not. |
| 4 | `fnvnx_image_load_relocs` / `_relocate` | If the exe has a `.reloc` directory its HIGHLOW locations in mapped sections are kept: they make code-pointer discovery exact. Relocation is applied only when `load_base != ImageBase`, which the target never does (tests do). |
| 5 | `fnvnx_imports_bind` | Each IAT slot (and delay-load IAT slot) gets the native value from the S4 resolver `(dll, name or ordinal, hint)`; unresolved slots get a trap value. |
| 6 | `fnvnx_scan_codeptrs` or `fnvnx_fixups_parse` | Find code pointers at runtime, or load the list `loader/tools/scan_codeptrs.py` produced offline from the same exe. |
| 7 | `fnvnx_fixups_apply` | Each selected record: the word must still hold the original target (else counted as mismatched and left alone), it becomes the address map's native value; S2 kind `G` targets get a flags-honouring adapter (`fnvnx_deldtor_adapter`); targets without a native implementation get a trap stub that reports the original address (`fnvnx_trap_value`). |
| 8 | `fnvnx_image_finalize` | Final protections: read-only for sections without `IMAGE_SCN_MEM_WRITE`. |
| 9 | (S4) CRT start | Runs the image's initializer tables `__xi_a..__xi_z` / `__xc_a..__xc_z` (now holding native pointers) in their original order. See "Native vtables". |

All image addresses are `uint32_t`; host addresses are `VA + bias` (`uintptr_t`). On the Switch `bias == 0`; on a 64-bit
host the window is reserved `PROT_NONE` anywhere and `bias = reservation - window_lo` (`loader/host/fnvnx_host.c`).
Every pointer the loader follows is a value read from the image, so it is an original VA; the bias is added only
to touch memory, never to a value that is stored.

### Address space

| Range | Content |
|---|---|
| `0x00400000` | header page (optional, read-only) |
| `0x00401000..0x00fdf000` | original `.text`: **not mapped**; kept free (see "Trampoline island") |
| `0x00fdf000..` | `.rdata` (IAT first), `.data` + `.bss`, `.tls`, `.rsrc`, up to `SizeOfImage` |
| default window | `0x00400000..0x02100000` |

*(units.json)* referenced data addresses cluster in `0x00fdf000..0x01110000` (`.rdata`), `0x01180000..0x01210000` and
`0x01260000..0x01273000` (`.data`, `.bss`, `.tls`); two outliers far above (one of them bound to a destructor name)
look like decomp artefacts and should be checked against `SizeOfImage` on a real exe.

**Horizon backend (for S5/S4).** `map_fixed(addr, size, prot)` must produce zeroed RW memory at exactly `addr`. In a
32-bit address space process the window lies in the code/ASLR region, where the NSO itself is loaded, so the window
must be kept free of the NSO, heap and stacks. Options, to be confirmed by S5: (a) give the NSO a fixed layout
(ASLR off) with a large zero segment covering the window, so `map_fixed` only has to check the range belongs to it;
(b) back the window with `svcMapProcessCodeMemory` + `svcSetProcessMemoryPermission` on our own process handle (what
libnx's JIT support does), which also allows an executable trampoline island. `protect` is optional.

## Finding code pointers

On disk the data sections hold absolute 32-bit VAs (image base `0x00400000`, no ASLR): data→data pointers (fine as
they are), data→code pointers (vftable slots, function-pointer tables, initializer tables, TLS callbacks, EH/SEH
tables) and, in the loader's own structures, RVAs and names. The exe most likely has no `.reloc`, so nothing says
which word is a pointer. What we know:

*(units.json)*: 68,657 defined function starts (68,607 of them 16-byte aligned, minimum gap between starts 5 bytes);
4,618 referenced `.text` addresses without definition (CRT, thunks), 11 of which lie inside a defined function; 2,333
vftable addresses named by `??_7...@@6B@` (4,090 names); 295 RTTI TypeDescriptors (`??_R0`) named, no
CompleteObjectLocators; 256 data addresses whose decorated type is a function pointer or array of them.

The S2 address map (below) carries all of these: `FUNC`, `TEXT`, `MID` entries. A word is a **candidate** when its
value lies in the `.text` span of the section table. Then, in order:

| Class | Confidence | Rule |
|---|---|---|
| `vtable_col` | high | Slot of a vftable whose word `[-1]` is a valid MSVC x86 CompleteObjectLocator: signature 0, offsets sane, TypeDescriptor with a mapped vptr and a printable `.?A...` name, ClassHierarchyDescriptor with signature 0, attributes < 16, 1..4096 bases whose array is mapped. The vftable runs while words hold `.text` addresses, up to the next vftable start. |
| `vtable_hint` | high | Same, from a vftable address the decomp names (`??_7`), for classes without RTTI (Havok, `/GR-`). Hints also cut a vftable that runs into the next one when no COL separates them. |
| `tls` | high | Entry of the TLS callback array (TLS directory). |
| `reloc` | high | With `.reloc`: only relocated words are candidates at all, and a relocated word holding a known code address is a pointer. |
| `run` | medium | Two or more consecutive known code addresses (tables, vftables nobody names, `__xc_a..__xc_z`). |
| `isolated` | low | A single known code address (e.g. the handler field of a script-command record) that passed the filters. |
| `vptr` | medium | Not code: a word holding an image vftable address for which the map has a `VTABLE` entry (see "Native vtables"). |
| `rej_string` | not applied | Looks like the tail of a string: three printable bytes and a NUL with a printable byte before it, or the UTF-16 equivalent. |
| `rej_round` | not applied | Isolated and the low 12 bits are zero: more likely a size (`0x00500000`) than a pointer. |
| `unknown` | not applied | `.text` address no table knows: EH funclets, functions neither defined nor referenced, chance. |
| `mid` | not applied | Address inside a function: SEH filter/handler, labels. |

Never scanned (their values look like `.text` VAs): every data directory range, each import descriptor's INT/IAT
arrays, hint/name entries and DLL names (normal and delay-load), the debug directory's raw data, the SafeSEH handler
table and a CFG function table named by the load config, resources.

### False positives

A false positive rewrites an integer into a native pointer. Base rates *(units.json)*: a uniformly random word hits
a known start with p = 1.7e-5; a word whose value is in `.text` with p = 0.59 %; a 16-byte aligned word in `.text`
with p = 8.9 %. So the risk is structured values that fall in `0x00401000..0x00fdf000`:

* **RVAs in loader structures** (IAT/INT, SafeSEH, debug): `.text` RVAs `0x401000..0xbde000` are valid-looking VAs.
  Excluded structurally (above); the synthetic test plants such RVAs equal to known starts.
* **String tails**: `"xyz\0"` with `z >= 0x40` is in range; 2,393 known starts have three printable low bytes. Per
  string about 1e-3 (1/4 length mod 4, ~0.85 letter, 1/16 low nibble 0, 8.9 %): tens of hits over the strings of
  `.rdata` without the `rej_string` filter. Remaining ambiguity: a 3-character string right after a NUL.
* **UTF-16**: two wide chars, the second >= `'A'`, are in range: mostly `unknown` noise; caught by the filter when they
  hit a start.
* **Round sizes**: 22 known starts have the low 16 bits zero, 265 the low 12: `rej_round` for isolated words.
* **Floats** in range are denormals (not in game data); low words of doubles and hash tables behave like random
  words (1.7e-5 each; a 1 KiB CRC table expects 0.004).
* **RGB without alpha** (`0x00RRGGBB`, R >= 0x40) is the residual risk: only context helps.

Mitigations available beyond the classes: apply only `>= medium` (`min_conf 2`) and let the trampoline island serve
the rest; promote `isolated` words at the 256 locations the decomp types as function pointers (not yet
implemented); compare the runtime list with the offline one (the tests require them to be identical).

**False negatives** are code pointers to addresses outside the map. In vftables the slot is still found (structural)
and counted unresolved at apply time; elsewhere it is `unknown`. The largest group is expected to be compiler
generated scalar/vector deleting destructors: 183 `??_G` are defined, while every polymorphic class has one.

### Scan cost

S15 measured the scan as 737 M of the 762 M instructions the boot runs up to InitHolder, about 0.9 s on the device
(J-0076). It looked up each of the window's 676 k words five times in the sorted exclusion ranges (`eligible_in`, twice
per word in each of the two COL passes, once in the main pass) and searched the 62 k-entry code map for every
non-`.text` word (the vptr test). The scan now builds per call, with the same answers:

* an **eligibility bitmap** per mapping: bounds, exclusion ranges (sorted and merged by
  `fnvnx_collect_exclusions`, so a word is excluded exactly when a range has `lo < w + 4` and `hi > w`; checked,
  else the binary searches answer) and, in `.reloc` mode, the relocated words; the main pass visits the set bits
  32 at a time;
* a **two-level index** over the code map and over the vftable starts: buckets are address pages (1 KiB for S2's
  map, at most 64 Ki of them), each pointing at its first record, then a binary search inside the bucket;
* a **running cursor** for the vftable holding the current word (the vftables are sorted, non-empty and disjoint;
  checked);
* **zero blocks**: when 0 is neither a `.text` address nor (after relocation) an image vftable in the map, a zero
  word yields nothing, so 128 zero bytes are skipped at once in the COL search and the main pass;
* one COL pass instead of two (the candidate list grows by doubling).

Missing memory, unsorted keys or odd mappings fall back to the plain searches; `FNVNX_SCAN_NO_INDEX` (loadcheck
`--no-index`) forces them. **Same output:** tests/s3 checks the indexed scan against the Python scanner and against
`--no-index` on every configuration, and the S14 synth image with S2c's f12 map gives byte-identical fixup lists and
statistics before and after (no hints; the 40 COL-less hints below; all 2,333 vftables the decomp names: 265
vftables, 967 slots). **Measured** with `tools/perf/frameprof.py` on `loader/bench/` (S3's load sequence as an ILP32
guest under S5's emulator, the boot's calls: scan to count, scan to fill, apply), same image and map:

| | before | after |
|---|---:|---:|
| one `fnvnx_scan_codeptrs` call | 367.1 M (`eligible_in` 191.4 M, `fnvnx_codemap_find` 132.9 M) | 17.4 M |
| the boot's two calls | 734.3 M (S15's boot profile: 737.4 M) | 34.8 M (about 43 ms at IPC 0.8) |

A real exe has fewer zero words than the synth image (about 82 % zero), so expect more there, but the same order of
magnitude. The boot still scans twice (count, then fill); one call with a large enough buffer would halve it (S5).

### COL-less vftables

Gamebryo and BSShader classes are built without C++ RTTI in the original, so their vftables have no COL at `[-1]`.
S14's data checks show this for 45 vftable names at 40 addresses: the word before the vftable holds the end of the
preceding literal or float (`rtti:col-word-other-item`, see the synthetic image section). S3's COL search cannot find
these vftables. Without hints their slots are found only as `run`s (medium confidence) or `isolated` words (low,
not applied), and a neighbouring vftable is not cut off from them.

`synth_image.py ... --checks C --hints-out hints.txt` writes these addresses as S3 hints, one original VA per line
with the evidence in `#` lines. The list is derived from the decomp, so it stays local. Hints reach the scan through
`fnvnx_scan_opts.vtable_hints` or loadcheck `--hints`. A hint is accepted only if its first word is a `.text` address
and eligible, so a wrong hint cannot add a fixup that would not be found anyway. **To be confirmed on the player's
exe:** `loader/tools/check_hints.py FalloutNV.exe hints.txt --s2-addrmap out/addrmap.tsv` gives one verdict per hint:

| Verdict | Meaning |
|---|---|
| `colless` | the hint is needed |
| `has-col` | the class has RTTI after all |
| `col-at-plus4` | the decomp's `??_7` is 4 too low |
| `not-code` | not a vftable here: drop the hint |
| `outside` | not in a mapped data section |

On the synth image all 40 are `not-code`, because their slots have no address there. The boot does not pass hints yet
(`toolchain/game/boot.cpp`: `opts = NULL`); S5 would read them from `sdmc:/fnvnx/game/vtable_hints.txt`, written next
to the exe by the build.

## Steam executable

The player's game is most likely the Steam release, whose `FalloutNV.exe` is wrapped by SteamStub: a `.bind`
section with the wrapper's code, entry point in `.bind`, `.text` encrypted on disk. **We only use the data
sections**, which the wrapper leaves plain, so no decryption is needed and none is ever implemented: the encrypted
`.text` is never read except by the code probes, which then fail. Acceptance (`loader/host/loadcheck`,
`scan_codeptrs.py`, same rules):

1. every mapped section except `.rsrc` must look plain (`fnvnx_pe_section_stats`: chi-square of the byte histogram
   against uniform > 2000 and >= 1 % zero bytes; encrypted or compressed data gives chi2 about 255 and 0.4 % zeros).
   Otherwise `FNVNX_E_DRM` ("the data sections are not plain"): refused, nothing is decrypted;
2. then either the code probes match (plain exe, verdict `code-and-data-checked`) or the data probes do (>= 90 %,
   verdict `data-checked`, e.g. the Steam exe). Data probes are what the decomp's decorated names spell out at fixed
   addresses: the first characters of 5,417 string literals (`??_C@_0...`) and the names of 295 RTTI type
   descriptors (`??_R0`) *(units.json: 5,712 probes, 101,811 bytes)*, so a match also proves 1.4.0.525 layout. They
   are derived from the decomp: `loader/tools/mkdataprobes.py build/units.json build/dataprobes.txt`, local only.

The synthetic tests cover this with a "wrapped" variant (scrambled `.text` plus `.bind`: accepted on data probes,
pipeline identical to the plain exe) and one whose `.rdata` is scrambled too (refused).

## Address map (S2 → S3)

S2's map is authoritative (`docs/LINK.md`, `tools/link/fnvnx_addrmap.h`): `addrmap.tsv` with kinds `F` function,
`C`/`X` complete-object constructor/destructor, `G` MSVC deleting destructor mapped to the Itanium D0, `V` image
vftable (native: Itanium address point), `E` referenced `.text` without definition (native 0), and the
runtime table `struct fnvnx_addrmap_entry {unsigned orig; void *native;}[fnvnx_addrmap_count]`, sorted. The loader
takes it through `fnvnx_codemap_from_s2(map, os, (const fnvnx_s2_addrmap_entry *)fnvnx_addrmap,
fnvnx_addrmap_count, kinds, extra, nextra)` (the loader's own types are named `fnvnx_codemap*` so both headers can
be included in one unit; `fnvnx_s2_addrmap_entry` has S2's layout):

* `kinds` - S2's parallel array `fnvnx_addrmap_kinds[]`: `F/C/X` -> FUNC, `G` -> FUNC + deldtor, `V` -> VTABLE (the
  scanner rewrites words holding the vftable address as vptrs), `E` -> TEXT (trapped); `NULL` means all `F`.
  `AddrMap.read_s2_tsv` reads the TSV the same way (`V` native `<link_symbol>+<offset>`).
* `extra` - entries S2 does not have: `TEXT` (referenced `.text` addresses without definition, from
  `extern_text.tsv` or S4) and `MID` (inside a function). They tell the scanner what is code even where there is
  nothing to bind; S2's entry wins where both exist.
* **Kind `G`.** An image pointer to `??_G`/`??_E` is called as `void *f(void *this, unsigned flags)`; D0 always
  deletes. `fnvnx_deldtor_adapter` hands out one of 1,024 adapters per distinct D0: flags bit 0 set -> D0; clear ->
  D1 from S2's table `fnvnx_addrmap_deldtors[] {orig, d1, d0}` (found through a weak reference when S2's
  `addrmap.c` is linked, or set with `fnvnx_deldtor_set_table`); bit 1 (vector delete) or no D1 -> trap. Only
  without any table (host tests) is D1 searched in the object's own Itanium vtable as the slot just before D0 (up to
  the first slot outside `fnvnx_deldtor_set_text`'s range): GCC stores 0 there for abstract classes.
* **Addresses not in the map** get one of 4,096 trap stubs that reports the original address (`fnvnx_trap_value`,
  `fnvnx_trap_report`), as LINK.md asks.

Offline, `scan_codeptrs.py --s2-addrmap out/addrmap.tsv --units build/units.json` merges S2's map with the
TEXT/MID addresses of the decomp. The loader's own internal form (also used by the host tests, where natives are
32-bit tokens) is below.

Text form of the loader's map:

    # fnvnx addrmap v1
    0x00500010	FUNC	-	_ZN3Foo3BarEi
    0x00500020	FUNC	init	_Z13gFoo_dyn_initv
    0x00500030	TEXT	-	-
    0x00500044	MID	-	-
    0x01000100	VTABLE	-	_ZTV3Foo+8

(illustrative addresses and names)

Columns: original VA; kind `FUNC` (defined in the decomp), `TEXT` (referenced `.text` address without definition;
native from S4 if it implements it), `MID` (referenced address inside a defined function), `VTABLE` (an image
vftable; native = the Itanium address point of the same class); flags `-` or `init` (`??__E`), `atexit` (`??__F`),
`bridge` (native is an ABI bridge, not the function), `deldtor` (S2 kind `G`); native symbol (`-` = none).

Binary form (`fnvnx_codemap_from_blob`, 4-byte aligned; what `fnvnx_codemap_from_s2` produces in memory):

    "FNVAMAP1"  u32 version=1  u32 count  u32 entry_size=12  u32 flags=0
    count x { u32 orig; u32 native; u32 info }        sorted by strictly increasing orig
    info = kind (FUNC 1, TEXT 2, MID 3, VTABLE 4) | init 0x100 | atexit 0x200 | bridge 0x400 | deldtor 0x800

Lookup is a binary search (73 k entries: 17 steps). `MID` and native-less entries are kept: they tell the scanner
what is code even where there is nothing to bind.

## Fixup list (S3 offline tool → runtime)

    "FNVFIX01"  u32 version=1  u32 count  u32 pe TimeDateStamp  u32 pe SizeOfImage  u32 entry_size=12  u32 0
    count x { u32 loc; u32 target; u8 class; u8 confidence; u16 aux }    sorted by loc (original VAs)

`aux` is the slot index in the vftable or run. The runtime refuses a list for another image and checks each word
before writing it. `loader/tools/scan_codeptrs.py` writes it (`--out`), plus a readable list (`--list`), counts
(`--report`), the vftables found with their RTTI class names (`--vtables`) and every vftable slot with its original
target (`--vtable-slots`). With `--units` it builds the address map straight from `build/units.json`. All of it is
derived: local only.

**Image reader for S2** (filling native vtables from image vftables): `fnvnx_image_vtable_slots(img, vftable,
out, cap, &n)` reads the slots of the vftable at an original VA (e.g. a `??_7Class@@6B@` address) as original code
addresses, before fixups; it stops at the first word outside `.text`, so pass the slot count you know as `cap`. In
Python (S2's tools are Python): `Image(PE(data)).vtable_slots(va, n)`, or the `--vtable-slots` listing.

## Native vtables

Objects built by our GCC code carry Itanium vtable pointers; the image's vftables are MSVC layout:

| | MSVC x86 (image) | Itanium (GCC, ours) |
|---|---|---|
| before the address point | `[-1]` CompleteObjectLocator | `[-1]` `std::type_info*`, `[-2]` offset-to-top (+ vcall/vbase offsets) |
| virtual destructor | one slot: scalar/vector deleting destructor `??_G`/`??_E (this, unsigned flags)` | two slots: D1 complete destructor, D0 deleting destructor |
| slot order | declaration order, overloads of one name grouped (MSVC is known to reverse them; verify per class) | declaration order |
| multiple inheritance | one vftable (own COL) per base with vfptr, adjustor thunks | one vtable group, secondary vtables with offset-to-top, `_ZThn` thunks |
| virtual inheritance | vbptr in the object, vbtable in `.rdata` | vbase offsets in the vtable: **object layout differs** (68 sites in the decomp; the `static_assert`s catch them) |

Who carries which vptr:

* **Heap and stack objects** are constructed by native code: Itanium vptrs.
* **Static objects** in the image: MSVC 2008 initializes every polymorphic object dynamically, so on disk they hold no
  vptr; the vptr is written by the `??__E` dynamic initializer. 6,266 `??__E` and 4,692 `??__F` are defined in the
  decomp *(units.json)*. If S4's CRT start runs the image's `__xc_a..__xc_z` table (rewritten to native pointers, it
  shows up as a `run` of `init` targets) in the original order, every static is re-constructed by our own native
  initializer and gets an Itanium vptr in place. This is the fix for static instances; nothing else should construct
  them. Objects that do carry an MSVC vptr on disk (TypeDescriptors always do, with `type_info`'s vftable) are counted
  by the scanner (`vptr_refs`).
* **Hand-written vptr stores and comparisons**: *(units.json)* 1,438 references to vftable addresses through names
  other than `??_7` (`?DAT_0101...`, `kVtbl_*`, `gVtable_*`) from 516 units, e.g.
  `*reinterpret_cast<void**>(this) = &kVtbl_X;` or `if (*(void**)p == &kVtbl_X)`.
* **Hand-written virtual calls by MSVC slot number**: at least 409 sites in 146 files
  (`((void(__thiscall*)(void*))(*(void***)p)[k])(p)`, grep lower bound).
* **MSVC RTTI**: `__RTDynamicCast` is called explicitly in 20 files with TypeDescriptor arguments; the 372 units whose
  `dynamic_cast<>` MSVC compiled to `___RTDynamicCast` use `__dynamic_cast` in the GCC build.

Consequences if nothing is done: a GCC virtual call on an object with an image vptr picks the wrong function for every
slot from the destructor on and the destructor gets no flags; a raw MSVC-index call on an Itanium object does the
same in reverse; `dynamic_cast`/`typeid` on an image vptr reads a COL as `type_info`; vptr comparisons never match.

**Proposal (decision D6 draft, needs S2 and S1):**

1. **The Itanium vtable is the only vptr an object ever holds.** Image vftables are data: read by S2's tools, by MSVC
   RTTI emulation, and by raw-index calls through a translation (point 4). Never installed in an object.
2. **S2 binds image vftable addresses to native vtables.** Every reference to an image vftable `V` (`??_7` names and
   hand-written names at the same address) resolves to the Itanium address point of the same class (secondary vtable
   offset for multiple inheritance). `V` → class comes from the `??_7` names (2,333) and, on the player's exe, from the
   COL → TypeDescriptor name (`scan_codeptrs.py --vtables`). The same pairs go into the address map as `VTABLE`
   entries, so on-disk vptrs and tables of vftable addresses are rewritten by S3 (`vptr` class).
3. **Missing vtables are synthesized from the image.** GCC emits `_ZTV` only in the unit of the key function; units
   reach each other by address, so many will be undefined at link. S2 then emits the vtable in Itanium layout from the
   image vftable: each slot = native function of the original slot target, mapped MSVC slot k → Itanium slot through
   the class layout GCC reports (`-fdump-lang-class`) matched by original addresses; the MSVC `??_G` slot becomes
   D1 = bridge `??_G(this, 0)` and D0 = bridge `??_G(this, 1)`, which is exactly the scalar deleting destructor's
   contract (bit 0: delete after destroying); classes whose `??_G` the decomp does not define (most of them: 183 are
   defined for 2,333 named vftables) need one generated `??_G` each. Offset-to-top and `type_info` come from GCC's class data.
4. **Raw MSVC-index calls** become `FNVNX_MSVC_VCALL(p, k)` through an S1 prepare rule; the helper looks the object's
   Itanium address point up in an S2-generated sorted table `{address point, MSVC-order slot array}` (one entry per
   class with an image vftable, binary search with a per-site cache) and calls slot k of the MSVC view. Hot sites get
   real virtual calls by patch later.
5. **MSVC RTTI**: S4's `__RTDynamicCast(obj, vfdelta, srcTD, dstTD, isRef)` maps TypeDescriptors (image addresses,
   `.?AVX@@`) to Itanium `type_info` (`_ZTI1X`) through an S2 table and calls `__dynamic_cast`.

## Trampoline island (proposal, not implemented)

The original `.text` range is free. Mapping it executable with a 4-byte `B native` at every known original address
(minimum gap between starts is 5 bytes, `B` reaches ±128 MiB) makes every code pointer in the data valid **without
rewriting it**: no false positives, no false negatives for known addresses, and unknown addresses can hold a trap
that reports the original address. If S2 also resolves address-taken functions (non-call relocations) to the island
address, function-pointer identity between native code and the image holds as well. The cost is an executable
fixed mapping (Horizon option (b) above) and one branch per indirect call. With the island, rewriting becomes an
optimisation and `min_conf` can be raised to high.

## Tests (`tests/s3/`)

`python3 tests/s3/test_s3.py` (from `ports/fnv-nx`, output in `build/s3`) builds the loader with ASan/UBSan, generates
a synthetic PE32 (`gen_synth_pe.py`: no game content, sections at the original addresses, every construct of the class
table plus imports, delay imports, TLS, load config, debug data, resources; it fails if any `.text`-looking word in
the image is not accounted for) and checks:

* the Python scanner gives exactly the list the generator built, with hints, without hints (the hint-only vftable then
  runs on in its neighbour), and with `.reloc` (exact mode);
* the C runtime scanner writes a byte-identical fixup list and the same statistics, at the original base and moved
  by `0x01c00000` with relocations applied, and the same again with its index switched off (`--no-index`, the plain
  searches; see "Scan cost");
* after the whole pipeline (`loader/host/loadcheck`): every fixed word holds its native value (trap values with
  `--trap`), rejected words are untouched, IAT and delay IAT are bound, data-to-data pointers still reach their
  strings, `.bss` and the `.data` tail are zero, `.tls` holds its template, `.text` and `.reloc` are not mapped,
  protections are set;
* a precomputed list gives the same result; a list for another image is refused; a stale entry is not written;
* a Steam-like wrapped exe (scrambled `.text`, `.bind`) is accepted on data probes with the same result; without
  data probes, with scrambled data, or with a changed code byte it is refused;
* S2's `addrmap.tsv` parses to the same functions; `fnvnx_codemap_from_s2` merges and checks order; the kind-`G`
  adapter and the trap stubs work with real calls (`test_units.c`); the C and Python vftable slot readers agree;
* mutation fuzzing of headers and data (1,000 iterations per seed by default, `FNVNX_FUZZ_ITERS`) never crashes;
* the portable sources compile for `aarch64-linux-gnu -mabi=ilp32` with `-Werror`.

To run on a real exe (local only):
`loader/tools/mkprobes.py ext/fnv_decomp/build/image_check.json --txt build/probes.txt`, then
`loader/tools/scan_codeptrs.py FalloutNV.exe --units ext/fnv_decomp/build/units.json --image-check
ext/fnv_decomp/build/image_check.json --out build/fixups.bin --report build/fixups.json --vtables build/vftables.tsv
--emit-addrmap-bin build/addrmap.bin --emit-hints build/hints.txt`, and
`build/loader/loadcheck --exe FalloutNV.exe --probes build/probes.txt --addrmap build/addrmap.bin --hints
build/hints.txt` (natives are 0 without S2, so every fixup counts as unresolved: the counts are the point).

## Synthetic image without the exe (stream S14)

Until a player's exe is at hand, `loader/synth/synth_image.py` rebuilds as much of the data window as the decomp
itself defines, from S11's MSVC 2008 objects of the pristine decomp (byte-identical code, `tools/equiv/README.md`):
each object carries the data its unit defines (literals, float constants, vftables, RTTI, EH tables, initialised
statics), and `units.json` gives the original address of every name a unit references. The output is a PE32 shell
the loader maps like an exe (`.rdata`, `.data`+`.bss`, `.tls` at the inferred boundaries, a synthetic import
directory from `platform/win32/imports.tsv`), a per-byte state map for the boot comparison (`tests/s14/
boot_compare.py`), and a report. Everything it writes is decomp-derived: local only, never committed.

    python3 -I loader/synth/synth_image.py --units <fnv_decomp>/build/units.json --objdir $S11/obj/pristine \
        --cache OUT/phase1.pickle --out OUT/synth.exe --state-out OUT/state.bin --report OUT/full.json \
        --summary OUT/summary.txt --missing OUT/missing.tsv --conflicts OUT/conflicts.tsv \
        --addrmap <S2 run>/out/addrmap.tsv --checks OUT/checks.json --violations OUT/violations.tsv

The object scan takes about 40 s and is cached (`PHASE1_VERSION` invalidates old caches); the rest about 15 s.

**How a byte is chosen.** An item is the bytes one object says are at an original address: a COMDAT section, or a
symbol of a non-COMDAT section up to the next symbol. It owns (hard extent) the whole COMDAT, the size its decorated
type fixes, a `$SG` literal up to and including its terminator (NUL, or NUL pair for UTF-16), otherwise up to its
last non-zero word (*inexact*); the rest is soft zero padding. Relocations resolve through the same names; an
unresolved one leaves its four bytes unknown. Identical items from several objects are one item with that many
copies. Overlapping items: the better rank wins (0 a name that spells the content and agrees: `??_C@`, `__real@`,
`__xmm@`; 1 the unit's own decorated name or a `$SG` whose per-unit numbering was matched; 2 another unit's
decorated name; 3 a plain per-unit name: C statics, `_s_sz00`, `__TI` tables), then more copies; equal rank and
copies with different bytes is *contested*: unknown, and 0 in the shell (J-0062: before, the first claimant's byte
was written). `$SG` numbering: S11's objects count +43 in C++ units and +1 in C units against units.json; the shift
is matched per unit (303 units, every name hit).

**Coverage** (decomp `87eb937`, S11 `obj/pristine` 2,987 objects, S2c run f12, J-0062). *known* = bytes with a
value, split into from exactly one object / identical copies of several objects / several distinct items agreeing /
several disagreeing and decided by rank or majority; *rank 0/1/2/3* of the winning item; *inexact* = known bytes of
an inexact extent; *guessed* = bytes no object states (soft padding; items made from a name alone: none);
*unknown* = unresolved relocation or contested.

| Region / kind | bytes | known | one | copies | agree | disagree | rank 0/1/2/3 | inexact | unknown | guessed | rest |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| `.rdata` without IAT `0x00fdf000..0x01183000` | 1,719,140 | 236,664 | 216,599 | 12,132 | 7,905 | 28 | 137,438 / 76,062 / 148 / 23,016 | 25,756 | 56,557 | 3,674 | 1,422,245 none |
| IAT (204 named slots) | 1,180 | - | - | - | - | - | - | - | 4 | 0 | 812 loader-bound, 364 none |
| `.data` initialised `..0x011c1341` | 254,785 | 9,547 | 4,222 | 3,054 | 2,158 | 113 | 37 / 9,410 / 0 / 100 | 168 | 27 | 43 | 245,168 none |
| `.data`/`.bss` gap `..0x011c31fc` | 7,867 | 3 | 3 | 0 | 0 | 0 | 0 / 3 / 0 / 0 | 0 | 0 | 0 | 7,864 none |
| `.bss` `..0x01272000` | 716,292 | 8 | 3 | 1 | 4 | 0 | 0 / 4 / 4 / 0 | 4 | 0 | 32 | 716,252 zero |
| `.tls` `0x01272000..0x01273000` | 4,096 | 702 | 4 | 0 | 697 | 1 | 0 / 702 / 0 / 0 | 4 | 6 | 0 | 3,388 none |
| vftables (item extents) | 64,280 | 7,822 | 6,682 | 272 | 851 | 17 | 138 / 7,532 / 148 / 4 | 8 | 56,458 | 0 | |
| RTTI | 8,676 | 8,649 | 3,356 | 3,022 | 2,158 | 113 | 0 / 8,649 / 0 / 0 | 0 | 27 | 0 | |
| strings `??_C@` pool | 131,922 | 131,914 | 122,052 | 6,506 | 3,352 | 4 | 131,914 / 0 / 0 / 0 | 0 | 8 | 0 | |
| strings `$SG` | 68,912 | 68,912 | 60,487 | 2,876 | 5,549 | 0 | 2,892 / 66,020 / 0 / 0 | 15 | 0 | 0 | |
| `__real@` constants | 4,196 | 4,193 | 2,024 | 2,131 | 23 | 15 | 4,193 / 0 / 0 / 0 | 0 | 3 | 0 | |
| EH tables | 236 | 164 | 132 | 32 | 0 | 0 | 0 / 0 / 0 / 164 | 0 | 72 | 0 | |
| other variables | 29,246 | 29,198 | 26,240 | 364 | 2,594 | 0 | 1,982 / 4,264 / 0 / 22,952 | 25,928 | 48 | 0 | |

Data addresses units.json references in the window (48,664): 9,266 defined (every byte known: 7,875 from one
object, 972 identical copies, 409 several agreeing, 10 several disagreeing; worst rank 0: 6,189, 1: 2,540, 2: 3,
3: 534; 558 with an inexact extent), 2,089 partly known, 17,194 written by an initializer (`.bss` objects built by
`??__E`), 5,940 other `.bss` (zero), 204 named IAT slots (+5 unnamed, 5 IAT addresses missing), 13,956 missing
(no object defines them: the exe's own data, 12,803 in `.rdata`, 1,148 in `.data`), 8 `.bss?`, 2 outside. By kind:
vftables 2,333 (265 complete, 2,061 partial: slots whose function has no address, 7 missing); RTTI 295 (all
TypeDescriptors defined); `??_C@` 5,499 (5,493); `$SG` 1,899 (1,896); `__real@` 693 (692); EH 12 (12); vbtables 5
(5); other variables 37,742 (608 defined, 17,194 init, 5,938 `.bss`, 13,948 missing). `--missing` lists the
missing ones by the first function that uses them in static startup order.

**Self-consistency checks** (`loader/synth/synth_checks.py`; `KINDS` names each kind's owner). There is no exe to
compare with, so the image is checked against itself, units.json and S2's address map:

| Check | Passed | Violations (owner) |
|---|---|---|
| pointers: every resolved pointer word the image holds targets a defined image byte or a code address the map knows | 13 data known, 2 `.bss`, 508 functions (F/C/X/G), 727 `.text` without definition (E); 16,574 words unresolved, 328 held by another item | 675 function starts units.json defines but f12 lacks (S1: 576 in `tesform.cpp`, 65 in `tes.cpp`, 13 `imagespaceshaderhdr_h_unit`, 10 zlib `deflate.c`, ...); 295 TypeDescriptor vptrs to `type_info`'s vftable `<va>`, which no object defines (CRT data: exe/S4b); 10 `__sehtable$` entries to labels (x86 only) |
| vftable slots: the definition at the target fits the slot's convention and arity (llvm-undname + `tools/link/sigs.py`, every definition at the address) | 499 the slot's own symbol, 672 fit, 1 free function that ignores `this` (x86-compatible, a bridge natively), 727 E targets; 13,442 slots unresolved (no address for the slot's symbol) | 1 convention: `NiTriBasedGeomData` slot 0 `LinkObject(NiStream&)` (thiscall, 1 argument) -> `0x00a8a9a0` defined as cdecl `FUN_00a8a9a0()` (decomp/S2b) |
| RTTI: vftable -> COL -> TD/CHD -> BCA -> BCD per object, the same COL/CHD across objects, placed TDs | 2,838 chains consistent, 277 placed TDs; no COL has an address (2,793 vftables keep `[-1]` unknown) | 150 class hierarchies declared differently in different objects, 11 COLs with other offsets, 31 COLs only spelled class/struct apart; 104 placed TDs whose bytes are another unit's spelling; 45 vftables whose `[-1]` word holds the preceding literal/float (Gamebryo/BSShader classes: no COL in the original, built without C++ RTTI, or the `??_7` address is off; decomp, S3 hints) |
| overlaps: no two items claim one byte with different contents | 631 agreeing overlaps (11,459 bytes) | 81 TD `.?AV`/`.?AU` (64 by majority, 17 contested), 13 TDs of different classes at one address, 13 vftables at one address with other slots (12 contested), 2 vftables running into the next item, 4 `__real@` with another value, 3 + 1 literals with other content or running on, 6 TLS stand-in aggregates vs variables inside them (5 contested), 10 `.bss` variables over another item's non-zero bytes (decomp) |
| strings: `$SG` and `??_C@` hold their terminator inside their own bytes, the image holds it, no other item claims them | 1,943 + 209 shared `$SG`, 5,447 `??_C@` | 4 + 4 `??_C@` literals contested with a longer spelling at the same address (decomp); `$SG`: 0 (561 before J-0062, see below) |

Fixed by these checks (J-0062): a `$SG` literal owned its bytes only up to the last word holding a character, so the
terminator of every string whose length is a multiple of 4 was soft padding (561 literals); and contested bytes kept
the first claimant's value in the shell (65 bytes: vftable slots, TypeDescriptor names, literals and constants
claimed by two objects; now 0, as the state map always said).

**Boot globals** (J-0057; `report['boot_globals']`): `g_memoryManager` `0x011f6238`, the allocator holder
`0x011f6080`, the init-options pointer `0x011f6118` and the run-once flag `0x011f6229` of `FUN_00aa3050` lie in
`.bss`: zero in the image, as on x86 at process start. `0x011f6229` is written only by `FUN_00aa3050` (sets 1) and
`FUN_00aa3070` (clears it at shutdown), both reached only from WinMain, so it is 0 when WinMain+0x1d0 calls
`FUN_00aa20b0` -> `FUN_00aa3050` on x86 too, and InitHolder runs. On x86 the holder is not zero by then: the
MemoryManager constructor `0x00aa3880` (run by an initializer the decomp lacks, `toolchain/game/boot.cpp`
`construct_memory_manager()`) calls `InitializeGlobal` `0x00866690`, which sets `0x011f6118` to
`new FUN_00aa21d0(new BSNiAllocator)` and calls InitHolder once directly (the flag stays 0); `FUN_00aa20b0(0)` then
takes the existing options (`<va>` = 0) and InitHolder runs a second time. The image gives BSNiAllocator's
vftable `0x010a2528` only its COL word and slot 0 (unknown: the decomp's stand-in class declares one virtual,
`m_virtual0`, without an address); the next vftable's COL word at `0x010a2550` bounds the original to 10 slots,
not 14 as the decomp's comment says, and NiStandardAllocator's at `0x010a24fc` also has 10.

**Vftable dump for one class** (`loader/synth/vftable_dump.py CLASS... --units ... --objdir ... [--synth DIR]`,
default output `render/native/vftables.tsv`, git-ignored because it is decomp-derived). One row per slot: class,
vftable, vftable address, slot, target address, slot symbol, its demangled form, source, `known`/`needs-exe`, note.
Sources, best first:
* `msvc-object`: an object emits the vftable, so the slot symbols and their order are MSVC's.
* `decomp declaration`: no object emits it, so the slots are taken from the declaration with the most virtuals,
  primary base first.
* `unknown`: words the synthetic image leaves free after the known slots, up to the next claimed byte (an upper
  bound).

For the render machine's four classes (decomp `87eb937`), every target but one needs the exe:

| Class | What the decomp gives |
|---|---|
| BSShaderNoLighting | 72 slots from u134's vftable at `0x010bb230`; one target known (`NiRefObject::DeleteThis`); room for 7 more |
| NiDX9Renderer | 143 declared slots (`src/NiRenderedTexture.h`, base `NiRenderer` first); no vftable address |
| NiDX9RenderState, TileShader | no vftable in any object, no declaration with virtual functions |

**Tests** (`tests/s14/`, hand-made COFF objects, no decomp content): `test_synth.py` (placement, relocations,
ranks, `$SG` shift, sections, PE shell and imports; the C loader with `FNVNX_LOADCHECK`), `test_checks.py` (every
check with planted violations, the COL-less hints and `check_hints.py`'s verdicts; with `FNVNX_S14_RUN=<output dir>`
also a real run: no violation owned by S14),
`boot_compare.py` (S5's emulated boot on the zero, prefilled and PE images).

## Open risks

* No real exe has been scanned yet: all false-positive numbers are estimates; the first run's `--report` is the
  measurement to take (per class, `vptr_refs`, `unknown`).
* `.tls`: the decomp reaches TLS variables by their template address; mapped as data that is one shared copy. S4 must give each thread its own block and redirect `__tls_index`/`fs:[0x2c]` style access.
* The Horizon side of `map_fixed` (window free of the NSO under ASLR) is S5's call.
* Points 2-4 of the vtable proposal depend on S2 being able to read GCC's class layouts; virtual inheritance classes
  (68 sites) have different object layouts and need patches.
* Image data written by the original code at runtime before our code runs (none: we run no original code) and
  pointers into `.text` used as data (jump tables live in `.text` and are not needed) are assumed absent. (ASM-023)
