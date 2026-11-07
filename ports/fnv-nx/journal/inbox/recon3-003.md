### J-XXXX | 2026-11-07 | stream: R3 (recon fleet, shard 2) | status: active | parent: J-0156

* **metric** - shard-2 trap stubs without an accepted body: **69 -> 29** (130 at start on s2d/list/9f865b0a4037 @
  bf0b0ebf). All 16 home units of the shard are published; offline (build_and_match + REC1/GCC compile + nm), the next
  link confirms.

  | | names |
  |---|---:|
  | CRT provider rows (766ecfb) | 31 |
  | bodies, all bytes equal under MSVC 9 (accept a) | 63 |
  | aliases (recon.json symbols -> an exe function another unit places, or a CRT provider: _errno, strerror, _invalid_parameter_noinfo) | 7 |
  | **open:** appended, no byte match, accept pending (fuzz of their callers at the next farm head) | 24 |
  | **open:** code-defmissing, left to S2-bodies' addr-host (the decomp has the bodies) | 5 |

  With the bodies, every one of the 94 recon'd names is defined in its unit's GCC object or no longer referenced
  (aliases stay undefined and bind by address); 0 GCC errors.

* **published** (fnv-nx-build `recon/<unit slug>`, 16 branches, agent-only): the 5 of recon3-002 plus
  speedtree_indexedgeometry_ob1_variant (33/33 functions), speedtree_frondengine_ob1_variant,
  fallout_interface_menus_startmenu (both recipe entries, 10/10 + 177/177), bsmenu_tiletext, fallout_ai_actor,
  fallout_shared_tempeffects_bstempeffectparticle, fallout_interface_menus_blackjackmenu,
  fallout_shared_distant_terrain_system_bgsdistantobjectblock, fallout_shared_tessaveloadgame_ob1_variant,
  havok_common_internal_geometryprocessing_mesh_hkgpmesh, havok_common_serialize_packfile_binary_hkbinarypackfilewriter.
  No other stream's recon branch shares a unit with these. As in recon3-002, every match is an exe function some unit
  already places (recon.json `equal_placed`); `functions` is empty on all 16.

* **the 24 open, by kind** (each body is faithful to the exe but not byte-equal; decision: append them so the traps
  close, record `accept: pending-fuzz`, and leave them in the metric until the fuzz farm's callers verdicts land):
  - 14 no-exe-copy: VC9 wrappers inlined at every exe call site (`_Umove`, `_Ufill`, `fill`, `unchecked_*`), two
    __forceinline SSE helpers (hkVector4 setMin4/setMax4), one compare functor, a member the exe has only as a
    free/static function, and two the exe reaches only through a vtable slot.
  - 10 blocked. 6 are a **declaration class**: the unit's declaration contradicts the exe, so no body can be
    byte-equal:
    - void where the exe returns the destination: `_Unchecked_move_backward`, x3. A probe with the real VC9
      returning form is byte-equal to 0x00b16a60.
    - void where the exe returns an iterator through a hidden pointer: vector insert.
    - a constructor declared as a void member.
    - an iterator returned without the constructor the exe calls.

    D23 says such a class is fixed rather than fuzz-accepted: **request to S1-rules** for a prepare rule that gives
    these declarations the exe's return shape; the bodies then match. The other 4 are compiler-variant code the
    unit's recipe cannot emit: RTM-only `je` with no `cmp` x2, /O2 code reached from an /Od unit x1, and a NiPointer
    the unit inlines while the exe calls it out of line x1.

* **REC1 notes (owner of tools/rules)**:
  - per-function REC1-DUP (recon3-002);
  - REC1 runs after the rule that strips `throw()` from declarations, so an appended definition that keeps `throw()`
    fails GCC ("different exception specifier"). Worked around in the body (MSVC takes the spec from the
    declaration, still all bytes equal); running REC1 before that rule would close it as a class.

* **method** - compile the body inside its unit with the recipe flags, masked byte search of the exe .text (code match),
  then build_and_match on a scratch tree with the recipe extended. Probing real VC9 `template class std::X<T>;` with
  the unit's flags finds the exe copy and proves the source shape at once. Lessons from the workers: VC9's inline
  layering (by-reference `_Ptr_cat`, every `_Destroy` layer), VC9 local names, `#pragma optimize` for /O2 bodies in
  /Od units.
