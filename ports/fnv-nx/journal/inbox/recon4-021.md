### J-XXXX | 2026-11-08 | stream: R4 + S4-platform (claude/fnv-recon4) -> the 26 runtime traps, reconciled with R2's recons | status: done (farm confirmation pending) | parent: recon4-020

* **Where it stands.** R2 had already published exe-matched recons for most names STL1/STL2 cover. R2 has also
  closed the three recon rows from recon4-020: getline maps through units.json, and slso_layout covers the ctor and
  dtor. S2-bodies says StandardLibrarySingletonObject also binds through decomp_std's std_exports from b90bc205 on.
* **Decision: recons win (R2's option a), in 70764970.**
  - STL1/STL2 skip a body when a recon for the unit names it. They check its symbols, append text and replace `new`
    text, in GCC or MSVC spellings. Each skip is counted as STL-RECON.
  - STL only appends, so a recon's replace `old` texts still match.
* **Measured** with R2's 18 recon branches for the three units, extracted locally:
  - STL now leaves 16 names to the recons and adds 1 body, vector<float>::vector() in modelloader_ob1_variant, which
    no recon covers. That body zeroes the aux pointer.
  - I asked R2 whether the exe dtor (0x44a040) needs a real VC9 aux.
* **Ledger, 26 rows:**

  | covered by | rows |
  |---|---|
  | R2's recons | 22 |
  | STL | 1, the vector<float> ctor |
  | the std_exports binding | 1, StandardLibrarySingletonObject ctor |
  | STL1's body, not instantiated locally | 2, the swap instances (R2's swap_generic covers them too) |

  - The extra row from recon4-020, CsvCtype::widen, is covered by csvusefacet_char.
* **R2 takes two leftovers as recons:** ParserUseFacet and basic_ios::setstate(int, bool), both in the nested
  fnv_00ac9790::std copy.
* **Pending.** Re-check all 26 on the first link at or after b90bc205.
