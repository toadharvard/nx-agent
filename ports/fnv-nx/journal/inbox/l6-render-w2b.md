# l6-render-w2b: shader ABI, FFP programs without uam, adapter identity facts

Branch claude/fnv-l6-render-w2b (base claude/fnv-l6-render-w2 24f18cdb). Commits: 12d500e (item 1), 7be0994 (item 2), 33ba56a (item 3).

## Item 1: NxShaderDesc (render/backend/nx/gpu_abi.h)
{ u32 stage; u32 glsl_size; u64 glsl; u8 key[32]; }, key = sha256 of the glsl_size bytes at glsl. _Static_assert size 48, offsets stage 0, glsl_size 4, glsl 8, key 16 (<stddef.h> added). dksh_size, dksh, pad removed. Only user in render/d3d9 was dev/ffp_prog.cpp (item 2). PLAN_DK.md size 32 -> 48.
Now-broken lines for L6A (not edited):
- render/native/gl/nxgl.c:393-395 reads d->glsl / glsl_size / stage: still compiles, but no key check yet.
- nxgpu4_selftest.c and dk_shader.c (deko3d files): every use of NxShaderDesc.dksh / dksh_size / pad and the 32-byte size must go to GLSL + key (uam at the backend). render/gl4 untouched: grep it for `dksh`/`pad` of NxShaderDesc.
- tests/v1/test_s6n_render_reuse.py:82-83 and test_s6n_lit_prepared.py:42-43 set d.dksh / d.dksh_size (old tests, not edited).

## Item 2: FFP programs
- ffp/gen_glsl.py: now only emits gen/ffp_glsl.gen.h: per program (vs/ps x smooth/flat) the blob, `_size`, and `_key` (sha256 computed at generation). No uam, no DKSH. A `generated` step of render/d3d9/build.json.
- dev/ffp_prog.cpp: no backend switch, no NxGpuInfo query; fills NxShaderDesc glsl/glsl_size/key. ffp_dksh.gen.h, NX_FFP_DKSH_BUILT, the deko3d branch, `NxFfpDev::kind` deleted.
- Test render/d3d9/tests/test_ffp_glsl_key.py: recomputes sha256 of the 4 emitted blobs against the keys; 8 single-byte key flips (4 keys x first/last byte) are each caught; checks that ffp_prog.cpp uses the generated names and no DKSH name is left in the root. DEVIATION from "links the real units": it is a python check of the generated header (the exact bytes ffp_prog.cpp compiles); no host binary links the product units (needs the L4 shared model). The unit itself is built by recipe.py (below).
- Old tests/l6/ffpdev/test_ffpdev.py still calls gen_glsl.py with --uam and reads ffp_dksh.gen.h (broken, left for the owner of the "no doubles" rewrite).

## Item 3: adapter identity
gen_env.py writes gen/ident_handheld.bin and gen/ident_docked.bin (1100 bytes = T["IDENT"], the T_IDENT bytes); die() if the two images differ. Declared in the gen_env step outputs of build.json. Test render/d3d9/tests/test_env_ident.py: bytes == T_IDENT of env_table.gen.cpp, Description at 512 NUL-terminated "NVIDIA GeForce GTX 470", a flipped byte of one docked row fails the generator (die in GetAdapterIdentifier (0,1)), and the same flip in all docked identifier rows fails the cross-profile rule. Same limitation as item 2 (python test of the generator output).

## Build numbers (recipe.py, root render/d3d9, commit 33ba56a)
Local-only change (not committed): admit.json with shim, render/shaders/include, render/backend added as tool roots, otherwise `recipe.py check` is red on includes (base state, owners L1/L6A). fnv-nx-build attached with push access for the gcc-msvcmodel external.
- Units: 70, all compile (ffp_prog.cpp is now green; before: red).
- cold 1 (fresh store): green, stages run 154 hit 0, 46 s.
- cold 2 (fresh store): green, stages run 154 hit 0, 34 s.
- warm (store of cold 1): green, stages run 0 hit 154, 3 s.
- Cold builds identical: 70/70 object sha256 equal, stage_keys equal.
- Outputs by the recipe: render/d3d9 gen/ident_handheld.bin, gen/ident_docked.bin, gen/ffp_glsl.gen.h.
- L1 verdict at 33ba56a with the committed admit.json (includes not admitted): RED, 227.7 s, 23 gates. Green: pure 35/35, selftests 52/52, oracle 52/52. Red: admit 0/2240 (whole repo, base state), build 0/70, determinism 0/70, argordergate 0/62, objstub, linkstrict, n1, n2_data, n2_call, n2_vcall, xc, xunit, rtti, pcrel, bodysel, abs, disp, reach, reachlink, gpa 0/1 each; all root gates are uncertified because the root's `include` dirs are outside admitted roots (same blocker as w2). Not measured with the local admit.
