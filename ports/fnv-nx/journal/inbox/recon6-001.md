### J-XXXX | 2026-11-07 | stream: R6-recon (claude/fnv-recon6) | status: wip | parent: J-0156

* **shard** - D23 recon shards 5 and 3 (orchestrator: R4 does not take shard 3, R6 takes it too): rows of
  fnv-nx-build `s2d/list/9f865b0a4037` causes.tsv with owner `recon` and shard 5 or 3 (sha1(home unit) mod 10, as
  defined there).
* **measured** - 112 names, 100 of them trap stubs in the guest, in 27 home units (13 shard 5, 14 shard 3).
  - shard 5: 45 = extern-text 25 + no-body 20 (msvc-direct 6, other 12, template-special 2).
  - shard 3: 67 = extern-text 12 + no-body 55 (msvc-direct 19, other 18, template-special 14, exe-shape 1).
  - The 37 extern-text names are 3 exe addresses (one body each, bound to every name).
* **method** - a local resolver compiles each referencing unit with MSVC 9 under Wine and reads, from the exe, the
  target of every unbound call in the unit's functions whose exe address is known: byte-equal callers first, then
  call order, then the unwind funclets of each FuncInfo (entry k of the object's unwind map against entry k of the
  exe's). First pass: about 25 of the 75 no-body names resolve to an address a unit already defines and matches.
  Those need a binding (recon.json `symbols`), not a second body.
* **metric** - shard names still without a body: 112 (trap stubs 100).
