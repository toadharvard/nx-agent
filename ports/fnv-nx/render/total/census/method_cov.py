#!/usr/bin/env python3
"""method_cov.py: universe + status_d9cap / status_exec / status_null (+ result_recorded, evidence file:line).
Statuses were assigned by reading tools/compare/d3d9/d9cap.c, render/native/capture/d3d9_exec.cpp and
toolchain/game/gen_null_d3d9.py (rules below cite the lines).   usage: python3 -I method_cov.py > method_coverage.tsv"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import method_spec as S

NOTWRAP = {"IDirect3DVolume9", "IDirect3DVolumeTexture9", "IDirect3DStateBlock9", "IDirect3DQuery9", "IDirect3D9Ex",
           "IDirect3DSwapChain9Ex", "IDirect3DDevice9Ex"}   # d9cap.c:20-21,1174 (QI refuses Ex), README
CAP = "tools/compare/d3d9/d9cap.c"
FULL, PART, FWD, CNT, NW = "journaled_full", "journaled_partial", "forwarded_unjournaled", "counted_unjournaled", "not_wrapped"
CONSTS = [f"Set{s}Shader Constant{t}".replace(" ", "") for s in ("Vertex", "Pixel") for t in "FIB"]
# device methods: name -> (status, evidence line(s), note / lost inputs)
D = {m: (FULL, "d9cap.c:~780-950", "record per call; only when call succeeded and not inside Begin/EndStateBlock") for m in
     ["SetRenderState", "SetSamplerState", "SetTextureStageState", "SetTexture", "SetViewport", "SetScissorRect",
      "SetRenderTarget", "SetDepthStencilSurface", "SetVertexDeclaration", "SetVertexShader", "SetPixelShader",
      "SetStreamSource", "SetStreamSourceFreq", "SetIndices"] + CONSTS}
D.update({
 "SetRenderTarget": (FULL, "d9cap.c:711", "RS-like record SRT idx,surf id; unwrapped/unknown surface id=0"),
 "Clear": (FULL, "d9cap.c:742-750", "CLR; dropped before S16_CAPTURE_FRAMES first"),
 "DrawPrimitive": (FULL, "d9cap.c:821", "DRAW; frame-gated; FVF state not in stream (SetFVF unjournaled)"),
 "DrawIndexedPrimitive": (FULL, "d9cap.c:828", "DRWI; frame-gated"),
 "DrawPrimitiveUP": (FULL, "d9cap.c:856-864", "re-expressed as VBU+STRM+DRAW+STRM(0): not 1 record per call; frame-gated"),
 "DrawIndexedPrimitiveUP": (FULL, "d9cap.c:866-890", "re-expressed as VB/IB/VBU/IBU/STRM/SIDX/DRWI/STRM/SIDX; frame-gated"),
 "StretchRect": (FULL, "d9cap.c:700-712", "STRC; frame-gated"), "ColorFill": (FULL, "d9cap.c:714-726", "CFIL; frame-gated"),
 "CreateVertexDeclaration": (FULL, "d9cap.c:895,436", "DECL elements (END dropped)"),
 "CreateVertexShader": (FULL, "d9cap.c:920,421", "VSH bytecode+hash"), "CreatePixelShader": (FULL, "d9cap.c:920,421", "PSH bytecode+hash"),
 "CreateDevice": (PART, "d9cap.c:523-541,505-521", "DEV keeps backbuffer w/h/format + auto-depth format only; lost: Adapter, DeviceType, hFocusWindow, BehaviorFlags, Windowed, MS, SwapEffect, interval, BackBufferCount, refresh, hDeviceWindow, PP flags"),
 "Reset": (CNT, "d9cap.c:571-577", "DEV re-emitted (w/h/fmt only); PP lost; return to default state not journaled"),
 "Present": (PART, "d9cap.c:578-590", "PRES only; lost: src/dst rect, window override, dirty region"),
 "CreateTexture": (PART, "d9cap.c:597-603,386-399", "TEX; lost: requested Levels (0 -> actual), Usage except RT/DS bit, Pool (sysmem/scratch: no record), SharedHandle"),
 "CreateCubeTexture": (PART, "d9cap.c:604-610", "TEX cube; lost: Usage, Pool, requested Levels, SharedHandle"),
 "CreateVertexBuffer": (PART, "d9cap.c:611-620", "VB id,size; lost: Usage, FVF, Pool"),
 "CreateIndexBuffer": (PART, "d9cap.c:621-630", "IB id,size,format; lost: Usage, Pool"),
 "CreateRenderTarget": (PART, "d9cap.c:631-637", "SURF; lost: MultiSample, quality, Lockable"),
 "CreateDepthStencilSurface": (PART, "d9cap.c:638-644", "SURF; lost: MultiSample, quality, Discard"),
 "CreateOffscreenPlainSurface": (PART, "d9cap.c:645-652", "SURF only if Pool==DEFAULT; lost: Pool, other pools unrecorded"),
 "UpdateSurface": (PART, "d9cap.c:653-669", "only whole-surface copy into a non-RT texture level -> TEXU; else counted"),
 "UpdateTexture": (PART, "d9cap.c:670-686", "contents -> TEXU/TEXC from sysmem source (whole levels); src/dst ids, dirty regions lost; else counted"),
 "ProcessVertices": (CNT, "d9cap.c:872-879", "counted only; no record"),
 "BeginStateBlock": (CNT, "d9cap.c:766-772", "counted; everything Set* until EndStateBlock is NOT journaled"),
 "EndStateBlock": (FWD, "d9cap.c:773-778", "state block unwrapped; Apply later unseen"),
 "CreateStateBlock": (FWD, "d9cap.c:forwarder", "block unwrapped; contents (captured state) unseen"),
 "GetRenderTargetData": (FWD, "d9cap.c:688-690", "changes sysmem surface contents; unjournaled and not counted"),
 "GetFrontBufferData": (FWD, "d9cap.c:691-693", "changes sysmem surface contents; unjournaled, not counted"),
 "SetFVF": (FWD, "d9cap.c:1318", "no SFVF record: FVF draws replay with stale SDCL; not counted (README claims counted: mismatch)"),
 "CreateAdditionalSwapChain": (FWD, "d9cap.c:549-554", "swap wrapped, no record; PP lost; its Present not journaled"),
 "CreateVolumeTexture": (FWD, "d9cap.c:forwarder", "volume textures unwrapped; no TEX; Lock/Unlock contents unseen"),
 "CreateQuery": (FWD, "d9cap.c:forwarder", "query unwrapped"),
 "DrawRectPatch": (FWD, "d9cap.c:forwarder", "pixels produced but neither journaled nor counted"),
 "DrawTriPatch": (FWD, "d9cap.c:forwarder", "pixels produced but neither journaled nor counted"),
})
EXEC_OK = {"CreateDevice", "Reset", "CreateTexture", "CreateCubeTexture", "CreateVertexBuffer", "CreateIndexBuffer",
           "CreateRenderTarget", "CreateDepthStencilSurface", "CreateOffscreenPlainSurface", "CreateVertexDeclaration",
           "CreateVertexShader", "CreatePixelShader", "SetRenderState", "SetSamplerState", "SetTexture", "SetVertexShader",
           "SetPixelShader", "SetVertexDeclaration", "SetStreamSource", "SetIndices", "SetRenderTarget",
           "SetDepthStencilSurface", "SetViewport", "SetScissorRect", "Clear", "DrawPrimitive", "DrawIndexedPrimitive",
           "DrawPrimitiveUP", "DrawIndexedPrimitiveUP", "Present", "UpdateTexture", "UpdateSurface", "Unlock",
           "UnlockRect"} | set(CONSTS)
EXEC_NOTE = {"SetStreamSource": "stream 0 only (d3d9_exec.cpp:699)", "SetRenderTarget": "index 0 only (:701)",
             "DrawPrimitive": "Skip(): fixed function, POSITIONT, FVF, missing stream (:300-337)"}
STATE_NULL = {"GetDirect3D", "GetCreationParameters", "GetDisplayMode", "Reset", "ResetEx", "CreateAdditionalSwapChain",
              "GetSwapChain", "GetBackBuffer", "GetPresentParameters", "SetRenderTarget", "GetRenderTarget",
              "SetDepthStencilSurface", "GetDepthStencilSurface", "SetViewport", "GetViewport", "SetScissorRect",
              "GetScissorRect", "SetRenderState", "GetRenderState", "SetSamplerState", "GetSamplerState",
              "SetTextureStageState", "GetTextureStageState", "SetTransform", "GetTransform", "SetTexture", "GetTexture",
              "SetStreamSource", "GetStreamSource", "SetStreamSourceFreq", "GetStreamSourceFreq", "SetIndices",
              "GetIndices", "SetVertexDeclaration", "GetVertexDeclaration", "SetFVF", "GetFVF", "SetVertexShader",
              "GetVertexShader", "SetPixelShader", "GetPixelShader", "SetSoftwareVertexProcessing",
              "GetSoftwareVertexProcessing", "SetNPatchMode", "GetNPatchMode", "GetContainer", "GetDeclaration",
              "GetDevice", "GetLevelDesc", "GetDesc", "GetLevelCount", "GetNumberOfSwapChains"}   # gen_null_d3d9.py:102-215,245-260
SCRATCH = {"Lock", "LockRect", "LockBox"}
GET_PREFIX = ("Get", "Check", "Enum", "Test", "Validate")


def d9cap(i, m, slot):
    if i in NOTWRAP: return NW, "d9cap.c:20-21,1174", "interface passes through unwrapped (Ex: QueryInterface refuses, Create9Ex=E_NOTIMPL)"
    if slot < 3: return FWD, "d9cap.c:1174-1190", "refcount/QI forwarded to the real object; no record"
    if i == "IDirect3DDevice9" and m in D: return D[m]
    if i == "IDirect3D9": return (PART, *D["CreateDevice"][1:]) if m == "CreateDevice" else (FWD, "d9cap.c:forwarders", "caps/format/mode answers forwarded from real d3d9; not recorded")
    if i == "IDirect3DSwapChain9" and m == "Present": return PART, "d9cap.c:996-1002", "PRES only for the implicit chain; lost: rects, window, dirty region, flags; other chains no record"
    if m in ("LockRect", "UnlockRect", "Lock", "Unlock") and i in ("IDirect3DTexture9", "IDirect3DCubeTexture9", "IDirect3DSurface9", "IDirect3DVertexBuffer9", "IDirect3DIndexBuffer9"):
        if m.startswith("Lock"): return PART, "d9cap.c:1013-1160", "contents emitted at Unlock (whole level / locked range); lost: rect, flags (DISCARD/NOOVERWRITE/READONLY), returned pointer+pitch; READONLY, sysmem and non-texture surfaces: no record"
        return FULL, "d9cap.c:1030-1160", "TEXU/TEXC/VBU/IBU carry the written bytes; RT-texture/surface locks counted_unjournaled"
    if m in ("GetSurfaceLevel", "GetCubeMapSurface"): return PART, "d9cap.c:1010,1050", "SURF(tex id,level,face) once at first call; later calls and AddRef not recorded"
    if m in ("GenerateMipSubLevels", "SetLOD", "SetAutoGenFilterType", "AddDirtyRect", "GetDC", "ReleaseDC", "SetPriority", "PreLoad", "SetPrivateData", "FreePrivateData"):
        return FWD, "d9cap.c:generated forwarders", "forwarded; may change contents/state invisibly" if m in ("GenerateMipSubLevels", "GetDC", "ReleaseDC") else "forwarded"
    return FWD, "d9cap.c:generated forwarders" if i == "IDirect3DDevice9" else "d9cap.c:1174+", "forwarded; no record"


def result(st, m, ret, i):
    if st == NW: return "no"
    if st in (FULL, PART) and ret == "HRESULT": return "success-only (call is journaled iff HRESULT succeeded; value/failure not recorded)"
    return "no"


def exe(i, m, st):
    if i in NOTWRAP and i != "IDirect3DVolume9" or i == "IDirect3DVolume9": return "absent", "no records for this interface"
    if m in EXEC_OK and i in ("IDirect3DDevice9", "IDirect3D9", "IDirect3DSwapChain9", "IDirect3DTexture9", "IDirect3DCubeTexture9", "IDirect3DSurface9", "IDirect3DVertexBuffer9", "IDirect3DIndexBuffer9"):
        if m in ("Reset", "CreateDevice") and i != "IDirect3DDevice9" and i != "IDirect3D9": return "absent", ""
        if m == "Present" and i == "IDirect3DSwapChain9": return "executed", "PRES for implicit chain"
        if m == "CreateDevice" and i == "IDirect3D9": return "executed", "DEV d3d9_exec.cpp:539"
        if i != "IDirect3DDevice9" and m not in ("Unlock", "UnlockRect"): return "absent", ""
        return "executed", EXEC_NOTE.get(m, "d3d9_exec.cpp:539-717")
    if i == "IDirect3DDevice9" and m == "SetTextureStageState": return "recorded_unused", "TSS: case kD9TSState: break (:673)"
    if i == "IDirect3DDevice9" and m in ("StretchRect", "ColorFill", "SetStreamSourceFreq"): return "skipped", "STRC/CFIL/SFRQ unknown tags skipped (default: break :718); nxd9play.c executes them"
    return "absent", ""


def null(i, m, ret, slot):
    if slot < 3: return "returns constant", "QI returns this for any IID; refcount real (gen_null_d3d9.py:614-616)"
    if m in SCRATCH: return "real state kept", "one persistent scratch buffer per object; level/rect/box not distinguished"
    if m in STATE_NULL or (m.startswith("Create") and "IDirect3D" in ret + "x"): pass
    if m in STATE_NULL: return "real state kept", ""
    if m.startswith("Create") or m == "CreateDevice": return "real state kept", "returns new Null_ object carrying size/format/usage; CreateDevice hooks native renderer"
    if m in S_UNIMPL.get(i, ()): return "returns constant", "NULL_UNIMPL note (gen_null_d3d9.py:82-88)"
    return "returns constant", "S_OK / 0 / canned caps; Set* value not stored" if m.startswith("Set") else "S_OK / 0 / canned answer"

S_UNIMPL = {"IDirect3DDevice9": {"DrawPrimitive", "DrawIndexedPrimitive", "DrawPrimitiveUP", "DrawIndexedPrimitiveUP", "DrawRectPatch", "DrawTriPatch", "Clear", "StretchRect", "ColorFill", "UpdateSurface", "UpdateTexture", "GetRenderTargetData", "GetFrontBufferData", "ProcessVertices", "Present"}, "IDirect3DSwapChain9": {"Present", "GetFrontBufferData"}, "IDirect3DSurface9": {"GetDC", "ReleaseDC"}}

if __name__ == "__main__":
    rows = S.universe(S.parse())
    print("interface\tslot\tmethod\tret\tnparams\tstatus_d9cap\tresult_recorded\tstatus_exec\tstatus_null\tevidence\tnotes\tparams")
    for i, slot, m, ret, n, ps in rows:
        st, ev, note = d9cap(i, m, slot)
        ex, exn = exe(i, m, st)
        nl, nln = null(i, m, ret, slot)
        print("\t".join(map(str, [i, slot, m, ret, n, st, result(st, m, ret, i), ex, nl, f"{ev}; exec:{exn}; null:{nln}", note, ps])))
