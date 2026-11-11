"""d3d9_structs.py: parse the struct and enum definitions of the shim headers and lay the structs out under the
x86 SDK rules, which AArch64-ILP32 reproduces: every pointer/HANDLE/HWND/long is 4 bytes, the d3d9 headers are
`#pragma pack(push, 4)` (member alignment = min(natural, 4)), win32 types are laid out at their natural alignment
(LARGE_INTEGER 8). The result is cross-checked by tests/l6/test_spec.py against a g++ -m32 probe of the headers."""
import os, re

INC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "shim", "include")
HDRS = ("d3d9types.h", "d3d9caps.h", "d3d9.h")
WIN_STRUCTS = ("GUID", "POINT", "RECT", "PALETTEENTRY", "RGNDATAHEADER", "RGNDATA", "LUID")
# base type -> (size, signed, kind)
BASE = {}
for _n in ("char", "signed char"): BASE[_n] = (1, True, "int")
for _n in ("unsigned char", "BYTE"): BASE[_n] = (1, False, "int")
for _n in ("short", "SHORT"): BASE[_n] = (2, True, "int")
for _n in ("unsigned short", "WORD"): BASE[_n] = (2, False, "int")
for _n in ("int", "INT", "LONG", "long", "BOOL"): BASE[_n] = (4, True, "int")
for _n in ("unsigned int", "UINT", "DWORD", "ULONG", "unsigned long", "D3DCOLOR", "UINT32", "DWORD_PTR"): BASE[_n] = (4, False, "int")   # DWORD_PTR: pointer-sized, 4 bytes on x86 / ILP32
BASE["float"] = (4, True, "float")
for _n in ("LONGLONG", "__int64"): BASE[_n] = (8, True, "int")
for _n in ("ULONGLONG", "UINT64"): BASE[_n] = (8, False, "int")
for _n in ("HWND", "HANDLE", "HDC", "HMONITOR"): BASE[_n] = (4, False, "ohandle")
# (struct, field) -> flags family; the family prefix table is FAMILIES
CAPS_FLAGS = {"Caps": "D3DCAPS", "Caps2": "D3DCAPS2", "Caps3": "D3DCAPS3", "PresentationIntervals": "D3DPRESENT_INTERVAL",
              "CursorCaps": "D3DCURSORCAPS", "DevCaps": "D3DDEVCAPS", "PrimitiveMiscCaps": "D3DPMISCCAPS", "RasterCaps": "D3DPRASTERCAPS",
              "ZCmpCaps": "D3DPCMPCAPS", "AlphaCmpCaps": "D3DPCMPCAPS", "SrcBlendCaps": "D3DPBLENDCAPS", "DestBlendCaps": "D3DPBLENDCAPS",
              "ShadeCaps": "D3DPSHADECAPS", "TextureCaps": "D3DPTEXTURECAPS", "TextureFilterCaps": "D3DPTFILTERCAPS",
              "CubeTextureFilterCaps": "D3DPTFILTERCAPS", "VolumeTextureFilterCaps": "D3DPTFILTERCAPS", "VertexTextureFilterCaps": "D3DPTFILTERCAPS",
              "StretchRectFilterCaps": "D3DPTFILTERCAPS", "TextureAddressCaps": "D3DPTADDRESSCAPS", "VolumeTextureAddressCaps": "D3DPTADDRESSCAPS",
              "LineCaps": "D3DLINECAPS", "StencilCaps": "D3DSTENCILCAPS", "FVFCaps": "D3DFVFCAPS", "TextureOpCaps": "D3DTEXOPCAPS",
              "VertexProcessingCaps": "D3DVTXPCAPS", "DevCaps2": "D3DDEVCAPS2", "DeclTypes": "D3DDTCAPS"}
