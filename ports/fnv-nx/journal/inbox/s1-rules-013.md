### J-XXXX | 2026-11-08 | stream: S1-rules | status: active | parent: s1-rules-012

* **Classlayout census** (recon/classlayout 28448283):
  - D22-CL 774 edits in 502 files; D22-CL-KEEP 597; held 1,416 before the split.
  - ffb06f4c split the holds: BELOW (the declaration ends before the exe's dtor slot, already right), ASIS (its
    virtual ~X sits at slot k), DTORPOS, and HELD (the gates). Sample: HELD 21 -> 3.
  - Fallback rows: 11,937, marked `fallback:` in build/d22_applied.tsv.
* **R4's 13 VTFILL ABI holds** (reports/vtgate-r4 1163693):
  - 10 are ABI-identical. R4 releases them through the link's existing slot_abi check.
  - HKRO1 (2350bfc2): Havok hkReferencedObject gets getClassType / calcContentStatistics (exe slots 1 / 2) under
    GCC, in the 17 units that declare it with the destructor only. hkpWeldingViewer's native table now reads D1, D0,
    GetClassType, CalcContentStatistics (compiled and checked).
  - recon/s1_aabbtreeworldmanager_base (eb4a2e2): MyBase1 follows exe vftable 0x010cd078's 11 slots.
  - recon/s1_baseformcomponent_slots (489b0ac): the BaseFormComponent shell's four slots, and FUN_005a78a0's dtor at
    exe slot 4 (od + ctxr units).
* **full3 compile failure, magiccaster.cpp** (d7d4daeb): D22-CL converted MagicCaster's SpellCast (classlayout
  dtor_slot 9), which magiccaster.cpp calls unqualified. The header gate now also counts unqualified calls in the
  including files. I asked S2 whether slot 9 is really the deleting dtor.
* **LAYOUT gate**, tools/link/layoutgate.py (e9a38d15, f30b9315, 48c8fc56):
  - Method: the MSVC side is clang i686-msvc record dumps of the same prepared source, with MSVC's own SIMD types;
    the native side is GCC probes. Compared field by field, with bases and a cause column.
  - First measurement, 65 boot-set units (fnv-nx-build reports/layoutgate f6ba3707): 4,993 records, 4,851 probed;
    16 differing records, 0 differing fields.
  - pack 12: vptr classes under #pragma pack(1) (hkReferencedObject.h), MSVC align 1, GCC 4.
  - ebo 4: classes holding only the FNVNX_EBO2 filler, GCC size 0, MSVC 1.
  - Next: check whether those types are ever members at narrow offsets, then the whole tree.
* **START (farms)**: layoutgate.py over the whole prepared head (about 3,000 units, --jobs N), report to
  fnv-nx-build reports/layoutgate/<head>.tsv; later cl /d1reportAllClassLayout as the MSVC reference.
