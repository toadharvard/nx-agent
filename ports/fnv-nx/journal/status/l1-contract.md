# L1 CONTRACT status (lead: Opus 5.5, session started 2026-11-11 22:34Z)

Branch `claude/fnv-l1-contract`. Trunk: `claude/fnv-nx-clean` 41c9ecab (w4 merged at e90a7e43), 23 gates, 0 product roots. Target: first frame in this budget (CLAUDE.md 08f6ea30).

## Tasks

| # | Task | Worker session | Branch | Status |
|---|---|---|---|---|
| 1a | merge queue via MERGER: w8 exepin (gate 23) | done by worker | claude/fnv-l1-contract-w8 a6753733 | doing: queued at MERGER session_01KmtZKSxPHjVrmy1iFKiW3g |
| 1b | merge queue: w4 msvcmodel compiler + plugins fnvnx_order/fnvnx_ftoi (= item 3): MERGED e90a7e43; still open B2 (5 L2 plugins), guest_compiler 2nd external + platform build.json, G | session_01KmtZKRkugvim1Bfui5aFTw | claude/fnv-l1-contract-w4 bc406ec0 | doing: rebase onto a3e9f0a9 + verdict; then guest_compiler.json (fnvnx-a64-msvcmodel 00e3d42a) + sample platform build.json (L5 waits), B2 seven L2 plugins as stages, G build-once manifest |
| 1c | merge queue: w5 python pins / TMPDIR | session_01KmtZKRw5sTxarSdSFC6Njj | claude/fnv-l1-contract-w5 db15c7ac | db15c7ac at MERGER (worker: green, 22 gates, 163 s); doing: setup_verdict_env.sh from scratch with sha256 pins (helper w13) |
| 1d | merge queue: L5 imports + machine f15935a9 | L5 | - | queued at MERGER |
| 2 | incremental verdict speed (S0 cold, S1 no change, S2 comment header, S3 one .cc, S4 tool) + census of second build paths in verify/ | session_01KmtZKS82vxSbxtKufqbp4i | claude/fnv-l1-contract-w14 | doing |
| 3s | goal step 3: guest ELF link through recipe.py (link.roots, link.libs, fixed PIE options, ldcheck post stage from ld.py; delete the out-of-recipe linker path) | session_01KmtZKRyNHyAGWeTxKmbdfZ | claude/fnv-l1-contract-w15 | doing |
| 3r | recipe: host tool roots from patched external sources (uam, MojoShader) + generated steps that run a root's ELF (render FFP DKSH, L6/L6A) | session_01KmtZKRpuoHrhZqwnLDyQ8L | claude/fnv-l1-contract-w16 | doing |
| 4d | w11 no doubles in tests (objstub) | predecessor's local agent | claude/fnv-l1-contract-w11 d799a5c3 (WIP) | doing; move to a cloud worker when its agent stops |
| 2b | budget in VERDICT.md, budget gate, delete second build paths | lead | - | blocked by 2 (numbers) |
| 4a | assumptions gate over verify/ASSUMPTIONS.json (OWNER = 9; field `pending` removed) | session_01KmtZKQZb21XFPG5GMjmjUp | claude/fnv-l1-contract-w10 (from b9f7cf14) | doing: rebase on trunk, drop `pending` + 47 pending records, add L6A, selftests in pinned env |
| 4b | w11 test-defined symbols ∩ product-root symbols = ∅ in objstub | predecessor's local agent -> moving to a cloud worker | claude/fnv-l1-contract-w11 001306f0 | doing: stage 2 (COMDAT, python AST rule, six L1 names to 0) |
| 4c | w12 hostapi.h = services with a call site in product roots (rule), FNVNX_EXT_DK for L4 | session_01KmtZKUQGJi3gKcTZcB8sr6 | claude/fnv-l1-contract-w12 (from 6766d18a) | doing: diagnose 3 red selftests (trunk / branch / without -dU), inline-wrapper and range-marker tests; L4, L5 told to delete their side |

## Global numbers (trunk 58a79b3e)

- gates in gates.json: 22; product roots in admit.json: 0 (every per-root gate has universe 0).
- verdict seconds: pending w13.
- tests with doubles (trunk c9765781, AST rule, whole ports/fnv-nx): 8 of 47 python test files, 9 names; L1 owns 6 (gate_build.ensure, l1._check, runtime.DEFAULT_DIRS, runtime.conf_dirs, sandbox.launch, verdict._git), L4 owns 2.
- assumptions (w10 b9f7cf14, before cleanup, not global): 100 records = 9 OWNER + 91 REMOVE; 47 of them pending -> removed; L6 not entered (enters with L6's merge).
- hostapi (w12 6766d18a): 5 services removed (27, 28, 29, 32, 33; 0 call sites in platform 2951f7ae); gate hostapi 0 of 0, no roots.
- handoff read: c1431c5d.
