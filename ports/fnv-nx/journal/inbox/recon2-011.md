# recon2-011 | 2026-11-08 | stream: R2 (recon fleet, D23) | status: active | parent: recon2-010

* **guest link blocker** (full1 4a15e91c967a, 1fe1e1463be8): 11 multiple definitions of `__fnv_<a>`.
  - Cause: recons spell some MSVC adjustor thunks for GCC as extern "C" `__fnv_<a>`, and fnvlink's R6 adjustor-thunk
    bridges emitted the same names.
  - Fix, 3c4151e7 (adopted): fnvlink skips adjustor and `??_G` stubs whose `__fnv_<a>` a unit already defines.
    Counted in stats `adjustor_thunk_unit_defined`.
* **top-down order** (owner order via the orchestrator, 05:00): s2d/list/d1ed15e74620/topdown.tsv (b2037bfd),
  INFLATED, method in TOPDOWN.md.
  - Rank: stub-boot hits, then shortest depth from `__fnvnx_guest_entry` in the guest ELF's static reference graph.
    - code edges: bl/b, adrp+add/ldr with GOT slots;
    - data edges: pointers from .rela.dyn and .relr.dyn;
    - roots: .init_array;
    - cut: the registries fnvnx_addrmap, fnvnx_xc_table and stubs.
  - 1,295 of 1,873 stubs are reachable; 2 were hit and are still stubs.
  - Top 50: s2-bodies 43 (sent to S2-bodies-2), recon 7.
  - Tools: tools/link/topdown.py and boothits.py (77470cf5). mklist.sh publishes topdown.tsv with every list from
    now on.
  - Gap: exe-vtable calls are bound at boot, so the static graph does not see them; they appear only as boot hits.
* **recon rows of the top 50**, checked against the head tree (all recons applied, game GCC flags):
  - closed at head, inflation of d1ed: __fnv_00a8a200 (nimainsdm.cpp defines it), __fnv_0087ce70
    (taskqueueinterface.cpp), __fnv_0087d1b0 (bsscraparray_h_unit.cpp defines the BSScrapArray ctor),
    __fnv_00a167c0 (CreateFontManager: font.cpp, d1ed's one failed unit, compiles since 1172491f).
  - __fnv_00b90180: MSVC adjustor thunk (`sub ecx, 0x6c; jmp 0x00b90150`, placed). It is in the R6 adjustor-stub
    class, so it closes on a link with the current fnvlink.
  - FUN_009fea2a (NVAPI lazy loader, pushal/popal): fuzz-needed, as reported in recon2-002.
  - MemoryManager::Deallocate(void*) (rank 4): bindings to 0x00aa4060 for the 6 referencing units.
    - Evidence: 113 units map that decorated name to 0x00aa4060 (thiscall, ret 4, placed). The referrers are class
      operator delete / ??_G, which MSVC inlines in the exe.
    - Branches: recon/{bsmenu_menu, bsmenu_tilehelpers, bsshader_waterfft, bssu_netconsole, niconsolerenderer_rtm,
      blk_00ec0000_ob1}_dealloc. Every unit builds all functions and bytes equal.
    - niconsolerenderer declares it returning void*; its only callers, operator delete, ignore the result.
* **next**: recon rows of topdown.tsv in order; re-rank on the first full1 link that has a guest ELF and compiles
  2,978/2,978. Farm status: full1 c46cd30231d9 (04:38) and try1of1 wip_d26-179fd85a5b11 end at compile.sh exit 1.
