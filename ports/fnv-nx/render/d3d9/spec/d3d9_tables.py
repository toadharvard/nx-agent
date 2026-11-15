"""d3d9_tables.py: static tables the length and class rules refer to. Everything here is a fact about D3D9 (the SDK
documentation, cross-checked against Wine's d3d9 / wined3d sources for the shader token walk), not a measurement."""

# D3DFORMAT -> [block width px, block height px, bytes per block]; rowbytes(f,w)=ceil(w/bw)*bytes, rows(f,h)=ceil(h/bh).
# bytes 0 = not an image format (no lockable pixel rows: UNKNOWN, VERTEXDATA, BINARYBUFFER, MULTI2_ARGB8 multi-element).
_B = {8: ["R3G3B2", "A8", "P8", "L8", "A4L4", "S8_LOCKABLE"], 16: ["R5G6B5", "X1R5G5B5", "A1R5G5B5", "A4R4G4B4", "A8R3G3B2", "X4R4G4B4",
      "A8P8", "A8L8", "V8U8", "L6V5U5", "D16_LOCKABLE", "D15S1", "D16", "L16", "INDEX16", "R16F", "CxV8U8"],
      24: ["R8G8B8"], 32: ["A8R8G8B8", "X8R8G8B8", "A2B10G10R10", "A8B8G8R8", "X8B8G8R8", "G16R16", "A2R10G10B10", "X8L8V8U8", "Q8W8V8U8",
      "V16U16", "A2W10V10U10", "D32", "D24S8", "D24X8", "D24X4S4", "D32F_LOCKABLE", "D24FS8", "D32_LOCKABLE", "INDEX32", "G16R16F", "R32F",
      "A2B10G10R10_XR_BIAS"], 64: ["A16B16G16R16", "Q16W16V16U16", "A16B16G16R16F", "G32R32F"], 128: ["A32B32G32R32F"]}
