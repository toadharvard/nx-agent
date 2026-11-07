### J-XXXX | 2026-11-07 | stream: T1-fastbench | status: wip | parent: J-0156

* **boot/lockstep lane in quick_job** (352b860) - `tools/farm/boottrace_job.sh`, on S5-boot's tests/s5/boot_trace.py:
  - list: list.txt of the newest `trace/<link12>` (boot_trace_job's discovery) or BOOTTRACE_LIST;
  - reference: `boot_trace.py ref` (Wine, 300 s), cached. The key covers TRACE_FORMAT, the tracer's sources
    (tools/lockstep fnvtrace.c, wine_trace.sh, build.sh), the exe, the game data's content key, the list, the
    seconds and Wine. The cache lives in `$FARM_HOME/tracecache/<key>` and on branch `tracecache/<key>`;
  - guest: the stub boot of lane A's build with `--trace-fn @list`;
  - gate: the first divergence must not move earlier than at the last verified head on the same key. This is a
    no-regression gate, because the two sides do not share one ini profile yet (S5's next harness item).
* **lane D** (same commit): threads, frames and boottrace only need the build. They now start when lane A's build
  ends and run beside fuzz and audit. Lane A was build + threads + frames + fuzz + audit in series, so this removes
  360 s from the critical lane at the default budgets.
* **tests** - `tests/t1/test_boottrace.sh` (9 cases: reference computed once, published, reused locally and by a
  fresh farm; FAIL when the divergence moves earlier; a tracer change makes a new key). test_quick.sh checks lane
  D's order. All of tests/t1 OK. Integration and recon4 are merged into this branch.
* **state** - qk1 runs its first quick_job (head 2e000047, full setup). There is no qk report yet, so the metric
  is not measured. No `trace/*` list is published yet, so the boot lane reports "no boot list" until S5's
  boot_trace_job runs once (`start.sh bt1 boot_trace`).
