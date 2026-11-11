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

### One-command toolchain build (L3b C2)
`bash ports/fnv-nx/farm/sail_setup.sh` builds everything from a clean Ubuntu 24.04 root shell: apt packages, opam 2.2.1, opam-repository pinned to one commit,
switch `sail` (OCaml 4.14.2, the 52 pinned opam packages incl. sail 0.20.3), isla e9b5d94 (`cargo build --release --locked`) and its isla-sail plugin,
z3-solver 5.4.0.0 (pip, libz3.so.5.4), sail-x86-from-acl2 0c6c023, then `sail_build.sh` (stock IR, proof IR, smoke test); it prints the sha256 of
`x86_proof.ir` and exits 1 unless it equals the pin. Every version, commit and sha256 is in `proof/sail_pins.txt`. Env: `REMS` `TOOLS` `WORK` `JOBS`.
`MIRROR=<dir> bash ports/fnv-nx/farm/sail_setup.sh` takes every download from `<dir>`; `bash ports/fnv-nx/farm/sail_setup.sh --make-mirror <dir>` fills it from the network.
The IR is path independent: isla-sail records source file names in the IR, so `sail_build.sh` runs it in a staging tree with relative names only.
Cause of 3acf7d99 (README) vs 66cf6d73 (farm): the old script passed the splice files by absolute path (`$HERE`); run from `/home/user/nx-agent/ports/...` it gives
3acf7d99 (10,389,604 B), from `/home/user/nx-l3/ports/...` 66cf6d73 (10,389,595 B), both reproduced. The staged build gives 55f87ab6 (10,389,242 B) in every directory.

### Sail model patches (W9b)
`proof/sail_patches/rotate_count.sail` (rotate_left_bits/rotate_right_bits reduce the count mod width) and `ror_cf.sail` (ROR CF = msb of the result for masked
count >= 2) are spliced in by `sail_build.sh` (default; `x86_proof.ir` sha256 55f87ab6... since the path-independent build, was 3acf7d99... / 66cf6d73... with absolute paths); `sail_build.sh --stock` builds the unpatched `x86_proof_stock.ir`
(sha256 1de1cdc6...). `test/rothost.py` compares the patched model with the native host exhaustively (8/16-bit rol ror rcl rcr shl shr sar, all values x counts 0..255 x
CF-in, cl and imm8 forms, second pre-flag pattern too): 488,112,128 cases, diff 0 on every SDM-defined output; `mfree` = model leaves a defined output
undefined (shl/shr/sar CF beyond the width), `undef-info` = host value of an SDM-undefined output differing from the model's.
Third patch (L3b, `sail_patches/bt_bitnumber.sail`): `x86_bt_0f_a3` (BT r/m, r with a memory operand) took `bitnumber = mod(abs(signed(offset)), 8)` while the byte
address is `floor(offset / 8)`: for a negative offset it selected the mirror bit (offset -1: bit 1 of byte -1; SDM and hardware: bit 7). The patch uses
`offset - 8 * floor2(offset, 8)`. The SDM says the addressed unit is `EA + size/8 * (BitOffset DIV size)` and the bit `BitOffset MOD size` (floor); `test/bthost.py` runs the real
instruction on the host (bt/bts/btr/btc, m16 and m32, register offset, memory window compared byte by byte, two memory patterns) against the DSL: m16 all 2^16 offsets,
m32 a class cover (all 32 residues x q in -4096..4095 and every power-of-two boundary up to 2^26 and both extremes), 2,642,944 cases, 0 disagreements; the stock formula
disagrees with the host in 123,756 of the bt cases. The DSL (and the hand emitter `fnvlift/f_int.py bitstr`, which uses the same word-wise rule) is right. The model implements only 0F A3 and 0F BA /4 of the bit group;
0F AB/B3/BB and 0F BA /5../7 are `Opcode Unimplemented` (model-gap, so bts/btr/btc shapes cannot be certified against it: their DSL is checked on the host only), 0F BA /4 takes the
bit index mod size from an 8-bit immediate (no sign issue). The IR is now sha256 e436ee6f... (10,389,445 B).
Gate with the patched IR (forms.py md5 5ba2bb32...): proved 358, FAILED 33 (rol/ror OF at masked count 0, shl/shr/sar CF beyond the width), model-gap 2.

