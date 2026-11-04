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
