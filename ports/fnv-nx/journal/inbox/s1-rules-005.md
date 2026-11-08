### J-XXXX | 2026-11-08 | stream: S1-rules | status: active | parent: s1-rules-003

* **D26 compile (wip/d26 276c577, head 179fd85)** - try1of1's 4a15e91c had 14 real errors. 12 were D26 and 2 were
  recon redefinitions.
  - The D26 ones were header edits of members that other files define or call. The generator's placeholder test is
    per file, so real members were listed: LoadBinary x7, GetType, GetPackageType, SetSortingCamera, Slot0.
    Fix: in a header, a D22 edit needs the member name unused in every other file of the tree (D22-EXTERN, 257
    withheld).
  - FNVNX_D22_CALL had rewritten a pointer-argument call. Fix: integer-literal flags only.
  - The 2 recon ones (hkmemutil CopyElementsBackward, blk_00460000_od FUN_0046ffd0) are recon bodies: they do not
    reproduce without FNVNX_RECON. Sent to R2.
* **verification**
  - On a local prepare of decomp 87eb9373 with the D26 rules and d22.json (no FNVNX_RECON), the D22 macros were
    unwrapped and the new rule re-run: D22 396, D22-CALLONLY 10,621, D22-DERIVED 92, D22-CALL 1, D22-EXTERN 257,
    D22-USED 570.
  - Full -fsyntax-only census, standin aarch64-linux-gnu-g++ 13.3.0: **2,978 / 2,978 clean, 0 errors**.
  - tests/s1 0 failed, tests/s11 ok.
* The ~2,480 empty-log failures on try1of1 were the farm's compile step (T1's compile.sh object wipe, fixed on
  integration). wip/d26 = 179fd85 merges it.
* **START** (unchanged from 003): full_job on the wip/d26 head (stub boot), then fuzz fz1..8 on it.
