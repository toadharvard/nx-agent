#!/usr/bin/env python3
"""spec_extract.py OUT.json: derives platform/com/com_spec.json from the platform's own interface declarations.

The COM interfaces the platform implements are C++ abstract classes (shim/include/dsound.h, dinput.h, platform/audio/
dsound_fx.h, dshow_int.h); this tool reads their `virtual` declarations in vtable order (a base class's methods first) and writes
the interface spec in the vocabulary of the Direct3D 9 spec of the render lead (iface / name / slot / ret / ret_kind /
params with ctype, kind, byref, dir, size), plus the kinds `callback` and `giface` (documented in gen_combridge.py).

What this tool does NOT have to be right about, because the generated code proves it at build time (gen_combridge.py): the
method names, argument types and return types (a static_assert against the declaration's member-pointer type), the slot
numbers (a probe of the compiler's vtable layout, nxcom_selfcheck), and the word class of every argument (static_asserts on
the C++ type: pointer/integral for a word, float, double, 64-bit integer).

What it decides, from the declared C++ types: a parameter whose type names a spec interface (`IPin_ *`, `IBaseFilter_ **`) is an
interface pointer (`**`: out); REFIID/REFGUID are references to a GUID; float/double/64-bit integers have their own slot
classes; everything else is one 32-bit word (a scalar or a guest address). The untyped `void **` parameters that carry an
interface are the ones named in OVERLAY: one entry per interface class, stating what the COM contract says the pointer holds
(MSDN), never per call site."""
import json, re, sys, os

HERE = os.path.dirname(os.path.abspath(__file__))
SHIM = os.path.join(HERE, "..", "..", "shim", "include")
AUDIO = os.path.join(HERE, "..", "audio")

