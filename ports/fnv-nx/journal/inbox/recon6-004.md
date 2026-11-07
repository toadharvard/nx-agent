### J-XXXX | 2026-11-07 | stream: R6-recon (claude/fnv-recon6) | status: wip | parent: J-0156

* **inline (orchestrator decision)** - the 8 inline bodies now carry `"accept": "inline"` per body in recon.json
  (`inline` list).
  - Each entry records the exe call sites the body was read from and the containing functions that fuzz has to
    confirm: 0x00afc960, 0x0045cee0, 0x00b66f20, 0x00b79930, 0x00acc6c0.
  - The four recons are republished.
* **hand-off** - the 23 open rows of shards 5+3, by class and owner, are a table on fnv-nx-build branch
  `recon-notes/r6` (HANDOFF.md). It holds decomp names, so it stays off nx-agent.
  - S2-bodies-2, 18 rows:
    - code-defmissing 15: STAND1 deletes the tagged function 2, missing fnvlink alias for an explicit `template<>`
      specialization 2, adjustor thunks 5, `??_G` 1, needing the link logs 5;
    - wrong decomp inline bodies 3.
  - S5, 2 rows: harness scaffolding.
  - R6, 2 rows: inline-only destructors, still open.
* **shard 6 (taken over from R7)** - causes.tsv bf0b0eb, `$8=="recon" && $9==6`.
  - 158 rows, 132 of them trap stubs, in 27 home units; one unit holds 74 rows and the next 29.
  - Causes: no-body 124 (other 59, msvc-direct 57, template-special 6, exe-shape 2), extern-text 32 (18 exe
    addresses), code-defmissing 2.
* **metric** - shard 5+3: 23 open (20 handed off). Shard 6: 158 rows without a body (trap stubs 132).
