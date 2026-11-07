### J-XXXX | 2026-11-07 | stream: R4 (claude/fnv-recon4) -> fault classifier, hang detectors | status: wip | parent: recon4-007

* **did**
  - **tools/report/faults.py** (D25 #1).
    - It reads emu_guest's output:
      - the `== trace` fault with its frames;
      - `stubbed fault` lines;
      - `skip-summary` lines;
      - `== hang` from the new emu_progress.
    - It reads linhost's output:
      - `fault:` + `frames`;
      - `skipped initializer`;
      - `hang:` + `hang thread`.
    - Faults are grouped by (kind, function, 3 callers).
    - Functions are keyed by the exe address an emulator symbol carries (`orig:XXXXXXXX`, stable across relinks).
      Otherwise by the native name. A linhost address goes through nm of the guest to its function and its
      `__fnv_` alias. Groups keyed by a bare address are marked unstable.
    - Kinds: fatal:<error>, fatal:signal-N, stubbed:<error>, skipped-init:<error>, hang.
    - `--prev`: new / known / fixed against the previous head's fnvnx-faults/1.
  - **tools/farm/faults_job.sh**, farm job `faults`.
    - Logs: FAULTS_LOGS (S18's scenario logs), else the full job's published link/<c12> boot logs (boot/emu.log,
      boot/stub/emu_stub.log, guest/fnvnx.elf).
    - The previous head is the newest older integration commit this farm classified ($FARM_HOME/faults/<c12>.json).
  - **zero_targets.py #1**: the faults lane's groups, with new / known / fixed in the note. They are shown beside
    S18's scenario count, and stand in for it when the scenarios did not measure #1.
  - **Hang detector, linhost `--progress-timeout S`.**
    - Progress is any of: a guest code page sampled for the first time (the per-thread CPU timers run for this even
      without --profile), a feed milestone hook hit, a feed step done.
    - A monitor thread (host code only, its own stack) checks every second. After S s without progress it prints
      `linhost: hang: no progress for S s (last: <what> at <ms> ms)`.
    - Then each thread gets SIGURG and prints `linhost: hang thread <tid> pc .. lr .. frames ..` from its own
      context. Then: the feed's step, the profile written, exit 4.
  - **Hang detector, tools/perf/emu_progress.py** (`--progress-timeout S <emu_guest args>`, or
    `emu_progress.install(emu_guest, S)` from emu_scenario).
    - Progress is any of: a new milestone, a function seen for the first time at a scheduling point (pick, the
      polling host calls), console output.
    - On a hang it prints `== hang` and one stack line per thread (each saved Unicorn context is restored only to
      read it; the current one is put back). Then SystemExit, which emu_guest reports as its STOPPED reason with the
      full summary.
* **measured** - Metric: not measured here. These are named prerequisites for D25 #1 (fault groups per head) and for
  farm time (hangs end in S s instead of the job's hour).
* **tests**
  - tests/s15/test_faults.py (new): 12 checks, 0 failed, on synthetic emu and linhost logs. It covers grouping, the
    exe-address keys, the unstable mark, the hang kind, and new / known / fixed.
  - tests/s15/test_progress.py (new):
    - emu_progress on a stand-in emulator with a fake clock;
    - live: a tiny spinning guest under linhost --progress-timeout 3 exits 4, with its thread's pc inside the loop
      function;
    - live: faults.py names that hang;
    - live: the busy mtqemu run under --progress-timeout 60 does not trigger.
    Results in the commit body.
  - tests/s15/test_zero_targets.py: 15 checks, 0 failed (new: #1 faults lane).
* **asks**
  - **S18**:
    - add `--progress-timeout 600` to the linhost runs (LINHOST_ARGS);
    - call `emu_progress.install(eg, S)` in emu_scenario.py's main (one line, before `eg.main`), with S from an env
      var such as SCENARIO_PROGRESS_S;
    - hand the scenario logs to faults_job.sh through FAULTS_LOGS.
  - **S5-boot**: farm_boot.sh can use EMU_PY=tools/perf/emu_progress.py for plain boots.
  - **farm START (orchestrator)**: NAME=faults (or on the zero farm's machine), JOB=faults, ENVX=` RUNNER_REPEAT_S=1800`.
