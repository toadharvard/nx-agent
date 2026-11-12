# int3: blocks the assumptions gate reads as assumptions and that cite no ASM id

Head: int3. Each block: file:line, the sentence with the word, the owning layer by path, and the kind.

kind "solver-term": the word is the SMT identifier `assume` / field `assumptions`. It cannot be reworded: it names a key the Sail model text emits (`model_sail.py` `h == 'assume'`), a parameter of `prove.check`, and text of the stored certificates (`fault_checks`); a rename changes certified data. The orchestrator decides: exempt by the gate rule, or rename with the certificates.

kind "decide": may be a real project assumption: a fact or gate that removes it (reword plus the gate named), or a REMOVE record requested from L1.

- `lift/README.md` (block starts at line 100; word at line 101), owner L3a (lift README), kind decide
  > Assumption recorded: memory forms are proven for pre-states where the model raises no segment-limit fault; on the
- `lift/README.md` (block starts at line 95; word at line 97), owner L3a (lift README), kind decide
  > against the pinned x86 host exhaustively where the input space allows (16 and 32 bits). Assumption A3 for this
- `lift/proof/README.md` (block starts at line 138; word at line 138), owner L3b (proof), kind solver-term
  > * One query per output: `prove.check` solves `assume AND ci` for each differing output (one register, one flag, the memory, eip) instead of one query over the disjunction (a complete split: unsat iff all are).
- `lift/proof/README.md` (block starts at line 142; word at line 142), owner L3b (proof), kind solver-term
  > * The fault-equivalence check ("no DSL fault implies the model does not fault") negated the full `assume`, which also holds closed constraints on the model's free values (`0 <= v` of an Int that stands for an
- `lift/proof/README.md` (block starts at line 33; word at line 44), owner L3b (proof), kind solver-term
  > returned `assume`; asserts over the pre-state are the path condition. A path that ends in a model exception (unimplemented opcode, #UD) is a
- `lift/proof/README.md` (block starts at line 67; word at line 72), owner L3b (proof), kind solver-term
  > Memory forms rely on the `assume` (no segment-limit #GP: access wrapping past 4 GiB).
- `lift/proof/census.py` (block starts at line 12; word at line 16), owner L3b (proof), kind decide
  > the family is a function of the key over the whole census by checking the finite set, not by an assumption; the representative encoding is decoded again and must give the same shape and family.  The form key is that of the REPRESENTATIVE (capstone names the predicate of cmpps/cmppd/cmpss/cmpsd by th
- `lift/proof/gate_fpexact.py` (block starts at line 17; word at line 24), owner L3b (proof), kind decide
  > source named by the hash computes RCPSS / RSQRTSS of the reference CPU for every input. Registered assumptions (ports/fnv-nx/lift/ASSUMPTIONS.l3b.json): A-RCPHOST (the certifying run was made by farm/rcpcert.sh on a CPU
- `lift/proof/gate_fpexact.py` (block starts at line 39; word at line 39), owner L3b (proof), kind decide
  > Check 3, float -> integer exactness (item `f2i:<root>/<unit>`). The x86 rule (cvt*2si, cvtt*, fist, fistp, fisttp): rounding by MXCSR.RC /
- `lift/proof/prove.py` (block starts at line 1; word at line 12), owner L3b (proof), kind solver-term
  > ret  = {'regs': {8 names: BV32}, 'flags': {6 names: BV1}, 'mem': post-state Array, 'eip': BV32, optional 'assume': Bool (pre-states the model is defined on)}
- `lift/proof/prove.py` (block starts at line 75; word at line 75), owner L3b (proof), kind solver-term
  > def check(conds, assume, args):
- `lift/proof/rewrite.py` (block starts at line 1; word at line 5), owner L3b (proof), kind decide
  > which are identities of the SMT-LIB bit-vector semantics (the special cases keep the original term with the operands substituted, so nothing is assumed).
- `lift/proof/shapeprove.py` (block starts at line 1; word at line 10), owner L3b (proof), kind solver-term
  > model_terms_sha, result, defects, assumptions, split, eip_dependence, note
- `lift/rt/fmath.c` (block starts at line 5; word at line 5), owner L3a (rt), kind decide
  > RULE (one engine for sin cos tan atan2 2^x-1 y*log2(x) y*log2(x+1)): ZIV'S STRATEGY on multi-precision binary floats.
- `lift/rt/msvcrt_eh.cc` (block starts at line 418; word at line 418), owner L3a (rt), kind decide
  > /* ---- _XcptFilter: Wine msvcrt_exception_filter over the process's signal() handlers ---- */
- `lift/rt/unhandled.cc` (block starts at line 35; word at line 37), owner L3a (rt), kind decide
  > the esp of the call. ContinueExecution returns to the native body (changes a handler made to that CONTEXT do not reach native state: assumption
- `lift/test/fmathtest.py` (block starts at line 454; word at line 456), owner L3a/L3b (test), kind decide
  > n = 6..60 within the relative error the proofs assume (table: < 2^(1-32n); series: <= 2^(64-32n))"""
- `lift/test/modlift_closure_test.py` (block starts at line 1; word at line 3), owner L3a/L3b (test), kind decide
  > AND pcsrc's s4 result (<facts>/<module>/pcsrc_s4.json) says 0 violations. No pcsrc file -> red 's4-unchecked' (never assumed green); violations > 0 -> red 's4-violation'.
- `lift/tools/ehflow.py` (block starts at line 286; word at line 286), owner L3a (tools), kind decide
  > # ---- imports: the declared machine's contract (L1 keys.tsv: calling convention and argument types of every imported function), Win32 as documented (ASSUMPTIONS.json 3):
- `lift/tools/ehtables.py` (block starts at line 1; word at line 8), owner L3a (tools), kind decide
  > (Win32 as documented, ASSUMPTIONS.json 3); otherwise unknown.
- `lift/tools/ehtables.py` (block starts at line 234; word at line 236), owner L3a (tools), kind decide
  > key list read only by the C++ frame handler (assumption), and its keys lie mid-block."""
- `lift/tools/ehtables.py` (block starts at line 268; word at line 269), owner L3a (tools), kind decide
  > """a decoded instruction is code only if every relocated dword it overlaps is one of its own operands (its immediate or displacement). Assumption: a relocated dword in the
- `lift/tools/fpmodes.py` (block starts at line 250; word at line 250), owner L3a (tools), kind decide
  > def statclass(F, x):
- `lift/tools/locksites.py` (block starts at line 1; word at line 3), owner L3a (tools), kind decide
  > (docs/ORDERING.md "Lifted code", assumption LOCK-SPLIT).
- `lift/tools/residue.py` (block starts at line 3; word at line 10), owner L3a (tools), kind decide
  > the residue is red, never assumed green.
- `lift/tools/residue.py` (block starts at line 77; word at line 79), owner L3a (tools), kind decide
  > FDP sources typed); export / tls / entry / eh: every such address is an entry of the universe (checked here, not assumed); no_s4_arith: pcsrc_s4.json exists and says 0 violations. A missing file is open."""
- `toolchain/f2i_check/fnvnx_f2i_check.cc` (block starts at line 1; word at line 14), owner L5/L2 (toolchain plugin), kind decide
  > //             (see asm_may_read_float for the stated assumption).
- `toolchain/f2i_check/fnvnx_f2i_check.cc` (block starts at line 93; word at line 94), owner L5/L2 (toolchain plugin), kind decide
  > // FP/SIMD register (v0..v31 and their s/d/q/h/b names, decoded by GCC's decode_reg_name) clobbered. Assumption (stated in the gate):
