### J-XXXX | 2026-11-04 | stream: S6n-native-render,P6n-render-perf | status: wip | parent: J-0149

The owner's three questions about the native path's shaders, answered from the code at 3925e92.

* **(1) Which shaders the shipping native path binds, and what decides it.**
  - Design (RENDER_NATIVE.md §4): every family uses S6n's own hand-written GLSL, compiled by uam. S6b's sdp2dksh
    (the game's SM3 programs, translated) is a parity reference and a planned per-family fallback
    (`nx::Family::Translated`). That fallback is **not implemented**: no code path binds a translated program.
  - What decides it in code:
    - `render/native/overrides.tsv` lists the engine entry points that are replaced (renderer frame and target
      calls, render-state cache, textures, D3DX, TileShader, BSShaderNoLighting, ImageSpaceManager::RenderEffect).
    - `nx::Renderer`'s family dispatch (`NxFamilyId` in `include/nx/families.h`: Tile, NoLight, Lit, Depth, Land,
      Sky, ImageSpace) picks the shader for each draw.
  - Per family today:

| family | native shader exists | wired into the engine path |
|---|---|---|
| UI (tile) | yes | yes |
| nolight (incl. particle strips) | yes | yes |
| image space | menuBG only | menuBG only; every other effect is counted and skipped |
| lit (statics, actors) | yes, incl. static light and epilogue permutations; skinning started | no |
| land | yes (land, land_lit) | no |
| sky | yes | no |
| shadow / depth | yes (depth, depth_at) | no |
| trees (SpeedTree), grass, water, LOD, decals, precipitation, world image-space chain | **no** | no |

  - `Renderer::RenderGeometry` (the Do_RenderGeometry override) draws all geometry it reaches with the tile shaders
    (tile_vc / tile). World families are not dispatched yet.
* **(2) Pixel parity of native shaders against the game's.**
  - V6n's c69 harness (`tests/v6n/claims/c69_families.py`, §4.2) renders each native family next to the game's
    original program for the same pass, on per-case inputs. It has 12 cases (TILE1000-1002, NOLIGHT004, SKY,
    SLS1025/1040 LANDAD, SLS1000 ZONLY_At). That is case-level only.
  - S16 compares the **translated** programs against WineD3D. Nothing compares the native families against the
    game's shaders on real frames. The scene-level check (§4, parity item 2: the same captured frame rendered once
    with translated and once with native shaders) is not built. Nothing checks families that do not exist yet.
  - Today parity is guaranteed only for the 12 c69 cases.
* **(3) Which GPU row is honest.**
  - Both rows replay the synthetic mix_hi stream. That fixes draw counts, coverage, overdraw and the alpha/fog mix.
  - The "native" row (10.25 ms handheld) costs families with **stand-ins**. That makes it a lower bound, not a
    shipping estimate:
    - trees, LOD and water reflection are costed as `lit.frag`;
    - grass and particles as `nolight_tex.frag`;
    - the whole image-space + UI chain as one `tex.frag` copy at 3.5 screens, so no HDR, bloom, DOF or
      sunbeam cost.
  - The "real019" row (27.22 ms) costs the game's own programs, translated, on the same stream. It is the honest
    estimate of the effects the game actually renders, and an upper bound for the native path: hand-written
    families can be cheaper, but they have to render the same effects.
  - Decision taken here: real019 is the GPU metric, as the orchestrator set. The native row stays only as a
    per-family cost reference.
  - Both rows move to S5-data's real-game captures (fnv-nx-build `gamedata/captures`) when they land.
* **next**
  1. Wire the world families into the engine path (lit, land, sky, depth first).
  2. Write the missing families (trees, grass, water, LOD, image-space chain). Until a family is written, build
     the translated fallback so world rendering is complete.
  3. Build the scene-level parity check: the same captured frame, translated against native.
