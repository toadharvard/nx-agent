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

## Status of the move (updated below as items land)
- Moved: 0 of 124. Blocked (PE or cl side): all 124 depend on a PE or COFF/CodeView builder except the 10 GCC-side-only cases of gate_names and the
  malformed/absent/empty GCC-section cases of gate_r7 (about 14), which can move to real ELF objects.
