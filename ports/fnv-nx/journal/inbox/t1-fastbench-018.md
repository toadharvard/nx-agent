### J-XXXX | 2026-11-08 | stream: T1-fastbench | status: wip | parent: J-0156

* **The regression ratchet: tools/farm/ratchet.py.** The ledger is ledger.json on the artifacts branch
  reports/ratchet (schema fnvnx-ratchet/1). runner.sh checks every report against it before publishing. A report
  with no regression advances the ledger, and the push is never forced: a refused push fetches and checks again.
  RATCHET=0 turns the check off.
  * Metrics:
    * full: trap stubs, trial-link undefined, compile objects, units without an object (by name), WinMain and the
      game milestones (by name), boot initializers skipped;
    * fuzz: passing functions per shard (functions - hard - soft; only when nothing is pending);
    * equiv: identical functions and units per shard;
    * sweep: passing programs and failing programs (by hash) per shard;
    * scenario: each measured D25 target.
  * A regression gives the report "- REGRESSION: <n> items vs <head>" as its first line, plus a REGRESSIONS
    section: each item, its best, the head that first had it good, and `git log` of <good>..<this head>.
  * The ledger does not advance on a check that found a regression, not even for the metrics that improved (owner
    order). `ratchet.py accept --ledger L <metric>` takes an intended regression.
  * A late report of an older head is not compared. A WIP report is compared and changes nothing.
  * zero_targets.py has a regressions column and a total line, read from <reports>/ratchet/ledger.json.
  * Tests: tests/t1/test_ratchet.py (20 checks), tests/t1/test_runner_ratchet.sh (the real runner, local
    repositories).
* **Real reports.** The ratchet replayed in history order over full1's 24 reports (private, checked here and not
  stored). It finds, for example, trap stubs 1,424 at 0540aff0d154 -> 1,843 at d1ed15e74620 and WinMain lost at
  dc5c0627023e. These are real changes, or configuration changes between heads; the orchestrator decides which to
  `accept`. The ledger starts empty on the first farm report after the merge.
* **runner.sh:** a job that exits non-zero keeps its own report under the "FAILED (rc N)" title, above the log
  tail. Before, the log tail replaced it, and boot_trace_job's and scenario_job's "- FAIL:" lines were lost.
