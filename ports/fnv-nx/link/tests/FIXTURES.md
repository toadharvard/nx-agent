# Fixtures of the link gate tests: census

Rule (owner order "no doubles in tests", L2 brief item 4): a gate test reads REAL objects built by the one build (`verify/recipe.py`, guest compiler
msvcmodel, all plugins). A negative case is a byte mutation of a real object (`mutate.py` finds the field by parsing with `link/elfobj.py`), never a
hand-assembled object. This file counts what is still hand-built. Unit = one `check(...)` call site (a loop of runs counts once; `fails.append`
branches of the cut loops and try/except negatives are counted as one negative case each). Measured at commit 09e8362 (merge of
claude/fnv-l2-source and claude/fnv-l1-contract-w4), before any change.

Builders: `synth.elf` (ELF .o), `synth.coff` (COFF .obj), `synth.CV` / `synth.debug_object` (CodeView `.debug$T`), `abi_fixture.PE` (PE32 exe),
`abi_fixture.scene` (PE + hand-written obj/bind fact stores). Every ELF/COFF case of gate_names and gate_r7 pairs a GCC (ELF) object with a cl (COFF)
object; the cl side is `blocked: cl toolchain as recipe external (L1)`.

| file | cases | positive | negative | builders |
|---|---|---|---|---|
| test_exevft.py | 24 | 16 | 8 | abi_fixture.PE only |
| test_gate_names.py | 39 | 17 | 22 | synth.elf (GCC side, 12 call sites in builders/cases) + synth.coff (cl side) |
| test_gate_r7.py | 35 | 16 | 19 | synth.elf (`fixture()`, 6 direct) + synth.coff/CV/debug_object (cl side) |
| test_thisadj.py | 7 | 3 | 4 | abi_fixture.scene (PE + hand fact stores) |
| test_vslots.py | 8 | 4 | 4 | abi_fixture.scene |
| test_vtables.py | 11 | 2 | 9 | abi_fixture.scene |
| total | 124 | 58 | 66 | |

## Per file

### test_exevft.py (24 = 16 positive + 8 negative), all `abi_fixture.PE`: blocked (cl toolchain / PE32 image of the exe class)
Positive: three vftables, name and offset, bases, slots, thunk encodings (loop), stacked this, chain, chain of a non-thunk, entry offsets (2), cli exit,
cli format, cli rows, cli thunk chain, cli plain slot, deterministic. Negative: two adjusts not exact, ordinary function, leaf, outside the image,
thunk loop is an error, bad hierarchy names the COL, cli error exit, bad RTTI name.

### test_gate_names.py (39 = 17 positive + 22 negative); GCC side `synth.elf` (`gcc_obj`, direct calls), cl side `synth.coff` (`cl_obj`)
Positive: universe, elf64, all certified, empty universe, outside the universe (2), units pairing, two cl objects (second defines), an unreadable one
does not hide a proof, pairs, two .group sections, rows count/sorted/certified, green, green empty, deterministic.
Negative: red, wrong name, undefined in cl, no cl object, unreadable cl (2), errors of 6 malformed GCC objects (loop), GCC object cut at every prefix,
cl object cut at every prefix, no units (stems differ), none defines f, unreadable and no proof, doubly present sympair, units file with a bad unit
(raises), rows red, rows error (2), red exit, red file, ids file, missing input, missing units file.
Of these, GCC-side-only inputs (no cl object needed to decide): the 6 malformed GCC objects, the GCC prefix cuts, empty universe, two .group, doubly
present sympair (10 cases); all others need the cl partner.

