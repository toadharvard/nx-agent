### J-XXXX | 2026-11-07 | stream: R6-recon (claude/fnv-recon6) | status: wip | parent: J-0156

* **list** - re-pulled s2d/list/9f865b0a4037 at bf0b0eb: shards 5 + 3 are now 127 rows (115 trap stubs).
  - No earlier name was dropped; 15 new rows of cause `code-defmissing`.
  - The list still counts the names R6 closed: the link predates the recon branches.
* **closed** - 104 of 127 rows (92 of the 115 trap stubs), in 25 fnv-nx-build branches `recon/<unit>`. Each was
  checked twice:
  - MSVC 9: the unit with the bodies matches every function and byte;
  - native: the prepared-tree unit compiles for aarch64 ILP32 and defines each body name.
  - Split by how each name closes:
    - bindings 54: recon.json `symbols`, each the exe call target checked by hand and each an fnvlink `exact` key;
    - bodies at new exe addresses 3: the extern-text wrappers, which close 37 names;
    - alias bodies 27: byte-equal to an exe copy that a unit already places;
    - deleting forms 2: D0, the compiler's deleting form of a byte-matched D1;
    - inline bodies 8: see the category below.
* **new category for the orchestrator: accept "inline"** - 8 bodies have no out-of-line exe copy at all. The exe only
  inlines them, and each body was written from the inlined sequence, read instruction by instruction:
  - LNode::DeleteNode, in 0x00afc960;
  - Lock/UnlockTileItemPool, in the allocator 0x0045cee0;
  - ac8parse::Input::Input and Big::SetState, in 0x00acc6c0;
  - V/PShader_bd12c0::DecRefCount, in 0x00b66f20;
  - Pass_bd12c0::DecRefCount, in 0x00b79930.
  - Neither D23 (a) nor (b) covers this case. Each recon.json says so ("accept": "bytes+inline"), and so does its
    README. If D23 should require fuzz instead, these 8 go back to the open list until tools/difffuzz runs here.
* **open, 23 traps** -
  - code-defmissing 15: rule or link classes, not missing bodies. The decomp defines every one of these, so a REC1
    body would redefine it under MSVC.
    - STAND1 deletes the tagged function itself when pragmas or a declaration sit between the `@fnv` tag and the
      body: 0x004ab230 BipedAnim::GetBoneNode, 0x0087ce70.
    - fnvlink adds no `__fnv_<addr>` alias for an existing native definition, e.g. 0x0069aee0: the object defines
      `BSSimpleArray<NavMeshInfo*,1024>` C1/C2 (an explicit `template<>` specialization), and sigs gives the same
      exact key on both sides.
    - MSVC adjustor thunks (`W..@` names), with no GCC counterpart: 0x00d26870, 0x00d65210, 0x00d6a890, 0x00d6a8a0,
      0x00dffb60. This is the thisadj class.
    - `??_G` 0x006b6390, and the others, need the link's own logs.
  - wrong decomp inline bodies 3 (an S2-bodies tree rule: make the member out of line, bound to its address):
    - `List62::Add62`, whose exe 0x00c62cc0 is an array Add, so NewItem and SetElement do not exist in the exe;
    - the `FUN_00a0ac80` constructor, whose exe 0x00a0ac80 builds a list, so Init does not exist in the exe;
    - `S00cb5100` FindItemForThreshold, a second static stand-in of 0x00cb3d10 that calls `pick`: wrong shape.
  - decomp harness scaffolding 2, not exe code:
    - getFlags_db0e50, called only by `CallTarget_db0e50`;
    - FUN_00cbbfe0_dummy, the entries of a fake vtable. Natively, MyCls::FUN_00cbbfe0 then stores that fake table
      where the exe stores 0x010c8efc: a real native bug for S1.
  - inline-only and not simple 2, need fuzz:
    - DynamicArray<LocalFrameRecord>::~DynamicArray, inlined in 0x00c7dc40;
    - NiTArrayA55<..SkinInfoA55..>::~NiTArrayA55, inlined in 0x00a54410.
* **fnvlink classes (tools/link, see recon6-002)** - conversion-to-pointer operators and unary operator* (82 names
  fleet-wide), plus functions returning a pointer to an array (`T (*f())[N]`: sig `special`). R6 closed its own names
  of these with alias bodies.
* **metric** - shard rows still without a body: 112 -> 23 (on the 127-row list; trap stubs 115 -> 23).
