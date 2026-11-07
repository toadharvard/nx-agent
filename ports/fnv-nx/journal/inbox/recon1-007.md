### J-XXXX | 2026-11-07 | stream: R1-recon (claude/fnv-recon1) | status: wip | parent: J-XXXX (recon1-006)

* **did** - REC1-REPL, approved by the orchestrator, is in tools/rules/s1_compile.py `r_recon_bodies`.
  - recon.json may carry `"replace": [{"old", "new"}]`. The pairs are applied in order to the text the rule
    receives, before the append, and each counts once in `REC1-REPL`.
  - Every `old` must occur exactly once. Otherwise the prepare raises a RuntimeError that names the recon dir, the
    unit, the pair index and the count. A recon written for other text never applies only half its edits.
  - A recon may now consist of `replace` alone (no append, no symbols).
  - A skipped recon (REC1-STALE / REC1-DUP) applies no replace either.
  - docs/EQUIVALENCE.md's REC1 row says what replace may change: GCC-side code only. Every recipe function keeps
    its MSVC bytes.
* **tests** - tests/s1/test_rules.py has 7 new REC1-REPL checks:
  - order, and the body appended after the edits;
  - the appended body is not edited;
  - REC1-SYM still persists a replace recon's symbols;
  - `old` occurring twice fails;
  - a missing `old` fails;
  - an `old` found only in the appended body fails;
  - a replace-only recon is accepted.
* **measured**
  - tests/s1: 0 failed.
  - tests/s11: 2 failures. Both are STAND5 (tools/rules/s2_bodies.py), unclassified in tools/equiv/equiv.py and
    missing from docs/EQUIVALENCE.md. They are pre-existing: the same 2 failures show on the integration head
    without this change. That is S2-bodies' to classify.
  - The real rule on the three shard-0 replace recons (shadowscenenode, blk_00da0000 rtm, tesobjectcell), run on
    the decomp's sources, applies 3, 2 and 3 pairs with no error.
* **metric** - a prerequisite. It makes shard 0's last 2 names (visitLevel, ReleaseBoundB5E) and the 2c/2d fixes
  take effect: shard 0 goes 2 -> 0 at the next prepare with FNVNX_RECON.