### test_gate_r7.py (35 = 16 positive + 19 negative); GCC side `synth.elf` of a hand-written `.fnvnx.layout` text, cl side `synth.CV`/`synth.coff`
Positive: universe, sorted, certified count, verdict counts, absent section, empty section, units pairing, set: identical / one lacks / one without
CodeView, rows count/sorted/shape/verdicts, green file, empty file. Negative: per-key verdict loop (21 of 31 keys differ/undecided/only-gcc), no cl
object, no CodeView, unreadable cl, bad signature, truncated record, table cut, unreadable members, 10 malformed GCC sections (loop), garbage GCC
object, set: no CodeView in any, set: differing definitions, set: unreadable, rows error, red file, red file lists, detail, ids file, missing input.
The `.fnvnx.layout` lines are hand-written text (keys such as `USizeBad@@` name no real type), not plugin output.

### test_thisadj.py / test_vslots.py / test_vtables.py (7 / 8 / 11): `abi_fixture.scene`
Each case is a PE32 exe (`abi_fixture.PE`) plus a hand-written obj store and bind store (`store.save`), not ELF objects. Positives: green runs
(thisadj 1+entry convention+..., vslots 1, vtables 1+). Negatives: one broken fact per case (wrong thunk offset, wrong callee, unbound point, extra
slot, broken destructor pair, missing row, malformed input, missing exe, mutated store entries).
Blocked as a unit: the exe side is the PE (cl toolchain); the obj side would have to be extracted from real objects by the same build.

## Status of the move (head of branch claude/fnv-l2-source-w2 after the move)
Real object: `fixtures/names_basic.cc` (names plugin rows: func, ctor/dtor, data, vtable) and `fixtures/r7_types.cc` (33 record types, layout plugin
rows), built by `realobj.py` through `verify/recipe.py` (toy guest root, msvcmodel compiler pinned in verify/guest_compiler.json, all 8 plugins,
`target: guest`, `cxx_origin: native`). Negatives are `mutate.py` edits located through `elfobj` (replace_section, rename_section, duplicate_section,
drop_symtab, truncate, rename_symbol, set_section_flags). `synth.elf` is removed; no direct compiler call in the six files.

| file | cases | GCC side real | blocked (reason) |
|---|---|---|---|
| test_gate_names.py | 35 check sites + loops (was 39 incl. the ELF64 case) | all | the cl side of every paired case stays `synth.coff` (cl toolchain as recipe external, L1); the ELF64 case ("elf64 gives the same verdicts") is dropped: the guest compiler yields ELF32 objects, an ELF64 real object needs an LP64 host root (not a guest product) |
| test_gate_r7.py | 36 check sites (loops count once) | all | cl side (`synth.CV`/`synth.coff`) hand-built for every case; `!_ZN3fooE` nokey row is a text edit of the real `UE@foo@@` row (no C++ fixture produces a nokey row) |
| test_exevft.py | 24 | 0 | blocked: cl toolchain as recipe external (L1) (abi_fixture.PE) |
| test_thisadj.py | 7 | 0 | blocked: cl toolchain as recipe external (L1) (abi_fixture.scene: PE + hand obj/bind stores) |
| test_vslots.py | 8 | 0 | same |
| test_vtables.py | 11 | 0 | same |
| total | 121 | 71 | 50 fully blocked (PE) + the cl side of 73 |

Real-compiler finding made while building fixtures: a by-value return of a polymorphic struct (`UVf f(UVf x)` with `virtual void f(); int m;`) is an
internal compiler error of the msvcmodel compiler under the plugins (`expand_expr_addr_expr_1, at expr.cc:8710`, stage cc, during RTL pass expand).
The fixtures use pointers instead; the ICE is reported to the L2 lead, not worked around in the compiler.

Direct compiler calls still present in link/tests (outside the six files): test_gate_abs.py, test_gate_addr.py, test_gate_disp.py, test_gate_order.py,
test_odr.py, test_abi.py (they call aarch64-linux-gnu-gcc/g++/as/ld themselves).

## Step 2 (L2 lead order): the remaining tests with compiler / assembler / linker calls
Mutation helpers added to mutate.py: function_words, patch_word, stlr_to_str, set_symbol_size. realobj.build(sources, plugins=subset|None, lifted, flags)
builds a toy guest root (native: all 8 plugins; lifted-origin: any subset, incl. none).

