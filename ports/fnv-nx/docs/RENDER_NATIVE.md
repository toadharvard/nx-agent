# fnv-nx native renderer (stream S6n-native-render)

> **Superseded in part (2026-11-10).** The owner order and decisions of 2026-11-10 in the repository's CLAUDE.md, and MASTER_PLAN4 (private: fnv-nx-build `plan/master3`), override this document where they conflict.
> - **D7, D22 and D26 (destructor slot order):** replaced by SDD1.
> - **D18 (frame target):** now a 45 fps gate, 60 a stretch; perf is counted in executed instructions.
> - **D23/D24 (bodies):** native or lifted, decided by N1-N4.
> - **Removed:** stand-ins, null device, recon fleet and the heuristic slot stack.
> - **Render:** no dynamic resolution and no reduced presets. Docked output is 1080p, handheld 720p.


Re-evaluation of D6 (a Direct3D 9 front end over deko3d, [`RENDER.md`](RENDER.md)) against a **native renderer**:
replace Gamebryo's `NiDX9Renderer` and the render-time half of Bethesda's `bsshader` with code written directly on
deko3d, keep the scene graph, culling and game logic. Numbers are measured on the decomp (`ext/fnv_decomp`, same
checkout as J-0006) with the scripts named in each section; estimates are marked as such.

Reading guide: §1 how the engine renders (what we replace), §2 the replacement boundary, §3 the deko3d design, §4 the
shader plan, §5 the recommendation and milestones.

Naming: the decomp names many functions `FUN_xxxxxxxx` or after a guessed owner class; this document uses the Gamebryo /
Bethesda class names, and gives `@fnv` addresses only where the decomp itself names the function.

## 1. The engine's render architecture (as the decomp shows it)

### 1.1 Size of the render code

Functions with an `@fnv` definition, by directory (`render/native/tools` scripts, decomp `src/`):

