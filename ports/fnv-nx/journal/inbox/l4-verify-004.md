## J-XXXX L4 VERIFY: state at the owner's stop of 2026-11-10 08:16Z

stream: L4 VERIFY, branch claude/fnv-l4-verify. Clean branch holds this stream up to 69538c17 (L1 green, pure 22/22,
selftests 22/22, 71 s). Not yet merged: 49647458 (cltree_job.sh calls equiv.gen_dir() once before the thread pool)
and this head (WIP reccert_job.sh, this note).

Done since note 003:
- farm/fontstate_job.sh (for L5): reference Wine 9.0 prefix font state, published to fnv-nx-build tools/fonts
  c9e3ccb2c68f (63 font files, 50 .fon, 10/10 registry keys, fc-list 119 lines, fontdump 344 lines; the dump was
  byte-equal on two fresh prefixes).
- farm/cltree_job.sh (for L2): cl objects of the prepared tree per shard + name gate; option CLTREE_OBJS_C12.

Running on farms, unattended (results in fnv-nx-build):
- cl objects, commit 72e1664119aa, FNVNX_PREPARE_SKIP of four rules, shards 1-4 on lg1, s5b1, vd1, lc1 ->
  msvc/prepared/72e1664119aa/<k>of4. Shard 1 failed once after a 16 min prepare on a race in equiv.gen_dir (temporary
  file named by pid, workers are threads); rerun at 49647458. The name gate does not run in these: the GCC objects
  (c/86b7beb2d271, five rules skipped) come from another tree hash. Tree hash of the four-skip tree: 58fbac80301baa90.
- GCC compile shards 1-4, commit 86b7beb2, five rules skipped (objects are not final: no DBUG1), on sdd1-sdd4 ->
  c/86b7beb2d271/<k>of4.
- The game's compiler (toolchain/msvcmodel/build.sh) at L2's 63aa47fa on full1, then test_msvcmodel.py and the five
  plugin tests. The first build (86b7beb2, lc1, 7 min) failed acceptance: include search list check, 2 test failures,
  plugin tests loaded the distribution compiler. The prefix is not mirrored yet (tools/gcc-ilp32-msvcmodel is empty).
- Queued behind those: the D3D9 recording of the original for L6 (d9rec_job.sh bb5e5ec8, D9REC_TRACE=1) on s5b1
  (handheld) and full1 (docked).

Half-done:
- farm/reccert_job.sh: the committed text is a WIP between two designs and is NOT runnable as is. L2's answers
  (63aa47fa): MSVC half = `reccert.py pristine-overlay <decomp> <recon> <out>` + `<out>/tools/build_and_match.py
  --image <exe> --jobs N --out match.json`; `reccert.py stamp <recon> --stats <tree>/prepare_stats.tsv --objects
  objects.tsv --match match.json --report cert.tsv`; PATCH-CERT: prepare with FNVNX_RECON_CERTIFY=1 and
  FNVNX_PATCH_CERTIFY=1, tests/s1b/msvc_check.py --report msvc.jsonl, `reccert.py stamp-patches --stats ... --objects
  ... --msvc msvc.jsonl --out certs.json --report patchcert.tsv`. Both flags and the recon set enter the tree key.
- Not started because L2's "switch" sha does not exist yet: the unified recompile on one sha, recertification, the
  name gate over all units, acceptance c) of the compiler (one shard with the new CXX), the guest runtime build.

next: 1) read the farm reports (cltree x4, sdd x4, compiler, d9rec x2) and pass the numbers to L2 / L6 / L1;
2) finish reccert_job.sh per the lines above and run it on L2's switch sha; 3) on a green compiler acceptance: one
compile shard with CXX=<prefix>, mirror the prefix to tools/gcc-ilp32-msvcmodel, add it to env.json and the build
key; 4) merge 49647458 and the finished job through MERGER.