FLAG_FIELDS = {("D3DCAPS9", k): v for k, v in CAPS_FLAGS.items()}
FLAG_FIELDS.update({("D3DPRESENT_PARAMETERS", "Flags"): "D3DPRESENTFLAG", ("D3DDEVICE_CREATION_PARAMETERS", "BehaviorFlags"): "D3DCREATE",
                    ("D3DSURFACE_DESC", "Usage"): "D3DUSAGE", ("D3DVOLUME_DESC", "Usage"): "D3DUSAGE",
                    ("D3DVERTEXBUFFER_DESC", "Usage"): "D3DUSAGE", ("D3DINDEXBUFFER_DESC", "Usage"): "D3DUSAGE",
                    ("D3DVERTEXBUFFER_DESC", "FVF"): "D3DFVF"})
FAMILIES = {"D3DCLEAR": "D3DCLEAR_", "D3DCREATE": "D3DCREATE_", "D3DCURSOR": "D3DCURSOR_", "D3DENUM": "D3DENUM_",
            "D3DFVF": "D3DFVF_", "D3DGETDATA": "D3DGETDATA_", "D3DISSUE": "D3DISSUE_", "D3DLOCK": "D3DLOCK_",
            "D3DPRESENT": "D3DPRESENT_", "D3DPRESENTFLAG": "D3DPRESENTFLAG_", "D3DPV": "D3DPV_", "D3DSGR": "D3DSGR_",
            "D3DSPD": "D3DSPD_", "D3DUSAGE": "D3DUSAGE_"}
FAMILIES.update({v: v + "_" for v in set(CAPS_FLAGS.values())})
HAND = {  # shapes the C parser does not read: anonymous unions / flexible arrays / the SDK's 64-bit union
    "LARGE_INTEGER": (8, 8, [("LowPart", 0, 4, "int", False), ("HighPart", 4, 4, "int", True)]),
    "ULARGE_INTEGER": (8, 8, [("LowPart", 0, 4, "int", False), ("HighPart", 4, 4, "int", False)]),
    "D3DMATRIX": (64, 4, [("m", 0, 64, "float", True)]),
}


def strip(t):
    t = re.sub(r"/\*.*?\*/", " ", t, flags=re.S)
    return re.sub(r"//[^\n]*", " ", t)


def text(f):
    return strip(open(os.path.join(INC, f)).read())


def body_of(t, start):
    d, i = 0, start
    while True:
        if t[i] == "{": d += 1
        elif t[i] == "}":
            d -= 1
            if d == 0: return t[start + 1:i], i + 1
        i += 1


def split_top(t):
    """Comma split outside parentheses (MAKEFOURCC('A', 'B', 'C', 'D') is one item)."""
    out, d, cur = [], 0, []
    for ch in t:
        d += (ch == "(") - (ch == ")")
        if ch == "," and d == 0: out.append("".join(cur)); cur = []
        else: cur.append(ch)
    return out + ["".join(cur)]


def load():
    t = {f: text(f) for f in HDRS + ("fnvnx_win32.h",)}
    allt = "\n".join(t[h] for h in HDRS)
    consts = {}
    for m in re.finditer(r"#define\s+(\w+)\s+([0-9xXa-fA-F() <>|+*-]+?)[LlUu]*\s*$", allt, re.M):
        try: consts[m.group(1)] = int(eval(re.sub(r"(?<=[0-9a-fA-F])[LlUu]+\b", "", m.group(2)), {}, {}))
        except Exception: pass
    enums, nextv = {}, 0
    for m in re.finditer(r"typedef\s+enum\s*\w*\s*\{", allt):
        body, end = body_of(allt, m.end() - 1)
        name = re.match(r"\s*(\w+)", allt[end:]).group(1)
        names, v = [], -1
        for item in split_top(re.sub(r"#\w+[^\n]*", " ", body)):
            item = item.strip()
            if not item: continue
            if "=" in item:
                n, e = [x.strip() for x in item.split("=", 1)]
                try: v = int(eval(re.sub(r"(?<=[0-9a-fA-F])[LlUu]+\b", "", e), {}, dict(consts)))
                except Exception: v += 1
            else:
                n = item; v += 1
            names.append(n); consts[n] = v
        enums[name] = names
    for m in re.finditer(r"#define\s+(\w+)\s+\(([A-Za-z0-9_ +*-]+)\)\s*$", allt, re.M):
        try: consts.setdefault(m.group(1), int(eval(m.group(2), {}, dict(consts))))
        except Exception: pass
    raw = {}
    for src, filt in ((allt, None), (t["fnvnx_win32.h"], WIN_STRUCTS)):
        for m in re.finditer(r"typedef\s+struct\s*\w*\s*\{", src):
            body, end = body_of(src, m.end() - 1)
            names = [x.strip() for x in src[end:src.index(";", end)].split(",")]
            nm = names[0]
            if (filt is None or nm in filt) and nm not in raw and "{" not in body and nm not in HAND:
                raw[nm] = (body, filt is not None)
    return {"enums": enums, "consts": consts, "raw": raw}