| subsystem | functions | content |
|---|---|---|
| `src/bsshader` | 999 (231 files) | shaders 280, shader properties 174, shadows/scene nodes 180, image space 155, accumulator/batching 99, manager/util/textures 73 |
| `src/gamebryo2.2/corelibs/niconsolerenderer` (= `NiDX9Renderer` + `NiD3D*`) | 772 (103 files) | NiD3D shader framework (passes, constant maps, program factories, render-state groups) 349, renderer core 253, textures/pixel formats 89, geometry groups/VB/IB 69 |
| address block `0x00b40000-0x00c0ffff` (bsshader's link range) | 2,246 | of which 1,121 still live in `src/staging/blk_*` (not yet split into named units) |
| address block `0x00e60000-0x00eaffff` (renderer's link range) | 1,228 | of which 325 in `staging`, 36 Havok |
| `src/speedtree` (SpeedTreeRT 4.x CPU side) | 484 | tree geometry/wind/LOD on the CPU; feeds `BSTreeNode` geometry |
| `gamebryo2.2/corelibs/niparticle` | 633 | particle simulation (CPU); draws are `NiParticles` geometry |

So the render layer we would replace is about **2,300-3,400 functions** (named units + their staging share), against
68,586 functions in the decomp. The particle and SpeedTree simulations stay.

### 1.2 Frame loop

1. **Cull.** The game (`TES`/`Main` render code in `fallout*`) culls the world scene graph whose root is
   `ShadowSceneNode` (`ShadowSceneNode::AddObject 0x00b5eeb0`, `UpdateShadowLights 0x00b5c3c0`,
   `CalculateLightingAtPoint 0x00b5b4d0`) with Gamebryo's `NiCullingProcess`; `BSFadeNode`/`BSMultiBound`/occlusion
   planes prune; visible `NiGeometry` go to the **accumulator**.
2. **Accumulate.** `BSShaderAccumulator` (an `NiAccumulator`, 38 functions; `SetupDepth 0x00b65550`,
   `ClearActivePasses 0x00b65580`) asks each geometry's `BSShaderProperty` for its **render pass list**
   (`BSShaderProperty::ShaderPassList::Add 0x00ba9ee0`, `ShaderPassEntry 0x00ba8ec0`): one entry per pass, each
   entry = (geometry, pass id (true FNV numbering, §1.3), up to N lights, flags). The pass list is built by the property from its
   NIF flags (specular, skinned, vertex colours, parallax, facegen, env map, glow, ...), the lights assigned by
   `ShadowSceneNode`, fog and the shader level.
3. **Batch.** `BSBatchRenderer` (43 functions; `FindPassBucket 0x00b9a090`, `FindBucketIndex 0x00b98ae0`) buckets the
   entries **by pass id**, then by texture/geometry group inside a bucket; alpha geometry is sorted by depth
   (`ComputeAlphaDepth`). The accumulator renders buckets in pass-id order: the pass enum *is* the render order
   (z-only/depth first, opaque lighting, land, LOD, sky, alpha passes from `FIRST_ALPHA_PASS` = 568 (true numbering, RENDER_VERIFY.md §1), water,
   precipitation, sun glare, blood splatter).
4. **Draw.** For each bucket the owning `BSShader` (a `NiD3DShader`) runs Gamebryo's shader protocol: setup pass
   (pick VS/PS from the pass id, `NiD3DPass` render/sampler state through the `NiDX9RenderState` cache), update
   constants (`NiD3DShaderConstantMap` entries: globals, per-object, per-light, by name/register), set textures,
   then `NiDX9Renderer` binds streams/declaration (`NiD3DShaderDeclaration`, geometry groups) and issues
   `DrawIndexedPrimitive`. Multi-pass lighting re-draws the geometry once per pass entry.
5. **Image space.** `ImageSpaceManager` (60 functions + 95 in effect/shader files) runs end-of-frame effects on
   `BSRenderedTexture` targets (§1.7).
6. **UI.** The interface renders its tile scene graph after image space (§1.8), then `Present`.

Extra scene renders per frame: shadow maps (§1.4), water reflection (§1.5), the cube-map camera
(`BSCubeMapCamera`), local map / Pip-Boy render targets, VATS.

### 1.3 Shader families (BSShader classes), passes and shader binaries

`BSShaderManager` loads one `shaderpackage%03i.sdp` by shader level (`etShaderLevel`: 1_X, 2_0, 2_A96, 2_B96, 2_A,
2_B, 3_0) and each shader class fetches its blobs by name; the decomp also keeps each class's **developer table**
(HLSL source + `#define`s per index), which documents every permutation. Pass ids: FNV uses
**759 ids** (0 = disabled, 1..758; `COUNT` = 759), not the decomp header's `eBSSMPassEnum` (`bsshader_shader_layout.h`,
756 values), which is an older/re-sorted list. The true ids come from the binary's pass-name function `FUN_00b4f9d0`
(V6n, `render/verify/enum/pass_names.py`, docs/RENDER_VERIFY.md §1): `FIRST_ALPHA_PASS` = 568, TILE = 757,
IMAGESPACE = 758; the header's "unused" block maps into live ranges. Only ZONLY..DEPTHMAP (1-19) and NOLIGHTING
(65-87) have the same ids in both. Ranges below are true ids (correction of 2026-11-04; the first version of this
section used the header).

| class (family) | binaries it loads (count) | HLSL sources (dev table) | passes (examples) | notes |
|---|---|---|---|---|
| `ShadowLightShader` (SLS, the main per-pixel lighting) | `SLS1%03i.vso` 31, `SLS1S%03i.vso` 22, `SLS2%03i.vso` 103, `SLS1%03i.pso` 48, `SLS2%03i.pso` 160 (+`SLS2%03is%01i.pso` shadow-sample variants) | `ADTS`, `ADTS10`, `AD`, `DiffusePt`, `Specular`, `Texture`, `land`, `LandO`, `landlod`, `landlighting`, `decal`, `depthmap`, `SimpleShadow`, `WaterFog`, `EnvMap`, `renderNormals/Depth/Velocity`, `localMap` | ADTS* / ADT* (ambient+diffuse+texture+spec, 1/2/3/4/10 lights), AD2/AD3, DIFFUSEPT2/3, TEXTURE, 2x_SPECULAR*, LAND* (1-7 layers, `O4` 4-layer), ZONLY, DEPTHMAP, SIMPLESHADOW, FOG, ENVMAP | defines: `LIGHTS`/`MAXLIGHTS`/`NUM_PT_LIGHTS`, `SKIN`, `VC`, `SPECULAR`, `PROJ_SHADOW`/`SHADOWMAP`, `FOG`, `PARALLAX`, `FACEGENBLEND`, `HAIR`, `EYE`, `GLOW`, `MODELSPACENORM`, `LANDALPHA`, `TREE` |
| `Lighting30Shader` (SM3 path) | `SM3%03i.pso` 50, `SM3LL%03i.pso`; VS from `SM3Lighting/SM3ZOnly/SM3DepthMap/SM3SimpleShadow` | `lighting\3x\*` | all `3X*` passes 376-479 (+ the eight `3XWATER_DEPTH*` ids in 730-749): 3XLIGHTING (+S/H/Px/Pxo/Fg/G/Vc/Spc/Shp), 3XZONLY, 3XDEPTHMAP, 3XENVMAP, 3XSILHOUETTE, 3XDEPTH/VELOCITY/LOCALMAP | the SM3 lighting loop does several lights in one pass |
| `BSShaderNoLighting` | `NOLIGHT%03i.vso` 28, `NOLIGHT{MAT,TEX,TEXVC,TEXVCPMA,VCONLY,WF,WFAt}.pso` | `NoLighting` | NOLIGHTING* 66-87 (same in the header; incl. particle-system strips, falloff) | effects, glow, UI-in-world |
| `TallGrassShader` | `GRASS2%03i.vso` 24, `GRASS23x%03i.vso` 24, `GRASS2/23x %03i[TMS].pso` 4 each | `tallgrass\lowDetail/highDetail` | GRASS_* 22-61, 604-647 (dir only / 1 point light, vertex/fragment lit, shadow, `B`illboard) | instance data in per-batch constant arrays (`TallGrassGroup`: 16 B position+scale per instance) |
| SpeedTree: `SpeedTreeBranchShader`/`FrondShader`/`LeafShader`, `BSDistantTreeShader` | `STB1%03i.vso` 10, `STB2%03i.vso` 20, `STB1%03i.pso`, `STFROND[2]%03i.*`, `DISTTREE[Sil].*` | `speedtree\frond`, `distanttree\DistantTree` | LEAVES_AD/ADPt, FRONDS, DISTANT_TREE_BASE/SILHOUETTE | leaf cards rotate in the VS (`STBB` billboard constants) |
| `DistantLODShader` | `DISTLOD1/2%03i` 2+2 | `landlod` | ADT_OptLOD, LANDLO_A, LANDO_Am | distant land and object LOD |
| `SkyShader` | `SKY*.vso/pso` 21 named binaries | `sky\sky[_quad]` | SKYBASEPRE, SKY, SKY_STARS/TEXTURE/CLOUDS/MOON_STARS_MASK (568-574, the first alpha passes), SKY_SUNGLARE | |
| `WaterShader` | `WATER%03i.pso` 38, `WATER000-002.vso` | `water\2_ab\*` (water, waterhmap, displace, waterlighting) | WATER* 648-718 (accumulator group base 648: wading, interior, no-reflection/refraction/depth, Vc; 705 = WATER_NO_REFLECTIONS_Vc), WATER_LOD, WATER_DEPTH* 728-747 | |
| `ParticleShader`, `PrecipitationShader` | `PARTICLE.*` (BSParticleShader), `NOLIGHTING_PSYS*`, `PRECIP%03i` 4+2 (`PAR2%03i` is ShadowLightShader's parallax set, not particles; render-veg) | `particle`, `precipitation` | PARTICLE, PARTICLE_PREPASS, PRECIPITATION_RAIN | GPU-side particle expansion |
| `GeometryDecalShader` | `GDECAL[S].vso`, `GDECAL.pso` | `decal` | GEOMDECAL[_S] | skinned decals |
| `BSShaderBloodSplatter` | `BS.*`, `BSF.*` | `BloodSplatter` | BLOOD_SPLATTER[_FLARE] | screen-space splats |
| `BoltShader`, `BeamShader` | `BOLT.*`, `BEAM.*` | `bolt`, `beam` | BOLT, BEAM | |
| `VolumetricFogShader` | `VolumetricFog%03d` 2+2 | `vfog` | VFOG_* 599-603 | |
| `TileShader` (UI) | `TILE1%03i` 3+3 | `tile` | TILE (757) | menus |
| `ImageSpaceShader*` | `IS%s.vso/pso` (one per effect shader) | `imagespace\*` (base, copy, blur2, HDR*, DOF, radial blur, sunbeams, refraction, map, tv, noise, cinematic, scanlines, menuBG, motion blur, water FFT) | IMAGESPACE (758) | §1.7 |
| `NiD3DDefaultShader` (Gamebryo) | none (fixed function) | - | - | debug lines, fallback; `SetTransform`/`SetLight`/texture stages |

57 distinct `#define` tokens appear in the developer tables (`SKIN` 80, `SM3` 41, `LIGHTS` 39, `VC` 38, `SPECULAR` 31,
`TREE` 30, `PROJ_SHADOW` 29, `MAXLIGHTS` 25, `FOG` 15, `FACEGENBLEND` 10, `LANDALPHA` 10, `ENVMAP` 8, `PARALLAX` 4,
...): the real permutation space is a handful of orthogonal bits per family, not one shader per pass id.

**Vertex formats.** Geometry comes from `NiGeometryData` streams (position, normal, binormal, tangent, colour, up to
N UV sets) and `NiSkinPartition` partitions (4 weights + 4 bone indices, a bone palette per partition, skinning in
the VS). `NiD3DShaderDeclaration` builds one D3D9 vertex declaration per (shader, data layout); geometry groups
(`NiStaticGeometryGroup` / `NiDynamicGeometryGroup` / `NiUnsharedGeometryGroup`) pack static meshes into shared
VBs, dynamic ones (particles, morphs, lines) into lock-per-frame buffers.

### 1.4 Shadows

`ShadowSceneNode` owns `ShadowSceneLight`s (22 functions; `AddShadowObject 0x00b9f6a0`, `ProcessShadowObject
0x00b9f5b0`, `TestSphereFrustum 0x00b9bbb0`); each shadow-casting light (actors, the sun canopy pass) renders its
casters into a `BSRenderedTexture` depth map with the DEPTHMAP/3XDEPTHMAP/GRASS_SHADOW passes, then lit passes
sample it (`PROJ_SHADOW`/`Shp` permutations, `SLS2%03is%01i.pso` sample-count variants). Shadow-map count and size
come from INI settings (data image). `SHADOWVOLUME_*` passes exist but are debug/unused on PC.

### 1.5 Water

`WaterShader` + the game's water manager: reflection = a second scene render from the mirrored camera into a
`BSRenderedTexture` (sky, LOD, large objects, with WATER_DEPTH passes for depth), refraction = copy of the scene
colour, plus image-space `EFFECT_WATER_FFT` (8 shader steps: spectrum, butterflies, scrambles, normals, filter) and
`EFFECT_WATER_DISPLACEMENT` for the ripple height map. Wading/interior/no-reflection variants are separate passes.

### 1.6 Sky, grass, trees, particles, decals

* **Sky**: `SkyShaderProperty` objects (sun, moons, stars, clouds, horizon fade) in their pass range, 21 binaries.
* **Grass**: `TallGrassShader` draws `TallGrassGroup` batches: one mesh, instance data (position/scale, 16 B) in VS
  constants, so one draw per batch; dir-only vs 1-point-light, vertex/fragment lighting, shadowed, billboard.
* **Trees**: SpeedTreeRT 4.x on the CPU (`src/speedtree`, 484 functions: wind, LOD, leaf card geometry) and
  `BSTreeNode`/`BSTreeModel`; three draws per tree (branch, frond, leaf) with per-tree wind/rotation constants;
  `BSDistantTreeShader` draws far trees as billboards.
* **Particles**: `niparticle` simulates on the CPU, `NiParticles` geometry is uploaded each frame (dynamic group) and
  expanded by `PARTICLE.vso` (BSParticleShader; `PAR2xxx` are ShadowLightShader PARALLAX programs, corrected by render-veg); `NOLIGHTING_PSYS*` for simple systems. `BSTempEffect*` (170 references)
  manages impact/temporary effects.
* **Decals**: geometry decals (`GeometryDecalShader`, skinned and static) and `BSShaderBloodSplatter` (screen).

### 1.7 Image space / post

`ImageSpaceManager::ImageSpaceEffectEnum` lists **34 top-level effects and 66 effect shaders** (100 values):
refraction, bloom, HDR (tone map with 9/16/4-tap downsamples, light adapt, lum clamp, blend-in, cinematic,
alpha mask), cinematic, depth of field (+mask), depth radial blur, radial blur (3 qualities), full-screen blur,
blur 3-15 taps (7) and bright-pass blur 3-15 (7), get-hit, sunbeams, interface (scanlines, VATS scan, menu unpack),
motion blur, volumetric fog, map, menu background, TV, water FFT/displacement, noise, night vision, copy/stencil
copy/restore/alpha-mult/blend/texture mask. Parameters (`ApplyTintColorParamModifier 0x00b8b540`,
`SetDepthOfFieldParameters 0x00b8b0d0`, weather modifiers `0x00b8b130..0x00b8b6e0`) are game logic; the effects
draw full-screen quads with `IS%s` shaders between `BSRenderedTexture` targets. Typical exterior chain: HDR
(~6-8 passes) + bloom/blur (2-3) + cinematic + optional DOF/radial/refraction/VATS: **~10-20 full-screen passes**.

### 1.8 UI / menus

Menus are a scene graph too: the `Tile` tree (`fallout`, `bsmenu`) builds `NiNode`/`NiTriShape`s with
`TileShaderProperty` (`TileShader::UpdateMaterial 0x00bcac60`), rendered by the interface with its own orthographic
camera and accumulator through the TILE pass (3 VS/PS variants); fonts are textured quads; the Pip-Boy and local map
render into render targets and the interface effects (scanlines, menu unpack) run through image space. So **menus use
the same draw path as the world**, just one family (Tile) plus a few image-space shaders.

### 1.9 Where the D3D boundary is

`render/native/tools/d3d_boundary.py` (on top of S6's stand-in scan) and a call-name census over `src/`:

| who calls D3D (stand-in or SDK) | files | distinct methods |
|---|---|---|
| `niconsolerenderer` (NiDX9Renderer, NiD3D*) | 11 | 28 (device creation, draws, streams, constants, RTs, textures) |
| `staging/blk_00e60000_o2` (renderer bring-up, adapter/modes/caps, Present) | 1 | 18 |
| `fallout` (`screenshot.cpp`, interface) | 3 | 11 (screenshot readback, `TestCooperativeLevel`) |
| `staging/blk_00e80000_o2` (renderer, fixed function lights/stages) | 1 | 9 |
| `bsshader` (`bsshaderutil.cpp`, `bsshader.cpp`: create shaders, procedural textures) | 2 | 7 |
| `staging/blk_00ea0000_od`, `blk_00c40000_od_ob1` (query/scissor), `blk_00b60000` | 3 | 9 |
| `nianimation` (morph VB unlock) | 1 | 2 |

Draw calls themselves: `DrawIndexedPrimitive` 12 sites, `DrawPrimitive` 3, `SetStreamSource` 3, `SetIndices` 5 -
**all inside the renderer**. Everything else goes through engine-level APIs: `SetRenderState` is called 314 times
from `bsshader` and 69 from staging (via `NiDX9RenderState`, the cache), `SetVertexShader`/`SetPixelShader` 56/55
from `bsshader` (via `NiD3DPass`), `SetTexture` 34 `fallout` + 30 `fallout_shared` + 23 staging (engine
`NiTexturingProperty`/shader texture slots, not the device). So the engine draws through three narrow
interfaces: **`NiRenderer` (virtual), `NiDX9RenderState` (virtual, the state cache), and the `NiD3DShader`/`BSShader`
protocol (virtual)**, and only ~20 files touch the device directly.

## 2. The replacement boundary

### 2.1 What stays (untouched engine code)

* The scene graph and all its data: `NiAVObject`/`NiNode`/`NiGeometry`/`NiGeometryData`/`NiSkinInstance`/
  `NiSkinPartition`, properties, NIF streaming, controllers, `niparticle` simulation, SpeedTreeRT, `BSFadeNode`,
  `BSMultiBound`, culling (`NiCullingProcess` and Bethesda's cullers).
* `ShadowSceneNode` light assignment and shadow-caster selection (`AddObject`, `UpdateShadowLights`,
  `CalculateLightingAtPoint`, `ShadowSceneLight::AddShadowObject/ProcessShadowObject/TestSphereFrustum`).
* **`BSShaderProperty` and subclasses' pass selection** (`ShaderPassList::Add`, the property `GetRenderPasses`
  logic and NIF flags). The pass id plus the entry's light list and the property flags is exactly the permutation
  key we need (§3.4); keeping it keeps every gameplay-visible decision (which objects are lit by what, alpha,
  glow, facegen, refraction) bit-identical.
* `ImageSpaceManager` parameter logic (modifiers, weather, which effects are active, `IsEffectActive 0x00b8b390`,
  `PrepareEndOfFrameEffects 0x00b8b3f0`) and the menu tile system.
* All game code (`fallout*`, TES render sequencing); calls into the renderer keep their signatures.

### 2.2 What we replace

| layer | replaced by | how much |
|---|---|---|
| `NiDX9Renderer` (all `NiRenderer` virtuals: frame begin/end/display, render target groups, camera/viewport, `RenderShape`/tri-strips/points/lines dispatch, texture/geometry precache and purge, screenshot, device reset) | `nx::Renderer` on deko3d with the same object layout as `NiDX9Renderer` (fields other code reads stay where they are; our state lives in a side object) | renderer core 253 + geometry 69 + textures 89 functions |
| `NiDX9RenderState` (state cache) | `nx::StateTracker`: the same virtual surface, but it only records packed render-state words (§3.4); nothing is sent per call | |
| `NiD3D*` shader framework: `NiD3DShader`/`NiD3DDefaultShader`, `NiD3DPass`, `NiD3DRenderStateGroup`, `NiD3DShaderConstantMap`, program factories/creators/caches, `NiD3DShaderDeclaration`, `NiDX9TextureData`/`NiDX9SourceTextureData`, VB/IB managers, geometry groups | removed from the draw path; replaced by family objects (`nx::Family`) that build draw packets | 349 functions |
| `BSShader` subclasses' render-time virtuals (setup pass, update constants, textures, alpha, pre/post render) and their `.sdp` loading | one native family per class (§4), filling typed constant structs | most of the 280 "shaders" functions (properties stay) |
| `BSShaderAccumulator` render/flush + `BSBatchRenderer` bucket rendering | `nx::Batcher`: accumulates draw packets per frame-graph pass and sorts by 64-bit key | ~80 of 99 (registration entry points stay as the hook) |
| `BSRenderedTexture`/`NiRenderedTexture`/`NiRenderTargetGroup` creation | `nx::RenderTarget` (deko3d images in the frame graph) | |
| `ImageSpaceShader*` / `ImageSpaceEffect*` render functions | native image-space passes in the frame graph | ~95 of 155 |
| the ~20 direct-D3D files of §1.9 outside the renderer | small native functions (screenshot readback, procedural texture creation, query, scissor) | ~40 functions |

Total on the order of **1,000-1,300 functions replaced by perhaps 25-40 k lines of our own code** (estimate), all
inside the two address blocks of §1.1 plus ~20 scattered sites.

### 2.3 How the replacement is wired

Three mechanisms, all already in the S1/S2/S3 design:

1. **Link-by-address override (S2, `tools/link/`).** Native functions carry the address they replace (an
   `// @fnv 0x...` comment, collected into `render/native/overrides.tsv`: address, our symbol, ABI). `fnvlink.py
   resolve` gives an override precedence over the decomp definition at the same address, so every call, every
   vtable-slot relocation and every code pointer the data image (S3) holds for that address lands on our function.
   Request to S2: an `--override <tsv>` input with that precedence and a report of decomp definitions it shadowed.
   Status (round 3): done (R1). The N1 stubs are `render/native/n1/engine_stubs.cpp` in `libnxrender.a`; the link
   command for the native build is
   `python3 tools/link/fnvlink.py resolve <units.json> <objects.tsv> <out> --override render/native/overrides.tsv
   --override-lib <n1 out>/libnxrender.a [--msvc-objs <pristine MSVC objects>]` (`<n1 out>` from
   `render/native/n1/build.sh <n1 out>`). Without `--msvc-objs` the vslot rows are skipped (they are all
   N1-pending anyway); the five NiDX9Renderer rows bind by implementation address. Pre-check:
   `python3 -I tests/s6n/test_overrides.py <n1 out>/libnxrender.a`.
2. **Prepare rules (S1, `tools/prepare.py`).** A rule (`NATIVE1`, proposed) drops whole units that become dead
   (`niconsolerenderer/*`, the replaced `bsshader` shader units, D3D stand-in headers) from the build list, so
   their D3D-dependent code does not even need to compile; units that are only partly replaced compile as they are
   and lose the replaced definitions to the override.
3. **Vtables.** Replaced classes keep their original vtable address in the data image; S3's rewrite of code
   pointers through the address map points each slot at our override. A replaced virtual therefore needs no source
   change on the caller side. Classes we add (`nx::*`) are ordinary ILP32 C++ and never appear in the data image.

Object layouts of replaced classes stay the decomp's (the 3,171 `static_assert`s still apply), because kept code
reads fields directly (e.g. `BSShaderProperty` subclasses read renderer/shader-manager globals; `fallout` reads
`NiDX9Renderer` fields for the screen size). New state goes in side structures reached from one reused pointer field.

### 2.4 The N1 menu path as wired (round 5)

What the native build replaces for the main menu, all rows in `render/native/overrides.tsv` (slots checked against
the image vftables, §R2), code in `render/native/n1/`:

* **Frame, targets, camera:** `NiDX9Renderer` slots 95, 96, 98-102 and the Present at 0x00e7ccd0 (`engine_stubs.cpp`
  -> `nx::Renderer`). Slot 97 (`Do_DisplayFrame`, deferred releases) stays the engine's.
* **Geometry:** slots 107/108 (one function, 0x00e744b0) -> `engine::ReadGeometry` -> `Renderer::RenderGeometry`.
  The engine's shader pipeline for the draw (TileShader pass setup, constant maps) is not reached; the draw uses the
  tile family with what the geometry's properties say:
  * NiPropertyState at geometry +0x9C: NiAlphaProperty (+0x9C; flags: blend, src/dst function, alpha test and its
    function, ref) -> D3D9 ALPHABLENDENABLE / SRCBLEND / DESTBLEND / ALPHATESTENABLE / ALPHAFUNC / ALPHAREF for that
    draw (restored after it); NiMaterialProperty (+0xA4) alpha; the shader property (+0xA8) as a
    TileShaderProperty: texture +0x60, tint rgb +0x68, alpha +0x78 -> tint = (rgb, alpha x material alpha);
  * texture: TileShaderProperty texture -> NiTexture renderer data (+0x24) -> the NativeTexture pointer in it
    (found by scanning the renderer data's dwords for a live NativeTexture, so its field offset is not needed);
    without one, the state tracker's stage 0, else a white texel.
* **Textures:** `NiDX9SourceTextureData::LoadFromFilename` (0x00e68a80) stays; the two D3DX calls it makes are
  replaced (`engine_textures.cpp`): `D3DXGetImageInfoFromFileInMemory` (0x00ee6e22) answers from the DDS header,
  `D3DXCreateTextureFromFileInMemory` (0x00ee6e1c) creates the native image (`ParseDDS`, `nx_d3d9_format`,
  NXFMT_CONVERT expansion) and returns a **NativeTexture**: an object laid out as an IDirect3DTexture9 (22-slot
  method table: AddRef/Release, GetType, GetLevelCount, GetLevelDesc, LOD; LockRect and GetSurfaceLevel refuse, the
  native path has no CPU access). The engine stores it as its D3D texture. Cube and volume textures (0x00ee6e16,
  0x00ee6e10) are not replaced yet (menus do not use them).
* **State cache:** NiDX9RenderState set/get of render and sampler states, the blend setters, and texture binding
  (slots 55-57: SetTexture / GetTexture / ClearTextureUsage) -> `nx::StateTracker`.
* **Shader construction:** TileShader CreateVertexShaders / CreatePixelShaders / CreateConstantMaps and
  BSShaderNoLighting::InitializeShaderData are no-ops.
* **Lockable textures (fonts):** fonts are tile geometry (`Font::CreateTextShape`, 0x00a14a20: an NiTriShape with a
  TileShaderProperty, its tint at +0x68 and the font page texture). Their textures are not files: the font loader
  reads `TEXTURES\FONTS\*.TEX` (8-byte size + RGBA pixels) into NiPixelData, and the renderer data creates a D3D
  texture (`NiDX9SourceTextureData::CreateSurf`, 0x00e68010) and fills it through GetSurfaceLevel / LockRect. That
  function is replaced: it makes a lockable NativeTexture (CPU staging per level, D3DFORMAT from the renderer pixel
  format's hint at +0x20, A8R8G8B8 without one) whose surfaces (IDirect3DSurface9 layout: GetDesc, LockRect,
  UnlockRect, GetContainer) take the engine's copy; the native image is (re)made from the staging copy when the
  texture is next bound or found after an UnlockRect.
* **Tile scroll, scissor, stencil:** TileShaderProperty TexScroll (flag +0x91, offset +0x7C) -> the `tile_vc_scroll`
  VS; its scissor rectangle (enable +0xAC, NiRectI at +0x9C, target pixels) -> scissor test for the draw;
  NiStencilProperty (+0xAC on the geometry; flags +0x18: enable, fail / zfail / pass actions, test function; ref
  +0x1C, mask +0x20) -> the D3D9 stencil states for the draw. All restored after the draw.
* **Image space:** `ImageSpaceManager::RenderEffect` (0x00b97550) is replaced by a dispatcher. EFFECT_MENUBG (15)
  renders natively (`Renderer::RenderMenuBackground`, family `menubg`: scene luminance through the tone texture,
  mixed by the effect's blend amount at +0x5C; its ImageSpaceTexture at +0x58 gives the tone texture), source and
  destination being engine render targets registered as target groups (destination 0: the current target). Every
  other effect is counted and skipped: in the native build the world's image-space chain (HDR, bloom, ...) is not
  drawn until its families exist. `ImageSpaceEffectMenuBackground::Setup` (0x00c04730) needs no replacement: it only
  builds engine data and loads `sepia.dds` through the D3DX overrides. The `menubg` formula matches the game's
  ISMENUBG shader (checked locally against the S6b translation of the user's shader package: 63 of 64 pixels within
  2/255, one at a tone-texel boundary).
* **Not native yet:** skinned geometry (N2, §4), engine render-target creation (BSRenderedTexture / NiRenderTargetGroup
  -> `RegisterTargetGroup`), the other image-space effects, culling modes of NiStencilProperty's draw mode.

Tested on GL and as the ILP32 guest by the N1 self-test (90 checks): a synthetic menu of three tiles built in
engine layout (checker-textured background, a green button at material alpha 0.5 blended with SRCALPHA, an
alpha-tested text tile) rendered only through the stubs, with textures created through the D3DX overrides, plus
the menu-background effect over a rendered target; the RenderEffect dispatch for MENUBG with a tone texture made and
filled through the lockable (font) path; stencil REPLACE/EQUAL, texture scroll and the tile scissor rectangle.

## 3. Native renderer design on deko3d

### 3.1 Memory and the ILP32 boundary

* Engine side (ILP32, below 4 GB): the `nx::` front (families, batcher, state tracker, frame graph) is ILP32 C++
  like the engine, so it can be called directly by the overrides.
* deko3d itself: compiled ILP32 if S5 makes libnx/deko3d ILP32-clean; otherwise a **command-stream boundary**:
  the ILP32 front writes a compact *packet stream* (bind pipeline-state
  id, bind UBO slice, bind texture handles, draw) into a ring in shared memory, and a small LP64 "nxgpu" worker
  translates packets into `dkCmdBuf*` calls. Either way the hot path does not cross the ABI per call: one boundary
  crossing per submitted command list.
* GPU memory: `DkMemBlock`s are caller-backed, so all GPU-visible memory (rings, static geometry, textures,
  render targets) is mapped below 4 GB and addressable from ILP32 code with 32-bit pointers. Budget estimate:
  textures 700-1000 MB, static geometry 100-200 MB, rings 3 x 8 MB, render targets ~60 MB at 1080p.

### 3.2 Resource model

* **Textures**: DDS loaded once into `DkImage`s; DXT1/3/5 are native (`BC1/2/3`), so no transcoding; ARGB8, L8,
  A8L8, V8U8, R5G6B5 map to native formats with component swizzles in the `DkImageView`. One upload via a staging
  ring and `dkCmdBufCopyBufferToImage` (tiling handled by the copy). Every image gets a slot in **one global
  descriptor set** (image + sampler descriptor tables, `dkCmdBufBindImageDescriptorSet`/`BindSamplerDescriptorSet`
  once per frame): a texture is then a 32-bit `dkMakeTextureHandle(image, sampler)` - bindless-style; per draw we
  only bind the handles the family needs (`dkCmdBufBindTextures`, 1 call for up to 8-12 slots).
* **Static geometry**: `NiGeometryData` uploaded once into a big static buffer (sub-allocated); one vertex layout
  per data layout, decided at upload. No geometry groups, no per-frame locks for static meshes.
* **Dynamic geometry** (particles, morphs, lines, UI text, SpeedTree leaf updates): written straight into a
  persistent mapped **vertex ring** (per-frame region, 3 frames in flight, fence-protected); no lock/unlock calls.
* **Constants**: a persistent mapped **UBO ring**; each draw writes only its per-draw struct (typically 64-256 B);
  per-pass and per-frame data (camera, fog, sun, shadow matrices) are written once per pass/frame and bound to
  their own UBO slot. Skinning palettes: per-partition slice of the ring.

### 3.3 Pipelines/state

deko3d has no monolithic pipeline objects: state is bound in blocks (`DkRasterizerState`, `DkColorState`,
`DkColorWriteState`, `DkBlendState`, `DkDepthStencilState`, `DkVtxAttribState`/`DkVtxBufferState`, shaders). We
"prebake" per **(family, permutation, render-state word)**:

* the shader pair (`DkShader` VS/PS, from DKSH compiled offline), identified by a 16-bit id;
* the render-state word (64 bits: blend src/dst/op, alpha-to-coverage, depth test/write/func, cull, stencil
  op/ref/mask, colour write mask, depth bias class) mapped once to prebuilt deko3d state blocks (a few hundred
  distinct words in practice);
* the vertex layout id.

A `PipelineState` record = those ids plus the deko3d state structs; switching costs only the blocks whose id
differs from the current one (typically 1-3 small bind calls).

### 3.4 Draw submission

* The kept `BSShaderProperty` pass entries arrive at the batcher; the family converts each entry into a **draw
  packet** (32-64 B): sort key, pipeline id, geometry (buffer offset, index range), UBO offset, texture-handle
  block, instance count.
* **Sort key** (64 bit): frame-graph pass (8) | layer (opaque/alpha/order) | pipeline id (16) | texture block
  hash (16) | depth (16, front-to-back for opaque, back-to-front for alpha). Opaque passes reorder freely across
  objects as the engine's own bucketing allows (pass id first, as today), alpha keeps the engine's depth order.
* **Instancing**: identical (geometry, pipeline, textures) runs inside a bucket become one instanced draw with a
  per-instance transform array in the UBO ring. Candidates measured from the code paths: grass (already batched;
  native draws one instanced mesh per group without constant re-upload), SpeedTree (branch/frond/leaf per tree
  type: 3 draws per tree type instead of per tree), distant LOD blocks and distant trees, rocks/clutter repeated
  per cell, decals.
* **Multi-pass lighting collapse** (optional, behind a parity switch): the SM3 path (Lighting30, 3X passes) already
  lights several lights per pass; we advertise SM3 caps so the engine selects it, and our 3XLIGHTING family loops
  over up to 8 lights from a light UBO. The 2.x multi-pass path (ADTS + DIFFUSEPT2/3 extra passes) stays available
  for parity tests.

### 3.5 Frame graph

Passes and their targets, declared once per frame by the overridden renderer entry points (begin/end render target
group, image-space dispatch):

1. shadow maps (one depth target per shadow light, the sun canopy pass) - depth only, front pipeline cache warm;
2. water reflection (mirrored camera, reduced resolution) and water depth;
3. main scene: depth/z-only pre-pass where the engine emits ZONLY passes, opaque, land, LOD, sky, alpha, water,
   precipitation, decals, blood;
4. image-space chain (HDR downsample/adapt, bloom/blur, DOF, cinematic, ... ) on transient targets;
5. UI (tile scene graph), present.

Targets are transient images aliased between passes where lifetimes do not overlap (HDR mip chain vs blur
targets); barriers (`dkCmdBufBarrier`) are computed by the graph, not per call. Maxwell tiled caches benefit from
binding each target once per pass rather than per draw.

### 3.6 Threading

* Main thread: game update, cull, accumulate (engine code, unchanged), packet build (families).
* Render thread: sorts packets, records the deko3d command buffers for frame N while the main thread builds N+1;
  GPU runs N-1. Hand-off is a per-frame packet arena (double buffered).
* Later: split recording of the main pass across 2 cores (deko3d command buffers are independent per thread).

### 3.7 Per-frame cost model (Mojave exterior)

Estimates from the code paths (to be replaced by a counter in milestone N2: packets per pass, per family):

| source | draws (estimate) |
|---|---|
| visible static objects, uGrids 5 (25 cells), after cull | 600-1,000 geometries; x 1.2-1.6 passes (z-only, extra light passes, fog) on the 2.x path |
| land (25 cells x 4 quadrants, LAND*O multi-layer) | 100-150 |
| distant LOD (land/object/tree billboards) | 100-200 |
| trees (branch+frond+leaf) | 150-450 |
| grass groups | 100-300 |
| actors (skin partitions) | 50-200 |
| shadow maps (2-4 lights x casters) | 100-300 |
| water reflection re-render | 100-300 |
| particles, decals, sky, effects | 50-150 |
| image space + UI | 20-60 |
| **total** | **~1,500-3,500 draws/frame** |

Per-draw CPU cost on the A57 (1.02 GHz), estimates:

* **D3D9 emulation (D6)**: the engine's own path stays (constant-map walk with per-entry dispatch, 10-30
  `NiDX9RenderState` checks, 4-12 `SetTexture`/sampler calls, VS/PS/decl/stream sets, ~20-60 virtual calls into the
  device per draw) plus the emulation's work (dirty tracking, state-word build and lookup, copying the dirty part
  of the 256-register constant file into the UBO ring, texture handle building, translated shaders' generic
  register-file layout). Roughly **10-20 us/draw -> 15-70 ms/frame**: over a 33 ms budget in exteriors without
  further reductions.
* **Native**: kept engine accumulation (~1-2 us/entry) + family packet build (typed struct fill, 64-256 B) + sorted
  submission with id-compare state changes (~1-2 us/draw on the render thread) -> **~3-6 ms main thread +
  2-4 ms render thread** for the same scene, before instancing; instancing (trees, grass, LOD) and the SM3 light
  loop cut draw counts a further estimated 25-40 %.
* GPU side the two approaches are close for identical shaders; hand-written shaders save mostly on removing
  register-file indirection, redundant multi-pass lighting (bandwidth) and full-screen copies in image space.

## 4. Shader plan

Hand-written GLSL 4.60 (deko3d/uam profile), compiled offline by `uam` into DKSH at build time, shipped as our own
work. Permutations are `#define`s compiled into separate DKSH binaries (uniform branches only where the cost is
negligible). Families and their bits (from the engine's own pass enum and developer tables, §1.3):

| family | VS bits | PS bits | variants (estimate) |
|---|---|---|---|
| `lit` (SLS + Lighting30: ADTS/ADT/AD/DIFFUSEPT/SPECULAR/TEXTURE, 3XLIGHTING) | `SKIN`, `VC`, `PROJ_SHADOW`, `FOG`, `MODELSPACENORM`, `TREE` | light count 0-8 (loop with constant bound by bucket: 1, 2-4, 5-8), `SPECULAR`, `PARALLAX`/`PARALLAXOCC`, `GLOW`, `ENVMAP`/`WINDOW`/`EYE`, `HAIR`, `FACEGENBLEND`, `SHADOWMAP` (sample count), `ALPHATEST`, `SI` | ~120-200 |
| `land` (LAND*, LANDAD, LANDLO, LOD land) | `FOG`, `PROJ_SHADOW`, `LODLANDCLIP`, `GEOMORPH` | layers 1-7 (`O4` packed), `LANDALPHA`, simple | ~40 |
| `depth` (ZONLY, DEPTHMAP, 3XDEPTH*, GRASS_SHADOW, WATER_DEPTH, renderNormals/Velocity/localMap) | `SKIN`, `VC`, `TREE` | `ALPHATEST`, `CLEAR`, output kind | ~30 |
| `nolight` (NOLIGHTING*, PSYS strips) | `VC`, `SKIN`, falloff, subtex | tex/mat/vc, premultiplied, falloff | ~30 |
| `grass` | instancing, `POINT`, vertex/fragment lit, billboard | `SHADOWMAP`, `TRANSPARENCYMS` | ~24 |
| `speedtree` (branch, frond, leaf, distant tree, silhouette) | wind, leaf billboard (`STBB`), `PTLIGHT` | `SHADOWMAP`, silhouette | ~20 |
| `sky` | quad/clouds/stars/moon/sun | texture fade, horizon fade, occlusion | ~16 |
| `water` (+ wading/interior/no-reflection/no-refraction/no-depth, LOD) and water FFT compute | displace, Vc | reflection/refraction/depth/specular flags | ~24 + 8 FFT |
| `particle`, `precip`, `bolt`, `beam`, `decal`, `blood`, `vfog` | per class | per class | ~30 |
| `tile` (UI) | 3 | 3 | 3 |
| image space (copy, blur N, bright-pass blur, HDR set, DOF, radial blur, sunbeams, refraction, cinematic, tv, noise, map, menuBG, scanlines, VATS, motion blur, night vision, stencil copy) | `base` | one per effect shader | ~66 |
| fixed-function replacement (debug lines, `NiD3DDefaultShader` users) | 1 | 2 | 3 |

About **400-500 DKSH binaries** from ~25 GLSL source files (estimate; the engine's PC packages hold ~600-900 blobs
per shader level).

**Parity checks against the originals** (S6b's translator `sdp2dksh` = MojoShader `dk` profile, used only as a
reference, never shipped in place of our shaders unless as a temporary fallback):

1. *Constant-level*: for each (family, permutation) the parity harness records the engine's constant writes
   (register file) for a captured draw and feeds them to the translated original; our shader gets the same inputs
   through our typed struct; both render the same geometry offscreen; compare per pixel (max abs error <= 2/255 on
   8-bit targets, relative 1e-3 on float targets).
2. *Scene-level*: captured frames (packet streams serialised on the console or in a host replay of deko3d through
   a recorded stream) rendered once with all-translated shaders and once with ours; PSNR / per-pass diff images.
3. *Coverage*: every pass id the engine emitted in a play-through route (Goodsprings, Primm, Strip, Vault 21
   interior, night) must map to a family/permutation; unmapped pass ids fail loudly and fall back to the
   translated original.
4. The translated original remains a selectable per-family fallback (`nx::Family::Translated`), which also lets
   world rendering come up before every family is hand-written.

### 4.2 Family semantics against the originals (V6n's c69 harness)

V6n compares each family with the game's original program for the same pass (`tests/v6n/claims/c69_families.py`,
the originals from the player's shader package, never in the repo). The families now follow the originals:

| family / variant | original | semantics | inputs the family needs |
|---|---|---|---|
| `tile_scroll` VS | TILE1001.vso | uv = uv x TexScroll.zw + TexScroll.xy (texture 0); texture 1 at the plain uv | NxTileDraw.texScroll all four (zw = 1 without scaling) |
| `tile_alphamap` PS | TILE1001.pso | rgb = Src0.rgb x tint.rgb, a = Src0.a x tint.a x AlphaMap.r | **two textures**: slot 0 Src0, slot 1 the alpha map |
| `tile_vc` PS | TILE1002.pso | rgb = tex.rgb x tint.rgb, a = tex.a x vertex alpha | vertex colour (alpha) |
| `nolight_texfall` VS | NOLIGHT004.vso | opacity mix by smoothstep(t), t from \|N.V\| between the falloff angles | as before |
| `sky` VS + PS | SKY.vso / .pso | rgb = B0 x vc.r + B1 x vc.g + B2 x vc.b, a = vc.a x B0.a, z = params.x x w (far plane) | **vertex colour at location 2**; NxSkyDraw = {wvp, blend0, blend1, blend2, params (far z/w, cloud alpha, uv scroll)} |
| `land_lit` VS + PS | SLS1025.vso / SLS1040.pso (LANDAD) | base x vc x clamp(clamp(N.Lt) x lightColor + ambient), N = 2 x normalMap - 1, Lt = (T.L, B.L, N.L) | **normal, tangent, binormal at locations 4-6; normal map at slot 4**; NxLandDraw += lightDir, lightColor, ambient |
| `depth_at` VS + PS | SLS1000 (ZONLY_At) | alpha = diffuse.a through the D3D9 alpha test (epilogue), depth only | **uv at location 1, diffuse at slot 0**, the epilogue's alpha test |

The first three plus the falloff pass V6n's cases as filed. For the other four the cases still feed the families'
old interfaces (one texture for the alpha map, no vertex colour for the sky, `land` instead of `land_lit`, `depth`
without uv); with the inputs in the last column all 12 cases match (run locally from a copy of the harness with
those inputs; nothing of it is in the repo). Request to V6n: update those four cases' family inputs accordingly.

### 4.3 Compile-time epilogue permutations (P6n hotspot 1)

Every fragment shader that includes `d3d9_epilogue.glsl` is also built as `<name>__e0` (no fog, no alpha test: no
epilogue code, no epilogue UBO), `<name>__ea` (alpha test only) and `<name>__ef` (fog only); `<name>` keeps the full
runtime epilogue for draws with both. `nx::Renderer::Render` picks the permutation from the draw's resolved shader
bits (alpha test enable, fog mode) for any package shader (it finds the blob from the shader's GLSL address,
`Backend::ShaderGlsl`, so callers that create shaders themselves benefit too) and binds the epilogue UBO only when
the permutation uses it. Image space (`menubg`) never had an epilogue. Maxwell code (uam, raw bytes x 3/32):

| shader | full | __e0 | __ea | __ef |
|---|---|---|---|---|
| tex (P6n's image-space stand-in) | ~114 | ~18 | ~66 | ~60 |
| tile (UI) | ~114 | ~12 | | |
| lit_spec | ~246 | ~150 | | |

The permutations are the same source with the branches folded, so results are identical (all scenes, the N1
self-test and V6n's c69 cases unchanged). The N0 scenes still use the full runtime shaders (they exercise it).

**Alpha compare without a switch (C98).** uam's GLSL front end (Mesa's GLSL IR to TGSI, no NIR) keeps a `switch` as
a loop of one test per case even when the selector is a compile-time constant, so every alpha-tested shader carried
seven tests and their bookkeeping. `nx_compare` now treats NxCompare as the bit set it is (1 LESS, 2 EQUAL, 4 GREATER):
`(func & mask) != 0 || func >= 7 || (unordered && func == 5)`, equal to the switch for every function with ordered and
NaN operands. Maxwell instructions: lit_spec_d1p2__ea 249 -> 222, nolight_tex__ea 80 -> 35, tex / nolight_tex with
the runtime epilogue 131 -> 104.

**Which shader P6n's GPU model costs.** `perf/gpu_cost.py` costs each family with what `Render` binds: the static
light permutation for the model's 1 directional + 2 point lights, then the epilogue permutation for the family's
(alpha test, fog) mix, replayed from the synthetic stream's D3D9 state (`epilogue_mix`). Before this round (P6n: "GPU model binds the renderer's ... permutations") it costed the
runtime base shaders, so §4.3 and §4.4 never showed in its numbers.

### 4.4 lit pass: static light permutations and the fp16 analysis (P6n hotspot 2)

**Permutations.** `lit[_spec]_d<D>p<P>` (D = 1: light 0 is the directional light; P = 1, 2, 4, 8 point lights,
7 with a directional one) unroll the light loop with constant indices: no loop counter, no per-light type branch,
and constant-bank operands instead of indexed UBO loads. `nx::Renderer::Render` picks the smallest permutation that
holds the draw's lights (at most one directional), copies the lights into the slice in the permutation's order
(directional first, then points, then zero-colour padding with range 1, which contributes exactly 0), and then the
epilogue permutation of §4.3 on top. More than one directional light, or no lights, keeps the dynamic shader.
Code (uam, approximate Maxwell instructions; the dynamic shader executes its ~110-instruction loop body once per
light, which is how P6n's ~411 instructions per pixel arise):

| shader | static size | executed for 1 directional + 2 points |
|---|---|---|
| lit_spec (dynamic, runtime epilogue) | ~270 | ~400 (loop x 3 + epilogue) |
| lit_spec__e0 (dynamic loop) | ~174 | ~300 |
| **lit_spec_d1p2__e0** | ~186 | **~186** |
| lit_spec_d1p1__e0 / _d1p4__e0 | ~138 / ~276 | same as size |
| lit_d1p2__e0 (no specular) | ~108 | ~108 |

Results are unchanged: the N1 self-test draws 1 directional + 2 point lights (given point first) through the
dynamic and the static shader, identical within 1/255 (exact on GL); the `lit` scene and its golden are unchanged
(the point-light maths now uses `dot(dv, dv)` and `inversesqrt`, the same values).

**Prepared per-draw values (C99).** The permutations read two values `Render` computes once per draw while it
orders the lights into the slice: the directional light's `posRange.xyz` is the unit vector towards the light
(-normalize(direction)) and every point light's `posRange.w` is 1 / range^2 (padding lights: 1). No fragment
normalizes a constant or divides by range^2 any more: lit_spec_d1p2__e0 201 -> 179, lit_spec_d1p8__e0 419 -> 394
Maxwell instructions. The dynamic shader keeps the D3D-style values (direction, range).

**fp16 precision analysis** (what may run at half precision without visible change on 8-bit targets):

| quantity | range | fp16 error | verdict |
|---|---|---|---|
| world positions, `lightPos - world` | FNV worldspace up to ~10^5 units | ~32 units at 4x10^4, overflow at 65504 | **highp** |
| N, L, V, H (unit vectors after normalisation) | [-1, 1] | 2^-11 ~ 4.9e-4 relative | mediump: N.L error ~1e-3, < 0.3/255 |
| attenuation 1 - d^2/r^2 (d^2 computed highp) | [0, 1] | 4.9e-4 | mediump |
| light / ambient sums (rgb) | 0 .. ~8 | 4.9e-4 relative, ~0.004 at 8 | mediump: < 1/255 after the clamp to [0, 1] in the output |
| specular pow(N.H, power), power up to ~100 | [0, 1] | relative error x power: ~5 % at 100 | **highp** (mediump only for power <= 16: < 1/255) |
| texture colour, vertex colour | [0, 1] | 4.9e-4 | mediump |

The shader marks those quantities `mediump`. **Today that changes nothing:** desktop GL ignores precision
qualifiers, and uam compiles GLSL through TGSI, which has no 16-bit types, so no FP16 (HFMA2) code is generated
(checked: a `mediump` and a `highp` test shader produce identical Maxwell code; `GL_EXT_shader_explicit_arithmetic_types_float16`
is rejected). Getting real fp16 needs a uam change (an fp16-aware path to the Maxwell backend), which belongs to
`render/shaders/uam.patch` (S6b): request filed in the S6n report. Until then the permutations are the lit pass's
gain, and V6n stays exact because the arithmetic is unchanged.

### 4.5 The engine path: family dispatch, the translated path and the draw counters

**Routing.** `Do_RenderShape` / `Do_RenderTristrips` (the `Do_RenderGeometry` override) read the engine's current
pass: `FUN_00b994f0` stores the pass id (`DAT_011f91e4`) and the `BSShaderProperty::RenderPass` (`DAT_011f91e0`;
+0 geometry, +4 u16 pass, +9 light count, +0xC lights) before the geometry is drawn. The pass counts only when its
RenderPass names the drawn geometry, so a menu drawn outside the accumulator never inherits the last world pass
(`engine::BindPassGlobals`; the native build calls `BindImagePassGlobals`). `n1/passes.cpp` maps the binary's pass
ids to routes; only ids whose family renders exactly the terms of the game's program get a native route (no
parallax, glow, facegen, hair, shadow-sampling, multi-layer land or particle bits).

**Dispatch table** (`engine::FamilyRows`, one row per route; family streams switch only their own row, CLAIMS.md):

| row | default | engine-path draw |
|---|---|---|
| tile (TILE 757), menu path (no pass) | native | `Renderer::RenderGeometry` (tile family) |
| image space | native for EFFECT_MENUBG | `RenderMenuBackground`; other effects are skipped (counted) |
| nolight, lit, depth, land, sky | translated | none native yet (render-lit, render-land own them, D21: coverage first) |
| every other pass id | translated | - |

**The translated path.** The game's own VS/PS of the pass, translated by S6b's sdp2dksh, on the native renderer
(`Renderer::RenderTranslated`): the program pair comes from the geometry's engine shader (NiGeometry +0xC0, a
BSShader; +0x5C the current NiD3DPass; VS object +0x5C, PS object +0x44), identified by the D3D shader object the
hooked device created (its bytecode hash) or else by the program's name (+0x0C). Constants are the translator's
layout (`fnvnx_dk_layout.h`): per stage UBO 0 = `c[256]`, UBO 1 = `i[16]` + `b[16]` at byte 256 + 16n, UBO 2 =
driver constants (VS posfix (0, 0, 1); PS fog colour / mode / range, alpha function and reference from the tracked
render state). Their values: the register files the engine wrote through the device (the hooked
`Set{Vertex,Pixel}ShaderConstant{F,I,B}`), overlaid with the pass's constant maps (VS map NiD3DPass +0x48, PS map
+0x34; entries in the NiTArray at +0x0C; entry key +0x0C, register +0x1C, count +0x20, data size +0x28, data +0x30):
data-backed entries are copied, predefined matrices (`WorldViewProj`, `World`, `View`, `Proj`, `ViewProj`,
`WorldView`, `...Transpose`: rows of the column-vector matrix) and `EyePos` are computed; any other predefined
entry makes the draw `translatedPartial`. Vertex inputs are fed at their dk locations (POSITION 0, NORMAL 3, COLOR0
4, TEXCOORD0 6, TANGENT 14, BINORMAL 15; other inputs read (0, 0, 0, 1)) through `NxVtxLayout.attr[].location`.
Textures: the 16 tracker stages. `Frame::SetTranslated` binds the six UBOs after the per-draw slice and restores the
pass constants for the next native draw. The engine layouts used here are the decomp's; they are unverified on a
running engine (the n1 world frame builds them synthetically).

**Device hook.** `engine::HookD3DDevice(device)` (or `HookRendererDevice(NiDX9Renderer*)`, device at +0x28C) swaps
the device's method table for a copy: CreateVertexShader / CreatePixelShader (slots 91 / 106) record each shader
object's bytecode hash, slots 94/96/98 and 109/111/113 keep the register files; every call still reaches the
device behind. The native boot must call it before the renderer creates its shaders (request in the S6n report).

**translated.nxsp** (the pack; NX1 check 9). Built on the player's side at install / build time, never compiled
on the console, never committed (game-derived):

```sh
sdp2dksh -o <dir> --glsl shaderpackage*.sdp                       # S6b (render/shaders)
python3 -I render/native/tools/nxsp_pack.py <dir> translated.nxsp --names shaderpackage019.sdp --check [--glsl]
```

The console reads `sdmc:/switch/fnv/translated.nxsp` (`FNVNX_SD_TRANSLATED`, sdlayout.h; the owner copies it next to the game: it is made from the player's files, so it can never be inside fnv.nro's RomFS, unlike NX1's `shaders.nxsp` for our own shaders) into memory and hands it to
`nx::TranslatedStore::Load`; `Renderer::SetTranslatedStore` binds it. Container: NX1's NXSP v1
(`toolchain/nro/shaderpack.py`):

| part | layout |
|---|---|
| header (32 bytes) | `"NXSP"`, u32 version 1, u32 count, u32 index offset, u32 data offset, u8[12] pack id (sha256[:12] of index + data) |
| index (count x 104 bytes) | char name[48], family[24], variant[16] (NUL-padded); u32 stage (0 vertex, 1 fragment), offset, size, crc32 of the blob |
| data | blobs at 256-byte boundaries |

| family | name | variant | blob |
|---|---|---|---|
| `translated` | the bytecode key, `fnvnx_sha256` (lib/sha256: SHA-256 of the D3D9 bytecode up to and including END), 64 lowercase hex digits (`fnvnx_shader_key_hex`): **the index key** | `<vs\|ps>_<major>_<minor>/<mask>`, mask = 4 hex digits, the VS input locations read | the DKSH |
| `translated-glsl` | the same hash (host / emulator packs only, `--glsl`) | - | the GLSL, NUL-terminated |
| `translated-name` | the program's name in the package the engine loads (e.g. `SLS2001.vso`) | its hash | none (size 0) |

`nxsp_pack.py --check` fails when any program sdp2dksh listed lacks a DKSH. At run time a program without DKSH is a
skipped draw (`Skip_NoDksh`) on deko3d, never a compile; only the GL reference backend may create a program from
GLSL, and every such create counts in `DrawStats.runtimeCompiles` (a failure on the console). The player's package
019: 815 programs (354 vertex, 461 fragment), all with DKSH, 1007 names, 7.3 MB without GLSL.

**Counters** (`nx::DrawStats`, `Renderer::stats()` / `lastFrameStats()`, per frame): engine draws (every
Do_RenderShape / Do_RenderTristrips and every image-space RenderEffect), native by route, translated (and how
many with an unevaluated constant-map entry), misrendered (drawn, but not by the family / program its pass needs),
skipped by reason (no renderer, unreadable geometry, skinned, no translated program, no engine shader / pass,
submit refused, image-space effect, no translated store, no DKSH), runtime compiles. The n1 world frame (28 passes
+ 3 effects through the stubs, GL and the ILP32 guest) measured: before this work 29 of 31 skipped or misrendered
(25 drawn with the tile family); now 4 skipped (1 skinned geometry, 3 image-space effects), 25 translated, 2 tile.
The real captures go through nx_render's D3D9 executor (§8), which runs the same translated programs.

### 4.1 N2 skinning (started)

Gamebryo skins on the GPU per NiSkinPartition: each partition has its bone list (the palette), up to 4 weights and
palette indices per vertex, and a vertex goes to world space by `BoneWorld[b] * SkinToBone[b] * v` (NiSkinData's
skin-to-bone transforms, the bones' world transforms from NiSkinInstance's bone nodes). The native path:

* `include/nx/skin.h`: `NxBoneXform` (an NiTransform) and `nx_skin_palette` (composition, 3x4 rows per palette entry,
  up to `NX_SKIN_MAX_BONES` = 32 per partition);
* SKIN variants `lit_skin` and `depth_skin` (vertex stage): attributes 4 (weights) and 5 (indices), both unorm8x4;
  the palette (`vec4 bones[96]`) follows the draw block (`NxLitSkinDraw`, `NxDepthSkinDraw`, 2000 / 1600 bytes), the
  skinned position and normal replace the model-space ones (world = identity, wvp = view-projection);
* N0 scene `skin`: a limb bent 90 degrees at a joint with blended weights, a quad whose bone turns its normal away
  from the light, a depth_skin quad moved in z by its bone; golden + checks, identical on the ILP32 guest.

Next: reading NiSkinInstance / NiSkinData / NiSkinPartition from engine objects into palettes and partition draws
(Do_RenderGeometry's skinned branch, now skipped and counted), and the remaining families' SKIN variants.

### 4.5 d9 families (removed 2026-11-10, see render/d3d9)

(removed 2026-11-10) Hand-written programs standing in for the game's programs are not a product path (owner order 2026-11-10; the translator `render/shaders` handles every program). `capture/d3d9_families.*`, the `d9_adts` / `d9_envmap` / `d9_fogpass` shaders, `nx_render --families` and their checks are gone (removed 2026-11-10).

## 5. Recommendation

**Native renderer, hybrid on the shader side; drop the D3D9 emulation layer as the main path.**

* Native submission: the engine's draw path is already narrow (three virtual interfaces, draws only in the
  renderer, ~20 direct-D3D files), the permutation key is already computed by kept engine code (pass id + lights +
  flags), and per-draw CPU is the Switch's bottleneck: the native path costs an estimated 3-5x less CPU per draw
  and enables instancing and light loops the D3D9 API shape prevents.
* Shaders: hand-written per family (shippable, our work), with S6b's translated originals as reference and as a
  temporary per-family fallback during bring-up - the useful half of D6 stays.
* No D3D9 emulation even for UI/debug: UI is one family (Tile) on the same path; debug/fixed-function is 3 tiny
  shaders. The remaining direct-D3D users (screenshot, procedural textures, adapter enumeration, queries) are ~40
  functions rewritten natively. D6's `shim/include` D3D9 headers stay useful to compile the kept units that still
  *name* D3D types (structs, enums) without calling the device.
* Cost: native is more engine-specific work up front (understanding each BSShader's constants: done once per family
  with the decomp + CTAB as reference), and the first pixel needs the engine to boot (S2/S3/S4), unlike D6's
  standalone M0. Mitigated by a standalone harness (N0) and a packet-stream replay tool.

### Milestones (one developer, estimates)

| | content | estimate |
|---|---|---|
| **N0 core** | deko3d device/queue/swapchain, caller-backed memory below 4 GB, UBO/vertex/staging rings with fences, global descriptor sets, DDS->`DkImage`, `PipelineState` cache, packet format + sorter, frame graph skeleton, render thread; GLSL->DKSH build step; standalone test: textured, sorted, instanced quads | 3-4 weeks |
| **N1 menus** | overrides of `NiDX9Renderer` frame/target/camera/`RenderShape` entry points, static/dynamic geometry upload from `NiGeometryData`, `NiDX9RenderState` -> state word, `tile` + `nolight` families, image-space copy/menuBG/scanlines, screenshot readback | 5-7 weeks |
| N2 world opaque | `lit` (SM3 path first), `land`, `depth`, `sky`, distant LOD, skinning; pass/packet counters; parity harness against translated originals | 8-10 weeks |
| N3 world complete | shadows, water (+FFT), speedtree, grass, particles/precip, decals/blood, bolt/beam/vfog, full image-space chain, render-to-texture (Pip-Boy, local map, VATS) | 8-12 weeks |
| N4 performance | instancing (trees/grass/LOD/clutter), light-loop collapse of the 2.x path, transient target aliasing, 2-thread recording, docked/handheld resolution | ongoing |

Total to in-game parity ~6-8 months, comparable to D6's 6-9, with a frame cost that fits the Switch.

**First milestone (N0+N1 "native main menu")**: the game boots to the main menu and renders it entirely through
`nx::Renderer` - the tile scene graph via the `tile`/`nolight` families, menu background image-space effect,
textures from the player's BSAs, 60 fps at 1280x720, no D3D9 device object in the process; plus the standalone
N0 test in `tests/` running on hardware. Exit criteria: menu screenshot matches a PC reference capture within the
parity threshold for the tile family; packet/draw counters reported. Depends on S2 (override input), S3 (data
image + vtable rewrite), S4 (boot), S5 (toolchain/deko3d linkage).

### Requests to other streams

* S2-link: `--override <tsv>` with precedence over decomp definitions at the same address; report of shadowed
  definitions; overrides also rewrite vtable-slot and data-image code pointers through the address map.
* S1-compile: rule `NATIVE1`, drop listed units from the build (from `render/native/drop_units.txt`).
* S5-toolchain: decide deko3d ILP32 vs LP64 worker; the front only assumes "submit one packet arena per frame".
* S6b-shaders: keep `sdp2dksh` as the parity reference; add a mode that dumps CTAB name->register per blob (the
  family constant structs are checked against it).

## 6. Scaffolding and tools

* `render/native/include/nx/` - interfaces only (no implementation yet): packet, pipeline state, frame graph,
  rings, family.
* `render/native/tools/d3d_boundary.py <decomp>` - groups S6's D3D call-site scan by subsystem (§1.9).

### 6.1 Pass markers in the stream

`NxOp_Marker` with tag `NX_MARKER_PASS` (`nx/gpu_abi.h`) carries the engine pass id (the numbering of §1.3) and the
family id (`NxFamilyId`, `nx/families.h`) of the draws that follow it in a pass. The front encodes one before the
first draw of each run of equally tagged draws after sorting; nx::Renderer tags its draws from `Draw.enginePass` /
`Draw.family` or from `Renderer::SetEnginePass(pass, family)`, which the N1/N2 families call where they know the pass.
Backends ignore it; `.nxcap` captures keep it, so `render/verify/capture/nxcap.py` can count draws per pass id.

## 7. N0: what exists (2026-11-04)

Status, commands and numbers: [`render/native/STATUS.md`](../render/native/STATUS.md); testable claims for the
verifier: [`render/native/CLAIMS.md`](../render/native/CLAIMS.md).

### 7.1 Pieces

| layer | files | role |
|---|---|---|
| state encoding | `render/native/include/nx/state.h` | backend-neutral `NxRenderState` (4 x u64), `NxDynamicState`, shader bits, `NxSamplerState` (16 B), `NxFormat`/`NxFormatInfo`, `NxEpilogue` |
| D3D9 translator | `render/native/front/d3d9_state.{h,c}` | pure C99: D3D9 render/sampler states and formats -> the encoding above, mirroring Gallium Nine 20.1 |
| boundary | `render/native/include/nx/gpu_abi.h`, `front/gpu_dispatch.c` | `NxGpuApi` table, command stream ops, ext_call module 10 |
| front | `render/native/include/nx/front.h`, `front/front.cpp` | rings, state cache (16-bit ids), radix-sorted packets, frame/pass encoding with redundant-bind elimination, dynamic records (viewport, scissor, blend constant, depth bias, stencil ref), DDS |
| GL reference backend | `render/native/gl/nxgl.{h,c}` | desktop GL 4.5 core over EGL surfaceless (Mesa llvmpipe), host only |
| deko3d backend | `render/native/host/nxgpu.{h,c}`, `host/nxgpu_host.c` | LP64 in the Switch host module; captured state blocks, render thread |
| shaders | `render/native/shaders/*.glsl`, `build.py` | one GLSL 4.60 source per shader (`// nx-variants:` builds define sets) -> DKSH (uam) + GL text in one generated C table; `d3d9_epilogue.glsl` (fog, alpha test); N1 families `tile` and `nolight` (blocks in `include/nx/families.h`) |
| N0 scenes | `render/native/n0/`, `tests/s6n/scenes.py`, `tests/s6n/golden/` | 13 scenes with CPU-computed conformance checks (incl. exact D3D9 coverage); host runner, ILP32 Switch guest, the same guest under Unicorn (`tests/s6n/emu_n0.py`) |
| N1 plan | `render/native/N1_PLAN.md`, `overrides.tsv`, `REQUESTS.md` | renderer entry points with slot evidence, override rows, linker/prepare requests with failing tests |
| N1 core | `render/native/n1/` (`renderer.{h,cpp}`, `selftest.cpp`, `build.sh` -> `libnxrender.a`) | nx::Renderer (frame, target group, camera virtuals) and the record-only nx::StateTracker; self-test on GL and as the ILP32 guest |
| capture / replay | `render/native/capture/` | CaptureBackend (guest and host) records streams + bound memory; `replay_gl` replays a capture on the host and compares readbacks |
| N2 families | `render/native/shaders/{lit,depth,land,sky}.*.glsl` | SM3 lit (8 lights, specular), depth (ZONLY/DEPTHMAP), land (4 layers), sky; scenes with CPU checks |
| environment | `render/native/env.sh`, `render/native/switch/build.sh` | rebuild toolchain/libnx/deko3d/uam; build the Switch package |

### 7.2 The backend split

The front never calls a graphics API. It builds an `NxRenderState` per draw (from D3D9 states through the
translator, or directly), interns (shader pair, state, layout) into a 16-bit state id, and emits 64-byte packets.
`Frame::Finish` sorts them (pass | layer | state | texture block | depth) and encodes one command stream; the
backend replays it. Both backends take the same stream and the same `NxGpuApi` calls:

* **deko3d** (`nxgpu.c`): each distinct w[0] (blend + write masks), w[1] low half (depth-stencil ops) and w[2]
  (rasterizer) is captured once into Maxwell command words and replayed on BindState; stencil masks/ref, blend
  constant, depth bias, viewport and scissor are dynamic (`dkCmdBufSet*`); sRGB write rebinds the render target
  through its sRGB view; device flags DepthZeroToOne | OriginUpperLeft | YAxisPointsUp (the D3D conventions).
* **GL** (`nxgl.c`): the same state applied with GL calls (diffed per word); `glClipControl(GL_LOWER_LEFT,
  GL_ZERO_TO_ONE)` with the vertex shader's y negated (framebuffer row 0 = image top, D3D9 tie rule), first-vertex provoking vertex, GL binding = stage x 16 + deko3d slot (shim on the GLSL text,
  `#version 460` -> `#version 450 core` because llvmpipe stops at 4.50), sRGB texture views + `GL_FRAMEBUFFER_SRGB`,
  caller-backed memory copied into GL buffers when a bind is replayed. Synchronous; readback by `glReadPixels`.
* In the ILP32 guest the front reaches the backend through S5's `ext_call` (module `NXGPU_EXT_MODULE` = 10,
  `nxgpu_host.c` registers it like Nine's module 9); LP64 programs call `nxgpu_dispatch()` directly.
* Texture handle = image id | sampler id << 20 (`dkMakeTextureHandle`) on both. Images whose format has an sRGB
  view reserve id + 1 for it.

### 7.3 Verification

* Translator: one unit test per state (`make -C tests/s6n`, 527 checks).
* Scenes on GL: every scene evaluates checks whose expected values are computed on the CPU from the D3D9 rules
  (blend equation, stencil arithmetic, wrap modes, mip selection, channel rules, fog formulas, exact coverage),
  31500 checks, and is compared bit-exactly with its golden PNG (`make -C tests/s6n scenes`).
* The ILP32 guest build of the same scenes runs under Unicorn with nxgpu forwarded to the GL backend and matches
  the goldens bit-exactly (`make -C tests/s6n emu GUEST=...`).
* The same scene code builds as the ILP32 Switch guest (`switch/build.sh`); on the console it writes the readbacks
  to `sdmc:/fnvnx/n0/out/`, which `tests/s6n/scenes.py --compare <dir> --tol N` checks against the GL goldens.
* V6n's oracle (Nine on softpipe, `render/verify/`) drives the translator API (§7.5) directly.

### 7.4 Traps (each has a check or a stated rule)

| trap | rule | where |
|---|---|---|
| D3D9 pixel centres are integers (Nine: `half_pixel_center = 0`) | NxViewport = D3D viewport + 0.5 for +0.5-centre rasterizers; `gl_FragCoord.xy` = VPOS + 0.5 | translator, C25/C59 |
| GL tie rule with GL_UPPER_LEFT is not D3D9's (horizontal and slanted edges; a viewport bias fixes only horizontal ones) | GL backend: GL_LOWER_LEFT + `gl_Position.y` negated by the shim; llvmpipe then follows the top-left rule exactly (V6n defect y_fill_bias) | `nxgl.c`, C59 |
| front face | "front" = CW on screen in y-down space; GL: `glFrontFace(GL_CCW)` in its y-up space (measured); deko3d: `DkFrontFace_CW` (to confirm) | scene `cull` |
| depth write with EQUAL/NEVER | forced off (Nine) | C4 |
| BOTHSRCALPHA / non-separate alpha | Nine's fix-ups and alpha table | C9, C12 |
| depth bias units | D3D9 units x 2^24 (D24) / 2^16 (D16); Nine-on-softpipe applies about half (oracle quirk, D1) | C18, C40 |
| MIPFILTER NONE | ignores LOD bias and MAXMIPLEVEL | C26, scene `sampler` |
| cube textures | always clamp-to-edge | C29 |
| view swizzles also swizzle the BORDER colour | no format uses a channel-moving swizzle: A8/L8/A8L8/L16/A2R10G10B10/ATI1 are expanded on upload (`nx_d3d9_convert`) | C32, C34, C35 |
| ATI2 | X/Y swapped (basetexture9.c) | C32 |
| sRGB write | sRGB view of the render target; clears are not encoded | `nxgl.c`, `nxgpu.c` |
| X8 render targets read dst alpha 1 | BGRX8/RGBX8 stored as GL_RGB8 / deko3d *X8 formats | `nxgl.c` |
| flat shading | GL cannot switch interpolation per state: families use `flat` qualifiers | STATUS.md |
| llvmpipe GLSL 4.50 | version rewrite in the shim | C60 |

### 7.5 C API for the verifier

Everything V6n needs is in two headers, both plain C with `extern "C"`:

* `render/native/include/nx/state.h` - encodings. Field positions are the `NXRS_*` shift macros; read a field with
  `NXRS_GET(word, shift, bits)`. Enums: `NxCompare` (= D3DCMP - 1), `NxStencilOp` (= D3DSTENCILOP - 1),
  `NxBlendFactor` (GL meaning), `NxBlendOp` (= D3DBLENDOP - 1), `NxCull` (on-screen winding), `NxFill`, `NxFog`,
  `NxWrap`, `NxMipFilter`, `NxFormat` (memory layouts listed), `NxSwizzle`.
* `render/native/front/d3d9_state.h` - the translator. Rules and deviations are listed in its header comment.

```c
uint32_t rs[NXD3D_RS_COUNT]; nx_d3d9_default_rs(rs);          /* then set states by D3DRENDERSTATETYPE */
NxD3D9Input in = {rs, ps_version, depth_d3dfmt, viewport, scissor, clip_planes};
NxRenderState st; NxDynamicState dyn; uint32_t shaderBits;
nx_d3d9_translate(&in, &st, &dyn, &shaderBits);                  /* any output may be NULL */
uint32_t samp[NXD3D_SAMP_COUNT]; nx_d3d9_default_samp(samp);
NxD3D9Texture tex = {d3dfmt, is_cube, is_shadow, managed_lod};
NxSamplerState ss; nx_d3d9_sampler(samp, &tex, &ss);
NxFormatInfo fi = nx_d3d9_format(d3dfmt);                         /* native format, view swizzle, flags */
if (fi.flags & NXFMT_CONVERT) n = nx_d3d9_convert(d3dfmt, w, h, mips, faces, src, srcSize, dst, dstCap);
NxEpilogue e; nx_epilogue_fill(&e, &dyn, shaderBits);             /* fragment UBO slot 2 of every shader */
```

Test harness (`render/native/front/harness.h`, LP64 host): `nxh_init(&h, api, w, h, colorFmt, depthFmt)`,
`nxh_shader(&h, NxStage_Fragment, glsl, dksh, size)`, `nxh_draw(&h, ps, &state, &dyn, ubo, size, &epi, handles, n)`,
`nxh_clear(&h, flags, rgba8, depth, stencil, x, y, w, h)`, `nxh_readback(&h, NxRb_RGBA8 | NxRb_RGBA32F | NxRb_Depth32F |
NxRb_Stencil8, x, y, w, h, dst, size)`. ABI v3 adds `NxReadback.format` and `NxOp_Clear` (rectangle clears).

Units: blend constant, fog colour and border are colour channels in [0,1] (byte / 255); `alphaRef` = ALPHAREF /
255 unclamped; `depthBiasUnits` is in GL `glPolygonOffset` units (r = 2^-24 for D24); viewport in pixels for a
+0.5-centre rasterizer; scissor in pixels, top-left origin; sampler `minLod`/`maxLod` in mip levels.

## 8. nx_render: captures and scenes to PNG (the S16-compare entry point)

S16-compare renders reference frames with the real exe under Wine and compares them (PSNR/SSIM/FLIP) with the
native renderer. Its one entry point here is **`nx_render`**, a host program that renders a capture or a scene
with the native renderer on the GL reference backend (`render/native/gl`, bit-exact to the goldens) and writes PNGs.

### 8.1 Build, run, outputs, exit codes

```sh
source <(render/native/env.sh --print)
make -C tests/s6n /tmp/fnvnx-s6n-tests/nx_render     # binary (OUT=<dir> to place it elsewhere)
make -C tests/s6n nx_render                          # its test (tests/s6n/test_nx_render.py)

nx_render d3d9  <file.nxd9>  -o <dir> [--shaders <dir>] [--per-draw] [--until-draw N] [--frame F]
nx_render nxcap <file.nxcap> -o <dir> [--per-draw] [--until-draw N] [--frame F]
nx_render scene <name|all>   -o <dir> [--per-draw] [--until-draw N]
```

Needs a Mesa/EGL GL 4.5 context (surfaceless; llvmpipe is fine). `<dir>` is created (one level).

| file | written for |
|---|---|
| `f<frame %05u>.png` | every frame: an NXD9 `PRES`, or an `.nxcap` `SUBM` with present = 1 (the swapchain image) |
| `rb<index %04u>.png` | every `.nxcap` `RDBK` record, in file order (RGBA8 as is; RGBA32F clamped; depth and stencil as grey) |
| `f<frame>_d<draw %05u>.png` | `--per-draw`: after each draw, the colour target of that draw's pass; draw = index of the draw in the frame's submit, counting backend draws (skipped NXD9 draws are not counted) |
| `<scene>.png`, `<scene>_d<draw>.png` | scene mode (160x120; identical to `tests/s6n/golden/<scene>.png`) |
| `summary.txt` | the counters line also printed on stdout; for NXD9 the first skipped draw and why |

`--native <dir>` (hand-written replacement shaders) and `tools/native_dir.py`, `shaders/native.tsv`, `tools/nxd9_census.py` were removed 2026-11-10 (see render/d3d9); `tools/parity_png.py` compares two output directories.

`--until-draw N` stops drawing after draw N of each frame (the frame image then holds draws 0..N; clears and later
passes still run). `--frame F` restricts `--per-draw` and `--until-draw` to frame F. Together they localise a
mismatch: bisect with `--until-draw`, then `--per-draw --frame F` around it.

Exit codes: **0** rendered everything; **1** runtime failure (malformed input, missing shader GLSL, backend error;
message on stderr); **2** usage or file I/O error; **3** rendered, but some draws were skipped as unsupported
(counts and the first reason in `summary.txt`). Images are PNG RGBA8, row 0 at the top, D3D9 pixel coordinates.

### 8.2 The `.nxcap` format (post-translation capture, `render/native/capture/capture.h`)

Recorded by `nx::CaptureBackend` after the native front end (state words, GLSL families); replayable only into an
NxGpuApi, not into D3D9. All integers little-endian; structs are those of `include/nx/gpu_abi.h` and
`include/nx/state.h` (sizes static-asserted there).

* Magic `"NXCAP\0\0\1"` (8 bytes), then records `u32 tag, u32 payload bytes, payload` (payloads are multiples of 4
  bytes). Tags are four ASCII bytes read as a little-endian u32. Unknown tags must be skipped.

| tag | payload |
|---|---|
| `INFO` | `NxGpuInfo` (32 B: magic `0x5550474e`, version 3, frames_in_flight, flags, width, height, backend, pad) |
| `MEMC` | u32 id, u32 size, u32 kind (`NxMemKind`): a memory block; contents arrive with `SUBM` |
| `IMGC` | u32 id, `NxImageDesc` (32 B; its `data` field 0), then `data_size` bytes of texels (mips 0..n-1 tightly packed, BCn by 4x4 blocks, padded to 4). An image whose format has an sRGB view also owns id + 1 |
| `SMPC` | u32 id, `NxSamplerState` (16 B) |
| `SHDC` | u32 id, u32 stage (0 vertex, 1 fragment), u32 dkshBytes, u32 glslBytes, DKSH, GLSL (each padded to 4) |
| `SUBM` | u32 frame, u32 present, u32 words, the command words, u32 nranges, then per range u32 block, u32 offset, u32 size and `size` bytes (padded to 4): the bytes of every memory range the stream binds (UBO, vertex, index), as the GPU saw them |
| `RDBK` | `NxReadback` (40 B; `dst` 0), u32 bytes, the pixels read at that point (row 0 at the top, format per `NxReadback.format`) |

Command words: `[opcode | argument count << 16]` then the arguments; the opcodes and their arguments are the
`NxOp` list in `gpu_abi.h` (End, DefineState, DefineLayout, BeginPass, EndPass, Barrier, BindState, BindUbo,
BindVtx, BindIdx, BindTextures, Draw, DrawIndexed, Viewport, Scissor, StencilRef, BlendConst, DepthBias, Clear,
Marker). A reader skips an unknown opcode by its count. `NxOp_Marker` with tag `PASS` names the engine pass id
(§1.3) and family of the draws that follow (§6.1). Texture handles in `BindTextures` are `image | sampler << 20`.
`render/verify/capture/nxcap.py` (V6n) is an independent reader.

### 8.3 The `.nxd9` format (D3D9-level capture; what S16's converter writes)

`.nxcap` cannot carry the exe's frames (it is recorded after translation), so S16 converts its D3D9 call stream to
**NXD9** (`render/native/capture/d3d9cap.h`; Python reference writer `tests/s6n/nxd9.py`). `nx_render d3d9` runs it
through the native D3D9 translator (`front/d3d9_state.h`, Nine 20.1 rules) and S6b's translated shaders into the GL
backend. **Choice:** shader translation is S6b's (`render/shaders`, MojoShader `glsldk` profile, layout contract
`render/shaders/include/fnvnx_dk_layout.h`); nx_render does not link it. The converter either embeds each shader's
GLSL in its `VSH`/`PSH` record, or leaves it empty and passes `--shaders <dir>` holding `<hash %016llx>.glsl` as
`sdp2dksh -o <dir> --glsl shaderpackage*.sdp` writes them (key = `fnvnx_sha256`, SHA-256 of the bytecode, as 64 hex digits by `fnvnx_shader_key_hex`).

File: magic `"NXD9\0\0\0\1"`, then records `u32 tag, u32 payload bytes, payload, zero padding to 4` (the length
excludes the padding). Little-endian; ids are converter-chosen, nonzero, unique per kind; records apply in order,
exactly like the D3D9 calls they stand for. Unknown tags are skipped.

| tag | payload | D3D9 call |
|---|---|---|
| `DEV ` | u32 width, height, back-buffer D3DFORMAT, auto depth-stencil D3DFORMAT (0 none) | CreateDevice / Reset |
| `TEX ` | u32 id, D3DFORMAT, width, height, levels, flags (1 render target, 2 cube), then per level u32 bytes + the level's texels, tightly packed rows (BCn: 4x4 blocks), padded to 4; cube: each level's bytes hold its six faces one after another in D3DCUBEMAP_FACES order (square, bytes a multiple of 6); render targets: levels with 0 bytes | CreateTexture / CreateCubeTexture (+ initial contents) |
| `TEXC` | u32 cube texture id, face (D3DCUBEMAP_FACES), level, bytes, the face's texels (bytes = the level's bytes / 6) | LockRect on a cube face |
| `TEXU` | u32 id, level, bytes, texels | LockRect/UnlockRect, UpdateTexture, UpdateSurface |
| `SURF` | u32 id, kind (1 colour, 2 depth-stencil), D3DFORMAT, width, height, texture id (render-target texture whose level 0 this is, else 0) | GetSurfaceLevel, CreateRenderTarget, CreateDepthStencilSurface |
| `VB  ` / `IB  ` | u32 id, size; IB adds format (101 INDEX16, 102 INDEX32) | CreateVertexBuffer / CreateIndexBuffer |
| `VBU ` / `IBU ` | u32 id, offset, bytes, data | Lock/Unlock (the bytes written; DISCARD needs no special record) |
| `DECL` | u32 id, count, count x D3DVERTEXELEMENT9 (8 B: u16 stream, offset; u8 type, method, usage, usageIndex), no END element | CreateVertexDeclaration |
| `VSH ` / `PSH ` | u32 id, hash lo, hash hi, bytecodeBytes, bytecode (padded), u32 glslBytes, GLSL | CreateVertexShader / CreatePixelShader |
| `RS  ` | u32 D3DRENDERSTATETYPE, value (floats as their bits) | SetRenderState |
| `SS  ` | u32 sampler 0-15, D3DSAMPLERSTATETYPE, value | SetSamplerState |
| `TSS ` | u32 stage, type, value | SetTextureStageState (recorded; unused) |
| `STEX` | u32 sampler 0-15, texture id (0 none) | SetTexture |
| `SVS ` / `SPS ` | u32 shader id (0 = fixed function) | SetVertexShader / SetPixelShader |
| `VSCF` / `PSCF` | u32 start register, count, count x 4 floats | Set*ShaderConstantF |
| `VSCI` / `PSCI` | u32 start, count, count x 4 int32 | Set*ShaderConstantI |
| `VSCB` / `PSCB` | u32 start, count, count x u32 | Set*ShaderConstantB |
| `SDCL` | u32 declaration id | SetVertexDeclaration |
| `STRM` | u32 stream, vb id, offset, stride | SetStreamSource |
| `SIDX` | u32 ib id | SetIndices |
| `SRT ` | u32 index, surface id (0 = back buffer); resets viewport and scissor to the target, as D3D9 does | SetRenderTarget |
| `SDS ` | u32 surface id (0 none, 0xFFFFFFFF the auto depth-stencil) | SetDepthStencilSurface |
| `VIEW` | D3DVIEWPORT9 (u32 x, y, w, h; f32 minZ, maxZ) | SetViewport |
| `SCIS` | RECT (i32 left, top, right, bottom) | SetScissorRect |
| `CLR ` | u32 flags (D3DCLEAR_*), D3DCOLOR, f32 z, u32 stencil, u32 rect count, rects | Clear (viewport, intersected with the scissor rect while SCISSORTESTENABLE, and with each rect; the colour through the sRGB view under SRGBWRITEENABLE) |
| `DRAW` | u32 D3DPRIMITIVETYPE, startVertex, primitiveCount | DrawPrimitive |
| `DRWI` | u32 type, i32 baseVertex, u32 minIndex, numVertices, startIndex, primitiveCount | DrawIndexedPrimitive |
| `PRES` | (empty) | Present: the end of a frame, written as `f<frame>.png` |

Executed (and claimed by the test): programmable draws from stream 0 with a declaration; every D3DDECLTYPE except
UDEC3/DEC3N (decoded to float4 on the CPU, D3DCOLOR swizzled to RGBA); attribute locations by
`fnvnx_dk_attrib_location`, remapped to the backend's 8 attribute slots; VS outputs added (as 0) for PS inputs the
VS does not write (D3D9 allows it, GL refuses to link); per stage UBO 0 = `c[256]`, UBO 1 = `i[16]` + `b[16]`,
UBO 2 = driver constants (VS posfix (0, 0, 1): the native viewport already carries D3D9's half pixel; PS fog mode
from FOGENABLE / FOGTABLEMODE (vertex fog when the table mode is NONE; none for ps_3_0), FOGSTART/END/DENSITY,
FOGCOLOR, alpha test = ALPHAFUNC when ALPHATESTENABLE, ALPHAREF / 255); all render states through
`nx_d3d9_translate` (blend, depth, stencil, raster, sRGB write, bias, scissor); sampler states through
`nx_d3d9_sampler` (sRGB view when SRGBTEXTURE); render-target textures and surfaces, the auto depth surface.

Sampled textures get their image when a draw binds them after a TEX, TEXU or TEXC (one image per change seen by a
draw, not per record): NxGpuApi images are immutable and never freed, and d9cap's captures fill managed textures
level by level, so making an image per TEXU ran past `NX_MAX_IMAGES` (4096) on docmitchell_13000 and the textures
made after that sampled as none (S16 R4: the black frame; render-land). A texture whose image cannot be made skips
the draw (`texture image not created`).

Skipped and counted (exit code 3): fixed-function draws (no VS or no PS), FVF draws (no declaration), POSITIONT,
streams other than 0 (instancing), cube render targets (sampled as none; cube textures with contents are sampled since S16 R2), vertex textures. Varyings the VS writes beyond
the PS are fine. A converter should still emit everything; nx_render reports what it skipped.

Size limits: 4096x4096 images, 128 MiB of per-frame vertex/index/constant copies, 4 M command words per frame.

### 8.4 Input limits (malformed files)

Both formats are outside input (S16's converter, console captures), so both readers check every size against its
record in 64 bits, and against fixed bounds, before they allocate, copy or call the backend. A file that breaks a
rule ends in exit 1 with a message that names the record (J-0147, V1). Ids out of a table's range map to 0 (no
resource) and do not fail.

| | limit |
|---|---|
| both | a record's length never runs past the file |
| `.nxd9` | DEV at most 4096x4096; TEX and SURF at most 8192 on a side and 16 M texels, at most 14 levels; VB/IB at most 64 MiB, VBU/IBU ranges inside 64 MiB; DECL at most 64 elements; CLR rect count fits the record; draws over 4 M vertices are skipped (exit 3); buffers and texture levels together at most 1 GiB |
| `.nxcap` | MEMC ids 0-256, each defined once, 1 byte to 256 MiB, at most 1 GiB in all; IMGC at most 8192 on a side and 16 M texels, at most 14 mips, `data_size` inside the record; SUBM: words, memory ranges and each op's arguments inside the record, ranges inside their block, Draw/DrawIndexed at most 4 M vertices and 65,536 instances, and at most 4 M vertices times instances; RDBK at most 8192 on a side, pixel bytes inside the record |

Tests: `make -C tests/s6n nx_render_robust` builds nx_render with ASan and UBSan, then runs
`tests/s6n/test_nx_render_bounds.py` (43 cases, one per limit) and V1's `tests/v1/test_nx_render_robust.py` (13
cases, plus `ITERS` mutations of the two `nx_render` test captures, 300 by default).

