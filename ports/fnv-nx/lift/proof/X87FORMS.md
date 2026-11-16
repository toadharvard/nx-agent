# X87FORMS: x87 Forms derived from the OWNER-10 spec (L3b design, 2026-11-16)

Problem: the universe has 1,534 x87 shape keys and `forms.build` has no x87 builder. The DSL has the x87 state effects
(fr, ftop, ftag, fsw, fptr; `dsl.xc_lines`, the `fx_dsl_*` helpers), but it has no value operations for them. Hand-written
builders per class would restate the spec. That is a second copy of the fact (CLAUDE.md 2026-11-15, rule 2), so they are
not written.

## Rule: the Form of an x87 key is generated from x87spec

1. **Selection.** The key's class is `x87class.cls(key)`: opcode D8..DF, mod (reg|mem), ModRM.reg, and ModRM.rm where the
   SDM opcode map row holds several instructions. The spec entry is `x87spec.REGISTRY[class]`, keyed by the same tuple.
   - A class with no entry is uncertified with the reason `no SDM page` (CLEARED_UD) or `no spec entry <class>`.
   - Nothing is chosen by mnemonic.
2. **Translation.** `proof/x87form.py` applies the entry to the symbolic pre-state of the DSL: `from_dsl` over the DSL
   inputs, with the memory operand as the DSL load of the shape's width.
   - It translates the resulting z3 terms (Out.s through `to_dsl`, Out.mem, Out.wr, Out.ax, Out.eflags, the fault kind and
     Out.trap) into one DSL DAG, operator by operator.
   - The map is one table, `X87OPS`: z3 decl kind -> DSL op.
   - A z3 operator outside `X87OPS` raises `Unsupported('x87 op <kind>')`, so that key is uncertified, naming the op. There
     is no fallback.
   - The DAG becomes the Form's effects: `xs` for the x87 state, `stores`, `regs` (ax), `flags` (fcomi), `faults`.
3. **Preconditions.** `Out.pre` (masked OE/UE of a register result) and the masked state of every unmasked-exception
   branch come from the facts. Every FLDCW, FLDENV, FRSTOR and FXRSTOR site keeps the masks (CLAUDE.md, hardware
   exceptions). The Form carries `pre` as its domain: a site where the fact does not hold is OutsideDomain and red.
4. **Ops.** Each DSL op that `X87OPS` names has:
   - its SMT semantics, which is the z3 operator itself, so the translation is the identity on terms;
   - one C helper in `rt/fnvl_x87.h` next to the `fx_dsl_*` helpers, over the runtime carriers.
   The universe of ops is derived: walk every REGISTRY entry on a symbolic state and collect the decl kinds. Hand-writing
   the list is not allowed.
   Each helper gets an op-level test against z3 evaluation of the same operator, the class of fpexact/opmap:
   - every bit-vector op exhaustively over a reduced width, or by the existing opmap proof;
   - every FP op (fpAdd, fpMul, fpDiv, fpSqrt, fpToFP and the rest, with the rounding mode operand from FCW.RC) under the
     OWNER-8 IEEE semantics on binary64 bit patterns.
5. **Proof.** prove.py gets the x87 branch. The Form's post-state, all fields (`xs`, mem, regs, flags, eip, fault), must
   equal `execute(class, from_dsl(pre))` mapped by `to_dsl`.
   - This is true by construction except for the translator, and the check closes translator defects.
   - `Form.check_state` holds, because every node reads only DSL inputs.
   - The certificate is the ordinary shape certificate: shape_sha plus dsl_sha.
6. **Emission.** lift_dsl emits the Form like any other: path=form. The x87 handler path (`f_x87*.py`, `fnvl_x87.h` fv
   carriers) is deleted for each class in the same commit that certifies it (the emission rule).

## Speed (it closes no way to the targets)

The spec computes every observable x87 output: sticky flags, C0-C3, the tags, FIP/FDP/FOP. Most of them are dead
between reads (fnstsw, fnstenv, fsave, fxsave, an SEH CONTEXT copy). The class rule that removes the dead ones is the
FLAGLIVE projection lifted to the x87 state fields:
- the same Lemma 1, because every field is a separate term;
- the same exit and escape points, plus every x87 store-environment instruction as a read of all fields.
It is built from the first frame on, as a speed rule. Until then the code is complete and correct, only slower; no fast
path is switched off, so there is no PERFDEBT entry.

