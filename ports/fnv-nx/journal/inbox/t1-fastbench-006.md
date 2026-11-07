### J-XXXX | 2026-11-07 | stream: T1-fastbench | status: wip | parent: J-0156

* **metric, first measurements** - quick_job on one machine, both cold (no verified base, every check), both on
  scripts from before the fixes below:

  | farm | head | wall clock | verdict | what happened |
  |---|---|---:|---|---|
  | qk2 | ed380ec7 | 141.8 min | FAIL | equiv 8,511 s (cold MSVC build of 2,988 units); build and fuzz exit 128 after 13 s, audit's setup failed with them |
  | qk1 | 2e000047 | 173.1 min | FAIL | build ok, 6,724 s (cold local prepare 545 s + compile + link + boot); equiv exit 128 after 11 s; fuzz and audit "not run" (no published t/<c12>) |

  The next number is qk1's run on 78435e5a (claimed, running), on warm caches.
* **fixes**
  - ca70f35: `tools/farm/worktree.sh`. Every farm job made the head's worktree with `[ -d ] || git worktree add`; with
    quick_job's lanes running at once, the losing adds exited 128. The add now runs under a lock and a cut-short
    worktree is remade. Every farm job uses it, and quick_job makes the worktree before the lanes start.
  - 4babb15: the base is the last finished head, not the last all-pass head. Checks that are not a pass in
    `$FARM_HOME/quick/status.json` are planned again whatever the diff, so one failure no longer pushes every later
    head back to "every check".
  - 959a3b2: fuzz and audit run on the head's own prepared tree and link (FUZZ_TREE, FUZZ_LINK_DIR from full_job's
    build directory). A quick farm needs no prepare farm.
  - 9f0fdd6: `gamedata_fetch.sh` retries every remote call with backoff and uses one fresh repository per item. This
    fixes sw36's "Connection reset by peer" on the 1.6 GB Voices1 item.
  - eefeef2: the farm prompt uses `setsid -w`, plus a runner pid file. The old `$!` was a parent that exits at once
    under job control; that was sw1's "rc 0 after start".
  - f7f7e33: test_runner_warm.sh killed its persistent test runners by a pattern that never matched, which left
    eight of them in this container. It now kills them by pid.
* **lanes and owner files**
  - T1 now owns quick_job.sh, quick_plan.py, tests/t1/test_quick.sh and full_job.sh.
  - Merged lanes: R4's threads/frames/mem/plat and S18's scenario in lane D, plus T1's boottrace.
  - boottrace follows S5's fnvtrace-2 (profile.py in the key, FARM_PROFILE for the guest). Its key covers S5's full
    input list.
  - R4's requests: plat lane (2478255), test timing (c164549), full_job step 6b NRO checklist (6fe571a, with an if/fi
    fix found by its test), `--plat` on the stub boot and the ini beside the boot image in the boot stage
    (26af562, boot-v4).
  - RACE farm path: cr/ object branches and race_job.sh (a42a149).
* **hosted here** - the `zero` job (reports/zero): nice 19, RUNNER_PERSIST, RUNNER_MIN_GAP_S=600 (ba0d7f0), about
  1 min a run. Restarted after this container's restart at 22:37; it was down from 22:06.
* **open**
  - qk1's incremental number.
  - The capture store: no capture/* branch yet, so replay is skipped "no capture store here yet".
  - The boot list: no trace/* yet (S5's boot_trace_job).
