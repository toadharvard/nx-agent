#!/usr/bin/env python3
"""method_spec.py: parse shim/include/d3d9.h into the D3D9 method universe (one row per interface x vtable slot) and
classify every parameter (direction in/out/inout; kind value|enum|flags|handle|in-struct|out-struct|in-buffer|
out-buffer|locked-memory). '?' marks a classification that is a guess from the type text.

    python3 -I method_spec.py [shim/include/d3d9.h] > method_universe.tsv   (also checks slot counts on stderr)
Columns: interface, slot, method, ret, nparams, params; a param is  name:dir:kind[=length rule]:C type
"""
import re, sys

HDR = sys.argv[1] if len(sys.argv) > 1 else "shim/include/d3d9.h"
EXPECT = {"IDirect3D9": 17, "IDirect3DDevice9": 119, "IDirect3DSwapChain9": 10, "IDirect3DResource9": 11,
          "IDirect3DSurface9": 17, "IDirect3DVolume9": 11, "IDirect3DVertexBuffer9": 14, "IDirect3DIndexBuffer9": 14,
          "IDirect3DBaseTexture9": 17, "IDirect3DTexture9": 22, "IDirect3DCubeTexture9": 22,
          "IDirect3DVolumeTexture9": 22, "IDirect3DVertexDeclaration9": 5, "IDirect3DVertexShader9": 5,
          "IDirect3DPixelShader9": 5, "IDirect3DStateBlock9": 6, "IDirect3DQuery9": 8, "IDirect3D9Ex": 22,
          "IDirect3DSwapChain9Ex": 13, "IDirect3DDevice9Ex": 134}   # real SDK slot counts incl. IUnknown
IUNK = [("HRESULT", "QueryInterface", "REFIID riid, void** ppvObj"), ("ULONG", "AddRef", ""), ("ULONG", "Release", "")]
ENUM_T = {"D3DFORMAT", "D3DPOOL", "D3DDEVTYPE", "D3DRESOURCETYPE", "D3DMULTISAMPLE_TYPE", "D3DPRIMITIVETYPE",
          "D3DBACKBUFFER_TYPE", "D3DTEXTUREFILTERTYPE", "D3DCUBEMAP_FACES", "D3DTRANSFORMSTATETYPE",
          "D3DRENDERSTATETYPE", "D3DTEXTURESTAGESTATETYPE", "D3DSAMPLERSTATETYPE", "D3DSTATEBLOCKTYPE",
          "D3DQUERYTYPE", "D3DCOMPOSERECTSOP", "D3DDISPLAYROTATION"}
FLAG_N = re.compile(r"^(flags?|usage|dw\w*flags?|behaviorflags|dwissueflags|dwgetdataflags|FVF)$", re.I)

