# certs.tsv: the format and the validity rules of a form certificate

A form certificate says: the symbolic Form that the current DSL builds for one encoding shape (proof/shape.py key) was proved equal to the pinned x86 model.
`certs.tsv` is the file gate `forms` (proof/formgate.py) reads; `proof/certs_build.py` writes it from the records of the shape-prover shards.

## Row format (12 tab-separated columns, a `#` line is a comment)
`shape_key  family  dsl_sha  model_sha  reference  result  obligations  undef_flags  def0_witness  def0_free  nonflag_free  declared`
- `shape_key`: the key of proof/shape.py (`<bytes with immediate and displacement bytes zeroed>/<imm_offset>.<imm_size>.<disp_offset>.<disp_size>`).
- `dsl_sha`: sha256 of the SYMBOLIC Form (fields are variables) that the current DSL builds for the shape.
- `model_sha`: sha256 of the pinned proof IR (`x86_proof.ir` in proof/sail_pins.txt).
- `reference`: `sail`. `result`: `proved`.
- `obligations`: the obligations the proof discharged, comma separated (the DEF0 obligation among them); `undef_flags`, `def0_witness`, `def0_free`, `nonflag_free`: the
  DEF0 columns, `-` when empty.
- `declared`: the declared outputs of the row (12th column): the platform-function outputs (uf) that the proof leaves free by contract, `-` when none. The contract is `proof/declared.json`
  (`fnvnx-declared/1`: per function its platform source, its SDM / OWNER basis and the outputs it may define). A result `agree-except-declared` is a certificate only if `platform_sha256`
  of the record equals the sha of the current declared.json rows it used; a uf outside the contract is not declared and the comparison stays strict.

## Certificate key rule (the validity of a record)
A record is a valid certificate of a shape iff ALL of these hold:
1. the shape key is the record's shape;
2. `dsl_sha` equals the sha256 of the symbolic Form the CURRENT DSL builds for that shape;
3. `model_sha` equals the pinned IR sha256;
4. `result` is `proved`, and `obligations` is the full obligation list of the current prover.
Nothing else enters: no run id, no hash of the DSL sources. A DSL edit voids exactly the certificates of the shapes whose Form changed.

## Aggregation rules (certs_build.py)
- Only proved shapes get a row; one row per proved shape; rows are unique and sorted; the output depends on the set of records only (not on shard order, file names or time).
- A record that does not match the key rule is STALE: it is ignored and counted, never a refusal and never a row. Stale kinds: `stale-model` (IR sha differs), `stale-dsl` (dsl_sha differs).
- The run is REFUSED (exit 2, no output) when the records contradict each other: the same current (shape, dsl_sha, model_sha) is `proved` in one record and `FAILED(...)` in another.
- A record that is not `proved` (`timeout`, `FAILED(...)`, `model-gap`) is counted by its result and gives no row.
- `--queue`: the shapes of a shapes.tsv (family `int` with a symbolic Form) that have no valid certificate; the queue id is the first 12 hex of sha256 over `key TAB dsl_sha` of the queue lines.
- `--coverage`: every int key with a Form has one decided record at the current (dsl_sha, model_sha); exit 1 and the missing list otherwise.

## Gate rule (formgate.py)
The gate applies the same key rule to the rows: a row that went stale after it was written is refused. A shape is certified iff a row of the current `dsl_sha` and the pinned `model_sha` with result `proved` and the full obligation list exists.

## Certificate key K (closure rule)

1. Executed set. The isla run of shape S, under the pinned env E (x86.toml, isla pin, SEG/LINEARIZE/sym_constraint source), explores every feasible symbolic path. F(S) is the set of model functions that run calls on any path.
2. Key. K(S) = sha256 over:
   - the text of every function in F(S), sorted by name;
   - every declaration that text names: types, registers, extern vals;
   - the isla builtins used;
   - E.
   Source positions are excluded.
