### J-XXXX | 2026-11-08 | stream: S1-rules | status: active | parent: s1-rules-008

* **Census of 2018b38 (D22-COM)**, a full prepare against prep_f (d3e94d2):
  - D22-COM 627 edits in 160 files; D22-CALLONLY 10,813 -> 10,826; D22-USED 570 -> 350; D22-DTOR 13; D22-EXTERN 17.
  - Converted classes with a `__stdcall` virtual: 259 -> 0.
  - The 13 converted classes still matching "Release(" are non-virtual refcount helpers, not COM.
* **Decision (orchestrator, 2026-11-08)**: the link keeps "every pair-less table is minus-one" and excludes only an
  exe-verified, directly defined slot 0, plus COM. D26 (b) stays as it is. S2-bodies implements the link side; it
  reads d22.json under FNVNX_RECON and prepare's build/d22_applied.tsv (00d2087).
* **Integration merge e3d943f**: both sides kept (REC1 / recon_retarget / _UNIT_PLACED, VFTBL1 + VLIT2, STAND7).
  s11's one failure is the integration's own: duplicate ids STL1 / STL2 in s4_stl.py (S4).
* **6a4673b**: a qualified call whose receiver resolves (`((T*)x)->ph(`, `((T*)x)->m.ph(`) to a class outside the
  family is not a use. extradatalist.cpp: D22-CALLONLY 8 -> 15, D22-USED 8 -> 0; AX converts.
* **00d2087**: numbered placeholders `P_00, P_01, ...` (InitHolder's FUN_00aa2020_Impl). Also build/d22_applied.tsv.
* **c9aafda RAWVT1**: a raw exe slot index k > 0 on `this` (`(*(void***)this)[k]`) in a non-COM member becomes
  FNVNX_D26_SLOT(k), i.e. k + 1 under GCC and k under MSVC. FUN_005e0830::Open (fallout.ini never read) now loads
  [vptr, 28]. 61 sites; 1 slot-0 call is held. About 170 raw indices on other receivers stay: their type is unknown.
* **fnv-nx-build recon/d22_vtable_dtor s5/d22.json** (39c4065, 126cea0): S5-boot's exe-verified rows.
  - The rows: ObjectRegistry 0x0104e6ac, fnv_00632ee0::ObjectRegistry 0x01018508, AX 0x0106ba6c,
    FUN_00aa2020_Impl and FUN_00aa2230_BSNiAllocator 0x010a2528, Heap 0x010a2884, RegSettingCollection 0x01082ed8.
  - RegSettingCollection's slot 0 0x0044f620 is checked here: flags test, ret 4.
  - BSNiAllocator stays exe order: its slot 0 is a defined Destroy. The link's exe-order-no-evidence rule covers it,
    so the D0-body rule is not needed while that holds.
* Open, S2-bodies' side: the 7 classes with a real pair against a non-dtor exe slot 0 (the decomp's own
  destructors). Also the Havok shells with getClassType at native slot 2 and no pair: no declaration makes them, and
  no prepared object defines those vtables, so they look link-made.
* **START (farms)**: boot try and fz1..fz8 on the integration head after c9aafda is adopted, with FNVNX_RECON
  including recon/d22_vtable_dtor (s5/).
