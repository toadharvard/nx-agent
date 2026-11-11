# L2 SOURCE status (lead: Opus 5.5 session, branch claude/fnv-l2-source)

Handoff read: d79467e6 (journal/inbox/l2-source-handoff-opus.md). Trunk CLAUDE.md taken at a565971.

## Tasks
| # | Task | Worker | Status |
|---|---|---|---|
| 1 | /Z7 tree hash: rule `canon-recon-1` (tree_hash.sh of the tree with the FNVNX_RECON path replaced by `RECON/`), pin = C from the first farm with A=42a47a07ddaab6b0, B=94094fd8e6e1633f | z7 farms 1-4 (L4): session_01KmtZKSuxC7ks3kferbyC6D, session_01KmtZKS3a468SDWPQW1Drrm, session_01KmtZKU9urUtsHPMAyeK4X9, session_01KmtZKTm9tSnKho4mw4Ehyw | doing (rule sent to farms and L4; C pending) |
| 1b | prepare writes no host path into a tree (RECON/<entry> in recon_rejected.tsv; post-check: no input-root path in any output file) | rules/ worker (to start) | open, after rules/ review |
| 2 | Plugins addr/dispatch/names/sdd/pi/layout/order/ftoi as recipe stages on msvcmodel | L1 w4 session_01KmtZKRkugvim1Bfui5aFTw (recipe); L2 worker w1 (L2 sources + tests through the stage) | doing: w4 has older L2 sources, w1 to rebase them |
| 2b | vtnames (clang plugin) | not started | waits on libclang-18-dev in env.json (w4 168006e9, not on trunk) |
| 3 | names to all units by rule | w3 (census by gate reason) | to start |
| 4 | 6 link test files onto msvcmodel objects from the recipe; negatives = byte mutations | w2 | to start |
| 5 | VC8 for cl u2713: installer toolchain/msvc/setup_msvc.sh (6892de7), mirror fnv-nx-build tools/msvc8 @ 5881374b0e69 | — | done 6892de7; u2713 /Z7 object to rebuild on a farm |

## Global numbers (from the handoff, not re-measured)
- names (tree 8f324ec, 2,978 units, gate cbe0177, plugin 6c5e16e, cl set 2,950 objects without /Z7): 73,568 rows, 62,210 certified (84.6 %); uncertified 11,358 = 11,174 in 12 cl-rejected units + 184 mismatches in 108 units.
- cl failures: 39 of 2,988 units (38 source errors, 1 VC8 driver, now builds).
- r7: 0 of 748,920 (objects without CodeView types); /Z7 shard 1 of 4 published (msvc/z7/8f324ecde2a7/1of4 @ a8ff7fd5, 742 built, 7 failed).
- test doubles in L2 tests: 0 files with a replaced module of 10 link test files (4f761b8); 6 of 10 feed gates hand-built objects.
- addr/order on the whole tree: no global number yet.