### Shape proofs (L3b C1c): the unit is the encoding SHAPE, not one encoding
`shape.py` (canonical key `<hex, imm/disp/rel bytes zeroed>/<imm_off>.<imm_size>.<disp_off>.<disp_size>`), `symform.py`, `shapeprove.py`, `symcheck.py`, `farm_shapeprove.sh`.
* **Model side.** `model_sail.model_post_shape`: isla runs with `code_bytes` as an unconstrained register and `--reset-constraint` fixing every instruction byte
  outside the symbolic ranges (and the 16 - len window bytes to 0); the field bytes stay free. The traces' `read-reg code_bytes` is bound to
  `Concat(concrete bytes, fld_imm / fld_disp bytes)`. isla forks only on the sign bit / value ranges of the field (e.g. imm8 sign extension, shift-count
  masks): the paths come back with their path conditions and are merged as before, so the case split is exhaustive by construction (isla enumerates every
  feasible branch); no field is split by hand (certificate `split: none`). eip: `code_base = rip` stay symbolic with `rip < 2^32 - 16` (assumption A-EIPRANGE; cost
  about 10 s per shape instead of 1-2 s); `--eip fixed` keeps 0x401000 (assumption A-EIP "semantics is translation-invariant in eip") and the certificate gives
  `eip_dependence` per shape by the structure of the Form (`none`: eip only in eip + size; `target`: relative transfer; `data`: eip stored, i.e. call).
* **DSL side.** `fnvlift/dsl.py` has a field-variable node `F(name, w)` (z3 name `fld_<name>`) and nodes may stand in `C(...)`. `symform.symbuild` presents to the
  unchanged `forms.build` an adapter instruction whose immediate / displacement operand values are 32-bit DSL nodes (`ext32(slice of F('imm'))`, plus `eip + size`
  for a relative target) and whose address is `V('eip')`. The slice, the zero/sign extension and the relative flag are read from capstone by probing (closed
  hypothesis set, else `field-decode`). A builder that compares / branches on the field raises `SymbolicBranch` (class `builder-branches-on-field`):
  today only `enter` (forms_flow.py: `assert c.ops[1].val == 0`, the nesting level).
* **Link to the lifter** (it builds concrete Forms): `canon(build(i)) == canon(symbuild(shape(i))[fields := i, eip := addr(i)])`, canon = serialization after constant
  folding; `symcheck.py` checks it for EVERY instance of the image (all unique (address, bytes) of the code ranges), `tests/test_symform.py` on synthetic encodings.
* **Obligation** = `prove.prove_one` (imported); the field variables are free in the unsat query. `shapeprove.py --shard K/N`, farm: `bash ports/fnv-nx/farm/farm_shapeprove.sh K N`.
  `dsl_sha` = `symform.shape_sha(key)` (no z3). Assumptions: A-FLAT, A-EIPRANGE | A-EIP, A-SEGLIM (the model's segment-limit faults, now also
  `:OUT-OF-SEGMENT-INSTRUCTION-POINTER`, are excluded; symcheck counts the relative targets of the image that leave [0, 2^32): 0 expected).

