### J-XXXX | 2026-11-04 | stream: render-veg | status: wip | parent: J-0137

* **claimed** - render/native/CLAIMS.md family rows on branch `claude/fnv-render-veg`: trees (SpeedTree
  branch/frond/leaf/billboard), grass (GRASS*), particles/precipitation/bolt/beam/decal/blood/vfog. Owner
  render-veg, status open, path stays "skipped -> Translated" until each family passes the scene check against
  Translated and costs no more GPU time. Merge of the rows: render machine.
* **plan** - in order of real019 GPU cost:
  1. grass (4.93 ms on real019 handheld 720p, 3.83 of it vertex: GRASS vso ~377 Maxwell instructions x 2,000
     vertices x 300 draws). Native `grass.vert/frag.glsl`: instance data per draw, one vertex stream (position,
     uv, colour), alpha test through the compile-time epilogue permutation (`__ea`), no runtime compile.
  2. trees (1.27 ms): SpeedTree leaf / branch / frond / billboard.
  3. particles and decals.
* **metric** - per family: draws on the native path / total in the captures, the parity error against Translated,
  and the family's GPU ms on real019 handheld 720p.
  - before: grass 4.93 ms, trees 1.27 ms, particles see report.json; native draws 0 / 0 in both captures.
  - The two captures (menu_11640, docmitchell_13000) contain no grass, tree, particle or decal draws (57 draws
    each: menu tiles; Doc Mitchell's hall wall). The draw ratio and the scene check need an exterior capture
    (e.g. Goodsprings at noon, with grass, Joshua trees and dust particles). Request to the orchestrator / S5-data.
* **did** - grass family, native (item 1):
  - `shaders/grass.vert.glsl` (variants base / vl = VERTLIT / slope = FIT_TO_SLOPE / vlslope), `grass.frag.glsl`
    (base / tms = TRANSPARENCYMS, epilogue permutations __e0/__ea/__ef from build.py): hand-written against the
    layout contract (attribute locations of fnvnx_dk_layout.h: POSITION 0, NORMAL 3, COLOR 4, TEXCOORD0 6,
    TEXCOORD1 7), uam -> DKSH in the package, no runtime compile. Two varyings instead of four (the PS only uses
    T4.rgb + T5.rgb, fog colour from the block).
  - `n1/veg_families.h`, `n1/veg_grass.cpp`: NxGrassDraw (208-byte head + InstanceData, up to 236 instances = VS
    c20..c255), `nx_grass_fold()` folds the per-draw constants on the CPU, `nx_grass_vs/ps()` map the engine's
    permutation macros to package names (NULL = stays Translated).
  - Tests: `make -C tests/s6n -f veg.mk` (22 checks), `make -C tests/s6n -f veg.mk parity FNVNX_T019=<sdp2dksh -o
    dir --glsl shaderpackage019.sdp>`: a 200-instance grass field drawn with GRASS2000-2003.vso + GRASS2000[TMS].pso
    translated and with the native variants on llvmpipe; 6/6 cases equal coverage, max colour error 0.75/255.
    Mutation check: removing wind, fade, fog or shade each fails the case.
* **measured** - real019 handheld 720p, efficiency 0.6, same model run (current sdp2dksh build): grass 4.37 ms
  (vertex 3.27, GRASS vso class 321 instr) -> 1.82 ms (vertex 1.28: grass_vl.vert 126 Maxwell instr; fragment
  0.53: grass__ea.frag 57 vs the model's STLEAF pso 117.5); frame 32.60 -> 30.05 ms. Instruction counts: GRASS2000-2003
  vso 265/252/310/325 -> 128/126/176/175; GRASS2000.pso 134 -> grass__ea 57.
* **requests** - render machine: the dispatch row for the grass family (TallGrassShader passes -> nx_grass_vs/ps,
  NxGrassDraw from the shader's constant maps: ShaderVariableMap at +0x34, InstanceData pointer +0x1a4, count
  +0x1ac); G1-gpu: MODEL/REAL_MAP grass row to the native shaders once the dispatch lands.
* **did** - trees family, SpeedTree leaves (item 2):
  - `shaders/tree_leaf.vert.glsl` (STLEAF000-003: base / FOG / one point light / both), `tree_leaf.frag.glsl`
    (STLEAF2000/2001[TMS]: base / fog / tms / fogtms + epilogue permutations). Semantics: leaf corner =
    LeafBase[int(BLENDINDICES.z)] x BLENDINDICES.w, rocked about x (RockParams), billboard axes rustled about z
    (RustleParams), blended to WindMatrices[int(BLENDINDICES.y)] by BLENDINDICES.x; lighting with the corner-bent
    normal (LeafLighting.y), shade = fract(BLENDINDICES.z) on the sun term only; the point light (LightPos,
    DiffColorPt) is attenuated by 1 - sat(d / range)^2 and not shaded; fog as the grass (clip-space length, power).
  - `n1/veg_tree.cpp`: NxTreeLeafDraw (1,280 bytes), `nx_tree_leaf_fold()`, `nx_tree_leaf_vs/ps()`.
  - Parity: 6 leaf cases on a 400-leaf crown (4 wind matrices, 12 leaf clusters), coverage equal, max 1/255 (one
    pixel, alpha only, a filtering LSB; 2/255 with TMS's 2 x alpha). Mutations of rock, rustle, wind blend, shade,
    point attenuation, fog power and the corner-normal weight each fail.
* **measured** - real019's trees row is costed with the STB (branch) class; with the trees row costed as leaves
  (STLEAF vso 299 / pso 117.5 class averages) the leaves go 1.37 -> 0.65 ms (vertex 0.68 -> 0.45, fragment 0.69 ->
  0.19: tree_leaf__ea.frag 33 instr). Request to G1-gpu: split the trees row into branches / fronds / leaves.
* **did** - particles family (item 3):
  - `shaders/particle.vert.glsl`, `particle.frag.glsl` (BSParticleShader: PARTICLE.vso / PARTICLE.pso + epilogue
    permutations): the GPU particle simulation per vertex from InstanceData (3 vec4 per particle, up to 79):
    position = birth + (radial wind + velocity + variation) t + (variation x fVars0.w + acceleration) t^2 / 2,
    rotation, size and colour piecewise linear in the normalized age, atlas frame offsets.
  - `n1/veg_particle.cpp`: NxParticleDraw, `nx_particle_fold()` (1 / lifetime folded).
  - Parity: 79 particles at every age (all curve segments), 2 cases, 0/255 difference, coverage equal; 8 mutations
    (size and colour segments, rotation, wind, acceleration, atlas row, variation) each fail. The first scene
    version had integer atlas rows, which wrap and hid the row term; fixed.
  - Unit tests: veg.mk 32 checks.
* **found** - real019's particles row is costed with the "PAR" class, but PAR2000-2019 are ShadowLightShader's
  PARALLAX lighting programs, not particles (staging blk_00bc0000 builds them from lighting\2x\v\*.hlsl with
  PARALLAX). The particle programs are PARTICLE.vso (441 Maxwell instr: most of it the relative-read checks of
  vs_dk_relc) and PARTICLE.pso (114). Request to G1-gpu: REAL_MAP particles -> ("PARTICLE vso", "PARTICLE pso").
* **measured** - particles on real019 handheld 720p with the PARTICLE classes: 0.94 ms -> 0.44 ms (vertex 0.22 ->
  0.12: particle.vert 226 instr; fragment with the family's measured epilogue mix 65 none / 67 fog / 18 both:
  particle__e0 32, __ef ~70, runtime 104 instr).
* **found** - tree branches (STB2000-2019.vso) are the ShadowLightShader 2x lighting programs with TREE (wind
  matrix blend by BLENDINDICES.x/y); their pixel programs are the SLS ones, i.e. render-lit's lit family. Plan: a
  shared include for the TREE wind blend, offered to render-lit, instead of a second copy of the lit shaders.
* **did** - decals and blood (item 4): `shaders/decal.vert.glsl` (GDECAL; skin = GDECALS, 4 bones from a D3DCOLOR
  index in colour order, the fourth weight 1 - sum), `decal.frag.glsl` (GDECAL.pso: clamped uv into the atlas
  rectangle, rgb x f, a x f^2), `blood.vert/frag.glsl` (BS: light-shifted second map, mix by the weighted alpha;
  flare = BSF: clip offset by Ctrl.x, map0.r x map1 x Alpha). Blocks NxDecalDraw / NxDecalSkinDraw / NxBloodDraw
  in veg_families.h (straight copies of the constants, no fold). Parity 4/4 (decal 0.53/255, the others exact);
  8 mutations caught. The first skinned scene folded (random bone rotations up to 0.3 rad): one pixel of 29,099
  differed at a depth tie; the palette is now mild (0.06 rad, 4 units) and the case is exact.
* **found** - docs/RENDER_NATIVE.md §1.6 says particles are expanded by the `PAR2` VS (20 variants): PAR2xxx are the
  PARALLAX lighting programs; particles use PARTICLE.vso (and NOLIGHTING_PSYS*). For the render machine.
* **did** - precipitation and beams (item 5): `shaders/precip.vert.glsl` (PRECIP000-003: drops wrapped in the
  [MinPos, MaxPos] box by a truncated fmod, cylindrical billboard about z or CAMERAUP-aligned, SPIN in the corner's
  xz plane, fade (1 - |offset from the box centre / size|)^2), `precip.frag.glsl` (PRECIP000/001.pso),
  `beam.vert/frag.glsl` (BEAM). Blocks NxPrecipDraw, NxBeamDraw (beam's u scale folded). Parity 5/5 (precip 1/255,
  beam exact); 8 mutations caught. 23/23 cases overall.