# ---- the interfaces: spec name -> (header, C++ struct text name, cxx type, base spec name, family, IID symbol, extra IID symbols)
# family: ds (dsound.h: GUID layout), di (dinput.h), dsh (dshow_int.h: raw 16-byte Guid)
IFACES = [
    # name, header, struct, cxx, base, family, iid symbol, aliases
    ("IUnknownCom", "dsound.h", "IUnknown", "::IUnknown", None, "ds", None, []),
    ("Unk", "dshow_int.h", "Unk", "dsh::Unk", None, "dsh", None, []),
    ("IDirectSound", "dsound.h", "IDirectSound", "::IDirectSound", "IUnknownCom", "ds", "IID_IDirectSound", []),
    ("IDirectSound8", "dsound.h", "IDirectSound8", "::IDirectSound8", "IDirectSound", "ds", "IID_IDirectSound8", []),
    ("IDirectSoundBuffer", "dsound.h", "IDirectSoundBuffer", "::IDirectSoundBuffer", "IUnknownCom", "ds", "IID_IDirectSoundBuffer", []),
    ("IDirectSoundBuffer8", "dsound.h", "IDirectSoundBuffer8", "::IDirectSoundBuffer8", "IDirectSoundBuffer", "ds", "IID_IDirectSoundBuffer8", []),
    ("IDirectSound3DListener", "dsound.h", "IDirectSound3DListener", "::IDirectSound3DListener", "IUnknownCom", "ds", "IID_IDirectSound3DListener", []),
    ("IDirectSound3DBuffer", "dsound.h", "IDirectSound3DBuffer", "::IDirectSound3DBuffer", "IUnknownCom", "ds", "IID_IDirectSound3DBuffer", []),
    ("IDirectSoundNotify", "dsound.h", "IDirectSoundNotify", "::IDirectSoundNotify", "IUnknownCom", "ds", "IID_IDirectSoundNotify", []),
    ("IDirectSoundFXI3DL2Reverb", "dsound.h", "IDirectSoundFXI3DL2Reverb", "::IDirectSoundFXI3DL2Reverb", "IUnknownCom", "ds", "IID_IDirectSoundFXI3DL2Reverb8", []),
    ("IDirectSoundFXWavesReverb", "dsound.h", "IDirectSoundFXWavesReverb", "::IDirectSoundFXWavesReverb", "IUnknownCom", "ds", "IID_IDirectSoundFXWavesReverb8", []),
    ("IKsPropertySet", "dsound_fx.h", "KsProperty", "::KsProperty", "IUnknownCom", "ds", "IID_KsPropertySet", []),
    ("IDirectSoundFXChorus", "dsound_fx.h", "FxIf<T>:FxChorus", "::FxIf<::FxChorus>", "IUnknownCom", "ds", "IID_Chorus", []),
    ("IDirectSoundFXFlanger", "dsound_fx.h", "FxIf<T>:FxChorus", "::FxIf<::FxChorus>", "IUnknownCom", "ds", "IID_Flanger", []),
    ("IDirectSoundFXCompressor", "dsound_fx.h", "FxIf<T>:FxCompressor", "::FxIf<::FxCompressor>", "IUnknownCom", "ds", "IID_Compressor", []),
    ("IDirectSoundFXDistortion", "dsound_fx.h", "FxIf<T>:FxDistortion", "::FxIf<::FxDistortion>", "IUnknownCom", "ds", "IID_Distortion", []),
    ("IDirectSoundFXEcho", "dsound_fx.h", "FxIf<T>:FxEcho", "::FxIf<::FxEcho>", "IUnknownCom", "ds", "IID_Echo", []),
    ("IDirectSoundFXGargle", "dsound_fx.h", "FxIf<T>:FxGargle", "::FxIf<::FxGargle>", "IUnknownCom", "ds", "IID_Gargle", []),
    ("IDirectSoundFXParamEq", "dsound_fx.h", "FxIf<T>:FxParamEq", "::FxIf<::FxParamEq>", "IUnknownCom", "ds", "IID_ParamEq", []),
    ("IDirectInput8A", "dinput.h", "IDirectInput8A", "::IDirectInput8A", "IUnknownCom", "di", "IID_IDirectInput8A", []),
    ("IDirectInputDevice8A", "dinput.h", "IDirectInputDevice8A", "::IDirectInputDevice8A", "IUnknownCom", "di", "IID_IDirectInputDevice8A", []),
    ("IDirectInputEffect", "dinput.h", "IDirectInputEffect", "::IDirectInputEffect", "IUnknownCom", "di", None, []),
    # DirectShow (namespace dsh, raw 16-byte GUIDs): the COM surface of the filter graph
    ("IPersist", "dshow_int.h", "IPersist_", "dsh::IPersist_", "Unk", "dsh", "dsh::IID_IPersist_", []),
    ("IReferenceClock", "dshow_int.h", "IReferenceClock_", "dsh::IReferenceClock_", "Unk", "dsh", "dsh::IID_IReferenceClock_", []),
    ("IMediaFilter", "dshow_int.h", "IMediaFilter_", "dsh::IMediaFilter_", "IPersist", "dsh", "dsh::IID_IMediaFilter_", []),
    ("IBaseFilter", "dshow_int.h", "IBaseFilter_", "dsh::IBaseFilter_", "IMediaFilter", "dsh", "dsh::IID_IBaseFilter_", []),
    ("IPin", "dshow_int.h", "IPin_", "dsh::IPin_", "Unk", "dsh", "dsh::IID_IPin_", []),
    ("IEnumPins", "dshow_int.h", "IEnum_", "dsh::IEnum_", "Unk", "dsh", "dsh::IID_IEnumPins_", []),
    ("IEnumFilters", "dshow_int.h", "IEnum_", "dsh::IEnum_", "Unk", "dsh", "dsh::IID_IEnumFilters_", []),
    ("IEnumMediaTypes", "dshow_int.h", "IEnum_", "dsh::IEnum_", "Unk", "dsh", "dsh::IID_IEnumMediaTypes_", []),
    ("IEnumRegFilters", "dshow_int.h", "IEnum_", "dsh::IEnum_", "Unk", "dsh", "dsh::IID_IEnumRegFilters_", []),
    ("IFileSourceFilter", "dshow_int.h", "IFileSource_", "dsh::IFileSource_", "Unk", "dsh", "dsh::IID_IFileSourceFilter_", []),
    ("IAsyncReader", "dshow_int.h", "AsyncReader", "dsh::AsyncReader", "Unk", "dsh", "dsh::IID_IAsyncReader_", []),
    ("IFilterGraph", "dshow_int.h", "IFilterGraph_", "dsh::IFilterGraph_", "Unk", "dsh", "dsh::IID_IFilterGraph_", []),
    ("IGraphBuilder", "dshow_int.h", "IGraphBuilder_", "dsh::IGraphBuilder_", "IFilterGraph", "dsh", "dsh::IID_IGraphBuilder_", []),
    ("IFilterGraph2", "dshow_int.h", "IFilterGraph2_", "dsh::IFilterGraph2_", "IGraphBuilder", "dsh", "dsh::IID_IFilterGraph2_", []),
    ("IDispatch", "dshow_int.h", "IDispatch_", "dsh::IDispatch_", "Unk", "dsh", "dsh::IID_IDispatch_", []),
    ("IMediaControl", "dshow_int.h", "IMediaControl_", "dsh::IMediaControl_", "IDispatch", "dsh", "dsh::IID_IMediaControl_", []),
    ("IMediaEvent", "dshow_int.h", "IMediaEvent_", "dsh::IMediaEvent_", "IDispatch", "dsh", "dsh::IID_IMediaEvent_", []),
    ("IMediaEventEx", "dshow_int.h", "IMediaEventEx_", "dsh::IMediaEventEx_", "IMediaEvent", "dsh", "dsh::IID_IMediaEventEx_", []),
    ("IMediaEventSink", "dshow_int.h", "IMediaEventSink_", "dsh::IMediaEventSink_", "Unk", "dsh", "dsh::IID_IMediaEventSink_", []),
    ("IMediaPosition", "dshow_int.h", "IMediaPosition_", "dsh::IMediaPosition_", "IDispatch", "dsh", "dsh::IID_IMediaPosition_", []),
    ("IMediaSeeking", "dshow_int.h", "IMediaSeeking_", "dsh::IMediaSeeking_", "Unk", "dsh", "dsh::IID_IMediaSeeking_", []),
    ("IBasicAudio", "dshow_int.h", "IBasicAudio_", "dsh::IBasicAudio_", "IDispatch", "dsh", "dsh::IID_IBasicAudio_", []),
    ("IVideoWindow", "dshow_int.h", "IVideoWindow_", "dsh::IVideoWindow_", "IDispatch", "dsh", "dsh::IID_IVideoWindow_", []),
    ("IBasicVideo", "dshow_int.h", "IBasicVideo_", "dsh::IBasicVideo_", "IDispatch", "dsh", "dsh::IID_IBasicVideo_", []),
    ("IObjectWithSite", "dshow_int.h", "IObjectWithSite_", "dsh::IObjectWithSite_", "Unk", "dsh", "dsh::IID_IObjectWithSite_", []),
    ("IGraphVersion", "dshow_int.h", "IGraphVersion_", "dsh::IGraphVersion_", "Unk", "dsh", "dsh::IID_IGraphVersion_", []),
    ("IFilterMapper", "dshow_int.h", "IFilterMapper_", "dsh::IFilterMapper_", "Unk", "dsh", "dsh::IID_IFilterMapper_", []),
]
NAMES = {i[0] for i in IFACES}
# C++ struct names of the interfaces -> spec names (a parameter `IPin_ *` is an IPin; the first entry of a shared class wins:
# a class shared by several COM interfaces (IEnum_) is never the type of an interface parameter)
CXX2SPEC = {}
for n, h, st, cxx, b, f, iid, al in IFACES:
    CXX2SPEC.setdefault(st.split(":")[0], n)
