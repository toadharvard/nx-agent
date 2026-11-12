#!/usr/bin/env python3
"""Generate null_d3d9.cpp: a headless IDirect3D9 / IDirect3DDevice9 (and every resource interface) from
shim/include/d3d9.h, so the engine can run without a renderer (S6 replaces it).

    python3 -I toolchain/game/gen_null_d3d9.py shim/include/d3d9.h toolchain/game/null_d3d9.cpp

Rules: every method returns S_OK / 0 / a plausible constant; out-parameters of interface type get a new null object
of that interface (carrying width/height/format/size from the Create* arguments); Get*Desc fill from those; Lock*
hand out a per-object scratch buffer; caps, adapter modes and display mode describe a 1280x720 X8R8G8B8 SM3 device.
A renderer reads state back, so state is kept where D3D9 keeps it: the device stores its present parameters and what
Set* was given (Get* returns it, D3D9's defaults before), hands out one back-buffer / depth / swap-chain object, and
every object knows its device (GetDevice); a texture's level surfaces, cube faces and volume levels are the texture's
size shifted by the level (STATE, SUBOBJECTS and the prelude below).
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
          "Pool": "pool", "EdgeLength": "w", "Depth": "d", "FVF": "fvf",
          "MultiSample": "ms", "MultisampleQuality": "msq",
          # the Ex entry points name their arguments in lower case
          "width": "w", "height": "h", "format": "fmt", "pool": "pool", "usage": "usage",
          "multisample_type": "ms", "multisample_quality": "msq"}
DESC_FILL = {
    "D3DSURFACE_DESC": "p->Format = (D3DFORMAT)fmt; p->Type = D3DRTYPE_SURFACE; p->Usage = usage; p->Pool = (D3DPOOL)pool; p->Width = w; p->Height = h; p->MultiSampleType = (D3DMULTISAMPLE_TYPE)ms; p->MultiSampleQuality = msq;",
    "D3DVERTEXBUFFER_DESC": "p->Format = D3DFMT_VERTEXDATA; p->Type = D3DRTYPE_VERTEXBUFFER; p->Usage = usage; p->Pool = (D3DPOOL)pool; p->Size = size; p->FVF = fvf;",
    "D3DINDEXBUFFER_DESC": "p->Format = (D3DFORMAT)fmt; p->Type = D3DRTYPE_INDEXBUFFER; p->Usage = usage; p->Pool = (D3DPOOL)pool; p->Size = size;",
    "D3DVOLUME_DESC": "p->Format = (D3DFORMAT)fmt; p->Type = D3DRTYPE_VOLUME; p->Usage = usage; p->Pool = (D3DPOOL)pool; p->Width = w; p->Height = h; p->Depth = d;",
}
RTYPE = {"IDirect3DSurface9": "D3DRTYPE_SURFACE", "IDirect3DTexture9": "D3DRTYPE_TEXTURE",
         "IDirect3DCubeTexture9": "D3DRTYPE_CUBETEXTURE", "IDirect3DVolumeTexture9": "D3DRTYPE_VOLUMETEXTURE",
         "IDirect3DVertexBuffer9": "D3DRTYPE_VERTEXBUFFER", "IDirect3DIndexBuffer9": "D3DRTYPE_INDEXBUFFER"}


# entry points whose effect is GPU work or GPU data: on the null device nothing executes them, so each call is an
# unimplemented path of the guest's own frames (D25 #3 #5; tools/perf/PLAT.md). With the native renderer linked the
# engine's replaced functions no longer reach them; a call that still does is a draw, clear or copy nobody performs.
UNIMPL = {
    "IDirect3DDevice9": {"DrawPrimitive", "DrawIndexedPrimitive", "DrawPrimitiveUP", "DrawIndexedPrimitiveUP",
                         "DrawRectPatch", "DrawTriPatch", "Clear", "StretchRect", "ColorFill", "UpdateSurface",
                         "UpdateTexture", "GetRenderTargetData", "GetFrontBufferData", "ProcessVertices", "Present"},
    "IDirect3DSwapChain9": {"Present", "GetFrontBufferData"},
    "IDirect3DSurface9": {"GetDC", "ReleaseDC"},
}


# Sub-objects made from a parent (a texture's level surface, a cube face, a volume level): the parent's dimensions
# with the level shift, its format, usage and pool, and the parent as container (Level is the argument's name)
SUBOBJECTS = {"GetSurfaceLevel", "GetCubeMapSurface", "GetVolumeLevel"}
# textures: Levels 0 is the whole chain (1 with AUTOGENMIPMAP), and a cube's height is its edge
TEXTURE_CREATE = {"CreateTexture": "o->levels = null_mips(Levels, Usage, Width, Height, 1);",
                  "CreateVolumeTexture": "o->levels = null_mips(Levels, Usage, Width, Height, Depth);",
                  "CreateCubeTexture": "o->h = o->w; o->levels = null_mips(Levels, Usage, EdgeLength, EdgeLength, 1);"}
# render targets and depth buffers carry their role in Usage (a device's Get*Desc says so)
SURFACE_USAGE = {"CreateRenderTarget": "D3DUSAGE_RENDERTARGET", "CreateRenderTargetEx": "D3DUSAGE_RENDERTARGET",
                 "CreateDepthStencilSurface": "D3DUSAGE_DEPTHSTENCIL", "CreateDepthStencilSurfaceEx": "D3DUSAGE_DEPTHSTENCIL"}

# Methods whose whole body is state: what Set* stored comes back from Get* (D3D9's defaults before), the device's
# own views of its back buffer are one object each, and the objects it hands out know their device. ("*": any
# interface.) DevState is in the prelude below; S() is the device's.
STATE = {
    ("*", "GetDevice"): """if (ppDevice) { *ppDevice = nullptr; if (dev) { dev->AddRef(); *ppDevice = dev; } else { auto *o = new Null_IDirect3DDevice9(); o->inherit(this); *ppDevice = o; } }
return S_OK;""",
    ("IDirect3DDevice9", "GetDirect3D"): """if (!ppD3D9) return D3DERR_INVALIDCALL;
