# L3b by-construction audit (w3, one pass, no farm, nothing fixed)

Tree: `claude/fnv-l3b-lift-w3b` at 7c47edbc0 (= merge of `claude/fnv-l3b-lift`); w4 head 6fb0f0214 and w9 head 7624667e9
read through `git show` / `git diff HEAD origin/...`, not merged. Method: grep of every constant table, pin, path and hex
fact in `lift/proof/**`, `farm/farm_shapeprove.sh`, `farm/sail_build.sh`, plus the files that only the w4/w9 heads add.
Not read line by line: sdmfeat.py bodies, gate_fpexact.py bodies, the sail_patches texts, the w9 inbox notes (grep only).
Classes: R1 hand-written set, R2 second copy of a fact, R3 second path, R4 universe not by one rule, R5 restated declared
target, R6 number not from a run at a commit. PATH = blocks the first-frame path (certs for the product pack, gate_oracle,
EH/SEH), AFTER = does not.

## Findings

| # | file:line | class | what it is | pinned complete source | replacement (one source) | when |
|---|---|---|---|---|---|---|
| 1 | symform.py:125-129 (`_probes`, `ADDRS`), used by `_find_slice` | R1 | The field slice and the (ext, rel) hypothesis of a Form are found by probing with 13 values per width (7 corner values + 6 seeded random) at 3 hand-picked addresses. CORRECTION (rule 8, lead): not sampling as proof: the probes only generate candidates; the cause is an unverified link (no gate) between the capstone-derived slice and the builders. Closed by gate forms running symcheck.link over the pack's instrs.tsv (6363906d4). `shape_sha` (the cert key) depends on the candidates. | the DSL builder source itself (the builder is a Python function of (bytes, addr)) | derive the dependence by symbolic execution of the builder over BitVec bytes (the form is built from symbolic fields; slice = the free variables of the result), no probes | PATH |
| 2 | forms_sys.py:16 `PRIV_MN` | R1+R4 | hand list of privileged mnemonics; the class is an explicit list, not a rule over the SDM | pinned SDM a4a62e6a (Vol. 2 "Protected Mode Exceptions: #GP(0) if CPL > 0"); w4 sysspec.py:33 already holds `PRIV` with the SDM sentence | delete the list, read sysspec's rows (generated from the SDM opcode/exception pages via sdmfeat); w4's `sysspec.PRIV` is then also generated, not typed | PATH (sys shapes of the pack) |
| 3 | forms_sys.py:15 `INT_KIND`; lift_dsl.py:27 `FAULT`; prove.py:31 `MODEL_FAULT` | R2 | fault/vector codes copied in three files; the comment names `f_sys.INT_VECTORS` and `fnvl.h` as the real ones | lift/rt/fnvl.h (fault kinds), lift/fnvlift/f_sys.py `INT_VECTORS` | parse the enum from fnvl.h once (a generated `faults.py` stage) and import it in all three; MODEL_FAULT keeps only the model message regexes | PATH (EH/SEH) |
| 4 | census.py:93 `DATA_CLASSES` | R2 | restates L3a codemap CLS | lift/tools/codemap.py CLS / CODEMAP.md (L3a w7, not delivered) | read the classes from the codemap output; blocker already recorded for test_census | PATH |
| 5 | declared.json (34 lines; cpuid row `outputs`, `ufs`, `source`; rdtsc row; w4 head adds privileged/INT rows) | R1+R5 | hand-written contract rows that restate CPUREF's interface (which outputs cpuid defines, which uf names) | lift/rt/cpumodel.c (CPUREF), SDM CPUID/RDTSC pages | generate the rows from cpumodel.c's exported fnvl_cpuid / fnvl_rdtsc signatures plus the SDM output registers (a stage in the recipe), formgate reads the generated file | PATH |
| 6 | model_sail.py:55 `SEG`, :60 `FAULT_OK` | R5+R1 | initial segment state of the isla run (flat 32-bit selectors, attrs 0x0c9a, limits 0xffffffff) = the declared x86 machine's flat model, typed here; `FAULT_OK` hand set of two ACL2 fault names | declared machine / ASM-009 ACL2 `:SEGMENT-LIMIT-FAIL` names in the IR | a `machine` record in declared.json (one file), SEG built from it; FAULT_OK derived from the IR's fault enum | AFTER (it is part of E, so K already tracks it) |
| 7 | model_sail.py:53 `ERRFNS` | R1 | hand list of IR error function names | the IR (x86_proof.ir): every function whose name is `x86_fault<..>` / `x86_model_error<..>` | derive from the IR text (irdeps already indexes it) | AFTER |
| 8 | defects.py:22-31 `DEFECTS` (5 entries) | R1+R2 | hand-written model-defect list with regions; entries rot-count-modulo, ror-cf, rot-of-count0, sar-cf-big-count are exactly what sail_patches/{rotate_count,ror_cf}.sail and bump/{ror_cf,rol_count0,sar_bigcount}.sail now fix in the IR, so the same fact lives in two files; rep-prefix-ignored is a model gap | SDM + the pinned bump IR (ACL2 3ccb342c transcriptions) | delete the entries the bump IR fixes (the IR is the one source); keep none by hand: a remaining disagreement is `model-gap`, not a typed region | AFTER |
| 9 | forms.py:14-16 `SEGPFX`/`PFX`/`SEGS`; shape.py:28 `PFX`; defects.py:11 tuple | R2 | the legacy prefix set written in three files | SDM Vol. 2 2.1.1 (cited in shape.py:28) | shape.py exports one table; forms.py and defects.py import it | AFTER |
| 10 | backends.py:8, prove.py:25, lift_dsl.py:29 `SIXFLAGS`, model_sail.py:48-49 `GPR`/`FLAGBIT` | R2 | register and flag name tables written four times | the DSL (lift_dsl) | one `arch.py`-style table in lift_dsl, others import | AFTER |
| 11 | formgate.py:53-54 `REFERENCES`/`FAMILIES`; codeuniverse.py:30 `FAMILIES`; shape.py:10 | R2 | family list in three files (and in a different order) | shape.py `family()` | shape.py exports `FAMILIES`; the other two import | AFTER |
| 12 | formgate.py:51 `EXT_KEYS`; formgate.py:162 `OBLIGATIONS` | R2 | copies of `recipe.EXT_KEYS` and `prove.OBLIGATIONS`, kept equal by a test (the test is a gate on a copy) | recipe.py, prove.py | formgate imports `recipe.EXT_KEYS` (it already imports recipe); `OBLIGATIONS` moves to a z3-free module both import; delete the two equality tests | AFTER |
| 13 | **IR sha: sail_pins.txt:71, formgate_inputs.json:23 + the `x86ir` external (:12-18), tests/test_formgate.py:43, farm/farm_shapeprove.sh (queue header)** | R2 | the pinned IR sha is written in 3 files and the IR itself is pinned by a commit in a 4th. **Defect now:** the merge of `claude/fnv-l3b-lift` moved the pin to 7c307464 (bump IR) but the `x86ir` external (mine, ca8c71e21) still names fnv-nx-build `tools/x86-proof-ir` baf8bc80, whose file is 70c8dc39. `model_sail.ir_file()` will therefore load the old IR while formgate_inputs.json says 7c307464. | sail_pins.txt row `x86_proof.ir` | keep the sha only in sail_pins.txt; the external entry carries the commit, and its file sha is checked against sail_pins.txt (one check); test_formgate reads sail_pins.txt instead of its literal. A new IR commit for 7c307464 must be published (farm) and the external re-pinned | PATH |
| 14 | acl2 commit 3ccb342c88bd... in 8 files (sail_patches/bump/*.sail, bump/*_dispatch.py, tests/test_bsfbsr_closed_form.py) | R2 | one pin typed in every patch | the pins file | a `acl2` row in sail_pins.txt; patch headers say `ACL2 pin` | AFTER |
| 15 | SDM pin a4a62e6a (tests/sdm_cpuid_feature_rows.tsv:1; w9 sdm_pages.py docstring with tools/intel-sdm commit 0768b0ba), Wine pin f3e6148c (w9 wine_pin.json) | R2 | pins live in docstrings / row headers, outside the pin file | the pins file | `sdm` and `wine` rows in sail_pins.txt (or one `pins.json`); tools read them | AFTER |
| 16 | tests/sdm_shape_rows.tsv (13 lines), tests/sdm_int_rows.tsv (21), tests/smtlib_bv_rows.tsv (7), tests/model_patch_rows.tsv (5); w9 tests/x87_sdm_rows.txt | R1 | reference rows typed by hand from the SDM / SMT-LIB. Git history: one commit each (8e6b4f559, 97fbe618d, ca8c71e21), no generating script. They exist so that tests have a reference outside the subject (gate_oracle). | pinned SDM a4a62e6a pages (tables 2-x, instruction pages) | generate by a script from the SDM text (the sdm_pages.py / sdmfeat.py route, citation per row), or delete the test and let the prover certificate be the reference (the Sail model is already the independent reference for the shapes) | AFTER |
| 17 | sdmfeat.py:408 `OPMAP` (hand rows with matcher lambdas), :323-324 `LENBIT`/`MAPS` | R1 | hand list of opcode rows "the SDM still defines" although the opcode tables have no row; each carries a text anchor, but the set is typed | SDM Vol. 2 Appendix A opcode maps (the tables are the complete source) | parse Appendix A tables into rows; OPMAP's entries become derived (82 /n = group 1 alias etc. are table notes) | PATH (it decides the cleared / lifted universe of shapes) |
| 18 | gate_fpexact.py:102-107 `RECIP`, `FCVT` (capstone ARM64 instruction ids), :107 `REF_KEYS` | R1+R5 | hand lists of float-to-int and reciprocal instruction ids; REF_KEYS restates CPUREF's key names | Arm ARM instruction classes (capstone `ARM64_GRP_*` / pinned Arm XML), lift/rt/cpumodel.c | derive the classes from the Arm instruction XML / the capstone group; read REF_KEYS from cpumodel.c | AFTER (PERFDEBT MINIMP3 depends on it, after first frame) |
| 19 | enc_table.py:6-31 (`ALU`, `BASE`, `EXTRA_ASM` ~60 entries, `EXTRA_HEX`) | R1+R3 | hand list of "other entries of the exe" encodings, assembled with keystone; used only by prove.py's table mode (prove.py:51) | the pack's instruction universe (codeuniverse / packuniverse) | delete (see #20) | AFTER |
| 20 | prove.py:298-306 table mode + `--backends`, universe.py (whole file), enc_table.py, backends.py (85 lines, `SPECIAL` test values at :38) | R3+R1 | second prover entry point beside shapeprove.py; its universe is `/home/user/l3work/instest/result.json` (private, unpinned) filtered by `startswith('f')` and a hand mnemonic list. backends.py is a cross-check of the prover by other backends (a verifier of a verifier). | none (shapeprove over the pack's shape keys is the one entry) | delete the table mode, universe.py, enc_table.py, backends.py and keystone; shapeprove.py is the only entry; prove.py keeps `prove_one` | AFTER |
| 21 | model_sail.py:20 `WORK`, :44 `ISLA`, :65 `CACHE`; tests/test_certs_build.py:12, tests/test_formgate.py:22; farm_shapeprove.sh:28,30,55; sail_build.sh:5-17; prove.py:300-301 | R3+R5 | default paths under /home/user/l3work, /home/user/rems-project (unpinned isla build), a sail_cache outside GATE_CACHE. Second cache and an isla that is not a declared external. | fnv-nx-build `tools/sail-mirror` (pinned isla/sail build, named in the farm script header) | isla and the Sail toolchain come as declared externals through `recipe.fetch_externals`; caches under GATE_CACHE; delete the l3work defaults. sail_build.sh (build from REMS clones) and sail_setup.sh (mirror) are two routes to the same toolchain: keep the mirror, delete the build | AFTER (the farm works today) |
| 22 | README.md:60 ("393 encodings / 270 form keys, 251 s"), :88 ("488,112,128 cases"), :94 ("2,642,944 cases"); my l3b-w3-doubles-census.md ("universe 26500 keys, 0 certs") | R6 | numbers without a commit and command | gate / prover output | replace by a pointer to the produced file (forms.json / certs.tsv header at a commit); I correct my own note | AFTER |
| 23 | test_irdeps.py / test_formgate.py literals (`GOOD`, `NOBUILD`, `MODEL`) | R2 (only `MODEL`) | fixtures are fine; `MODEL` is a copy of the pin (see #13) | | covered by #13 | PATH (part of #13) |

## Not findings (checked)

- tests/oracle.json holds one entry per test file: the gate's universe is the glob `tests/test_*.py` (gate_oracle),
  the entries are references, so this is not an R4 list.
- certs.tsv, shapes.tsv, instrs.tsv, bases.tsv, queue.tsv are produced by scripts from pinned sources (certs_build.py,
  the lift pack, packqueue.py with a queue_id hash header). Not R1.
- sdm_cpuid_feature_rows.tsv is produced by sdmfeat.py from the pinned SDM plus cpumodel.c (header: `sdm_sha256`,
  `cpumodel_sha256`, counts). Not R1.
- sail_pins.txt is the one pin file for the toolchain (it is the source, not a copy), except for the rows in #13-#15.

## Counts per class (rows in the table; #13 and #23 count once under R2, #20 under R3)

| class | rows | of which PATH |
|---|---|---|
| R1 hand-written set | 1, 2, 5, 6, 7, 8, 16, 17, 18, 19, 20 = 11 | 1, 2, 5, 17 = 4 |
| R2 second copy | 3, 4, 9, 10, 11, 12, 13, 14, 15, 23 = 10 | 3, 4, 13, 23 = 4 |
| R3 second path | 19, 20, 21 = 3 | 0 |
| R4 universe not by one rule | 2 (also R1) = 1 | 1 |
| R5 restated declared target | 5, 6, 18, 21 = 4 | 5 = 1 |
| R6 number without a run at a commit | 22 = 1 | 0 |

23 rows, 9 PATH (1, 2, 3, 4, 5, 13, 17, 23 and the PRIV/INT part of 2), 14 AFTER.
Several rows carry two classes, so the per-class column sums to more than 23.

## Order of work for the PATH rows (my suggestion)

1. #13 now: the x86ir external is out of date relative to the pin (a defect today, not a style issue).
2. #1 (probes in symform): cert keys depend on it.
3. #5, #3, #2, #4, #17: they fix the universe and the declared facts that the certs for the product pack rest on.
