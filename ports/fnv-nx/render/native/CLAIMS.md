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
failed` and `golden comparison: 13 scenes match`). One scene: `python3 tests/s6n/scenes.py --bin <run_gl> --out <dir> cull`.

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
| C63 | tile family (`shaders/tile.*.glsl`, block NxTileDraw): tile = texture x tint, tile_scroll adds TexScroll to the uv, tile_vc = texture x vertex colour x tint, tile_alphamap = (tint.rgb, tint.a x texture.a), within 1/255; SRCALPHA blending of a tile over the background matches the CPU blend. (The PS semantics are provisional until the parity check against the translated TILE1000-1002.) | scene `tile`, 80 checks |
| C62 | The ILP32 Switch guest (`switch/build.sh` -> guest.nso: front, translator and scenes built with fnvnx-a64 -mabi=ilp32) run under Unicorn with S5's emulated host and nxgpu forwarded to the GL backend produces all 13 scene images bit-identical to the goldens and passes all 31500 checks. | `make -C tests/s6n emu GUEST=<out>/guest/guest.nso` (pip install unicorn) |
| C65 | Front: StateCache dedups (shader pair, NxRenderState, layout) and defines only new ids; the radix sort orders by pass, layer, state, texture block, depth and keeps submission order for equal keys; Frame encoding binds an unchanged vertex buffer once, emits StencilRef only for a draw that overrides the ref and rebinds the state to restore it, BlendConst on changes only, nothing for pass-default viewport/scissor; Encoder reports overflow and keeps room for End; ParseDDS maps masks/FOURCCs through nx_d3d9_format and rejects truncated files. | `make -C tests/s6n` -> `front: 33 checks, 0 failed` (test_front.cpp) |
| C66 | Capture/replay (`capture/`): CaptureBackend records resources, every submitted stream and the bytes of every bound memory range; `replay_gl` replays the file on a fresh GL context and every readback is byte-identical to the captured one - for the LP64 host run (13 frames, 1.17 MB) and for the capture the ILP32 guest writes under emulation. | `make -C tests/s6n capture` (`replay: 13 frames ... 0 differ`), `make -C tests/s6n emu GUEST=...` |
| C67 | `nx::StateTracker` (record-only NiDX9RenderState): Set/Get/Restore of render and sampler states with Gamebryo's save semantics; an unchanged state is not translated again; recorded states reach the NxRenderState through the translator. | `n1/selftest.cpp` test_tracker; `make -C tests/s6n n1` |
| C68 | `nx::Renderer`: BeginFrame/EndFrame/DisplayFrame, Begin/EndUsingRenderTargetGroup with CLEAR_* clears and the background colour, SetScreenSpaceCameraData with a port rectangle (viewport), and Render() with recorded D3D9 blend states draw the expected pixels into an engine-registered target group and the back buffer; CameraView/CameraProjection map the near/far planes to z 0/1 and frustum corners to (+-1, +-1), ortho included. 36 checks on GL, and the same 36 as the ILP32 guest under Unicorn. | `make -C tests/s6n n1`; `render/native/n1/build.sh <o>` then `make -C tests/s6n n1emu N1OUT=<o>` |
| C61 | The deko3d backend (`host/nxgpu.c`), host module (`host/nxgpu_host.c`) and the ILP32 N0 guest compile and link with fnvnx-a64 + libdeko3d 373bfd4 (`render/native/switch/build.sh`). Not yet run on hardware. | switch/build.sh |
