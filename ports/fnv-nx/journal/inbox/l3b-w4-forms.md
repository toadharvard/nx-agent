# l3b w4: per-form certificate keys, 0x67, push/pop r16, bound, mirror run

Branch claude/fnv-l3b-lift-w4. Commits: 18a2e8b5 (cert key + farm queue), 86c3d193 (0x67), 7b2dd605 (push/pop 16, bound), then two farm-script commits
(empty queue; python dependencies). Raw prover records: fnv-nx-build branch `l3b-lift/w4-results` @054c686.

## 1. Certificate key (18a2e8b5)
Rule: a record is a certificate of a shape iff (shape key, symform.shape_sha(key) of the CURRENT DSL, IR sha256) all match and result == proved.
No run id, no DSL source hash. certs_build.py ignores non-matching records (stale-dsl / stale-model counted, never refused; contradictions proved vs FAILED
on the same current triple still refuse). `certs_build.py --queue SHAPES --shards ...` lists the int shapes with a Form and no valid certificate; the farm
(farm_shapeprove.sh) fetches all published shards under the prefix, proves only the queue, branch = <IR id>-<queue id>/<K>of<N>; empty queue = exit 0.
formgate.py already compared per encoding with symform.shape_sha; unchanged.
Tests: test_certs_build (stale ignored; DSL edit re-queues exactly the changed forms; failed/timeout stay queued), test_formgate
(test_certificate_stale_after_a_dsl_edit_is_refused). 59 tests in proof/tests run; 1 error = test_fpexact, `elftools` missing in this container.
Real shapes (l3b-lift/shapes @7752455, 26,500 shapes): 19,416 int shapes had a Form before my forms; queue of the empty-shard state now 19,494.
Editing ONE mnemonic's builder changes shape_sha of: xor 598, add 2,660, mov 4,746, push 185 shapes (measured by an edit that adds an undefined flag);
every other certificate stays valid.

