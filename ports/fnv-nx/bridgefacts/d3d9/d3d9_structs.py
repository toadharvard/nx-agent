"""d3d9_structs.py: parse the struct and enum definitions of the shim headers and lay the structs out under the
x86 SDK rules, which AArch64-ILP32 reproduces: every pointer/HANDLE/HWND/long is 4 bytes, the d3d9 headers are
`#pragma pack(push, 4)` (member alignment = min(natural, 4)), win32 types are laid out at their natural alignment
(LARGE_INTEGER 8). The result is cross-checked by tests/l6/test_spec.py against a g++ -m32 probe of the headers."""
import os, re

HDRS = ("d3d9types.h", "d3d9caps.h", "d3d9.h")
WIN_STRUCTS = ("GUID", "POINT", "RECT", "PALETTEENTRY", "RGNDATAHEADER", "RGNDATA", "LUID")
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
# shapes the C parser does not read (a union with a 64-bit member, a 4x4 float matrix): fields are (name, type, count), laid out sequentially under the member
# alignment of their struct; UNION_WITH names the type of the member the SDK overlays on them, whose size and alignment the record gives.
HAND = {
    "LARGE_INTEGER": ([("LowPart", "DWORD", 1), ("HighPart", "LONG", 1)], "LONGLONG"),
    "ULARGE_INTEGER": ([("LowPart", "DWORD", 1), ("HighPart", "DWORD", 1)], "ULONGLONG"),
    "D3DMATRIX": ([("m", "float", 16)], None),
}
FLOATS = ("float", "double", "long double")


class Base:
    """The base types of the headers, derived and not stated: the record is DATAMODEL.json `types` (size, align, and signed for the integral types, of each fundamental type, pointer and enum:
    the one data model); a name of the headers is a typedef chain (`typedef <type> NAME`, a pointer declarator or a handle macro `#define M(n) ... typedef ... *n`)
    that ends in one of them. `unsigned T` is T's size and alignment, unsigned. A name that has no chain is not a base type (`in` is False; `[]` raises)."""

    def __init__(self, dm, td, ptr):
        self.types = {t["type"]: t for t in dm["types"]}
        self.td, self.ptr, self.memo = td, ptr, {}

    def entry(self, kind):
        t = self.types[kind]
        return t["size"], t["align"]

    def resolve(self, name, seen=()):
        if name in self.memo: return self.memo[name]
        r = None
        if name in self.ptr:
            r = (self.types["pointer"]["size"], self.types["pointer"]["align"], False, "ohandle")
        elif name in self.td and name not in seen:
            r = self.words(self.td[name].split(), seen + (name,))
        else:
            r = self.words(name.split(), seen)
        self.memo[name] = r
        return r

    def words(self, ws, seen):
        ws = [w for w in ws if w != "const"]
        if len(ws) == 1 and (ws[0] in self.td or ws[0] in self.ptr): return self.resolve(ws[0], seen)
        uns = "unsigned" in ws
        rest = [w for w in ws if w not in ("unsigned", "signed")]
        base = " ".join(rest) if rest else "int"
        if "signed" in ws and base == "char": base = "signed char"
        t = self.types.get(base)
        if t is None or t["type"] in ("pointer", "enum"): return None
        kind = "float" if base in FLOATS else "int"
        return t["size"], t["align"], (kind == "float" or (not uns and t["signed"])), kind   # `signed` is in the record for the integral types only; a floating type is signed by its kind

    def __contains__(self, name):
        return self.resolve(name) is not None

    def __getitem__(self, name):
        r = self.resolve(name)
        if r is None: raise SystemExit("d3d9_structs: %r has no typedef chain to a base type of DATAMODEL.json types" % name)
        return r[0], r[2], r[3]   # (size, signed, kind)

    def align(self, name):
        return self.resolve(name)[1]


def typedefs(srcs):
    """(typedef name -> its target type words, the names that are pointers) of the comment-stripped SRCS: `typedef <type> N1, *N2, ...;` without a struct body, an enum
    body or a function declarator, and every use `M(NAME)` of a macro whose definition ends in `typedef ... *n` (a handle declaration)."""
    td, ptr = {}, set()
    for src in srcs:
        for m in re.finditer(r"#\s*define\s+(\w+)\(\s*(\w+)\s*\)[^\n]*typedef[^\n;]*\*\s*\2\s*$", src, re.M):
            ptr.update(x.group(1) for x in re.finditer(r"^\s*%s\(\s*(\w+)\s*\)\s*;" % m.group(1), src, re.M))
        for m in re.finditer(r"(?m)^\s*typedef\s+([^;{}()#]+);", src):
            parts = [p.strip() for p in m.group(1).split(",")]
            h = re.match(r"^(.*?)\s*(\*?)\s*(\w+)$", parts[0])
            if not h or not h.group(1):
                continue
            ty = h.group(1)
            if h.group(2): ptr.add(h.group(3))
            else: td[h.group(3)] = ty
            for p in parts[1:]:
                dd = re.match(r"^(\*?)\s*(\w+)$", p)
                if dd and dd.group(1): ptr.add(dd.group(2))
                elif dd: td[dd.group(2)] = ty
    return td, ptr