# (method) -> {param: (dir, kind, length rule or note)}; applied to every interface that has the method.
H = {
 "SetPrivateData": {"data": ("in", "in-buffer", "data_size bytes"), "data_size": ("in", "value", ""), "flags": ("in", "flags", "D3DSPD_IUNKNOWN: data is an IUnknown* (refcounted)")},
 "GetPrivateData": {"pData": ("out", "out-buffer", "*pSizeOfData bytes"), "pSizeOfData": ("inout", "value", "")},
 "GetFunction": {"a0": ("out", "out-buffer", "*pSizeOfData bytes (NULL: size query)"), "pSizeOfData": ("inout", "value", "")},
 "GetDeclaration": {"a0": ("out", "out-buffer", "*pNumElements x 8 bytes (NULL: count query)"), "pNumElements": ("inout", "value", "")},
 "CreateVertexDeclaration": {"elements": ("in", "in-buffer", "D3DVERTEXELEMENT9[] up to D3DDECL_END (8 bytes each)")},
 "CreateVertexShader": {"byte_code": ("in", "in-buffer", "DWORD tokens: version token .. 0x0000FFFF end token")},
 "CreatePixelShader": {"byte_code": ("in", "in-buffer", "DWORD tokens: version token .. 0x0000FFFF end token")},
 "Clear": {"rects": ("in", "in-buffer", "rect_count x 16 bytes (NULL ok)")},
 "Present": {"dirty_region": ("in", "in-buffer", "RGNDATA: header.dwSize + nCount x 16 (?)"), "flags": ("in", "flags", "")},
 "SetClipPlane": {"plane": ("in", "in-buffer", "4 floats")}, "GetClipPlane": {"pPlane": ("out", "out-buffer", "4 floats")},
 "SetPaletteEntries": {"entries": ("in", "in-buffer", "256 x PALETTEENTRY (4 bytes)")},
 "GetPaletteEntries": {"pEntries": ("out", "out-buffer", "256 x PALETTEENTRY")},
 "SetGammaRamp": {"ramp": ("in", "in-struct", "D3DGAMMARAMP 3x256 WORD")},
 "GetGammaRamp": {"pRamp": ("out", "out-struct", "D3DGAMMARAMP")},
 "DrawPrimitiveUP": {"data": ("in", "in-buffer", "vertices(primitive_type, primitive_count) x stride")},
 "DrawIndexedPrimitiveUP": {"index_data": ("in", "in-buffer", "vertices(primitive_type, primitive_count) x (2|4 by index_format)"),
                            "data": ("in", "in-buffer", "(min_vertex_idx + vertex_count) x stride")},
 "DrawRectPatch": {"segment_count": ("in", "in-buffer", "4 floats (?)"), "patch_info": ("in", "in-struct", "NULL: reuse patch handle")},
 "DrawTriPatch": {"segment_count": ("in", "in-buffer", "3 floats (?)"), "patch_info": ("in", "in-struct", "NULL: reuse patch handle")},
 "GetData": {"pData": ("out", "out-buffer", "dwSize bytes; HRESULT S_FALSE = not ready")},
 "Lock": {"ppbData": ("out", "locked-memory", "SizeToLock bytes (0: to end) at OffsetToLock; guest writes it before Unlock")},
 "LockRect": {"locked_rect": ("out", "locked-memory", "D3DLOCKED_RECT{Pitch,pBits}; bytes = rect rows x pitch; guest writes/reads"),
              "rect": ("in", "in-struct", "NULL = whole level")},
 "LockBox": {"locked_box": ("out", "locked-memory", "D3DLOCKED_BOX{RowPitch,SlicePitch,pBits}; bytes = box depth x slice"),
             "box": ("in", "in-struct", "NULL = whole level")},
 "CheckResourceResidency": {"resources": ("in", "in-buffer", "resource_count x pointer")},
 "SetConvolutionMonoKernel": {"rows": ("in", "in-buffer", "width floats"), "columns": ("in", "in-buffer", "height floats")},
 "GetContainer": {"ppContainer": ("out", "handle", "interface chosen by riid; AddRef'd")},
 "QueryInterface": {"ppvObj": ("out", "handle", "interface chosen by riid; AddRef'd")},
 "RegisterSoftwareDevice": {"pInitializeFunction": ("in", "value", "function pointer (?)")},
 "Reset": {"pPresentationParameters": ("inout", "in-struct", "runtime resolves 0 fields in place")},
 "CreateDevice": {"pPresentationParameters": ("inout", "in-struct", "runtime resolves 0 fields in place")},
 "CreateAdditionalSwapChain": {"pPresentationParameters": ("inout", "in-struct", "runtime resolves 0 fields in place")},
 "CreateDeviceEx": {"parameters": ("inout", "in-struct", ""), "mode": ("in", "in-struct", "NULL ok")},
 "ResetEx": {"parameters": ("inout", "in-struct", ""), "mode": ("in", "in-struct", "NULL ok")},
}
CONST_N = {"SetVertexShaderConstantF": 16, "SetPixelShaderConstantF": 16, "SetVertexShaderConstantI": 16,
           "SetPixelShaderConstantI": 16, "SetVertexShaderConstantB": 4, "SetPixelShaderConstantB": 4}