## 2. Prefix 0x67: MODELLED (86c3d193)
fnvlift translates 0x67 (core.py ea, f_string.py, f_sys.py), so the DSL models it. forms.py `ea`: 16-bit sum base+index*scale+disp mod 2^16, zero-extended,
segment base added after (SDM Vol.2 2.1.5 Table 2-1, Vol.1 3.7.5). New `AReg` for SI/DI/CX/BX of string, loop, jcxz, xlat (low word written, upper half kept).
Prover on all 295 int shapes with 0x67 that have a Form: proved 270, model-gap 14, FAILED(dsl) 11.
FAILED(dsl) 11 (all three shards, records in l3b-lift/w4-results):
 * CORRECTION (w4, after the first report): the push/pop failures are NOT a model stack-size defect. `6751` (push ecx, 0x67) proves; `6451`, `6551`, `656751`, `676451`
   fail, i.e. exactly the encodings with a 0x64/0x65 byte. Cause: the prover DRIVER (model_sail._post / run_path) added a free fs_base / gs_base to EVERY memory access of an
   instruction that carries a 0x64/0x65 byte (isla can initialise seg_hidden_bases with one value for all six segments, not per segment), including the implicit SS stack
   access and ES:EDI. Fixed as a class, in the model: sail_patches/segment_bases.sail (FS / GS base = two symbolic registers, each access carries its own segment's base) and the
   driver's blanket addition and prove.py's "forced to 0" workaround are deleted. The 2 segment-prefix cases (6765206400, 67652e0a00) are the same cause.

## 3. push/pop 16-bit, bound (7b2dd605)
forms_move push/pop: width 16 under 0x66 (ESP+-2, 16-bit store/load, reg write keeps the upper half). Of the 8 int shapes with push/pop + 0x66: proved 6;
the 2 FAILED(dsl) are those that also carry 0x67 (see above).
bound: forms_flow `bound`: fault code 4 (#BR) = slt(index, lower) | slt(upper, index), state unchanged; f_dsl.py maps code 4 to FNVL_BOUNDS as a fault (trap=False,
same as the hand handler f_sys.h_bound). 75 int shapes got a Form; prover: all 75 model-gap, "Opcode Unimplemented in x86isa!" (the Sail model does not
implement BOUND). So bound stays uncertified by the model: needs another reference (SDM table, a host test) or a model extension: L3b decision.
Int shapes with a Form: 19,416 -> 19,494 (+78: bound 75, pop r16 3). Still no builder (int family, counted at the time of the run): arpl 157, sal 43, les 42, lds 23, daa/das/aas/aaa/aad/aam, salc,
popaw/pushaw, retf, lcall/ljmp, mov sreg, enter (symbolic branch).

## 4. :NON-WRITABLE-SEGMENT (model clause, not patched)
sail-x86-from-acl2 model/memory_accessors.sail, `store_bytes_to_ea`, lines 100-103:
`if not_bool(in_64bit_mode(proc_mode)) & (seg_reg == 1 | seg_attrs[w] == 0b0) then x86_fault(":NON-WRITABLE-SEGMENT")`.
seg_reg == 1 is CS: a store through a CS override (0x2e, or last-wins 2e) always faults in the model (matches hardware: CS is not writable, #GP).
model_sail.FAULT_OK = {:SEGMENT-LIMIT-FAIL, :OUT-OF-SEGMENT-INSTRUCTION-POINTER} lacks it, so those shapes are model-gap. DSL and lifter (core.ea) ignore the CS
override and store: a lifter gap for the CS-store class (Windows: STATUS_ACCESS_VIOLATION). In this run: 10 of the 295 0x67 shapes, 1 of 40 farm shapes.
The rule that closes the class is a DSL fault for any store with seg == cs (needs a #GP fault code and the lifter's raise), not an exemption of the model fault.

## 5. Mirror run (the path the farms use)
Clean /home/user/l3work, toolchain built from tools/sail-mirror @33c9badb (verified head), `PUBLISH=0 LIMIT=40 bash farm/farm_shapeprove.sh 1 400`.
 * Machine: 4 cores, 15 GB RAM (free 15 GB idle), 252 GB disk.
 * Toolchain from the mirror (apt, opam switch, isla cargo, IR): about 25 min wall (opam 422 s). IR sha256 equalled the pin (the script's check passed).
 * First attempt FAILED after the toolchain: prove.py -> enc_table.py imports `keystone`; the mirror path installs neither capstone nor keystone. Fixed in
   farm_shapeprove.sh (pip install capstone==5.0.9 keystone-engine==0.9.2, versions as measured, from PyPI, not hash-pinned; mirror them if the farm has no PyPI).
 * Second run (toolchain warm): shard 1/400 of the 19,494-shape queue, 40 shapes: proved 39, model-gap 1 (2e006900: :NON-WRITABLE-SEGMENT), wall 446 s
   including ~17 s of queue computation = 11.1 s/encoding (the handoff estimate 11.5 s held).
 * Targeted run (same prover, 3 processes in parallel on the 4 cores, 373 shapes, 0x67 + bound + push/pop16): shards proved 93+91+92 = 276, FAILED(dsl) 11,
   model-gap 86; wall time of that run was not timed separately (the three shards finished within the monitor window, ~12 s/encoding/process by the 40-shape rate).
 * Whole queue estimate: 19,494 x 11.1 s = 60 CPU-hours (before re-runs of timeouts). Memory: each process runs under ulimit -v 6,000,000 KB; I did not measure
   the RSS peak, so shards-in-parallel within VMEM_KB = 6 GB per process: 15 GB / 6 GB = 2 by address-space budget, 3 ran side by side here without trouble
   (4 cores bound it at 4). That is a bound by arithmetic, not by a measurement of peak RSS.
Not done: no publication run (PUBLISH=1) was made; no certs.tsv was rebuilt from these records (certs_build --shards on them is the next step; the
formgate pin of certs.tsv must be updated then).

## 6. The shape queue: scope and superset argument (w4, f46bf37d + this note)
Source: fnv-nx-build branch l3b-lift/shapes @77524556d0ce5d10f22451870350caa2f66dd724. KEYS.tsv pins, per module, image_sha256, ranges_sha256, tool_sha256 (= sha256(proof/shape.py + proof/census.py))
and the sha256 of its shapes.tsv; farm_shapeprove.sh refuses a mismatch. Modules (7 images): FalloutNV, atimgpud, binkw32, d3dx9_31, d3dx9_38, libvorbis, libvorbisfile. Each shapes.tsv = proof/census.py
output: ONE row per shape key = proof/shape.py shape_key(instruction) over EVERY instruction of the recursive code ranges (ranges.tsv of the module facts), each distinct range decoded from its start.
Union of the 7 modules (distinct shape keys, global): int 19 817, x87 2 175, simd 4 369, sys 139 = 26 500.
Queue of farm_shapeprove.sh = the INT family only (shapeprove.py: "Integer family only (shape.family == 'int'); the other families have their own provers") that has a DSL Form and no valid cert:
19 494 of the 19 817 int shapes at this commit (the other 323 have no builder yet or a builder precondition: no-form / builder-branches-on-field / field-decode, printed per class by shapeprove). x87 (2 175),
simd (4 369) and sys (139) shapes are NOT in this queue: x87/simd have no run of the x86isa integer model (their own provers / the FP exactness gate), sys has the forms_sys builders of stage 2. Nothing of them is certified by the 19 494 run.
Superset argument: every instruction the lifter emits is decoded from the same code ranges of the same images (the lifter's input is the ranges, the census decodes the ranges); a shape key is a function of the
instruction's bytes (prefix, opcode, ModRM, SIB bytes kept; immediate, displacement and relative-target bytes masked), so the shape of every emitted instruction is a row of that module's shapes.tsv, hence in the
union, by construction. It holds only while (1) the lifter's ranges = the census ranges (same ranges_sha256), (2) every range decodes: FalloutNV has `# undecodable 00e7c100-00e7c1d9 stopped_at=00e7c11c`, i.e. the
shapes of instructions after 00e7c11c in that range are NOT in the universe (census exit status 1) - the ranges owner must resolve it or exclude the range from the lifter; I did not check whether the lift pack
ranges contain it; (3) 'the family is a function of the shape' (census.py docstring Assumption, checked per shape by re-decoding the representative, not proved; no ASM id for it yet).
Not certifiable yet (not failed): 75 bound shapes + 67ac (lodsb with 0x67) = model-gap 'Opcode Unimplemented in x86isa!' (model fact, case (b)); 671e (push ds) FAILED(dsl) = the sreg operand has no source variable (stage 2).

## 7. BOUND census (w4; measured at l3b-lift/shapes @77524556, shape rows of proof/census.py; global over the 7 images)
BOUND (opcode 62, ModRM mod != 11, form_key bound|r32,m64|62, + prefixed variants): 75 distinct shapes, 1 272 sites (an instruction address counted once per shape). Per module: FalloutNV 12 shapes / 30 sites,
d3dx9_31 42 / 312, d3dx9_38 66 / 930 (shapes are shared between modules, so 75 is the union, not the sum), atimgpud, binkw32, libvorbis, libvorbisfile 0 / 0. Largest: 626500 (be\0) 263 sites d3dx9_38 / 102 d3dx9_31;
626100 75 / 12; 626900 90 / 28; 626c6500 111 / 44; 6200 46 (d3dx9_38), 18 (FalloutNV). Indicator ONLY (not a rule): 105 of the 117 module-shape rows (FalloutNV 5 of 12, d3dx9_31 39 of 42, d3dx9_38 61 of 66) are made of printable-ASCII or
NUL bytes only (62 65 00 = "be\0", 62 6c 65 00 = "ble\0"): the byte pattern of text decoded as code inside the census ranges. Reachability of each site is NOT decided here: that is the closure / call-graph rule's (sites per module are
in the census rows: first_address is in shapes.tsv, the full site list needs census.py with the facts, which are private). Model fact: x86isa does not implement BOUND ('Opcode Unimplemented in x86isa!'), so no certificate is possible; DSL builder: forms_flow `bound`
(fault kind 4, state unchanged otherwise), certificate-free until the orchestrator decides. Same class: 67ac (lodsb under 16-bit addressing) 1 shape: model 'Opcode Unimplemented'.

## 8. forms_sys intake (w4, from L3a's e9124b30; measured at shapes @77524556)
Taken byte for byte from L3a's commit: fnvlift/dsl.py (uf, swhen, FAULTS 1..11), fnvlift/f_sys.py (trap0d_privileged), proof/forms_sys.py. Hooks applied by hand (same hunks as bab6030): forms.build imports forms_sys,
forms_move.mov routes CRn/DRn to forms_sys.privileged, symform.eip_dependence and normalize carry swhen. Builders: 11 handler functions in forms_sys (arpl, privileged [25 mnemonics + mov cr/dr], int, into, int1, sldt, str, smsw,
sgdt/sidt, cpuid, rdtsc): 11 taken, 0 rewritten; bound stays mine, in forms_flow.
shape_sha over the 19,817 int keys (before = 20422a8d, after = this tree): 19,494 same, 166 error in both, 157 new Form (arpl, keys 63xx), 0 changed. The sys-family shapes (139) are not in the int key list and had no Form before.
Model answer, one representative encoding per class (27 encodings, prove_one on the 70c8dc39 IR): ALL 27 are model-gap. 'Opcode Unimplemented in x86isa!': arpl, int n, into, int1, cli, sti, in, out, ins, cpuid, rdtsc.
'(:GP)' (the model dispatches these as a #GP fault): sldt, str, sgdt, sidt, smsw, mov cr, hlt, lldt/lgdt-class (0f00d0, 0f0130). So nothing in forms_sys is certifiable against the pinned model; the uf nodes
(cpuid, rdtsc) have no model counterpart at all (the model does not implement either), so "uf matched to the model" has no answer: the declared machine's cpuid / platform timer are not a model fact.
Fault kinds 4-11 in the model-faults table (prove.MODEL_FAULT): none. Kinds 4,5,6,8,9,10,11 correspond to no model message; kind 7 = :NON-WRITABLE-SEGMENT (CS store) only; 1 = DE. The Windows reporting of
kinds 2-11 is the platform's (KiTrap0D / Wine 9.0), not the model's. A certificate for them needs another reference: L3b/orchestrator decision (no hand-written model hunk).

## 9. Farm questions (w4, answers)
PUBLISH=0 rerun: PUBLISH=0 proves and writes DIR/shapeprove_K_of_N.jsonl in the work dir, publishes nothing, and does not read the published shards. A later run without PUBLISH=0 publishes the same rows ONLY IF the queue id is
unchanged (the same OUT dir <IR id>-<queue id>, shapeprove.py resumes: rows already in the jsonl are skipped). The queue id is a function of the published shards (a shape with a valid certificate leaves the queue), so as soon as any
farm publishes, a rerun has another id, another OUT dir, and proves again. The rows themselves stay valid (key, shape_sha, IR sha): publish by the farm path or by certs_build --shards of the jsonl, not by id.
Queue id vs KEYS.tsv: the farm script fetches the HEAD of the branch l3b-lift/shapes (not a commit). A re-pinned census pushed to that branch changes the shape union and so the queue id of every farm that starts afterwards.
Rule: the re-pinned census goes to ANOTHER branch (farm_shapeprove.sh now has SHAPES_BRANCH, default l3b-lift/shapes); l3b-lift/shapes @77524556 is never moved.
Selector hunk e2975877: dsl.XIN += sel_es/cs/ss/ds/fs/gs (16 bit, SEG_* order); push sreg Form = 16-bit store (pinned model x86-push-segment-register: 'a 16-bit move, leaving the upper portion of the stack location unmodified'; so the
32-bit slot keeps its upper half, the Form is written from the model); prover: a Form that reads sel_* runs isla without the uniform seg_visibles literal (the six selectors are free, isla prints (_ vec v0..v5), element i = SEG_* i). No IR change
(IR sha 70c8dc39 unchanged, existing certificates untouched). Proved: 06 0e 16 1e 0fa0 0fa8 661e. pop sreg / mov sreg are NOT in it: they write a selector (and, for FS, the base); the Form has no selector output slot (needs an output
in dsl.Form and f_dsl: L3a's side) and the model does not implement them.

## 10. F2, G1, G3 (w4, 2026-11-12)

**F2 (cb32e9e4 on claude/fnv-l3b-lift-w4-l3a, 95089468 on claude/fnv-l3b-lift-w4).** Every Form memory access goes through one of the
address functions of forms.py: `ea` (explicit operand: effective segment = last prefix, a16), `addoff`, `ssa` (stack), `esa` (string destination,
ES flat), `dsa` (string source, XLAT: DS default, override gives the fs/gs base). `mload`/`mstore` refuse an address made by none of them;
tests/test_forms_mem.py also checks by AST that no forms_*.py except forms.py calls `load` or `.store`. shape_sha over all 19,817 int keys:
77 changed (bound 72, arpl 2, fs/gs string/xlat 3), 19,740 identical.

**G3 (cmpsb a6, cmpsd a7).** Class: operand order of the model. ACL2 8.5 `x86-cmps` calls `(gpr-arith/logic-spec size OP-SUB dst src)` with
dst = ES:EDI, so the flags are those of [edi] - [esi]; master (string.lisp:443) calls it with src1 src2 = [esi] - [edi], the SDM order and ours.
Swapping the Form proves both, so the Form is right. Fix class: a transcription patch of the master hunk (ASM-009), IR-changing.

**G1 (rol|m32,imm|c1, rol|m8,r8|d2, sar|m8,r8|d2 x2, sar|m8,imm|c0 x2).** Not memory-specific: the register forms of the same encodings give the same
FAILED(model-defect) (c1c000, d2c0, c0c000 reproduce it). The disagreement lies in the declared regions of defects.py: `rot-of-count0` (rol/ror at masked count 0:
the 8.5 model leaves OF undefined, the SDM and the Form leave all flags unchanged) and `sar-cf-big-count` (CF at count >= size). Master fixes rol/ror
(rotates-spec.lisp:284-288 and :660-664, count 0 -> input flags). So the class is a model defect whose fix is a transcription patch of master's
rotates-spec.lisp; the Form is not touched. These shapes carry FAILED(model-defect), not a certificate, until that patch is in the IR.

## 11. (D) The ACL2 table of the model-gap classes (w4, read-only; no patch)

Sources: ACL2 8.5 (acl2-85 checkout, the line numbers of the pinned model's family) and master 3ccb342c. Column "dispatch" = the function in
machine/inst-listing.lisp (NIL = Opcode Unimplemented, which is what the pinned IR returns for every row below).

| class | opcodes | 8.5 | master dispatch (inst-listing.lisp) | master defun (machine/instructions/...) | modes | descriptor load |
|---|---|---|---|---|---|---|
| pop sreg | 17, 1f, 07, 0f a1, 0f a9 | NIL (the defun is commented out, push-and-pop.lisp:617) | x86-pop-segment-register (:171, :214) | push-and-pop.lisp:581 | 32-bit (I64 = legacy) and 64 | yes: `get-segment-descriptor` (:642) then `load-segment-reg` (:670) |
| les/lds/lfs/lgs/lss | c4, c5, 0f b2, b4, b5 | NIL | x86-load-far-pointer (:1417, :1426) | segmentation.lisp:693 | legacy | see its body (segment load) |
| mov to/from sreg | 8c, 8e | NIL | x86-mov-Op/En-MR / -RM (:1055, :1064) | data-transfer family | all | to be read: the shared mov defuns |
| far jmp indirect | ff /5 | NIL | x86-far-jmp-Op/En-D | jump-and-loop.lisp:375 (8.5: :390) | all | yes |
| far jmp direct ea, far call 9a, far call ff /3 | ea, 9a, ff /3 | NIL | NIL (also in master: `'NIL`) | none | - | - |
| retf | ca, cb | NIL | cb: x86-ret (:1481); ca: NIL | subroutine.lisp:337 | all | not for far (only the near path) |
| enter | c8 | NIL | x86-enter (:1469) | subroutine.lisp:704 | all | no |
| aaa aas aam aad daa das | 37, 3f, d4, d5, 27, 2f | NIL | x86-aaa/aas/aam/aad/daa/das (:326, :363, :1725, :1734, :252, :289) | bcd.lisp:53, :95, :141, :203, :262, :312 | legacy (I64) | no |
| salc | d6 | #UD | not in the table | - | - | - |
| system class (sldt, str, sgdt, sidt, smsw, mov cr, hlt, lgdt, lldt) | 0f 00, 0f 01, 0f 20.., f4 | `(:GP)` in this build | segmentation.lisp / system-level: sgdt :239, lgdt :123, lldt :448, ltr :56, lidt :331 | | | |

Consequences: enter and the six BCD instructions are defined in master and take no descriptors: each is a transcription candidate like `segment_bases`
(covered by ASM-009), each an IR-changing hunk. pop sreg and les/lds are defined in master only with the descriptor load (GDT read, hidden base) -- the
model's flat-segment setting (A-FLAT) would need the same selectors-as-inputs treatment as push sreg; far call, ljmp ea, retf ca, salc are undefined in
master too and stay escalated. mov sreg needs a read of the master mov defuns (not done). Not done in this note: mode coverage of the system rows and the
exact master lines of load-far-pointer's descriptor path.

## 12. (K) Design note: key a certificate by the closure of the IR, not the whole IR sha (w4)

Today the certificate key is (shape key, shape_sha, IR sha256). Any model hunk (cmps order, rol count 0, ...) changes the IR sha and invalidates all
~19,500 certificates, although a hunk in `x86-cmps` cannot change the semantics of `add`. The proposal: replace the IR sha by `closure_sha(enc)`,
computed from the IR text only (no isla traces), and keep the IR sha as a coarse field of the record.

**Rule.** For an encoding (its bytes, with prefix and modrm/sib structure fixed by the shape key):
1. Parse the pinned IR (the Sail-to-IR output) into its top-level items: function definitions, `val` declarations, enum/struct/union types,
   register declarations, let-bound constants.
2. The dispatcher (`x86_fetch_decode_execute` and the opcode tables generated from inst-listing.lisp) is a decision tree over the opcode bytes. Fold it
   on the encoding's literal prefix/opcode/modrm.reg bytes (the bytes the shape key fixes; the shape's field bytes stay symbolic and are never
   inspected by the dispatcher by construction of the shape key, which is why they are fields). The result is the set `E` of execute-function entries
   reachable under the encoding. This is constant folding on the IR dispatcher, not an execution.
3. `C(enc)` = the least set containing `E`, the decode functions the folded path calls (prefix, modrm, sib, effective address, operand-size selection,
   segment selection), closed under: calls (the IR has no indirect calls), reads of registers and globals (named registers; global lets), type definitions
   used, and the undefined-value / flag helpers they call.
4. `closure_sha(enc)` = sha256 of the concatenation, in name order, of the normalized text of every member of `C(enc)` (source line numbers and comments
   stripped: the IR embeds locations, which a splice that keeps line counts would otherwise not hide) plus the model configuration that is in every key
   today: isla version pin, the `-C` config, the `-I` initial constraints, `SEG`/`SEG_FREE`, `FAULT_OK`, segment_bases splice contents.
5. The certificate key becomes (shape key, shape_sha, closure_sha). A hunk invalidates exactly the shapes whose `C(enc)` contains a changed definition.

**Soundness (why nothing outside `C(enc)` can change the result).** The model post-state of the encoding is a function of the IR program
restricted to what the dispatcher folds to and everything that program can reach. In Sail/IR there are four channels from one function to another:
(a) calls: static names, so reachability is complete; (b) registers: a function reads or writes only registers it names, and a name is a static token,
so the read/write set of `C(enc)` is the union of its members' names; a register changed by another function only matters through a write that is
itself in `C(enc)`, or through the initial state, which is the configuration hashed above; (c) global lets and types: referenced by name; (d) the
undefined-flag oracle: in isla `undefined` is a fresh symbolic value per occurrence, so it is a property of the function text, not of other functions;
the `model_terms_sha` already covers the isla side. Decoder tables: the opcode tables are part of the dispatcher, which step 2 folds, so a change in the
table entry of another opcode changes the dispatcher text but not the folded result for this encoding. The claim needs the folded result, not the
dispatcher text, in the key; the key contains `E` and the decode functions the fold touches, and not the whole dispatcher.

**What would break it.**
- If step 2 is a heuristic (a regex on names) instead of a constant-folding of the dispatcher semantics: a wrongly small `C` silently keeps stale
  certificates. It must be a fold with an explicit "unfoldable -> keep the whole dispatcher in C(enc)" fallback, which is a rule (over-approximation), not a skip.
- Any indirect or dynamic reference (a name built at run time, a function in a table of values): none in the IR today; a checker must assert that no
  function value escapes (static, from the IR).
- Isla-side changes (version, config, loop unrolling, the SEG splice) that alter the result without changing IR text: they are in the key (step 4).
- Source-position-dependent output: strip locations in the normalization or the key changes on harmless splices.
- Soundness of the rule itself is a proof obligation, so test it as a differential, not a decision: for each real hunk, the set of shapes whose
  isla-trace `model_terms_sha` changed must be a subset of the shapes the closure rule invalidates (the converse is allowed: over-invalidation).
  A shape that changed but whose closure_sha did not is a rule bug and makes the build red.
- A new ASSUMPTIONS id would be needed only if the closure rule is not a theorem about the IR; as above it is a static program-slicing argument over the pinned
  IR, so it adds no assumption beyond ASM-009 (the IR is the model).

Cost: parsing the IR once (the IR sha already needs it), a dispatcher fold per distinct opcode/modrm.reg, and one hash per distinct closure; the closure
shas are shared by the many shapes with one execute function.

## 13. Open queue at the pause (w4, 2026-11-12), in the lead's order
1. G2: imul|r32,m32,imm|69 timeouts (shapes 69050000000000000000, 69247200000000, 690c5600000000; 690000000000 proves with the default solver). Decide by a uniform solver rule, not a per-shape timeout.
2. Census correction: pin = w7's next facts commit, universe from tools/lifted.py (b7b04616), DATA_CLASSES dropped; new branch l3b-lift/shapes-2.
3. (B) selector output slot + pop/mov sreg Forms.
4. (E) pinned queue artifact (QUEUE_REF).
5. (K) closure-keyed certificates (§12), implement.
6. Batched model-hunk IR bump: cmps order (G3), rol/ror count 0 and sar big-count CF (G1), enter + BCD six (D).
Also open: G4 (shard 14/48, 27 keys fewer), (C) reached-site counts.
