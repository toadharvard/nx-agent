# recon2-001 | 2026-11-07 | stream: R2 (recon fleet, D23) | status: active

* **shard** - S2-bodies' authoritative list on fnv-nx-build `s2d/list/9f865b0a4037` (orchestrator), owner `recon`,
  shard 1 (sha1(home unit) mod 10): 161 rows, of which 158 are trap stubs in the guest (3 rows trap = 0).
* **by cause** (trap stubs): no-body 111 (other 72, msvc-direct 32, exe-shape 5, template-special 2); extern-text 47
  (unit/exact 41, gref/exact 3, name-addr 3).
* **by home unit**: 17 units; 88 stubs in staging blocks, 37 in Havok units, 13 in bscore, 20 in the rest.
* **order**: boot path and most-called first; bodies go to fnv-nx-build `recon/<unit>` (fnvnx-recon/1), nothing
  exe-derived in nx-agent.
* **metric** (shard stubs without an accepted body): 158.
