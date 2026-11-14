# certs.tsv: the format and the validity rules of a form certificate

A form certificate says: the symbolic Form that the current DSL builds for one encoding shape (proof/shape.py key) was proved equal to the pinned x86 model.
`certs.tsv` is the file gate `forms` (proof/formgate.py) reads; `proof/certs_build.py` writes it from the records of the shape-prover shards.

## Row format (11 tab-separated columns, a `#` line is a comment)
`shape_key  family  dsl_sha  model_sha  reference  result  obligations  undef_flags  def0_witness  def0_free  nonflag_free`
- `shape_key`: the key of proof/shape.py (`<bytes with immediate and displacement bytes zeroed>/<imm_offset>.<imm_size>.<disp_offset>.<disp_size>`).
- `dsl_sha`: sha256 of the SYMBOLIC Form (fields are variables) that the current DSL builds for the shape.
- `model_sha`: sha256 of the pinned proof IR (`x86_proof.ir` in proof/sail_pins.txt).
- `reference`: `sail`. `result`: `proved`.
- `obligations`: the obligations the proof discharged, comma separated (the DEF0 obligation among them); `undef_flags`, `def0_witness`, `def0_free`, `nonflag_free`: the
  DEF0 columns, `-` when empty.

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
4. Validity. A record is valid iff model_sha == the pinned IR, or recomputing K over the record's model_fns on the pinned IR gives the record's closure_sha. The IR file must hash to the pin. Otherwise the record is REFUSED.
5. Test obligations:
   - an edit in an executed function changes K;
   - an edit in an uncalled function keeps K;
   - a position-only change keeps K;
   - a change to E changes K;
   - an IR that does not match the pin is REFUSED.
