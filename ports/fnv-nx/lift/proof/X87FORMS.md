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

**Transcendentals** (F2XM1, FYL2X, FPTAN, FPATAN, FSIN, FCOS and FSINCOS: D9 reg /6 rm0-3, /7 rm3 rm6 rm7):
- OWNER 8 says "correctly rounded at the operand type". The spec leaves them uninterpreted, so no Form can be proved
  against it.
- These classes stay uncertified with reason `spec: uninterpreted x87_<fn>`.
- They are reported to the orchestrator: a proof route for correctly rounded transcendentals needs an owner decision, for
  example CORE-MATH's published proofs under OWNER 9.

**Order.**
1. Slice 1: the translator with the op-symbol mechanism, the Form entry point and the prover branch, for the classes whose
   DAG has no FP op and no Real (FLD m64, FILD m32, FLD ST(i), FXCH, FCHS, FABS, FFREE, the control and environment
   instructions).
2. Slice 2: the op proofs of the rounding machinery, then FLD/FST m32 and m64, the arithmetic and the compares.
