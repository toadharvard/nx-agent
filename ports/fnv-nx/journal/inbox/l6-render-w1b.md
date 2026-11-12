# l6-render-w1b: render/shaders as host tool roots (steps 1-3 + drafts)

Branch claude/fnv-l6-render-w1b (from origin/claude/fnv-l6-render). Nothing was run through verify/recipe.py: origin/claude/fnv-l1-contract-w16 and
the trunk (claude/fnv-nx-clean) do not exist on the remote, so there is no recipe.py/RECIPE.md to check against. Stopped after step 3 + the drafts.

## Step 1: tree_sha256 (verify/cas.py ingest of `git archive` of the pin)
- tools/uam 5a5afc2bae8b55409ab36ba45be63fcb73f68993 = 8dbb502db95ccd73e5be58e83a4862be2067540ed5640b65364a71ef9d67201b
- tools/mojoshader ad5dff84830c2863c841f4b1f4e3df78c705b383 = e49a1b65e09c86647fd8c0b26c7a8e16e4a16e6ad1290a1e7ada85047306f361 (matches the brief)

## Step 2: census of build.sh (global, whole sets)
- MojoShader (CMake, Release, PIC, profiles GLSL/GLSLES/GLSLES3/BYTECODE on, D3D/HLSL/ARB1/ARB1_NV/METAL/SPIRV/GLSPIRV/GLSL120 + effects off): 13 units
  (mojoshader.c, _common, _opengl, _d3d11, _sdlgpu, profiles/{arb1,bytecode,d3d,hlsl,glsl,metal,spirv,common}.c), flags `-O3 -DNDEBUG -fPIC -Wall -ggdb3`,
  defines SUPPORT_PROFILE_{ARB1,ARB1_NV,D3D,GLSL120,GLSPIRV,HLSL,METAL,SPIRV}=0, include `.` (the three dxvk-native dirs do not exist).
  Generated: `mojoshader_version.h` (CMake WRITE_FILE: version -1, changeset "???"), `fnvnx_sm_rows.h` (build.sh copies total/fnvnx_sm_rows.h).
- uam (meson, release): libuam.a = 179 units (136 C++, 43 C); uam ELF = 180 (+ source/main.cpp). Flags: C `-std=c99 -Werror=implicit-function-declaration`,
  C++ `-std=c++11 -Wno-class-memaccess -Wno-non-virtual-dtor`, both `-O3 -DNDEBUG -Wall -D_FILE_OFFSET_BITS=64 -DDESKTOP -D_USE_MATH_DEFINES -D_GNU_SOURCE
  -DHAVE_POSIX_MEMALIGN -ffunction-sections` (+`-fPIC` in the lib), include `. mesa-imported mesa-imported/glsl mesa-imported/glsl/glcpp`, link `-Wl,--gc-sections`.
  meson-generated: glsl_parser.{cpp,h} (bison -p _mesa_glsl_), glsl_lexer.cpp (flex), glcpp-parse.{c,h} (bison -p glcpp_parser_), glcpp-lex.c (flex),
  ir_expression_operation{,_constant,_strings}.h (python+mako) = 9 files, 4 of them compiled units.
- libfnvnx_shaders + sdp2dksh (CMake): units src/{sdp,frontend,backend_mojoshader,translate}.c, src/dk.cpp, src/sdp2dksh.c; `-std=gnu99`/`gnu++17`, Release,
  include `include`, `src`, mojoshader dir, `uam/source`; link libmojoshader.a libuam.a -lm -lpthread (C++ link).
- Consumers of the products found by grep: the `uam` ELF (selftest/build_dksh.sh, d3d9/build_guest.sh), libfnvnx_shaders+libmojoshader+libuam (cache/shxlate.c, tests/s6b_*, tests/s5).

