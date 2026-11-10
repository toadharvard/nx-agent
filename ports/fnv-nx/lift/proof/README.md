# proof/ : single-source instruction semantics (W8)

Status after the scope change: the only reference model for form proofs is the Sail x86 model (`model_sail.py`, section Sail
below); the VEX second model was cancelled and `model_vex.py` was never finished, so it does not exist. What exists and is measured:

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

## Sail (reference model = Sail x86 model run by isla)

Files: `model_sail.py` (driver), `x86.toml` (isla config), `sail_build.sh` (idempotent rebuild), `x86_fetch.sail` + `x86_code_regs.sail`
(the two splice files). Gate: `python3 proof/prove.py --model model_sail`. Scratch/cache: `/home/user/l3work/proof` (`sail/x86_proof.ir`,
`sail_cache/`: raw isla output per encoding, key = sha256 of IR + config + isla arguments + encoding bytes).

**Execution.** `isla-execute-function -A x86_proof.ir -C x86.toml x86_fetch_decode_execute '()'` with `-I` register settings: CS DS ES SS FS GS
flat (base 0, limit 0xffffffff, attributes 0x0c9a: D/B=1, L=0, present, writable), EFER.LMA=0, CR0..4=0, `app_view=true` (linear memory, no paging),
`ms_reg=fault_reg=false`, `rip` = 0x401000. General registers, rflags (all 32 bits) and data memory are symbolic. Instruction fetch: the model reads
code through `rb(.., ":X")`; `x86_fetch.sail` replaces `rb` so that fetches come from the registers `code_bytes` (16 bytes) / `code_base`
(because isla-execute-function cannot preload memory and symbolic code would fork on every opcode); data accesses are untouched
(`read-mem`/`write-mem` events, 1 byte each). `-L bool_to_bits -L bits_to_bool -L zf_spec` linearise three tiny helpers so flag code does not fork.
isla returns one trace per feasible path (SMT-LIB `declare/define-const`, `assert`, `read-reg`, `write-reg`, `read-mem`, `write-mem`).
`model_post` evaluates the traces with its own SMT-LIB-subset evaluator into z3 terms: a pre-value read becomes the prover's variable
(`rax = Concat(rax_hi, eax)`, rflags = flag variables + a free `rflags_other`), memory events are applied in order to the pre array (a read is a `Select`
of the array as modified so far), paths are merged as `If(path condition, post, ...)`. Values the model leaves undefined (`undef_flg()`) stay free z3
variables, so an undefined model flag against a flag the DSL declares defined is a counter-example. Asserts that only constrain such a free value go into the
returned `assume`; asserts over the pre-state are the path condition. A path that ends in a model exception (unimplemented opcode, #UD) is a
`ModelGap` carrying the message (printed through `-D p --probe x86_fault/x86_model_error`). The one exception kind tolerated is `:SEGMENT-LIMIT-FAIL`
(an access that wraps past 4 GiB is a #GP in the model, wraps silently in the DSL): those paths are excluded by `assume` (the only change in
`prove.py`: an optional `'assume'` key of the model result is added to the solver and hashed into the certificate); the obligation is thus
"for all pre-states in which the model does not fault". A `0x64/0x65` prefix is modelled by running with base 0 and adding a free `fs_base`/`gs_base`
to every data address (checked in a run with base 0x10000: lin = base + zero-extended eax, truncated to 32 bits, segmentation.sail:273).

**Mode question.** The model implements 64-bit mode and "compatibility mode"; the 32-bit protected-mode case is handled as compatibility mode
(opcode_ext.sail `x86_operation_mode`: `Mode_64bit` iff EFER.LMA and CS.L, else `Mode_Compatibility`; `modes.sail` says "32-bit mode" covers both).
Real mode, V86, SMM are not implemented. State selects the mode (`-I` registers), so 32-bit semantics are available: default operand/address size
from CS.D (memory_accessors.sail `select_address_size`), stack width from SS.D, segment base/limit/attributes honoured (`ea_to_la`, null selector and limit
checks), 0x40-0x4f are inc/dec (REX only in 64-bit mode; in 64-bit mode `40` decodes as REX + `00 00`, rip+3), 0x66 forms, push/pop/call/ret with a
32-bit stack (ran: all proved/ran in `/home/user/l3work/proof/t2.py` list) and fs: overrides all execute. Consequently no form needs the 64-bit-mode workaround
(0x67 / constraining upper halves); the driver still returns the low 32 bits of 64-bit registers with free upper halves, so a dependence on an upper half
would be a counter-example.

**Gate result** (`prove.py --model model_sail`, 393 encodings / 270 form keys, cold isla cache 251 s, warm 13 s): proved 349, FAILED 42,
model-gap 2 (bswap: "Opcode Unimplemented in x86isa!"), timeout 0. The 42 FAILED are all rol/ror/shl/shr/sar on 8/16-bit operands and ror/rol OF/CF, and
every one is a disagreement about a flag or rotate the SDM defines or leaves undefined, none is an arithmetic disagreement:
* shl/shr (r8,r16): CF for a count >= operand width: the model leaves CF undefined (free value), the SDM says CF is undefined there for SHL and SHR;
  the DSL claims the last bit shifted out (0 beyond): DSL over-claims. sar (r8,r16): the model is equally loose but the SDM does not exempt SAR; the DSL (sign bit) is right.
* rol/ror OF with masked count 0: the model leaves OF undefined, the SDM says no flag is affected: DSL right, model under-specified.
* ror (any width) CF for count >= 2: the model takes CF = bit 0 of the result (rotates_spec.sail ror_spec_*); the SDM says CF = msb: model wrong.
* rol/ror (r8 count 9..31 except 16/24 coincidences, r16 count 17..31): the model's `rotate_left_bits`/`rotate_right_bits` (prelude.sail) do not reduce the
  count modulo the width, so the result is wrong (rol bl,9 of 0x7f gives 0, SDM 0xfe): model wrong. For a count equal to the width (8/16) the result is unchanged and rol CF agrees.
Doubts: (1) CF of shl/shr/sar beyond the width: undefined in the model (see above). (2) CF of rol/ror with a non-zero masked count that is a multiple of the width:
rol agrees with the DSL (lsb of the unchanged result) at count 8/16; ror disagrees only because of the model's ror CF bug; r8 counts 16/24 hit the rotate bug.
(3) `lea r16`: proved, low 16 bits of the 32-bit address, upper half of the destination kept. (4) 16-bit cmovcc (all 16 conditions, reg-reg): proved, the upper half is unchanged whether or not the move happens.
Memory forms rely on the `assume` (no segment-limit #GP: access wrapping past 4 GiB).
