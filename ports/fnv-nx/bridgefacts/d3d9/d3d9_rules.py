"""d3d9_rules.py: the hand table of RD1. Keys are "Method" or "Iface9.Method" (Iface9 = interface name without the
IDirect3D prefix, e.g. "Texture9.LockRect"); the more specific key wins. Values map parameter names to a spec built
by the helpers below; whatever a rule does not name is classified from its C type by d3d9_spec.py.
Facts follow the DirectX 9 SDK documentation; Wine's d3d9 was consulted for the cases the documentation leaves open."""

def F(fam, d=None): return {"kind": "flags", "flags": fam, "d": d}
def H(null=False, iface=None, d=None): return {"kind": "handle", "null": null, "iface": iface, "d": d}
def S(null=False, d=None, struct=None): return {"kind": "struct", "null": null, "d": d, "struct": struct}
def O(os, null=False, d=None): return {"kind": "ohandle", "os": os, "null": null, "d": d}
def V(size=4, d=None, in_when=None): return {"kind": "value", "size": size, "d": d, "in_when": in_when}
def B(elem, ln, off=None, null=False, d=None, canon=None):
    return {"kind": "buffer", "elem": elem, "len": ln, "off": off, "null": null, "d": d, "canon": canon}
def L(shape, unlock, match=(), **ex): return {"kind": "locked", "d": "out", "lock": dict(shape=shape, unlock=unlock, match=[list(m) for m in match], **ex)}

SHARED = O("HANDLE", null=True, d="inout")
GLOBAL = {"Usage": F("D3DUSAGE"), "usage": F("D3DUSAGE"), "FVF": F("D3DFVF"), "pSharedHandle": SHARED, "shared_handle": SHARED}
PRIM_UP = "verts(primitive_type,primitive_count)"
NOT_DISCARD = "eq(band({f},D3DLOCK_DISCARD),0)"
NOT_READONLY = "eq(band({f},D3DLOCK_READONLY),0)"


def lock_expr(flag):
    return {"on_lock": NOT_DISCARD.format(f=flag), "on_unlock": NOT_READONLY.format(f=flag)}


def pixels(w, h, d=None, fmt="this.fmt"):
    return dict(fmt=fmt, w=w, h=h, **({"d": d} if d else {}))