CXX2SPEC["IEnum_"] = None
CXX2SPEC["Unk"] = "Unk"   # a parameter `Unk *` is the game's IUnknown, in DirectShow's spelling (raw 16-byte IIDs)
CXX2SPEC["LPUNKNOWN"] = "IUnknownCom"
CXX2SPEC["IUnknown"] = "IUnknownCom"
CXX2SPEC["KsProperty"] = "IKsPropertySet"

# ---- the untyped `void **` (and `void *`-valued) parameters that carry an interface, per COM interface (MSDN contract):
#   (spec interface, method) -> {param index: ("$param name" | interface name)}  -- an out pointer of that interface
#   "items" for the enumerators: Next fills an array of this item
OVERLAY = {
    ("IBaseFilter", "EnumPins"): {0: "IEnumPins"},
    ("IFilterGraph", "EnumFilters"): {0: "IEnumFilters"},
    ("IFilterGraph2", "EnumFilters"): {0: "IEnumFilters"},
    ("IGraphBuilder", "EnumFilters"): {0: "IEnumFilters"},
    ("IMediaControl", "AddSourceFilter"): {1: "IDispatch"},
    ("IMediaControl", "get_FilterCollection"): {0: "IDispatch"},
    ("IMediaControl", "get_RegFilterCollection"): {0: "IDispatch"},
    ("IFilterMapper", "EnumMatchingFilters"): {0: "IEnumRegFilters"},
    ("IObjectWithSite", "GetSite"): {1: "$iid"},
    ("IDirectSoundBuffer8", "GetObjectInPath"): {3: "$rguidInterface"},
}
ENUM_ITEMS = {"IEnumPins": ("handle", "IPin"), "IEnumFilters": ("handle", "IBaseFilter"),
              "IEnumMediaTypes": ("ptr", None), "IEnumRegFilters": ("ptr", None)}
