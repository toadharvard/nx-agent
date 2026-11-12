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
| datamodel | L1 | claude/fnv-l1-contract-w17 (session_01KmtZKQzN38LhJYh6sixhL1) | aaa709c8 (merged, trunk) | done |
| exepin | L1 | w4 (session_01KmtZKRkugvim1Bfui5aFTw) | head pending | next slot (priority) |
| r7 | L2 | claude/fnv-l2-source-w5 (session_01KmtZKUHWWEEBqzMR6cAtAZ) | 3b35851 (green 26 gates, 223.7 s) | queued |
| namegate | L2 | claude/fnv-l2-source-w5 | 3b35851 | queued |
| abi | L2 | claude/fnv-l2-source-w5 | 3b35851 | queued |
| order | L3a | claude/fnv-l3a-lift-gates (session_01KmtZKR34WmRCfdDoE7ajAt) | 0a28d38c green (6f052947, 225.9 s); citation removed | with MERGER |
| eh | L3a | claude/fnv-l3a-lift-gates | 376acbaa | with order |
| addr32 | L3a | claude/fnv-l3a-lift-gates | 376acbaa | with order |
| forms | L3b | claude/fnv-l3b-lift-w2g | - | doing |
| fpexact | L3b, claude/fnv-l3b-lift-w2g (native criterion: w17 makes fnvnx_ftoi reject any unrewritten FIX_TRUNC_EXPR) | - | - | - |
| d3d9total | L6 | claude/fnv-l6-render-w4 | 8cfbcdbf (merged, trunk) | done |

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
- 03:00Z render/d3d9 closure, from L6:
  - fnvnx_wine_fixme → L5 platstat.cpp; fnvnx_reach_violation → L5 (asked).
  - fnvnx_import_key* → an L1 generated root from contract/iat.py (w16 B).
  - New headers tree contract/include declares them (w16 A).
  - D3DX compile-site census per site → w18, for L6 w3b.
  - Dynamic GetProcAddress sites open: 12, so certificates don't count yet.
- 03:01Z w17: -mno-outline-atomics becomes a required guest flag, because the declared Cortex-A57 has no LSE (from L2). A reference from a guest object to __aarch64_* outline helpers is red. Every CC key changes once.
- 03:02Z w15 5d6e42ea adds link_archives with members recorded, the objects.json symbol check and the tool-root refusal; test_link 42/42. Next: linkstrict/objstub read the members, full verdict. fea3d99 stays queued. contract/include paths are fnvnx/reach.h, declared.h and import_key.h, one declaration per extern (agreed with L5).
- 03:03Z w17 c6726a2 is green: 27/27 gates, 236.1 s (gcc-13-plugin-dev pinned; host linkstrict rule (d)). It replaces cca84bb in the MERGER queue, ahead of the L6 translator. Later: symbol versions and keying by deb sha256.
- 03:05Z w10 0371bf6: gates.d (27 files) with covers; contract gates cover [linked]. 26/27 gates, 167 s; selftests red 53/57 for lack of fnv-nx-build externals, rerunning with them attached. w20's second item: objstub judges a tail b to a cited log function as bl+ret, and ICF aliases once with all names (from L6 w6).
- 03:07Z:
  - w12 2901bc9 is green (36b24d9d, 261.9 s, 26 gates): hostapi with the wrapper rule widened and docstrings generated; sent to MERGER.
  - w16 5b0dd218 is green (3b88da89, 185.4 s, 24 gates): 3m mirror, fixtures and the `fixtures` gate. It rebases onto 7503d2fa, then goes to MERGER; `fixtures` goes into REQUIRED_PRODUCT.
  - w15 83053a54: tool-root refusal; objects.json check; archive members judged by linkstrict/objstub. Its variant run on a real root is blocked until the first product root lands (L5). New item: cross-root `outputs`/`uses` keyed by content hash (for L5 w13).
  - New task 4l: w21 (session_01KmtZKTZ76LhVoz6LLMqcHv (w21b; w21 archived, stuck pending), claude/fnv-l1-contract-w21) builds the `legal` gate (VA in the image ranges paired with a decomp name is red) and a census per layer. Status: doing.