def decl_fields(body):
    out = []
    for stmt in re.sub(r"(?m)^\s*#.*$", "", body).split(";"):
        stmt = re.sub(r"\s+\[", "[", " ".join(stmt.split()))
        if not stmt: continue
        parts = [p.strip() for p in stmt.split(",")]
        head = parts[0]
        m = re.match(r"(.*?)\s*(\w+)((?:\[[^\]]+\])*)$", head)
        ty, names = m.group(1).strip(), [(m.group(2), m.group(3))]
        for p in parts[1:]:
            mm = re.match(r"(\w+)((?:\[[^\]]+\])*)$", p)
            names.append((mm.group(1), mm.group(2)))
        for n, arr in names:
            out.append((ty, n, arr))
    return out


def layout(db):
    """-> {name: {size, align, fields[]}} for every parsed struct plus HAND ones"""
    enums, consts, raw = db["enums"], db["consts"], db["raw"]
    done = {}

    def dims(arr):
        c = 1
        for d in re.findall(r"\[([^\]]+)\]", arr):
            c *= int(d) if d.isdigit() else consts[d]
        return c

    def build(name):
        if name in done: return done[name]
        if name in HAND:
            size, al, fl = HAND[name]
            done[name] = {"size": size, "align": al, "fields": [dict(name=a, off=b, size=c, kind=k, **({"signed": s} if k != "float" else {})) for a, b, c, k, s in fl]}
            return done[name]
        body, win = raw[name]
        pos, al, fields = 0, 1, []
        for ty, fname, arr in decl_fields(body):
            n = dims(arr) if arr else 1
            f = {"name": fname}
            ty = ty.replace("const ", "").strip()
            if "*" in ty:
                esz, ea, f["kind"] = 4, 4, "ptr"
            elif ty in enums:
                esz, ea, f["kind"], f["enum"] = 4, 4, "enum", ty
            elif ty in raw or ty in HAND:
                sub = build(ty)
                esz, ea, f["kind"], f["struct"] = sub["size"], sub["align"], "struct", ty
            elif ty in BASE:
                esz, sg, k = BASE[ty]
                ea = esz
                f["kind"] = "char" if (ty == "char" and arr) else k
                if f["kind"] == "int": f["signed"] = sg
                fam = FLAG_FIELDS.get((name, fname))
                if fam: f["kind"], f["flags"] = "flags", fam; f.pop("signed", None)
            else:
                raise SystemExit(f"d3d9_structs: unknown type {ty!r} in {name}.{fname}")
            if not win: ea = min(ea, 4)
            if pos % ea: fields.append({"name": f"_pad{pos}", "off": pos, "size": ea - pos % ea, "kind": "pad"}); pos += ea - pos % ea
            f.update(off=pos, size=esz * n)
            if arr: f["count"] = n
            fields.append(f); pos += esz * n; al = max(al, ea)
        if pos % al: fields.append({"name": f"_pad{pos}", "off": pos, "size": al - pos % al, "kind": "pad"}); pos += al - pos % al
        done[name] = {"size": pos, "align": al, "fields": fields}
        if name == "RGNDATA":
            [f for f in fields if f["name"] == "Buffer"][0]["flex"] = "Buffer holds rdh.nCount RECTs; sizeof counts one char"
        return done[name]

    for n in sorted(list(raw) + list(HAND)): build(n)
    return {n: done[n] for n in sorted(done)}