ENUM_NEXT_NAMES = {}      # filled per interface below

IID_LIT = {   # IIDs the C++ headers do not name as a constant
    "IUnknownCom": ["00000000-0000-0000-c000-000000000046"],
    "Unk": ["00000000-0000-0000-c000-000000000046"],
    "IDirectInputDevice8A": ["5944e682-c92e-11cf-bfc7-444553540000", "57d7c6bc-2356-11d3-8e9d-00c04f6844ae"],   # IDirectInputDevice2A, 7A: prefixes of the 8A vtable
}
# the COM structs that carry interface pointers (MSDN: PIN_INFO.pFilter, FILTER_INFO.pGraph): member -> interface; the callee fills them
STRUCT_IFACE = {"PinInfo": [("filter", "IBaseFilter")], "FilterInfo": [("graph", "IFilterGraph")]}
FLOAT_T = {"float", "D3DVALUE", "FLOAT"}
DOUBLE_T = {"double", "REFTIME", "DOUBLE"}
INT64_T = {"LL", "int64_t", "LONGLONG", "ULONGLONG", "uint64_t", "REFERENCE_TIME", "long long"}
STRUCT_BYVAL = {"Guid": 16}
CB = {}
REF_T = {"REFIID": "GUID", "REFGUID": "GUID", "REFCLSID": "GUID"}


def die(msg):
    sys.stderr.write("spec_extract: " + msg + "\n")
    sys.exit(2)


def strip_comments(s):
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    return re.sub(r"//[^\n]*", "", s)


def struct_body(text, name):
    m = re.search(r"\bstruct\s+" + re.escape(name) + r"\b\s*(final\s*)?(:[^{;]*)?\{", text)
    if not m:
        die("struct %s not found" % name)
    i, depth = m.end(), 1
    while depth:
        c = text[i]
        depth += (c == "{") - (c == "}")
        i += 1
    return text[m.end():i - 1]


def split_params(s):
    out, depth, cur = [], 0, ""
    for c in s:
        if c in "(<":
            depth += 1
        if c in ")>":
            depth -= 1
        if c == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += c
    if cur.strip():
        out.append(cur.strip())
    return [] if out == ["void"] else out


