#!/usr/bin/env python3
"""state_model.py: the declarative model of ALL D3D9 device state (schema + sources: README.md). `python3 -I state_model.py OUTDIR` -> state.gen.h/.cpp.
Table lines: NAME default blocks group.  default = C++ expr (F0/F1 = float bit patterns, `i` = stage, @name = creation-parameter dependent, see state_gen.py);
blocks = P pixel / V vertex / - none (every state is in D3DSBT_ALL by rule); group = dirty group the draw path reads.
Block lists: MS D3DSTATEBLOCKTYPE pages "Saving Pixel/Vertex States With a StateBlock" (primary); where MS lists omit a state, Wine stateblock.c decides (FOGENABLE, NORMALIZENORMALS).
Defaults: MS D3DRENDERSTATETYPE / D3DSAMPLERSTATETYPE / D3DTEXTURESTAGESTATETYPE pages; deviations are reported by the test against nx_d3d9_default_rs."""
RS = """
ZENABLE @zenable P DEPTH
FILLMODE D3DFILL_SOLID P RASTER
SHADEMODE D3DSHADE_GOURAUD P RASTER
ZWRITEENABLE 1 P DEPTH
ALPHATESTENABLE 0 P ALPHA
LASTPIXEL 1 P RASTER
SRCBLEND D3DBLEND_ONE P BLEND
DESTBLEND D3DBLEND_ZERO P BLEND
CULLMODE D3DCULL_CCW V RASTER
ZFUNC D3DCMP_LESSEQUAL P DEPTH
ALPHAREF 0 P ALPHA
ALPHAFUNC D3DCMP_ALWAYS P ALPHA
DITHERENABLE 0 P RASTER
ALPHABLENDENABLE 0 P BLEND
FOGENABLE 0 V FOG
SPECULARENABLE 0 P LIGHT
FOGCOLOR 0 V FOG
FOGTABLEMODE D3DFOG_NONE V FOG
FOGSTART F0 PV FOG
FOGEND F1 PV FOG
FOGDENSITY F1 PV FOG
RANGEFOGENABLE 0 V FOG
STENCILENABLE 0 P STENCIL
STENCILFAIL D3DSTENCILOP_KEEP P STENCIL
STENCILZFAIL D3DSTENCILOP_KEEP P STENCIL
STENCILPASS D3DSTENCILOP_KEEP P STENCIL
STENCILFUNC D3DCMP_ALWAYS P STENCIL
STENCILREF 0 P STENCIL
STENCILMASK 0xFFFFFFFF P STENCIL
STENCILWRITEMASK 0xFFFFFFFF P STENCIL
TEXTUREFACTOR 0xFFFFFFFF P TEXFACTOR
WRAP0 0 P WRAP
WRAP1 0 P WRAP
WRAP2 0 P WRAP
WRAP3 0 P WRAP
WRAP4 0 P WRAP
WRAP5 0 P WRAP
WRAP6 0 P WRAP
WRAP7 0 P WRAP
CLIPPING 1 V CLIP
LIGHTING 1 V LIGHT
AMBIENT 0 V LIGHT
FOGVERTEXMODE D3DFOG_NONE V FOG
COLORVERTEX 1 V LIGHT
LOCALVIEWER 1 PV LIGHT
NORMALIZENORMALS 0 V LIGHT
DIFFUSEMATERIALSOURCE D3DMCS_COLOR1 PV LIGHT
SPECULARMATERIALSOURCE D3DMCS_COLOR2 PV LIGHT
AMBIENTMATERIALSOURCE D3DMCS_MATERIAL PV LIGHT
EMISSIVEMATERIALSOURCE D3DMCS_MATERIAL PV LIGHT
VERTEXBLEND D3DVBF_DISABLE V TESS
CLIPPLANEENABLE 0 V CLIP
POINTSIZE F1 V POINT
POINTSIZE_MIN F1 V POINT
POINTSPRITEENABLE 0 V POINT
POINTSCALEENABLE 0 V POINT
POINTSCALE_A F1 V POINT
POINTSCALE_B F0 V POINT
POINTSCALE_C F0 V POINT
MULTISAMPLEANTIALIAS 1 V RASTER
MULTISAMPLEMASK 0xFFFFFFFF V RASTER
PATCHEDGESTYLE D3DPATCHEDGE_DISCRETE V TESS
DEBUGMONITORTOKEN D3DDMT_ENABLE - MISC
POINTSIZE_MAX F1 V POINT
INDEXEDVERTEXBLENDENABLE 0 V TESS
COLORWRITEENABLE 0xF P BLEND
TWEENFACTOR F0 V TESS
BLENDOP D3DBLENDOP_ADD P BLEND
POSITIONDEGREE D3DDEGREE_CUBIC V TESS
NORMALDEGREE D3DDEGREE_LINEAR V TESS
SCISSORTESTENABLE 0 P RASTER
SLOPESCALEDEPTHBIAS F0 P RASTER
ANTIALIASEDLINEENABLE 0 P RASTER
MINTESSELLATIONLEVEL F1 V TESS
MAXTESSELLATIONLEVEL F1 V TESS
ADAPTIVETESS_X F0 V TESS
ADAPTIVETESS_Y F0 V TESS
ADAPTIVETESS_Z F1 V TESS
ADAPTIVETESS_W F0 V TESS
ENABLEADAPTIVETESSELLATION 0 V TESS
TWOSIDEDSTENCILMODE 0 P STENCIL
CCW_STENCILFAIL D3DSTENCILOP_KEEP P STENCIL
CCW_STENCILZFAIL D3DSTENCILOP_KEEP P STENCIL
CCW_STENCILPASS D3DSTENCILOP_KEEP P STENCIL
CCW_STENCILFUNC D3DCMP_ALWAYS P STENCIL
COLORWRITEENABLE1 0xF P BLEND
COLORWRITEENABLE2 0xF P BLEND
COLORWRITEENABLE3 0xF P BLEND
BLENDFACTOR 0xFFFFFFFF P BLEND
SRGBWRITEENABLE 0 P BLEND
DEPTHBIAS F0 P RASTER
WRAP8 0 P WRAP
WRAP9 0 P WRAP
WRAP10 0 P WRAP
WRAP11 0 P WRAP
WRAP12 0 P WRAP
WRAP13 0 P WRAP
WRAP14 0 P WRAP
WRAP15 0 P WRAP
SEPARATEALPHABLENDENABLE 0 P BLEND
SRCBLENDALPHA D3DBLEND_ONE P BLEND
DESTBLENDALPHA D3DBLEND_ZERO P BLEND
BLENDOPALPHA D3DBLENDOP_ADD P BLEND
"""
SAMP = """
ADDRESSU D3DTADDRESS_WRAP P SAMP
ADDRESSV D3DTADDRESS_WRAP P SAMP
ADDRESSW D3DTADDRESS_WRAP P SAMP
BORDERCOLOR 0 P SAMP
MAGFILTER D3DTEXF_POINT P SAMP
MINFILTER D3DTEXF_POINT P SAMP
MIPFILTER D3DTEXF_NONE P SAMP
MIPMAPLODBIAS F0 P SAMP
MAXMIPLEVEL 0 P SAMP
MAXANISOTROPY 1 P SAMP
SRGBTEXTURE 0 P SAMP
ELEMENTINDEX 0 P SAMP
DMAPOFFSET 0 V SAMP
"""
TSS = """
COLOROP i?D3DTOP_DISABLE:D3DTOP_MODULATE P TSS
COLORARG1 D3DTA_TEXTURE P TSS
COLORARG2 D3DTA_CURRENT P TSS
ALPHAOP i?D3DTOP_DISABLE:D3DTOP_SELECTARG1 P TSS
ALPHAARG1 D3DTA_TEXTURE P TSS
ALPHAARG2 D3DTA_CURRENT P TSS
BUMPENVMAT00 F0 P TSS
BUMPENVMAT01 F0 P TSS
BUMPENVMAT10 F0 P TSS
BUMPENVMAT11 F0 P TSS
TEXCOORDINDEX i PV TSS
BUMPENVLSCALE F0 P TSS
BUMPENVLOFFSET F0 P TSS
TEXTURETRANSFORMFLAGS D3DTTFF_DISABLE PV TSS
COLORARG0 D3DTA_CURRENT P TSS
ALPHAARG0 D3DTA_CURRENT P TSS
RESULTARG D3DTA_CURRENT P TSS
CONSTANT 0 - TSS
"""
# families with a per-key table above (prefix, enum, count expr, key ranges (lo, hi, first slot of range))
SAMP_RANGES = [("0", "16", 0), ("D3DDMAPSAMPLER", "D3DDMAPSAMPLER+5", 16)]       # 0-15, D3DDMAPSAMPLER, D3DVERTEXTEXTURESAMPLER0-3 (MS SetTexture/SetSamplerState)
XF_RANGES = [("D3DTS_VIEW", "D3DTS_VIEW+2", 0), ("D3DTS_TEXTURE0", "D3DTS_TEXTURE0+8", 2), ("D3DTS_WORLDMATRIX(0)", "D3DTS_WORLDMATRIX(0)+256", 10)]
# other families: name, element C++ type, count, shape, default, blocks, group, setter, getter.  blocks: A is implied; '-' = not in any block (rule: applied directly even while recording)
FAMILIES = [
    dict(name="tex", ct="IDirect3DBaseTexture9*", n="NX_SAMP_SLOTS", shape="objat", dom="samp", blocks="", group="TEXTURE", set="SetTexture", get="GetTexture", chk="tex"),
    dict(name="xf", ct="D3DMATRIX", n="NX_XF_COUNT", shape="mat", dom="xf", default="nx_identity()", blocks="", group="XFORM", set="SetTransform", get="GetTransform", mul="MultiplyTransform"),
    dict(name="material", ct="D3DMATERIAL9", n="1", shape="val0", default="D3DMATERIAL9()", blocks="", group="MATERIAL", set="SetMaterial", get="GetMaterial", arg="D3DMATERIAL9"),
    dict(name="clip", ct="NxPlane", n="NX_D3D_MAX_CLIP_PLANES", shape="vecat", default="NxPlane()", blocks="", group="CLIP", set="SetClipPlane", get="GetClipPlane"),
    dict(name="viewport", ct="D3DVIEWPORT9", n="1", shape="val0", default="@viewport", blocks="", group="VIEWPORT", set="SetViewport", get="GetViewport", arg="D3DVIEWPORT9"),
    dict(name="scissor", ct="RECT", n="1", shape="val0", default="@scissor", blocks="", group="SCISSOR", set="SetScissorRect", get="GetScissorRect", arg="RECT"),
    dict(name="stream", ct="NxStream", n="NX_D3D_MAX_STREAMS", shape="stream", default="NxStream()", blocks="", group="STREAMS", set="SetStreamSource", get="GetStreamSource"),
    dict(name="freq", ct="uint32_t", n="NX_D3D_MAX_STREAMS", shape="freq", default="1", blocks="V", group="STREAMS", set="SetStreamSourceFreq", get="GetStreamSourceFreq"),
    dict(name="ib", ct="IDirect3DIndexBuffer9*", n="1", shape="obj0", blocks="", group="INDEX", set="SetIndices", get="GetIndices", chk="obj"),
    dict(name="vdecl", ct="IDirect3DVertexDeclaration9*", n="1", shape="obj0", blocks="V", group="VDECL", set="SetVertexDeclaration", get="GetVertexDeclaration", chk="obj"),
    dict(name="vs", ct="IDirect3DVertexShader9*", n="1", shape="obj0", blocks="V", group="VSHADER", set="SetVertexShader", get="GetVertexShader", chk="obj"),
    dict(name="ps", ct="IDirect3DPixelShader9*", n="1", shape="obj0", blocks="P", group="PSHADER", set="SetPixelShader", get="GetPixelShader", chk="obj"),
    dict(name="vsf", ct="NxF4", n="NX_VS_CONST_F", shape="range", default="NxF4()", blocks="V", group="VSCONST", set="SetVertexShaderConstantF", get="GetVertexShaderConstantF", arg="float", w=4),
    dict(name="vsi", ct="NxI4", n="NX_CONST_I", shape="range", default="NxI4()", blocks="V", group="VSCONST", set="SetVertexShaderConstantI", get="GetVertexShaderConstantI", arg="int", w=4),
    dict(name="vsb", ct="BOOL", n="NX_CONST_B", shape="range", default="0", blocks="V", group="VSCONST", set="SetVertexShaderConstantB", get="GetVertexShaderConstantB", arg="BOOL", w=1),
    dict(name="psf", ct="NxF4", n="NX_PS_CONST_F", shape="range", default="NxF4()", blocks="P", group="PSCONST", set="SetPixelShaderConstantF", get="GetPixelShaderConstantF", arg="float", w=4),
    dict(name="psi", ct="NxI4", n="NX_CONST_I", shape="range", default="NxI4()", blocks="P", group="PSCONST", set="SetPixelShaderConstantI", get="GetPixelShaderConstantI", arg="int", w=4),
    dict(name="psb", ct="BOOL", n="NX_CONST_B", shape="range", default="0", blocks="P", group="PSCONST", set="SetPixelShaderConstantB", get="GetPixelShaderConstantB", arg="BOOL", w=1),
    dict(name="npatch", ct="float", n="1", shape="val0", default="0.0f", blocks="V", group="TESS", set="SetNPatchMode", get="GetNPatchMode", arg="float", retval=1),
    dict(name="curpal", ct="UINT", n="1", shape="val0", default="0", blocks="", group="PALETTE", set="SetCurrentTexturePalette", get="GetCurrentTexturePalette", arg="UINT"),
    # SetConvolutionMonoKernel (m1_misc.cpp): width / height of the kernel, hand-written setter (Ex only, no getter in the API); MS: recorded in D3DSBT_PIXELSTATE
    dict(name="convk", ct="uint32_t", n="2", shape="none", default="1", blocks="P", group="MISC"),
    # not in any block (MS: render targets, depth-stencil are device bindings; set/get hand-written in state_ext.cpp because of RT0 -> viewport/scissor reset)
    dict(name="rt", ct="IDirect3DSurface9*", n="NX_D3D_MAX_RTS", shape="none", blocks="-", group="RT"),
    dict(name="ds", ct="IDirect3DSurface9*", n="1", shape="none", blocks="-", group="DS"),
]
# methods defined by hand in state_ext.cpp (declared from the spec, forwarded by the generator): they need more than a table store
HAND = ["SetLight", "GetLight", "LightEnable", "GetLightEnable", "SetPaletteEntries", "GetPaletteEntries", "SetFVF", "GetFVF", "SetRenderTarget", "GetRenderTarget",
        "SetDepthStencilSurface", "GetDepthStencilSurface", "SetSoftwareVertexProcessing", "GetSoftwareVertexProcessing", "CreateStateBlock", "BeginStateBlock", "EndStateBlock"]
HAND_SB = ["Capture", "Apply"]
GROUPS_EXTRA = ["LIGHTS"]   # dirty groups of state that lives outside the slot families (sparse lights)
if __name__ == "__main__":
    import os, sys
    sys.dont_write_bytecode = True
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import state_gen
    state_gen.emit(sys.modules[__name__], sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
