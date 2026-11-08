### J-XXXX | 2026-11-08 | stream: T1-fastbench | status: wip | parent: J-0156

* **e4303b7e, full_job publishes t/<c12>** (the 8 fuzz farms' rc=3, "no prepared tree for link commit
  97deacd38aec"). lib_job.sh publish_tree pushes the pipeline's own prepare/tree in prepare_job's layout to
  t/<c12> only, as a child of `tree` (only the changed files are sent); `tree` itself is not moved. It runs only
  when prepare.py ran from this commit with a clean tree and the stage is ok or warn. It does nothing when t/<c12>
  exists, when FARM_PUBLISH=0, on a WIP build or on an adopted tree. It runs before publish_link. Test:
  tests/t1/test_publish_tree.sh (fetch_tree.sh accepts the result). 97deacd38aec gets no tree until full1 runs a
  head with this code.
* **45970941, boot_trace_job (bt1 0e2a4b160f9e, rc 0 with "guest 0 calls").** `boot guest` begins with
  rm -rf $W/guest, which is where the discovery's guest copy was kept. Phase C therefore booted a deleted guest:
  0 s, no trace, then a FileNotFoundError traceback in compare. The copy now lives in $W/guest-bin.
  * A side with no trace, or a failing compare or seeds, fails the job: exit 1 and "- FAIL:" under the title. The
    reference trace is still published.
  * boot_trace.py compare on a missing trace prints an error line and exits 2.
  * The discovery's SIGTERM is its wall-clock limit (EMU_TIMEOUT 5400; it ran 5,902 s), now a WARN note. The list
    covers only the part of the boot reached in that time; a longer EMU_TIMEOUT or a smaller TRACE_DISC_MAX is the
    lever. Test: tests/t1/test_boot_trace_job.sh (6 of its checks fail on the old job).
* **cfc75add, quick's verdict (qk1 89dcf1c0).** A failed check makes the verdict "FAIL (<checks>)", with "a full
  run is needed too" beside it when the plan says so; its headline reads "FAILED (exit N): ...". When sweep_job's
  setup fails, the report says "- FAIL: setup failed: ..." and "metric: not measured", and the sidecar has
  "error", where it used to say "programs checked 0". Tests: test_quick.sh, tests/t1/test_sweep_fail.sh.
* The zero job no longer runs here (sw14 has it).
* Next: the regression ratchet (tools/farm/ratchet.py).