3. Invariance (by induction over run steps). Each step is a function of the text of the function being executed, the declarations it names and E. So the trace, and with it the verdict, is a function of K(S)'s inputs. An edit to a function outside F(S) cannot change the trace. For such a function to become called, a function in F(S) would have to be edited, and that changes K(S).
4. Validity. A record is valid iff K_v2(model_fns, the IR of the record's model_sha) == K_v2(model_fns, the pinned IR). K_v2 is computed here from the two IR files; the stored
   closure_sha is informational. Both IRs are fetched by sha as externals (tools/x86-proof-ir*) and hashed; a missing IR is REFUSED.
5. Test obligations:
   - an edit in an executed function changes K;
   - an edit in an uncalled function keeps K;
   - a position-only change keeps K;
   - a change to E changes K;
   - an IR that does not match the pin is REFUSED.
   - the record's IR is fetched by sha, not trusted: a file whose hash differs from the record's model_sha is not that IR, and a record whose IR is not provided is REFUSED.

## x87 admissible choices (x87spec.py)

The SDM leaves seven points of x87 behaviour undecided. x87spec.py does not pick a value. Each is a free z3 variable
with a precondition (`Out.nd`, `Out.nd_pre`). A Form refines the spec if, for some value of these variables that
satisfies `nd_pre`, the Form equals the spec. A certificate for a shape that uses one of them is valid for the
Form's own choice only, and the K of that record names the variable. Quotes are from the pinned SDM (fnv-nx-build
`tools/intel-sdm` 0768b0baf29a, PDF sha256 a4a62e6a...f321); the page is the PDF page index (`sdm_pages.tsv`).