## 9. Frame-rate targets, dynamic resolution and presets

Targets set by the owner: **45 fps stable** (22.2 ms) is the main target, **60 fps** (16.7 ms) the stretch, below
30 fps a fail, and the target platform for the gates is **720p handheld**. Lowering the resolution must not hide
render cost: both presets render the scene at **1280x720** (scale 1.0); the performance gates measure that frame
with dynamic resolution **off**, and the work to reach 45 fps there is optimisation (profile-driven, P6n's ranked
hotspots in `render/native/perf/report.json` / `docs/RENDER_PERF.md`). Dynamic resolution (below) is only a safety
net for peak scenes in shipped builds, never the baseline. The UI is never scaled.

### 9.1, 9.2 Dynamic resolution (removed 2026-11-10)

Cancelled by the owner (removed 2026-11-10). `front/dynres.h`, the N0 scene `dynres` and `test_dynres` are removed; the gates measure the 1280x720 frame.

### 9.3 Presets

Output 1280x720 in both modes (the UI, the upscale target); docked 1080p output is a later option (the UI at
1080p, the scene range unchanged). Values are the starting points to tune with measurements (P6n's CPU numbers in
`render/native/perf/`, GPU timestamps); INI settings are the game's own (`FalloutPrefs.ini` / `Fallout.ini`).

