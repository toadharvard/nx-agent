#!/usr/bin/env python3
"""Generate null_d3d9.cpp: a headless IDirect3D9 / IDirect3DDevice9 (and every resource interface) from
shim/include/d3d9.h, so the engine can run without a renderer (S6 replaces it).

    python3 -I toolchain/game/gen_null_d3d9.py shim/include/d3d9.h toolchain/game/null_d3d9.cpp

Rules: every method returns S_OK / 0 / a plausible constant; out-parameters of interface type get a new null object
of that interface (carrying width/height/format/size from the Create* arguments); Get*Desc fill from those; Lock*
hand out a per-object scratch buffer; caps, adapter modes and display mode describe a 1280x720 X8R8G8B8 SM3 device.
"""
import re
import sys

src, out = sys.argv[1], sys.argv[2]
text = open(src).read()

ifaces = {}   # name -> (base, [(ret, name, args)])
for m in re.finditer(r"struct (IDirect3D\w+)\s*:\s*public (\w+)\s*\{(.*?)\n\};", text, re.S):
    name, base, body = m.groups()
    meths = []
    for mm in re.finditer(r"FNVNX_D3D_METHOD\(([^,]+),\s*(\w+)\)\((.*?)\)\s*=\s*0;", body, re.S):
        meths.append((mm.group(1).strip(), mm.group(2), " ".join(mm.group(3).split())))
    ifaces[name] = (base, meths)


def chain(n):
    out = []
    while n in ifaces:
        base, meths = ifaces[n]
        out = meths + out
        n = base
    return out


def split_args(a):
    if not a.strip() or a.strip() == "void":
        return []
    parts, depth, cur = [], 0, ""
    for ch in a:
        if ch == "," and depth == 0:
            parts.append(cur.strip()); cur = ""
            continue
        depth += ch in "(<"
        depth -= ch in ")>"
        cur += ch
    parts.append(cur.strip())
    res = []
    for i, p in enumerate(parts):
        mm = re.match(r"(.*?)(\w+)\s*$", p)
        typ, nm = (mm.group(1).strip(), mm.group(2)) if mm and mm.group(1).strip() else (p, None)
        if nm is None or typ in ("", "const", "struct"):
            typ, nm = p, f"a{i}"
        res.append((typ, nm))
    return res


IFACE_OUT = re.compile(r"(?:struct\s+)?(IDirect3D\w+)\s*\*\s*\*$")
FIELDS = {"Width": "w", "Height": "h", "Format": "fmt", "Length": "size", "Levels": "levels", "Usage": "usage",
          "Pool": "pool", "EdgeLength": "w", "Depth": "d", "FVF": "fvf"}
DESC_FILL = {
    "D3DSURFACE_DESC": "p->Format = (D3DFORMAT)fmt; p->Type = D3DRTYPE_SURFACE; p->Usage = usage; p->Pool = (D3DPOOL)pool; p->Width = w; p->Height = h;",
    "D3DVERTEXBUFFER_DESC": "p->Format = D3DFMT_VERTEXDATA; p->Type = D3DRTYPE_VERTEXBUFFER; p->Usage = usage; p->Pool = (D3DPOOL)pool; p->Size = size; p->FVF = fvf;",
    "D3DINDEXBUFFER_DESC": "p->Format = (D3DFORMAT)fmt; p->Type = D3DRTYPE_INDEXBUFFER; p->Usage = usage; p->Pool = (D3DPOOL)pool; p->Size = size;",
    "D3DVOLUME_DESC": "p->Format = (D3DFORMAT)fmt; p->Type = D3DRTYPE_VOLUME; p->Usage = usage; p->Pool = (D3DPOOL)pool; p->Width = w; p->Height = h; p->Depth = d;",
}
RTYPE = {"IDirect3DSurface9": "D3DRTYPE_SURFACE", "IDirect3DTexture9": "D3DRTYPE_TEXTURE",
         "IDirect3DCubeTexture9": "D3DRTYPE_CUBETEXTURE", "IDirect3DVolumeTexture9": "D3DRTYPE_VOLUMETEXTURE",
         "IDirect3DVertexBuffer9": "D3DRTYPE_VERTEXBUFFER", "IDirect3DIndexBuffer9": "D3DRTYPE_INDEXBUFFER"}


