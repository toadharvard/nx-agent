### J-XXXX | 2026-11-08 | stream: S1-rules | status: active | parent: s1-rules-011

* **Classlayout (orchestrator: the exe decides)**: D22-CL (92099e4, 91d6c0b). The D22 / D26 declaration rule reads
  S2's classlayout.tsv first: fnvnx-classlayout/1, agreed with S2, on fnv-nx-build recon/classlayout (28448283).
  - The file has 3,269 rows. The 591 secondary-vftable rows are skipped.
  - exe-order / com: the declaration is kept (RAWVT1 too). minus-one k: `virtual ~C()` goes at the k-th virtual,
    whatever its name.
  - Classes without a row use the name heuristics, marked `fallback:` in build/d22_applied.tsv.
  - Census with the table: in progress.
* **EQ G3, VSPLIT1 (0d7f2e6)**: one declaration per virtual declarator, before D22. 10,709 declarators in 57 files.
  All 143 changed units compile (115 at first; the 28 others failed only on missing shim macros under another
  checkout's shim, and 29/29 then compiled with this branch's shim).
* **EQ G1 (0d7f2e6)**: numbered placeholder sequences (`_v01, _v02`, `d000, d001`, step-4 `v000, v004`).
* **S2 V3 / V4**:
  - D22-INLINE (4941302): a placeholder base whose derived class defines members gets an inline empty ~C. 298 edits.
  - D22-EXEORDER: classes the link keeps in exe order are not converted.
  - a0a7a89: per-file dtor_slots rows.
* **Boot: FloatObjectRegistry (S2 W45 / orchestrator)**: R2's recon/ctor_719c00_registries_w1 converts it at head
  (the full chain checked). That guest's prepare evidently did not apply the recon; T1 is checking the farm's
  FNVNX_RECON. Backstop: all five registry classes are listed in recon/d22_vtable_dtor s5/d22.json (f20f5d9). Checked
  both ways: with the recon REC1 converts them and D22 skips them; without it D22 converts them.
* **Census at 92099e4 (main + s5 + rtti + slots lists, no classlayout)**: VSPLIT1 10,709; D22-CALLONLY 10,927;
  D22-INLINE 298; D22-SLOTK 213 (held 343).
* **START (farms)**: link + boot on integration with claude/fnv-s1-rules HEAD adopted and all recon/* (classlayout,
  d22_vtable_dtor, the R2 and S1 recons).
