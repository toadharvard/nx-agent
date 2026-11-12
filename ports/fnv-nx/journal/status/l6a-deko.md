# L6A DEKO status (lead: Opus, branch claude/fnv-l6a-deko)

Updated 2026-11-12. Trunk read at claude/fnv-nx-clean e35b870a (2026-11-12). CLAUDE.md on this branch = trunk e35b870a. L1 w15 (559fc150) and w16 (d91524a3) not yet on the trunk.

## Priority after the owner correction (6f549875: two backends)
GL renders in the emulator; deko3d (this layer) is the console backend. On the first-frame path L6A has only item 1
(the backend as an admitted product root, so the one build covers it). Items 2, 3, 3b, 2b wait until after the first
frame; GL is not touched. No NRO before dkall.

## Layout decision
The trunk is an unrelated history (shared paths: 20, all machinery). Trunk-bound work of L6A is done on trunk-based worker
branches (claude/fnv-l6a-deko-w<N> from claude/fnv-nx-clean); they carry only the root's files. This branch keeps status,
inbox notes and the dk_* sources of the integration lineage. The two histories are never merged.

## Acceptance of the deko3d frame (owner, CLAUDE.md "Two render backends": OpenGL is the reference)
1. **First acceptance item.** The same nxgpu command stream (the same D3D9 front end, scenes and game frames) is rendered
   by both backends; every deko3d frame is compared with the GL frame produced on the L4 boot machine with the GL
   library. A difference is a deko3d defect until a rule explains it. The check verifies deko3d; it never selects code.
2. The deko3d backend is written by reading the GL backend (render/gl4, nxgl, the renderer pair) as the reference
   implementation of the same nxgpu ABI. Nothing of GL is deleted or disabled.

