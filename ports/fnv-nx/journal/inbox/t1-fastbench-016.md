### J-XXXX | 2026-11-08 | stream: T1-fastbench | status: wip | parent: J-0156

* **eqa's numbers (before 06e0e3e).** 79d473df: the cache went from 1,301 to 3,699 manifests, 1,326 s prepare +
  build + compare, 9,185 s EH-off + attribution. 9a303226: from 3,699 to 3,706, 1,191 s and 9,566 s. With a warm
  cache the second head stored only 7 entries, yet took as long. Most compiles had no hashable key (the Wine-path
  bug, fixed in 06e0e3e), so they ran again without being stored. The next eqa heads show the fix (unkeyed.txt
  lists what remains).
* **4097dfd, equiv_job keeps the prepared tree.** equiv.py prepare runs in $FARM_HOME/eqprep/tree under
  traced.py. The next head keeps it while the record, the decomp head and the tree's content id hold, and
  equiv-<c12>/tree is a hard-link copy. A failed prepare keeps nothing and fails as before. EQUIV_PREPARE_REUSE=0
  turns it off. Test: tests/t1/test_equiv_prepare_reuse.sh. The prepare's share of the 1,191-1,326 s has not been
  measured separately; "about 10 min" is the estimate from the pipeline's own prepare (572 s).
* **dddfe69, attribution's reduced texts kept.** The per-(candidate, file) texts that equiv.py attribute prepares
  with one rule or group left out are kept under the sha256 of that record, so they are reused only while every
  input of the prepare is the same. MSVC does not run here, so attribution is not exercised by a test: for an
  unchanged head, eqa's attribution table must equal the previous head's. That is the check to read.
* metric: equiv on heads that change no prepare input: the prepare and the reduced texts become checks, and the
  compiles hit the unit cache once the Wine-path fix has filled it. eqa will show it.
