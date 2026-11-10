# proof/ : single-source instruction semantics (W8)

Status after the scope change: the only reference model for form proofs is the Sail x86 model; the VEX second model was
cancelled and `model_vex.py` was never finished, so it does not exist. Nothing here has been proved against any reference
yet. What exists and is measured:

* `fnvlift/dsl.py`: bit-vector DSL, backends `to_smt()` (z3) and `Form.to_c()` (C++17, unsigned fixed-width ops only; shifts are
  defined for every count, sign extension is written with xor/sub on unsigned values, no signed type appears).
* `forms.py`: the slice (add adc sub sbb cmp and or xor test inc dec neg not shl shr sar rol ror imul mul movzx movsx setcc
  cmovcc lea bswap cdq xchg push pop, plus mov and one memory form each way) built from a capstone decode; form keys are the
  ones of `test/instest.py` (`form_key` copied; prove.py does not import instest, which needs numpy/elftools/unicorn).
* `enc_table.py`: 393 concrete encodings (keystone + a few raw hex), 270 form keys.
* `prove.py --backends`: emitted C++ compiled with `g++ -std=c++17 -O1 -Wall -Wextra -Werror -fsanitize=undefined
  -fno-sanitize-recover=all`, run on 1000 random inputs per form (half drawn from edge values), compared with the z3 term
  (registers, six flags, eip, four probed memory bytes; memory is a hash function of the address, overlaid by the writes).
* `prove.py --model MODULE`: the proof gate, model-agnostic; the plug-in interface is in the docstring of prove.py.
  Plumbing was checked only with a reference equal to the DSL (proves nothing) and with a deliberately broken copy of
  forms.py (AF/OF changed) which gives FAILED with a counterexample line.

Undefined flags (declared in the forms, excluded from obligations): AF of and/or/xor/test and of shifts; SF ZF AF PF of
mul/imul; OF of shifts and rotates unless the masked count is 0 or 1.
Doubts (SDM silent or loose, to be settled by the Sail model): CF of shl/shr/sar with a count above the operand width on
8/16-bit operands (DSL: last bit shifted out, 0 beyond); CF of rol/ror when the count is a non-zero multiple of the width
(DSL: lsb/msb of the unchanged result); `lea r16` takes the low 16 bits of the 32-bit address.
Measured times: see the bottom.
Backend gate: 270 form keys x 1000 inputs, compile (g++ UBSan) + run + z3 evaluation 153 s total (4 cores shared); 0 disagreements.
