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

The SDM leaves five points of x87 behaviour undecided. x87spec.py does not pick a value. Each is a free z3 variable
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

Rule: no other undecided point is encoded as a choice. A point the SDM decides, or that OWNER 8 fixes (IEEE values,
see x87spec.py header), has one value in the spec. Where the SDM marks a flag "Undefined" (C0/C2/C3 after
FCMOVcc, C0/C3 after FPTAN, ...), the spec marks it in `Out.undef`; a Form may set any value, and the lifter's
consumer must not read it. Checked by `tests/test_x87spec.py` (class Nondeterminism).