- 03:09Z w19 b178859 is green (222.5 s cold, warm 2 s; gate warm with B=2.0). Fixes asked: test determinism checked in the sandbox (testdet), not assumed; test deps declared as oracle `uses`, not taken from the AST or name strings; gate cache keyed by tree. L2 w4-clean 9be67297 is green (261.8 s) and with MERGER.
- 03:11Z w20 5126d5b0 deletes onexit.c and its set: 278 lines in 49 files → 223 in 40 at 3fb7f9bc→5126d5b0, journal excluded; 121 of the remainder are newlib crt_symbols rows.
  - platform/crt keeps _onexit, __dllonexit, exit and _amsg_exit (imported by d3dx9_31/38); deleted atexit, doexit, _cexit and _c_exit (no importer).
  - Next, same class: the xc runner (xc.c, mkxctab, XC1/XC2, dyninit_rewrite's ??__E emission). Verdict pending: envcheck fails in w20's container, so the env setup is rerun.
- 03:13Z MERGER order for L1: exepin (w4, priority from the orchestrator: a required gate for product roots), w16, w17, w12. env.json conflicts resolved by union of disjoint pins (authorized).
- 03:14Z w5 adds a new item: env.json profile toolchain-build (for L5 w14's guest runtime from source), with envcheck --profile, a chroot-only setup script, and the debs mirrored.
- 03:15Z w15 3a6ec1b2: cross-root outputs/uses (test_uses 9/9; test_link 45/45 and test_recipe 53/53 at bcf281fb). Full verdict running. toolchain-build package list from L5 w14 forwarded to w5 (full closure plus sha256; pyelftools as a deb).
- 03:17Z: w10 0371bf6 is green 27/27 (7767b55, 227.8 s; selftests 57/57) and with MERGER. w10's next item: root class `ref` plus oracle REF kinds `external` and `probe` (L6 asked). L3a (order, eh, addr32) and L3b (forms, fpexact) asked for MERGER heads or blockers.
- 03:19Z w12 f6ac90a is green (c6eda21e, 217.6 s): stale slot names 14 hits in 5 files → 0, with a stale_names gate clause from hostapi_holes.json; it replaces 2901bc9 in the queue. L3a's order/eh/addr32 are at 376acbaa.
- 03:22Z:
  - w18 a6c97620 (local; verdict running): fixed a base coordinate hole. Facts sat at the assigned base and were read at the preferred base, so every dll site was falsely "unreached".
  - Least image set: exe, d3dx9_38, binkw32, libvorbis, libvorbisfile, atimgpud. New bases: d3dx9_38 014b0000, libvorbis 01880000, libvorbisfile 019c0000.
  - LoadLibrary sites: 23 in total, 20 closed, 3 open. (ii) is L5's import_escape n; (i) is the index bounds rule; (iii) is a field-sensitive data word.
  - Assumptions gate defect: bare ASM ids without records pass. w10 fixes it first. L3a is held until ASM-043/045 have records.
  - L3b w2g (forms, fpexact): ETA about 30 min.
- 03:25Z trunk clean=aaa709c8 (w14 d6c72958, w18 7675fda5, w17 aaa709c8 merged).
  - ASM-043/045 are removed by w5's Form-writes fact, not recorded (orchestrator correction).
  - w20 13c3d951 adds the objstub tail-b and ICF rule; it rebases onto the trunk and runs the full objstub tests.
  - EMLINK: w14 generalizes the store rule to all content.
  - Generated `sources` (FreeType): assigned to w15.
  - Pip stdin fix: w5. w16 rebases onto aaa709c8 with the pin union.
- 03:28Z check-in, trunk aaa709c8 (27 gates; verdict 246–263 s against a 300 s budget):
  - w17 23f723b is green (244.9 s): flags.semantics in guest_compiler.json. -mno-outline-atomics is still missing; asked before MERGER.
  - w19's speed fixes are prioritized right after w16.
  - w16's new head 1cba64c8: env.json is the union of pins, and the trunk version wins on a clash.
  - Worker heads: w10 0371bf6, w12 f6ac90a, w14 0e474275, w15 3a6ec1b2, w18 9b1c64ee (a6c97620 local), w20 11168c64.
- 03:31Z:
  - The trunk has no gate_assumptions and no ASSUMPTIONS.json. They arrive with w10 (4c5bcc2; judge_stray makes any unrecorded ASM-\d{3} red), so w10 merges before L3a.
  - ASM-043/045 records are deleted from w10, because they are removed by fact.
  - w16 369d5948 is green (92a5e9da, 218 s) and contains w15 fea3d99. It still has to merge aaa709c8 (env.json union) and add fixtures to REQUIRED_PRODUCT.
  - w3 (L3a): gate_pure's class is the import closure, not a directory name. w14 deletes any `tests`-name exemption.
  - L4 has the link-stage form for item 11 (guest test image).
- 03:33Z w20 bce5dead deletes the xc runner class (xc.c, mkxctab, mkxcgen, dyninit_rewrite XC1, XC2, the boot.cpp xc capture), about 2300 lines together with 5126d5b0, plus the objstub tail-b/ICF rule. envcheck in its container fails on /usr/include, so MERGER runs the verdict. The ??__E bodies without a generator are handed to L2's static rule (the exe's own table and the exe's atexit).
- 03:36Z: a guest test root uses link.kind pie, not exe (L4 corrected; w15 fixes RECIPE.md and refuses guest+exe). Plugins declare languages; ftoi and order load for C guest units too, with required evidence (w17, from L3b). L2's w20 decisions: the first ELF lifts all ??__E bodies through the exe's atexit; dyninit_conv is deleted. L3b merges aaa709c8.
- 03:38Z: w14 76130ea is the trunk plus the pure entry-point universe (the tests-name exemption deleted), plus the EMLINK replicas rule (a 70000-link test). test_gates was fixed in 803327b; verdict running. Full plugin set for C and C++ (order, ftoi, addr, dispatch, names, sdd, pi, layout): recipe by w17, cc1 support by L2 w1.
- 03:40Z trunk clean=8cfbcdbf: 28 gates (L6 d3d9total), cold 302.9 s, over budget. Returned: w10 0371bf6 (real rebase, gates.d carrying 28 gates), w16 8d72015d (env.json). L1 order: w16 → w10 → w19 (speed: cold, warm and per-gate times).
- 03:43Z:
  - Class `test` in gate_admit, with its own required set (w10 item 2, for L4 item 11).
  - crt0 FNXG header: no data in "ax" sections; it moves to its own "a" section placed first by guest.ld (crt0 owner).
  - Gate format for L2's 5 object gates: gates.d, objects from gate_build.ensure(); L2's `order` is renamed (it clashes with L3a's).
  - Trace variant for L3a via w15 variants. A product image may link only base variants (w15).
- 03:45Z:
  - w16 39fc3d7e (aaa709c8 merged, env.json union, fixtures required).
  - w20 45daef38: the trunk already lacks the xc/onexit files; objstub W-rule only, test_objstub 239 pass / 11 env fail, same as trunk.
  - w5 ea7e5ea6 is green 25/25: 134 debs from mirror 161a464b, sha256 134/134.
  - New blocker: z3 files_sha256 depends on how pip installs, so envcheck fails in fresh containers. w5 fix: verify against the wheel's RECORD plus a mirrored wheel. MERGER runs the verdicts meanwhile.
- 03:48Z:
  - L3a 0a28d38c is green and with MERGER.
  - w10 4c5bcc2 is green 27/27 (b4c35c53, 219.7 s). Its ASM-043/045 are existing REMOVE records (LLVM decoder, owner L4), deleted together with their citations by the replacing fact. w10 now rebases onto 8cfbcdbf.
  - crt0 is split by input section (.crt0 ax is 4 bytes; .crt0.hdr and .crt0.mod0 are a), so the loader contract is unchanged.
  - L3a's trace variant is withdrawn: the trace becomes a boot-machine observation.
- 03:51Z: new gate `onedef` (w15), per image: (a) strong/weak defined in one root; (b) COMDAT from one source definition with equal bytes (ODR); (c) equal normalized function bytes from different definitions are red. Censuses over the platform and render roots come before MERGER. lib/sha256 is a product root (owner L4) with a guest build plus a host variant.
- 03:53Z w14's next item: gate_reach request kind closure_key (for L3a item 26). Premises: the closure fact, dispgen --closure, and 0 open dynamic sites. It is measurement-only until then.
- 03:54Z onedef (c) dropped (body equality is a heuristic). Replaced by provenance: (c') each unit source belongs to one root, by sha256 and by path; (c'') a header's content appears under one path only.

## 2026-11-12 03:22Z
- exepin (w4/w8 5efe2154) on clean 8cfbcdbf: RED e30e9bd6 (29 gates, 366 s).
  - exepin 45/91: contract/bodysel.py:73 reads the exe outside load_exe.
  - selftests 60/61: test_exepin.py fails.
  - w4 was told to extend certification to every exe read site and rebase. Status: doing.
- w5 58bd4c2a: conflicts with 8cfbcdbf. Told to rebase, env.json pins as a union with the trunk winning on a clash. L2 w3 confirmed the `pip -r -` gap. Status: doing.
- Budget rule decided by L1. The 300 s budget gates only the incremental verdict, with a warm GATE_CACHE, on the pinned farm host. Cold time is reported but not gated. A docs-only change must show 0 recompiled units, else it is a keying defect and red. Owner: w19, doing.
  - Evidence: L2 w3 754a94c1 at 299.961 s; w6 c9bdb4e8 (docs only) at 303.2 s.
- 03:30Z decisions:
  - **flags.semantics** (w17, doing). One set of codegen-semantics flags lives in guest_compiler.json. A build.json may carry only -W/-D/-I/-std/-O/-g/-f{function,data}-sections. The DW_AT_producer gate checks that every CU of an image has the same set. Found by L6: render/d3d9 lacked -ffp-contract=off (IEEE, OWNER assumption 8), -fwrapv and others; lib/sha256 lacked the TLS/rounding/wchar flags.
  - **Variant target** (w15, doing). An admit variant may set its own `target`; its sources stay the base root's, and it is linkable only by tests. lib/sha256@host replaces the loader Makefile host build. w15 also fixes the root@NAME CLI split_spec bug.
  - **Assumption ids.** L3b gets ASM-300..399. L3b w3t 536b2548 holds 29 new REMOVE records and 14 aliases; they go onto a branch based on w10 and merge right after w10.
  - **crt0 split** (w20, queued). L5 w12 281c4a33 plus guest.ld KEEP/ASSERT, in one commit.
  - **Incremental acceptance** (w19). A warm run is timed per gate for three cases: docs-only, one unit changed, one gates.d file changed. A gate that re-runs without a changed declared input is red. Each gate's deps are declared and enforced by the sandbox.
  - test_gates on 8cfbcdbf: the pure/tests/gates.json cases are all green; the verdict fixtures need the declared env. The w2g failures come from a stale verify/ in its merge.
- 03:28Z: assumption id ranges, fixed by L1. Legacy ASM-001..054 stay. New ids by layer:

  | Layer | Range |
  |---|---|
  | L1 | 100-199 |
  | L3a | 200-299 |
  | L3b | 300-399 |
  | L4 | 400-499 |
  | L5 | 500-599 |
  | L6 | 600-699 |
  | L6A | 700-799 |
  | L2 | 800-899 |

  - ASM-300..328 are L3b's. L3a w9 cites them in fnvlift, so there is no collision.
  - w10's gate will be red on a duplicate id, a duplicate alias, or a new id outside its owner's range.
- 03:29Z: **descs fact** for L3a item 26 (lift/bridges). Assigned to w16, after the iat root.
  - It is the sorted, unique union of `.fnvnx.disp` over every admitted root except lift/bridges, published as a recipe output and read through `uses`.
  - lift/bridges' own objects must have no `.fnvnx.disp`; that rule breaks the cycle.
- The closure question goes to w14's closure_key. Its premises are open = 0 (w18) and dispgen --closure green.
- gate_assumptions will write its full findings list to its detail file (w10).
- 03:30Z:
  - env gap found by L2. On 8cfbcdbf, the python modules capstone, pyelftools and unicorn are pinned by version only, with no hash. This is added to w5's scope: every module gets a wheel sha256 plus a RECORD hash, and envcheck refuses an unhashed one.
  - The id range rule is keyed by `minted_by`, not by owner. L3a-owned ASM-3xx ids keep their numbers, and ASM-200..202 are minted by L3a (w10).
- 03:33Z:
  - **Id ranges are checked against the owner** (orchestrator). L3b renumbers its L3a- and L2-owned records to 2xx and 8xx and keeps the old ids as aliases; the records merge together with w2g.
  - **The L3a `flags` gate** (L3a w5 b7b04d53) is now required for class `lifted`, through w10's gates.d. Its universe is the 6 images.
  - **flags.semantics decisions (w17):**
    - One set for all languages. Per-language removal is read from the pinned driver's --help=<lang>.
    - The set includes -ffixed-x19..x27, -fno-jump-tables, -fno-ipa-ra, -fno-devirtualize, -fno-builtin, the stack and fortify flags, and -ftls-model=local-exec.
    - -fPIE replaces -fPIC, unless a pack is linked -shared.
    - -fno-exceptions is undecided until w17 reports the global try/throw/catch count in the native guest sources.
  - **Evidence from L6:** the trunk sample's C flags have no semantics flags, and C unit ffp_vs_state.c had 7 fused multiply-adds (fmadd/fmsub) under the default -ffp-contract=fast.
- 03:36Z: heads and assignments.

  | Worker | Head | State | Next |
  |---|---|---|---|
  | w14 | b04f3ec7 | green, 295.9 s, 27 gates: imports/trees, entry universe, EMLINK, closure_key | rebase onto w10 (gates.d); then warm ensure() (L4: 54 variants take 63 s warm with 0 stages run) |
  | w16 | de8ed638 | headers class plus gate, 28 gates; sent to MERGER | contract/include, then iat, then descs |
  | w10 | 1638c742 | contains trunk 8cfbcdbf, gates.d with 29 gates; test class done | link.kind/roots as a real link-stage input |
  | w12 | — | new item: doubles gate on a w10 base | — |
  | w18 | 9f500ec5 | D3DX census: 6 sites, 2 reached; entry-range bug fixed (20487 functions) | — |

  - Open counts: dynamic GetProcAddress (GPA) 12, LoadLibrary 3.
  - crt0.s moves into an L5 guest runtime product root. Test roots reach it through link.roots, and shared headers through the headers class.
  - w17 adds a no-__CHAR_UNSIGNED__ check per unit and an object-change count. There is no FMA instruction scan (orchestrator retracted it).
  - MERGER order: w16 → w10 → w14 → w19.
- 03:38Z:
  - **exepin** (w4/w8 03c699d0): green, 29 gates, 374.6 s cold. Merge allowed under the warm-budget rule.
  - **w17 2ae56ebf:** all 8 plugins in every guest C and C++ stage.
  - **-fno-exceptions:** decided by a static call-graph fact. Until that fact exists, exceptions stay on (orchestrator). -fsigned-char is explicitly in the set.
  - **L3b w3a 326f2c8e:** L3b 300..308 (9 records), L3a 203..220 (18), L2 801 (1), with aliases; 77 registry entries. L3a confirmed.
  - **L6 fmadd evidence:** 7 → 0 at c646a014, once -ffp-contract=off and the rest of the semantics set applied to C.
  - **lib/sha256 admit entry with a host variant:** w15.
  - **selftest timeout rule:** derived from a stored warm-runtime fact on the pinned host only (w19).
- 03:40Z:
  - **closure_key** (w14): two ids, FNVNX_REACH_CLOSURE_KEY_NATIVE and _LIFTED. They are measurement-only until sources.tsv is closed, dispgen --closure is green, and open = 0. reach_facts.h is a recipe fact output that fnvnx/reach.h includes through `uses`.
  - **ASM-043:** deleting it dropped L3b's capstone aliases. L3b mints ASM-329/330. The w10 gate adds three rules: ids are never reused (a `retired` list), a cited record cannot be removed, and a cited alias must resolve to a live record.
  - **rt/guest** (L5, product): crt0.s plus the runtime units. guest_pie.specs and build.sh are removed.
  - **MERGER order:** w10 → L3a lift-gates (ASM-200..202) → L3b unit.
- 03:41Z:
  - w21 never started (stuck pending), so it was archived and w21b (session_01KmtZKTZ76LhVoz6LLMqcHv) now does the legal gate plus census.
  - w5's acceptance is now a fresh-container green with nothing installed by hand: setup, envcheck, verdict. Status: open. w13 had 25 dpkg errors at install_debs.
- 03:44Z: **w18**, open LoadLibrary sites.
  - Verdict: 9f500ec5 green (26 gates). memfx.edge_bound (dominating unsigned cmp) is at 37b24de3, which has no verdict yet.
  - Results: of 127 indexed .data stores, 1 is closed and 126 stay unbounded. Open sites are unchanged: LoadLibrary 3, GPA 12.
  - **Open class, reported to the orchestrator:** site (i) 0xda284c needs data object extents in the exe.
    - OWNER-6 alone does not give them: interior references and negative in-bounds indices make "next referenced address" unsound. I withdrew that idea.
    - Candidates: (a) extents from the decomp layout facts (L2), (b) value ranges from L3a, (c) a write-set fact for the cell.
  - w18 now does (iii) 0x56b719 and records the raw fact for (i).
- 03:48Z: three corrections to my own decisions, from facts measured by w17.
  - (1) **Plugins per language:** set by a static ELF fact. cc1 cannot load layout, sdd, pi or names (they need cp_global_trees). C units get ftoi, order, dispatch and addr only. This is reported to L2 as an open coverage gap: layout and names must become C-capable.
  - (2) **-fPIC is kept,** per ARCHITECTURE D16 and D9 (GOT rebinding). I withdrew -fPIE.
  - (3) **EH stays on:** native guest code has throw in platform/win32, platform/crt and shim/include (L5 db504735: try 8, throw 22, catch 9). Render and loader have 0.
  - flags.semantics: 36 flags. Only -fno-sized-deallocation is dropped for C.
- **w18 (iii):** the field rule is retracted as unsound, because it would need object extents. Both (i) and (iii) are open pending extents.
  - Next for w18: allocation-site objects with a constant size.
  - L2 is asked whether the decomp layout facts give global extents.
- **Heads for MERGER:** w16 0b0d08b3 (contract/include headers) and w14 c0f23a3f (rebased onto w10). closure_key is in 58a2984b, which is in w14.
