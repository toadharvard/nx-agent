### J-XXXX | 2026-11-08 | stream: S1-rules | status: active | parent: s1-rules-009

* **Census at 6a4673b with the main and s5 lists**: D22-CALLONLY 10,930, D22 459, D22-USED 337, D22-COM 627.
  build/d22_applied.tsv has 11,488 rows: callonly 10,930, listed 459, derived 99.
* **RAWVT1 + exe order (b63ba08)**: RAWVT1 skips classes whose table the link keeps in exe order. It reads S2's
  exe_order_classes.tsv from FNVNX_EXE_ORDER or under FNVNX_RECON. Matching is by simple name; S2 is adding the shell
  names from units.json.
* **_d22_classes.py --rtti (1f03309)**: pairs a placeholder class with the primary vftable the image's RTTI names.
  - Output: fnv-nx-build recon/d22_vtable_dtor rtti/d22.json (6cc78fc), 57 classes. Two are held back: `S` (a
    one-letter name) and basic_streambuf<> (libstdc++ vtables).
  - RTTI is positive evidence only. The deleting-dtor shape test has false negatives (0x00a66350, 0x00625f80), and a
    base's own vftable (TESForm 0x0101c794, empty slot 0) says nothing about the derived objects a call-only stand-in
    receives. The 373 call-only rows whose RTTI slot 0 "is not a dtor" are therefore not acted on.
* **R2's registry recons**: REC1's replace pairs run before D22, and after them each class declares `virtual ~X()`, so
  D22 skips it. Checked with both ObjectRegistry rows put back: no double conversion. The two rows stay out of s5
  anyway, since R2's recon is their conversion.
* **EQ comcheck**: 5 of 5 COM +4 sites are covered by D22-COM, a header (DepthDevice) included.
