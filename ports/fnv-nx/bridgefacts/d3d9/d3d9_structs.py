"""d3d9_structs.py: parse the struct and enum definitions of the shim headers and lay the structs out under the
x86 SDK rules, which AArch64-ILP32 reproduces: every pointer/HANDLE/HWND/long is 4 bytes, the d3d9 headers are
`#pragma pack(push, 4)` (member alignment = min(natural, 4)), win32 types are laid out at their natural alignment
(LARGE_INTEGER 8). The result is cross-checked by tests/l6/test_spec.py against a g++ -m32 probe of the headers."""
import ast, os, re

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
FAMILIES.update({v: v + "_" for v in sorted(set(CAPS_FLAGS.values()))})
HAND = {  # shapes the C parser does not read: anonymous unions / flexible arrays / the SDK's 64-bit union
    "LARGE_INTEGER": (8, 8, [("LowPart", 0, 4, "int", False), ("HighPart", 4, 4, "int", True)]),
    "ULARGE_INTEGER": (8, 8, [("LowPart", 0, 4, "int", False), ("HighPart", 4, 4, "int", False)]),
    "D3DMATRIX": (64, 4, [("m", 0, 64, "float", True)]),
}


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
        a = [x.strip() for x in m[1].split(",") if x.strip()]
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


TOK = re.compile(r"""\s*(?:(?P<float>(?:\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?[fFlL]?|\d+[eE][-+]?\d+[fFlL]?|\d+[fF]\b)|(?P<int>0[xX][0-9a-fA-F]+[uUlL]*|\d+[uUlL]*)|(?P<char>'(?:\\.|[^'\\])')|(?P<str>"(?:\\.|[^"\\])*")|(?P<id>[A-Za-z_]\w*)|(?P<op><<|>>|<=|>=|==|!=|&&|\|\||[-+*/%&|^~!<>()?:,.\[\]{}=#]))""")
TYPE_WORDS = {"const", "unsigned", "signed", "struct", "enum", "union", "void", "char", "short", "int", "long", "float", "double", "extern", "static", "inline", "volatile"}
BIN = {"*": 10, "/": 10, "%": 10, "+": 9, "-": 9, "<<": 8, ">>": 8, "&": 5, "^": 4, "|": 3}
ESC = {"n": 10, "t": 9, "r": 13, "0": 0, "\\": 92, "'": 39, '"': 34}


def tokens(text):
    """[(kind, text)] of a C preprocessor line: float, int, char, str, id, op. A character that is no token stops the generator."""
    out, pos, text = [], 0, text.rstrip()
    while pos < len(text):
        m = TOK.match(text, pos)
        if not m: raise SystemExit("d3d9_structs: %r is outside the token grammar at %r" % (text, text[pos:pos + 10]))
        out.append((m.lastgroup, m[m.lastgroup])); pos = m.end()
    return out


def trunc(v, size, signed):
    v &= (1 << (8 * size)) - 1
    return v - (1 << (8 * size)) if signed and v >> (8 * size - 1) else v


