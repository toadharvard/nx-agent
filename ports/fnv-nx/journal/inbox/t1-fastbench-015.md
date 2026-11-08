### J-XXXX | 2026-11-08 | stream: T1-fastbench | status: wip | parent: J-0156

* **7bc00ef, the fuzz cache's quadratic step.** fuzzcache's todo, status (recheck_due), view, merge and seed
  globbed div/<addr>-*.json once per function, and each glob scans the whole directory. On 29k rows with 60k
  divergence files that is about 1,000 s (synthetic, measured here). It fits qk1's 689 s fuzz setup with nothing
  fuzzed and much of its 1,324 s report (view + status). divs_index() lists div/ once and groups the files by
  address: the same paths in the same order (checked on tricky names), 0.07 s. tests/s13/test_fuzzcache.py and
  test_d14.py pass.
* **dcd96ff, prepare reuse.** The record also holds the decomp's head and the recon id, which reach prepare.py
  through s1_compile's `git rev-parse HEAD` subprocess that the trace cannot see; both must be unchanged. The rules
  read only FNVNX_PREPARE_SKIP and FNVNX_RECON from the environment, and the record covers both.
* the zero job: stopped here at 07:58 (a tracked task's 2 h limit). The orchestrator is asked to give it a farm.
* expected quick on a head like aec30808, with all of today's fixes: equiv left to eqa when the shim headers
  change (else warm), the build with prepare kept and the link warm, fuzz setup in seconds plus 420 s of fuzzing,
  the attribution in 1 s, scenario at most about 2 x 600 s. The next qk run's report has the seconds per step.
