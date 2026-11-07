### J-XXXX | 2026-11-07 | stream: R4 (claude/fnv-recon4) -> D25 #10 script-error lane | status: wip | parent: recon4-008

* **did**
  - **zero_job runs the fault classifier first** (b1e76e3): faults_job.sh on the same head. Its fnvnx-faults/1 feeds
    the table's #1 row, so one farm covers both.
  - **Call watcher** (tools/perf/callwatch.py), for linhost and for the emulator.
    - Config: `<target> <name> [label=xN | label=*xN+OFF]...`. A target is 0xORIG (the image function's
      `__fnv_<orig>`), re:REGEX, or a symbol.
    - The config names the game's own error-print entry points and where their arguments hold the script's form.
      That is game knowledge, so it lives with the farm's private data (written by the owner, S18 or S5), never in
      this repository.
    - `callwatch.py resolve` writes linhost's `--calls FILE`.
    - linhost: the trampolines' stub now hands its saved registers to the hook handler. Each watched entry prints
      `linhost: call NAME caller 0x<lr> label 0x<value> ...`: registers, or the 32-bit word they point to.
    - Emulator: `callwatch.install(emu_guest, config)` or `callwatch.py --calls CFG <emu args>`. Each watched entry
      prints `emu: call NAME caller <orig:..|name> label 0x..`.
    - Both log values only, never strings: the first 5000 calls, then `calls NAME n <count>` totals.
  - **tools/report/scripterr.py**.
    - Groups script errors by (form id, kind, caller). Linhost callers map through nm to faults.py's stable keys.
    - Optional private `--patterns` (`kind<TAB>regex`) count error lines from the platform's debug-output stand-ins
      and the console log. Their form comes from the regex's `form` group or the first 8-hex word. No text is
      written.
    - `--baseline`: the original's run (Wine). Groups also found there are listed and left out of the metric.
    - Metric: distinct groups not in the original. Writes fnvnx-scripterr/1.
  - **zero_targets.py #10**: `<h>-scripterr.json`, or the same object under `scripterr` in S18's
    `<h>-scenario.json`. The runner publishes one sidecar per job, so S18 embeds it there.
* **measured** - Metric: not measured here; the scenario runs are S18's. A named prerequisite for D25 #10, by form
  id and kind.
* **tests**
  - tests/s15/test_scripterr.py (new), synthetic part:
    - grouping;
    - the emulator caller key;
    - a pattern's form, with no text in the md or the json;
    - calls past the 5000 logged ones;
    - baseline subtraction;
    - no data (exit 3).
  - test_scripterr, emulator part (a stand-in Unicorn): targets by exe address and regex, a dereferenced form, totals
    per kind.
  - test_scripterr, live: callwatch.py resolve and `linhost --calls` on mtqemu's mtq_frame_mark(k). 31 calls, forms
    0..30, one caller function; the guest still passes; scripterr groups them.
  - test_zero_targets: 0 failed (new: #10, also embedded in the scenario sidecar). The linhost tests are rerun
    because the stub changed; results in the commit body.
* **asks - S18**:
  1. A private callwatch config next to scenarios.json's game data (not committed): the game's script / console
     error entry points by exe address, with `form=` on the argument that holds the script's form.
  2. Under linhost: `callwatch.py resolve <cfg> --elf <guest> --out watch.txt`, then `--calls watch.txt` in
     LINHOST_ARGS.
  3. Under the emulator: `callwatch.install(eg, cfg)` in emu_scenario.py's main.
  4. `scripterr.py <logs> --elf <guest> [--patterns <private>] --baseline <the Wine run's scripterr.json>`. Put the
     result under `scripterr` in the scenario metric sidecar, and use its metric as your #10 count.