def parse_methods(body, subst=None):
    body = strip_comments(body)
    ms = []
    for m in re.finditer(r"virtual\s+([\w:\s\*]+?)\s+(?:STDMETHODCALLTYPE\s+)?(\w+)\s*\(([^)]*)\)\s*=\s*0\s*;", body):
        ret, name, params = m.group(1).strip(), m.group(2), m.group(3)
        ps = []
        for k, p in enumerate(split_params(params)):
            if subst:
                p = re.sub(r"\bT\b", subst, p)
            mm = re.match(r"^(.*?[\s\*&])(\w+)$", p)
            # a trailing identifier is the parameter name unless the whole text is one type word (unnamed `uint32_t`)
            if mm and not re.match(r"^(const\s+)?[\w:]+\s*[\*&]*$", p.strip()):
                ctype, pname = mm.group(1).strip(), mm.group(2)
            else:
                ctype, pname = p.strip(), "a%d" % k
            ctype = re.sub(r"\s+", " ", ctype).replace(" *", " *")
            ps.append((pname, ctype))
        ms.append((name, ret, ps))
    return ms


def norm(ctype):
    t = ctype.replace("const", "").replace("struct", "")
    return re.sub(r"\s+", "", t)


def classify(spec_if, mname, k, pname, ctype, iface_ov, family):
    """one parameter -> spec dict"""
    p = {"name": pname, "ctype": ctype}
    t = norm(ctype)
    base = t.rstrip("*&")
    stars = t.count("*")
    if t in REF_T:
        p.update(kind="struct", byref=True, struct=REF_T[t], dir="in", size=16)
        return p
    if t in STRUCT_BYVAL:
        p.update(kind="struct", byref=False, struct=t, dir="in", size=STRUCT_BYVAL[t])
        return p
    if iface_ov is not None:
        p.update(kind="handle", byref=True, iface=iface_ov, dir="out", size=4)
        return p
    sp = CXX2SPEC.get(base)
    if sp and stars in (1, 2):
        if stars == 1:
            p.update(kind="giface" if base in ("IBaseFilter_", "IPin_", "IReferenceClock_", "IFilterGraph_", "Unk", "LPUNKNOWN") or
                     family == "dsh" else "handle", byref=False, iface=sp, dir="in", size=4)
            if base == "LPUNKNOWN":
                p["kind"] = "giface"
        else:
            p.update(kind="handle", byref=True, iface=sp, dir="out", size=4)
        return p
    if base == "LPUNKNOWN" and stars == 0:
        p.update(kind="giface", byref=False, iface="IUnknownCom", dir="in", size=4)
        return p
    if stars == 0 and base in CB:
        p.update(kind="callback", byref=False, dir="in", size=4, sig=CB[base])
        return p
    if stars == 0 and base in FLOAT_T:
        p.update(kind="float", byref=False, dir="in", size=4)
    elif stars == 0 and base in DOUBLE_T:
        p.update(kind="double", byref=False, dir="in", size=8)
    elif stars == 0 and base in INT64_T:
        p.update(kind="int64", byref=False, dir="in", size=8)
    elif stars or re.match(r"^(LP|P)[A-Z]", base) or "&" in t:
        p.update(kind="buffer", byref=True, dir="in", size=4)
        if base in STRUCT_IFACE:   # a struct the callee fills that carries interface pointers (PIN_INFO, FILTER_INFO)
            p.update(dir="out", struct=base, members=[{"name": m, "iface": i} for m, i in STRUCT_IFACE[base]])
    else:
        p.update(kind="value", byref=False, dir="in", size=4)
    if p["kind"] == "value" and re.match(r"^(ULONG|DWORD|UINT|uint\d+_t|unsigned.*|WORD|UINT_PTR|uintptr_t)$", base):
        p["signed"] = False
    elif p["kind"] == "value":
        p["signed"] = True
    return p


def ret_of(r):
    text = re.sub(r"\s+", " ", r.strip())
    r = norm(r)
    if r == "void":
        return "void", "void"
    if r in DOUBLE_T:
        return text, "double"
    if r in FLOAT_T:
        return text, "float"
    return text, "hresult" if r in ("HRESULT", "HR") else "value"