class CEval:
    """The value of the integer constants of the SDK headers by the C grammar they use: integer and character literals, + - * / % << >> & ^ | with the C precedence, unary - + ~ !, parentheses,
    casts to an integer type (the base types of BASE and the typedef chains of the headers), object-like #define names, enum members and function-like #define macros (expanded from their own
    #define). Object-like macros: a body that is empty, holds a string or a floating literal, or begins with a type is not an integer constant (decided by the tokens); any other body must evaluate:
    a name with no value, a node outside this grammar or a malformed expression stops the generator."""
    def __init__(self, objs, funs, enums, typedefs, etypes):
        self.objs, self.funs, self.enums, self.td, self.et, self.memo, self.busy = objs, funs, enums, typedefs, etypes, {}, []

    def stop(self, msg): raise SystemExit("d3d9_structs: " + msg)

    def ctype(self, words):
        n = " ".join(words)
        while n not in BASE and n in self.td: n = self.td[n]
        if n in self.et: return (4, True, "int")   # an enum type is an int (32-bit, ILP32 and x86)
        return BASE[n] if n in BASE and BASE[n][2] == "int" else None

    def is_type_tokens(self, toks):
        return bool(toks) and all(k == "id" for k, _ in toks) and (self.ctype([t for _, t in toks]) is not None or toks[0][1] in TYPE_WORDS)

    def not_int(self, toks):
        """True when the object-like body TOKS is by its tokens no integer constant: empty, a string or floating literal, a top-level comma list, or begins with a type."""
        if not toks: return True
        if any(k in ("float", "str") for k, _ in toks): return True
        d = 0
        for k, t in toks:
            d += (t == "(") - (t == ")")
            if t == "," and d == 0: return True
        for i, (k, t) in enumerate(toks):   # a cast to a type that is no integer type (a pointer, a handle): an address constant, no integer constant
            if t == "(":
                j = i + 1
                while j < len(toks) and (toks[j][0] == "id" or toks[j][1] == "*"): j += 1
                inner = [x for x in toks[i + 1:j] if x[1] != "*"]
                if inner and j < len(toks) and toks[j][1] == ")" and inner[0][1] in TYPE_WORDS | set(BASE) | set(self.td) | self.et and (
                        toks[j - 1][1] == "*" or self.ctype([x for _, x in inner]) is None): return True
        return toks[0][0] == "id" and (toks[0][1] in TYPE_WORDS or toks[0][1] in BASE or toks[0][1] in self.td)

    def value(self, name):
        if name in self.memo: return self.memo[name]
        if name in self.busy: self.stop("the constant %s refers to itself" % name)
        self.busy.append(name)
        if name in self.enums:
            kind, arg = self.enums[name]
            v = self.expr(arg) if kind == "expr" else (self.value(arg) + 1 if arg else 0)
            if name in self.objs and not self.not_int(self.objs[name]) and self.expr(self.objs[name]) != v: self.stop("%s is an enum member and a #define of another value" % name)
        elif name in self.objs: v = self.expr(self.objs[name])
        else: self.stop("%s has no value (not a #define of an integer, not an enum member)" % name)
        self.busy.pop(); self.memo[name] = v
        return v

    def expr(self, toks):
        st = {"t": toks, "i": 0}
        v = self.binary(st, 0)
        if st["i"] != len(toks): self.stop("expression %r does not end at %r" % (" ".join(t for _, t in toks), toks[st["i"]][1]))
        return v

    def peek(self, st): return st["t"][st["i"]] if st["i"] < len(st["t"]) else ("end", "")

    def binary(self, st, lim):
        a = self.unary(st)
        while True:
            k, t = self.peek(st)
            if k != "op" or t not in BIN or BIN[t] <= lim: return a
            st["i"] += 1; b = self.binary(st, BIN[t])
            if t in "/%" and b == 0: self.stop("division by zero in a constant expression")
            a = {"*": lambda: a * b, "/": lambda: abs(a) // abs(b) * (1 if (a < 0) == (b < 0) else -1), "%": lambda: abs(a) % abs(b) * (-1 if a < 0 else 1), "+": lambda: a + b, "-": lambda: a - b,
                 "<<": lambda: a << b, ">>": lambda: a >> b, "&": lambda: a & b, "^": lambda: a ^ b, "|": lambda: a | b}[t]()

    def unary(self, st):
        k, t = self.peek(st)
        if k == "end": self.stop("an operand is missing")
        st["i"] += 1
        if k == "int": return int(t.rstrip("uUlL"), 16 if t[:2] in ("0x", "0X") else 8 if len(t.rstrip("uUlL")) > 1 and t[0] == "0" else 10)
        if k == "char":
            b = t[1:-1]
            if b[0] != "\\": return ord(b)
            if b[1] in ESC and len(b) == 2: return ESC[b[1]]
            self.stop("character literal %s is outside the grammar" % t)
        if k == "op" and t in ("-", "+", "~", "!"):
            v = self.unary(st)
            return {"-": -v, "+": v, "~": ~v, "!": int(not v)}[t]
        if k == "op" and t == "(":
            j, d = st["i"], 1
            while d and j < len(st["t"]): d += (st["t"][j][1] == "(") - (st["t"][j][1] == ")"); j += 1
            if d: self.stop("an unbalanced parenthesis")
            inner = st["t"][st["i"]:j - 1]
            if self.is_type_tokens(inner):
                ty = self.ctype([x for _, x in inner])
                if ty is None: self.stop("a cast to %s: not an integer type of the table" % " ".join(x for _, x in inner))
                st["i"] = j; return trunc(self.unary(st), ty[0], ty[1])
            v = self.expr(inner); st["i"] = j; return v
        if k == "id":
            if self.peek(st)[1] == "(" and t in self.funs:
                params, body = self.funs[t]; body = tokens(body); st["i"] += 1
                args, cur, d = [], [], 1
                while d:
                    if st["i"] >= len(st["t"]): self.stop("the call of %s is not closed" % t)
                    x = st["t"][st["i"]]; st["i"] += 1
                    d += (x[1] == "(") - (x[1] == ")")
                    if d and not (x[1] == "," and d == 1): cur.append(x)
                    elif d == 0 or x[1] == ",": args.append(cur); cur = []
                if args == [[]] and not params: args = []
                if len(args) != len(params): self.stop("%s takes %d arguments, %d given" % (t, len(params), len(args)))
                if t in self.busy: self.stop("the macro %s calls itself" % t)
                sub = {p: [("op", "(")] + a + [("op", ")")] for p, a in zip(params, args)}
                self.busy.append(t); v = self.expr([y for x in body for y in (sub[x[1]] if x[0] == "id" and x[1] in sub else [x])]); self.busy.pop()
                return v
            if t in self.funs: self.stop("the function-like macro %s is used without arguments" % t)
            return self.value(t)
        self.stop("token %r is outside the grammar of constant expressions" % t)


