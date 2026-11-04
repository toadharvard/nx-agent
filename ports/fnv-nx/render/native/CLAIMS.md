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
| C32 | Formats: A8R8G8B8 BGRA8; X8R8G8B8 BGRX8 (RGB1); R5G6B5 R5G6B5; A1R5G5B5/X1R5G5B5; A4R4G4B4 (X4: RGB1); L8 R8 RRR1; A8 R8 000R; A8L8 RG8 RRRG; V8U8 RG8S RG11; A2R10G10B10 RGB10A2 BGRA; R16F R111; DXT1 BC1; DXT2/3 BC2; DXT4/5 BC3; ATI1 BC4 R001; ATI2 BC5 GR11 (X/Y swapped as basetexture9.c); D32, P8, R8G8B8 unsupported. | test_formats |
| C33 | `nx_d3d9_default_rs()` / `nx_d3d9_default_samp()` reproduce Nine's state defaults (ZENABLE FALSE, CULL CCW, ZFUNC LEQUAL, masks 0xF, BLENDFACTOR 0xFFFFFFFF, sampler WRAP/POINT/NONE, MAXANISOTROPY 1). | test_defaults, test_samp_defaults |
| C40 | Deviation D1: depth-bias units are D3D9's (x 2^24 for D24), not Nine-on-softpipe's effective ~half. | test_depthbias |
| C41 | Deviation D2: no sRGB view for L8/A8L8/L16. | test_samp_srgb |
| C42 | Deviation D3: D3DFMT_D32 has no native format. | test_formats |
