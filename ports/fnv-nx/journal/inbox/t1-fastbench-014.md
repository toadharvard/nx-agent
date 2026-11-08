### J-XXXX | 2026-11-08 | stream: T1-fastbench | status: wip | parent: J-0156

* **40e520f, quick's cold-equiv check accepts eqa's 1of1.** eq1-eq6 are retired, but their equiv/cache/1of6..6of6
  branches remain. The check needed every shard newer than the cold head, so it would have deferred forever. It
  now accepts any N with a complete newer set (1ofN..NofN); a stale 1of6 beside a newer 1of1 is tested in
  test_quick.
* **6228d62, compile.sh: S2's "silent exit when the last unit has no object".** Not reproduced here. The real
  compile.sh on a tiny tree whose last unit does not compile exits 0 in every mode (no list, a list of that
  unit, a flags-stamp change with a list, computed keys, KEYS_ONLY, --print-flags), and
  tests/t1/test_compile_lastunit.sh now runs each one. To make any remaining path loud: set -E (the ERR trap also
  fires in functions and subshells), and the trap prints "compile.sh: line N: exit R: <command>". S2's command
  line is wanted to pin its case.
* **0bd970f, link-head from integration builds only.** runner.sh exports FARM_WIP_BRANCH while it runs a wip
  head. With it, publish_link pushes link/<c12> only, built in a separate local branch (link-wip) on top of
  origin's link-head. meta.json carries wip_branch, and the publish note says "(wip/<b>; link-head not moved)".
  Tests: tests/t1/test_publish_wip.sh; tests/v1/test_publish_stale.sh (A-E) passes with the host tools installed
  here.
* the zero runner here is alive (tracked task; last run b9a17e4956de 07:47).