## Order of work

1. x87form.py: the op census (derived) and the translator; the op table; the proof branch. One worker.
2. The C helpers and their op tests. The same worker, in the same item, so that the census and the helpers stay one set.
3. Emission switch-over per class, then deletion of the handlers (with L3a, owner of the emitter).

## Ruling after the op census (w-x87f 5217dc179: 73 decl kinds, 118 entries)

The census shows that one DSL op per z3 decl kind is the wrong grain, for two reasons:
- 64 entries reach exact-real arithmetic (FPA_TO_REAL, Real DIV) inside the part (i) rounding machinery;
- six transcendentals are uninterpreted.

**Revised ops: the part (i) functions are the ops.**
- The translator runs each REGISTRY entry with every part (i) machinery function (view64, to_m80, the rounding and
  conversion helpers, tag_of_img and the rest that the entries call) replaced by a z3 uninterpreted symbol of the same
  name and sorts.
- The resulting DAG is over bit-vector, array and logic kinds plus those symbols. Each symbol is one DSL op, so the op
  set is the spec's own function structure and needs no pattern matching.
- Real terms appear only inside a part (i) function, never in the DAG. A Real kind that survives in the DAG raises
  Unsupported, naming the function that leaks it.
- **Per-op proof.** For each op, prove once and universally, by z3 at the deterministic bound (bounds.Z3_RLIMIT), that the
  original part (i) function equals the IEEE z3 term that the C helper computes. For example, view64(img, fcw) equals
  fpToIEEEBV of fpToFP(rm(fcw), the FPSort(15,64) value of img), with the NaN payload rule stated in bit-vector terms.
- The C helper computes that IEEE term natively (OWNER 1 for the compiler, OWNER 8 for IEEE).
- The spec text is unchanged. No second formulation of a clause is written; the equivalence is a proof obligation per op.
- **Entry proof.** Form equals execute() as before, with each op symbol instantiated by its function. This is true by
  construction, and it checks the translator.

**Transcendentals** (F2XM1, FYL2X, FPTAN, FPATAN, FSIN, FCOS, FSINCOS). Orchestrator ruling, CLAUDE.md integ
b7b6f0df0:
- The value of the spec's uninterpreted `x87_<fn>` is CR_binary64(fn), by OWNER 8.
- The implementation is CORE-MATH (tools/core-math, built into the guest runtime by L2), taken by its documentation under
  the owner's existing rule for external libraries. No proof is claimed for it.
- The Form is the SDM machinery around the call: stack, tags, C1, C2 for an out-of-range |x| >= 2^63, the #IA domain when
  unmasked. That part is certified against the spec, like every other entry.
- The call of CR(fn) is one op symbol bound to the library symbol. The certificate states the binding, and the op proof
  for it is the binding itself.
- The lowering table of that section is L3a's to emit (for example FYL2X = y * cr_log2(x), an IEEE multiply).

**Order.**
1. Slice 1: the translator with the op-symbol mechanism, the Form entry point and the prover branch, for the classes whose
   DAG has no FP op and no Real (FLD m64, FILD m32, FLD ST(i), FXCH, FCHS, FABS, FFREE, the control and environment
   instructions).
2. Slice 2: the op proofs of the rounding machinery, then FLD/FST m32 and m64, the arithmetic and the compares.

3. Slice 3: the transcendental classes, with x87_<fn> bound to CORE-MATH.

## Slice 1 (x87form.py)

- **Entry:** `forms.build` calls `x87form.build` first for an instruction whose opcode byte is D8..DF or 9B. The class is `x87class.x87_class(raw)` in the spec's key spelling; the register index `i` is ModRM.rm.
  A class outside this slice raises `x87form.Unsupported` naming the thing; `forms.build` then raises `KeyError` with it as `__cause__` (the handler path as before).
- **Translator:** `Tr`, one table over the z3 decl kinds that the census found (BV arithmetic and logic, compare, ite, extract, concat, extensions, Boolean). An array over BV3 (the register file)
  is eight DSL nodes; `Select`/`Store` with a symbolic TOP index is an `ite` chain over fr0..fr7. Any other kind raises `Unsupported('x87 op <KIND>')`; an op symbol raises `Unsupported('x87 op symbol <name>')`.
