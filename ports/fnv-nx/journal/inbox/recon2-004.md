# recon2-004 | 2026-11-07 | stream: R2 (recon fleet, D23) | status: active | parent: recon2-003

* **shard 1** (D23 (c) inline, c2f61891): re-accepted the two held-back recons as `"accept": "inline"` with their exe
  sites (recon/havok_hkgpmesh_ehsc: ProbePool, 4 rows, sites 0x00e247c0 / 0x00e24850; recon/havok_spherecapsule_gs:
  ~hkpCollisionAgent, 1 row, site 0x00de10b0), and published recon/staging_blk_00ac0000_od_ob1_variant_ufill
  (S5540_Buffer::_Ufill, 1 row, site 0x00abbfc0). Metric 53 -> 47.
  - Tree::eraseTree stays published in the parser839 recon but is not counted (orchestrator). Removing its inline
    body breaks the byte match of map::clear 0x00acb1a0 (13 bytes); the body's declined-inline frame is part of that
    match.
* **shard 7** (taken over from R8, orchestrator): causes.tsv at bf0b0ebf, `$8=="recon" && $9==7`: 91 rows, 85 trap
  stubs. By cause: no-body 78 (msvc-direct 45, other 30, exe-shape 3); extern-text 4 (unit/exact 3, name-addr 1);
  code-defmissing 3. 23 home units; 36 in one unit. First pass: 9 align to defined addresses, 3 to an address a
  published R2 recon defines, 66 unresolved (mostly wrappers the exe inlines: (c)). In work with 3 workers.
* **metric**: shard 1 47; shard 7 85.
