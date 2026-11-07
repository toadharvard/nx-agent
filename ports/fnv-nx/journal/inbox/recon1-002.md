### J-XXXX | 2026-11-07 | stream: R1-recon (claude/fnv-recon1) | status: wip | parent: J-XXXX (recon1-001)

* **did** - Shard 0: 26 functions have bodies, and 9 more are bound by address. That is 13 units on fnv-nx-build
  `recon/<unit>`.
  - Units: bsmenu_tile_ob1_variant (13), the convex-hull unit (2), blk_00e40000_o2 (2), main (2), and one each in
    blk_00520000_od, blk_00ce0000 (rtm), precipitationshader, navmeshpovsearch, nirollcontroller, vatsmenu (o2) and
    blk_004e0000_od. The Havok contact-point unit has 9 names bound by address (symbols only).
  - Every unit passes build_and_match with the bodies: all of the unit's functions and bytes are equal under MSVC 9.
    The bodies also compile with the game's AArch64 flags (tools/link/compile.sh --print-flags), and every shard name
    of the unit is then defined (nm).
* **finding (class)** - Most no-body names in this shard are a unit's local re-declaration of an exe function the
  decomp already defines elsewhere. 13 of the 13 names in the tile unit are of this kind (pool alloc/free, a spin
  lock's unlock, NiPointer assignment, MemoryManager alloc/free).
  - The exe call sites are in COMDATs the recipe does not place, or in inlined code. So the link sees no address.
  - The body forwards to the native definition: GCC calls `__fnv_<addr>`; MSVC calls the absolute address, with
    thiscall written as `__fastcall` plus an unused edx. recon.json `forwarders` records the target and the exe call
    site of each body.
* **finding (link, for S2-bodies)** - The link leaves conversion operators and by-value `operator*` unbound even when
  the unit's units.json symbols name their address. Examples: `??BCamNodePtr@fnv_00872b00@@QBE...` and
  `??BQTaskPtr@...` -> 0x00559450, `??DVec@fnv_006a0ef0@@...` -> 0x0045bb20, `??DxMat3@@...` -> 0x004b4500.
  - In this shard I wrote those bodies myself, from the exe code at the address (all bytes equal; marked `NOINLINE`
    under MSVC, so recipe callers keep their calls).
  - Other shards probably have more names of this class. A fix in the MSVC-to-GCC name pairing would close the
    whole class.
* **request (rules, orchestrator)** - REC1 should persist recon.json `symbols` even when the recon adds no
  `functions`.
  - Today add_recon_definitions writes units.json only `if n` (n = recipe functions added). A symbols-only recon is
    therefore dropped.
  - recon/havok_physics_utilities_dynamics_savecontactpoints_hkpsavecontactpointsutil needs this fix.
    DCD6A0Agent1..9 are compared by address in FUN_00dcd4d0, which checks `cmp eax, imm32` against 0x00d3fe90 and
    eight more. A body would give each name a new address, and the comparison would stay false natively. These names
    must be bound to the exe's functions, not given bodies.
* **blocker (for S2-bodies)** - `visitLevel(int)` (blk_00da0000 rtm unit) has no exe counterpart.
  - It is called only from the decomp's filler body of `S00da8180_AabbCastMachine::FUN_00da71c0`. The recipe maps
    that function to 0x00da71c0, the real MOPP aabb-cast traversal.
  - 0x00da71c0 already has a native definition (MoppCastMachine::FUN_00da71c0, hkostream rtm unit).
  - Fix: bind or override the unit's filler FUN_00da71c0 to `__fnv_00da71c0`. visitLevel then goes away with it. A
    body for visitLevel alone would make the native aabb cast find nothing.
* **metric** - shard 0 functions still without a body: 61 -> 35. The 35 are 9 bound by symbols (waiting for the
  REC1 fix), 1 blocked (visitLevel), and 25 in progress (shadow scene node, nav-mesh obstacle manager, tree manager,
  leaf info, facegen, and 6 single-function units). Accept split: (a) 26, (b) 0.