- **Op symbols:** only part (i) functions with a floating-point or Real expansion (`introduces_fp`). A BV-only part (i) function stays inline: its kinds are the DSL's own ops, already proved by opmap, and a
  symbol would state the same fact a second time. No op symbol is implemented in this slice, so there is no new C helper and no op proof; the classes that need one are slice 2.
- **Form effects:** `fr`, `ftop`, `ftag`, `fsw`, `fcw` (slice 1b; C line `FLDCW(w);`, the runtime's one place that sets c->fcw and the host rounding mode), `fptr`, `store`, `eax` (fnstsw ax).
  A memory operand wider than 128 bits is `mem_pieces`: the 32-bit words of the environment image (x87spec.ENV_BITS), then the 80-bit registers after it (SAVE_BITS); one load or store per piece.
  Out of the Form (Unsupported, the reason names it): EFLAGS (fcomi family), a nondeterminism without a row in `NDRES`, FP / Real kinds and op symbols (slice 2).
- **Domain (slice 1b):** `Form.domain` (base3 80bd1328d's field, `require`): FCW[5:0] all set (the masks fact: every FLDCW/FLDENV/FRSTOR/FXRSTOR site keeps them), no pending-exception trap (ES clear), `Out.pre`,
  and `nd_pre` at the resolution. The Form has no FLT fault: every x87 exception is unmasked-only and outside the domain. Instantiating the Form where the domain is not proved is L3a's masks fact's obligation.
  ES clear is a state invariant (ES is derived from the sticky flags and FCW) that the domain states; it is not a separate fact.
- **Nondeterminism (slice 1b):** `NDRES`, one table keyed by the spec's nd names (`nd_names()` derives them): resolution term and source. Rows: nd.fcmov.uf_false = False (SDM 2A:FCMOVcc reads ST(i) only inside THEN; f_x87.py
  `if(cond) ST0=STi`), nd.fbld.img = bcd_to_img(m) (rt/x87.c fnvl_x_from_bcd, ASM-216). Every other nd (nan.tie, prem.N, trig.*, f2xm1.out) belongs to a class that needs op symbols: slice 2 / 3.
- **Undefined bits:** `Out.undef` / `Out.mem_undef` name bits the SDM leaves undefined. The Form takes the spec's own value for them, a refinement of "any value"; the proof compares exactly.
- **Proof:** `x87form.prove_form`, called from `prove.prove_one` for a Form with `.x87`. On the Form's domain: `nd_pre` at the resolution holds, the Form raises no fault, and every field (x87 registers, TOP, tags, FSW, FCW,
  pointers, memory, EAX, the other registers, flags, EIP, SSE state) equals `execute()[nd := resolution]`. The proof caught a translator defect (z3's n-ary bvadd), now a test.


## Slice 2 part (i): the carrier ops from the C (ctrans.py, x87carrier.py)

Rule (CLAUDE.md "Derive, never enumerate"): the value semantics of the x87 carrier is the C of `rt/fnvl_x87.h` and `rt/x87.c`, so the z3 terms are produced from that C, not written
beside it.  `ctrans.py` parses the C (pycparser, after the pinned cpp-13 expands the macros) and executes it symbolically with exact C semantics: integer promotions and conversions,
shifts, switch with fall-through, forward goto, constant-bound loops, pointers into the store, struct values.  Every undefined behaviour (shift count, division by zero, signed
overflow, float to int out of range, clz 0) is an obligation `Result.ub` and never an approximation; a construct outside the subset is `Unsupported`.  Doubles are BV64: the FPA operation
on non-NaN operands under the host `fpcr_rmode`; NaN results follow the Arm ARM (FPProcessNaNs, FPDefaultNaN, FPConvertNaN; FPCR.DN = 0).  `fxn_slow`'s own payload rule is translated
as ordinary C.  `x87carrier.py` derives the helper set: every identifier of every string literal of the emitters (ast), macros expanded, call graph through the C.  A Form's op symbol is the
helper's name.  Tests: `tests/test_ctrans.py` (closure, host agreement on boundary inputs against the C compiled by the pinned g++-13 with the same text, mutations, NaN rules).
Not translated (outside the value boundary, with the executor's reason): asm over the host FPSR, memory macros, the register file indexed by a symbolic TOP, a C++ template.
