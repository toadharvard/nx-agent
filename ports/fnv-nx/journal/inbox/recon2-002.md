# recon2-002 | 2026-11-07 | stream: R2 (recon fleet, D23) | status: active | parent: recon2-001

* **metric** (shard-1 trap stubs without an accepted body or binding): 158 -> 91 (67 closed).
* **published** on fnv-nx-build (fnvnx-recon/1, REC1), every unit rebuilt with build_and_match against the owner's
  exe, all functions and bytes equal:
  | branch | kind | stubs |
  |---|---|---:|
  | recon/bscore_zerooverheadheap | (a) bytes, 1 body (linker-folded empty /Od member) | 13 |
  | recon/staging_blk_00640000_od | (a) bytes, 2 bodies | 9 |
  | recon/havok_hkquaternion | (a) bytes, 1 body | 15 |
  | recon/gamebryo_nifile | (a) bytes, 2 bodies | 2 |
  | recon/staging_blk_00ac0000_od_ob1_variant | binding, 16 names | 16 |
  | recon/staging_blk_00b00000_od_ob1_variant | binding, 9 names | 9 |
  | recon/bsmenu_debugtext_ob1_variant | binding, 2 names | 2 |
  | recon/havok_hkpcontinuoussimulation | binding, 1 name | 1 |
* **a/b split**: (a) bytes 39 stubs (6 bodies); (b) fuzz 0; binding 28.
* **new acceptance kind `binding`** (decision, please veto if wrong): 28 no-body names are not missing code. The decomp
  declares them under its own names (stringCopy, GetCharTable38, SeekString::append, ...) and the exe calls a function
  that a unit already defines or that S4 provides (crt_symbols.tsv). recon.json then carries only `symbols` (REC1
  adds them to the referencing unit's units.json, the link binds by `unit.refs`) and `"accept": "binding"`. Evidence:
  calls followed from placed functions locate the referrer in the exe, S2's call-order alignment gives the target, and
  the target is checked for shape (member or free, `ret N` = argument bytes, result pointer). 11 candidates were
  rejected on shape.
* **blockers / classes for others**
  - S2-bodies: hkpcontinuoussimulation.cpp defines 0x00d0f300 twice. The placed overload (Limits, ...) byte-matches;
    the unplaced one (EventQuery, ...) is what the placed caller 0x00d100a0 calls by name, and it alone references
    22 helpers that have no exe function (21 still trap). Class: an unplaced duplicate definition of a placed
    address. Binding the duplicate to `__fnv_00d0f300` closes them all; that is a binding rule, not a recon body.
  - S4: the exe calls 0x00ec3740 (`_Locinfo_ctor`) with a `const std::string&` (the byte-matched 0x00ad3ca0 does
    this), but crt_symbols.tsv declares it `(_Locinfo*, const char*)`. Left unbound (InitLocaleInfo38, GvLocinfo,
    DestroyLocaleInfo38) until the provider's signature is checked.
  - fuzz (D23 b), next: the 4 NVAPI rows (0x009fea2a lazy loader with pushal/popal, 0x009fead6 / 0x009feae8 /
    0x009febd8 `jmp [slot]` thunks of the nvapi_QueryInterface table). Hand-written assembly, so MSVC byte parity is
    out of reach. The native body is "nvapi.dll absent": init returns -2, thunks return the not-initialised status.
  - 0x00402d40 / 0x00402d60 / 0x00402d70 (BaseFormComponent Init / CopyFromBase / CompareWithBase, extern-text gref):
    these addresses are inside 0x00402d50's body, so the decomp's addresses are wrong. The real targets come from
    the BaseFormComponent vftable slots (next).
* **next**: the 63 unresolved no-body rows (BasicReadBuffer virtuals the exe calls through the vptr: S2's vcall
  class; parser839 tree/string members; ProbePool, hkpSphereCapsuleAgent), then the fuzz path.
