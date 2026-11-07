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


def body(iface, ret, name, args):
    lines = []
    if name in UNIMPL.get(iface, ()):
        lines.append(f'NULL_UNIMPL("{iface}::{name}");')
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
        ("IDirect3D9", "GetAdapterModeCount"): "return NULL_MODES;",
        ("IDirect3D9", "EnumAdapterModes"): "if (Mode >= NULL_MODES) return D3DERR_INVALIDCALL; if (pMode) null_enum_mode(Mode, pMode); return S_OK;",
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
# the platform lane's recorder (platform/include/fnvnx_win32_ext.h, platform/win32/platstat.cpp): weak, so a guest
# without it links and the marks are inert; one load per call while recording is off
o += ["extern \"C\" volatile int fnvnx_plat_on __attribute__((weak));",
      "extern \"C\" void fnvnx_plat_note(int cls, const char *name, unsigned caller, const char *detail) __attribute__((weak));",
      "#define NULL_UNIMPL(n) do { if (&fnvnx_plat_on && fnvnx_plat_on && fnvnx_plat_note) \\",
      "    fnvnx_plat_note(0 /* FNVNX_PLAT_UNIMPL */, n, (unsigned)(unsigned long)__builtin_return_address(0), \"null device: not executed\"); } while (0)",
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