if (d3d) { d3d->AddRef(); *ppD3D9 = d3d; } else *ppD3D9 = new Null_IDirect3D9();
return S_OK;""",
    ("IDirect3DDevice9", "GetCreationParameters"): "if (!pParameters) return D3DERR_INVALIDCALL; *pParameters = S()->cp; return S_OK;",
    ("IDirect3DDevice9", "GetDisplayMode"): "if (iSwapChain != 0) return D3DERR_INVALIDCALL; if (pMode) null_display_mode(S()->pp, pMode); return S_OK;",
    ("IDirect3DSwapChain9", "GetDisplayMode"): "if (pMode) { if (pp) null_display_mode(*pp, pMode); else null_mode(pMode); } return S_OK;",
    ("IDirect3DDevice9", "GetNumberOfSwapChains"): "return 1;",
    ("IDirect3DDevice9", "Reset"): "null_reset(this, pPresentationParameters); return S_OK;",
    ("IDirect3DDevice9Ex", "ResetEx"): "null_reset(this, parameters); return S_OK;",
    ("IDirect3DDevice9", "CreateAdditionalSwapChain"): """if (!pSwapChain) return D3DERR_INVALIDCALL;
*pSwapChain = nullptr;
if (!pPresentationParameters) return D3DERR_INVALIDCALL;
null_resolve_pp(pPresentationParameters);
*pSwapChain = null_new_sc(this, pPresentationParameters);
return S_OK;""",
    ("IDirect3DDevice9", "GetSwapChain"): """if (!pSwapChain) return D3DERR_INVALIDCALL;
*pSwapChain = nullptr;
if (iSwapChain != 0) return D3DERR_INVALIDCALL;
*pSwapChain = null_ref(null_sc(this));
return S_OK;""",
    ("IDirect3DDevice9", "GetBackBuffer"): """if (!ppBackBuffer) return D3DERR_INVALIDCALL;
*ppBackBuffer = nullptr;
if (iSwapChain != 0 || iBackBuffer >= S()->pp.BackBufferCount) return D3DERR_INVALIDCALL;
*ppBackBuffer = null_ref(null_bb(this));
return S_OK;""",
    ("IDirect3DSwapChain9", "GetBackBuffer"): """if (!ppBackBuffer) return D3DERR_INVALIDCALL;
*ppBackBuffer = nullptr;
if (iBackBuffer >= (pp && pp->BackBufferCount ? pp->BackBufferCount : 1)) return D3DERR_INVALIDCALL;
if (!child) { auto *o = new Null_IDirect3DSurface9(); o->inherit(this); o->w = w; o->h = h; o->fmt = fmt; o->ms = ms; o->msq = msq; o->usage = D3DUSAGE_RENDERTARGET; child = o; }
child->AddRef(); *ppBackBuffer = (IDirect3DSurface9 *)child;
return S_OK;""",
    ("IDirect3DSwapChain9", "GetPresentParameters"): """if (!pPresentationParameters) return D3DERR_INVALIDCALL;
if (pp) *pPresentationParameters = *pp;
else { memset(pPresentationParameters, 0, sizeof *pPresentationParameters); pPresentationParameters->BackBufferWidth = w; pPresentationParameters->BackBufferHeight = h; pPresentationParameters->BackBufferFormat = (D3DFORMAT)fmt; pPresentationParameters->BackBufferCount = 1; pPresentationParameters->Windowed = TRUE; }
return S_OK;""",
    ("IDirect3DDevice9", "SetRenderTarget"): """if (RenderTargetIndex >= 4 || (RenderTargetIndex == 0 && !pRenderTarget)) return D3DERR_INVALIDCALL;
DevState *s = S();
null_set(s->rt[RenderTargetIndex], pRenderTarget);
D3DSURFACE_DESC d;
if (RenderTargetIndex == 0 && SUCCEEDED(pRenderTarget->GetDesc(&d))) null_target_size(s, d.Width, d.Height);
return S_OK;""",
    ("IDirect3DDevice9", "GetRenderTarget"): """if (!ppRenderTarget) return D3DERR_INVALIDCALL;
*ppRenderTarget = nullptr;
if (RenderTargetIndex >= 4) return D3DERR_INVALIDCALL;
IDirect3DSurface9 *r = S()->rt[RenderTargetIndex];
if (!r && RenderTargetIndex == 0) r = null_bb(this);
if (!r) return D3DERR_NOTFOUND;
*ppRenderTarget = null_ref(r);
return S_OK;""",
    ("IDirect3DDevice9", "SetDepthStencilSurface"): "DevState *s = S(); null_set(s->ds, pNewZStencil); s->ds_set = true; return S_OK;",
    ("IDirect3DDevice9", "GetDepthStencilSurface"): """if (!ppZStencilSurface) return D3DERR_INVALIDCALL;
