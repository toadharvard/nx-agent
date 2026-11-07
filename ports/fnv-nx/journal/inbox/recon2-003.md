# recon2-003 | 2026-11-07 | stream: R2 (recon fleet, D23) | status: active | parent: recon2-002

* **list**: re-pulled s2d/list/9f865b0a4037 at bf0b0ebf (orchestrator). Shard 1 is now 163 trap stubs: +5
  code-defmissing, none dropped.
* **metric**: 163 -> 53 (110 closed). Since recon2-002: +43 (4 workers in parallel plus R2's own rows).
* **a/b split of the 110**: (a) bytes 62 (bodies plus byte-equal referrers); (b) fuzz 0; binding 48 (vetted as in
  recon2-002). Every published recon rebuilds its unit with all functions and bytes equal.
* **new branches** (fnv-nx-build): recon/fallout_spectatorpackage_nvapi (bytes; NVAPI thunks 0x009fead6 /
  0x009feae8 / 0x009febd8), recon/staging_blk_00ac0000_od_ob1_variant_{crt,readbuf,bind2,parser839},
  recon/staging_blk_00b00000_od_ob1_variant_crt, recon/staging_blk_00c80000_od_o2_nood_variant,
  recon/bsmenu_debugtext_ob1_variant_2, recon/fallout_shared_teswater_o2_variant, recon/fallout_shared_tesrace,
  recon/staging_blk_00640000_od_006592f0.
* **held back** (format `fnvnx-recon-pending/1`, so REC1 skips them, and not counted): recon/havok_hkgpmesh_ehsc
  (ProbePool, 4 rows) and recon/havok_spherecapsule_gs (~hkpCollisionAgent). Neither byte-compares any exe function:
  the code is inlined into referrers that do not match with the current decomp text.
* **flag for veto**: the parser839 recon contains Tree::eraseTree. Its own code is instruction-for-instruction the
  same as 0x00acf0b0, but the frame is 0x0c where the exe has 0x34. Not counted as closed.
* **the remaining 53, by class (owner suggestion)**
  - 21, S2-bodies: hkpcontinuoussimulation.cpp defines 0x00d0f300 twice; the unplaced overload carries the helpers.
    A scan of the pristine objects finds 681 FUN_<addr> definitions, in 255 units, that are not the recipe symbol
    placed at their address. That is an upper bound: it includes legitimate views of the same address.
  - 5, S2-bodies addr-host: code-defmissing rows that are MSVC this-adjustor thunks (`W..`) and one `??_G`. units.json
    already places them, so REC1 would skip them as REC1-DUP.
  - 3, F2 / S2c-vt: BaseFormComponent Init / CopyFromBase / CompareWithBase are pure virtuals in the exe (vftable
    0x01011d68, all _purecall). The decomp's component classes in effectsetting.h lack their overrides and extra slots.
  - 3, S4: the exe's 0x00ec3740 takes `const std::string&`; the provider takes `const char*` and works only for
    names that fit the string's inline buffer (InitLocaleInfo38, GvLocinfo, DestroyLocaleInfo38).
  - 13, decomp class (S1 / S2-bodies): the basic_ios members (InitBasic, SetState) sit on the stream class instead of
    the virtual base; GvCtype is too small (no _Ctypevec); stringbuf_stub::_Init is modelled wrong; the parser839
    Text / StringBuf / ConstIterator lack inline bodies (Ios ctor, Input ctor, key, Less); hkpSphereCapsuleAgent's
    base has no ctor; SolverMotionReport and consumeRay are placeholders over the real 0x00d23f50 / 0x00dd1f30.
  - 8, fuzz (D23 b): the NVAPI loader 0x009fea2a (pushal/popal); S5540 _Ufill / _Ucopy; the 5 held-back rows above.
    Prerequisite, all streams: difffuzz has no oracle mode for code that exists only in the exe (no MSVC object),
    and this container has no AArch64 ILP32 toolchain. This is farm work (START line below when wanted).
* **pipeline check for S1/S2**: tesrace's NiPointer<BSFaceGenModel>::operator T*() was already mapped in units.json
  (87eb9373, 0x00559450) and was still a trap stub. Other units' units.json-only mappings may not reach the guest
  either.
* **next**: R2's own queue is empty of rows that an appended body or binding can close. The rest needs the class
  fixes above or the fuzz oracle.
