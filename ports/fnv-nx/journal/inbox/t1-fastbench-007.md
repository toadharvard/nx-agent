### J-XXXX | 2026-11-08 | stream: T1-fastbench | status: wip | parent: J-0156

* **metric** - qk1 on 78435e5a, cold (no verified base): 175.8 min, FAIL. equiv ran cold for 10,549 s. prepare
  failed (REC1-REPL, which went to the orchestrator as a blocker). The build check still passed, and fuzz then used
  a stale link. c0057af fixes that check (below). The first incremental number (a verified base and warm caches)
  is still pending, on qk1's next head.
* **full_job report always renders** (bc2c503). try1of1's report was lost to `--since`, which the installed
  full_report.py does not accept. Step 5 now uses `$HERE/full_report.py`, drops any option the tool rejects and
  reruns. If the tool still fails, it writes a minimal report with the stage table.
* **WIP_ONLY=<regex>** (ea64239). A try farm builds only the wip/* branches that match. The report title carries
  `(wip/<b> @ <c12>)`.
* **build verdict** (c0057af). full_job exits 0 once its report is written, so quick_job now also reads the
  prepare, compile, link and guest stage.json and the report's provenance line. If any of these failed, build.rc is
  2, build.why gives the reason, and fuzz, audit and lane D are skipped with the reason "the build failed".
* **compile.sh**, three fixes from try1of1 and full1:
  - 3a795a1: a failed unit always says why (exit N, signal, timeout, exec failure, exit 0 without an object,
    never compiled, or compiled and then lost its object). compile_one always returns 0, so one unit can no longer
    stop xargs. One compile.sh runs per object directory (flock on `$OUT/.compile.lock`, COMPILE_LOCK_WAIT).
  - 5a20649: when the flags stamp changes, every object is deleted. try1of1 asked for 493 units and lost the
    other 2,485. Every unit that had an object is now added to the work list.
  - 6ca280e: 5a20649's loop `[ -f obj ] && echo` returns 1 when the last unit has no object. Under pipefail and
    set -e, compile.sh then exited 1 with no message right after "order keys" (full1 on c46cd30231d9). The check is
    now an if/fi. An ERR trap prints `compile.sh: line N: exit rc` for any future set -e failure.
    test_compile_reasons.sh runs the block under `set -euo pipefail`: rc 1 on the old code, 0 now.
  - Lesson for any shell in the pipeline: never end a loop body or a function with `[ ] && cmd` under set -e
    with pipefail; use if/fi.
* **zero job** (hosted here). It had stopped after 01:06Z, with no runner left after the container restart. It was
  restarted at 04:58Z and is running on c46cd30231d9.
* **next**: the incremental metric on qk1, then shrinking the slowest lane. equiv's cold cost should go once
  equiv/cache/* is seeded.