DevState *s = S();
IDirect3DSurface9 *r = s->ds_set ? s->ds : s->pp.EnableAutoDepthStencil ? null_auto_ds(this) : nullptr;
*ppZStencilSurface = null_ref(r);
return r ? S_OK : D3DERR_NOTFOUND;""",
    ("IDirect3DDevice9", "SetViewport"): "if (!viewport) return D3DERR_INVALIDCALL; S()->vp = *viewport; return S_OK;",
    ("IDirect3DDevice9", "GetViewport"): "if (!pViewport) return D3DERR_INVALIDCALL; *pViewport = S()->vp; return S_OK;",
    ("IDirect3DDevice9", "SetScissorRect"): "if (!rect) return D3DERR_INVALIDCALL; S()->scissor = *rect; return S_OK;",
    ("IDirect3DDevice9", "GetScissorRect"): "if (!pRect) return D3DERR_INVALIDCALL; *pRect = S()->scissor; return S_OK;",
    ("IDirect3DDevice9", "SetRenderState"): "if ((unsigned)State >= 256) return D3DERR_INVALIDCALL; S()->rs[State] = Value; return S_OK;",
    ("IDirect3DDevice9", "GetRenderState"): "if ((unsigned)State >= 256 || !pValue) return D3DERR_INVALIDCALL; *pValue = S()->rs[State]; return S_OK;",
    ("IDirect3DDevice9", "SetSamplerState"): "int i = null_sampler(Sampler); if (i < 0 || (unsigned)Type >= 16) return D3DERR_INVALIDCALL; S()->ss[i][Type] = Value; return S_OK;",
    ("IDirect3DDevice9", "GetSamplerState"): "int i = null_sampler(Sampler); if (i < 0 || (unsigned)Type >= 16 || !pValue) return D3DERR_INVALIDCALL; *pValue = S()->ss[i][Type]; return S_OK;",
    ("IDirect3DDevice9", "SetTextureStageState"): "if (Stage >= 8 || (unsigned)Type >= 33) return D3DERR_INVALIDCALL; S()->tss[Stage][Type] = Value; return S_OK;",
    ("IDirect3DDevice9", "GetTextureStageState"): "if (Stage >= 8 || (unsigned)Type >= 33 || !pValue) return D3DERR_INVALIDCALL; *pValue = S()->tss[Stage][Type]; return S_OK;",
    ("IDirect3DDevice9", "SetTransform"): "if ((unsigned)state >= 512 || !matrix) return D3DERR_INVALIDCALL; S()->xf[state] = *matrix; return S_OK;",
    ("IDirect3DDevice9", "GetTransform"): "if ((unsigned)State >= 512 || !pMatrix) return D3DERR_INVALIDCALL; *pMatrix = S()->xf[State]; return S_OK;",
    ("IDirect3DDevice9", "SetTexture"): "int i = null_sampler(Stage); if (i < 0) return D3DERR_INVALIDCALL; null_set(S()->tex[i], pTexture); return S_OK;",
    ("IDirect3DDevice9", "GetTexture"): "int i = null_sampler(Stage); if (i < 0 || !ppTexture) return D3DERR_INVALIDCALL; *ppTexture = null_ref(S()->tex[i]); return S_OK;",
    ("IDirect3DDevice9", "SetStreamSource"): """if (StreamNumber >= 16) return D3DERR_INVALIDCALL;
DevState *s = S();
null_set(s->vb[StreamNumber], pStreamData);
s->vb_off[StreamNumber] = pStreamData ? OffsetInBytes : 0; s->vb_stride[StreamNumber] = pStreamData ? Stride : 0;
return S_OK;""",
    ("IDirect3DDevice9", "GetStreamSource"): """if (StreamNumber >= 16 || !ppStreamData) return D3DERR_INVALIDCALL;
DevState *s = S();
*ppStreamData = null_ref(s->vb[StreamNumber]);
if (OffsetInBytes) *OffsetInBytes = s->vb_off[StreamNumber];
if (pStride) *pStride = s->vb_stride[StreamNumber];
return S_OK;""",
    ("IDirect3DDevice9", "SetStreamSourceFreq"): "if (StreamNumber >= 16) return D3DERR_INVALIDCALL; S()->vb_freq[StreamNumber] = Divider; return S_OK;",
    ("IDirect3DDevice9", "GetStreamSourceFreq"): "if (StreamNumber >= 16 || !Divider) return D3DERR_INVALIDCALL; *Divider = S()->vb_freq[StreamNumber]; return S_OK;",
    ("IDirect3DDevice9", "SetIndices"): "null_set(S()->ib, pIndexData); return S_OK;",
    ("IDirect3DDevice9", "GetIndices"): "if (!ppIndexData) return D3DERR_INVALIDCALL; *ppIndexData = null_ref(S()->ib); return S_OK;",
    # a vertex declaration and an FVF replace each other
    ("IDirect3DDevice9", "SetVertexDeclaration"): "DevState *s = S(); null_set(s->decl, pDecl); s->fvf = 0; return S_OK;",
    ("IDirect3DDevice9", "GetVertexDeclaration"): "if (!ppDecl) return D3DERR_INVALIDCALL; *ppDecl = null_ref(S()->decl); return S_OK;",
    ("IDirect3DDevice9", "SetFVF"): "if (FVF) { DevState *s = S(); null_set(s->decl, (IDirect3DVertexDeclaration9 *)nullptr); s->fvf = FVF; } return S_OK;",
    ("IDirect3DDevice9", "GetFVF"): "if (!pFVF) return D3DERR_INVALIDCALL; *pFVF = S()->fvf; return S_OK;",
    ("IDirect3DDevice9", "SetVertexShader"): "null_set(S()->vs, pShader); return S_OK;",
    ("IDirect3DDevice9", "GetVertexShader"): "if (!ppShader) return D3DERR_INVALIDCALL; *ppShader = null_ref(S()->vs); return S_OK;",
    ("IDirect3DDevice9", "SetPixelShader"): "null_set(S()->ps, pShader); return S_OK;",
    ("IDirect3DDevice9", "GetPixelShader"): "if (!ppShader) return D3DERR_INVALIDCALL; *ppShader = null_ref(S()->ps); return S_OK;",
    ("IDirect3DDevice9", "SetSoftwareVertexProcessing"): "S()->swvp = bSoftware; return S_OK;",
    ("IDirect3DDevice9", "GetSoftwareVertexProcessing"): "return S()->swvp;",
    ("IDirect3DDevice9", "SetNPatchMode"): "S()->npatch = nSegments; return S_OK;",
    ("IDirect3DDevice9", "GetNPatchMode"): "return S()->npatch;",
    ("IDirect3DSurface9", "GetContainer"): """if (!ppContainer) return D3DERR_INVALIDCALL;
*ppContainer = nullptr;
return container ? container->QueryInterface(riid, ppContainer) : D3DERR_INVALIDCALL;""",
    ("IDirect3DVolume9", "GetContainer"): """if (!ppContainer) return D3DERR_INVALIDCALL;
*ppContainer = nullptr;
return container ? container->QueryInterface(riid, ppContainer) : D3DERR_INVALIDCALL;""",
    ("IDirect3DVertexDeclaration9", "GetDeclaration"): """if (!pNumElements) return D3DERR_INVALIDCALL;
