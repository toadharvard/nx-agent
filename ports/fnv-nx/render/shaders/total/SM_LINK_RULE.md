# sm_link: the rule text (item 4.6, "Shaders" decision of 2026-11-14)

Status: DRAFT of the written argument. Nothing in this file is a measurement. It is the rule the gate's tool must implement;
the gate (tool root, host program linking `render/shaders/translator@host`) and its first measurement come after the host
variant lands. Mesa source below is the Ubuntu source package `mesa 25.2.8-0ubuntu0.24.04.4`
(`mesa_25.2.8.orig.tar.xz` sha256 097842f3e49d996868b38688db87b006f7d4541e93ce86d2f341d8b3e7be7c93 + its `debian/patches`;
the patches of that package touch `src/compiler/spirv/spirv_to_nir.c`, `src/compiler/nir/nir_functions.c`, `src/util/*`,
`src/vulkan/util/vk_util.h`, `src/glx/dri_common.h`, anv, ppc64el assembly: none is in `src/compiler/glsl` or the GLSL linker,
so the linker is the upstream 25.2.8 one).

## 0. Correction to the file names

In 25.2.8 there is no `linker.cpp`, `link_varyings.cpp` or `link_uniforms.c`: the GLSL linker runs on NIR and lives in
`src/compiler/glsl/`: `gl_nir_linker.c` (`gl_nir_link_glsl`, line 3620), `gl_nir_link_varyings.c`, `gl_nir_link_uniforms.c`,
`gl_nir_link_uniform_blocks.c`, `gl_nir_link_interface_blocks.c`, `gl_nir_link_atomics.c`, `linker_util.cpp`. The rule cites those.

## 1. What is checked

For a vertex shader V (stage VS) and a fragment shader P (stage PS), both compiled by the pinned Mesa as GLSL 4.60 (translator
output, `#version 450`/460 as emitted), `glLinkProgram` of {V, P} succeeds. The gate's result per form id is the link status.

## 2. The signature (what a pair class is made of)

The lead's condition (i) asks for the signature to come from the program-interface query. Finding: the query alone is NOT
complete. `GL_PROGRAM_INPUT/OUTPUT` list only active resources, but the linker's checks read `data.used` and `data.assigned`
(gl_nir_link_varyings.c:`gl_nir_cross_validate_outputs_to_inputs`, lines 830-990: a declared output that is not read still has its
type/qualifiers cross-validated against an input of the same name when one exists; a declared input that is not `used` is never
an error for a missing output). So a declared-but-inactive output is part of the cross-stage function. Rule:

    sig(stage) = ( stage, GLSL version,
                   D = the ordered list of declarations of the stage, each
                       (mode in/out, name, glsl type, array size, location, component, interpolation, centroid, sample, patch,
                        invariant, used, assigned),
                   U = default-block uniforms (name, type, array size, location, binding),
                   B = blocks (name, instance name, layout, binding, ordered members: name, type, array size, offset),
                   S = samplers (name, type, binding),
                   C = counts entering a combined limit: texture image units, uniform components, input/output components ).

D, U, B, S, C are read from the compiled stage in two ways that must agree, and the gate is red if they disagree:
(a) the pinned Mesa program-interface query on a SEPARABLE single-stage program (active resources: GL_PROGRAM_INPUT,
GL_PROGRAM_OUTPUT, GL_UNIFORM, GL_UNIFORM_BLOCK, GL_ACTIVE_RESOURCES) and (b) the translator's emission grammar of section 4,
which fixes the declared-but-inactive remainder by construction. Reflection JSON is not used.

## 3. Cross-stage checks of `gl_nir_link_glsl` and each one's dependence

"Function of (sig_V, sig_P)" is shown by reading the cited function: it takes only the two linked shaders (their NIR variable
lists) and `prog` constants fixed by the program (GLSL version, IsES = false, SeparateShader = false, `consts` of the context).

