### J-XXXX | 2026-11-08 | stream: T1-fastbench | status: wip | parent: J-0156

* **D22 identifier index (owner order: exact fix, identity test over the whole decomp).**
  * 0062ff2c: d22_placeholder and _d22_callonly scan a per-text index of the maximal \w+ runs instead of a
    whole-file regex per name. The patterns run only in windows around runs equal to the name, ending at the first
    ( ; { } ). The blanked spans of d22_placeholder go through _D22Rest. A name that is not a plain identifier
    keeps the original scan. Speed only: text, stats and applied rows are unchanged.
  * Identity, the whole decomp: base and patched rule on the same input at the rule, every call compared. Output
    text, stats delta and the _fnvnx_d22_applied rows were all compared.
    * On R2's 56ee671c: 5,478 calls, 0 differing; 367.5 -> 75.6 s.
    * On integration's file (_d22_numbered, no exe_ok): 5,478 calls, 0 differing; 438.2 -> 89.0 s (prepare
      1,535 s, so about 1,186 s with the patch).
  * Matcher: 10,688 random checks against re.finditer, 0 mismatches. tests/s1/test_rules.py: 0 failed with and
    without the patch.
  * S1 owns the D22 path: this is a speed-only refactor of its scanning, with no change to what the rule decides.
* **Caches.** full1 d17625063785 on the stable worktree: 325 of 2,978 units compiled (613 s), the rest from the
  cache. Its link still took 1,914 s, so the link-only path is not the cost there yet: next target.
* **bl1** has published nothing since its 11:06 report (bea5a2ed3ccd, the cold, capstone-less run).
* Next slow prepare rules: r_harness_internal 125.8 s, r_duplicate_int_overloads 74.4 s, r_forward_template_calls
  68.6 s.
