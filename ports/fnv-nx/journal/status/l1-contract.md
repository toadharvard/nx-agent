# L1 CONTRACT status (lead: Opus 5.5, session started 2026-11-11 22:34Z)

Branch `claude/fnv-l1-contract`. Trunk: `claude/fnv-nx-clean` 29b41938 (07862d5a + CLAUDE.md), 26 gates, 0 product roots. Target: first frame in this budget (CLAUDE.md 08f6ea30).

## Tasks

| # | Task | Worker session | Branch | Status |
|---|---|---|---|---|
| 2p | (w14 2d8176a, fix bc033dc: placeholder base once per store, inode per 60000 names, ro bind per closure file; store errors red with objects.json; test_views; retest sent to L3a/L3b/w3/w1) TOP: PP view EMLINK at L3a lift/pack 4,098 units and L3b lift/test/dslemit 4,096 units (O(units×names) placeholder links): shared all-placeholder base per real tree + per-unit ro bind of closure objects; store errors = red verdict with stage; check on L3a pack 2 cold + warm | session_01KmtZKS82vxSbxtKufqbp4i (w14) | claude/fnv-l1-contract-w14 | doing |
| 2c | selftests content-keyed (138-149 s of ~143 s warm verdict, w14 at e90a7e4; red results recomputed every run) + budget gate (no-change rerun recomputes 0; warm wall-clock ≤ B in VERDICT.md) | session_01KmtZKV6pbaGLooiWzXRtev (w19) | claude/fnv-l1-contract-w19 | doing |
| 3y | guest roots refuse error-downgrading flags (-fpermissive, -w, -Wno-error*) (L3a) | w14 | claude/fnv-l1-contract-w14 04e372d | done |
| 1a | merge queue via MERGER: w8 exepin | session_01KmtZKRkugvim1Bfui5aFTw (w4 takes it over) | claude/fnv-l1-contract-w8 a6753733 | doing: w4 rebasing on f1059af7 |
| 1b | merge queue: w4 msvcmodel compiler + plugins fnvnx_order/fnvnx_ftoi (= item 3): MERGED e90a7e43; still open B2 (5 L2 plugins), guest_compiler 2nd external + platform build.json, G | session_01KmtZKRkugvim1Bfui5aFTw | claude/fnv-l1-contract-w4 bc406ec0 | doing: rebase onto a3e9f0a9 + verdict; then guest_compiler.json (fnvnx-a64-msvcmodel 00e3d42a) + sample platform build.json (L5 waits), B2 seven L2 plugins as stages, G build-once manifest |
| 1c | merge queue: w5 python pins / TMPDIR | session_01KmtZKRw5sTxarSdSFC6Njj | claude/fnv-l1-contract-w5 db15c7ac | cce6c1e1 (gate TMPDIR + worktree-clean rule, green 23/23 at 08f6ea30); rebasing on f1059af7; deb mirror tools/verdict-debs (snapshot.ubuntu.com 5xx), deb sha256 0/130 → 130/130; doing: setup_verdict_env.sh from scratch with sha256 pins (helper w13) |
| 1d | merge queue: L5 imports + machine f15935a9 | L5 | claude/fnv-l5-platform-w4r 0533f920 | done (trunk f1059af7) |
| 2 | incremental verdict speed (S0 cold, S1 no change, S2 comment header, S3 one .cc, S4 tool) + census of second build paths in verify/ | session_01KmtZKS82vxSbxtKufqbp4i | claude/fnv-l1-contract-w14 | doing |
| 3s | goal step 3: guest ELF link through recipe.py (link.roots, link.libs, fixed PIE options, ldcheck post stage from ld.py; delete the out-of-recipe linker path) | session_01KmtZKRyNHyAGWeTxKmbdfZ | claude/fnv-l1-contract-w15 | doing |
| 3r | recipe: Rule A (first, blocks platform root): "sources" list of patched externals, any root class incl. guest product (FreeType); Rule B (generated runs a root ELF) after first frame (uam/sdp2dksh are deko-only); host tool roots from patched external sources (uam, MojoShader) + generated steps that run a root's ELF (render FFP DKSH, L6/L6A) | session_01KmtZKRpuoHrhZqwnLDyQ8L | claude/fnv-l1-contract-w16 b57061c8 (Rule A 9b05ab80 + isystem, bare prog, include-in-root, GL pins; test_recipe 81/81) | conflict with 07862d5a (env.json), rebasing; then w15 rebases on it |
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
| 3u | configure step: pinned cmake on writable patched tree; units = one declared target's compileGroups from CMake File API codemodel-v2; source-dir outputs as Rule B outputs (L6 MojoShader; patches deleting units = exclude list, refused) | w16 | claude/fnv-l1-contract-w16 | queued |
| 3c | class follows where objects run: root reached via link.roots from a product image = product root with all product gates; tool root in link.roots refused (L6 translator) | w15 | claude/fnv-l1-contract-w15 | queued |
| 1i | (census: atimgpud REACHED exe 0x4db2b8 LoadLibraryA const; gdffalloutnv not reached; d3dx9_31 not reached by resolved site; 12 open dynamic sites, argflow/dynsites mismatch on d3dx9_38, writable-global rule; bases final before push) image set = least set from exe closed under imports + statically resolved LoadLibrary of reached code (not every dll file); atimgpud, d3dx9_31, gdffalloutnv: reached? then lifted (L3a facts bc9f12d7: atimgpud 660 entries green, gdffalloutnv 322 green, d3dx9_31 18,945 entries codemap RED 270 undecodable runs, data-in-code rule L3a); wine-specs 678c780e → declared_dlls.tsv, every Galaxy/ssce gap = wine (L5 bridge); gpa -I import fix | w18 | claude/fnv-l1-contract-w18 e999ed27 | doing |
| 1s | bootsock argument census (21 wsock32 exe sites → winsock.cpp → SOCK 16..26,30,31): proved value sets with fact chains, certificates for fnvnx_reach_violation; unresolved GPA/LoadLibrary reaching wsock32? (L4) | w18 | claude/fnv-l1-contract-w18 | queued after closure roles |
| 3h2 | cross-root headers = admitted headers trees only (platform/include for fnvl_host.h, L3a lift/rt); include of another root's own tree refused | w16 | claude/fnv-l1-contract-w16 | with headers class |
| 3z | distro compiler spec defaults neutralised by rule: flag set = diff(distro gcc, GNU msvcmodel gcc 13.3) of -Q --help and -dM; all distro-compiled roots (L6A dk root) | w16 | claude/fnv-l1-contract-w16 | queued |
| 3q | one file per gate (verify/gates.d/<name>.json, gates.json deleted, test_gates expected set from dir) + no vacuous green (gate declares `covers`; universe 0 with a covered admitted root = red) | w10 | claude/fnv-l1-contract-w10 | queued after DECL_FACTS |
| 3j | gate_pure: z3 on EXTERNAL pinned (ASM-009) first; universe = import closure of stage entry points (build.json generated scripts + tool roots' declared `entry`), not directories; stage→host-reader import red; cross-root `imports` (L3b, L3a w3) | w14 | claude/fnv-l1-contract-w14 | queued |
| 3t | tests needing game data declare the pinned gamedata external; absent = blocked: env (never green); test_boot_job dummy exe deleted | w4 (w8) | claude/fnv-l1-contract-w8 25977efc | doing |
| 3o | host-root product gates: objstub plugin built per root compiler (env pins gcc-13-plugin-dev); linkstrict host boundary = dynamic exports of pinned libc6/libgcc-s1/libstdc++6 .so + link.roots (L6 translator: 69 of 688 uncertified) | w17 | claude/fnv-l1-contract-w17 | queued before guest flags |
| 3x | fixture roots: per-unit gate + expected finding, never linked or in a universe (L3b forms/fpexact tests) | w16 | claude/fnv-l1-contract-w16 | queued |
| 3w | recipe: failed_units 054d65c, -Werror=attributes 5171fae, envcheck before any stage 892c248 (L3a, L2) | w14 | claude/fnv-l1-contract-w14 b7df218 | done; L1 line pending |
| 2b | budget in VERDICT.md, budget gate, delete second build paths | lead | - | blocked by 2 (numbers) |
| 4a | assumptions gate over verify/ASSUMPTIONS.json (OWNER = 9; field `pending` removed) | session_01KmtZKQZb21XFPG5GMjmjUp | claude/fnv-l1-contract-w10 (from b9f7cf14) | doing: rebase on trunk, drop `pending` + 47 pending records, add L6A, selftests in pinned env |
| 4b | w11 test-defined symbols ∩ product-root symbols = ∅ in objstub | predecessor's local agent -> moving to a cloud worker | claude/fnv-l1-contract-w11 001306f0 | doing: stage 2 (COMDAT, python AST rule, six L1 names to 0) |
| 4c | w12 (ec1ed3e: rule (i) FNVNX_PLAT_ only in hostapi.h defines; SOCK 16..26, 30, 31 measured used, kept; SOCK_FIRST/LAST deleted; console_flush, tz_offset_minutes, 10, main_loop, buttons_down, 15 deleted, holes kept; struct 0x1A0, 46 slots, 29 services; sha f4463658) hostapi.h = services with a call site in product roots | session_01KmtZKUQGJi3gKcTZcB8sr6 | claude/fnv-l1-contract-w12 | doing: rule (i) widened to every header of admitted roots, headers trees and externals (only unit files may use FNVNX_PLAT_); merge clean 7503d2fa (3 hunks, w12 side) + bootmachine.py rule 5a docstring generated from SOCK_SERVICES (L4 review); then full L1 green with fnv-nx-build, then MERGER after w10; header forwarded to L5, L4, L6 |

Rule (L1+L2): a header-only dir is not a root; its headers are certified as PP inputs of admitted units (shim/include L2, loader include).

Assumptions (w10 a2bd503, code 2f1bbe2 on e35b870): gate GREEN, mentions 96/96; 52 records = 9 OWNER + 43 REMOVE (removed_by: gate 3, planned 18, no gate yet 22, OPEN 0); queued at MERGER. Next w10: objstub DECL_FACTS/DECL_SOURCE from declared_machine.tsv header (no-galaxy).

MERGER serial queue: w16 885852ac (green 4043444, 171 s) → L2 w4 5380477 (fnvnx_order, no gate files) → L2 w2 f54601ce (link tests on recipe objects, addr empty-section rule) → w14 2d8176a (EMLINK) → w18 (contract universe, dllkind; head with green line) → w17 cca84bb (datamodel only, green 27/27 224 s; ftoi rule (c) to be reapplied as new commits on L2 w1 plugin, cdc0041 is an -s ours ancestor) → L2 w5 77836bf → w10 a2bd503 → L6 w4 5902362d (d3d9total, green 27 gates 247 s) → w5 84af0b87 (green 25/25) → w8 25977efc. ASSUMPTIONS.json conflicts: union by id (records enter with the files that use them, e.g. L3a ASM-200..202 with lift/gates). Gate-file conflicts resolved by union until gates.d lands (orchestrator).

## Missing REQUIRED_PRODUCT gates (blocker of goal step 1)

Global count: 11 missing of 32 required at trunk 2b7897a5 (gates.json has 25; imports, machine merged). No product root can be admitted until 0.

| Gate | Owner | Branch | Commit | MERGER |
|---|---|---|---|---|
| datamodel | L1 | claude/fnv-l1-contract-w17 (session_01KmtZKQzN38LhJYh6sixhL1) | cca84bb (green 27/27, 223.8 s) | serial queue |
| exepin | L1 | claude/fnv-l1-contract-w8 (w4 rebasing) | a6753733 | rebasing on f1059af7 |
| r7 | L2 | claude/fnv-l2-source-w5 (session_01KmtZKUHWWEEBqzMR6cAtAZ) | 3b35851 (green 26 gates, 223.7 s) | queued |
| namegate | L2 | claude/fnv-l2-source-w5 | 3b35851 | queued |
| abi | L2 | claude/fnv-l2-source-w5 | 3b35851 | queued |
| order | L3a (L2 supplies plugin certificate; L2 w4 0f9e337 fnvnx_order rules ORDER-CONT, HARDREG, FENCE-MERGE, LOCAL, queue #5) | claude/fnv-l3a-lift-gates (session_01KmtZKR34WmRCfdDoE7ajAt) | - | doing |
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

- 2026-11-12 02:58Z clean=7503d2fa. Decisions:
  - contract+link become one admitted root (`trees` list); a declared-import cycle between distinct roots is red (w14).
  - Contract gates' `covers` = roots with a non-null link (w10, from the L6 translator root).
  - L2 w5 lands before L2 w4: a branch carries admit/oracle records for every file it adds. w4 was red on admit 494/513 and oracle 56/76 at fad7d2d4.
  - w16 rebases onto 7503d2fa for the env.json conflict.
- 2026-11-12 02:57Z new task 4u (from the orchestrator): w20 (session_01KmtZKSwpJXxijdPeaxppza, claude/fnv-l1-contract-w20) deletes startup/guest/onexit.c (7 MSVC CRT bodies by name) and its reference set. platform/crt import bodies are kept iff an image-set module imports the name. Count at 3fb7f9bc: 307 lines including the journal; the orchestrator counted 89 lines in 20 files. Status: doing.
- 02:58Z w15 fea3d99 green 25/25 gates, 244.4 s, sent to MERGER. Follow-up (a-d) is being written on it and waits for the trunk.
- Test-root form (answered to L6 w5):
  - class `test`, with its own build.json and every product gate plus oracle;
  - it links its product roots' guest objects through link.roots and runs in the L4 boot machine;
  - a symbol no root defines is linkstrict red, blocked by the named owner.