| # | check | Mesa 25.2.8 | spec | reads | class |
|---|---|---|---|---|---|
| X1 | output-to-input match by name; "no matching output" when input used and GLSL >= 4.20 | gl_nir_link_varyings.c `gl_nir_cross_validate_outputs_to_inputs` 830-990, `static_input_output_matching` | GLSL 4.60 4.3.4 / 4.3.6 | D(V.out), D(P.in) incl. used | signature |
| X2 | explicit-location match: input at (location, component) needs an output at the same slot; aliasing/overlap by `check_location_aliasing`, `validate_explicit_variable_location` | same file 417-742, 743 | 4.4.1 | D locations, components, types, qualifiers | signature |
| X3 | type equality (`cross_validate_types_and_qualifiers` 146-): arrays, struct compare | same, 146-340 | 4.3.4 | D types | signature |
| X4 | qualifier equality: sample, patch (always), centroid (check disabled), invariant (< 4.20), interpolation (< 4.40) | same, 235-340 | 4.5, 4.3.4 | D qualifiers, GLSL version | signature |
| X5 | interface blocks between stages (name, member list) | gl_nir_link_interface_blocks.c `gl_nir_validate_interstage_inout_blocks` 605-; `interstage_match` | 4.3.9 | B (in/out blocks) | signature |
| X6 | uniform blocks shared by name across stages | same file `gl_nir_validate_interstage_uniform_blocks` 751- | 4.4.5 | B (uniform blocks) | signature |
| X7 | default-block uniforms and samplers with the same name in both stages: same type, location, binding, precision, initializer | gl_nir_linker.c `cross_validate_uniforms` 2011, `cross_validate_globals` 1719-2010 | 4.3.5 | U, S | signature |
| X8 | first/last interface explicit locations | gl_nir_link_varyings.c `gl_nir_validate_first_and_last_interface_explicit_locations` 743 | 4.4.1 | D | signature |
| X9 | varying slot assignment, packing, "too many ... components/vectors" | `gl_nir_link_varyings` 4323-, `linker_error_io_limit_exceeded` 3897, 4226-4287 | 4.3.4, 7.3 (MAX_VARYING_COMPONENTS) | D, C | signature |
| X10 | vertex-input / fragment-output count | gl_nir_link_varyings.c 1375-1470 `gl_assign_attribute_or_color_locations` | 4.4.1 | per-stage D only | per stage (not cross-stage) |
| X11 | GLSL <= 1.20 varying not written | gl_nir_link_varyings.c 4040-4062 | 4.3.4 (old) | GLSL version | does not occur: version >= 450 (checked by the gate) |
| X12 | combined resources: combined texture image units, combined uniform blocks, uniform components | linker_util.cpp `link_util_check_uniform_resources` 300-370; gl_nir_linker.c 3194-3210 | 7.3 / Table 23.57 | S, B, C of both | signature (sums over the two sigs) |
| X13 | `validate_sampler_array_indexing`, atomic counters, subroutines, xfb | gl_nir_linker.c | 4.1.7, 4.4.x | none in the translator grammar (no sampler arrays, no atomics, no subroutines, no xfb) | closed by section 4 |

