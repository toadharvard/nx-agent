### J-XXXX | 2026-11-08 | stream: T1-fastbench | status: wip | parent: J-0156

* **the orchestrator's three tool bugs (each under 30 min)**
  - de584b5, zero table. full_traps counted a full report without an undefined-symbols table as 0 trap stubs,
    also when the guest had not linked (c46cd30231d9: link failed, guest skipped). It is now 0 only when the guest
    stage is ok or warn; otherwise that head measured nothing and the next older head with a value is used. The
    dc5c0627023e run had also read the reports at 06:12, a minute before full1 published 97deacd38aec. The next
    zero heads (b48b056f, 009faa8e) show 222 trap stubs and 674 link errors from it. Test:
    tests/t1/test_zero_traps.py.
  - eb0e00e, scenario. Without a link of its own head the job took link-head, which then pointed at
    1fe1e1463be8, a link whose guest had failed; it now points at a try farm's WIP link. Result: 0/12 in 7 s.
    The job now takes the newest link/<c12> of the head's own history that holds guest/fnvnx.elf (blob-less
    check), never link-head. When the guest runs in no scenario, the report starts with "- FAIL: ...", the
    sidecar carries "error", and the job exits 3. tests/s18 dry job updated to that.
  - aa53e2c, farm boot. BUDGET (instructions per thread slice) defaults to 2e8, S5's local value; it was 6e9,
    and the timeout's SIGTERM is handled only between slices (S5 saw 22 min past the timeout).
* **c0a5146, the fuzz report's attribution**, and 06e0e3e, the equivalence cache's Wine paths: see 010 and the
  orchestrator batch.
* **fuzz setup, measured here** (31971038913d, its link from fnv-nx-build, the catalog built here in 62 s):
  targets 1 s, fnkeys 53 s under the profiler (96k functions hashed). So the 689 s of qk1's setup is in the cache
  step or todo; quick's cache is local (FUZZ_PUBLISH=0, no fuzz/cache/1of1 branch). The setup-step timings from
  279f180 show it on the next qk run.
* **resolve now**: the profile is flat (top function 11 s of 103 under the profiler). What remains for step 2 is
  structural: the per-unit source scans that both resolves repeat (hand_vptr_stores, CDecls, unit_casts; about
  15 s per resolve), then leq1's phase metrics.