def body(iface, ret, name, args):
    lines = []
    outs = [(t, n) for t, n in args if IFACE_OUT.search(t)]
    setters = [f"o->{FIELDS[n]} = (uint32_t){n};" for t, n in args if n in FIELDS and "*" not in t]
    for t, n in outs:
        cls = "Null_" + IFACE_OUT.search(t).group(1)
        if cls[5:] not in ifaces:
            lines.append(f"if ({n}) *{n} = nullptr;")
            continue
        lines.append(f"if ({n}) {{ auto *o = new {cls}(); o->inherit(this); {' '.join(setters)} *{n} = o; }}")
    for t, n in args:
        base = t.replace("const", "").replace("*", "").strip()
        if t.endswith("*") and "const" not in t and base in DESC_FILL and name.startswith("Get"):
            lines.append(f"if ({n}) {{ auto *p = {n}; memset(p, 0, sizeof *p); {DESC_FILL[base]} }}")
    if name in ("LockRect",):
        lr = [n for t, n in args if "D3DLOCKED_RECT" in t][0]
        lines.append(f"if ({lr}) {{ {lr}->pBits = scratch(); {lr}->Pitch = (INT)(pitch()); }}")
    if name == "LockBox":
        lb = [n for t, n in args if "D3DLOCKED_BOX" in t][0]
        lines.append(f"if ({lb}) {{ {lb}->pBits = scratch(); {lb}->RowPitch = (INT)pitch(); {lb}->SlicePitch = (INT)(pitch() * (h ? h : 1)); }}")
    if name == "Lock":
        lines.append("if (ppbData) *ppbData = (char *)scratch() + OffsetToLock;")
    special = {
        ("IDirect3D9", "GetAdapterCount"): "return 1;",
        ("IDirect3D9", "GetAdapterModeCount"): "return 1;",
        ("IDirect3D9", "EnumAdapterModes"): "if (pMode) null_mode(pMode); return Mode == 0 ? S_OK : D3DERR_INVALIDCALL;",
        ("IDirect3D9", "GetAdapterDisplayMode"): "if (pMode) null_mode(pMode); return S_OK;",
        ("IDirect3D9", "GetDeviceCaps"): "if (pCaps) null_caps(pCaps); return S_OK;",
        ("IDirect3D9", "GetAdapterIdentifier"): "if (pIdentifier) { memset(pIdentifier, 0, sizeof *pIdentifier); strcpy(pIdentifier->Driver, \"fnvnx-null\"); strcpy(pIdentifier->Description, \"fnv-nx null D3D9 device\"); pIdentifier->VendorId = 0x10de; pIdentifier->DeviceId = 0x0391; } return S_OK;",
        ("IDirect3D9", "CheckDeviceMultiSampleType"): "if (pQualityLevels) *pQualityLevels = 1; return S_OK;",
        ("IDirect3D9", "GetAdapterMonitor"): "return (HMONITOR)1;",
        ("IDirect3DDevice9", "GetDeviceCaps"): "if (pCaps) null_caps(pCaps); return S_OK;",
        ("IDirect3DDevice9", "GetDisplayMode"): "if (pMode) null_mode(pMode); return S_OK;",
        ("IDirect3DSwapChain9", "GetDisplayMode"): "if (pMode) null_mode(pMode); return S_OK;",
        ("IDirect3DDevice9", "GetAvailableTextureMem"): "return 512u << 20;",
        ("IDirect3DDevice9", "GetNumberOfSwapChains"): "return 1;",
        ("IDirect3DBaseTexture9", "GetLevelCount"): "return levels ? levels : 1;",
        ("IDirect3DQuery9", "GetDataSize"): "return 8;",
        ("IDirect3DQuery9", "GetData"): "if (pData) memset(pData, 0, dwSize); return S_OK;",
    }
    key = None
    for k in special:
        if k[1] == name and (k[0] == iface or k[0] in bases(iface)):
            key = k
            break
    if key:
        lines.append(special[key])
        return lines
    if name == "GetType" and iface in RTYPE:
        lines.append(f"return {RTYPE[iface]};")
    elif name == "GetType" and iface == "IDirect3DQuery9":
        lines.append("return D3DQUERYTYPE_EVENT;")
    elif ret == "HRESULT":
        lines.append("return S_OK;")
    elif ret == "void":
        pass
    elif ret == "BOOL":
        lines.append("return TRUE;")
    elif ret == "float" or ret == "FLOAT":
        lines.append("return 0.0f;")
    else:
        lines.append(f"return ({ret})0;")
    return lines


def bases(n):
    out = []
    while n in ifaces:
        n = ifaces[n][0]
        out.append(n)
    return out