NULLABLE_SET = {k: H(True) for k in ("pStreamData", "pIndexData", "pTexture", "pShader", "pDecl", "pNewZStencil", "pRenderTarget")}
PP_IN = {"pPresentationParameters": S(d="inout"), "parameters": S(d="inout")}
RULES = {
    "QueryInterface": {"ppvObj": H(True, "$riid", "out")},
    "GetContainer": {"ppContainer": H(True, "$riid", "out")},
    "SetPrivateData": {"data": B(1, "data_size", canon={"when": "band(flags,D3DSPD_IUNKNOWN)", "as": "ohandle"}), "flags": F("D3DSPD")},
    "GetPrivateData": {"pData": B(1, "if(nonnull(pData),ok(*pSizeOfData@out),0)", null=True, d="out", canon={"when": "pd_iunknown(refguid)", "as": "ohandle"}), "pSizeOfData": V(4, "inout")},
    "GetFunction": {"pData": B(1, "if(nonnull(pData),ok(this.shader_bytes),0)", null=True, d="out"), "pSizeOfData": V(4, "inout", "nonnull(pData)")},
    "GetDeclaration": {"pDecl": B(8, "if(nonnull(pDecl),ok(this.decl_bytes),0)", null=True, d="out"), "pNumElements": V(4, "out")},
    "CreateVertexDeclaration": {"elements": B("D3DVERTEXELEMENT9", "decl_bytes(elements)")},
    "CreateVertexShader": {"byte_code": B(4, "sm_bytes(byte_code)")},
    "CreatePixelShader": {"byte_code": B(4, "sm_bytes(byte_code)")},
    "Clear": {"rects": B("D3DRECT", "rect_count*16", null=True), "flags": F("D3DCLEAR")},
    "Device9.Present": {"src_rect": S(True), "dst_rect": S(True), "dst_window_override": O("HWND", True),
                        "dirty_region": B(1, "32+u32(dirty_region,8)*16", null=True)},
    "SwapChain9.Present": {"src_rect": S(True), "dst_rect": S(True), "dst_window_override": O("HWND", True),
                           "dirty_region": B(1, "32+u32(dirty_region,8)*16", null=True), "flags": F("D3DPRESENT")},
    "PresentEx": {"src_rect": S(True), "dst_rect": S(True), "dst_window_override": O("HWND", True),
                  "dirty_region": B(1, "32+u32(dirty_region,8)*16", null=True), "flags": F("D3DPRESENT")},
    "SetClipPlane": {"plane": B(4, "16")}, "GetClipPlane": {"pPlane": B(4, "ok(16)", d="out")},
    "SetPaletteEntries": {"entries": B("PALETTEENTRY", "256*4")}, "GetPaletteEntries": {"pEntries": B("PALETTEENTRY", "ok(256*4)", d="out")},
    "SetGammaRamp": {"flags": F("D3DSGR")},
    "DrawPrimitiveUP": {"data": B(1, PRIM_UP + "*stride")},
    "DrawIndexedPrimitiveUP": {"index_data": B(1, PRIM_UP + "*index_size(index_format)"),
                               "data": B(1, "if(primitive_count,vertex_count*stride,0)", off="min_vertex_idx*stride")},
    "DrawRectPatch": {"segment_count": B(4, "16"), "patch_info": S(True)},
    "DrawTriPatch": {"segment_count": B(4, "12"), "patch_info": S(True)},
    "CheckResourceResidency": {"resources": B(4, "resource_count*4", d="in", canon={"when": "1", "as": "handle", "iface": "IDirect3DResource9"})},
    "SetConvolutionMonoKernel": {"rows": B(4, "width*4", null=True, d="in"), "columns": B(4, "height*4", null=True, d="in")},
    "SetVertexShaderConstantF": {"data": B(4, "count*16")}, "SetPixelShaderConstantF": {"data": B(4, "count*16")},
    "SetVertexShaderConstantI": {"data": B(4, "count*16")}, "SetPixelShaderConstantI": {"data": B(4, "count*16")},
    "SetVertexShaderConstantB": {"data": B(4, "count*4")}, "SetPixelShaderConstantB": {"data": B(4, "count*4")},
    "GetVertexShaderConstantF": {"pConstantData": B(4, "ok(Vector4fCount*16)", d="out")}, "GetPixelShaderConstantF": {"pConstantData": B(4, "ok(Vector4fCount*16)", d="out")},
    "GetVertexShaderConstantI": {"pConstantData": B(4, "ok(Vector4iCount*16)", d="out")}, "GetPixelShaderConstantI": {"pConstantData": B(4, "ok(Vector4iCount*16)", d="out")},
    "GetVertexShaderConstantB": {"pConstantData": B(4, "ok(BoolCount*4)", d="out")}, "GetPixelShaderConstantB": {"pConstantData": B(4, "ok(BoolCount*4)", d="out")},
    "ProcessVertices": {"Flags": F("D3DPV")}, "SetCursorPosition": {"Flags": F("D3DCURSOR")},
    "GetAdapterIdentifier": {"Flags": F("D3DENUM")}, "Issue": {"dwIssueFlags": F("D3DISSUE")},
    "GetData": {"pData": B(1, "ok0(min(dwSize,this.data_size))", null=True, d="out"), "dwGetDataFlags": F("D3DGETDATA")},
    "GetAdapterDisplayModeEx": {"mode": S(d="inout")}, "GetDisplayModeEx": {"mode": S(d="inout")},
    "GetFVF": {"pFVF": F("D3DFVF", "out")},
    "RegisterSoftwareDevice": {"pInitializeFunction": O("function pointer", d="in")},
    # IDirect3DShaderValidator9 (x1): the callback is a code address of the CALLER (guest code when the caller is the lifted game), the context an opaque value of the caller; both are passed through as values
    # (bridge rule of `ohandle`) and canonicalised by first appearance in a recording. file: the NUL-terminated source name D3DX passes (NULL = none).
    "ShaderValidator9.Begin": {"callback": O("guest callback", True, "in"), "context": O("guest pointer", True, "in"), "arg3": V(4)},
    "ShaderValidator9.Instruction": {"file": B(1, "str_bytes(file)", null=True, d="in"), "tokens": B(4, "token_count*4", null=True)},
    "CreateDevice": {"BehaviorFlags": F("D3DCREATE"), **PP_IN}, "CreateDeviceEx": {"flags": F("D3DCREATE"), **PP_IN, "mode": S(True, "inout")},
    "Reset": PP_IN, "ResetEx": {**PP_IN, "mode": S(True, "inout")}, "CreateAdditionalSwapChain": PP_IN,
    "AddDirtyRect": {"dirty_rect": S(True)}, "AddDirtyBox": {"dirty_box": S(True)},
    "UpdateSurface": {"src_rect": S(True), "dst_point": S(True)}, "ColorFill": {"rect": S(True)},
    "StretchRect": {"src_rect": S(True), "dst_rect": S(True)},
    "SetStreamSource": {"pStreamData": H(True)}, "SetIndices": {"pIndexData": H(True)}, "SetTexture": {"pTexture": H(True)},
    "SetVertexShader": {"pShader": H(True)}, "SetPixelShader": {"pShader": H(True)}, "SetVertexDeclaration": {"pDecl": H(True)},
    "SetDepthStencilSurface": {"pNewZStencil": H(True)}, "SetRenderTarget": {"pRenderTarget": H(True)},
    "GetDC": {"phdc": {"kind": "locked", "d": "out", "lock": dict(shape="dc", unlock="ReleaseDC", match=[], **pixels("this.width", "this.height"),
                                                                     on_lock="1", on_unlock="1")}},
    "ReleaseDC": {"hdc": O("HDC")},
    "VertexBuffer9.Lock": {"ppbData": L("linear", "Unlock", off="OffsetToLock", bytes="if(SizeToLock,SizeToLock,this.length-OffsetToLock)", **lock_expr("Flags")), "Flags": F("D3DLOCK")},
    "Surface9.LockRect": {"locked_rect": L("rect", "UnlockRect", rect="rect", **pixels("rect_w(rect,this.width)", "rect_h(rect,this.height)"), **lock_expr("flags")), "flags": F("D3DLOCK"), "rect": S(True)},
    "Texture9.LockRect": {"locked_rect": L("rect", "UnlockRect", [("level", "Level")], rect="rect", **pixels("rect_w(rect,lvl(this.width,level))", "rect_h(rect,lvl(this.height,level))"), **lock_expr("flags")), "flags": F("D3DLOCK"), "rect": S(True)},
    "CubeTexture9.LockRect": {"locked_rect": L("rect", "UnlockRect", [("face", "FaceType"), ("level", "Level")], rect="rect", **pixels("rect_w(rect,lvl(this.width,level))", "rect_h(rect,lvl(this.width,level))"), **lock_expr("flags")), "flags": F("D3DLOCK"), "rect": S(True)},
    "Volume9.LockBox": {"locked_box": L("box", "UnlockBox", box="box", **pixels("box_w(box,this.width)", "box_h(box,this.height)", "box_d(box,this.depth)"), **lock_expr("flags")), "flags": F("D3DLOCK"), "box": S(True)},
    "VolumeTexture9.LockBox": {"locked_box": L("box", "UnlockBox", [("level", "Level")], box="box", **pixels("box_w(box,lvl(this.width,level))", "box_h(box,lvl(this.height,level))", "box_d(box,lvl(this.depth,level))"), **lock_expr("flags")), "flags": F("D3DLOCK"), "box": S(True)},
}
RULES["IndexBuffer9.Lock"] = RULES["VertexBuffer9.Lock"]
NAME_FIX = {"GetFunction": ["pData"], "GetDeclaration": ["pDecl"], "GetLight": [None, "pLight"], "DrawIndexedPrimitive": ["PrimitiveType"]}
NEW = {"CreateDevice", "CreateDeviceEx", "CreateAdditionalSwapChain", "CreateTexture", "CreateVolumeTexture", "CreateCubeTexture",
       "CreateVertexBuffer", "CreateIndexBuffer", "CreateRenderTarget", "CreateDepthStencilSurface", "CreateOffscreenPlainSurface",
       "CreateRenderTargetEx", "CreateDepthStencilSurfaceEx", "CreateOffscreenPlainSurfaceEx", "CreateStateBlock", "EndStateBlock",
       "CreateVertexDeclaration", "CreateVertexShader", "CreatePixelShader", "CreateQuery"}
ADDREF = {"QueryInterface", "GetContainer", "GetDevice", "GetDirect3D", "GetBackBuffer", "GetRenderTarget", "GetDepthStencilSurface",
          "GetSwapChain", "GetTexture", "GetStreamSource", "GetIndices", "GetVertexDeclaration", "GetVertexShader", "GetPixelShader",
          "GetSurfaceLevel", "GetCubeMapSurface", "GetVolumeLevel"}
