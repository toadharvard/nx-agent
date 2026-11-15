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
