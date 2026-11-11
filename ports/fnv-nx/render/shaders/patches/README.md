# render/shaders/patches: tracked patches that make "all files of the patched external tree" the unit set

Applied in this order on the pinned trees of fnv-nx-build (git archive of the pin, no .git):

| external | pin | tree_sha256 (verify/cas.py ingest) | patches, in order |
|---|---|---|---|
| mojoshader | `tools/mojoshader` ad5dff84830c2863c841f4b1f4e3df78c705b383 | e49a1b65e09c86647fd8c0b26c7a8e16e4a16e6ad1290a1e7ada85047306f361 | `../mojoshader.patch`, `mojoshader_units.patch` |
| uam | `tools/uam` 5a5afc2bae8b55409ab36ba45be63fcb73f68993 | 8dbb502db95ccd73e5be58e83a4862be2067540ed5640b65364a71ef9d67201b | `../uam.patch`, `uam_gen.patch`, then `uam_nomain.patch` (only in the sdp2dksh root) |

Patched trees (cas.py ingest, measured at the commit of this note): mojoshader after both patches 7af1fe43..., uam after the three patches 4e16a499...

## mojoshader_units.patch
Deletes the files the CMake profile set of build.sh does not build: `mojoshader_effects.c` (EFFECT_SUPPORT=OFF) and the six `utils/*.c`
programs. Adds `mojoshader_version.h`, which CMake wrote with `MOJOSHADER_VERSION -1` and `MOJOSHADER_CHANGESET "???"` (build.sh's shallow
clone has no HEAD~, so CMake's git step always gave "???"). Result: 13 units, all built today by `ninja mojoshader`.
`fnvnx_sm_rows.h` is not in the patch: it is a recipe `generated` python step (`total/gen_sm_rows.py`, input `total/sm_table.json`,
output byte-equal to the committed `total/fnvnx_sm_rows.h`, which `gen_sm_rows.py --check` compares).

## uam_gen.patch
Adds the nine files meson generated, at their source-tree places (`mesa-imported/glsl/`, `.../glcpp/`). Produced from the pinned tree with
the patched sources (uam.patch applied), run inside the directory of the input so `#line` paths are the bare input name:
```
cd mesa-imported/glsl
bison -o glsl_parser.cpp -p _mesa_glsl_ --defines=glsl_parser.h glsl_parser.yy        # GNU Bison 3.8.2
flex  -o glsl_lexer.cpp glsl_lexer.ll                                                  # flex 2.6.4 (westes tarball sha256 e87aae03...ee995)
python3 ir_expression_operation.py enum      > ir_expression_operation.h              # mako 1.x
python3 ir_expression_operation.py constant  > ir_expression_operation_constant.h
python3 ir_expression_operation.py strings   > ir_expression_operation_strings.h
cd glcpp
bison -o glcpp-parse.c -p glcpp_parser_ --defines=glcpp-parse.h glcpp-parse.y
flex  -o glcpp-lex.c glcpp-lex.l
```
Checked against the meson build of the same tree: the three `ir_expression_operation*.h` are byte-identical; the bison/flex outputs are
identical except the `#line` file names and the include-guard text of the two `.h` (6 lines each). Why tracked content and not a recipe
step: the pinned env (verify/env.json) has no bison, flex or mako, and the python modules of the sandbox are capstone, pyelftools, unicorn only.
The alternative (owner/L1 decision) is to pin bison+flex+m4 (and mako) in env.json and make them recipe tools; then this patch shrinks to nothing.

## uam_nomain.patch
Deletes `source/main.cpp` (the `uam` command line). The sdp2dksh root links uam's units as a library and has its own `main`; the `uam` root keeps main.cpp.
All other units are the same in both roots; use the same flags in both so the unit cache is shared.

## Census numbers (measured by building the pinned trees with build.sh's commands; flags in ../roots/*/build.json)
- MojoShader: 13 units after the patch, defines `SUPPORT_PROFILE_{ARB1,ARB1_NV,D3D,GLSL120,GLSPIRV,HLSL,METAL,SPIRV}=0`, `-O3 -DNDEBUG -fPIC -Wall`, include `.`.
- uam (libuam.a): 179 units = 175 tracked + `source/uam_api.cpp` (uam.patch) + 4 generated (`glsl_parser.cpp`, `glsl_lexer.cpp`, `glcpp-parse.c`, `glcpp-lex.c`) - `source/main.cpp`; 136 C++ + 43 C.
  The `uam` ELF adds main.cpp: 180 units. No tracked source of uam is unbuilt, so uam needs no deletions.