- test_odr.py: moved whole (real objects at -O0 and -Os through realobj.build; malformed = byte edits). No direct compiler call left.
- test_abi.py: has no compiler, assembler or linker call (it was listed by mistake; its inputs are MSVC name strings and in-memory obj stores). Unchanged.
- test_gate_order.py: moved. The OK object (fixtures/order_ok.cc, all plugins) certifies and covers the rules ordered, frame, frame-escape, fenced-load,
  fenced-store, readonly; the BAD objects (fixtures/order_bad.cc, no plugin; -march=armv8.1-a for the LSE atomic) give the plain-store, plain-load,
  frame-escape-unknown, weak-atomic, no-frame-record/section rows; frame-record cases and undecodable / size-0 / plain-store-with-same-registers cases are
  byte mutations of the OK object. Assembler-only cases that are NOT covered any more (the property is an instruction sequence no C++ fixture
  compiled by the pinned compiler and no single mutation of the real object gives; the old test hand-assembled them):
  ok_literal (literal-pool `ldr w0, lit`: rule `literal`), ok_ro's `linker` rule (`adrp :got:` load: PIC code, the guest is not PIC), ok_ordered's
  exclusive forms (`ldaxr/stlxr` pair accepted as ordered), ok_fenced_branch (fence on both paths of a branch), ok_local_base; bad_one_path and
  bad_one_path2 (a fence on one path only), bad_call (load, call, fence), bad_store_late (store then fence), bad_store_ro (store to a read-only table via adrp),
  bad_ro_written (read-only base register overwritten), bad_excl (plain `ldxr/stxr`), bad_load_only_ishst (`dmb ishst` after a load), bad_x29 (x29
  overwritten, then used as a base), bad_escape_plain_x29 (x29-based plain store to an escaped slot); record cases rec_overlap, rec_above, rec_pair (hand-set
  frame ranges against hand-written stores); alias cases alias_ok / alias_bad (`.set fb, fa` with two records: C++ ctor C1/C2 aliases exist in
  fixtures/order_doc.cc but their records are written by the plugin from one function; no mutation gives two different records on aliased symbols
  without editing the symbol table, which mutate.py does not do yet). 25 hand-assembled functions + 3 record cases + 2 alias cases = 30 cases listed.

### Step 2 continued (test_gate_addr, test_gate_abs, test_gate_disp)
- test_gate_addr.py: moved. Real objects of fixtures/addr_main.cc, addr_disp.cc (all 8 plugins, native root), addr_fn.cc (functions fa, fb), addr_nofn.cc (no function).
  Negative cases are `.fnvnx.addr` section replacements/renames of the real addr_fn object (mutate.replace_section / rename_section); the plugin is no longer
  built by the test. Remaining tool call: `aarch64-linux-gnu-objdump -d` (read-only disassembly of a real object, no compile/assemble/link).
  Finding fixed on the way: fnvnx_addr wrote no section for a unit without a function, so gate_addr certified such an object red; the plugin now always writes it.
- test_gate_abs.py: moved. Real clean object (fixtures/abs_clean.cc); absolute reference/definition = mutate.set_symbol_shndx (ext -> SHN_ABS), symbol-less
  relocation = mutate.zero_reloc_symbol; archive written in Python (ar format, no `ar`); linked image = the recipe's `link` stage (realobj.build(link_script=...)
  with a script that defines `scr = 0x1000;`, fixtures/abs_ref.cc refers to it). Nothing left blocked.
- test_gate_disp.py: no compiler/assembler/linker call (struct-built ELF32 image). NOT moved: the image needs the lifted runtime's perfect-hash dispatch
  table (fnvl_disp_table, fnvl_g, fnvl_phf_params), which no tracked source or recipe stage in this repository generates (the producer is private); blocked on the
  lifted runtime product root. 25 checks stay hand-built until then.
- test_abi.py: no compiler call (checked); unchanged.
