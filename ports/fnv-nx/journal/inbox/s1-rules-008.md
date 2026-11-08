### J-XXXX | 2026-11-08 | stream: S1-rules | status: active | parent: s1-rules-007

* **D22-COM (2018b38, boot blocker, S5-boot)**: D26 (b) had given call-only stand-ins of COM interfaces a virtual
  destructor. On link 3f7a6c35e923, 0x00e7bc90's adapter-mode path read the IDirect3D9 at [vptr+108] / [vptr+72]
  where the exe reads +104 / +68, and boot faulted in Null_IDirect3D9::GetAdapterDisplayMode. D22 / D26 now leave alone
  any class with a `__stdcall` / STDMETHOD / WINAPI-style virtual, an HRESULT / REFIID virtual, a virtual
  QueryInterface / AddRef / Release, or an SDK interface name. A COM-shaped derived class keeps its re-declaration.
  Census (prepare of the decomp at the integration's commit, against prep_f = d3e94d2): running; the D22-COM and D22-CALLONLY totals follow in the next note.
* **D26 (b) gaps from S5-boot**:
  - NiTMap_S (actor.cpp) was held back by CVShell's unqualified call of its own CVB::v0. An unqualified `ph(` now
    blocks a class only inside a member of that class or of a class deriving from it.
  - BGSOpenCloseForm (modelloader_activator_declarations.h) declared a body-less, non-virtual `~C();`. It converts
    now, and the `~C();` goes under GCC (D22-DTOR).
  - NiTMapC01 / NiTMapD53 were already converted at d3e94d2 (S5 had an older build).
  - Not heuristic, left open: defined classes with a placeholder slot 0 (BSNiAllocator defines Destroy, Heap has a
    single method0) and call-only classes whose slot 0 has a name (ObjectRegistry Reset / Register: several unrelated
    classes share that name). Both need the exe's slot 0 confirmed. The route is a fnvnx-d22/1 list under FNVNX_RECON:
    the listed branch of the rule already converts named members. I asked S5 for (file, class, member, vftable) rows.
    BSNiAllocator also needs its Destroy as the D0 body (separate rule, queued).
* d3e94d2's object check: compiled the 88 units it changes with and without the commit. No new undefined D0 / D1 / D2
  / _ZTV references. The one hit was a unit whose old object did not build; `_ZTI8NiObject` is defined in the link.
* **START (farms)**: boot try + fz1..fz8 on the integration head once 2018b38 is adopted.