def split_args(a):
    a = a.strip()
    if not a or a == "void":
        return []
    parts, depth, cur = [], 0, ""
    for ch in a:
        if ch == "," and depth == 0:
            parts.append(cur.strip()); cur = ""; continue
        depth += ch in "(<"; depth -= ch in ")>"; cur += ch
    parts.append(cur.strip())
    out = []
    for i, p in enumerate(parts):
        m = re.match(r"(.*?)(\w+)\s*$", p)
        t, n = (m.group(1).strip(), m.group(2)) if m and m.group(1).strip() and m.group(1).strip() not in ("const", "struct") else (p, f"a{i}")
        out.append((t, n))
    return out


def classify(meth, t, n):
    ty = t.replace("struct ", "").strip()
    base = ty.replace("const", "").replace("*", "").strip()
    stars, const = ty.count("*"), "const" in ty.split("*")[0]
    if meth in H and n in H[meth]:
        d, k, note = H[meth][n]; return d, k, note
    if meth in CONST_N:
        if n == "data": return "in", "in-buffer", f"count x {CONST_N[meth]} bytes"
    if meth.startswith("Get") and meth.endswith("Constant" + meth[-1]) and n.startswith("pConstantData"):
        return "out", "out-buffer", "count x %d bytes" % (4 if meth[-1] == "B" else 16)
    if stars >= 2 and "IDirect3D" in base: return "out", "handle", "new/AddRef'd object"
    if stars == 1 and "IDirect3D" in base: return "in", "handle", "NULL ok" if "Set" in meth else ""
    if stars >= 2: return "out", "locked-memory?", ""
    if base == "HANDLE" and stars: return "inout", "value", "shared handle (?)"
    if base == "REFGUID" or base == "REFIID": return "in", "in-struct", "16-byte GUID"
    if stars == 1 and const: return "in", "in-struct", ""
    if stars == 1:
        if meth.startswith("Set") or meth.startswith("Create"): return "inout?", "in-struct?", ""
        return "out", ("out-struct" if base.startswith("D3D") or base in ("RECT", "LUID", "PALETTEENTRY") else "value"), ""
    if base in ENUM_T: return "in", "enum", base
    if FLAG_N.match(n) or n in ("Usage", "Flags"): return "in", "flags", ""
    return "in", "value", ""


def parse(path=HDR):
    text = open(path).read()
    ifs = {}
    for m in re.finditer(r"struct (IDirect3D\w+)\s*:\s*public (\w+)\s*\{(.*?)\n\};", text, re.S):
        name, base, body = m.groups()
        ms = [(int(mm.group(1)), mm.group(2).strip(), mm.group(3), " ".join(mm.group(4).split()))
              for mm in re.finditer(r"/\*\s*(\d+)\s*\*/\s*FNVNX_D3D_METHOD\(([^,]+),\s*(\w+)\)\((.*?)\)\s*=\s*0;", body, re.S)]
        ifs[name] = (base, [(s, r, n, a) for s, r, n, a in ms])
    return ifs


def universe(ifs):
    rows = []
    def chain(n):
        if n not in ifs: return [(i, r, nm, a) for i, (r, nm, a) in enumerate(IUNK)]
        b, ms = ifs[n]
        return chain(b) + ms
    for name in ifs:
        for slot, ret, meth, args in chain(name):
            ps = []
            for t, n in split_args(args):
                d, k, note = classify(meth, t, n)
                ps.append(f"{n}:{d}:{k}{'=' + note if note else ''}:{t}")
            rows.append((name, slot, meth, ret, len(ps), "; ".join(ps)))
    return rows


if __name__ == "__main__":
    ifs = parse()
    rows = universe(ifs)
    cnt = {}
    for r in rows: cnt[r[0]] = cnt.get(r[0], 0) + 1
    for i, e in EXPECT.items():
        slots = [r[1] for r in rows if r[0] == i]
        ok = cnt.get(i) == e and slots == list(range(e))
        print(f"{'ok  ' if ok else 'MISMATCH'} {i}: header {cnt.get(i)} expected {e}", file=sys.stderr)
    print("interface\tslot\tmethod\tret\tnparams\tparams (name:dir:kind[=length]:type)")
    for r in rows: print("\t".join(map(str, r)))
