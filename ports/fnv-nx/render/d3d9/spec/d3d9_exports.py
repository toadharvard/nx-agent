"""d3d9_exports.py: the export table of d3d9.dll (all __stdcall on x86). Signatures are the public SDK ones where documented
(Direct3DCreate9, Direct3DCreate9Ex, D3DPERF_*) and the ones read off the callers (DebugSetMute, Direct3DShaderValidatorCreate9: render/d3d9/dev/x1_validator.h); the rest is undocumented: nothing is guessed, `params` is left out."""

def _v(name, ctype, size=4, signed=False): return {"name": name, "ctype": ctype, "dir": "in", "kind": "value", "size": size, "signed": signed, "byref": False}
def _wstr(name): return {"name": name, "ctype": "LPCWSTR", "dir": "in", "kind": "buffer", "elem": 2, "len": f"wstr_bytes({name})", "null": True, "byref": False}


def _fn(name, ret, params, ret_kind="value", cls="det", why=None, **kw):
    d = {"name": name, "ret": ret, "ret_kind": ret_kind, "ret_cls": cls, "params": params, "callconv": "stdcall"}
    if why: d["ret_why"] = why
    d.update(kw)
    return d


def _undoc(name, note): return {"name": name, "undocumented": True, "note": note, "callconv": "stdcall"}


def exports():
    sdk = _v("SDKVersion", "UINT")
    ev = _wstr("wszName")
    col = _v("col", "D3DCOLOR")
    out = [
        _fn("Direct3DCreate9", "IDirect3D9*", [sdk], "handle", "env", "fails (NULL) when the runtime does not support the SDK version", iface="IDirect3D9", own="new"),
        _fn("Direct3DCreate9Ex", "HRESULT", [sdk, {"name": "ppD3D", "ctype": "IDirect3D9Ex**", "dir": "out", "kind": "handle", "iface": "IDirect3D9Ex", "own": "new",
                                                    "null": True, "byref": True, "cls": "det", "when": "always"}], "hresult", "env", "Ex runtime availability is a property of the environment"),
        _fn("D3DPERF_BeginEvent", "int", [col, ev], "value", "det", None, ret_size=4),
        _fn("D3DPERF_EndEvent", "int", [], "value", "det", None, ret_size=4),
        _fn("D3DPERF_GetStatus", "DWORD", [], "value", "env", "nonzero only while a PIX capture is attached to the process", ret_size=4),
        _fn("D3DPERF_QueryRepeatFrame", "BOOL", [], "value", "env", "TRUE only while a PIX capture replays frames", ret_size=4),
        _fn("D3DPERF_SetMarker", "void", [col, ev], "void"),
        _fn("D3DPERF_SetOptions", "void", [_v("dwOptions", "DWORD")], "void"),
        _fn("D3DPERF_SetRegion", "void", [col, ev], "void"),
        _undoc("DebugSetLevel", "export of the debug runtime; not in the SDK headers and absent from Wine; signature unknown"),
        _fn("DebugSetMute", "void", [], "void"),
        _fn("Direct3DShaderValidatorCreate9", "IDirect3DShaderValidator9*", [], "handle", "det", None, iface="IDirect3DShaderValidator9", own="new"),
        _undoc("PSGPError", "internal processor-specific-geometry-pipeline hook; not in the SDK; signature unknown"),
        _undoc("PSGPSampleTexture", "internal processor-specific-geometry-pipeline hook; not in the SDK; signature unknown"),
    ]
    return sorted(out, key=lambda e: e["name"])
