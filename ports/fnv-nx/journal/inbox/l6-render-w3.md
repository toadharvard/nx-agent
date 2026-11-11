# l6-render-w3 (worker W3, branch claude/fnv-l6-render-w3)

Head: see the report message; the work commits are ac8630c (merge), e40feb6 (test enabled), 97a6aae (defect fix).
Bit 1 (FNVNX_SHADER_FLAT_COLOR, variant bit 1) is defined by 817a0abd (an ancestor of this head); L6A: use 817a0abd.

## 1. Merge
origin/claude/fnv-l6-w-int @ 19c9620 merged (no-ff) into origin/claude/fnv-l6-render @ 1b74946 as ac8630c. Conflicts: none (git merged all 16 files automatically; both sides touched disjoint hunks). Nothing to resolve.

## 2. Rule check (FLAT)
Rule: a varying gets `flat` iff variant bit 1 is set AND its usage is COLOR; no other varying does. Sites (mojoshader.patch): dk_layout (stage inputs and outputs by usage == COLOR, never vertex attributes), the unowned padding slots of a complete VS (q/16 == COLOR), dk_centroid (a flat input is not centroid-read). ps_1_x v0/v1 and ps_3_0 COLOR-declared registers are COLOR usage; texcoord/fog/t# never flat. The bit comes from D3DRS_SHADEMODE only (nx_ffp_flat, ffp_prog.cpp, draw_prog.cpp:84), both stages of a pair carry it; it is part of the backend memo key (gl4_d3dshader.c key.variant) and of the descriptor check (variant <= 3, pixel stage bit 0 clear).
Wine 9.0 (raw.githubusercontent.com/wine-mirror/wine/wine-9.0/dlls/wined3d): state.c state_shademode (346-366: FLAT -> glShadeModel(GL_FLAT), GOURAUD and PHONG -> GL_SMOOTH); glsl_shader.c declare_in_varying / declare_out_varying (1891-1928: `flat` only when the caller's flat argument is set), used for ffp_varying_diffuse / ffp_varying_specular only (7181-7182, 7774-7776, 8075-8076, 9099-9100, 9714-9715) while ffp_varying_texcoord is always declared with FALSE (7777); `flatshading` is compared in the program key (8618); the vs3 rasterizer input setup takes it from the state (10424-10427).
Caveat: Wine flat-shades the legacy colour varyings (diffuse/specular); for SM3 registers declared COLOR we follow the D3D9 meaning (shade mode applies to the colour semantic). Not verified against a Windows run.
CLOSED later (lead's request): the cache key flag mask was a hand list (bits 2-5) without FLAT_COLOR and VS_COMPLETE: two variants -> one key. Now SHCACHE_FLAGS_MASK = FNVNX_SHADER_KEY_FLAGS = ~FNVNX_SHADER_NONKEY_FLAGS (KEEP_GLSL|NO_DKSH), defined in fnvnx_shaders.h and parsed from it by mkshcache.py; tests/l6/shcache part c checks each of the 32 flag bits (58 checks failed before, 0 after; all parts a-g green, 14/14 mutations). deko3d must honour variant bit 1.

## 3. Defect: ps_1_1 `texcoord t0` read 0
Cause (reproduced, shade_flat_texcoord_{tt,ft}_ps11: got (0,0,0,0), want (7,2,0,230); independent of FLAT): MojoShader's parse_args_TEXCRD reads only the destination for ps < 1.4, but emit_GLSL_TEXCRD used source_args[0] (never set = r0): GLSL was `ps_t0 = (ps_r0);`. Rule: for ps_1_1..1_3 the single operand is the stage's own interpolant t#, result = clamp(t#.xyz, 0, 1), w = 1 (Wine 9.0 glsl_shader.c shader_glsl_texcoord 6272-6285, ps < 1.4 branch: clamp(ffp_texcoord[n], 0.0, 1.0); we set w = 1 as part k of drawgl states; Wine clamps w instead, they differ only for a 4-component coordinate with w != 1). The ps_1_4 texcrd path is unchanged. Whole class (every texcoord of ps 1.1-1.3, any writemask), not one shader. Test: the two ps11 cases of drawgl part j (the exclusion was removed from cases.py); they fail before (1024 pixels each) and pass after. mojoshader.patch was regenerated with git diff from the pinned tree (re-applies cleanly; only hunk offsets/context differ from the old text).

## 4. Acceptance (head 97a6aae; translator built by render/shaders/build.sh from fnv-nx-build tools/mojoshader ad5dff84 and tools/uam 5a5afc2b, toolchain hostcache/toolchain-1fc1f7b1ef44; apt: flex g++-multilib g++-mingw-w64-i686 libegl-dev libgl-dev; pip meson 1.12.1)
| test | --no-mutations | with mutations |
| front | pass | pass |
| tex | pass (a 1210812, b 5893, c 4430, d 219, e 80, h 579 checks) | pass |
| copy | pass | pass |
| drawgl | pass, 123 cases (a 6, b 17, c 13, d 7, e 9, f 4, g 4, h 17, i 12, j 26, k 8), 0 failed | pass, 34/34 mutations caught |
| gl4 | pass | pass, 49/49 caught |
| ffpdev | pass | pass, 24/24 caught |
| sm_link | pass (own mutate part 16 checks) | same run |
| test_sm_total | universe 7538, ok 7538, fail 0; EXIT 1: unresolved 8, conflicts 4 | not run twice (no mutation mode) |
test_sm_total exit 1 is outside FLAT: unresolved/conflicts come from render/shaders/total/sm_table.json data (git diff of render/shaders/total against the lead head is empty); I did not change the test.
