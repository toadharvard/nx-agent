### J-XXXX | 2026-11-07 | stream: T1-fastbench | status: wip | parent: J-0156

* **item 3 done: `tools/farm/quick_job.sh <commit>`** (claude/fnv-t1-fastbench 3650baf)
  - `tools/farm/quick_plan.py plan` turns the diff against the last verified head into checks: build, equiv, fuzz,
    audit, replay, programs. Path classes match first-come; an unknown path selects everything.
  - The checks run on one machine, in 3 parallel lanes, each on the caches from items 1-2:
    - A: `full_job.sh` (incremental prepare/compile/link in its persistent build directory, guest, emulated boot,
      link/<c12>), then `fuzz_job.sh 1 1` (verdict cache, FUZZ_MAX_S 600) and `audit_job.sh 1 1` (row cache,
      AUDIT_BUDGET 300).
    - B: `equiv_job.sh 1 1` (object cache, seeded from every shard).
    - C: `sweep_job.sh 1 1` with SWEEP_CAPTURE=replay on `quick_plan.py cover` (the fewest capture-store scenes
      that hold every VS/PS program; QUICK_CELLS 12). Programs run only when render/shaders changed.
  - One report: verdict, wall clock (T1's metric), and run/skip reason, exit, seconds and headline per check; plus
    the `fnvnx-quick/1` sidecar. A pass moves `$FARM_HOME/quick/verified`.
  - The capstore index now holds each scene's program hashes and its cells.tsv line.
* **item 4 done: warm farms + the full-run rule** (b1ade80)
  - runner.sh:
    - a flock per farm name (`$FARM_HOME/<name>.lock`): a second start exits;
    - `RUNNER_PERSIST` (no exit after RUNNER_MAX_S, FARM_DEADLINE per job), `RUNNER_POLL_S`;
    - `RUNNER_CLAIM`: an atomic head claim on `quick/claim/<tag>/<c12>`. A fixed-date commit lets a restarted farm
      re-take its own claim, and every other farm's push is refused.
  - `qk<k>` names default to persistent, a 20 s poll and the claim tag "quick", so they override the farm prompt's
    `RUNNER_MAX_S=60`.
  - The quick farms keep their caches local (`FUZZ_PUBLISH=0`, `AUDIT_PUBLISH=0`, `EQUIV_CACHE_PUBLISH=0`). Three
    writers must not share a 1-of-1 branch.
  - Full-run rule (quick_plan.py, README): the report says `FULL RUN NEEDED` with old -> new values when any of
    these changes:
    - the toolchain or the pins;
    - difffuzz `HARNESS`, `AUDIT_HARNESS`, unitcache `CACHE_V`;
    - capstore `FORMAT`, scene_stats `REF_FORMAT`, sweep_job `CAPTURE_V`;
    - a capture-key input file.
    The sharded farms of that check then run (fz/au/eq/sw). Everything else is incremental.
* **metric** - not measured yet: this container has no toolchain, Wine, MSVC or game install, and setting them up is
  longer than 10 minutes.
  - Estimate for a ~5-unit commit after the caches are populated:
    - lane A: incremental build + boot, then fuzz/audit of the changed keys (tens of functions);
    - lane B: ~5 MSVC compiles;
    - lane C: 12 replayed scenes at ~45 s (~9 min).
  - Before: hours on 48 machines. After: a target of <= 20 min on one warm machine. The first qk reports give the
    real number, and I will tune QUICK_CELLS / FUZZ_MAX_S from them.
* **tests** - `tests/t1/test_quick.sh` (14: plan classes, cover, quick_job with stand-in jobs, parallel lanes,
  verified-head logic, full-run cases), `tests/t1/test_runner_warm.sh` (8: lock, persist, two farms split the heads
  exactly once, own re-claim), plus items 1-2's tests; tests/farm stage_after and fuzz_summary OK. Integration
  merged at 0eaf834; the t1 tests still pass.
* **farm START (orchestrator), after merging this branch, in order**
  1. Populate the caches. The sweep is item 1's pass: `SWEEP_PROGRAMS=0` sw1..sw36 `sweep <k> 36`. Audit au1..au26
     and equivalence eq1..eq6 run as before, so their caches fill.
  2. Three warm farms, each with tools/orch/farm_prompt.txt: NAME=qk1, qk2, qk3; JOB=quick; ARGS empty;
     ENVX=` FARM_APT=1`. That is: `setsid -w nohup flock -n /root/farm.lock env RUNNER_MAX_S=60 RUNNER_KILL_S=86400
     FARM_APT=1 bash /home/user/fnv-nx-build/farm/start.sh qk<k> quick`. The qk name makes the runner persistent
     despite RUNNER_MAX_S=60.
     - The first run on each qk farm sets up everything: Wine + MSVC, the toolchain (~1 h, from
       toolchain-<id>), game data, the shader tools, and seeds from the cache branches. That run is slow, and its
       report shows "no verified head: every check". From then on, each new head is verified incrementally.
     - Read `reports/qk<k>/<c12>-quick.md`. The wall clock there is T1's metric.
* **next (my queue, unless redirected)** - measure on the first qk reports and cut the largest lane: likely lane A's
  full_job prepare and link (prepare caching per file, as compile already does).
