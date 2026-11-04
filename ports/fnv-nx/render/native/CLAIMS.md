# render/native: family claims and behaviour claims

## Family claims (who works on which shader family)

The render machine (branch `claude/port-nsw-render`) owns render/native's integration. That covers the frame and
pass structure in `n1/renderer.cpp`, the family dispatch table, `overrides.tsv`, `include/nx/families.h`, the
**Translated** path (each pass that has no finished native family runs the game's own program through S6b's
sdp2dksh on deko3d), the skip counters (skipped engine draws = 0), and the scene check against the real captures
(fnv-nx-build `gamedata/captures`: menu_11640, docmitchell_13000).

A family stream claims a row by setting `owner` and `status` on its own branch and telling the render machine. The
render machine merges it.

**Order of work (owner rule D21, ARCHITECTURE.md: coverage first, then optimisation).**
1. Coverage (render machine). The general path draws every program correctly:
   - Translated for every family;
   - exact D3D9 state emulation as a class: every render, sampler and texture-stage state with its D3D9 or engine
     default and save/restore, constants, vertex formats and render targets.
   Metric: skipped draws = 0, and every capture scene passing (menu, docmitchell, and S5-data's new ones).
2. Only after that, family by family: hand-written GLSL, then GPU tuning. Each step is measured against
   Translated on the captures, at equal pixels and lower GPU time.
Until step 1 holds, family streams (render-lit, render-land, render-veg, G1-gpu) work on correctness and measurement:
- the family's parity on the captures;
- defects in the Translated path or the state emulation, with failing tests;
- cost-model accuracy.
They do not switch families to native.

Rules for family streams:
- Block structs may live in the stream's own header (e.g. `n1/veg_families.h`) until the render machine takes
  them into `families.h`.
- Family code lives in your family's files: `shaders/<family>*.glsl`, a family module under `n1/`, and the
  family's scenes and tests under `tests/s6n/`.
- You may add or change only your family's row in the dispatch table, and only your family's override rows.
- Any other change to renderer.cpp, overrides.tsv or families.h goes to the render machine as a request with a
  failing test.
- A family switches from Translated to native only when it passes the scene check against Translated on the
  captures (equal pixels within the RENDER_NATIVE §4 tolerances) and costs no more GPU time (real019 and the
  captures).

| family | engine passes / classes (RENDER_NATIVE §1.3) | native GLSL | path today | owner | status |
|---|---|---|---|---|---|
| UI tile | TileShader | tile, tile_vc, tile_scroll, tile_alphamap, text | native | render machine | done (V6n c69 TILE1000-1002) |
| nolight | BSShaderNoLighting, PSYS strips | nolight* | native | render machine | done (c69 NOLIGHT004) |
| image space: menuBG | ImageSpaceManager menuBG | menubg | native | render machine | done |
| lit: statics | SLS, Lighting30 (ADTS/ADT/AD/DIFFUSEPT/SPECULAR/TEXTURE, 3XLIGHTING) | lit, lit_spec, light/epilogue permutations | skipped -> Translated | render-lit | open |
| lit: actors (skinned, facegen, hair, eye) | SLS SKIN, Lighting30 skin variants | lit + SKIN (started) | skipped -> Translated | render-lit | open |
| land | LAND*, LANDAD, LANDLO | land, land_lit | skipped -> Translated | render-land | open |
| LOD (land, objects, trees) | DISTLOD, LOD land | - | skipped -> Translated | render-land | open |
| sky | SKY* (quad, clouds, stars, moon, sun) | sky | skipped -> Translated | render-land | open |
| water | WATER*, wading, interior, LOD water | - | skipped -> Translated | render-land | open |
| depth / shadow | ZONLY, DEPTHMAP, 3XDEPTH*, GRASS_SHADOW, shadow maps | depth, depth_at | skipped -> Translated | render-land | open |
| image space: world chain | HDR, bloom/blur, DOF, radial blur, sunbeams, refraction, cinematic, VATS, night vision, ... | - | skipped -> Translated | render-land | open |
| trees | SpeedTree branch/frond/leaf/billboard | - | skipped -> Translated | render-veg | open |
| grass | GRASS* | - | skipped -> Translated | render-veg | open |
| particles, precipitation, bolt, beam, decal, blood, vfog | per class | - | skipped -> Translated | render-veg | open |
| GPU cost model and pass structure | render/native/perf (gpu_cost.py, gen_stream.py, report.*), pass order, early-z, sorting | - | - | G1-gpu (with the render machine's perf work) | open |

"skipped -> Translated": this pass is skipped today, and the render machine's Translated path, now in progress, will
draw it. A claim replaces that with native.

# S6n claims (native renderer) - for the verifier

Each claim is a statement about behaviour. "Backed by" names the code and the test that shows it; run the tests
with the command given. Encodings are documented in `include/nx/state.h`; the translator API and its rules in
`front/d3d9_state.h`. Values are D3D9 numeric constants (d3d9types.h). Deviations from Nine-on-softpipe are
listed in `front/d3d9_state.h` ("Deviations", D1-D3) and are claims too (C40-C42).

Run: `make -C ports/fnv-nx/tests/s6n` (host gcc; prints `d3d9_state: N checks, 0 failed`).

## D3D9 state translator (`front/d3d9_state.{h,c}`, tests `tests/s6n/test_d3d9_state.c`)

| id | claim | backed by |
|---|---|---|
| C1 | `nx_d3d9_translate()` is pure: no globals written, no allocation; any of the three outputs may be NULL. | d3d9_state.c |
| C2 | ZENABLE != 0 (incl. D3DZB_USEW=2) sets the depth test; ZENABLE == 0 leaves w[1] bits 0-4 all zero. | test_zenable |
| C3 | ZFUNC 1..8 maps to NxCompare 0..7; ZFUNC 0 (NEVER_ZERO) and > 8 map to Never. | test_zfunc |
| C4 | Depth write = ZWRITEENABLE != 0 and the mapped ZFUNC is neither Equal nor Never. | test_zwriteenable |
| C5 | STENCILENABLE == 0 leaves w[1] bits 5-55 zero; enabled: front func/fail/zfail/pass from STENCIL*, ops 1..8 -> 0..7, other values -> Zero. | test_stencilenable, test_stencilops |
| C6 | Two-sided: back fields = CCW_* when TWOSIDEDSTENCILMODE != 0, else a bit copy of the front fields; bit 6 records the mode. | test_twosidedstencil |
| C7 | Stencil read mask, write mask, ref = STENCILMASK, STENCILWRITEMASK, STENCILREF & 0xFF in w[1] bits 32-55 and in NxDynamicState. | test_stencilmasks_ref |
| C8 | ALPHABLENDENABLE == 0 leaves w[0] bits 0-31 zero (masks still set). | test_alphablendenable |
| C9 | Colour factors use Nine's colour table (SRCCOLOR2 -> Src1Color, INVSRCCOLOR2 -> InvSrc1Color, invalid -> Zero); without SEPARATEALPHABLENDENABLE, alpha factors are SRCBLEND/DESTBLEND through the alpha table (SRCCOLOR2 -> One, INVSRCCOLOR2 -> Zero) and alpha op = colour op. | test_srcblend_destblend_tables, test_blendop |
| C10 | BLENDOP/BLENDOPALPHA 1..5 -> Add, Subtract, RevSubtract, Min, Max; other values -> Add. | test_blendop |
| C11 | SEPARATEALPHABLENDENABLE != 0: alpha factors from SRCBLENDALPHA/DESTBLENDALPHA (alpha table), op BLENDOPALPHA. | test_separatealphablend |
| C12 | SRCBLEND = BOTHSRCALPHA/BOTHINVSRCALPHA: src = SrcAlpha/InvSrcAlpha, dst colour = InvSrcAlpha/SrcAlpha, dst alpha too unless separate; else if separate and SRCBLENDALPHA is BOTH*: only dst alpha is fixed up. | test_bothsrcalpha |
| C13 | RT0..RT3 write masks = COLORWRITEENABLE, COLORWRITEENABLE1..3 & 0xF at w[0] bits 32/36/40/44. | test_colorwriteenable |
| C14 | blendConstant = BLENDFACTOR as RGBA, channel = byte/255 (ARGB order in). | test_blendfactor |
| C15 | CULLMODE NONE(1) -> None, CW(2) -> CW, CCW(3) -> CCW, other -> None (w[2] bits 0-1). | test_cullmode |
| C16 | FILLMODE POINT(1) -> Point, WIREFRAME(2) -> Wireframe, anything else (SOLID, SOLID_ZERO=0) -> Solid. | test_fillmode |
| C17 | SHADEMODE == FLAT(1) sets w[2] bit 7. | test_shademode |
| C18 | Depth-bias flag = (DEPTHBIAS \| SLOPESCALEDEPTHBIAS) != 0 as bit patterns (-0.0 counts). depthBiasUnits = DEPTHBIAS x 2^24 (D24S8/D24X8/D24X4S4/INTZ/DF24), x 2^16 (D16/D16_LOCKABLE/DF16), x 2^15 (D15S1), x 2^32 (D32), x 2^23 otherwise; depthBiasSlope = SLOPESCALEDEPTHBIAS; depthBiasD3D = DEPTHBIAS. | test_depthbias |
| C19 | SCISSORTESTENABLE != 0 sets w[2] bit 5; scissor = (left, top, max(right-left,0), max(bottom-top,0)). | test_scissortestenable |
| C20 | SRGBWRITEENABLE != 0 sets w[2] bit 6. | test_srgbwriteenable |
| C21 | CLIPPLANEENABLE & 0xFF goes to w[2] bits 8-15; clip planes are copied to NxDynamicState.clipPlane. | test_clipplaneenable |
| C22 | POINTSPRITEENABLE != 0 sets w[2] bit 16; pointSize = Mesa CLAMP(POINTSIZE, MIN, MAX) (x <= min gives min even when min > max). | test_pointsprite_pointsize |
| C23 | Shader bits: ALPHATESTENABLE != 0 sets bit 3 and bits 0-2 = mapped ALPHAFUNC; alphaRef = ALPHAREF/255 unclamped. | test_alphatest |
| C24 | Fog bits 4-6: None if FOGENABLE == 0 or (ps_version & 0xFFFF) >= 0x0300; else FOGTABLEMODE EXP -> Exp, EXP2 -> Exp2, LINEAR -> Linear, other -> Vertex. fogColor RGBA, fogEnd, fogScale = 1/(end-start), fogDensity are filled. | test_fog |
| C25 | Viewport: x = X + 0.5, y = Y + 0.5, w, h, minZ, maxZ copied. | test_viewport |
| C26 | Sampler, MIPFILTER NONE: mip None, lodBias 0, minLod 0; otherwise mip Nearest for POINT, Linear for any other value, lodBias = MIPMAPLODBIAS, minLod = clamp(MAXMIPLEVEL - managed_lod, 0, 15); maxLod always 15. | test_samp_mipfilter, test_samp_maxmiplevel_managed |
| C27 | MIN/MAG filter is nearest only for POINT on a non-shadow texture; every other value (NONE, LINEAR, ANISOTROPIC, ...) is linear. | test_samp_minmag |
| C28 | maxAniso = MAXANISOTROPY only when MINFILTER or MAGFILTER is ANISOTROPIC, else 0. | test_samp_anisotropy |
| C29 | ADDRESS WRAP/MIRROR/CLAMP/BORDER/MIRRORONCE -> Repeat/Mirror/Clamp/Border/MirrorOnce, other -> Clamp; cube textures get Clamp on all three axes. | test_samp_address |
| C30 | border = BORDERCOLOR ARGB repacked to RGBA8 (R in bits 0-7). | test_samp_border |
| C31 | SRGB sampler flag = SRGBTEXTURE != 0 and the texture format has NXFMT_SRGB (BGRA8, BGRX8, RGBA8, RGBX8, BC1-3). | test_samp_srgb |
| C32 | Formats: A8R8G8B8 BGRA8; X8R8G8B8 BGRX8 (RGB1); R5G6B5 R5G6B5; A1R5G5B5/X1R5G5B5; A4R4G4B4 (X4: RGB1); V8U8 RG8S RG11; R16F R111; DXT1 BC1; DXT2/3 BC2; DXT4/5 BC3; ATI2 BC5 GR11 (X/Y swapped as basetexture9.c); converted (C34): L8 RGBA8 RGB1, A8 RGBA8, A8L8 RGBA8, L16 RGBA16 RGB1, A2R10G10B10 RGB10A2, ATI1 RGBA8; D32, P8, R8G8B8 unsupported. Apart from ATI2's X/Y swap, no view swizzle duplicates, moves or zeroes a channel; the only constant is 1 for a channel the D3D format lacks. | test_formats |
| C34 | `nx_d3d9_convert()` expands NXFMT_CONVERT texels (all mips, faces): A8 -> (0,0,0,A), L8 -> (L,L,L,255), A8L8 -> (L,L,L,A), L16 -> 16-bit (L,L,L,65535), A2R10G10B10 -> R/B swapped, ATI1 -> decoded (Mesa rgtc integer formula) to (R,0,0,255); returns 0 for other formats, a short source or a small destination. | test_convert |
| C35 | BORDER addressing returns the border colour as Nine does on A8, L8 (alpha 1), A2R10G10B10 and ATI1 (fix for V6n defect border_swizzle). | scene `border` (tests/s6n/scenes.py) |
| C33 | `nx_d3d9_default_rs()` / `nx_d3d9_default_samp()` reproduce Nine's state defaults (ZENABLE FALSE, CULL CCW, ZFUNC LEQUAL, masks 0xF, BLENDFACTOR 0xFFFFFFFF, sampler WRAP/POINT/NONE, MAXANISOTROPY 1). | test_defaults, test_samp_defaults |
| C40 | Deviation D1: depth-bias units are D3D9's (x 2^24 for D24), not Nine-on-softpipe's effective ~half. | test_depthbias |
| C41 | (withdrawn with C34: L8 and A8L8 have sRGB views; L16 and V8U8 have none, as in Nine.) | test_samp_srgb |
| C42 | Deviation D3: D3DFMT_D32 has no native format. | test_formats |

## Front end, backends and scenes

Run: `make -C ports/fnv-nx/tests/s6n scenes` (builds `run_gl` with the GL backend and the shader package, runs every
scene, evaluates its conformance checks and compares with `tests/s6n/golden/*.png`; prints `scenes: N checks, 0
failed` and `golden comparison: 19 scenes match`). One scene: `python3 tests/s6n/scenes.py --bin <run_gl> --out <dir> cull`.

| id | claim | backed by |
|---|---|---|
| C50 | Every N0 scene image on the GL backend (llvmpipe, Mesa 25.2) matches its golden PNG bit-exactly. | scenes.py (tol 0) |
| C51 | cull: with CULLMODE NONE both an on-screen CW and an on-screen CCW triangle are drawn; CULLMODE CW removes the CW one only; CULLMODE CCW removes the CCW one only (CW on screen survives D3DCULL_CCW). | scene `cull`, 6 checks |
| C52 | blend: 12 blend configurations (incl. MIN/MAX, REVSUBTRACT, BOTHSRCALPHA, BLENDFACTOR, separate alpha, COLORWRITEENABLE R\|A, DESTALPHA) match the D3D9 blend equation computed on the CPU within 1/255. | scene `blend` |
| C53 | stencil: REPLACE, INCRSAT, EQUAL with three refs on one state id (NxOp_StencilRef), and two-sided stencil (CW = front ops, CCW = CCW_* ops) give the expected stencil values. | scene `stencil` |
| C54 | sampler: WRAP, MIRROR, CLAMP, BORDER, MIRRORONCE with point filtering sample the expected texel at 450+ pixel centres away from texel edges; mip selection: MIPFILTER NONE uses level 0, POINT uses level 1 when 2x minified, MAXMIPLEVEL 1 forces level 1 when magnified and is ignored with MIPFILTER NONE. | scene `sampler` |
| C55 | format: 16 D3D formats (A8R8G8B8, X8R8G8B8, A8B8G8R8, R5G6B5, A1R5G5B5, X1R5G5B5, A4R4G4B4, L8, A8, A8L8, V8U8, A2R10G10B10, G16R16, R16F, DXT1, ATI2) sample to the D3D9/Nine channel values within 1/255 (DXT1 3/255). | scene `format` |
| C56 | epilogue: linear/exp/exp2 table fog, vertex fog and alpha test (GREATER/LESS/EQUAL at ALPHAREF 127/128 against alpha 0.5) match the Nine formulas within 2/255. | scene `epilogue` |
| C57 | sorted: opaque packets are grouped by state id (3 BindState ops for 28 packets in 3 states); alpha packets keyed back-to-front composite in depth order although submitted scrambled. | scene `sorted` |
| C58 | instanced: one draw with 16 instances places and tints each instance from the per-draw UBO. | scene `instanced` |
| C59 | D3D9 rasterization on the GL backend: coverage equals the exact D3D9 rule (integer pixel centres, top-left tie rule) for triangles on integer, half, 1/16 and 1/256 px vertex grids, slanted edges included (viewport +0.5; GL_LOWER_LEFT with y negated in the vertex shader, no bias). | V6n `render/verify/rastercheck.py v6n_s6ngl` (0 wrong px of 240 triangles), `tests/v6n/run.sh y_fill_bias`; scene `textured` top-row checks |
| C60 | `nxgl_shim_glsl()` rewrites `#version 460` to `#version 450 core`, `binding = n` to `binding = 16*stage + n`, suffixes uniform block names per stage (`_nxvs`/`_nxfs`) and wraps the vertex `main` to negate `gl_Position.y`. | gl/nxgl.c; exercised by every scene |
| C64 | raster_a / raster_b: 30 triangles with vertices on 1, 1/2, 1/16 and 1/256 px grids cover exactly the pixels of a CPU implementation of the D3D9 rule (integer centres, top-left ties, 1/256 px fixed point), all 30720 pixels of their cells. | scenes `raster_a`, `raster_b`; `D3D9Covers()` in n0/scenes.cpp |
| C63 | tile family (`shaders/tile.*.glsl`, block NxTileDraw) has the originals' semantics (TILE1000-1002): tile = texture x tint; tile_scroll uv = uv x TexScroll.zw + TexScroll.xy; tile_alphamap rgb = texture.rgb x tint.rgb, alpha = texture.a x tint.a x alphamap.r (texture 1, unscrolled uv); tile_vc rgb = texture.rgb x tint.rgb, alpha = texture.a x vertex alpha. Matches the S6b translation of the user's TILE1000-1002 on V6n's c69 harness (5 tile cases, local .sdp). | scene `tile` (80 checks); V6n `tests/v6n/claims/c69_families.py` (needs FNVNX_SDP; alphamap needs the case to bind both textures, see RENDER_NATIVE.md §4.2) |
| C62 | The ILP32 Switch guest (`switch/build.sh` -> guest.nso: front, translator and scenes built with fnvnx-a64 -mabi=ilp32) run under Unicorn with S5's emulated host and nxgpu forwarded to the GL backend produces all 19 scene images bit-identical to the goldens and passes all 31589 checks. | `make -C tests/s6n emu GUEST=<out>/guest/guest.nso` (pip install unicorn) |
| C65 | Front: StateCache dedups (shader pair, NxRenderState, layout) and defines only new ids; the radix sort orders by pass, layer, state, texture block, depth and keeps submission order for equal keys; Frame encoding binds an unchanged vertex buffer once, emits StencilRef only for a draw that overrides the ref and rebinds the state to restore it, BlendConst on changes only, nothing for pass-default viewport/scissor; Encoder reports overflow and keeps room for End; ParseDDS maps masks/FOURCCs through nx_d3d9_format and rejects truncated files. | `make -C tests/s6n` -> `front: 33 checks, 0 failed` (test_front.cpp) |
| C66 | Capture/replay (`capture/`): CaptureBackend records resources, every submitted stream and the bytes of every bound memory range; `replay_gl` replays the file on a fresh GL context and every readback is byte-identical to the captured one - for the LP64 host run (13 frames at the time, now 17) and for the capture the ILP32 guest writes under emulation. | `make -C tests/s6n capture` (`replay: 13 frames ... 0 differ`), `make -C tests/s6n emu GUEST=...` |
| C67 | `nx::StateTracker` (record-only NiDX9RenderState): Set/Get/Restore of render and sampler states with Gamebryo's save semantics; an unchanged state is not translated again; recorded states reach the NxRenderState through the translator. | `n1/selftest.cpp` test_tracker; `make -C tests/s6n n1` |
| C68 | `nx::Renderer`: BeginFrame/EndFrame/DisplayFrame, Begin/EndUsingRenderTargetGroup with CLEAR_* clears and the background colour, SetScreenSpaceCameraData with a port rectangle (viewport), and Render() with recorded D3D9 blend states draw the expected pixels into an engine-registered target group and the back buffer; CameraView/CameraProjection map the near/far planes to z 0/1 and frustum corners to (+-1, +-1), ortho included. 36 checks on GL, and the same as the ILP32 guest (now 45 with C71/C73) under Unicorn. | `make -C tests/s6n n1`; `render/native/n1/build.sh <o>` then `make -C tests/s6n n1emu N1OUT=<o>` |
| C69 | N2 families: lit = ambient + point (1-(d/range)^2) + directional Lambert, lit_spec adds Blinn specular x texture alpha (54 pixels within 2/255 of the CPU formula); depth_out writes z/w, ZONLY + EQUAL colours exactly the depth-only area, depth_at (ZONLY_At, SLS1000) cuts the diffuse texture's alpha holes out of the depth pass; land blends base + 3 layers by vertex weights x vertex colour, land_lit (LANDAD, SLS1025/SLS1040) multiplies by clamp(N.L x light + ambient) with N from the normal map and L in the tangent frame; sky (SKY.vso) colours by the vertex colour weighting three blend colours on the far plane; nolight FALLOFF uses the original's smoothstep. The originals' semantics were checked locally against the S6b translations of the user's shader packages (all 12 c69 cases match with the interfaces of §4.2). | scenes `lit`, `depth`, `land`, `sky`; V6n c69 harness (local) |
| C70 | ABI v3: `NxReadback.format` = NxRb_RGBA8 / NxRb_RGBA32F / NxRb_Depth32F / NxRb_Stencil8 (40-byte struct); on GL a RGBA16F target reads back unclamped floats (e.g. -0.5, 2.0), depth reads 0.375 after a clear to 0.375, stencil reads 5 after a clear to 5; wrong image kind, too small a destination or a rectangle outside the image is refused. | `tests/s6n/test_harness.c` (`make -C tests/s6n` -> `harness: 23 checks, 0 failed`) |
| C71 | `NxOp_Clear` (flags, RGBA8, depth, stencil, pixel rectangle) clears only that rectangle of colour and/or depth-stencil, outside the bound state's masks and scissor; `Frame::Clear` orders it like a draw; `Renderer::Do_ClearBuffer` with a partial port-normalized rect clears exactly it (edges x = 96, y = 48 on a 128x96 target), the whole rect or 0 is a pass load. | test_harness.c; n1 selftest (38 checks) |
| C72 | `front/harness.{h,c}`: a C pixel-shader path over NxGpuApi - built-in full-screen VS (uv (0,0) top-left, D3D9 integer pixel centres: uv = (x/W, y/H)), caller GLSL PS with its UBO at fragment slot 1, NxEpilogue at slot 2, textures, and an NxRenderState/NxDynamicState from the translator (SRCALPHA blend checked). | test_harness.c |
| C73 | N1 wiring: `n1/engine_stubs.cpp` (in libnxrender.a) defines thiscall forwarders with the decomp signatures at 0x00e74f40 / 0x00e69660 / 0x00e7ccd0 / 0x00e6f310 / 0x00e6eea0 (BeginFrame, EndFrame, DisplayFrame, ClearBuffer, BeginUsingRenderTargetGroup) plus EndUsing / camera / NiDX9RenderState forwarders; every non-pending overrides.tsv row names a symbol the archive defines at an address the decomp defines; a frame driven only through the stubs clears and partially clears the back buffer (GL and ILP32 guest, 45 checks). Not yet run through the orchestrator's full `fnvlink.py resolve` (no objects.tsv here). | `python3 -I tests/s6n/test_overrides.py <o>/libnxrender.a`; `make -C tests/s6n n1`, `n1emu` |
| C74 | Screen space (V6n screen_space_halfpixel): `nx::ScreenSpaceProjection(m, W, H)` maps screen (x, y, z) to clip (2x - 1 - 1/W, 1 - 2y + 1/H, z/9999), the engine's 0x00e6cf40 view x projection with Gamebryo's D3D9 half-pixel correction; nx::Renderer passes the current target's size. | `tests/v6n/run.sh screen_space_halfpixel`; n1 selftest |
| C75 | Camera ports (V6n camera_port_rounding): Do_SetCameraData and Do_SetScreenSpaceCameraData build the viewport by truncation like 0x00e6c780 / 0x00e6cf40 (X = (u32)(W*left), Y = (u32)(H*(1-top)), Width = (u32)(W*(right-left)), Height = (u32)(H*(top-bottom))); a null screen-space port is the whole target. | `tests/v6n/run.sh camera_port_rounding` |
| C76 | sRGB clears (V6n clear_srgb): `NxClear_Srgb` (flag 8 of NxOp_Clear) clears the colour through the target's sRGB view when it has one (GL: GL_FRAMEBUFFER_SRGB on the sRGB-view attachment; deko3d: the sRGB view is bound for the clear - unconfirmed on hardware), so 0x80 is stored as 188 like Nine; `Renderer::Do_ClearBuffer` sets it while SRGBWRITEENABLE is recorded (also for a full clear, which then is an NxOp_Clear instead of a pass load). | `tests/v6n/run.sh clear_srgb`; test_harness.c (29 checks) |
| C77 | Pass markers (V6n request, RENDER_VERIFY.md §6.1): `NxOp_Marker` (tag, a, b; ignored by both backends) with tag `NX_MARKER_PASS`, a = engine pass id (§1.3 numbering, NX_PASS_UNKNOWN after tagged draws when untagged ones follow), b = NxFamilyId. The front emits one before the first draw of each run of equally tagged draws in a pass (after sorting), none for untagged passes; `nx::Renderer` tags draws from `Draw.enginePass/family` or `SetEnginePass`; CaptureBackend keeps it (it records the stream words verbatim). | n1 selftest (46 checks: TILE 757 then 758 in the menu pass, none in the back-buffer pass), GL + ILP32 guest |
| C78 | R2 from the user's exe: `tools/exe_vftables.py` reads the image vftables of NiDX9Renderer (115 slots), NiDX9RenderState (66), TileShader (84) and BSShaderNoLighting (79); all 21 `vft=` slot annotations in overrides.tsv equal the dump (the dump is game-derived and gitignored). | `python3 -I tests/s6n/test_overrides.py` (4 tests), `python3 -I tests/s6n/test_requests.py` (3 tests); slot checks need the local dump |
| C79 | N1 wiring: overrides.tsv has 21 active address rows into libnxrender.a: the NiDX9Renderer frame/clear/camera/target-group entry points; Do_RenderShape/Do_RenderTristrips (0x00e744b0) -> `Renderer::RenderGeometry`; NiDX9RenderState set/get of render and sampler states plus the blend setters (with the engine's per-state lock); no-op TileShader/BSShaderNoLighting shader construction. `engine::ReadGeometry` reads NiTriShape (list) and NiTriStrips (strips) through the interop offsets; the geometry draws with the tile family (vertex colour x stage-0 texture x tint) under the screen-space camera at the expected pixels and edges; skinned or data-less geometry is skipped and counted. Not covered yet: engine textures (NiDX9SourceTextureData), material/alpha properties as tint, skinning, the menu-background image space. | n1 selftest (55 checks) on GL and as the ILP32 guest |
| C80 | `nx_render` (RENDER_NATIVE.md §8): scene mode writes PNGs identical to the 18 goldens; nxcap mode replays run_gl's capture to rb<i>.png equal to the goldens; d3d9 mode executes NXD9 through the native translator and S6b-convention GLSL: D3DCOLOR vertex colours, indexed and non-indexed draws, alpha test through the PS driver UBO, a varying the VS does not write, rect clears, D3D9 pixel-centre edges, two frames; --per-draw / --until-draw / --frame give stable names and stop where asked; exit codes 0 / 1 (malformed) / 2 (missing file) / 3 (skipped fixed-function draw). Also run locally on a real TILE1000 VS/PS pair from the user's shader packages (not committed). | `make -C tests/s6n nx_render` (42 checks) |
| C81 | Dynamic resolution: scene `dynres` renders the scene at 0.75x into its own target, upscales it bilinearly (texel centres on pixel centres) and draws the UI after it at native resolution: UI box and 1-px line exact and sharp, scene interiors exact, the upscaled edge within 2/255 of the bilinear formula; same image as the ILP32 guest. `nx::DynResController` (a safety net only: 0.75-1.0, starts at 1.0, off in the gates): hysteresis (3 frames over 95 %, 30 under 80 %), two-step panic over 125 %, clamps, dead band, 8-aligned sizes. | scene `dynres` (golden + 12 checks), `make -C tests/s6n emu`; test_front `test_dynres` |
| C82 | Engine textures: the D3DX overrides answer image info from a DDS header (2x2 A8R8G8B8: width, height, D3DFMT 21, TEXTURE, DDS) and create a NativeTexture whose IDirect3DTexture9-layout method table answers GetLevelCount, GetLevelDesc (format, size), AddRef/Release through the slots the engine would call; NiDX9RenderState SetTexture / GetTexture / ClearTextureUsage bind and unbind its native image on the tracker. | n1 selftest |
| C83 | Tile properties: `ReadGeometry` finds the native texture from TileShaderProperty -> NiTexture -> renderer data, takes the tint from TileShaderProperty and its alpha x NiMaterialProperty alpha, and NiAlphaProperty's blend / alpha test drive the draw's D3D9 states (restored after it): a checker background textures exactly, a green button at alpha 0.5 blends to (128, 128, 0) over red within 1, an alpha-tested (GREATER 128) text tile keeps its opaque texel and discards the transparent one. | n1 selftest (GL and ILP32 guest, 76 checks) |
| C84 | Menu background effect (`menubg` family, `Renderer::RenderMenuBackground`): scene luminance through the tone texture, mixed by the blend constant: half mix of (64, 128, 191) and (200, 100, 50) gives (132, 114, 121) within 2, and of (255, 0, 0) gives (228, 50, 25) within 2. | n1 selftest |
| C85 | Lockable textures (the font / NiPixelData path): `CreateSurf` makes a NativeTexture from the renderer data's pixel format hint and the pixel data's size and levels and stores it at the renderer data's +0x64; GetSurfaceLevel / GetDesc / LockRect / UnlockRect through the IDirect3DTexture9 / IDirect3DSurface9 method tables take a texel write; the native image is made from it when the texture is next found. | n1 selftest |
| C86 | ImageSpaceManager::RenderEffect dispatch: EFFECT_MENUBG renders the menubg mix from a registered source target with the effect's tone texture and blend amount (same pixels as C84); other effects are counted and skipped. | n1 selftest |
| C87 | Tile stencil, scroll, scissor: NiStencilProperty REPLACE (ALWAYS, ref 1) on the left half then EQUAL 1 leaves a full-screen green tile only on the left half; TexScroll u + 0.5 moves the checker by one texel column; the scissor rectangle (100, 60)-(110, 70) limits a full-screen tile to exactly that rectangle; stencil and scissor states are restored after the draws. | n1 selftest (90 checks, GL and ILP32 guest) |
| C88 | N2 skinning: `nx_skin_palette` composes BoneWorld x SkinToBone into 3x4 rows (a rotated, scaled, translated bone with a translated bind maps (2, 0, 0) to (1, 4, 3)) and caps palettes at 32 bones; scene `skin` (lit_skin, depth_skin): the limb part on bone 1 is bent up about the joint and absent where the unbent limb would be, a bone turning a quad 180 degrees about y leaves only the ambient term, depth_skin moves a quad to depth 0.75; same image on the ILP32 guest. | test_front `test_skin_palette`; scene `skin` (golden + 7 checks), `make -C tests/s6n emu` |
| C89 | UBO ring capacity (P6n's overflow): `Frame::Init(be, 0, slice)` sizes the ring for `Frame::kMaxPackets` (8192) draws of slice + 256-byte epilogue per frame in each frame in flight (`UboBytesFor`); nx::Renderer uses it. The old 8 MiB ring at 3 frames in flight drops draws of a 3,710-draw frame (reproduced, and now counted by `droppedDraws()`); the sized ring holds 8,192. | test_front `test_ubo_ring_capacity` |
| C90 | Epilogue permutations: build.py builds `__e0` / `__ea` / `__ef` for every epilogue-using fragment shader; `Renderer::Render` binds `__e0` for draws without alpha test and fog (and no epilogue UBO) and `__ea` for alpha-tested draws, found for any package shader by its GLSL address; results unchanged (an alpha-tested and a plain tile get different permutations and the same pixels as before). | n1 selftest (91 checks, GL + ILP32); scenes 19/19; V6n selfcheck |
| C91 | lit static light permutations: `Renderer::Render` binds `lit_spec_d1p2__e0` for 1 directional + 2 point lights (given in another order) with the lights reordered and padded, and the image equals the dynamic `lit_spec` within 1/255; the lit scene is unchanged. `mediump` marks the fp16-safe quantities (§4.4); uam emits no fp16 code yet. | n1 selftest (100 checks, GL + ILP32); scene `lit` |
| C92 | `SortPackets` is an 8-bit-digit LSD radix over the full 64-bit key (one histogram pass, 8 KiB tables, constant digits skipped, ping-pong buffers): stable and identical to std::stable_sort on 200 random sets of up to 4,000 keys with ties; V6n's C65 property check passes. Measured with callgrind on the same generator (pass/layer/state/texture/depth keys): 3,710 draws 3.13 M -> 0.70 M instructions per sort, 1,490 draws 2.80 M -> 0.29 M, 300 draws 2.65 M -> 0.07 M (11-bit digits measured 0.73 M / 0.37 M / 0.17 M). | test_front `test_sort_property`; V6n selfcheck C65 |
| C93 | Uniform binds on change: `Frame::SetEpilogue` shares one epilogue block between consecutive draws with equal contents, and the encoder binds an epilogue block only when its contents differ from the bound one (6 draws a, a, b, b, a, a -> 3 epilogue binds instead of 6); the deko3d backend skips a uniform-buffer bind of the same range on the same stage and slot within a command list and reads cached block GPU addresses. Output unchanged. | test_front `test_epilogue_binds`; scenes, N1 selftest, V6n selfcheck |
| C94 | Per-stage sampler cache: `StateTracker::Handles` translates a stage's sampler (`nx_d3d9_sampler` + cache search) only after a `SetSamplerState` that changes a value, a restore, or a texture whose kind (cube, shadow, managed LOD, sRGB capability of the format) differs; another image of the same kind keeps the sampler. Handles are identical to the per-draw translation. | n1 selftest `test_sampler_cache` (7 checks); scenes, nx_render |
| C95 | `Renderer::Render` finds a fragment shader's epilogue permutations with one table read (shader id -> permutation set, ids below 4,096; larger ids keep the list scans), so shaders without permutations beyond the former 64-entry list are no longer looked up again on every draw; the per-draw constant slice is written once (data, then a zero tail) instead of cleared and then copied. Permutation choice and slice contents unchanged. | n1 selftest; scenes, nx_render |
| C96 | `Frame::Finish` compares a draw's dynamic record (viewport, scissor, blend constant, depth bias) with the encoded values only when its record id differs from the previous draw's in the pass; the encoded stream is byte-identical (mix_hi .nxcap before/after: same md5). | scenes, capture replay (19/19), n1 selftest, nx_render |
| C97 | `Renderer::Render` reuses the previous draw's state id while the tracker has not re-translated and shaders/layout are equal, and the previous draw's texture block while the tracker's texture generation (image, kind or sampler state of any stage) is unchanged in the frame and its samplers all exist; mix_hi .nxcap byte-identical. | n1 selftest (texture generation checks); scenes, capture replay, nx_render |
| C98 | The D3D9 epilogue's alpha compare is branch-free (NxCompare as a LESS/EQUAL/GREATER bit set; unordered operands pass NOTEQUAL and ALWAYS only), equal to the former switch for all 8 functions and ordered/NaN operands; uam no longer emits the switch as a 7-test loop: lit_spec_d1p2__ea 249 -> 222, nolight_tex__ea 80 -> 35, tex/nolight_tex runtime 131 -> 104 Maxwell instructions. | scenes (alpha test GREATER/LESS/EQUAL, 19 goldens), n1 selftest, capture replay |
| C99 | The lit static light permutations read per-draw values `Renderer::Render` prepares in the slice: the directional light as the unit vector towards it and each point light's 1 / range^2, so no fragment normalizes a constant or divides by range^2; images within 1/255 of the dynamic shader (n1 selftest), lit_spec_d1p2__e0 201 -> 179 Maxwell instructions. | n1 selftest (static vs dynamic); scenes |
| C100 | `Renderer::Render` reuses the previous draw's epilogue block and dynamic record while the tracker has not re-translated in the frame (both depend only on the resolved state), instead of filling, comparing and appending them per draw; mix_hi .nxcap byte-identical. | n1 selftest, scenes, capture replay, nx_render |
| C61 | The deko3d backend (`host/nxgpu.c`), host module (`host/nxgpu_host.c`) and the ILP32 N0 guest compile and link with fnvnx-a64 + libdeko3d 373bfd4 (`render/native/switch/build.sh`). Not yet run on hardware. | switch/build.sh |

## Stream claims (who writes which native family)

Families are claimed here before work starts, so parallel streams do not write the same GLSL. A family not claimed
stays with the render machine. The Translated path (S6b's translation of the original) covers a family until its
native GLSL lands.

| stream | branch | families | native GLSL (`shaders/`) |
|---|---|---|---|
| render-land | claude/fnv-render-land | terrain (LAND*, DISTLOD), sky (SKY*), water (WATER*, water FFT), depth / shadow map (ZONLY, DEPTHMAP, SM3ZOnly/SM3DepthMap/SM3SimpleShadow, shadow casters), post (image space: IS* - HDR, bloom/blur, copy, cinematic, DOF, radial blur, refraction, sunbeams, VATS, TV, noise, map) | `is_*.glsl`, `land*`, `sky*`, `water*`, `depth*` (draws mapped in `shaders/native.tsv`) |
