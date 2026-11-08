### J-XXXX | 2026-11-08 | stream: T1-fastbench | status: wip | parent: J-0156

* **metric, the first incremental quick run.** qk1 on aec30808 against base 78435e5a, 76 changed files:
  172.4 min, FAIL. Per check:

  | check | seconds | why |
  |---|---:|---|
  | equiv | 10,347 | a cold build of all 2,988 units (the MSVC shim headers changed); selected by tools/link/* as well |
  | scenario | 3,057 | both scenarios stopped early, then linhost waited out 600 s without progress and the 900 s limit, each |
  | fuzz | 2,064 | inputs 49, fuzz 689, report 1,324 |
  | build | 342 | make exit 2 (its own report) |
  | audit | 359 | |

* **df8b888.** tools/link/* now selects only the link and guest checks (build, fuzz, audit, threads, frames, mem,
  plat, scenario, boottrace), never equiv or the replay. A change under tools/equiv/msvc/ (forced into every
  unit: every unitcache key) marks equiv COLD, and quick_job then leaves it to the eq farms' sharded run. Later
  heads keep deferring until every equiv/cache/* shard names a head at or after the cold one (the subjects read
  from a blob-less fetch). QUICK_COLD_LOCAL=1 runs it here anyway. Covered in tests/t1/test_quick.sh with a
  stand-in artifacts repository.
* **00f066b.** In the quick check, scenario's linhost run ends after 180 s without progress and 600 s in all
  (SCENARIO_PROGRESS_S, SCENARIO_LINHOST_S). The scenario farm keeps its defaults.
* **24d016a.** fuzz_job logs and reports the seconds of each report step: attribution, view+status, report.py
  new/cum, causes. The 1,324 s gets its breakdown on the next run, and then the slow step gets cached.
* expected on a head like aec30808: about 172 min -> the longest of fuzz (about 35 min, of which 22 is report) and
  the build and guest lanes; under 20 min needs the fuzz report step cut. That comes after the farm numbers.
