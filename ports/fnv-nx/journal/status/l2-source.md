# L2 SOURCE status (lead: Opus 5.5 session, branch claude/fnv-l2-source)

Handoff read: d79467e6 (journal/inbox/l2-source-handoff-opus.md). Trunk CLAUDE.md taken at a565971.

## Tasks
| # | Task | Worker | Status |
|---|---|---|---|
| 1 | /Z7 tree hash: rule `canon-recon-1` (tree_hash.sh of the tree with the FNVNX_RECON path replaced by `RECON/`), pin = C from the first farm with A=42a47a07ddaab6b0, B=94094fd8e6e1633f | z7 farms 1-4 (L4): session_01KmtZKSuxC7ks3kferbyC6D, session_01KmtZKS3a468SDWPQW1Drrm, session_01KmtZKU9urUtsHPMAyeK4X9, session_01KmtZKTm9tSnKho4mw4Ehyw | done: pin tree_hash = 5dd203b860b5cc52 (rule canon-recon-1, A 42a47a07ddaab6b0, B 94094fd8e6e1633f); shard 1 republished @ dd3e3088; all 4 shards published with that pin (fnv-nx-build msvc/z7/8f324ecde2a7/{1of4 @ dd3e3088, 2of4 @ 2c4f2f2d, 3of4 @ 276fc311, 4of4 @ 2b8ec9cd}): 2,988 units, 2,949 built, 39 failed; L4 took the pin; L4 told |
| 1b | prepare writes no host path into a tree (RECON/<entry> in recon_rejected.tsv; post-check: no input-root path in any output file) | — | waits: not on the first-frame path (the fully lifted ELF takes no prepared decomp tree) |
| 2 | Plugins addr/dispatch/names/sdd/pi/layout/order/ftoi as recipe stages on msvcmodel | L1 w4 (recipe stage, merged on trunk e90a7e43); L2 w1 session_01KmtZKTcvH46vVSGgeKTGru, branch claude/fnv-l2-source-w1 | doing: trunk has older copies of all 7 L2 plugin sources; w1 brings the current ones and moves toolchain/tests/plugbuild.py (a direct g++-13 call) onto the recipe stages with msvcmodel |
| 2b | vtnames (clang plugin) | not started | waits on libclang-18-dev in env.json (w4 168006e9, not on trunk) |
| 3 | names to all units by rule | — | waits: not on the first-frame path (uncertified names go LIFTED; the first ELF is fully lifted) |
| 4 | 6 link test files onto msvcmodel objects from the recipe; negatives = byte mutations | w2 session_01KmtZKUiDYYhRvaS8TJ2kGu, branch claude/fnv-l2-source-w2 | doing (ELF side; COFF/CV/PE side blocked by the cl toolchain as a recipe external, L1) |
| 6 | Plugins on the first product root (platform/): every plugin diagnostic on its units is L2's to fix by rule | L5 writes the recipe/1 root; L2 w3 session_01KmtZKSTD6uibY3r7BhtJ7f, branch claude/fnv-l2-source-w3: census of plugin diagnostics on L5's one platform/build.json (L5 w10 session_01KmtZKTMZZBSRk7vPjUsu95 writes it; w3 writes no root) | doing: waits for w10's root or draft |
| 7 | abi_native/vslots minus-one destructor rule is dead under sdd (mandatory for every native root; tables hold dtor.sdd, a D0 in a table is a plugin compile error): delete the rule, make dtor.d0/d1 in a table a finding | — | waits: not on the first-frame path |
| 8 | cl failures of the /Z7 tree (shard 1: 7, shard 3: 7): source outside the VC9 language (decltype, in-class member initializers, C2660 calls with too few arguments, C2065, C2334, C2143) from the prepare; rule needed: the cl tree is emitted only in the VC9 language | — | waits: not on the first-frame path (certification only); 39 of 2,988 units, to be closed by one prepare rule (VC9 language only), not per file |
| 9 | /Z7 object naming is not injective: src/fallout/ai_baseprocess.cpp and src/fallout/ai/baseprocess.cpp both map to src_fallout_ai_baseprocess.cpp.obj (shard 4: 757 files for 758 units; shard 1 738 for 742 likely the same class); rule needed: object name = injective function of the unit path (e.g. unit index) | — | waits: not on the first-frame path |
| 5 | VC8 for cl u2713: installer toolchain/msvc/setup_msvc.sh (6892de7), mirror fnv-nx-build tools/msvc8 @ 5881374b0e69 | — | done 6892de7; u2713 /Z7 object to rebuild on a farm |

## Global numbers (from the handoff, not re-measured)
- names (tree 8f324ec, 2,978 units, gate cbe0177, plugin 6c5e16e, cl set 2,950 objects without /Z7): 73,568 rows, 62,210 certified (84.6 %); uncertified 11,358 = 11,174 in 12 cl-rejected units + 184 mismatches in 108 units.
- cl failures: 39 of 2,988 units (38 source errors, 1 VC8 driver, now builds).
- r7: 0 of 748,920 (objects without CodeView types); /Z7 shard 1 of 4 published (msvc/z7/8f324ecde2a7/1of4 @ a8ff7fd5, 742 built, 7 failed).
- test doubles in L2 tests: 0 files with a replaced module of 10 link test files (4f761b8); 6 of 10 feed gates hand-built objects.
- addr/order on the whole tree: no global number yet.