## Gates `forms` and `fpexact` (fnvnx-gate/1, verify/VERDICT.md classes 6 and C)
Both read their private inputs the way verify/gate_contract.py does: declared as content-pinned externals of fnv-nx-build, loaded by `recipe.fetch_externals`
from `ports/fnv-nx/verify` (sibling of lift/; a lift-only branch has no verify/, so the gates are red with that reason and the tests overlay it through
`FNVNX_TEST_VERIFY`). No path arguments, no environment variables other than the verdict's `GATE_OUT` / `GATE_CACHE`. A missing or invalid input is RED with the reason.
* `formgate.py` (gate `forms`): universe = the shapes of `shapes.tsv` in the external `liftpack` (`shape_key<TAB>family<TAB>count`, sorted); certificates = `certs.tsv`
  of the external `certs` (fnv-nx-build `l3b-lift/certs`). Inputs: `formgate_inputs.json` (`fnvnx-forms-inputs/1`; `model_sha256` must equal the `x86_proof.ir` pin of
  `sail_pins.txt`). A shape is certified iff a `proved` row of the pinned model carries the dsl_sha the current code gives. Today the file declares no `liftpack`:
  red, `inputs: external liftpack not declared` (the docstring says what to add). Tests: `tests/test_formgate.py` (local bare repos as externals).
* `gate_fpexact.py` (gate `fpexact`): per admitted product root (gate_build.quantify convention, no root = universe 0 green): the rcp/rsqrt certificate, no A64
  estimate instruction, float->int conversions only in marked helpers (plugin `toolchain/f2i_check/fnvnx_f2i_check.cc`, result file `obj/<unit>.o.f2i.json`), and the item
  `<root>:forms` = the gate forms is green (the helpers' correctness is the form certificates'; evaluated by calling `formgate.judge`, since each gate has its own GATE_OUT).
  Tests: `tests/test_fpexact.py`.

### Why the first 340-shape measurement timed out, and the rules that closed it (L3b)
Measured causes (12 shapes: `mov m32,r32` x5, `mov m16,r16` x2, `mov m32,imm`, `imul r32,m32`, `imul r32,r32,imm`, `div m32`, `div r32`): not the isla path count (1-3 paths) but the queries.
* Fetch offsets: the model reads displacement / immediate bytes of the instruction window with 128-bit `lshr` by `8 * ((code_base + K) - (rip + K))`, the sums formed at 48 or 64 bits.
  `model_sail.zext_add_pairs`: `Z_V(c + Z_W(e)) = c + Z_V(e)` for a constant `c < 2^W - 2^n` (the sum cannot wrap), unconditional; with the existing eip rule, applied to a fixpoint (each exposes the other),
  the shift amount becomes a constant and the window read a plain extract. Closed: every `mov`/`imul` timeout.
* One query per output: `prove.check` solves `assume AND ci` for each differing output (one register, one flag, the memory, eip) instead of one query over the disjunction (a complete split: unsat iff all are).
* SIB scale: the DSL has `index * 8`, the model a shift; `rewrite.pow2_mul` turns `x * 2^k` into the wiring `concat(x[w-1-k:0], 0^k)` on both sides.
* Division: the model computes the remainder as `a - b * floor(a / b)`, the DSL as `bvurem`; z3 cannot relate them (the identity is `unknown` after 60 s even at 16 bits).
  `rewrite.div_norm`: `bvurem(a, b) = If(b = 0, a, a - b * bvudiv(a, b))`, the SMT-LIB definition, and truncation of `+ - *` distributes (`Extract(h, 0, a op b) = Extract(h, 0, a) op Extract(h, 0, b)`).
* The fault-equivalence check ("no DSL fault implies the model does not fault") negated the full `assume`, which also holds closed constraints on the model's free values (`0 <= v` of an Int that stands for an
  undefined result), so it was satisfiable by choosing `v < 0`. The model now returns `side` (those closed constraints) and `nofault` (the pre-state condition); the check negates `nofault` and requires
  `side` satisfiable once (a closed formula over free values is satisfiable for every pre-state iff it is satisfiable).
All rewrites are identities of the bit-vector semantics (tests/test_rewrite.py checks each on 8/16-bit instances for all inputs); none changes what is proved.
