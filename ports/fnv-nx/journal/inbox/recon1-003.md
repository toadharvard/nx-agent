### J-XXXX | 2026-11-07 | stream: R1-recon (claude/fnv-recon1) | status: wip | parent: J-XXXX (recon1-002)

* **shard** - On the orchestrator's message, the shard is now S2-bodies' authoritative list: fnv-nx-build
  s2d/list/9f865b0a4037 at bf0b0ebf, rows with `owner == recon` and `shard == 0`. That is 81 names:
  - no-body 56 (other 26, msvc-direct 12, exe-shape 11, template-special 7);
  - extern-text 16, covering 7 exe addresses with no decomp definition;
  - code-defmissing 9.
  Five names of the old list are gone, because S2-bodies' rules close them: NodeRef::Assign and four by-value
  `operator*`. Their bodies are withdrawn.
  - recon/bsmenu_tile_ob1_variant was republished without NodeRef::Assign.
  - The three branches that held only the dropped names now have a withdrawal commit with no recon.json:
    navmeshpovsearch, nirollcontroller, vatsmenu (o2). The proxy refuses branch deletes.
  - recon/staging_blk_004e0000_od is withdrawn the same way, and will carry the unit's extern-text bodies.
* **did** - 22 units are published on fnv-nx-build `recon/<unit>`, with bodies for 45 names. The Havok contact-point
  unit binds 9 more names by address (symbols only; it needs the REC1 fix requested in recon1-002).
  - Every unit passes build_and_match with its bodies, all functions and bytes equal: (a).
  - Every body compiles with the game's AArch64 flags, and every listed name of the unit is then defined.
  - Most bodies forward to the native definition at the exe address the call site reaches. Each forwarder's
    evidence (call site, `this` passed) is in its recon.json.
  - The other bodies are inline reconstructions read from the exe code: free-list pops, NiPointer conversions, a
    sized NiFree, and template destructors forwarding to the NiPointer release.
* **blockers (unit bugs, for S2-bodies / S1)**
  - `visitLevel(int)`: see recon1-002. The unit's filler `S00da8180_AabbCastMachine::FUN_00da71c0` should bind to
    `__fnv_00da71c0`.
  - `ReleaseBoundB5E(BSMultiBoundB5E*)` (shadowscenenode): the decomp's `RemoveBound` is not the exe's.
    - In the exe, RemoveBoundByOwner (0x00b5edf0) calls 0x00b5cff0 at 0x00b5ee71, with ecx = the manager and the
      NiPointer& pushed. 0x00b5cff0 queues the bound on the list at +0xf0 under the lock 0x011f9ea0 when the byte at
      +0x1fc is set.
    - The decomp instead calls ReleaseBoundB5E(ptr) and increments +0xcc. That call gets no `this`, so no body for
      it can be correct.
    - Fix in the unit: RemoveBound forwards to 0x00b5cff0(this, &ptr).
  - shadowscenenode `S00b5eca0_List::FUN_00a5a310` stores `pkItem->light` as a plain pointer. The exe does a
    NiPointer assignment there and adds a reference, so the native build is one reference short.
* **metric** - shard 0 names (authoritative list) without a body: 81 -> 27.
  - Of the 27: 25 are new extern-text and code-defmissing names, now in progress; 2 are blocked (above).
  - The 9 names bound by address count as closed, pending the REC1 fix.
  - Accept split: (a) 45 (+9 bound), (b) 0.
