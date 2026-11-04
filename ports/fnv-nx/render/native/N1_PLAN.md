# N1 plan: the main menu through the native renderer (S6n, 2026-11-04)

Goal (RENDER_NATIVE.md §5): the game boots to the main menu and draws it entirely through `nx::` - tile scene
graph, menu background image-space effect, textures from the BSAs - with no D3D9 device in the process. This note
records what the decomp shows about the entry points and how the replacement is wired. Addresses are the decomp's
`@fnv` annotations; slot numbers are read from decomp call sites (evidence column). Nothing here is decomp code.

## 1. Shape of the work

```
 kept engine code                         replaced (overrides.tsv)                      native (render/native)
 Interface / Tile tree / MenuManager  ->  NiDX9Renderer virtuals (§2)   ------------->  nx::Renderer: frame, targets,
 TileShaderProperty, NiMaterialProperty   NiDX9RenderState (§3)          (record only)    camera, packets (front.h)
 BSShaderNoLightingProperty               TileShader render half (§4)  ------------->  tile family (shaders/tile.*)
 ImageSpaceManager parameter logic        BSShaderNoLighting render half (§5) ------>  nolight family
                                          ImageSpaceEffectMenuBackground (§6) ------>  image-space pass
```

The native objects keep the replaced classes' layouts (fields that kept code reads stay in place); native state hangs
off one reused pointer field per object (RENDER_NATIVE.md §2.3).

## 2. NiDX9Renderer entry points

The engine calls the renderer through `NiRenderer`'s non-virtual wrappers, which check the frame state by name
(`FUN_004a0e10(name, state)`, inlined into game code) and then call the protected `Do_*` virtual. The wrappers' names
and the slot they call fix the slot numbers:

| slot | virtual (Gamebryo 2.2 name) | evidence | candidate implementation | status |
|---|---|---|---|---|
| 95 | Do_BeginFrame | order | 0x00e74f40 (device lost check, `BeginScene`) | inferred |
| 96 | Do_EndFrame | order | 0x00e69660 (`EndScene`) | inferred |
| 97 | Do_DisplayFrame | order | 0x00e7ccd0 (`Present(0,0,0,0)`) | inferred |
| 98 | Do_ClearBuffer(rect, mode) | 0x007148c0: "ClearBuffer" wrapper calls vt[98] | 0x00e6f310 (`Clear`) | confirmed slot |
| 99 | Do_SetCameraData(loc, dir, up, right, frustum, port) | 0x004e9c90: "SetCameraData" wrapper, 6 args | - | confirmed slot |
| 100 | Do_SetScreenSpaceCameraData(port) | 0x004a0dd0: "SetScreenSpaceCameraData" | - | confirmed slot |
| 101 | Do_BeginUsingRenderTargetGroup(group, clear) | order | 0x00e6eea0 (`SetRenderTarget` per buffer) | inferred |
| 102 | Do_EndUsingRenderTargetGroup | order | - | inferred |
| 107 | Do_RenderShape(NiTriShape*) | 0x00a745c0 (NiTriShape render) calls vt[107] | 0x00e74840 / 0x00e745a0 (indexed draws) | confirmed slot |
| 108 | Do_RenderTristrips(NiTriStrips*) | 0x00a71d30 calls vt[108] after the base render | 0x00e6c550-region strip loop | confirmed slot |
| 109-111 | Do_RenderPoints / Lines / ScreenTexture | order | 0x00e70be0, 0x00e718a0 (other draw paths) | inferred |

Request R2 (REQUESTS.md) asks for the image vftable so the inferred rows become facts; overrides.tsv already
names the slots as `vslot:` rows (R1b), so the table does not need the implementation addresses at all.

What the native versions do (all on the existing front, `nx/front.h`):

