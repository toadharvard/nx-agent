# L1 CONTRACT status (lead: Opus 5.5, session started 2026-11-11 22:34Z)

Branch `claude/fnv-l1-contract`. Trunk measured against: `claude/fnv-nx-clean` 58a79b3e (a3e9f0a9 + CLAUDE.md), 22 gates, 0 product roots.

## Tasks

| # | Task | Worker session | Branch | Status |
|---|---|---|---|---|
| 1a | merge queue via MERGER: w8 exepin (gate 23) | done by worker | claude/fnv-l1-contract-w8 a6753733 | doing: queued at MERGER session_01KmtZKSxPHjVrmy1iFKiW3g |
| 1b | merge queue: w4 msvcmodel compiler + plugins (= item 3) | session_01KmtZKRkugvim1Bfui5aFTw | claude/fnv-l1-contract-w4 bc406ec0 | doing: rebase onto a3e9f0a9 + verdict; then guest_compiler.json (fnvnx-a64-msvcmodel 00e3d42a) + sample platform build.json (L5 waits), B2 seven L2 plugins as stages, G build-once manifest |
| 1c | merge queue: w5 python pins / TMPDIR | session_01KmtZKRw5sTxarSdSFC6Njj | claude/fnv-l1-contract-w5 db15c7ac | db15c7ac at MERGER (worker: green, 22 gates, 163 s); doing: setup_verdict_env.sh from scratch with sha256 pins (helper w13) |
| 1d | merge queue: L5 imports + machine f15935a9 | L5 | - | queued at MERGER |
| 2 | incremental verdict speed (S0 cold, S1 no change, S2 comment header, S3 one .cc, S4 tool) + census of second build paths in verify/ | session_01KmtZKS82vxSbxtKufqbp4i | claude/fnv-l1-contract-w14 | doing |
| 2b | budget in VERDICT.md, budget gate, delete second build paths | lead | - | blocked by 2 (numbers) |
| 4a | assumptions gate over verify/ASSUMPTIONS.json (OWNER = 9; field `pending` removed) | predecessor's local agent w10 -> moving to a cloud worker | claude/fnv-l1-contract-w10 | doing: WIP push requested |
| 4b | w11 test-defined symbols ∩ product-root symbols = ∅ in objstub | predecessor's local agent -> moving to a cloud worker | claude/fnv-l1-contract-w11 001306f0 | doing: stage 2 (COMDAT, python AST rule, six L1 names to 0) |
| 4c | w12 hostapi.h = services with a call site in product roots (rule), FNVNX_EXT_DK for L4 | predecessor's local agent -> moving to a cloud worker | claude/fnv-l1-contract-w12 | doing: WIP push requested |

## Global numbers (trunk 58a79b3e)

- gates in gates.json: 22; product roots in admit.json: 0 (every per-root gate has universe 0).
- verdict seconds: pending w13.
- tests with doubles (trunk c9765781, AST rule, whole ports/fnv-nx): 8 of 47 python test files, 9 names; L1 owns 6 (gate_build.ensure, l1._check, runtime.DEFAULT_DIRS, runtime.conf_dirs, sandbox.launch, verdict._git), L4 owns 2.
- assumptions: no total yet (w10).
- handoff read: c1431c5d.