FORMAT = {"D3DFMT_" + n: [1, 1, bits // 8] for bits, ns in _B.items() for n in ns}
FORMAT.update({"D3DFMT_UNKNOWN": [1, 1, 0], "D3DFMT_VERTEXDATA": [1, 1, 0], "D3DFMT_BINARYBUFFER": [1, 1, 0], "D3DFMT_MULTI2_ARGB8": [1, 1, 0],
               "D3DFMT_A1": [8, 1, 1], "D3DFMT_UYVY": [2, 1, 4], "D3DFMT_YUY2": [2, 1, 4], "D3DFMT_R8G8_B8G8": [2, 1, 4],
               "D3DFMT_G8R8_G8B8": [2, 1, 4], "D3DFMT_DXT1": [4, 4, 8], "D3DFMT_DXT2": [4, 4, 16], "D3DFMT_DXT3": [4, 4, 16],
               "D3DFMT_DXT4": [4, 4, 16], "D3DFMT_DXT5": [4, 4, 16]})
# D3DPRIMITIVETYPE -> [mul, add]: verts(type,count) = count*mul+add, and 0 when count == 0 (the runtime returns D3D_OK without reading)
PRIMITIVE = {"D3DPT_POINTLIST": [1, 0], "D3DPT_LINELIST": [2, 0], "D3DPT_LINESTRIP": [1, 1], "D3DPT_TRIANGLELIST": [3, 0],
             "D3DPT_TRIANGLESTRIP": [1, 2], "D3DPT_TRIANGLEFAN": [1, 2]}
# D3DQUERYTYPE -> data type of GetData and the class of what it returns (strings are justifications)
_P_GPU = "result of GPU execution or GPU counters, not a function of the call stream"
_P_TIM = "GPU/driver utilisation percentages: wall-clock timing statistics"
QUERY = {
    "VCACHE": ("D3DDEVINFO_VCACHE", "env", "post-transform vertex cache description of the GPU/driver (fixed per device model)"),
    "RESOURCEMANAGER": ("D3DDEVINFO_D3DRESOURCEMANAGER", "pix", "driver resource-manager statistics: depend on memory pressure and GPU timing"),
    "VERTEXSTATS": ("D3DDEVINFO_D3DVERTEXSTATS", "pix", "triangle counts after clipping: depend on the driver's clipper, not on the stream"),
    "EVENT": ("BOOL", "pix", "TRUE once the GPU consumed the stream: depends on GPU timing"),
    "OCCLUSION": ("DWORD", "pix", "samples passed: depends on rendered pixels"),
    "TIMESTAMP": ("UINT64", "pix", "GPU clock value"),
    "TIMESTAMPDISJOINT": ("BOOL", "pix", "GPU clock discontinuity: GPU timing"),
    "TIMESTAMPFREQ": ("UINT64", "env", "GPU clock frequency: a property of the device"),
    "PIPELINETIMINGS": ("D3DDEVINFO_D3D9PIPELINETIMINGS", "pix", _P_TIM),
    "INTERFACETIMINGS": ("D3DDEVINFO_D3D9INTERFACETIMINGS", "pix", _P_TIM),
    "VERTEXTIMINGS": ("D3DDEVINFO_D3D9STAGETIMINGS", "pix", _P_TIM),
    "PIXELTIMINGS": ("D3DDEVINFO_D3D9STAGETIMINGS", "pix", _P_TIM),
    "BANDWIDTHTIMINGS": ("D3DDEVINFO_D3D9BANDWIDTHTIMINGS", "pix", _P_TIM),
    "CACHEUTILIZATION": ("D3DDEVINFO_D3D9CACHEUTILIZATION", "pix", "cache hit rates measured during execution"),
    "MEMORYPRESSURE": ("D3DMEMORYPRESSURE", "pix", "driver memory-eviction counters"),
}
# Shader token walk (rule sm_bytes). First DWORD = version token (0xFFFE.... vertex, 0xFFFF.... pixel; major = bits 8..15).
# Then tokens up to and including END (0x0000FFFF). COMMENT token (low 16 bits 0xFFFE): size in DWORDs = bits 16..30, skipped.
# Other instruction: major >= 2 -> operand DWORD count = bits 24..27 of the opcode token; major < 2 -> params[opcode] below.
# ps_1_4 TEXCOORD and TEX have 2 operands (ps14), earlier pixel versions 1. Source: SDK docs "Shader Token Formats"; Wine wined3d shader_sm1.c
SM1_PARAMS = {"NOP": 0, "MOV": 2, "ADD": 3, "SUB": 3, "MAD": 4, "MUL": 3, "RCP": 2, "RSQ": 2, "DP3": 3, "DP4": 3, "MIN": 3, "MAX": 3, "SLT": 3,
              "SGE": 3, "EXP": 2, "LOG": 2, "LIT": 2, "DST": 3, "LRP": 4, "FRC": 2, "M4x4": 3, "M4x3": 3, "M3x4": 3, "M3x3": 3, "M3x2": 3,
              "EXPP": 2, "LOGP": 2, "MOVA": 2, "DEF": 5, "TEXCOORD": 1, "TEXKILL": 1, "TEX": 1, "TEXBEM": 2, "TEXBEML": 2, "TEXREG2AR": 2,
              "TEXREG2GB": 2, "TEXREG2RGB": 2, "TEXM3x2PAD": 2, "TEXM3x2TEX": 2, "TEXM3x3PAD": 2, "TEXM3x3TEX": 2, "TEXM3x3DIFF": 2,
              "TEXM3x3SPEC": 3, "TEXM3x3VSPEC": 2, "TEXDP3TEX": 2, "TEXM3x2DEPTH": 2, "TEXDP3": 2, "TEXM3x3": 2, "TEXDEPTH": 1, "CND": 4,
              "CMP": 4, "BEM": 3, "PHASE": 0}
SM_WALK = {"version_token": {"vertex_high": 0xFFFE, "pixel_high": 0xFFFF, "major_shift": 8, "major_mask": 0xFF},
           "comment": {"opcode": "D3DSIO_COMMENT", "size_shift": 16, "size_mask": 0x7FFF}, "end": "D3DSIO_END",
           "length_ge_2": {"shift": 24, "mask": 0xF}, "params_lt_2": {"D3DSIO_" + k: v for k, v in sorted(SM1_PARAMS.items())},
           "ps_1_4": {"D3DSIO_TEXCOORD": 2, "D3DSIO_TEX": 2}}
# Class rules referenced by `"cls": "by:<rule>"` (evaluated by the generator/comparator on object state, not by measuring)
CLS_RULES = {
    "taint": "per-resource-level bit pix_taint. Content read back through a lock (read of Lock/LockRect/LockBox/GetDC) is pix iff "
             "the bit is set for that level, else det. Initially set for: objects created with D3DUSAGE_RENDERTARGET or "
             "D3DUSAGE_DEPTHSTENCIL, CreateRenderTarget/CreateDepthStencilSurface surfaces, swap-chain back buffers, the auto "
             "depth-stencil, levels >= 1 of D3DUSAGE_AUTOGENMIPMAP textures. Changed by methods listed in tables.taint "
             "(effects[] of the method). Lock bytes of a non-tainted level are the application's own earlier writes (det).",
    "query_type": "per-query class from tables.query_type[type of the CreateQuery that made `this`]",
}
# Methods with effects on resource contents the application cannot see through a recorded output. Effect vocabulary:
#   pix(X)      the contents of X become pix_taint (X: rendertargets | depthstencil | a parameter name | this)
#   copy(D,S)   pix_taint(D) = pix_taint(S) (pure copy of bytes)
TAINT = [("Clear", "pix(rendertargets),pix(depthstencil)", "GPU clear of the bound targets"),
         ("DrawPrimitive", "pix(rendertargets),pix(depthstencil)", "GPU rasterisation"), ("DrawIndexedPrimitive", "pix(rendertargets),pix(depthstencil)", "GPU rasterisation"),
         ("DrawPrimitiveUP", "pix(rendertargets),pix(depthstencil)", "GPU rasterisation"), ("DrawIndexedPrimitiveUP", "pix(rendertargets),pix(depthstencil)", "GPU rasterisation"),
         ("DrawRectPatch", "pix(rendertargets),pix(depthstencil)", "GPU rasterisation"), ("DrawTriPatch", "pix(rendertargets),pix(depthstencil)", "GPU rasterisation"),
         ("ProcessVertices", "pix(pDestBuffer)", "vertex transform output of the vertex pipeline (float arithmetic of the driver)"),
         ("ColorFill", "pix(surface)", "GPU fill with format conversion on render targets; surface contents then depend on the GPU path"),
         ("StretchRect", "pix(dst_surface)", "GPU blit with filtering/format conversion"),
         ("GetRenderTargetData", "pix(pDestSurface)", "readback of a render target"),
         ("GetFrontBufferData", "pix(pDestSurface)", "readback of the displayed image"),
         ("UpdateSurface", "copy(dst_surface,src_surface)", "byte copy sysmem to default pool"),
         ("UpdateTexture", "copy(pDestinationTexture,pSourceTexture)", "byte copy of dirty levels"),
         ("GenerateMipSubLevels", "pix(this)", "GPU downfilter: result is implementation specific"),
         ("Present", "pix(rendertargets)", "back buffer contents are undefined after a flip"),
         ("PresentEx", "pix(rendertargets)", "back buffer contents are undefined after a flip")]
