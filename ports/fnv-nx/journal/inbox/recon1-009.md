### J-XXXX | 2026-11-07 | stream: R1-recon (claude/fnv-recon1) | status: wip | parent: J-XXXX (recon1-008)

* **shard** - On the orchestrator's message, R1 takes shard 8 (R9's; R9 is retired). The list is s2d/list at
  bf0b0ebf, rows with `owner == recon` and `shard == 8`.
  - 80 rows: 73 trap stubs, 7 not.
  - By cause: no-body 70 (other 38, msvc-direct 26, template-special 4, exe-shape 2), code-defmissing 6,
    extern-text 4.
  - 21 units. Largest: 24, 12, then four units of 5 each.
  - No recon branch of a shard-8 unit exists yet.
* **orchestrator decisions on recon1-008** - Accepted: the /Ob1 evidence for 0x00ad7020. hk3AxisSweep32CreateBroadPhase
  gets no stub; it goes to S2-bodies' "unreferenced" class. matrixvt goes to S2-bodies-2.
* **metric** - shard 8 rows without a body or binding: 80.