def main(out):
    texts = {}
    CB.clear()
    for h in ("dsound.h", "dinput.h"):
        texts[h] = open(os.path.join(SHIM, h)).read()
    for h in ("dsound_fx.h", "dshow_int.h"):
        texts[h] = open(os.path.join(AUDIO, h)).read()
    for h, t in texts.items():
        for m in re.finditer(r"FNVNX_CALLBACK_T\((\w+),\s*(\w+),\s*\(([^)]*)\)\)", t):
            CB[m.group(1)] = "%s(%s)" % (m.group(2), m.group(3).strip())
    by_name = {}
    own = {}   # spec iface -> list of own methods (declaration order)
    for n, h, st, cxx, base, fam, iid, al in IFACES:
        if ":" in st:
            tmpl, arg = st.split(":")
            body = struct_body(texts[h], tmpl.split("<")[0])
            own[n] = parse_methods(body, arg)
        else:
            own[n] = parse_methods(struct_body(texts[h], st))
    # the dshow video interfaces (strmif/control.h order) are declared in dshow_int.h as IVideoWindow_ / IBasicVideo_
    interfaces, methods = {}, []

    def full(n):
        row = [i for i in IFACES if i[0] == n][0]
        b = row[4]
        return (full(b) if b else []) + own[n]

    for n, h, st, cxx, base, fam, iid, al in IFACES:
        # IEnumPins & co share one C++ class; the `IUnknown` base of a family is declared per family struct
        ms = full(n)
        if n == "IUnknownCom":
            ms = own[n]
        interfaces[n] = {"base": base, "cxx": cxx, "family": fam, "iids": ([iid] if iid else []) + IID_LIT.get(n, []), "hidden": n == "Unk", "alias": "IUnknownCom" if n == "Unk" else None,
                         # the interface a pointer of this one may be used as (every interface is an IUnknown)
                         "parent": base if base else ("IUnknownCom" if n != "IUnknownCom" else None), "slots": len(ms), "header": h}
        for slot, (mname, ret, ps) in enumerate(ms):
            ov = OVERLAY.get((n, mname), {})
            params = []
            for k, (pname, ctype) in enumerate(ps):
                iface_ov = ov.get(k)
                if mname == "QueryInterface" and k == 1:
                    iface_ov = "$" + ps[0][0]
                if n in ENUM_ITEMS and mname == "Next" and k == 1:
                    kind, item = ENUM_ITEMS[n]
                    q = {"name": pname, "ctype": ctype, "dir": "out", "byref": True, "size": 4, "count": [ps[0][0], ps[2][0]]}
                    if kind == "handle":
                        q.update(kind="handle_array", iface=item)
                    else:
                        q.update(kind="buffer")
                    params.append(q)
                    continue
                if n in ENUM_ITEMS and mname == "Clone" and k == 0:
                    iface_ov = n
                if iface_ov is not None and iface_ov.startswith("$"):
                    iface_ov = iface_ov
                p = classify(n, mname, k, pname, ctype, iface_ov, fam)
                if p.get("iface", "").startswith("$"):
                    ref = p["iface"][1:]
                    if ref not in [x[0] for x in ps]:
                        die("%s::%s: %s names no parameter" % (n, mname, p["iface"]))
                    p["null"] = True
                params.append(p)
            r, rk = ret_of(ret)
            m = {"iface": n, "name": mname, "slot": slot, "params": params, "ret": r, "ret_kind": rk}
            if slot < 3 and mname in ("QueryInterface", "AddRef", "Release"):
                m["role"] = {"QueryInterface": "qi", "AddRef": "addref", "Release": "release"}[mname]
            methods.append(m)
    exports = [
        {"dll": "dsound.dll", "name": "DirectSoundCreate", "ordinal": 1, "ret": "HRESULT", "ret_kind": "hresult",
         "decl": "HRESULT (*)(LPCGUID, LPDIRECTSOUND *, LPUNKNOWN)", "params": [
            {"name": "pcGuidDevice", "ctype": "LPCGUID", "kind": "buffer", "byref": True, "dir": "in", "size": 4},
            {"name": "ppDS", "ctype": "LPDIRECTSOUND *", "kind": "handle", "byref": True, "iface": "IDirectSound", "dir": "out", "size": 4},
            {"name": "pUnkOuter", "ctype": "LPUNKNOWN", "kind": "giface", "byref": False, "iface": "IUnknownCom", "dir": "in", "size": 4}]},
        {"dll": "dsound.dll", "name": "DirectSoundCreate8", "ordinal": 11, "ret": "HRESULT", "ret_kind": "hresult",
         "decl": "HRESULT (*)(LPCGUID, LPDIRECTSOUND8 *, LPUNKNOWN)", "params": [
            {"name": "pcGuidDevice", "ctype": "LPCGUID", "kind": "buffer", "byref": True, "dir": "in", "size": 4},
            {"name": "ppDS8", "ctype": "LPDIRECTSOUND8 *", "kind": "handle", "byref": True, "iface": "IDirectSound8", "dir": "out", "size": 4},
            {"name": "pUnkOuter", "ctype": "LPUNKNOWN", "kind": "giface", "byref": False, "iface": "IUnknownCom", "dir": "in", "size": 4}]},
        {"dll": "dsound.dll", "name": "DirectSoundEnumerateA", "ordinal": 2, "ret": "HRESULT", "ret_kind": "hresult",
         "decl": "HRESULT (*)(LPDSENUMCALLBACKA, LPVOID)", "params": [
            {"name": "pDSEnumCallback", "ctype": "LPDSENUMCALLBACKA", "kind": "callback", "byref": False, "dir": "in", "size": 4,
             "sig": "BOOL(LPGUID, LPCSTR, LPCSTR, LPVOID)"},
            {"name": "pContext", "ctype": "LPVOID", "kind": "buffer", "byref": True, "dir": "in", "size": 4}]},
        {"dll": "dinput8.dll", "name": "DirectInput8Create", "ordinal": "", "ret": "HRESULT", "ret_kind": "hresult",
         "decl": "HRESULT (*)(HINSTANCE, DWORD, REFIID, LPVOID *, LPUNKNOWN)", "params": [
            {"name": "hinst", "ctype": "HINSTANCE", "kind": "value", "byref": False, "dir": "in", "size": 4, "signed": False},
            {"name": "dwVersion", "ctype": "DWORD", "kind": "value", "byref": False, "dir": "in", "size": 4, "signed": False},
            {"name": "riidltf", "ctype": "REFIID", "kind": "struct", "struct": "GUID", "byref": True, "dir": "in", "size": 16},
            {"name": "ppvOut", "ctype": "LPVOID *", "kind": "handle", "byref": True, "iface": "$riidltf", "dir": "out", "size": 4, "null": True},
            {"name": "punkOuter", "ctype": "LPUNKNOWN", "kind": "giface", "byref": False, "iface": "IUnknownCom", "dir": "in", "size": 4}]},
        {"dll": "ole32.dll", "name": "CoCreateInstance", "ordinal": "", "ret": "HRESULT", "ret_kind": "hresult",
         "decl": "HRESULT (*)(const GUID *, void *, DWORD, const GUID *, LPVOID *)", "params": [
            {"name": "rclsid", "ctype": "const GUID *", "kind": "buffer", "byref": True, "dir": "in", "size": 4},
            {"name": "pUnkOuter", "ctype": "void *", "kind": "buffer", "byref": True, "dir": "in", "size": 4},
            {"name": "dwClsContext", "ctype": "DWORD", "kind": "flags", "byref": False, "dir": "in", "size": 4, "signed": False},
            {"name": "riid", "ctype": "const GUID *", "kind": "buffer", "byref": True, "dir": "in", "size": 4, "iid": True},
            {"name": "ppv", "ctype": "LPVOID *", "kind": "handle", "byref": True, "iface": "$riid", "dir": "out", "size": 4, "null": True}]},
    ]
    spec = {"schema": "fnvnx-com-spec/1",
            "headers": ["dsound.h", "dinput.h", "dsound_fx.h", "dshow_int.h"],
            "interfaces": interfaces, "methods": methods, "exports": exports}
    with open(out, "w") as f:
        json.dump(spec, f, indent=1, sort_keys=True)
        f.write("\n")
    print("spec_extract: %d interfaces, %d methods, %d exports" % (len(interfaces), len(methods), len(exports)))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "com_spec.json"))
