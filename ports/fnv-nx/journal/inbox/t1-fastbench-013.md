### J-XXXX | 2026-11-08 | stream: T1-fastbench | status: wip | parent: J-0156

* **7657dc5, bodies: one parse per pristine object.** The bodies passes parsed each MSVC object about 5 times
  (16,198 Coff parses of 2,990 objects). vslots.coff() keeps one per unchanged file for the process.
  bodies --no-synth on 31971038913d: 38 -> 28 s (99 s before b9374f3); outputs identical to the committed code's.
* **796b20f, step 3: the link compile cache shared.** tools/farm/linkcache.sh seeds $FARM_HOME/linkobj from the
  artifacts repository's linkobj/cache when a machine has fewer than 500 results. full_job publishes it after a
  link, as one orphan commit, pruned to 2 GB and only when it changed. quick_job does not publish. An entry is
  used only where the compiler bytes, the command lines (the farms share $FARM_HOME's layout) and every
  dependency's sha256 are the same. Results are about 47 KB each, 6x smaller compressed; a full link is about
  10k results. A fresh farm (leq1's first head, every new container) then starts warm. Test:
  tests/t1/test_linkcache.sh.
* **0e11a93, prepare kept when its inputs did not change.** full1 on 97deacd38aec spent 572 s preparing a tree
  with 0 files changed. tools/pipeline/traced.py runs prepare.py under an audit hook and records every file it
  read (imported modules' sources too), every directory it listed, the argv, the interpreter and the FNVNX_*
  environment. The stage keeps the last tree only when all of them are unchanged and the tree is still the one
  that run made. That is exactly the tree prepare.py would make at this head, so J-0054 holds: the provenance
  names this head, with reused_from and mode "prepare.py (reused: inputs identical)". Anything else runs
  prepare.py, and so does PREPARE_REUSE=0. Tests: tests/t1/test_traced.py, tests/t1/test_prepare_reuse.py;
  tests/s9/test_provenance.py and tests/farm/test_report_provenance.py pass.
* metric: a full build on a head that changes no prepare input: prepare 572 s -> its check (seconds); a fresh
  farm's first link: cold -> from linkobj/cache. Farm numbers pending (leq1, qk1).
