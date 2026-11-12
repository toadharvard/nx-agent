# L1 CONTRACT status (lead: Opus 5.5, session started 2026-11-11 22:34Z)

Branch `claude/fnv-l1-contract`. Trunk: `claude/fnv-nx-clean` e35b870a (CLAUDE.md launcher-run-once; code as 2b7897a5, w4 @1fa25f8b merged, 240 s), 25 gates, 0 product roots. Target: first frame in this budget (CLAUDE.md 08f6ea30).

## Tasks

| # | Task | Worker session | Branch | Status |
|---|---|---|---|---|
| 2p | TOP: PP view EMLINK at 4,098-unit pack (O(units×names) placeholder links): shared all-placeholder base per real tree + per-unit ro bind of closure objects; store errors = red verdict with stage; check on L3a pack 2 cold + warm | session_01KmtZKS82vxSbxtKufqbp4i (w14) | claude/fnv-l1-contract-w14 | doing |
| 2c | selftests content-keyed (138-149 s of ~143 s warm verdict, w14 at e90a7e4; red results recomputed every run) + budget gate (no-change rerun recomputes 0; warm wall-clock ≤ B in VERDICT.md) | session_01KmtZKV6pbaGLooiWzXRtev (w19) | claude/fnv-l1-contract-w19 | doing |
| 3y | guest roots refuse error-downgrading flags (-fpermissive, -w, -Wno-error*) (L3a) | w14 | claude/fnv-l1-contract-w14 | queued after EMLINK |
| 1a | merge queue via MERGER: w8 exepin | session_01KmtZKRkugvim1Bfui5aFTw (w4 takes it over) | claude/fnv-l1-contract-w8 a6753733 | doing: w4 rebasing on f1059af7 |
| 1b | merge queue: w4 msvcmodel compiler + plugins fnvnx_order/fnvnx_ftoi (= item 3): MERGED e90a7e43; still open B2 (5 L2 plugins), guest_compiler 2nd external + platform build.json, G | session_01KmtZKRkugvim1Bfui5aFTw | claude/fnv-l1-contract-w4 bc406ec0 | doing: rebase onto a3e9f0a9 + verdict; then guest_compiler.json (fnvnx-a64-msvcmodel 00e3d42a) + sample platform build.json (L5 waits), B2 seven L2 plugins as stages, G build-once manifest |
| 1c | merge queue: w5 python pins / TMPDIR | session_01KmtZKRw5sTxarSdSFC6Njj | claude/fnv-l1-contract-w5 db15c7ac | cce6c1e1 (gate TMPDIR + worktree-clean rule, green 23/23 at 08f6ea30); rebasing on f1059af7; deb mirror tools/verdict-debs (snapshot.ubuntu.com 5xx), deb sha256 0/130 → 130/130; doing: setup_verdict_env.sh from scratch with sha256 pins (helper w13) |
| 1d | merge queue: L5 imports + machine f15935a9 | L5 | claude/fnv-l5-platform-w4r 0533f920 | done (trunk f1059af7) |
| 2 | incremental verdict speed (S0 cold, S1 no change, S2 comment header, S3 one .cc, S4 tool) + census of second build paths in verify/ | session_01KmtZKS82vxSbxtKufqbp4i | claude/fnv-l1-contract-w14 | doing |
| 3s | goal step 3: guest ELF link through recipe.py (link.roots, link.libs, fixed PIE options, ldcheck post stage from ld.py; delete the out-of-recipe linker path) | session_01KmtZKRyNHyAGWeTxKmbdfZ | claude/fnv-l1-contract-w15 | doing |
| 3r | recipe: Rule A (first, blocks platform root): "sources" list of patched externals, any root class incl. guest product (FreeType); Rule B (generated runs a root ELF) after first frame (uam/sdp2dksh are deko-only); host tool roots from patched external sources (uam, MojoShader) + generated steps that run a root's ELF (render FFP DKSH, L6/L6A) | session_01KmtZKRpuoHrhZqwnLDyQ8L | claude/fnv-l1-contract-w16 b57061c8 (Rule A 9b05ab80 + isystem, bare prog, include-in-root, GL pins; test_recipe 81/81) | queued at MERGER |
| 4d | w11 no doubles in tests (objstub) | predecessor's local agent | claude/fnv-l1-contract-w11 d799a5c3 (WIP) | doing; move to a cloud worker when its agent stops |
| 3b | recipe build judges every unit, failed_units listed (L3a lift/rt, pack 4098 units) | session_01KmtZKS82vxSbxtKufqbp4i (w14 item 2) | claude/fnv-l1-contract-w14 | queued after census |
| 3k | link kinds pie/exe/shared (+version_script) for GL backend render/gl4; shared roots via link.roots; target "variants" (root@NAME; also the sanitizer variant) (L6A) | session_01KmtZKRyNHyAGWeTxKmbdfZ (w15) | claude/fnv-l1-contract-w15 | doing |
| 3e | env.json pins: patch, libegl-dev, libgl-dev | session_01KmtZKRpuoHrhZqwnLDyQ8L (w16) | claude/fnv-l1-contract-w16 | doing |
| 3a | recipe: bare prog name (as, ld) resolved like the driver (programs: list, then PATH); host x86_64 roots red today (L6A render/gl4) | w16 | claude/fnv-l1-contract-w16 | doing |
| 3n | recipe check: include dir inside the root's own tree refused (#pragma once identity; L6) | w16 | claude/fnv-l1-contract-w16 | doing |
| 3i | (in w16 b57061c8) recipe key isystem (paths inside externals only) for libnx/newlib headers (L6A) | w16 | claude/fnv-l1-contract-w16 | doing |
| 1u | owner order 9e5856f0: Galaxy/GalaxyWrp not lifted. One rule for images.py+modules.py: guest image = found, no bridge row, closure resolves; else declared-machine module (kind declared, bodies = L5 bridge rows, not walked); rule contract/dllkind.py 568b67ab (images: exe, d3dx9_38, binkw32, libvorbis(file); Galaxy not reached; declared: galaxy, galaxywrp, ssce5432 (comdlg32 gap, L5)); mp4 0772c61 pushed (libvorbis 01ce0000, libvorbisfile 01e20000); next: declared_dlls.tsv from pinned Wine 9.0 dlls/*.spec, provider column, closure.tsv roles from dllkind, regenerate stale gpa/reach; item 2 green f31d416d at c7acc8c1 (194 s) | session_01KmtZKQwjYFpEvoHtfEjCcb (w18) | claude/fnv-l1-contract-w18 | doing |
| 3f | link root over admitted roots of mixed cxx_origin (L3b product ELF/tests) | w15 | claude/fnv-l1-contract-w15 | doing |
| 3p | PLUGIN_SOURCES (8) built as hermetic stages; f2i_check folded into fnvnx_ftoi by L2 w1 (PLUGIN_ARGS mode from cxx_origin, L2's edit) | w4 (B2) | claude/fnv-l1-contract-w4 | queued after runtime manifest |
| 3l | link.libs: archives inside declared externals (newlib libc.a), pulled members judged by linkstrict+objstub (L3b) | w15 | claude/fnv-l1-contract-w15 | doing |
| 3d | admit.json class "headers" (tracked tree, include-only, no units/gates; certified via PP reads; unit file inside refused) — L2 shim/include, 58/73 platform units wait | w16 | claude/fnv-l1-contract-w16 | TOP, doing |
| 3m | include mounts at root-relative paths in one mirrored tree (`../` works; undeclared = missing) (L2, L5 platform root) | w16 | claude/fnv-l1-contract-w16 | queued (before 3x) |
| 3g | guest library external fnvnx-a64-msvcmodel + sample platform build.json (L1 green 63511d69, 215.8 s, 51/51 selftests) | w4 | claude/fnv-l1-contract-w4 1fa25f8b | done (trunk 2b7897a5); next: runtime manifest == guest compiler (same GCC 13.3+msvcmodel, -fshort-wchar; 00e3d42a = GCC 15.2 libstdc++ red), L5 rebuilds tools/fnvnx-a64-msvcmodel13; runtime external required, -nostdinc |
| 3h | guest system headers = linked libc's (fnvnx-a64-msvcmodel newlib+libnx, -nostdinc); glibc cross headers refused in guest roots (L2/L5 socket units) | w4 | claude/fnv-l1-contract-w4 | queued (after w8 rebase) |
| 3x | fixture roots: per-unit gate + expected finding, never linked or in a universe (L3b forms/fpexact tests) | w16 | claude/fnv-l1-contract-w16 | queued |
| 3w | recipe: failed_units 054d65c, -Werror=attributes 5171fae, envcheck before any stage 892c248 (L3a, L2) | w14 | claude/fnv-l1-contract-w14 b7df218 | done; L1 line pending |
| 2b | budget in VERDICT.md, budget gate, delete second build paths | lead | - | blocked by 2 (numbers) |
| 4a | assumptions gate over verify/ASSUMPTIONS.json (OWNER = 9; field `pending` removed) | session_01KmtZKQZb21XFPG5GMjmjUp | claude/fnv-l1-contract-w10 (from b9f7cf14) | doing: rebase on trunk, drop `pending` + 47 pending records, add L6A, selftests in pinned env |
| 4b | w11 test-defined symbols ∩ product-root symbols = ∅ in objstub | predecessor's local agent -> moving to a cloud worker | claude/fnv-l1-contract-w11 001306f0 | doing: stage 2 (COMDAT, python AST rule, six L1 names to 0) |
| 4c | w12 (7f318d95: slots 10, 15, console_flush, main_loop, buttons_down, tz_offset_minutes deleted, holes kept; d161674, L1 red only selftests needing fnv-nx-build; next: no inline wrappers expanding FNVNX_PLAT_, delete SOCK 16..26,30 + markers, zero-use slots) hostapi.h = services with a call site in product roots (rule), FNVNX_EXT_DK for L4 | session_01KmtZKUQGJi3gKcTZcB8sr6 | claude/fnv-l1-contract-w12 (from 6766d18a) | doing: L5 took 6766d18a (3bf9c4d6); delete zero-use slots console_flush, tz_offset_minutes, ACCESS_FAULT_HANDLER 10, main_loop/buttons_down (test-only), UNMAP_FIXED unless a loader unit calls it; numbers kept as holes; selftest diagnosis |

Rule (L1+L2): a header-only dir is not a root; its headers are certified as PP inputs of admitted units (shim/include L2, loader include).

Assumptions (w10 399e639 at 6066375): 48 records = 9 OWNER + 39 REMOVE; mentions 92/98 certified. The 6 trunk mentions (hive regsites, imports objects.json, unit_facts) and 2 gate_call clauses get L1 REMOVE records with named removing checks.

## Missing REQUIRED_PRODUCT gates (blocker of goal step 1)

Global count: 11 missing of 32 required at trunk 2b7897a5 (gates.json has 25; imports, machine merged). No product root can be admitted until 0.

| Gate | Owner | Branch | Commit | MERGER |
|---|---|---|---|---|
| datamodel | L1 | claude/fnv-l1-contract-w17 (session_01KmtZKQzN38LhJYh6sixhL1) | 531ebbd (green 24/24 at c5b20de, 191 s) | queued |
| exepin | L1 | claude/fnv-l1-contract-w8 (w4 rebasing) | a6753733 | rebasing on f1059af7 |
| r7 | L2 | claude/fnv-l2-source-w5 (session_01KmtZKUHWWEEBqzMR6cAtAZ) | - | doing |
| namegate | L2 | claude/fnv-l2-source-w5 | - | doing |
| abi | L2 | claude/fnv-l2-source-w5 | - | doing |
| order | L3a (L2 supplies plugin certificate) | claude/fnv-l3a-lift-gates (session_01KmtZKR34WmRCfdDoE7ajAt) | - | doing |
| eh | L3a (L3b reviews) | claude/fnv-l3a-lift-gates | - | doing |
| addr32 | L3a | claude/fnv-l3a-lift-gates | - | doing |
| forms | L3b | claude/fnv-l3b-lift-w2g | - | doing |
| fpexact | L3b, claude/fnv-l3b-lift-w2g (native criterion: w17 makes fnvnx_ftoi reject any unrewritten FIX_TRUNC_EXPR) | - | - | - |
| d3d9total | L6 | claude/fnv-l6-render-w4 (session_01KmtZKUN17roaVoBLBe4QKK) | - | doing |

## Global numbers (trunk 58a79b3e)

- gates in gates.json: 22; product roots in admit.json: 0 (every per-root gate has universe 0).
- verdict seconds: pending w13.
- tests with doubles (trunk c9765781, AST rule, whole ports/fnv-nx): 8 of 47 python test files, 9 names; L1 owns 6 (gate_build.ensure, l1._check, runtime.DEFAULT_DIRS, runtime.conf_dirs, sandbox.launch, verdict._git), L4 owns 2.
- assumptions (w10 943d70d, trunk 08f6ea30 registry): 46 records = 9 OWNER + 37 REMOVE (L1 11, L4 14, L2 4, L3a 3, L6 2, L5 1, L1+L3b 1, L3b+L4 1); removed_by gate 3, planned 12, no gate yet 22, OPEN 0; mentions 88/93 certified (5 red, decided: 2 records + 1 check). Layer branches add their records at merge.
- hostapi (w12 6766d18a): 5 services removed (27, 28, 29, 32, 33; 0 call sites in platform 2951f7ae); gate hostapi 0 of 0, no roots.
- handoff read: c1431c5d.

## Deferred past the first frame

- Rule B (generated runs a root's ELF; uam, sdp2dksh).
- TLS gate: 0 R_AARCH64_TLS* / STT_TLS in roots without the console TLS ABI (L6A measured 0 in render/backend 87323b02).