if (a0 && blob) memcpy(a0, blob, blob_size);
*pNumElements = blob_size / sizeof(D3DVERTEXELEMENT9);
return S_OK;""",
}


def lookup(table, iface, name):
    for i in [iface] + bases(iface):
        if (i, name) in table:
            return table[(i, name)]
    return table.get(("*", name))


def body(iface, ret, name, args):
    lines = []
    if name in UNIMPL.get(iface, ()):
        lines.append(f'NULL_UNIMPL("{iface}::{name}");')
    st = lookup(STATE, iface, name)
    if st:
        return lines + st.split("\n")
    outs = [(t, n) for t, n in args if IFACE_OUT.search(t)]
    setters = [f"o->{FIELDS[n]} = (uint32_t){n};" for t, n in args if n in FIELDS and "*" not in t]
    for t, n in outs:
        cls = "Null_" + IFACE_OUT.search(t).group(1)
        if cls[5:] not in ifaces:
            lines.append(f"if ({n}) *{n} = nullptr;")
            continue
        extra = ""
        if name in SUBOBJECTS:
            lines.append(f"if ({n}) *{n} = nullptr;")
            if name == "GetCubeMapSurface":
                lines.append("if ((unsigned)FaceType > D3DCUBEMAP_FACE_NEGATIVE_Z) return D3DERR_INVALIDCALL;")
            lines.append("if (Level >= (levels ? levels : 1)) return D3DERR_INVALIDCALL;")
        if iface in ("IDirect3D9", "IDirect3D9Ex") and name in ("CreateDevice", "CreateDeviceEx"):
            # the device keeps its back buffer's size and format (the present parameters; 0 / UNKNOWN in windowed
            # mode mean the window's and the display's: the null mode)
            # (resolved in place, as D3D9 does: the caller reads the sizes back from its parameters)
            pp = [m for u, m in args if "D3DPRESENT_PARAMETERS" in u][0]
            extra = f"null_init_device(o, o, this, {pp}, {args[0][1]}, {args[1][1]}, {args[2][1]}, {args[3][1]});"
        elif name in SUBOBJECTS:
            extra = "null_sub(o, this, Level); o->set_container(this);"
        elif name in TEXTURE_CREATE:
            extra = TEXTURE_CREATE[name]
        elif name in SURFACE_USAGE:
            extra = f"o->usage |= {SURFACE_USAGE[name]};"
        elif name == "CreateVertexDeclaration":
            extra = "null_copy_decl(o, elements);"
        fill = " ".join(x for x in (" ".join(setters), extra) if x)
        lines.append(f"if ({n}) {{ auto *o = new {cls}(); o->inherit(this); {fill} *{n} = o; }}")
    for t, n in args:
        base = t.replace("const", "").replace("*", "").strip()
        if t.endswith("*") and "const" not in t and base in DESC_FILL and name.startswith("Get"):
            fill = DESC_FILL[base]
            if name == "GetLevelDesc":
                # a level of a texture: the texture's size shifted down (at least 1), in range
                lines.append("if (Level >= (levels ? levels : 1)) return D3DERR_INVALIDCALL;")
                fill = (fill.replace("p->Width = w", "p->Width = null_lvl(w, Level)")
                        .replace("p->Height = h", "p->Height = null_lvl(h, Level)")
                        .replace("p->Depth = d", "p->Depth = null_lvl(d, Level)"))
            lines.append(f"if ({n}) {{ auto *p = {n}; memset(p, 0, sizeof *p); {fill} }}")
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
        # the modes of the asked display format only, as D3D9 lists them: modes for every format gave the
        # renderer's mode list (0x00e7b7d0, per format) one duplicate per format (S5-boot lockstep)
        ("IDirect3D9", "GetAdapterModeCount"): "return Adapter == 0 && Format == D3DFMT_X8R8G8B8 ? (UINT)NULL_MODES : 0;",
        ("IDirect3D9", "EnumAdapterModes"): "if (Adapter != 0 || Format != D3DFMT_X8R8G8B8 || Mode >= NULL_MODES) return D3DERR_INVALIDCALL; if (pMode) null_enum_mode(Mode, pMode); return S_OK;",
        ("IDirect3D9", "GetAdapterDisplayMode"): "if (pMode) null_mode(pMode); return S_OK;",
        ("IDirect3D9", "GetDeviceCaps"): "if (pCaps) null_caps(pCaps); return S_OK;",
        ("IDirect3D9", "GetAdapterIdentifier"): "if (pIdentifier) { memset(pIdentifier, 0, sizeof *pIdentifier); strcpy(pIdentifier->Driver, \"fnvnx-null\"); strcpy(pIdentifier->Description, \"fnv-nx null D3D9 device\"); pIdentifier->VendorId = 0x10de; pIdentifier->DeviceId = 0x0391; } return S_OK;",
        ("IDirect3D9", "CheckDeviceMultiSampleType"): "if (pQualityLevels) *pQualityLevels = 1; return S_OK;",
        ("IDirect3D9", "GetAdapterMonitor"): "return (HMONITOR)1;",
        ("IDirect3DDevice9", "GetDeviceCaps"): "if (pCaps) null_caps(pCaps); return S_OK;",
        ("IDirect3DDevice9", "ValidateDevice"): "if (pNumPasses) *pNumPasses = 1; return S_OK;",
        ("IDirect3DDevice9Ex", "GetGPUThreadPriority"): "if (priority) *priority = 0; return S_OK;",
        ("IDirect3DDevice9Ex", "GetMaximumFrameLatency"): "if (max_latency) *max_latency = 3; return S_OK;",
        ("IDirect3DDevice9", "GetAvailableTextureMem"): "return 512u << 20;",
        ("IDirect3DDevice9", "GetNumberOfSwapChains"): "return 1;",
        ("IDirect3DBaseTexture9", "GetLevelCount"): "return levels ? levels : 1;",
        ("IDirect3DQuery9", "GetDataSize"): "return 8;",
        ("IDirect3DQuery9", "GetData"): "if (pData) memset(pData, 0, dwSize); return S_OK;",
    }
    if iface in ("IDirect3D9", "IDirect3D9Ex") and name in ("CreateDevice", "CreateDeviceEx"):
        # the device object exists: the native renderer starts here (toolchain/game/native_device.cpp, linked with
        # libnxrender.a); its HRESULT is the call's, so a renderer that cannot start fails the device creation
        dev = [n for t, n in outs][0]
        pp = [n for t, n in args if "D3DPRESENT_PARAMETERS" in t][0]
        lines.append(f"if ({dev} && *{dev} && fnvnx_d3d9_device_created) return fnvnx_d3d9_device_created({pp}, *{dev});")
        lines.append("return S_OK;")
        return lines
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


# D3D9's default render states / sampler states / texture stage states (the SDK's "default" columns); the rest are 0
RS_DEFAULT = {
    "1": "ZENABLE ZWRITEENABLE LASTPIXEL CLIPPING LIGHTING COLORVERTEX LOCALVIEWER MULTISAMPLEANTIALIAS",
    "D3DFILL_SOLID": "FILLMODE", "D3DSHADE_GOURAUD": "SHADEMODE", "D3DBLEND_ONE": "SRCBLEND SRCBLENDALPHA",
    "D3DBLEND_ZERO": "DESTBLEND DESTBLENDALPHA", "D3DCULL_CCW": "CULLMODE", "D3DCMP_LESSEQUAL": "ZFUNC",
    "D3DCMP_ALWAYS": "ALPHAFUNC STENCILFUNC CCW_STENCILFUNC",
    "D3DSTENCILOP_KEEP": "STENCILFAIL STENCILZFAIL STENCILPASS CCW_STENCILFAIL CCW_STENCILZFAIL CCW_STENCILPASS",
    "0xFFFFFFFFu": "STENCILMASK STENCILWRITEMASK TEXTUREFACTOR MULTISAMPLEMASK BLENDFACTOR",
    "D3DMCS_COLOR1": "DIFFUSEMATERIALSOURCE", "D3DMCS_COLOR2": "SPECULARMATERIALSOURCE",
    "0xF": "COLORWRITEENABLE COLORWRITEENABLE1 COLORWRITEENABLE2 COLORWRITEENABLE3",
    "D3DBLENDOP_ADD": "BLENDOP BLENDOPALPHA", "D3DDEGREE_CUBIC": "POSITIONDEGREE", "D3DDEGREE_LINEAR": "NORMALDEGREE",
    "null_f(1.0f)": "FOGEND FOGDENSITY POINTSIZE POINTSIZE_MIN POINTSCALE_A MINTESSELLATIONLEVEL MAXTESSELLATIONLEVEL",
    "null_f(64.0f)": "POINTSIZE_MAX",
}


def default_switch(fn, table, prefix):
    out = [f"static DWORD {fn}(unsigned s) {{", "    switch (s) {"]
    for val, names in table.items():
        for nm in names.split():
            out.append(f"    case {prefix}{nm}: return {val};")
    out += ["    default: return 0;", "    }", "}"]
    return out


def bases(n):
    out = []
    while n in ifaces:
        n = ifaces[n][0]
        out.append(n)
    return out


PRELUDE_STATE = r"""
/* a device's state: what Set* stored, so Get* gives it back (D3D9's defaults before the first Set*) */
struct DevState {
    D3DPRESENT_PARAMETERS pp;                 /* as created or reset, sizes and format resolved */
    D3DDEVICE_CREATION_PARAMETERS cp;
    D3DVIEWPORT9 vp; RECT scissor;            /* the render target 0's size until set */
    DWORD rs[256];
    DWORD ss[20][16];                         /* samplers 0-15, then the 4 vertex samplers */
    DWORD tss[8][33];
    D3DMATRIX xf[512];                        /* VIEW, PROJECTION, TEXTUREn, WORLDMATRIX(n) = 256 + n */
    IDirect3DBaseTexture9 *tex[20];
    IDirect3DVertexBuffer9 *vb[16]; UINT vb_off[16], vb_stride[16], vb_freq[16];
    IDirect3DIndexBuffer9 *ib;
    IDirect3DVertexDeclaration9 *decl; DWORD fvf;
    IDirect3DVertexShader9 *vs; IDirect3DPixelShader9 *ps;
    IDirect3DSurface9 *rt[4], *ds; bool ds_set;   /* as set; unset: the back buffer / the auto depth buffer */
    IDirect3DSurface9 *bb, *auto_ds; IDirect3DSwapChain9 *sc;   /* the device's own views: one object each */
    BOOL swvp; float npatch;
};
template <class T> static T *null_ref(T *p) { if (p) p->AddRef(); return p; }
template <class T> static void null_set(T *&slot, T *p) { if (p) p->AddRef(); T *old = slot; slot = p; if (old) old->Release(); }
template <class T> static void null_rel(T *&p) { T *old = p; p = nullptr; if (old) old->Release(); }
static DWORD null_f(float f) { DWORD d; memcpy(&d, &f, 4); return d; }
"""

PRELUDE_STATE2 = r"""static DWORD null_ss_default(unsigned t) {
    switch (t) {
    case D3DSAMP_ADDRESSU: case D3DSAMP_ADDRESSV: case D3DSAMP_ADDRESSW: return D3DTADDRESS_WRAP;
    case D3DSAMP_MAGFILTER: case D3DSAMP_MINFILTER: return D3DTEXF_POINT;
    case D3DSAMP_MAXANISOTROPY: return 1;
    default: return 0;
    }
}
static DWORD null_tss_default(unsigned stage, unsigned t) {
    switch (t) {
    case D3DTSS_COLOROP: return stage == 0 ? D3DTOP_MODULATE : D3DTOP_DISABLE;
    case D3DTSS_ALPHAOP: return stage == 0 ? D3DTOP_SELECTARG1 : D3DTOP_DISABLE;
    case D3DTSS_COLORARG1: case D3DTSS_ALPHAARG1: return D3DTA_TEXTURE;
    case D3DTSS_TEXCOORDINDEX: return stage;
    default: return 0;   /* COLORARG2 / ALPHAARG2 / ARG0 / RESULTARG are CURRENT (0) */
    }
}
static DevState *null_new_state() {
    DevState *s = (DevState *)calloc(1, sizeof *s);
    for (unsigned i = 0; i < 256; i++) s->rs[i] = null_rs_default(i);
    for (unsigned i = 0; i < 20; i++) for (unsigned t = 0; t < 16; t++) s->ss[i][t] = null_ss_default(t);
    for (unsigned i = 0; i < 8; i++) for (unsigned t = 0; t < 33; t++) s->tss[i][t] = null_tss_default(i, t);
    for (unsigned i = 0; i < 512; i++) s->xf[i]._11 = s->xf[i]._22 = s->xf[i]._33 = s->xf[i]._44 = 1.0f;
    for (unsigned i = 0; i < 16; i++) s->vb_freq[i] = 1;
    s->vp.MaxZ = 1.0f;
    return s;
}
static void null_free_state(DevState *s) {
    for (auto &t : s->tex) null_rel(t);
    for (auto &v : s->vb) null_rel(v);
    for (auto &r : s->rt) null_rel(r);
    null_rel(s->ib); null_rel(s->decl); null_rel(s->vs); null_rel(s->ps); null_rel(s->ds);
    null_rel(s->bb); null_rel(s->auto_ds); null_rel(s->sc);
    free(s);
}
/* bytes in a row of w pixels (blocks for DXT): LockRect's Pitch, which the engine copies rows by */
static uint32_t null_pitch(uint32_t fmt, uint32_t w) {
    switch (fmt) {
    case D3DFMT_DXT1: return ((w + 3) / 4) * 8;
    case D3DFMT_DXT2: case D3DFMT_DXT3: case D3DFMT_DXT4: case D3DFMT_DXT5: return ((w + 3) / 4) * 16;
    case D3DFMT_R8G8B8: return w * 3;
    case D3DFMT_R5G6B5: case D3DFMT_X1R5G5B5: case D3DFMT_A1R5G5B5: case D3DFMT_A4R4G4B4: case D3DFMT_X4R4G4B4:
    case D3DFMT_A8R3G3B2: case D3DFMT_A8P8: case D3DFMT_A8L8: case D3DFMT_V8U8: case D3DFMT_L16: case D3DFMT_R16F:
    case D3DFMT_D16: case D3DFMT_D16_LOCKABLE: case D3DFMT_INDEX16: return w * 2;
    case D3DFMT_A8: case D3DFMT_L8: case D3DFMT_P8: case D3DFMT_R3G3B2: case D3DFMT_A4L4: return w;
    case D3DFMT_A16B16G16R16: case D3DFMT_A16B16G16R16F: case D3DFMT_Q16W16V16U16: case D3DFMT_G32R32F: return w * 8;
    case D3DFMT_A32B32G32R32F: return w * 16;
    default: return w * 4;
    }
}
"""

PRELUDE_HELPERS = r"""
/* levels of a texture: Levels 0 is the whole chain (one level with AUTOGENMIPMAP) */
static uint32_t null_mips(UINT levels, DWORD usage, UINT w, UINT h, UINT d) {
    uint32_t full = 1, m = w > h ? w : h;
    if (d > m) m = d;
    while (m > 1) { m >>= 1; full++; }
    if (!levels) return (usage & D3DUSAGE_AUTOGENMIPMAP) ? 1 : full;
    return levels > full ? full : levels;
}
static uint32_t null_lvl(uint32_t v, UINT level) { v >>= level; return v ? v : 1; }
/* a level surface / cube face / volume level of a texture: its size shifted by the level, the texture's format */
static void null_sub(NullBase *o, const NullBase *p, UINT level) {
    o->w = null_lvl(p->w, level); o->h = null_lvl(p->h, level); o->d = p->d ? null_lvl(p->d, level) : 0;
    o->fmt = p->fmt; o->usage = p->usage; o->pool = p->pool; o->ms = p->ms; o->msq = p->msq;
}
static int null_sampler(DWORD s) { return s < 16 ? (int)s : (s >= D3DVERTEXTEXTURESAMPLER0 && s <= D3DVERTEXTEXTURESAMPLER3) ? (int)(s - D3DVERTEXTEXTURESAMPLER0 + 16) : -1; }
/* 0 sizes / UNKNOWN format mean the window's and the display's: the null mode */
static void null_resolve_pp(D3DPRESENT_PARAMETERS *p) {
    if (!p->BackBufferWidth) p->BackBufferWidth = 1280;
    if (!p->BackBufferHeight) p->BackBufferHeight = 720;
    if (p->BackBufferFormat == D3DFMT_UNKNOWN) p->BackBufferFormat = D3DFMT_X8R8G8B8;
    if (!p->BackBufferCount) p->BackBufferCount = 1;
}
static void null_display_mode(const D3DPRESENT_PARAMETERS &p, D3DDISPLAYMODE *m) {
    null_mode(m);
    if (!p.Windowed) { m->Width = p.BackBufferWidth; m->Height = p.BackBufferHeight; m->Format = p.BackBufferFormat; if (p.FullScreen_RefreshRateInHz) m->RefreshRate = p.FullScreen_RefreshRateInHz; }
}
/* render target 0 changed: the viewport and the scissor rect are its size again */
static void null_target_size(DevState *s, uint32_t w, uint32_t h) {
    s->vp.X = s->vp.Y = 0; s->vp.Width = w; s->vp.Height = h; s->vp.MinZ = 0.0f; s->vp.MaxZ = 1.0f;
    s->scissor.left = s->scissor.top = 0; s->scissor.right = (LONG)w; s->scissor.bottom = (LONG)h;
}
static void null_setup(NullBase *d, const D3DPRESENT_PARAMETERS &p) {
    DevState *s = d->S();
    s->pp = p; d->w = p.BackBufferWidth; d->h = p.BackBufferHeight; d->fmt = (uint32_t)p.BackBufferFormat;
    null_target_size(s, d->w, d->h);
    s->rs[D3DRS_ZENABLE] = p.EnableAutoDepthStencil ? 1 : 0;   /* D3DZB_TRUE with an auto depth buffer */
}
/* CreateDevice(Ex): the device keeps its present parameters (resolved in place, as D3D9 does) and creation parameters */
static void null_init_device(NullBase *o, IDirect3DDevice9 *self, IDirect3D9 *d3d, D3DPRESENT_PARAMETERS *pp,
                             UINT adapter, D3DDEVTYPE type, HWND hwnd, DWORD flags) {
    D3DPRESENT_PARAMETERS tmp; memset(&tmp, 0, sizeof tmp); tmp.Windowed = TRUE;
    if (!pp) pp = &tmp;
    o->dev = self; o->d3d = d3d;
    null_resolve_pp(pp);
    null_setup(o, *pp);
    DevState *s = o->S();
    s->cp.AdapterOrdinal = adapter; s->cp.DeviceType = type; s->cp.hFocusWindow = hwnd; s->cp.BehaviorFlags = flags;
}
/* Reset(Ex): every state is back to its default and the views are made again (the old ones keep their size) */
static void null_reset(NullBase *d, D3DPRESENT_PARAMETERS *pp) {
    DevState *old = d->S();
    D3DPRESENT_PARAMETERS np = pp ? *pp : old->pp;
    D3DDEVICE_CREATION_PARAMETERS cp = old->cp;
    null_resolve_pp(&np);
    if (pp) *pp = np;
    d->st = nullptr; null_free_state(old);
    null_setup(d, np);
    d->S()->cp = cp;
}
/* CreateVertexDeclaration: keeps the elements through the D3DDECL_END entry, for GetDeclaration */
static void null_copy_decl(NullBase *o, const D3DVERTEXELEMENT9 *e) {
    if (!e) return;
    size_t n = 1;
    while (n < 64 && e[n - 1].Stream != 0xFF) n++;
    o->blob = malloc(n * sizeof *e); memcpy(o->blob, e, n * sizeof *e); o->blob_size = (uint32_t)(n * sizeof *e);
}
"""

PRELUDE_VIEWS = r"""/* the device's own views: the back buffer, the auto depth buffer and the swap chain are one object each, so
 * GetBackBuffer, GetRenderTarget(0) and the swap chain's GetBackBuffer agree (and compare equal) */