| variable | meaning | SDM lines (PDF page) |
| --- | --- | --- |
| `nd.prem.N` (BitVec16, 32 <= N <= 63) | FPREM/FPREM1 with exponent difference D >= 64: the amount N by which the exponent is reduced | PDF 1071 (2A:FPREM Operation): "ELSE C2 := 1; N := An implementation-dependent number between 32 and 63; QQ := Integer(TruncateTowardZero((ST(0) / ST(1)) / 2(D − N))); ST(0) := ST(0) – (ST(1) ∗ QQ ∗ 2(D − N));". Bounds also PDF 1070: "The exponent of the partial remainder will be less than the exponent of the original dividend by at least 32" and "no more than 63". FPREM1: 2A:FPREM1 Operation, same text. |
| `nd.trig.push` (Bool) | FPTAN / FSINCOS with a NaN or infinite source: whether the push (1.0 / cosine) happens | PDF 1075 (2A:FPTAN Operation): "IF ST(0) < 263 THEN C2 := 0; ST(0) := fptan(ST(0)); TOP := TOP − 1; ST(0) := 1.0; ELSE (* Source operand is out-of-range *) C2 := 1;". The push is inside the THEN branch, and the SDM does not say whether a NaN or infinity satisfies "ST(0) < 2^63". Table 1-29 (PDF 1074) gives only the ST(0) result: NaN → NaN, ±∞ → "*" (#IA). 2A:FSINCOS Operation has the same shape. |
| `nd.trig.c2` (Bool) | FPTAN / FSINCOS / FSIN / FCOS with a NaN or infinite source: the value of C2 | PDF 1075 (FPTAN, FPU Flags Affected): "C2 Set to 1 if outside range (−263 < source operand < +263); otherwise, set to 0." Whether NaN and ±∞ are "outside" is not stated. PDF 1074: "If the source operand is outside the acceptable range, the C2 flag in the FPU status word is set, and the value in register ST(0) remains unchanged." |
| `nd.fcmov.uf_false` (Bool) | FCMOVcc with a false condition and an empty ST(i): whether the underflow (IE, SF) is reported | PDF 1026 (2A:FCMOVcc Operation): "IF condition TRUE THEN ST(0) := ST(i); FI;" and Exceptions "#IS Stack underflow occurred." and "C1 Set to 0 if stack underflow occurred." The SDM does not say whether the operand check happens when the condition is false. With a true condition the underflow is certain (the operand is read). |
| `nd.f2xm1.out` (BitVec80) | F2XM1 with a source outside -1.0..+1.0 (a NaN excepted): the 80-bit result image | PDF 1011 (2A:F2XM1 Description): "The value of the source operand must lie in the range –1.0 to +1.0. If the source value is outside this range, the result is undefined." Any image is admissible; the flags and exceptions are those the SDM gives for the operand (#IA, #D, #U, #P; C1 = rounded up; C0, C2, C3 undefined). There is no precondition and no L3a range fact is needed. |
| `nd.nan.tie` (Bool) | an instruction with two register operands that are NaNs of the same kind, equal significand and different sign: which operand is the result | PDF 107 (1:T4-8, x87 column): "Two SNaNs: X87 FPU — SNaN source operand with the larger significand, converted into a QNaN." and "Two QNaNs: X87 FPU — QNaN source operand with the larger significand." With equal significands both operands are "the larger"; the SDM names neither. Added to `Out.nd` of FADD..FDIVR (ST,ST(i) and ST(i),ST forms), FSCALE, FPREM, FPREM1, FYL2X, FPATAN. "Larger significand" is read on the binary64 view of the operands (OWNER 8). |
| `nd.fbld.img` (BitVec80) | FBLD of a packed BCD with a digit above 9 (AH..FH, the BCD indefinite included): the 80-bit image pushed | PDF 1020 area (2A:FBLD Description): "The packed BCD digits are assumed to be in the range 0 through 9; the instruction does not check for invalid digits (AH through FH). Attempting to load an invalid encoding produces an undefined result." Any image is admissible; the flags and the push are those the SDM gives (C1 = overflow; C0, C2, C3 undefined). There is no precondition. |

Rule: no other undecided point is encoded as a choice. A point the SDM decides, or that OWNER 8 fixes (IEEE values,
see x87spec.py header), has one value in the spec. Where the SDM marks a flag "Undefined" (C0/C2/C3 after
FCMOVcc, C0/C3 after FPTAN, ...), the spec marks it in `Out.undef`; a Form may set any value, and the lifter's
consumer must not read it. Checked by `tests/test_x87spec.py` (class Nondeterminism).

## x87 precondition facts (x87spec.py)

One precondition remains in x87spec.py, `ou_pre` of `finish`: OE and UE are masked in FCW when an arithmetic
instruction raises them. Without it the SDM 1:8.5.4/8.5.5 result (the biased, wrapped significand) would be needed;
it is not representable in the binary64 arithmetic of OWNER 8. It is not assumed. It is the L3a fact
`x87-masked-sites` (CLAUDE.md, "Hardware exceptions in lifted code": "It is a fact that every fldcw, ldmxcsr,
fldenv, frstor and fxrstor site keeps the exceptions masked. A site the facts cannot decide is reported to the
orchestrator."). The fact gives FCW.OM = FCW.UM = 1 at every x87 instruction of the exe, since FCW starts masked
(SDM 1:8.1.5, FINIT) and only those sites write it. A certificate that uses `ou_pre` names `x87-masked-sites` in the K
of its record; until L3a publishes the fact, the K is not closed and the form gate does not admit the shape.

## SIMD refinement (simdspec.py, simdcheck.py)

The pinned Sail model has no SIMD semantics (every 0F SSE/MMX opcode is unimplemented), so a SIMD shape is certified against `simdspec.py`, the way x87 is
against `x87spec.py` (OWNER 10 for the parts no pinned formal model covers; the SDM text and the ACL2 x86isa model are the sources).
- Source of a row: the pinned ACL2 `acl2/acl2` 3ccb342c, `books/projects/x86isa/machine/instructions`, file and line recorded in `acl2_cites.tsv`
  (`acl2_cite_check.py DIR` resolves every cite and fails on a stale or missing one). A row ACL2 lacks is an SDM row with the quote, as in x87spec.py.
- Obligation (per encoding shape, `simdcheck.py`): the symbolic Form's z3 post-state (xmm0-7, fr0-7, ftop, ftag, mxcsr, the eight general registers, memory,
  the fault kind) equals the spec's on every pre-state and every displacement/immediate value, under the declared environment. unsat is the proof; the
  result classes are proved | FAILED (counter-model) | no-row | no-form | timeout.
- Dependences stated, not hidden: the effective address is the Form's own (`forms.ea` of the same adapter operand): it is the integer path's certified fact.
  The environment is `cr0_em = 0, cr0_ts = 0, cr4_osfxsr = 1` (Res.env): the rows of L5's x86sys (the user-mode machine); the spec reads them as state
  variables, never as a constant.
- A misaligned 16-byte memory operand of an aligned form is the access violation (SDM 2A 'Exceptions Type 1/4': #GP(0), reported by Windows as the AV code of the
  fault table); the rule per row is `al` (ACL2 `inst-ac?`). QEMU/unicorn does not fault on it, so the emulator is not the reference for that cell.
- The MMX rule is x87spec's (`mmx_exec_effect`, `mmx_write_effect`: TOP := 0, tags valid, a written mmN gets 0xFFFF in bits 79..64, SDM 1:9.6.2). QEMU leaves
  bits 79..64 of the destination alone, so the emulator test compares the 64 data bits only; the exponent bits are checked against the SDM rule.

## Pinned IR 7c307464: hunks

New pin: x86_proof.ir sha256 7c3074644653d2976cbb101ae993e94f2a09c9b7a3b574d3c74f6a80278d079c (BUMP now defaults to 1 in farm/sail_build.sh; BUMP=0 rebuilds the superseded IR).
All hunks are transcriptions of ACL2 master (sail_pins.txt row acl2) (ASM-009, OWNER 9) over the ACL2 8.5 translation; each patch file cites ACL2 file:line in its own header.

| patch (sail_patches/bump/) | ACL2 master source | change |
|---|---|---|
| ror_cf.sail | rotates-spec.lisp:662-665, :709-717 | ror: masked count 0 affects/undefines no flag; CF = MSB of the result |
| rol_count0.sail | rotates-spec.lisp:285-288 | rol: masked count 0 affects/undefines no flag (was: OF undefined) |
| sar_bigcount.sail | shifts-spec.lisp:797-852 | sar, count >= size: CF = MSB of the destination, not undefined |
| cmps.sail | string.lisp:312-560 (:383-386, :397-433, :443, :465-471) | x86_cmps operand order (src1=[rSI], src2=[rDI]) and rep termination |
| cpuid.sail + cpuid_dispatch.py | cpuid.lisp:28-106; inst-listing.lisp:9985-9987 | 0F A2 implemented; eax..edx are declared outputs (rt/cpumodel.c) |
| bit_bsf_bsr.sail + bsf_bsr_dispatch.py | bit.lisp:715-723, 755-843, 870-954; inst-listing.lisp:10314-10325 | 0F BC/0F BD implemented (closed forms z3-checked against the ACL2 recursions: tests/test_bsfbsr_closed_form.py); source 0 leaves the destination, ZF=(src=0), other flags undefined |
| bsf_bsr_dispatch.py (F3 arms) | Intel SDM Vol. 2B, TZCNT and LZCNT pages ("executed as BSF/BSR") | F3 0F BC / F3 0F BD: BMI1 (CPUID.07H.0:EBX[3]) and LZCNT (CPUID.80000001H:ECX[5]) are READ from the declared CPU (lift/rt/cpumodel.c, leaf above the max leaf = absent); both clear, so the arms go to x86_bsf / x86_bsr (a set bit leaves the arm unimplemented) |

Proved on the new IR (shapeprove, SAIL_IR): 0fbc, 0fbd, f30fbc (reg, mem), f30fbd (reg, mem), 66f30fbc: 7 of 7 proved. DSL: forms_alu.py handler bs also covers tzcnt/lzcnt (same F3 rule).
Changed-closure counts follow from certs_build once the v2 farm records land.

Published IR: fnv-nx-build branch tools/sail-ir-7c3074644653 (ir/x86_proof.ir, MANIFEST.json with ir_sha256 and tree_sha256).