def strip(t):
    t = re.sub(r"/\*.*?\*/", " ", t, flags=re.S)
    return re.sub(r"//[^\n]*", " ", t)


def text(inc, f):
    return strip(open(os.path.join(inc, f)).read())


def pack_map(src):
    """[(offset, pack)] of the `#pragma pack` lines of SRC after comment stripping: pack(push, N) pushes N, pack(pop) pops, pack(N) sets; None = no pragma in force (natural
    alignment). The member alignment of a struct is min(natural, pack) at its position (pack_at), read from the headers, stated nowhere else."""
    st, out = [None], []
    for m in re.finditer(r"#\s*pragma\s+pack\s*\(([^)]*)\)", src):
        a = [x.strip() for x in m.group(1).split(",") if x.strip()]
        if a and a[0] == "push": st.append(int(a[1]) if len(a) > 1 else st[-1])
        elif a and a[0] == "pop": st.pop()
        elif a: st[-1] = int(a[0])
        else: st[-1] = None
        out.append((m.start(), st[-1]))
    return out


def pack_at(pm, pos):
    cur = None
    for off, p in pm:
        if off < pos: cur = p
    return cur


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


def load(inc, dm):
    t = {f: text(inc, f) for f in HDRS + ("fnvnx_win32.h",)}
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
        pm = pack_map(src)
        for m in re.finditer(r"typedef\s+struct\s*\w*\s*\{", src):
            body, end = body_of(src, m.end() - 1)
            names = [x.strip() for x in src[end:src.index(";", end)].split(",")]
            nm = names[0]
            if (filt is None or nm in filt) and nm not in raw and "{" not in body and nm not in HAND:
                raw[nm] = (body, pack_at(pm, m.start()))
    return {"enums": enums, "consts": consts, "raw": raw, "base": Base(dm, *typedefs(list(t.values())))}


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
    enums, consts, raw, base = db["enums"], db["consts"], db["raw"], db["base"]
    done = {}

    def dims(arr):
        c = 1
        for d in re.findall(r"\[([^\]]+)\]", arr):
            c *= int(d) if d.isdigit() else consts[d]
        return c

    def build(name):
        if name in done: return done[name]
        if name in HAND:
            hf, union = HAND[name]
            pos, al, fl = 0, 1, []
            for fname, ty, cnt in hf:
                sz, sg, k = base[ty]
                fl.append(dict(name=fname, off=pos, size=sz * cnt, kind=k, **({"signed": sg} if k != "float" else {})))
                pos, al = pos + sz * cnt, max(al, base.align(ty))
            if union: pos, al = max(pos, base[union][0]), max(al, base.align(union))
            done[name] = {"size": pos + (-pos) % al, "align": al, "fields": fl}
            return done[name]
        body, pack = raw[name]
        pos, al, fields = 0, 1, []
        for ty, fname, arr in decl_fields(body):
            n = dims(arr) if arr else 1
            f = {"name": fname}
            ty = ty.replace("const ", "").strip()
            if "*" in ty:
                (esz, ea), f["kind"] = base.entry("pointer"), "ptr"
            elif ty in enums:
                (esz, ea), f["kind"], f["enum"] = base.entry("enum"), "enum", ty
            elif ty in raw or ty in HAND:
                sub = build(ty)
                esz, ea, f["kind"], f["struct"] = sub["size"], sub["align"], "struct", ty
            elif ty in base:
                esz, sg, k = base[ty]
                ea = base.align(ty)
                f["kind"] = "char" if (ty == "char" and arr) else k
                if f["kind"] == "int": f["signed"] = sg
                fam = FLAG_FIELDS.get((name, fname))
                if fam: f["kind"], f["flags"] = "flags", fam; f.pop("signed", None)
            else:
                raise SystemExit(f"d3d9_structs: unknown type {ty!r} in {name}.{fname}")
            if pack: ea = min(ea, pack)
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