o = ["/* null_d3d9.cpp - GENERATED by toolchain/game/gen_null_d3d9.py from shim/include/d3d9.h; do not edit.",
     " * Headless D3D9 for the guest boot (S6 replaces it): every call succeeds, nothing is drawn. */",
     "#include <string.h>", "#include <stdint.h>", "#include <stdlib.h>", "#include <d3d9.h>", "",
     "static void null_mode(D3DDISPLAYMODE *m) { m->Width = 1280; m->Height = 720; m->RefreshRate = 60; m->Format = D3DFMT_X8R8G8B8; }",
     "static void null_caps(D3DCAPS9 *c) {",
     "    memset(c, 0, sizeof *c);",
     "    c->DeviceType = D3DDEVTYPE_HAL; c->MaxTextureWidth = c->MaxTextureHeight = 4096; c->MaxVolumeExtent = 256;",
     "    c->MaxTextureRepeat = 8192; c->MaxTextureAspectRatio = 4096; c->MaxAnisotropy = 16; c->MaxSimultaneousTextures = 8;",
     "    c->MaxTextureBlendStages = 8; c->MaxActiveLights = 8; c->MaxUserClipPlanes = 6; c->MaxVertexBlendMatrices = 4;",
     "    c->MaxStreams = 16; c->MaxStreamStride = 255; c->MaxPrimitiveCount = 0xFFFFF; c->MaxVertexIndex = 0xFFFFFF;",
     "    c->VertexShaderVersion = 0xFFFE0300; c->PixelShaderVersion = 0xFFFF0300; c->MaxVertexShaderConst = 256;",
     "    c->MaxPointSize = 64.0f; c->NumSimultaneousRTs = 4; c->MaxVShaderInstructionsExecuted = 65535;",
     "    c->MaxPShaderInstructionsExecuted = 65535; c->MaxVertexShader30InstructionSlots = 32768;",
     "    c->MaxPixelShader30InstructionSlots = 32768; c->PixelShader1xMaxValue = 65504.0f;",
     "    c->DevCaps = 0xFFFFFFFF; c->Caps2 = 0xFFFFFFFF; c->TextureCaps = 0xFFFFFFFF & ~0x2u /* no POW2 restriction */;",
     "    c->RasterCaps = 0xFFFFFFFF; c->PrimitiveMiscCaps = 0xFFFFFFFF; c->ZCmpCaps = c->AlphaCmpCaps = 0xFF;",
     "    c->SrcBlendCaps = c->DestBlendCaps = 0xFFFF; c->ShadeCaps = 0xFFFFFFFF; c->TextureFilterCaps = 0xFFFFFFFF;",
     "    c->StencilCaps = 0xFF; c->DeclTypes = 0x3FF; c->VertexProcessingCaps = 0xFFFF;",
     "}",
     "",
     "struct NullBase {",
     "    volatile long refs = 1; uint32_t w = 0, h = 0, d = 0, fmt = 0, size = 0, levels = 0, usage = 0, pool = 0, fvf = 0;",
     "    void *buf = nullptr; IDirect3DDevice9 *dev = nullptr;",
     "    void inherit(void *) {}",
     "    uint32_t pitch() const { return (w ? w : 256) * 4; }",
     "    void *scratch() { if (!buf) { size_t n = size ? size : (size_t)pitch() * (h ? h : 256) * (d ? d : 1); if (n < 4096) n = 4096; buf = calloc(1, n); } return buf; }",
     "    virtual ~NullBase() { free(buf); }",
     "};",
     ""]

defs = []
o += ["struct Null_" + n + ";" for n in ifaces] + [""]
for name in ifaces:
    cls = "Null_" + name
    o.append(f"struct {cls} : {name}, NullBase {{")
    o.append("    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID, void **p) override { if (p) *p = this; refs++; return S_OK; }")
    o.append("    ULONG STDMETHODCALLTYPE AddRef() override { return (ULONG)++refs; }")
    o.append("    ULONG STDMETHODCALLTYPE Release() override { long r = --refs; if (r <= 0) delete this; return (ULONG)(r < 0 ? 0 : r); }")
    seen = set()
    for ret, mname, args in chain(name):
        sig = (mname, args)
        if sig in seen:
            continue
        seen.add(sig)
        a = split_args(args)
        params = ", ".join(f"{t} {n}" for t, n in a)
        o.append(f"    {ret} STDMETHODCALLTYPE {mname}({params}) override;")
        owner = name
        for b in [name] + bases(name):
            if b in ifaces and any(m[1] == mname for m in ifaces[b][1]):
                owner = b
                break
        defs.append(f"{ret} STDMETHODCALLTYPE {cls}::{mname}({params}) {{")
        for t, n in a:
            defs.append(f"    (void){n};")
        for line in body(owner, ret, mname, a):
            defs.append("    " + line)
        defs.append("}")
    o.append("};")
    o.append("")
o += defs + [""]

o += ["extern \"C\" IDirect3D9 *WINAPI Direct3DCreate9(UINT) { return new Null_IDirect3D9(); }",
      "extern \"C\" HRESULT WINAPI Direct3DCreate9Ex(UINT, IDirect3D9Ex **pp) {",
      "    if (pp) *pp = new Null_IDirect3D9Ex();", "    return S_OK;", "}", ""]
open(out, "w").write("\n".join(o))
print(f"{out}: {len(ifaces)} interfaces, {sum(len(chain(n)) for n in ifaces)} methods")
