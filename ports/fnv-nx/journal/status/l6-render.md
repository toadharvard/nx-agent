# L6 RENDER status (lead: Opus 5.5, session started 2026-11-11 22:34Z)

Branch `claude/fnv-l6-render`. Handoff read: `journal/inbox/l6-render-handoff-opus.md` at ce235cf5. Trunk measured against:
Orchestrator: session_01KmtZKRGpFka9ShREBTYbTx (since 22:48Z; the earlier session_01KmtZKURkbywQNqjAdqyELR is archived).
`claude/fnv-nx-clean` 58a79b3e (0 product roots; render is not in the trunk).

## Tasks

| # | Task | Worker session | Branch | Status |
|---|---|---|---|---|
| 1 | (CANCELLED by owner correction 22:45, CLAUDE.md 6f549875: two backends, OpenGL + deko3d) GL removal; branch claude/fnv-l6-render-w1 89fa6a889 is never merged (owner order) | session_01KmtZKSPYqkKJ24Q6D7aBcE (w1) | claude/fnv-l6-render-w1 | cancelled; w1 reassigned to 2c |
| 1b | OpenGL backend is product and the REFERENCE for deko3d (CLAUDE.md d7fac9b5): render/gl4 (moved unchanged by w2), nxgl, capture/replay_gl, n0/run_gl, gl_link stay; the front end emits one nxgpu stream consumed by both; GL-rendered captures/scenes are kept as reference inputs of the deko-vs-GL frame check. Own host root render/gl4 | L6A lead session_01KmtZKSgdFAv1Sd3kGuQtHk (orchestrator assignment) | (L6A worker) | handed to L6A 22:47Z; L6 w2 does the unchanged move to render/gl4; root needs w1b translator libraries + EGL/GL dev packages pinned in env.json (L1) |
| 2 | render/d3d9 as a guest (trunk verify/ at 08f6ea30 has msvcmodel + plugins; L6A root moves to render/backend) product root: build.json on msvcmodel b4057704 + fnvnx_order/fnvnx_ftoi, admitted, built by recipe.py; build_guest.sh deleted | session_01KmtZKU3QvS8E92qtA8rKes (w2) | claude/fnv-l6-render-w2 | doing: root on lead branch 61443784, 70/70 units with local admit of its include roots; trunk entry blocked by include-root admission: shim/include (L2 w6), render/backend (L6A), translator include via L1 w15 root-reference rule |
| 2c | GL-path translator host root: MojoShader external + our SM1-3 -> GLSL sources, no uam (link null, consumed by L6A's render/gl4 via link.roots) | session_01KmtZKSPYqkKJ24Q6D7aBcE (w1b) | claude/fnv-l6-render-w1b | doing: verdict 8fedbc7b green on pure/build 16/16/determinism/argorder. L1 ruled 02:48Z: objstub per-root-compiler plugin + plugin-dev pins, host boundary = pinned libc6/libgcc-s1/libstdc++6 dynamic exports + link.roots (L1 w17, commit pending); 14 "missing result" gates = L1 w18 universe-0 rule (MERGER queue); rebased: 85dc017f on L1 w16 b57061c8 (verify/ diff = admit.json only), recipe valid, 16 objects, cold x2 identical, warm 34/34 0.17 s; sent to L6A 02:49Z. Verdict 85dc017f red 188 s/23: build + determinism 16/16 green; objstub plugin + linkstrict host boundary = L1 w17; 14 contract gates on a link-null host root = L1 w18 universe-0; admit 0/502 = replay carried render/** onto the w16 base, w1b reduces the branch to render/shaders/**; 07269dd5 scoped: admit 0/475 = DKSH side/loose files without roots, removed from the translator branch (stay on lead branch until their roots) |
| 2d | uam and sdp2dksh roots (DKSH for deko3d); bison/flex/mako pinned in env.json, generated steps | - | - | after the first frame (L1, L6A); generated files as patch content refused (second path) |
| 2e | Guest FFP programs without uam: front end emits GLSL + content key; DKSH from deko3d's own pack later; no empty DKSH arrays | session_01KmtZKUrHj7PAAvjM7QVXb4 (w2b; w2 to be archived after w2b pushes) | claude/fnv-l6-render-w2b | done (merged w2b at 61443784): NxShaderDesc 12d500e (48 bytes, sha256 key, size+offset asserts), FFP GLSL + key 7be0994 (gen_glsl.py generated step, no DKSH). render/d3d9: 70 units, ffp_prog.cpp builds; cold x2 identical (154 stages, 46/34 s), warm 154/154 hit 3 s (local admit for include roots). w2 archived. test_ffpdev.py on the GLSL+key step + merged-head build (w2b 7d4fa49, merged 8d5c42b4: 70 units, cold x2 33/34 s, warm 154 hit 3 s, tests pass). gpu_abi.h quoted include "state.h" done (w2b 5d3c0e7, merged). Open (w2b): include L5 shim/include/fnvnx_render_hooks.h (extern "C", NxEnvProfile fixed int) and delete render's local declarations in one commit when L5 w10 sends it |
| 2a | Recipe stages render needs: host root from external sources (MojoShader, uam); generated step running a built host ELF (FFP GLSL -> DKSH, SDP -> DKSH) | - | - | done at L1 (on remote, not yet trunk): w16 d91524a3 (rule A external sources+patches, tool roots, rule B tool generated step), w15 559fc150 (link.roots, variants) |
| 2b | Markers (stubs/no-op/refusals) in render -> 0. Count = objstub findings (classes 1, 2) on the render root's objects; the "29" of the orchestrator audit is re-measured by the gate, not by text | session_01KmtZKQbpDKvc9qzXT9bLHX (w6); census w5 | claude/fnv-l6-render-w6 | doing: w6 8d20d51e: objstub green 1772/1772, 0 findings over 69 units (was 25 at 24f18cdb); wine_fixme rows 48-54; Draw*Patch now D3D_OK per Wine 9.0. Before merge: one wine_fixme table (verify/), ids generated, gen_env.py caps assertion corrected, measure 70/70 |
| 3 | Doubles in tests -> 0. Census (partial) at 06097801: claude/fnv-l6-w-doubles render/DOUBLES.md | session_01KmtZKS7moRHazN3AonGwXJ (w5) | claude/fnv-l6-render-w5 | doing: L1 form (w5 de1cc161): test = admitted root class test, guest ELF linked via link.roots, run on L4 boot machine, oracle record each; host -m32 tests retired. Census 17/14/30/173 unchanged; every rewrite blocked by an owner (closure map). w5 now: oracle reference census of all 40 drivers (render/ORACLES.md) |
| 4 | d3d9total gate + render/total tool root on a trunk-based branch, to MERGER when L1 w16 pins libc6-dev-i386, libclang-common-18-dev (on the path: goal step 1 needs the 13 REQUIRED_PRODUCT gates) | session_01KmtZKUN17roaVoBLBe4QKK (w4) | claude/fnv-l6-render-w4 | doing: 5902362d green on trunk 29b41938 (27 gates, 247.3 s; d3d9total 332/332, selftests 57/57, admit 463/463); 7th in MERGER queue, head sent 02:50Z |
| 5 | FLAT: bit 1 = flat colour varyings from D3DRS_SHADEMODE; ps_1_x texcoord fix; shader cache key = all flags except the declared non-key set (FNVNX_SHADER_NONKEY_FLAGS) | session_01KmtZKSg7yny8K34kFfftjv (w3) | claude/fnv-l6-render-w3 77c6837 | done feca6ccf (merged). Acceptance at 97a6aae: front, tex, copy, drawgl 123, gl4, ffpdev, sm_link green; mutants drawgl 34/34, gl4 49/49, ffpdev 24/24, shcache 14/14 |
| 5a | test_sm_total: 7538/7538 ok, but 8 unresolved + 4 conflicts in render/shaders/total/sm_table.json (pre-existing at 1b74946f) | - | - | done (merged w3b d905cc34): rules R1-R4 from 3 pinned sources (msdocs-sm e18183f8, vkd3d fd97ca35, wine-sm1 0a52e82d; sm_table.py refuses other trees); test_sm_total 7550/7553 ok, 0 unresolved, 0 conflicts; d3d9total 332/332 |
| 5c | b# as vs ALU source (vs_2_0, vs_2_x, vs_3_0): legal by the pinned sources, translator emits bool where vec4 is read (3 red rows of test_sm_total). Value of a bool read as float4: from Wine 9.0 wined3d (the reference implementation) | session_01KmtZKSg7yny8K34kFfftjv (w3b) | claude/fnv-l6-render-w3b | doing: step 1 at w3b 65f7c6b: whole precompiled set (gamedata/shaders b545961f, set_sha256 7c097d1d) 15791 shaders (7631 vs, 8160 ps), b# operands 0 (ALU 0, flow 0); proof open on D3DX run-time path (D3DXAssemble/CompileShader[FromFileA] call sites unresolved, L1 asked); no fact_id until then |
| 5b | Shader bytecode key is FNV-1a 64 (not injective) in the cache and SHADER_CREATE_D3D: sha256 | - | - | next (deko3d side after first frame, L6A) |
| 6 | L1 assumption #80 (uam, translator, Wine pins) | lead | - | done: answered L1 22:40Z |
| 7 | 5 DROP rows of render/ASSUMPTIONS.md; front/gen_enums.py runs g++ -m32 (compile-only rule) | - | - | next |
| 8 | Adapter Description = one fact for L5 (FalloutPrefs.ini sD3DDevice): env_{docked,handheld}.tsv GetAdapterIdentifier(0,0), "NVIDIA GeForce GTX 470" both profiles, b4373f37 | session_01KmtZKUrHj7PAAvjM7QVXb4 (w2b) | claude/fnv-l6-render-w2b | done: render/d3d9 gen/ident_{handheld,docked}.bin (33ba56a, merged 61443784; build dies if they differ), sent to L5 + w13 |
| 9 | render/d3d9 link closure (w5 56f5ac6b, 69 objects): 80 undefined = 2 own (ffp_prog, now built), 16 guest libc/libm/libstdc++/crt, 48 vtable "[clone .fnvnx_ap8]" (L2), 14 gaps: platform hooks x6 incl. nx_env_profile (L5), nxd3d_gpu_call/alloc/free guest glue (L6A), fnvnx_wine_fixme/fnvnx_import_key (L1), fnvnx_guest_invoke/fnvnx_dispatch (L3a), __aarch64_ldadd4_relax outline atomics (L2/L1; proposed -mno-outline-atomics, A57 has no LSE) | session_01KmtZKS7moRHazN3AonGwXJ (w5) | claude/fnv-l6-render-w5 | doing (answers 02:58Z): platform hooks x6 defined in L5 platform root (27f2c2e8; trunk after L1 w16); one declaration rule agreed: shim/include/fnvnx_render_hooks.h (L5 moves, L6 deletes its 4 local declaration sites); fnvnx_guest_invoke = L3a lift/rt enter.cc, fnvnx_dispatch = L3a item 26 (generated bridges); fnvnx_wine_fixme body = L5, fnvnx_import_key* = L1 contract iat stage, fnvnx_reach.h = L1 contract/include; outline atomics: L2 decided -mno-outline-atomics (L1 recipe); 48 vtable clones: w5 97026ab7 classified (a) 48 / (b) 0: every _ZTV<C> is a WEAK definition in a render/d3d9 unit and no object defines any *.fnvnx_ap* (the defining object is also the referencing one), so L2's sdd pass must emit the clones; sent to L2 lead. Recount on 61443784 (w5 0a44845c, merged): 70 objects, 1507 globals, 78 undefined (L5 9 incl. gpu_call guest glue per L6A, L1 2, L3a 2, L2/L1 1, L2 48 vtables, libc/crt 16) |

## Global numbers

- d3d9total: 332/332 (161 enum values + 171 SM ops) at f8545efa.
- render assumptions: 129 rows (OWNER 13, removed by gate 16, by fact 8, DROP 5, REMOVE without gate 87) at 66e2f971.
- tests with doubles at 06097801 (claude/fnv-l6-w-doubles 86405ed1, partial): render tests 36: 22 double sibling files, 1 boundary double, 1 GL model only, 4 clean, 1 python only, 8 NOT measured (d3d9total, 5 sm_fix, sm_link, test_sm_total; drivers exited rc=2). Doubles in 26 files, 88 distinct symbols, 326 summed over tests. Most frequent: dev/app_unk_native.cpp. L6A tests: 2 files, 53 symbols. Global count is not final: the 8 unmeasured tests are measured next; the real GL library counts as the boundary, not as a double (CLAUDE.md 6f549875).
- render product roots admitted: 0 on the trunk; render/d3d9 recipe/1 root on claude/fnv-l6-render-w2 413db88d: 70 units, 69 compile, determinism green on 69.

- 03:05Z L1 (session_01KmtZKUn5UJpiHYPtNRT6p9): objstub fix by rule (tail b to a cited log function = bl+ret; aliases at one address judged once, each name cited), w20. After it lands: drop the asm barrier in NX_WINE_FIXME_VOID and the noinline sharing. verify/wine_fixme.tsv is the only table.
- 03:04Z L2 (session_01KmtZKRi5XRMhd5qzRaEyxC) confirmed the vtable-clone defect as an sdd bug: ap symbols go into the vtable's section and comdat group with the same binding, and an unplaceable referenced ap becomes a compile error. L2 w4 does it on claude/fnv-l2-source-w4-sdd. We re-measure the closure on its commit. L5 w10 b0cff1a: nine C-linkage hooks in fnvnx_render_hooks.h; w2b switches render over (merge after L5 moves the header to shim/include).
- 03:06Z merged w6 60a7a9da (verify/wine_fixme.tsv is the only table; ids are generated by gen/gen_wine_ids.py; const_fixme/contract_consts tables deleted; the gen_env caps assertion is cited; objstub 1784/1784, 0 findings at w6 head) and w5 f3efae23 (render/ORACLES.md: 42 drivers, external reference as primary 22/42, NONE 10). Merge head 072090e3; test_ffpdev conflict resolved to the GLSL+key step (no uam) plus gen_wine_ids. Open to L1: REF kinds for a Wine 9.0 source / MS page and for probe dumps, a test root for tests/l6.
- 03:07Z L1 answers.
  - Oracle REF kinds: `external` (a pinned fnv-nx-build tree plus a path; MS docs archived in tools/docs-* under OWNER assumption 3) and `probe` (pinned probe source, machine and sha256).
  - Reference files move into render/ref/, class `ref`, which w10 adds. Test roots: class test, guest, msvcmodel, link.roots = render/d3d9 (+translator), run on L4.
  - The contract covers rule is in w10 0371bf6 (MERGER).
  - admit stays strict by design: no product root admits while a required gate is missing. Of the 13 missing gates:
    - imports and machine are on trunk 7503d2fa;
    - datamodel: w17;
    - exepin: L1 w4;
    - r7, namegate, abi: L2 w5;
    - order, eh, addr32: L3a;
    - forms, fpexact: L3b;
    - d3d9total: our w4, at its MERGER turn.
  - translator admit is blocked until all 13 land. Owners: L1, L2, L3a, L3b, MERGER.
- 03:08Z w6 measured render/d3d9 at 26c606eb:
  - cold build: 70 objects, 155 stages run, 32.3 s;
  - warm build: 155/155 hit, 2.9 s;
  - objstub: 1784/1784, 0 findings.
- w2b 4f740818 moves render/d3d9 onto L5's fnvnx_render_hooks.h and deletes every local declaration of the nine hooks (including glue_stream.h 32-34). In a scratch tree: two cold builds give identical objects, the warm build hits 154/154, and all nine hooks are unmangled undefined names.
  - It is HELD (not merged) until L5 moves the header to shim/include with two fixes: the C++ static_assert guard, and hostapi.h reachable or dropped.
  - Blocker: L5 w10.
- w5 4391ce62 merged: NONE is 6/42 (it was 10).
  - Classes: W-rec 3, W-src 11, MS 9, SPEC 2, MODEL 10, library-doc 1, NONE 6.
  - Open for L1: a library-doc REF kind (deko3d.h).
- 03:11Z Oracle census merged (w5 a776ba8f and 7a3b67d6).
  - Of the 6 tests without a reference: each gets one, or a gate. One part is deleted; no whole test is deleted.
  - Corrected by the lead: our own headers are never a reference.
  - Implementation waits for the test-root rewrite after the first frame.
  - Reference material still pending:
    - the wine-9.0 and docs-d3d9 mirrors (w5);
    - the probe mingw pin and archived binaries (w5);
    - the render/ref class (L1 w10).
  - deko3d/libnx externals come from L6A: deko3d efc48589, libnx 518bf11a.
- 03:15Z w5 17274910 merged.
  - Mirrors in fnv-nx-build:
    - tools/wine-9.0 c8373ee4 (tag wine-9.0, 10154 files, tree 3ac9a4a0);
    - tools/docs-d3d9 56908b51 (1987 pages with a manifest, tree e19cab4c).
  - Their records, plus libnx and deko3d, are in render/total/externals.json.
  - The probe gap is NOT closed: the recorded envprobe2.exe and its dll are lost, and the compiler that built them is unknown. make_env.py now pins every mingw package and tool version and copies the binaries out for archiving.
  - Closing it needs a farm re-recording of env_{handheld,docked}.tsv: Wine 9.0 and Xvfb, 232251 queries x 2 profiles x 2 runs. It is off the first-frame path: the current dumps stay as generator input, identified by sha256 but not yet a probe reference. Asked the orchestrator for the farm slot.
  - Gap: IDirect3DSwapChain9Ex::GetPresentStats has no MS page in either upstream repo, so test_m1's clock rule rests on the Wine source alone.
- 03:16Z Env-dump farm STARTED on the orchestrator's order (a non-reproducible generator input is a determinism defect on the boot path). Farm session_01KmtZKSSTwzATWBSyXQRW2Q, branch claude/fnv-l6-render-farm-env. Status: doing. The job: pinned mingw-w64 + Wine 9.0 + Xvfb; 232251 queries x 2 profiles x 2 runs; byte-equal runs, else report the differing queries as a class; publish to fnv-nx-build tools/envprobe-bin with commit + tree_sha256; list every differing query before swapping the input.
- 03:19Z Hash census merged (w5 34b7b086, render/HASHES.md, measured at f66de58e).
  - Census: 8 content-key sites use 64-bit FNV-1a. 5 of them are one function written five times (C: translate.c:13, res_shader.cpp:12, shcache_key.c:6; Python: 3). The ABI field NxShaderD3D.hash is 8 bytes.
  - Lead decision (rule): every content key is sha256 of the content bytes, 32 bytes. A 64-bit non-cryptographic key can collide and select the wrong shader, a run-time failure with no gate.
    - NxShaderD3D.hash becomes a 32-byte key, the same as NxShaderDesc.key.
    - The shcache index and file names use the full digest.
    - Python keeps hashlib.
  - Implementation: one portable C99 function on both sides, fnvnx_sha256 (loader/src/sha256.c), reached through link.roots of ONE shared root, never copied. libnx sha256 is rejected because it needs ARMv8 crypto asm that L4 would have to emulate.
  - Not keys, so they stay:
    - draw_ids.cpp:14, a bucket index where every hit is confirmed by memcmp;
    - the integrity checksums: the shcache body hash and rec_chk.
  - Blocked: loader/ is not a recipe root. The owner of the shared sha256 root is asked of the orchestrator. The ABI change goes to L6A.
- 03:20Z Orchestrator accepted the sha256 key rule (OWNER assumption 2).
  - Shared root lib/sha256: owner L4, admitted by L1, class product, guest + host.
  - w5 does phase 1: move fnvnx_sha256 there unchanged; the loader keeps building by path; NIST vectors pass. L4 reviews before it enters their branch.
  - Phase 2 follows: the 8 key sites become calls into it, the 5 copies are deleted, and NxShaderD3D.hash becomes key[32] in one ABI commit with L6A.
  - Asked L1: one root for two targets, and the gate clause for a renamed second copy.
- 03:22Z Key ABI diff sent to L6A: NxShaderD3D.hash (u64) becomes key[32] = sha256(bytecode); the struct goes from 40 to 64 bytes, with offset asserts. dk takes the NXSP v2 D-table with byte compare (no sha256 needed); gl4 links lib/sha256. lib/sha256 review is L4 item 14 (session_01KmtZKUcrakBqKb1mC8iXp4): one definition across roots, the FIPS/CAVP vectors, guest/host/ASan variants, and a warm run of 0 stages.
- 03:21Z L6A accepted the key diff (offsets 0/8/12/16/48/56/60, 64 bytes).
  - The commit triple:
    - ours: w5 phase 2 (render/d3d9, translator, shcache, Python), which does not touch render/backend;
    - L6A w4: the nxgpu4_shader.h header plus the dk D-table;
    - L6A w6: gl4_d3dshader.c:51 becomes fnvnx_sha256 + memcmp.
  - Each side merges the other's.
  - Blockers: lib/sha256 (L4 intake of w5 phase 1) and the L1 w15 variants/link.roots. onedef (L1 w15) catches renamed copies by exact equality of normalized bytes.
- 03:24Z w5 lib/sha256 phase 1 (cc188cf9) reviewed by the lead. The move is right: the body is unchanged and the loader Makefile takes the file by path. Recipe: 1 object, cold 2 stages run, warm 2/2 hit. 133 NIST vectors pass (Short 65, Long 64, FIPS examples 4), 0 mismatches.
  - Before L4 intake, w5 must:
    - take render/d3d9's guest ABI flags exactly (missing: -ftls-model, -frounding-math, -fshort-wchar);
    - run the CAVP Monte vectors;
    - commit the NIST driver to lib/sha256/tests.
  - The host variant needs L1 w15.
  - Asked L1 for one shared guest ABI flag set, plus a gate that all objects of an image have the same ABI flags (DW_AT_producer).
  - onedef (L1, revised): copies are caught by source provenance; body equality was dropped as a heuristic.