def macros(allt):
    """({name: tokens} of the object-like #defines, {name: (params, body text)} of the function-like ones, in file order; the first #define of a name holds, like `#ifndef` guards)."""
    objs, funs = {}, {}
    for m in re.finditer(r"^[ \t]*#[ \t]*define[ \t]+(\w+)(\([^)]*\))?[ \t]*(.*)$", allt, re.M):
        if m[1] in objs or m[1] in funs: continue
        if m[2]: funs[m[1]] = ([x.strip() for x in m[2][1:-1].split(",") if x.strip()], m[3])   # tokenized at its first call: the body of a declaring macro (`##`, `;`) is no expression
        else: objs[m[1]] = tokens(m[3])
    return objs, funs


def typedef_names(src):
    """{new name: old type words} of the `typedef <type> NAME;` lines of SRC (struct / enum / function typedefs have no integer type: they are not matched)."""
    return {m[2]: " ".join(m[1].split()) for m in re.finditer(r"\btypedef\s+((?:unsigned\s+|signed\s+|const\s+)?[A-Za-z_][\w ]*?)\s+(\w+)\s*;", src)}


def strip_cont(t): return t.replace("\\\n", " ")


def load(inc):
    t = {f: text(inc, f) for f in HDRS + ("fnvnx_win32.h",)}
    allt = "\n".join(t[h] for h in HDRS)
    objs, funs = macros(strip_cont(allt))   # the constants of the d3d9 headers
    wobjs, wfuns = macros(strip_cont(t["fnvnx_win32.h"]))   # the Win32 shim header: its macros give values to the names the d3d9 constants use (S_OK, MAKEFOURCC), they are no constants of the table
    td = typedef_names(allt + "\n" + t["fnvnx_win32.h"])
    edefs, enums = {}, {}
    for m in re.finditer(r"typedef\s+enum\s*\w*\s*\{", allt):
        body, end = body_of(allt, m.end() - 1)
        name = re.match(r"\s*(\w+)", allt[end:])[1]
        names, prev = [], None
        for item in split_top(re.sub(r"#\w+[^\n]*", " ", body)):
            item = item.strip()
            if not item: continue
            if "=" in item:
                n, e = [x.strip() for x in item.split("=", 1)]
                edefs[n] = ("expr", tokens(e))
            else:
                n = item; edefs[n] = ("next", prev)
            names.append(n); prev = n
        enums[name] = names
    ev = CEval({**wobjs, **objs}, {**wfuns, **funs}, edefs, td, set(enums))
    consts = {n: ev.value(n) for n in objs if not ev.not_int(objs[n])}
    consts.update({n: ev.value(n) for n in edefs})
    raw = {}
    for src, filt in ((allt, None), (t["fnvnx_win32.h"], WIN_STRUCTS)):
        pm = pack_map(src)
        for m in re.finditer(r"typedef\s+struct\s*\w*\s*\{", src):
            body, end = body_of(src, m.end() - 1)
            names = [x.strip() for x in src[end:src.index(";", end)].split(",")]
            nm = names[0]
            if (filt is None or nm in filt) and nm not in raw and "{" not in body and nm not in HAND:
                raw[nm] = (body, pack_at(pm, m.start()))
    return {"enums": enums, "consts": consts, "raw": raw}


def decl_fields(body):
    out = []
    for stmt in re.sub(r"(?m)^\s*#.*$", "", body).split(";"):
        stmt = re.sub(r"\s+\[", "[", " ".join(stmt.split()))
        if not stmt: continue
        parts = [p.strip() for p in stmt.split(",")]
        head = parts[0]
        m = re.match(r"(.*?)\s*(\w+)((?:\[[^\]]+\])*)$", head)
        ty, names = m[1].strip(), [(m[2], m[3])]
        for p in parts[1:]:
            mm = re.match(r"(\w+)((?:\[[^\]]+\])*)$", p)
            names.append((mm[1], mm[2]))
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
        body, pack = raw[name]
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
