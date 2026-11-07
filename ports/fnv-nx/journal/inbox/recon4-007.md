### J-XXXX | 2026-11-07 | stream: R4 (claude/fnv-recon4) -> zero-targets table | status: wip | parent: recon4-006

* **did**
  - **tools/report/zero_targets.py**: one table, for one head, of D24's 4 and D25's 12 zero targets. It reads the
    newest reports of every lane: `<h12>-<job>.md` and the `.json` sidecars on the reports/<farm> branches.
    - Each row gives the number, the delta against the next older head that has a value, the head the number comes
      from ("stale" when older than the table's head), the source report, and a note.
    - Writes `<out>.md` and the fnvnx-zero/1 sidecar.
  - **Sources**, one per row:
    - D24 trap stubs: full.md's "undefined symbols" table sum.
    - D24 link errors: full.md's trial link, undefined + multiple definitions.
    - D24 false interpretations: the fuzz sidecars' hard.functions summed over shards ("partial: k of N" when shards
      are missing).
    - D24 wrong shaders: the sweep shard sidecars, distinct failing programs (over / ill) + programs vacuous in some
      set.
    - D25 #1-#12: S18's fnvnx-scenario-metric/1 count when measured in at least one scenario. Otherwise the lane that
      measures the target:
      - #3, #4, #6: plat;
      - #7: threads;
      - #8: mem (failures + 1 if over the budget);
      - #12: frames (calibrated only).
      When both exist, the lane's number is shown beside the scenario's.
  - **tools/farm/zero_job.sh**: farm job `zero`.
    - Inputs: every reports/* branch of $ARTIFACTS, fetched and archived per farm, and the integration history
      (git log, 400 heads).
    - Output: zero_targets.py's table. The runner publishes it as reports/<farm>/<c12>-zero.md + .json. On a farm
      named `zero` that is reports/zero/<c12>-zero.md: the runner's naming, beside the asked reports/zero/<c12>.md.
    - RUNNER_REPEAT_S refreshes a head whose lanes report later.
* **measured** - This is the orchestrator's view, not a metric of its own. A prerequisite: every D24 / D25 number in
  one place.
* **tests**
  - tests/s15/test_zero_targets.py (new): 14 checks, 0 failed, on a synthetic three-head reports tree. It covers
    every source; stale heads; deltas; summed fuzz shards and partial shards; distinct sweep programs; scenario
    first, lane fallback, and lane beside.
  - A zero_job.sh smoke run against a local stand-in artifacts repository: the fetch, the archive, the table.
* **farm START (orchestrator)**: one farm, NAME=zero, JOB=zero, ARGS empty, ENVX=` RUNNER_REPEAT_S=1800`.
  - It needs a checkout of fnv-nx-build at $ARTIFACTS, read access only. Minutes per run.
* **asks** - **T1**: no quick_job hook for `zero`: it reads other farms' reports, so it belongs on its own farm.
* **next** - Adjust the sources once the first real reports land (the sweep and full report formats are read as
  their scripts write them today).
