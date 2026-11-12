# w3: dslemit root, numbers at shapes commit 77524556 (build of 76763619; branch w3t on w2g)

Root lift/test/dslemit (guest, lifted_by lift/gen/gen_dslemit.py, pack = shapes external). Two cold builds equal, warm 0 stages run (257 hit).
Totals over the whole universe: shapes 26500 (union of 7 images); emitted 18939 shapes (19082 functions); bad 7561; units compiled 128/128, failed 0.

## bad by family (family = capstone group of the decoded instruction: fpu = x87; mmx/sse*/avx/3dnow = simd; int/iret/privilege/vm and arpl, bound, les/lds/lss/lfs/lgs, in/out, hlt, cli/sti, cpuid, rdtsc, wait, int3, into, sysenter = sys; the rest = int)
| class | int | x87 | simd | sys | total |
|---|---|---|---|---|---|
| uncovered (no DSL form) | 262 | 2145 | 4344 | 385 | 7136 |
| emit error | 425 | 0 | 0 | 0 | 425 |
| total | 687 | 2145 | 4344 | 385 | 7561 |

## emit errors (425), by error class
- 232 Unsupported('segment')   (the emitter's own error text; the cause is not analysed here)
- 92 Unsupported('form variable bx'), 63 bp, 25 di, 10 si  = 190 (emitter error text 'form variable <reg>')
- 3 AssertionError (shapes 6659, 665c, 665d: bytes 66 59, 66 5c, 66 5d: the decoder reads them as 16-bit pop)
Sum 232+190+3 = 425.

## undef.tsv (17 rows)
These are NOT link symbols. Each row is (mnemonic, flag) where the proven DSL form declares the flag undefined (x86 SDM) while the hand handler assigns a value:
and af; imul af pf sf zf; mul af pf sf zf; or af; sar af; shl af; shld af; shr af; shrd af; test af; xor af.
Owner: emitter (L3a/forms). Not rt, platform or libc. The DSL leaves the flag untouched, the hand handler writes a value; they differ only for code that reads an architecturally undefined flag.

## verdict findings for L1 (EMLINK)
The unit view hardlinks one #error blob per other unit into every unit: links = units^2. 4096 units gave EMLINK (ext4 limit 65000); the generator keeps its own 4096-unit split (no workaround); fix is L1 w14. L3a's 4096-unit pack hits it.

## puregate violations (47; puregate check lift/gen lift/fnvlift lift/proof at cbe0b8b3 base; format file:line:RULE symbol)

### gen (2)
- lift/gen/gen_dslemit.py:60:import-unlisted fnvlift
- lift/gen/gen_dslemit.py:61:import-unlisted fnvlift

### fnvlift (7)
- lift/fnvlift/__init__.py:21:dynamic globals
- lift/fnvlift/__init__.py:22:dynamic globals
- lift/fnvlift/dsl.py:86:import-unlisted z3
- lift/fnvlift/dsl.py:89:import-unlisted z3
- lift/fnvlift/dsl.py:231:import-unlisted z3
- lift/fnvlift/dsl.py:238:import-unlisted z3
- lift/fnvlift/f_dsl.py:24:import-unlisted proof

### proof (38)
- lift/proof/formgate.py:39:import-unlisted proof
- lift/proof/formgate.py:40:import-unlisted gatefile
- lift/proof/formgate.py:41:import-unlisted recipe
- lift/proof/forms.py:10:import-unlisted fnvlift.dsl
- lift/proof/forms.py:88:import-unlisted proof
- lift/proof/forms_alu.py:2:import-unlisted fnvlift.dsl
- lift/proof/forms_alu.py:3:import-unlisted proof.forms
- lift/proof/forms_flow.py:2:import-unlisted fnvlift.dsl
- lift/proof/forms_flow.py:3:import-unlisted proof.forms
- lift/proof/forms_move.py:2:import-unlisted fnvlift.dsl
- lift/proof/forms_move.py:3:import-unlisted proof.forms
- lift/proof/gate_fpexact.py:92:import-unlisted proof
- lift/proof/gate_fpexact.py:93:import-unlisted gate_build
- lift/proof/gate_fpexact.py:94:import-unlisted gatefile
- lift/proof/gate_fpexact.py:95:import-unlisted recipe
- lift/proof/symform.py:22:import-unlisted fnvlift
- lift/proof/symform.py:23:import-unlisted fnvlift.dsl
- lift/proof/symform.py:25:import-unlisted proof
- lift/proof/symform.py:26:import-unlisted proof.detrand
- lift/proof/tests/fakeext.py:10:import-unlisted cas
- lift/proof/tests/fakeext.py:11:import-unlisted verdict
- lift/proof/tests/test_formgate.py:3:import-unlisted atexit
- lift/proof/tests/test_formgate.py:11:import-unlisted unittest
- lift/proof/tests/test_formgate.py:19:import-unlisted proof
- lift/proof/tests/test_formgate.py:20:import-unlisted recipe
- lift/proof/tests/test_formgate.py:22:entropy tempfile.mkdtemp
- lift/proof/tests/test_formgate.py:314:unsorted-set set
- lift/proof/tests/test_formgate.py:334:clock shutil.copytree
- lift/proof/tests/test_formgate.py:335:unsorted-listing os.listdir
- lift/proof/tests/test_formgate.py:341:clock shutil.copytree
- lift/proof/tests/test_fpexact.py:4:import-unlisted atexit
- lift/proof/tests/test_fpexact.py:13:import-unlisted unittest
- lift/proof/tests/test_fpexact.py:14:import-unlisted unittest
- lift/proof/tests/test_fpexact.py:22:import-unlisted proof
- lift/proof/tests/test_fpexact.py:24:entropy tempfile.mkdtemp
- lift/proof/tests/test_fpexact.py:421:clock shutil.copytree
- lift/proof/tests/test_fpexact.py:422:clock shutil.copytree
- lift/proof/tests/test_fpexact.py:423:clock shutil.copytree

Blocker: rt headers: L3a lift/rt root (dslemit's inputs lift/rt/fnvl.h, fnvl_x87.h, x87.h, fmath.h come from it once on the trunk). fnvlift is w2g's copy: set_emit is a change request to L3a; gen sets FNVL_EMIT from its argument before the import (a generated stage has no env key).