Every row's reads are NIR variable lists and counts of the two linked shaders: no row reads the shader bodies except through
`used`/`assigned`/the counts, all in the signature. Therefore two pairs (V, P) and (V', P') with sig(V)=sig(V') and
sig(P)=sig(P') have the same link outcome. A row the gate cannot show to be a function of the signature (none known today, but the
table is the checklist) makes that class exhaustive.

Not covered by this argument, and stated as what they are: (1) per-stage compile errors are checked on every unique bytecode
(443 VS + 1,366 PS) and every variant, not by class; (2) the per-stage limits (X10) are per stage; (3) the argument shows the
signature determines the outcome of the linker's own checks; it does not show the NIR backend of llvmpipe accepts the program.
The checker is the Mesa linker as the contract states; llvmpipe code generation is outside the gate.

## 4. Closure of the declaration grammar (why D, U, B, S are small and fixed)

The translator's GLSL profile (`profiles/mojoshader_profile_glsl.c` of the patched mojoshader) is the only producer of
declarations. Its emission sites are a finite list; every declaration is one of:

- varyings: `layout(location = N) in|out highp vec4 NAME;` (sites at lines 115, 919, 1050, 1300, 1318, 1778, 1905): explicit
  location, `vec4`, no struct, no interface block, no arrays, no interpolation qualifier except the FLAT_COLOR variant's `flat`
  on COLOR0 (variant bit 1);
- UBOs with fixed names per stage: `layout(std140, binding = 0|1|2) uniform vs_/ps_ ... { ... };` (lines 743, 751, 1251, 1253):
  block names differ between stages, so X6 compares nothing;
- samplers: `layout(binding = K) uniform sampler2D/samplerCube/sampler3D NAME;` (lines 1556-1589);
- default-block uniforms `uniform vec4 NAME[SIZE];`, `uniform float vpFlip;`, `uniform vec2 vposFlip;` (lines 1120, 1266, 1409, 1996).

The gate checks this by static rule, not by pattern guess: the list of emission sites is generated from the profile source by
parsing every `output_line` call whose format begins with `layout`/`uniform`/`in`/`out` and refuses (red) a site it cannot
classify. A new emission site fails the build until classified.

## 5. The variant universe (condition (ii)), read from render/d3d9 at 65b14eac

Source: `render/d3d9/dev/draw_prog.cpp` (`nx_prog_resolve`, `nx_link_set`, `shader_prog`, P1-P4) and `fnvnx_shaders.h`
(key mask = complement of FNVNX_SHADER_NONKEY_FLAGS).

A program stage is `(bytecode, stage, variant, L)`:
- `variant` bit 0 = `fnvnx_dk_vs_needs_complete(vw, pr)` for a VS only, a function of the pair (VS written set, PS read set);
  a PS has 0; a fixed-function VS is complete by construction (draw_ffp.h) with no variant of its own.
- `variant` bit 1 = FLAT_COLOR on both stages of the pair: free, 2 values (`nx_ffp_flat` from D3DRS_SHADEMODE).
- `L` = `nx_link_set`: empty when every pair of VS-out (without POSITION0/PSIZE0) and PS-in is canonical, else the ascending union;
  a function of the pair, so it is not a free parameter.
- EPI variants (`FNVNX_SHADER_EPI_STATIC/ALPHA/FOG`) and NUMERICS_NINE change PS bodies, not declarations; they are in the
  per-stage compile universe (5 EPI states: the runtime shader and the 4 static ALPHA/FOG combinations, x 2 NUMERICS) and are not part of the link signature. This is the open point to
  close with the d3d9 owner: the epilogue is declared inside the PS GLSL and could in principle add a uniform; the gate
  enumerates them for compile and the signature read (section 2) is taken on every one.

Per bytecode the compile universe is therefore: VS 2 (complete) x 2 (flat) = 4 per pair context; PS 2 (flat) x 5 (EPI) x 2 (numerics) = 20. These
are counted by the gate over the keys, and the (complete, L) of a VS depend on the paired PS, so VS variants are enumerated per
signature class pair, not per bytecode alone.

Open points (not guessed, reported):
1. Fixed-function programs (`nx_ffp_vs_program`, `nx_ffp_ps_program`) pair with translated stages. Their declarations come from
   render/d3d9/ffp, not from gamedata. Their finite set of signatures must enter the pair universe; owner: the FFP module (D5b).
2. A VS/PS pair can include a user shader against an FFP stage; "complete" is then decided by the draw path (draw_prog.cpp P3).
3. Whether the Mesa pin enters the recipe as an `external` or an env.json package (L4 asked by the L6 lead).
