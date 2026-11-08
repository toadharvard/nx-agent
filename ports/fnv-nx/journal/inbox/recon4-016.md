### J-XXXX | 2026-11-08 | stream: R4 + S4-platform (claude/fnv-recon4) -> plat metric 0 on the boot path | status: wip | parent: recon4-015

* **did**
  - The original's failed-open baseline (7d6edaa3):
    - fnvtrace.dll accepts `dll!Export` trace-list lines;
    - tools/perf/plat_wine.py keeps the game's own failed CreateFileA calls, keyed by path class and FNV-1a hash,
      with no path text (fnvnx-plat-original/1);
    - plat.py --original lists a matching failure as "also fails in the original", leaves it out of the metric, and
      reports metric_all beside it.
    - Tested on a synthetic trace: test_plat_wine, 13 ok.
  - S18 ran it under Wine: boot-menu, fnv-nx-build scenario/original boot-menu/original_opens.json.
    - The hook works: 18 opens by the game, 2 distinct failed, Plugins.txt (8c42ed6d) among them.
    - S18's wine_scenario.sh (claude/fnv-s18 030c3b5d) adds it to every Wine run once 7d6edaa3 is adopted.
* **measured** - plat.py on the stub boot of d1ed15e74620 with that baseline: **metric 0** (unimpl 0, opens 0,
  decode 0, input 0). metric_all is 1: the relative Plugins.txt open, which the original also fails, is listed.
  - Caveat: this measures the boot path up to S5's frontier (GraphicsRenderer::Create). Platform calls past it
    (archives, ESMs, audio, input in play) have not run yet.
  - The baseline is boot-menu, run in Wine. The stub boot is not the same run, but the open in question happens
    at start-up in both.
