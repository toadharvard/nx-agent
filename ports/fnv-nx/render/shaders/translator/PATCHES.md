# render/shaders/translator: how the mojoshader external is derived

The recipe takes MojoShader as the external `mojoshader` = fnv-nx-build branch `tools/mojoshader-glsldk`, pinned in build.json by commit and
tree_sha256 (verify/cas.py ingest). The recipe has no patch step, so the tree is the result of the patches below, applied in this order (`git apply`) to
the pinned tree of `tools/mojoshader` ad5dff84830c2863c841f4b1f4e3df78c705b383 (tree_sha256 e49a1b65e09c86647fd8c0b26c7a8e16e4a16e6ad1290a1e7ada85047306f361):

1. `derivation/mojoshader.patch` (glsldk profile, FLAT_COLOR via MOJOSHADER_glsldkSetFlat, the SM1-3 totality header)
2. `derivation/mojoshader_units.patch`
3. `derivation/mojoshader_prune.patch` (reach census, below)
4. `derivation/mojoshader_sm.patch`
5. `derivation/mojoshader_classes.patch` (class rules of the sm_link census, item 4.6): (a) comment blocks (CTAB, preshader) are metadata: a block that
   does not parse is dropped whole (errors and partial symbols removed, the instruction stream untouched), as D3D9 and Wine 9.0 skip comment tokens;
   (b) SRCMOD_DZ/DW outside texld/texcrd use the register as is (Wine 9.0 glsl_shader.c shader_glsl_gen_modifier :2597).

The tree of branch `tools/mojoshader-glsldk` commit 532cbebc has tree_sha256 c9b1f0d704f1c0fd9edd5c6dafab755931ee751fbe62a08b3de0439076724fd4. The unit list is
generated (render/total/translator_gen/gen_sources.py): every .c file of the external.

## mojoshader_units.patch
Deletes the files the CMake profile set of build.sh does not build: `mojoshader_effects.c` (EFFECT_SUPPORT=OFF) and the six `utils/*.c`
programs. Adds `mojoshader_version.h`, which CMake wrote with `MOJOSHADER_VERSION -1` and `MOJOSHADER_CHANGESET "???"` (build.sh's shallow
clone has no HEAD~, so CMake's git step always gave "???"). Result: 13 units, all built today by `ninja mojoshader`.
`fnvnx_sm_rows.h` is not in the patch: it is a recipe `generated` python step (`total/gen_sm_rows.py`, input `total/sm_table.json`,
output byte-equal to the committed `total/fnvnx_sm_rows.h`, which `gen_sm_rows.py --check` compares).

uam patches (`uam_gen.patch`, `uam_nomain.patch`) were removed: generated content is not tracked. See journal/inbox/l6-render-w1b.md, "after the first frame".

## mojoshader_prune.patch (reach census)
Rule: the root holds only what its ABI (the functions of include/fnvnx_shaders.h that its objects define) reaches. `tools/reach_census.py`
links the recipe's -ffunction-sections/-fdata-sections objects with `ld --gc-sections --print-gc-sections -u <root>` (binutils of env.json)
and writes reach.tsv; `tools/prune_collected.py` deletes a file with no reached code and every collected function of the other files. A
collected function that the remaining code still calls (the compiler inlined all calls; the out-of-line copy is unreferenced) stays: the loop
adds each undefined symbol of the pruned objects (a build fact) to the keep list until none is left (3 rounds). Census of the result:
589 non-empty sections, 12 collected (the 12 inlined-only helpers). Measurement only, the build is `recipe.py`.

Profile set (rule): every `SUPPORT_PROFILE_*` macro that `mojoshader_internal.h` defaults with `#ifndef` is set explicitly in build.json (13 today; `reach_census.py`
checks this against the header, so a new upstream profile cannot default to 1 unseen). It is 1 exactly for the profiles the ABI produces and what they need to
compile: GLSLDK (the profile `backend_mojoshader.c` requests) and its dependency chain GLSLES3, GLSLES, GLSL; 0 for all others (D3D, BYTECODE, HLSL, GLSL120,
ARB1, ARB1_NV, METAL, SPIRV, GLSPIRV). Census after the prune (7 objects): 493 non-empty sections, 12 collected (inlined-only helpers, see above).

## Profile set, corrected rule (BYTECODE)
The profiles compiled in are exactly those the root's own sources REQUEST by name, plus their dependency chain: `backend_mojoshader.c` asks for
MOJOSHADER_PROFILE_GLSLDK (needs GLSLES3, GLSLES, GLSL) and `frontend.c` asks for MOJOSHADER_PROFILE_BYTECODE (the parse profile of the front end).
An earlier version set BYTECODE=0 from the census alone, which compiled the request out: every translation failed at run time with
"Profile 'bytecode' is unknown or unsupported". `reach_census.py` now reads the requested names from src/*.c and exits 1 unless each is `=1` in build.json.
The prune was re-derived with BYTECODE=1 (`derivation/mojoshader_prune.patch`, loop result: 6 collected inlined-only helpers kept: get_GLSL_destarg_varname,
get_GLSL_srcarg_varname, get_GLSL_uniform_type, out_of_memory, output_GLSL_uniform_array, ver_ui32). The tree of the mirror is the pin in build.json.

## 6th patch: the declared-set rule (`derivation/mojoshader_declared.patch`)
`dk_attrib_location` / `dk_varying_location` read the front end's tables and nothing else: no canonical fallback, no `failf`. `MOJOSHADER_glsldkSetLocations`
takes the three tables as required arguments (`nonnull`), built only by `fnvnx_dk_assign`, and a glsldk parse is entered only after that call (single call site,
`backend_mojoshader.c`). Why no lookup misses: the tables hold the DECLARED set, which `compute_locations` (frontend.c) builds from the parse's own
`attributes` / `outputs` (ps_1_x implicit t#/v# included: measured on ps_1_1 `tex t0`, `mov r0, v0`: locations 2 and 0, never -1) plus the link set; a register read
without a declaration is a validator rejection (res_smcheck, class list of item 4.6). Mirror pin: commit 532cbebc, tree_sha256 c9b1f0d7…, see build.json.
