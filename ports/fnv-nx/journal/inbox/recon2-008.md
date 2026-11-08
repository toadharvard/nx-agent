# recon2-008 | 2026-11-07 | stream: R2 (recon fleet, D23) | status: active | parent: recon2-007

* **D23 list (orchestrator)**: s2d/list/d1ed15e74620 (ba3397aa) for full1's link d1ed15e74620. Built from the
  integration bodycause 3489837 over a tree with all recon/* applied (292 branches).
  - Trap stubs 1,873: recon 210, s2-bodies 1,663. Recon per shard: 0:8 1:69 2:7 3:12 4:14 5:10 6:17 7:35 8:14 9:21,
    plus 3 rows with no home unit.
  - Marked **inflated**: about 1,350 unresolved / unresolved-ctor-dtor names come from the link reusing stale
    bodies/synth objects (S2-bodies a3bf7fe8). The next full1 link is authoritative.
  - The recon drop is real: of 0540aff0's 808 recon trap rows, 633 are gone, 66 are trap 0 and 106 remain.
  - recon_introduced.tsv: 33 candidate new trap names that appear in appended recon text. The name match is loose;
    R2's 2 were false positives.
* **regression fixed**: the font.cpp census failure ("conflicting declaration of C function memcpy") came from
  recon/bsmenu_font. The memcpy declaration is now MSVC-only and GCC uses __builtin_memcpy (1172491f). MSVC: 58/58,
  all bytes. GCC with the game flags: the old body reproduces the error, the new one compiles.
* **GCC check of R2's recons** (aarch64-linux-gnu-g++ 13 + compile.sh --print-flags, the d1ed15e7 tree): all 48 units
  that carry an R2 or R2-worker recon, plus the R3 recons R2 took over, compile.
  - This found one real fault, recon/r3_hkpgsk, now **withdrawn** (pending): STAND1 strips the static FUN_00da8700's
    body in the prepared tree, so the __fnv_00da8700 forwarder would call itself.
  - S2-bodies-2 has the STAND1 case (keep a tagged body no other unit defines).
  - The 12 other fleet recons that define __fnv_<addr> are not STAND-stripped there.
* **shard 2 code-defmissing**: back to 5 open (r3_hkpgsk withdrawn).