| setting | docked | handheld |
|---|---|---|
| GPU clock | 768 MHz | 384 MHz (460 boost profile if allowed) |
| scene resolution | 1280x720 (scale 1.0) | 1280x720 (scale 1.0) |
| dynamic resolution (safety net only, off in the gates) | 0.75 - 1.00 (960x540 - 1280x720), starts at 1.0 | 0.75 - 1.00 (960x540 - 1280x720), starts at 1.0 |
| budget | 22.2 ms (45); 16.7 ms in 60-fps mode | 22.2 ms (45) only |
| shadow maps `iShadowMapResolution` | 1024 | 512 |
| `iActorShadowCountExt` / `Int` | 2 / 4 | 1 / 2 |
| `fShadowLODStartFade` | 200 | 100 |
| water reflections `iWaterReflectWidth` x `Height` | 512 x 512 (scaled with the scene) | 256 x 256 |
| `bUseWaterReflections` / `bUseWaterDisplacements` | 1 / 1 | 1 / 0 |
| grass `fGrassStartFadeDistance` / `iMinGrassSize` | 3500 / 80 | 2000 / 120 |
| object LOD `fLODFadeOutMultObjects` / `Items` / `Actors` | 10 / 3 / 6 | 6 / 2 / 4 |
| `fBlockLoadDistanceLow` / `fTreeLoadDistance` | 50000 / 40000 | 35000 / 25000 |
| `fSpecularLODStartFade` | 1500 | 800 |
| `iMaxDecalsPerFrame` / `iMaxSkinDecalsPerFrame` | 10 / 3 | 5 / 1 |
| `bDoDepthOfField` | 1 | 0 |
| `iMultiSample` / `bTransparencyMultisampling` | 0 / 0 | 0 / 0 |
| `uGridsToLoad` | 5 (gameplay: unchanged) | 5 |

### 9.4 Render-thread budgets

The render thread runs the native back half (§3.6): sort, encode, submit, plus the per-draw translation the
families do at packet build. Budgets per frame (the main thread's game update and cull are measured by P6n and are
not part of these numbers):

| work | 22.2 ms (45 fps) | 16.7 ms (60 fps) |
|---|---|---|
| packet build in the families (main thread, ~1-2 us x 1.5-3.5 k draws) | 4.0 ms | 3.0 ms |
| sort + encode (render thread, ~0.5 us/draw) | 1.5 ms | 1.2 ms |
| deko3d command recording + submit (render thread) | 2.0 ms | 1.5 ms |
| dynamic geometry / UBO ring writes (render thread) | 1.0 ms | 0.8 ms |
| render thread total (must overlap the main thread) | **4.5 ms** of 22.2 | **3.5 ms** of 16.7 |
| GPU (the controller's budget; frame N-1 while the CPU builds N) | 22.2 ms, aim 19-21 | 16.7 ms, aim 14-16 |
| slack for the main thread (game update, AI, cull) | ~16 ms | ~12 ms |

Above budget, the order of measures is: instancing (trees, grass, LOD), the SM3 light loop (fewer passes), the
controller's scene scale, then the preset's distances.
