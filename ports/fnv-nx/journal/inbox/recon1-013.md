### J-XXXX | 2026-11-07 | stream: R1-recon (claude/fnv-recon1) | status: wip | parent: J-XXXX (recon1-012)

* **reconciliation with S1-rules' triage** (fnv-nx-build reports/s1-void a1dac8e) - S1 judged the callee side: whether
  eax is set before every ret. R1 judged the caller side: whether a caller reads eax after the call. All 255 rows R1
  rejected were re-checked. The direct call sites were walked again, following unconditional jmps. When eax reaches
  the caller's own ret, the enclosing recipe function's decorated return type decides.
  - The 140 rows with no direct call are referenced only as data: vftable slots or function tables. Their
    references in the exe are dwords in .rdata/.data. The decomp has no value-returning code reference for them, so
    there is no evidence of a value-reading caller.
  - That leaves 25 new candidates: 2 where a caller reads eax after a jmp, and 23 where a caller hands eax on to its
    own caller. All 25 were read by hand in the exe.
    - 4 are real and now fixed (GCC-only REC1-REPL, try.py all-equal, gcc tail-returns the value):
      - 0x00d943d0, 0x00d94c40, 0x00d947e0: Havok job handlers that return LunaOwner::ProcessCallback's status
        (0x00d04e80). The dispatcher 0x00d01e50 returns it. Each is on its own `recon/<unit>_void` branch.
      - <va>: returns FUN_006603a0's end pointer, which itself returns CopyConstructRange's. Added to
        recon/staging_blk_00660000_od_void (11 pairs).
    - 21 are false. The enclosing caller is a void wrapper or thunk, eax at the join is reset (`xor eax, eax`), or
      the enclosing function returns float in ST0. Several enclosing decomp declarations (`int`/`_N`/`PAX`) are
      themselves wrong: 0x00a92ef0 NiTextureEffect::UpdateWorldData, 0x0054ede0, 0x006bec20, 0x00d2ec30.
  - S1's 206 "call-only" rows: in 182 of them no caller uses the value, directly or one level up. GCC's tail call
    therefore does not matter there.
* **for S5 (one line)** - tests/s5/void_defs.py should count code references only. 128 of its 363 hits take their
  "value-returning reference" from a data symbol (`?DAT_...@@3HA`, a function address the decomp treats as an int).
* **metric** - void-class real cases: 93 (89 + 4 found here), all fixed. Open: 0.
