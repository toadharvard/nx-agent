# L1 CONTRACT status (lead: Opus 5.5, session started 2026-11-11 22:34Z)

Branch `claude/fnv-l1-contract`. Trunk measured against: `claude/fnv-nx-clean` 58a79b3e (a3e9f0a9 + CLAUDE.md), 22 gates, 0 product roots.

## Tasks

| # | Task | Worker session | Branch | Status |
|---|---|---|---|---|
| 1a | merge queue via MERGER: w8 exepin | (predecessor) | claude/fnv-l1-contract-w8 a6753733 | doing: queued at MERGER session_01KmtZKSxPHjVrmy1iFKiW3g |
| 1b | merge queue: w4 msvcmodel compiler + plugins (= item 3) | session_01KmtZKRkugvim1Bfui5aFTw | claude/fnv-l1-contract-w4 bc406ec0 | doing: verdict re-run after 2 red tests (test_plugins, test_recipe; disk full) |
| 1c | merge queue: w5 python pins / TMPDIR | session_01KmtZKRw5sTxarSdSFC6Njj | claude/fnv-l1-contract-w5 db15c7ac | doing: 4 TMPDIR-leaking tests red |
| 1d | merge queue: L5 imports + machine f15935a9 | L5 | - | queued at MERGER |
| 2 | incremental verdict speed (S0 cold, S1 no change, S2 comment header, S3 one .cc, S4 tool) + census of second build paths in verify/ | session_01KmtZKS82vxSbxtKufqbp4i | claude/fnv-l1-contract-w13 | doing |
| 2b | budget in VERDICT.md, budget gate, delete second build paths | lead | - | blocked by 2 (numbers) |
| 4a | assumptions gate over verify/ASSUMPTIONS.json (OWNER = 9) | - | - | next (file does not exist on any branch) |
| 4b | w11 test-defined symbols ∩ product-root symbols = ∅ in objstub | (predecessor) | claude/fnv-l1-contract-w11 001306f0 | doing |
| 4c | w12 delete hostapi services 27,28,29,32,33 | - | - | next |

## Global numbers (trunk 58a79b3e)

- gates in gates.json: 22; product roots in admit.json: 0 (every per-root gate has universe 0).
- verdict seconds: pending w13.
- tests with doubles: 0 of 0 (no product roots).
