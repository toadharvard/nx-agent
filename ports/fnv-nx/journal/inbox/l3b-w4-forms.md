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
 * 9 are push/pop with 0x67: push r32/imm/esp/ds, popal (e.g. 656751 push ecx, 676554 push esp, 6464676800000000 push imm32). Counterexample: model stores at a
   16-bit-wrapped stack address (esp=0: model store differs), the DSL uses esp. SDM Vol.1 6.2.3: the stack address size is SS.B, the 0x67 prefix does not
   change it. So I believe it is a model defect (x86isa uses the address-size attribute for the stack), NOT confirmed on hardware (the native host test
   runs 64-bit; a 32-bit-stack check needs a compat-mode driver). Not patched.
 * 2 with segment-prefix stacks: 6765206400 (and gs:[si+disp8]) and 67652e0a00 (or al, cs:[bx+si], prefixes 65 2e). Not analysed further: the 16-bit
   address paths without a segment prefix all prove, so the suspects are the prefix handling of the model (last-prefix rule / gs base forced to 0).
Open for L3b lead: decide the model-defect route (as for bt) for the push-with-0x67 class.

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