## Tasks
| # | task | worker | status |
|---|---|---|---|
| 1 | dk product root (target host, LP64 module-10 image per L4 l4-verify-008 @a4b0a846; env.json cross gcc-13; newlib/libnx/deko3d headers from pinned mirrors) ports/fnv-nx/render/backend (agreed with L6: render/d3d9 is L6's root, no nesting; dk_*.c + nxgpu4_check.c + include closure), recipe.py, admitted | w4 session_01KmtZKT5Qhn2Ywp9VAbJkmt -> claude/fnv-l6a-deko-w4 | GREEN on a scratch merge of L1 w16 b57061c8 (w4 head 484b3583): 19/19 C units compiled, 2 fresh builds 38 stages run 0 hit, identical sha256 (deterministic); TLS relocations 0, STT_TLS 0; rule: undefined set ⊆ dkabi rows ∪ the pinned newlib archives the link names (libc.a, libsysbase.a) ∪ the dk.h embedder hooks; distribution spec defaults turned off by flags matching build_dk.sh (-fno-stack-protector -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0), per-root for now; L1 makes it one recipe rule (neutralising set derived from distro compiler vs msvcmodel GCC 13.3, roots may not set it, w16 follow-up), w4 reverts its per-root flags when that lands; after that change: undefined 87 external = 74/74 dkabi + 10 libc (9 in libc.a, clock_gettime in libsysbase.a) + 3 hooks, 0 outside the set; PAC/BTI instructions 0; still deterministic (2 fresh builds); stale dk.h embedder comment, w4 fixing; waits on w16 at trunk for rebase + L1 verdict; admit red: 13 product gates missing (owners per L1 table) |
| 1a | plugins in the recipe | w4 | trunk e90a7e43 has them; they load only into C++ units (RECIPE.md "Plugins"); the dk root is all C, so 0 plugins apply by the trunk rule |
| 2 | doubles: 12/12 harnesses -> real dk_* files on the L4 deko3d model (FNVNX_EXT_DK=12, verify/bootdk.py), or deleted; dkall gate | — | waits until after the first frame; then blocked by L4 P2-P3 |
| 3 | dk boundary contract: accepted L4 design l4-verify-008; dkabi.tsv (74 rows) moves to render/backend and is republished to fnv-nx-build l6a/dkabi by w4; (67 dk*, 206 sites, 7 libnx; fnv-nx-build l6a/dkabi @071b497) for the L4 model; uam as hermetic stage -> DKSH | — | doing (contract sent to L4) |
| 3b | uam as hermetic stage: DKSH + SM 5.x form table from uam's own encoder source (L4 consumes it for its interpreter and dkcover) | w5 session_01KmtZKRDzqZrLJByWwhKKFu -> claude/fnv-l6a-deko-w5 | paused, partial survey 53bad56f (claude/fnv-l6a-deko-w5): uam needs bison/flex/mako (not in env.json); gm107 emitter 121 emit fns / 149 emitInsn; AST extractor not proven. Resumes after the first frame |
| 2b | 12 harnesses -> LP64 host-test product roots linking the real backend files on module 12 | — | waits until after the first frame; then blocked by L4 P2+P3 |
| G1 | GL backend as trunk product root ports/fnv-nx/render/gl4 (path agreed with L6), target host x86_64, gl4_*.c (17) + gl4.h, gl4_funcs.h, libnxgl4.map from claude/fnv-l6-render 7f8581af; nxgpu4 ABI from render/backend | w6 session_01KmtZKQxwicx7EEmsGzNneG -> claude/fnv-l6a-deko-w6 | green with link null on a scratch merge of L1 w16 885852ac (translator w1b 85dc017f, rebased by L6 onto w16, merged): 17/17 units, 2 fresh builds 34 run, warm 34/34 hit, 17/17 sha256 equal across all runs and unchanged by the translator merges; cross-root header includes (translator/include, backend) accepted by w16, header content in the PP key; undefined: 3 translator + 8 egl symbols, libEGL.so.1 only; link waits for L1 w15 (shared kind, link.roots, variants); render/nxgpu move drafted locally (6 headers + nxgpu4_check.c, 11 include lines in backend/gl4 by census), lands with w15 together with the matching w4 change; L6 asked to rebase w1b onto b57061c8 (its verify/ copy is from w16 d91524a3) |
| A1 | NxShaderDesc ABI v: {stage, glsl_size, glsl, key[32]=sha256}, dksh removed (L6 proposal, agreed): L6 w2 edits gpu_abi.h (+ _Static_assert 48, offsetof) and the gl4_shader.c/nxgl.c field layout; L6A edits dk_shader.c (SHADER_CREATE = key lookup in nxdk_shader_pack + GLSL byte compare) and selftest | — (worker on L6's commit) | L6 landed gpu_abi.h (12d500e on claude/fnv-l6-render-w2b, merged into claude/fnv-l6-render at 61443784; FFP as GLSL+key 7be0994, 0 DKSH in the front end); w4 doing: gpu_abi.h byte-equal into render/backend, dk_shader.c SHADER_CREATE = key lookup in nxdk_shader_pack + GLSL byte compare, mismatch reported on the dkabi fn 0 cbError queue, selftest; pack completeness = static gate (L6A, to raise); tests/v1 s6n_render_reuse/s6n_lit_prepared wait for the test rewrite |
| A2 | deko shader pack key (after the first frame): must cover every translation flag except FNVNX_SHADER_NONKEY_FLAGS (KEEP_GLSL|NO_DKSH), so FLAT 0x80 (FNVNX_SHADER_FLAT_COLOR, bit 1, L6 817a0abd) and VS_COMPLETE 0x40 are in it; L6 merge claude/fnv-l6-render feca6ccf | — | after the first frame |
| 4 | no NRO to the console until dkall green | — | holds; NRO l6a/nro/796aac6c150a withdrawn |
| — | predecessor handoff (session_01KmtZKSVcxvoK4pCGdhtZit) | — | waiting for l6a-deko-handoff-opus.md |

## Global numbers
| what | value | commit |
|---|---|---|
| backend units (nx/dk_*.c + nxgpu4_check.c) | 18 + 1 = 19 | 4335c142 |
| test harnesses with doubles | 12 of 12, 137 product symbols redefined | 4c542643 |
| deko3d boundary | 67 dk* symbols, 206 call sites, 7 libnx symbols | l6a/dkabi 071b497 |
| L4 deko3d model | 0 lines | L4 at 2026-11-11 |
| product roots of L6A admitted on the trunk | 0 | trunk 58a79b3e |
