# recon2-006 | 2026-11-07 | stream: R2 (recon fleet, D23) | status: active | parent: recon2-005

* **fuzz queue** (orchestrator (b)): fnv-nx-build `recon-notes/fuzz-queue` (f9233d5b), 81 rows for EQ's recon_fuzz job.
  - 49 inline-confirm (D23 (c): the containing exe functions to fuzz with the native body linked).
  - 24 R3 pending-fuzz bodies (14 no-exe-copy, 10 blocked).
  - 8 exe-only or not simple (R2's NVAPI loader 0x009fea2a and S5540 _Ucopy; R6's atexit thunks 0x00fdad30 /
    0x00fdae10 / 0x00fdb140, LTCG 0x00c8b4f0, and 2 inline-only destructors).
  - Columns: source branch, name, kind, exe addresses, convention, seed hints, note. The generator fuzzqueue.py is in
    the branch.
* **shard 2**: nothing left to split. R3 published all 16 recon units; blk_00ec0000_od closed through crt_symbols.tsv.
* **R3's declaration class** (orchestrator: REC1-REPL), 3 of 6 closed, in recon/speedtree_indexedgeometry_ob1_variant
  (unit 16/16):
  - REC1-REPL gives the unit's `_Unchecked_move_backward` declaration VC9's returning form. <unsigned*,unsigned*> is
    byte-equal to the exe copy 0x00b16a60.
  - The <vector<unsigned short>*> and <vector<unsigned int>*> instantiations are REPL'd explicit specializations,
    declared only. They bind to the byte-matched 0x00b35ce0 / 0x00b35da0 (same shape: cdecl, pointer result, three
    pointers).
  - Back to S2-bodies-2 as link shape, 3 rows:
    - P133List::InsertAt: a REPL to the returning form grows 0x00b40c80's frame by 8. The void name vs 0x00b156b0's
      8-byte iterator result is a shape pairing.
    - endIter: by value vs 0x0065f550's explicit result pointer (exe-sret).
    - initializeProxy: a void member vs a placed constructor.
* **R3's code-defmissing** (5): recon/r3_hkpgsk adds a GCC-only `extern "C" __fnv_00da8700` forwarding to the
  decomp's byte-matched static FUN_00da8700. Dropping `static` changes MSVC's code (202 bytes: private convention).
  - 0x0062c250 (NiTransform::operator*) and 0x006b9760 (BSSimpleArray ctor): inline in *_h_unit; S2-bodies' addr-host
    closes them (-fkeep-inline-functions).
  - 0x005effb0 and 0x00cd28b0: MSVC this-adjustor thunks; S2-bodies (needs the GCC-layout adjustment).
* **D23 list** (orchestrator (3)): mklist pipeline ready, dry run on link/0540aff0d154 (19:31 commit, before the
  recons).
  - Steps: link branch, the nx-agent commit from meta.json, fetch_recon of every recon/*, prepare.py, trap names read
    from the guest ELF, bodycause.py --traps --counts, per-shard counts.
  - Guest trap names: function stubs are code branching to __fnvnx_undef_trap (mktraps.py); data stubs are 4096-byte
    .bss objects. The guest's undefined.txt is not published with the link.
  - The newest-head list follows when full1 publishes it.
* **not checked here**: none of R2's GCC sides compile in this container (no AArch64 toolchain); farms / V1 confirm.
