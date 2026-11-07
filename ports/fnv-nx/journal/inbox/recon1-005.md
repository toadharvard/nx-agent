### J-XXXX | 2026-11-07 | stream: R1-recon (claude/fnv-recon1) | status: wip | parent: J-XXXX (recon1-004)

* **shard** - On the orchestrator's message, R1 takes shard 4 (R5's; R5 is retired). The list is fnv-nx-build
  s2d/list/9f865b0a4037 at bf0b0ebf, rows with `owner == recon` and `shard == 4`.
  - 91 rows in 42 units. 88 are trap stubs in the guest; 3 are not.
  - By cause: no-body 59 (other 30, msvc-direct 24, exe-shape 2, template-special 3), extern-text 21,
    code-defmissing 11.
  - Largest units: 17, 8, 8, 6, 5, 4, 4, 4. Then 9 units have 2 rows and 25 units have 1.
  - No recon branch of a shard-4 unit exists yet on fnv-nx-build.
* **metric** - shard 4 rows still without a body or binding: 91.
