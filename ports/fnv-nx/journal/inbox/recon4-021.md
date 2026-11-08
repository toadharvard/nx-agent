### J-XXXX | 2026-11-08 | stream: R4 + S4-platform (claude/fnv-recon4) -> the 26 runtime traps, reconciled with R2's recons | status: done (farm confirmation pending) | parent: recon4-020

* **Where it stands.** R2 had already published exe-matched recons for most names STL1/STL2 cover. R2 has also
  closed the three recon rows from recon4-020: getline maps through units.json, and slso_layout covers the ctor and
  dtor. S2-bodies says StandardLibrarySingletonObject also binds through decomp_std's std_exports from b90bc205 on.
* **Decision: recons win (R2's option a), in 70764970.**
  - STL1/STL2 skip a body when a recon for the unit names it. They check its symbols, append text and replace `new`
    text, in GCC or MSVC spellings. Each skip is counted as STL-RECON.
  - STL only appends, so a recon's replace `old` texts still match.
* **Measured** with R2's 18 recon branches for the three units, extracted locally:
  - STL now leaves 16 names to the recons. The 17th, vector<float>::vector() in modelloader_ob1_variant, is bound by
    the unit's units.json symbols to 0x00449fd0, the exe ctor that allocates VC9's aux. STL now skips such names too
    (STL-BOUND, top-level std symbols only).
  - With the recons present, STL adds nothing to the three units. STL1/STL2 stay as the fallback for units that have
    no recon or binding.
* **Ledger, 26 rows:**

  | covered by | rows |
  |---|---|
  | R2's recons | 25 |
  | the unit's own units.json binding | 1, the vector<float> ctor (0x00449fd0) |

  - The 25 recon rows: the 17 SpeedTree helpers (the 2 swaps through swap_generic), tesobject rows 1-5, the
    StandardLibrarySingletonObject ctor and dtor (slso_layout; the ctor also binds through std_exports), and
    ~vector<float>.
  - The extra row from recon4-020, CsvCtype::widen, is covered by csvusefacet_char.
* **The two leftovers in the nested fnv_00ac9790::std copy are closed by existing bindings (R2):**
  - basic_ios::setstate(int, bool): units.json binds it to 0xaca4e0. It resolves as code to blk_00ac0000_od's native
    definition, and the ABI agrees.
  - ParserUseFacet: a global name that units.json binds to 0xad38d0. Its only referrer, ParserIos::widen, is never
    instantiated, so the link has no row for it.
  - My local nm -u lists both only because a lone unit is not linked.
* **Pending.** Re-check all 26 on the first link at or after b90bc205.