static IDirect3DSurface9 *null_bb(NullBase *d) {
    DevState *s = d->S();
    if (!s->bb) {
        auto *o = new Null_IDirect3DSurface9(); o->inherit(d);
        o->w = d->w; o->h = d->h; o->fmt = d->fmt; o->usage = D3DUSAGE_RENDERTARGET; o->ms = s->pp.MultiSampleType; o->msq = s->pp.MultiSampleQuality;
        s->bb = o;
    }
    return s->bb;
}
static IDirect3DSurface9 *null_auto_ds(NullBase *d) {
    DevState *s = d->S();
    if (!s->auto_ds) {
        auto *o = new Null_IDirect3DSurface9(); o->inherit(d);
        o->w = d->w; o->h = d->h; o->usage = D3DUSAGE_DEPTHSTENCIL; o->ms = s->pp.MultiSampleType; o->msq = s->pp.MultiSampleQuality;
        o->fmt = s->pp.AutoDepthStencilFormat != D3DFMT_UNKNOWN ? (uint32_t)s->pp.AutoDepthStencilFormat : (uint32_t)D3DFMT_D24S8;
        s->auto_ds = o;
    }
    return s->auto_ds;
}
static Null_IDirect3DSwapChain9 *null_new_sc(NullBase *d, const D3DPRESENT_PARAMETERS *p) {
    auto *o = new Null_IDirect3DSwapChain9(); o->inherit(d);
    o->w = p->BackBufferWidth; o->h = p->BackBufferHeight; o->fmt = (uint32_t)p->BackBufferFormat; o->ms = p->MultiSampleType; o->msq = p->MultiSampleQuality;
    o->pp = (D3DPRESENT_PARAMETERS *)malloc(sizeof *p); *o->pp = *p;
    return o;
}
static IDirect3DSwapChain9 *null_sc(NullBase *d) {
    DevState *s = d->S();
    if (!s->sc) {
        auto *o = null_new_sc(d, &s->pp);
        o->child = null_ref(null_bb(d));
        s->sc = o;
    }
    return s->sc;
}"""


o = ["/* null_d3d9.cpp - GENERATED by toolchain/game/gen_null_d3d9.py from shim/include/d3d9.h; do not edit.",
     " * Headless D3D9 for the guest boot (S6 replaces it): every call succeeds, nothing is drawn. */",
     "#include <string.h>", "#include <stdint.h>", "#include <stdlib.h>", "#include <d3d9.h>", "",
     "static void null_mode(D3DDISPLAYMODE *m) { m->Width = 1280; m->Height = 720; m->RefreshRate = 60; m->Format = D3DFMT_X8R8G8B8; }",
     "/* the adapter's modes: a PC adapter's usual list, so the INI's iSize (the engine's default 640x480 until the",
     " * INI is read) has one; the desktop mode above is the handheld panel, 1920x1080 the docked output */",
     "static const uint16_t null_modes[][2] = {{640, 480}, {800, 600}, {1024, 768}, {1280, 720}, {1280, 1024}, {1920, 1080}};",
     "#define NULL_MODES (sizeof null_modes / sizeof null_modes[0])",
     "static void null_enum_mode(UINT i, D3DDISPLAYMODE *m) { null_mode(m); m->Width = null_modes[i][0]; m->Height = null_modes[i][1]; }",
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
     ""]
o += PRELUDE_STATE.splitlines() + default_switch("null_rs_default", RS_DEFAULT, "D3DRS_") + [""] + PRELUDE_STATE2.splitlines()
o += [
     "struct NullBase {",
     "    volatile long refs = 1; uint32_t w = 0, h = 0, d = 0, fmt = 0, size = 0, levels = 0, usage = 0, pool = 0, fvf = 0;",
     "    uint32_t ms = 0, msq = 0;   /* multisample type and quality (surfaces) */",
     "    void *buf = nullptr;",
     "    /* the device and the Direct3D object this one came from (D3D9 hands them back from GetDevice /",
     "     * GetDirect3D): not referenced here, so the device's own views of its back buffer make no cycle */",
     "    IDirect3DDevice9 *dev = nullptr; IDirect3D9 *d3d = nullptr;",
     "    IUnknown *container = nullptr;   /* a level surface's texture (referenced) */",
     "    IUnknown *child = nullptr;       /* a swap chain's back buffer (referenced) */",
     "    DevState *st = nullptr;          /* a device's state, made on first use */",
     "    D3DPRESENT_PARAMETERS *pp = nullptr;   /* a swap chain's present parameters */",
     "    void *blob = nullptr; uint32_t blob_size = 0;   /* a vertex declaration's elements */",
     "    void inherit(NullBase *p) { dev = p->dev; d3d = p->d3d; }",
     "    void set_container(IUnknown *c) { c->AddRef(); container = c; }",
     "    DevState *S() { if (!st) st = null_new_state(); return st; }",
     "    uint32_t pitch() const { return null_pitch(fmt, w ? w : 256); }",
     "    void *scratch() { if (!buf) { size_t n = size ? size : (size_t)pitch() * (h ? h : 256) * (d ? d : 1); if (n < 4096) n = 4096; buf = calloc(1, n); } return buf; }",
     "    virtual ~NullBase() { free(buf); free(pp); free(blob); if (container) container->Release(); if (child) child->Release(); if (st) null_free_state(st); }",
     "};",
     ""]
o += PRELUDE_HELPERS.splitlines()

defs = []
# the platform lane's recorder (platform/include/fnvnx_win32_ext.h, platform/win32/platstat.cpp): weak, so a guest
# without it links and the marks are inert; one load per call while recording is off
o += ["extern \"C\" volatile int fnvnx_plat_on __attribute__((weak));",
      "extern \"C\" void fnvnx_plat_note(int cls, const char *name, unsigned caller, const char *detail) __attribute__((weak));",
      "#define NULL_UNIMPL(n) do { if (&fnvnx_plat_on && fnvnx_plat_on && fnvnx_plat_note) \\",
      "    fnvnx_plat_note(0 /* FNVNX_NOTE_UNIMPL */, n, (unsigned)(unsigned long)__builtin_return_address(0), \"null device: not executed\"); } while (0)",
      ""]
# toolchain/game/native_device.cpp (weak: only the native-render guest has it): nx::Renderer comes up with the device
o += ["extern \"C\" HRESULT fnvnx_d3d9_device_created(const void *present_params, void *device) __attribute__((weak));", ""]
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
o += PRELUDE_VIEWS.splitlines() + [""]
o += defs + [""]

o += ["extern \"C\" IDirect3D9 *WINAPI Direct3DCreate9(UINT) { return new Null_IDirect3D9(); }",
      "extern \"C\" HRESULT WINAPI Direct3DCreate9Ex(UINT, IDirect3D9Ex **pp) {",
      "    if (pp) *pp = new Null_IDirect3D9Ex();", "    return S_OK;", "}", ""]
# d3d9.dll as a module, as render/nine/nine_register.cpp does for the Nine proxy: the engine loads it with
# LoadLibraryA("D3D9.DLL") and GetProcAddress("Direct3DCreate9") (its only IAT import from d3d9.dll is
# D3DPERF_SetOptions), so a null-device guest that does not register it stops at "Failed to initialize renderer."
# boot.cpp calls fnvnx_d3d9_register() (weak) before the image's imports are bound. Direct3DCreate9Ex is not offered,
# like Nine: the engine then takes the Direct3DCreate9 path it takes on the hardware.
o += ["typedef FARPROC (*fnvnx_module_resolver)(const char *proc);",
      "extern \"C\" void fnvnx_register_module(const char *dll_name, fnvnx_module_resolver resolve);",
      "extern \"C\" {",
      "int WINAPI D3DPERF_BeginEvent(D3DCOLOR, LPCWSTR) { return -1; }",
      "int WINAPI D3DPERF_EndEvent(void) { return -1; }",
      "void WINAPI D3DPERF_SetMarker(D3DCOLOR, LPCWSTR) {}",
      "void WINAPI D3DPERF_SetRegion(D3DCOLOR, LPCWSTR) {}",
      "BOOL WINAPI D3DPERF_QueryRepeatFrame(void) { return FALSE; }",
      "void WINAPI D3DPERF_SetOptions(DWORD) {}",
      "DWORD WINAPI D3DPERF_GetStatus(void) { return 0; }",
      "}",
      "static FARPROC null_d3d9_export(const char *proc) {",
      "    static const struct { const char *name; FARPROC fn; } exports[] = {"]
o += [f"        {{\"{n}\", (FARPROC){n}}}," for n in (
    "Direct3DCreate9", "D3DPERF_BeginEvent", "D3DPERF_EndEvent", "D3DPERF_SetMarker", "D3DPERF_SetRegion",
    "D3DPERF_QueryRepeatFrame", "D3DPERF_SetOptions", "D3DPERF_GetStatus")]
o += ["    };",
      "    if (!proc) return nullptr;",
      "    for (const auto &e : exports)",
      "        if (!strcmp(e.name, proc)) return e.fn;",
      "    return nullptr;",
      "}",
      "extern \"C\" void fnvnx_d3d9_register(void) {",
      "    static int done;",
      "    if (__atomic_exchange_n(&done, 1, __ATOMIC_ACQ_REL)) return;",
      "    fnvnx_register_module(\"d3d9.dll\", null_d3d9_export);",
      "}", ""]
open(out, "w").write("\n".join(o))
print(f"{out}: {len(ifaces)} interfaces, {sum(len(chain(n)) for n in ifaces)} methods")