* **BeginFrame**: `Frame::Begin(n)`; **EndFrame**: `Frame::Finish`; **DisplayFrame**: submit with `present = 1`.
* **ClearBuffer**: becomes the clear flags of the current pass (or a full-target quad when the rect is partial).
* **SetCameraData / SetScreenSpaceCameraData**: compute view/projection exactly as Gamebryo does (D3D clip space,
  the translator's viewport rule) into the pass constants; the menu uses the screen-space camera.
* **Begin/EndUsingRenderTargetGroup**: `Frame::AddPass` with the group's colour/depth images (`nx::RenderTarget`).
* **RenderShape / RenderTristrips**: ask the geometry's shader (TileShader, BSShaderNoLighting, ...) for its family
  packet (§4, §5); upload `NiGeometryData` once into a StaticBuffer (dynamic data into the frame ring).

## 3. NiDX9RenderState

The engine's state cache (`NiDX9RenderState`, virtual) is the reason the translator takes `rs[210]` and
`samp[16][14]`: the native replacement keeps the same virtual surface but only records - `SetRenderState(state,
value, save)` writes `rs[state]`, `SetSamplerState(stage, type, value)` writes `samp[stage][type]`, save/restore stacks
as Gamebryo has them - and every draw calls `nx_d3d9_translate()` + `nx_d3d9_sampler()` on the recorded arrays to get
the state id (front `StateCache`) and the sampler handles. Dirty flags per word avoid re-translating unchanged
groups (`nx_d3d9_blend_word` etc. are exported for that). Implementation addresses: block 0x00e8xxxx
(`RenderState::ApplyRenderModes` 0x00e8bb70 is one); the full list needs R2.

## 4. TileShader (TILE pass, true id 757)

Decomp (bsshader/tileshader.cpp):

* Shaders: `CreateTileVertexShaders` 0x00bca0d0 builds `tile.v.hlsl` x {-, `TEXSCROLL`, `VC`} as TILE1000-1002.vso
  (vs_1_1); `CreateTilePixelShaders` 0x00bca240 builds `tile.p.hlsl` x {-, `ALPHAMAP`, `VC`} as TILE1000-1002.pso.
  Native: `shaders/tile.vert.glsl` (variants tile, tile_scroll, tile_vc) and `shaders/tile.frag.glsl` (tile,
  tile_alphamap, tile_vc), block `NxTileDraw` (nx/families.h); scene `tile` checks them (C63).
* Constants (`CreateShaderConstantMaps` 0x00bcaff0): VS `WorldViewProjTranspose` (c0-c3) and `TexScroll` (c4, from
  `DAT_01202198`); PS `tintcolor` (c0, from `DAT_01202188`).
* Per draw (`UpdateMaterial` 0x00bca980): tint rgb = material diffuse, tint a = material alpha x alpha property
  alpha; TexScroll from the material when its flag is set; an optional clip rectangle (material flag) becomes a
  scissor rect (y remapped through the viewport when the menu is rendered into the main target) - native: DynRec
  scissor + the D3D9 SCISSORTESTENABLE state; the renderer-specific property's flag word sets stencil func/ops/ref
  (`FUN_00b980c0`, `FUN_00b98180`) - native: written into the recorded rs[] (§3) so the translator handles it.
* Pass selection (`PassShader::ApplyToRenderer` 0x00bca760): VS/PS pair from the geometry flags (alt -> VS B,
  next -> PS B, "second" -> pair C).
* Note: `0x00bcb180` in the same file switches on 0x24c/0x24d, which are GEOMDECAL / GEOMDECAL_S in the true pass
  numbering (V6n's pass_names.py), not tile passes: that function belongs to the geometry-decal shader.

## 5. BSShaderNoLighting (NOLIGHTING passes 66-81 true ids)

`InitializeShaderData` 0x00bc5c40 builds `NoLighting.v.hlsl` x {TEX, VC, FALLOFF, SKIN} combinations (16) and the
particle-system VS (PSYS_SUBTEX, PSYS_STRIP) and waterfog VS. N1 needs TEX / VC / FALLOFF without SKIN:
`shaders/nolight.vert.glsl` (tex, vc, texvc, texfall, texvcfall), `nolight.frag.glsl` (tex, col), block
`NxNoLightDraw`. Per pass: `PrepareShaderPass` 0x00bc9a50, material colour `UpdateMaterialColor` 0x00bc6fe0.
The falloff formula in the native VS is provisional (opacity interpolated between the start and stop angles of
BSShaderNoLightingProperty) until the parity check.

## 6. Image space for the menus

`ImageSpaceEffectMenuBackground::Setup` 0x00c04730 (menu background) and the menu-unpack shader
(`imagespaceshadermenuunpack.cpp`) run at the end of the frame on `BSRenderedTexture` targets. Native: one
image-space pass family (`shaders/is_*.glsl`, to write) with a full-screen triangle, reading the previous pass's
colour image (front `PassDesc.sampledAfter` + `NxOp_Barrier`).

## 7. Order of work and checks

1. R1/R2 land (orchestrator); `render/native/n1/` builds `libnxrender.a` (front + families + nx::Renderer) for the
   guest; the override rows lose `N1-pending` one by one.
2. Renderer frame/target/camera virtuals + record-only state cache; RenderShape for TileShader geometry only; other
   shaders fall back to "draw nothing" with a per-pass counter (visible in NxFrameStats).
3. Capture: the guest can dump the frame's command stream (front encoder output) to SD; `n0/run_gl`-style host
   replay renders it with the GL backend -> menu screenshots on the host without a Switch, compared with a PC
   capture (exit criterion of RENDER_NATIVE.md §5: within the tile-family parity threshold).
4. nolight + menu background image space; then the BSA texture path (ParseDDS + nx_d3d9_convert).

## 8. N2 (opaque world) - first steps

* `lit` family on the SM3 path (Lighting30 / 3XLIGHTING passes, light loop from a light UBO), `depth` (ZONLY),
  `land`, `sky`; per-pass packet counters.
* Skinning: bone palette slice of the UBO ring per partition (`NiSkinPartition`).
* The parity harness: S6b's translated originals (sdp2dksh) run beside our families on the same packets.
