### J-XXXX | 2026-11-07 | stream: T1-fastbench | status: wip | parent: J-0156

* **item 2 done: keyed caches for the overhead audit and equivalence** (claude/fnv-t1-fastbench e81a1a2, fe77579)
  - **Audit** (`overhead_audit.py --catalog/--cache/--cache-out`, `audit_job.sh`): a function's row is reused while
    its audit key is unchanged.
    - The key is sha1 of AUDIT_HARNESS, the seeds, the max steps, the llvm-mca version, and the function's fnkeys.py
      key (MSVC code, rewritten A64 code + relocations and those of what it reaches, the difffuzz harness).
    - Cached rows use no deadline time. Setup failures and pending rows are not cached.
    - Every shard reads the union of `audit/cache/*` and writes `audit/cache/<k>of<N>`, so changing N keeps the
      rows.
    - fuzz_job.sh now builds the catalog before the FUZZ_SETUP_ONLY exit (FNVNX_AUDIT_CATALOG); fuzzing is
      unchanged.
  - **Equivalence** (`tools/equiv/unitcache.py`, hooked into `equiv.compile_unit` by FNVNX_EQUIV_CACHE): MSVC
    objects keyed by:
    - the compiler binaries;
    - the command line, with the tree/decomp/port/gen/cmath paths replaced by tokens;
    - the file names on every include path;
    - the content of the source and of every header cl.exe lists with /showIncludes.
    It covers every build variant: head, EH-off and attribution leave-outs. Rows are still fingerprinted from the
    object. Each shard keeps a checkout of `equiv/cache/<k>of<N>` (seeded from all shards for a new N), prunes it
    to the live objects and publishes it when it changed. `EQUIV_CACHE=0` turns the cache off.
* **metric** - per unchanged function / unit: one harness run / one MSVC compile under Wine -> a cache lookup or
  copy. After one cached pass, a ~5-unit change re-measures only its functions and recompiles ~5 units, not
  26 x 90 min of audit and ~500 compiles per equivalence shard. One warm farm is measured with item 3.
* **tests** - `tests/t1/test_audit_cache.py` (stubbed harness: reuse, changed key, deadline),
  `tests/t1/test_equiv_cache.py` (a stand-in msvc.py: hits across tree paths, misses on an included header two
  levels down, variants, a new header on the include path, uncached failures, the compiler in the key); tests/s11
  (run.sh) and tests/s1 OK.
* **farm START** - none of its own. The next run of the existing jobs populates the caches: `start.sh au<k> audit
  <k> 26` and `start.sh eq<k> equiv <k> 6`, as before, after the merge. Their reports show `keyed/cached/measured`
  (audit) and `object cache: ... -> ...` (equivalence).
* **objection (for the orchestrator, not blocking)** - equiv.py belongs to the orchestrator (MACHINES.md). The
  change is in compile_unit only and is off unless FNVNX_EQUIV_CACHE is set.
