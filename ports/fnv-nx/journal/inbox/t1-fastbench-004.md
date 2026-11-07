### J-XXXX | 2026-11-07 | stream: T1-fastbench | status: wip | parent: J-0156

* **farm reliability: the sw1 "rc 0 after start"** (eefeef2) - the job was alive; the watcher watched the wrong PID.
  - The farm's Bash runs with job control, so `setsid nohup ... &` forks, and farm.pid named a parent that exits at
    once with 0. Reproduced here.
  - tools/orch/farm_prompt.txt now uses `setsid -w` (the PID lives as long as the job), and WATCH also waits while a
    runner.sh process exists. runner.sh writes `$FARM_HOME/<name>.pid`.
  - Farms started with the old prompt keep running detached: do not restart them.
* **quick_job lane A** (2f278cf) - FARM_TREE_WAIT=0 and FARM_OBJ_WAIT=0 (it was up to 2 x 300 s idle before a local
  incremental build), V1_BUDGET=600.
* **the RACE farm path (D25 #7)** (a42a149)
  - `RACE=1 compile_job.sh`: objects in obj-race-<k>-of-<N>, branches shard-race-<k>of<N> and
    `cr/<commit12>/<k>of<N>`, never `c/`. RACE is in the flags key; meta.json has `"race": 1`.
  - `OBJ_PREFIX=cr` for fetch_objs.sh and lib_job.sh's objects_commit/job_inputs.
  - `tools/farm/race_job.sh` (runner job `race`):
    - takes the toolchain shared with full_job, the prepare farm's tree, and the RACE objects (of the commit or of
      its newest ancestor with the same compile inputs);
    - runs `make link` in `$FARM_HOME/race/build`: only the link stage, since the pipeline's guest stage has no
      tsan runtime and variant.sh RACE=1 links hb.c;
    - then runs threads_job.sh with THREADS_BUILD=that build, THREADS_RACE=1 and the verified exe.
    - Only the report is published, never link/ or nro/.
  - test: `tests/t1/test_race_path.sh` (9 cases: c/ and cr/ never mix).
* **farm START (orchestrator)**, with tools/orch/farm_prompt.txt (now `setsid -w`):
  1. RACE compile shards: NAME=crc1..crc4, JOB=compile, ARGS=`<k> 4`, ENVX=` RACE=1 FARM_APT=1`. They read the
     prepare farm's tree t/<c12> (else they prepare locally) and the keys k/<c12>, as the c/ farms do.
  2. The race farm: NAME=race1, JOB=race, ARGS empty, ENVX=` FARM_APT=1`. Its first run builds the toolchain
     (~1 h, as on a full farm), then waits up to 1 h for the four cr/ shards. Its report is
     `reports/race1/<c12>-race.md` with the fnvnx-threads-job/1 sidecar.
  - It runs after the non-RACE watchdog number from full1's build, as the orchestrator said.
* **still open**
  - (a) the T1 metric on the first incremental qk head: qk1-qk3 hold claims for 8db71937, ed380ec7 and 2e000047,
    but no report yet (first-run setup).
  - (c) the boot/lockstep lane: waiting for S5-boot's boot_trace.py. I asked S5 for ref/diff commands and a format
    version, so the Wine reference trace can be cached like the captures.
