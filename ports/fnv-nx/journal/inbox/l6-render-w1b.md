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