## Step 3: patches (render/shaders/patches/, see patches/README.md for the generation commands)
- mojoshader_units.patch: deletes mojoshader_effects.c + 6 utils/*.c, adds mojoshader_version.h. Units = 13 = the built set. fnvnx_sm_rows.h = recipe `generated` python step (gen_sm_rows.py, checked byte-equal).
- uam_gen.patch: the 9 generated files as tracked content, produced from the pinned tree (bison 3.8.2, flex 2.6.4, python+mako). Equal to meson's output except #line names. No tracked uam source is unbuilt, so no uam deletions.
- uam_nomain.patch: deletes source/main.cpp for the sdp2dksh root (179 units); the uam root keeps it (180).
- All chains apply to the pinned trees (patch -p1 on git archive): mojoshader+units tree 7af1fe43..., uam+gen tree 4e16a499... (cas.py hashes in patches/README.md).
Draft roots: render/shaders/roots/uam/build.json, render/shaders/roots/sdp2dksh/build.json (keys per the brief; not validated by recipe.py).

## Questions / blockers for L1 (w16)
Q1. sdp2dksh links two externals (mojoshader, uam) plus our own sources. A root with one `sources.external` cannot express it. Needed: either `sources` as a list (each with own patches/include/flags, as drafted), or root-to-root linking (the uam root exports its unit objects).
Q2. This branch's verify/env.json has no x86_64 g++ (only gcc-13/cpp-13-x86-64, libstdc++-13-dev; programs list has `gcc`, no g++). The trunk's may differ; uam and dk.cpp are C++ (137 + 1 units).
Q3. Generated bison/flex/mako content: tracked patch (drafted) or pin bison+flex+m4+mako in env.json as recipe tools. The tracked patch is 763 KB of derived text; owner/L1 choice.
Q4. Unit-cache sharing: both roots use identical uam flags (incl. -fPIC), so 179 of 180 uam units are shared. Keep it so.
Q5. Not covered yet: shxlate (cache/shxlate.c) and tests/s6b_* roots; libfnvnx_shaders as an archive output.

## Follow-up (lead's order after review of c89fdcc)
- verify/ of origin/claude/fnv-nx-clean (a2453fe9) is taken into this branch as the measurement base (committed; admit.json unchanged).
- `recipe.py check` on the drafts, rewritten to the trunk's keys (format, compiler x86_64-linux-gnu-gcc-13/g++-13, flags, include, units all, external with commit+tree_sha256+at),
  with temporary admit entries (class product, target host; not committed). The trunk's keys alone gave only these findings, besides the w16 key `sources` (unknown key):
  1. `link must be {script: tracked file of the root, output: NAME.elf, flags}`: a host link has no linker script and the output names are `uam`, `sdp2dksh`.
     w16 must allow a host link by the driver without `-T`, and output names without `.elf` (or the products get `.elf` names; consumers use `uam`/`sdp2dksh` today).
  2. `include` of `render/shaders/include` and `render/shaders/src` and `generated[].tool` `render/shaders/total/gen_sm_rows.py`: "not inside an admitted root". render/shaders must be an admitted root (tool or product) first.
  3. Q6 (new): the sdp2dksh root's own units (src/*.c, dk.cpp) sit in render/shaders/src, next to render/shaders/cache/shxlate.c (has a main). A root = render/shaders would compile every tracked unit under it (shcache, shxlate...). Either move src/ + include/ into a root directory of their own (e.g. render/shaders/translator/, with build.json) or admit sub-roots; I did not move files.
  4. External pins are accepted as written (names, commits, tree_sha256, `at` /ext/uam, /ext/mojoshader).
- Patches on externals (`sources.patches`) and the multi-external root are w16 keys; `check` cannot judge them.
- Selftest tests/l6/shaders_gen/test_uam_gen.py: run once with bison 3.8.2 (distro), flex 2.6.4 (built from the westes tarball sha256 e87aae032bf07c26f85ac0ed3250998c37621d95f8bd748b31f15b33c45ee995), mako 1.4.3: tree_sha256 of the input = the pin, 9/9 generated files byte-equal to uam_gen.patch, ALL OK.

## RE-SCOPE RESULT (GL path first): host library root render/shaders/translator
Root `ports/fnv-nx/render/shaders/translator` (class product, target host, link null; needs an admit.json entry + `render/shaders/total` admitted as a tool root for the generator).
- Own units (tracked, units=all): translator/src/{frontend.c, backend_mojoshader.c, translate.c} = 3. Headers: translator/include/{fnvnx_shaders.h, fnvnx_dk_layout.h, fnvnx_sdp.h}, translator/src/backend.h.
- External mojoshader (`sources`, w16) with patches `mojoshader.patch` + `patches/mojoshader_units.patch`: 13 units. Generated: fnvnx_sm_rows.h (gen_sm_rows.py), 1 header. Total 16 units.
- No dk.cpp, no uam reference in the root. translate.c was split: `fnvnx_shader_translate_glsl` (GLSL half, result in r->glsl, always) stays in the root;
  `fnvnx_shader_translate` + `fnvnx_dksh_validate` moved to render/shaders/src/translate_dksh.c (DKSH side, with dk.cpp, sdp.c, sdp2dksh.c, new src/dk_api.h holding fnvnx_dk_out/fnvnx_dk_compile).
- API used by render/d3d9/backend/gl4_d3dshader.c (all in this root): fnvnx_shader_hash, fnvnx_shader_opts, fnvnx_shader_result, fnvnx_shader_result_free, and ONE CHANGE
  NEEDED in gl4_d3dshader.c (L6A's file, not edited by me): call `fnvnx_shader_translate_glsl` instead of `fnvnx_shader_translate` (it already passes NO_DKSH|KEEP_GLSL; same args, same result).
  Include path: `translator/include` (render/shaders/include moved there; references updated in d3d9/dev, d3d9/backend, cache, tests/l6, CMakeLists.txt).
- recipe.py check (trunk a2453fe9, temp admit entries, not committed): `recipe valid` with `sources` removed; with `sources`: only "unknown key 'sources'" (w16). w16/w15 are still not on the remote.
- Removed: patches/uam_gen.patch, uam_nomain.patch, roots/ drafts, tests/l6/shaders_gen (it checked uam_gen.patch).

## AFTER THE FIRST FRAME (drafts, not in the tree)
- Pin in env.json: bison 3.8.2, flex 2.6.4 (westes tarball sha256 e87aae032bf07c26f85ac0ed3250998c37621d95f8bd748b31f15b33c45ee995), python3-mako 1.4.3.
- uam (tools/uam 5a5afc2b..., tree_sha256 8dbb502d...): 9 generated files from bison/flex/mako as `generated` steps with "tool": {"env": ...}
  (glsl_parser.{cpp,h}: bison -p _mesa_glsl_; glsl_lexer.cpp: flex; glcpp-parse.{c,h}: bison -p glcpp_parser_; glcpp-lex.c: flex; ir_expression_operation{,_constant,_strings}.h: python+mako).
- Root `uam_lib` (link null) = 179 units, reused by sdp2dksh and the uam ELF via link.roots; uam ELF root patch reduces to adding main.cpp (180 units). Same flags in all, so units cache once.
- Root sdp2dksh: own src/*.c(pp) (dk.cpp, sdp.c, translate_dksh.c, sdp2dksh.c) + link.roots = [glsl, uam_lib]; flags gnu99/gnu++17; link -lm -lpthread.

## w16 build of the translator root (measurement base: verify/ of origin/claude/fnv-l1-contract-w16 d91524a3, copied as files; my branch has unrelated history, so no merge)
- w15 (559fc150) was NOT merged: it conflicts with w16 in verify/recipe.py, RECIPE.md and tests/test_recipe.py (checked in a scratch worktree, aborted). That merge is L1's.
- Root render/shaders/translator, admit class "tool" + target host (w16 rules; gates selftests). `sources` = {external mojoshader, dir ".", patches [mojoshader.patch, mojoshader_units.patch] (moved into the root, because patches are root-relative), include ["."]}.
  All references to render/shaders/mojoshader.patch were updated (build.sh, mkshcache.py, gate_d3d9total.py + its test, ASSUMPTIONS.md, TRUNK.md, READMEs). mkshcache.py build_id listed a nonexistent "include" dir after my earlier move: fixed to "translator".
- Fixed bugs of my split: backend.h and dk_api.h had lost their closing #ifdef/#endif.
- gen_sm_rows.py got a recipe form `TABLE OUT` (the sandbox mounts the tool read-only; it wrote next to itself).
- Build (cold = fresh store, jobs 4): check "recipe valid"; cold1 green 16 objects, stages run 34 hit 0, 7.3 s; cold2 (fresh store) green 16 objects, run 34 hit 0, 5.9 s; warm (cold1 store) green, run 0 hit 34, 0.17 s.
  Determinism: the 16 .o files of cold1, cold2 and warm are byte-identical (diff -r); objects.json differs only in the out-dir path.

## L1 answers Q1-Q3 (lead message): declaration of the MojoShader configure step, and what recipe.py must support
Done now: admit.json: render/shaders/translator is class product, target host, all 32 REQUIRED_PRODUCT gates, link null. Include dirs: only translator/include.
Not done (needs L1): mojoshader_units.patch stays until the step exists (deleting it now makes the unit set wrong: effects.c + 6 utils/*.c would build).
Declaration (what I will write into build.json once the keys exist; it is NOT in build.json, no second path):
- Step: `cmake -S <patched tree> -B /out/cmake -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DCMAKE_EXPORT_COMPILE_COMMANDS=ON (not needed once the File API is used)
  -DPROFILE_D3D=OFF -DPROFILE_HLSL=OFF -DPROFILE_ARB1=OFF -DPROFILE_ARB1_NV=OFF -DPROFILE_METAL=OFF -DPROFILE_SPIRV=OFF -DPROFILE_GLSPIRV=OFF -DPROFILE_GLSL120=OFF
  -DPROFILE_GLSLES=ON -DPROFILE_GLSLES3=ON -DEFFECT_SUPPORT=OFF` (options declared once; the SUPPORT_PROFILE_*=0 defines then come from CMake itself, not from build.json flags).
- Inputs: the PATCHED tree (stage patch:<root> output, i.e. mojoshader.patch applied), cmake from env.json, ninja or make, the host compiler for CMake's compiler checks.
- Outputs: compile_commands.json and mojoshader_version.h. CMake WRITE_FILEs the header INTO THE SOURCE DIR (version -1, changeset "???" without .git): the step must run on a writable copy of the tree
  and emit the header as an output at ext/mojoshader/mojoshader_version.h.
- What recipe.py must do: (1) a generated-step kind `configure` (cmake pinned in env.json, runs in the sandbox, no network); (2) the unit set of `sources` = the units of compile_commands.json for the TARGET `mojoshader`
  only (compile_commands also lists the utils/* programs and, with EFFECT_SUPPORT=ON, effects.c; target filter needed: either run `cmake --build --target mojoshader -n` / use the target's source list via the File API
  `cmake -E` codemodel, or compile_commands entries restricted to files of the library target); (3) per-unit flags = the entry's defines (-D) taken from it; -O3/-fPIC come from the same entry.
  Today's measured set: 13 units, flags `-O3 -DNDEBUG -fPIC -Wall` (+ -ggdb3 from Release on this CMake) + the 8 SUPPORT_PROFILE_*=0 defines.
- fnvnx_sm_rows.h stays a rule-B generated step (gen_sm_rows.py); it is not a configure output.

- CORRECTION (lead, agreed with L1): the unit set and per-unit defines come from the CMake File API (codemodel-v2): target "mojoshader", its sources and compileGroups
  (defines, flags, includes), not from compile_commands.json (it has no target field). The configure step's outputs are the File API reply files and mojoshader_version.h.
