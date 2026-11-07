### J-XXXX | 2026-11-07 | stream: R4 (claude/fnv-recon4) -> S18 plan-driven input under linhost | status: wip | parent: recon4-005

* **did** (for S18's scenario_job: the same plan a second time under `linhost --profile --frame Present`, its profile
  into FRAMES_PROFILES, D25 #12)
  - **linhost --feed FILE** (tests/s15/mtqemu/linhost.c).
    - input_poll returns FNVNX_EV_KEY events. Before, it was a stub returning 0.
    - The plan's steps run on CLOCK_MONOTONIC. They are checked at input_poll, sleep_ns, thread_yield,
      wall_time_us, every futex wait and every hook hit. That matches emu_scenario.py's checks on the emulator's
      clock (pad_read, sleep, yield, wall time, the scheduler).
    - press: the downs at once, the ups half a gap later, the next step after the gap, as in emu_scenario.
    - until: a hook milestone, a file under --root (case-insensitive, any depth, like emu_scenario's find_file), or
      only the timeout, for a milestone linhost cannot see.
    - wait, mark.
    - Every step is logged as `linhost: scenario ...`. After the last one: `scenario complete`, the profile written,
      exit 0. A guest that ends first logs `scenario incomplete at step N`.
  - **Hooks**: the `--frame` trampolines are general now: a 48-byte site carries its id in w15.
    - A feed `hook 0xADDR name` marks a milestone reached when its function is entered.
    - A function that is both a hook and a `--frame` shares one site.
  - **tools/perf/linfeed.py**: plan.json (fnvnx-scenario-plan/1, `scenario.py compile`) -> fnvnx-linfeed/1.
    - Keys come from S18's tools/scenario/keys.py `press_events`, the same vocabulary. Unknown keys are skipped and
      listed.
    - Milestones come from tests/s5/milestones.txt. `call 0xORIG` resolves to the image function's `__fnv_<orig>` and
      `call re:...` to every matching ELF function symbol; both become hooks. `data` and `store` milestones are not
      visible from linhost: the step waits its timeout and is logged `until unsupported`.
  - **For S18's scenario_job** (S18 owns it):
    1. `linfeed.py plan.json --elf guest.elf --out feed.txt`;
    2. `LINHOST_ARGS="--feed feed.txt --profile prof.txt --frame <Present>" tests/s15/mtqemu/boot_game.sh ...`;
    3. `FRAMES_PROFILES=prof.txt FRAMES_ELF=guest.elf tools/farm/frames_job.sh`.
    The Present symbols are the ones frames_job finds by FRAMES_MATCH.
  - Fixes on the way:
    - tests/s15/mtqemu now links platstat.cpp. recon4-005's FNVNX_UNIMPL in thread.cpp broke the mtqemu link.
    - The test libc (mlibc.c) gained strchr / strrchr / strstr.
* **measured** - Metric: not measured here. This is a named prerequisite: S18's D25 #12 under qemu.
* **tests**
  - tests/s15/test_linfeed.py (new): 12 checks, 0 failed. It builds mtqemu, compiles a plan with linfeed, and checks
    in the run: the key reaches the guest's input_poll as down/up of usage 4; until file (case-insensitive, at
    depth); until unsupported (timeout); the hook milestone; mark; complete; exit 0; the profile's frame marks.
  - test_mtqemu (new case `feed`: polls input_poll) and test_frames: results in the commit body.
* **next** - Wait for S18's adoption; then the farm's first frames report from a plan run.
