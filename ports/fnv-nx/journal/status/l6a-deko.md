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
| 1 | dk product root (target host, LP64 module-10 image per L4 l4-verify-008 @a4b0a846; env.json cross gcc-13; newlib/libnx/deko3d headers from pinned mirrors) ports/fnv-nx/render/backend (agreed with L6: render/d3d9 is L6's root, no nesting; dk_*.c + nxgpu4_check.c + include closure), recipe.py, admitted | w4 session_01KmtZKT5Qhn2Ywp9VAbJkmt -> claude/fnv-l6a-deko-w4 | red at 87323b02 (claude/fnv-l6a-deko-w4): 19 C units, 0/19 compiled; check valid; externals libnx 518bf11a, deko3d efc48589, fnvnx-a64-msvcmodel 00e3d42a (newlib + gcc builtin headers). -mtp=soft dropped (pushed after 87323b02); TLS tokens in PP outputs 0/19 units, relocation count after first objects; now red at cc:nx/dk_blit.c on newlib sys/lock.h under -Werror: -isystem for external headers: L1 agreed key "isystem" (externals only), w4 build.json uses it (0fbebe1c), but the key is on no L1 branch at 2026-11-12 02:37 (trunk e35b870a, L1 w15-w18 checked); in L1 w16 b57061c8 (with bare-as fix and GL/EGL pins), at MERGER, ETA < 1 h; w4 builds against it on a scratch merge; admit red: 13 product gates missing from gates.json, owners per L1 table (L1 w17/w8, L5, L3b, L6, L2, L3a) |
| 1a | plugins in the recipe | w4 | trunk e90a7e43 has them; they load only into C++ units (RECIPE.md "Plugins"); the dk root is all C, so 0 plugins apply by the trunk rule |
| 2 | doubles: 12/12 harnesses -> real dk_* files on the L4 deko3d model (FNVNX_EXT_DK=12, verify/bootdk.py), or deleted; dkall gate | — | waits until after the first frame; then blocked by L4 P2-P3 |
| 3 | dk boundary contract: accepted L4 design l4-verify-008; dkabi.tsv (74 rows) moves to render/backend and is republished to fnv-nx-build l6a/dkabi by w4; (67 dk*, 206 sites, 7 libnx; fnv-nx-build l6a/dkabi @071b497) for the L4 model; uam as hermetic stage -> DKSH | — | doing (contract sent to L4) |
| 3b | uam as hermetic stage: DKSH + SM 5.x form table from uam's own encoder source (L4 consumes it for its interpreter and dkcover) | w5 session_01KmtZKRDzqZrLJByWwhKKFu -> claude/fnv-l6a-deko-w5 | paused, partial survey 53bad56f (claude/fnv-l6a-deko-w5): uam needs bison/flex/mako (not in env.json); gm107 emitter 121 emit fns / 149 emitInsn; AST extractor not proven. Resumes after the first frame |
| 2b | 12 harnesses -> LP64 host-test product roots linking the real backend files on module 12 | — | waits until after the first frame; then blocked by L4 P2+P3 |
| G1 | GL backend as trunk product root ports/fnv-nx/render/gl4 (path agreed with L6), target host x86_64, gl4_*.c (17) + gl4.h, gl4_funcs.h, libnxgl4.map from claude/fnv-l6-render 7f8581af; nxgpu4 ABI from render/backend | w6 session_01KmtZKQxwicx7EEmsGzNneG -> claude/fnv-l6a-deko-w6 | w6 ran gcc -E once outside the recipe (no artifact, nothing used; corrected). red at 9a713d9 / 7746ee1 (claude/fnv-l6a-deko-w6, base trunk + w4; link block written against the announced w15 keys): 0/17 units; check valid; first red: unitstage resolves the x86_64 driver's bare `as` to cwd/as (machinery gap; L1 w16 fixes it: programs: list, then PATH, same for ld). Then: EGL/GL headers not pinned (17/17 units, L1 w16 + L4 w1b), translator root render/shaders/translator (L6 w1b, admitted a5dfbdc5, builds on L1 w16 sources key); gl4_d3dshader.c call -> fnvnx_shader_translate_glsl (w6, agreed with L6), shared link + root-to-root objects (L1 w15); link.libs = libEGL.so.1 only (all GL entry points via eglGetProcAddress, gl4_core.c:92), run-time set pinned by L4 (claude/fnv-l4-verify @1979832) |
| A1 | NxShaderDesc ABI v: {stage, glsl_size, glsl, key[32]=sha256}, dksh removed (L6 proposal, agreed): L6 w2 edits gpu_abi.h (+ _Static_assert 48, offsetof) and the gl4_shader.c/nxgl.c field layout; L6A edits dk_shader.c (SHADER_CREATE = key lookup in nxdk_shader_pack + GLSL byte compare) and selftest | — (worker on L6's commit) | waiting for L6 w2 commit |
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
