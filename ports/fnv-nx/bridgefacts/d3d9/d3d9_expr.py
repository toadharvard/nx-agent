"""d3d9_expr.py: the closed expression language of the D3D9 spec (length, offset and extent rules). Syntax:
  expr  := add ; add := mul (('+'|'-') mul)* ; mul := una (('*'|'/') una)* ; una := '-'? atom
  atom  := NUMBER | '*'NAME['@out'] | NAME | 'this.'PROP | FUNC '(' [expr (',' expr)*] ')' | '(' expr ')'
  NAME   a parameter of the same method, or a header constant (D3D... macro / enumerator)
  *NAME  the scalar a pointer parameter points to, BEFORE the call ('@out': AFTER the call)
  this.P a property of the object the method is called on (OBJ_PROPS)
All values are unsigned 64-bit integers, '/' is integer division (divisor 0 -> 0). Nothing else is allowed."""
import re

OBJ_PROPS = {"length": "VB/IB Length", "fmt": "D3DFORMAT of the surface/volume/index buffer", "width": "level 0 (or surface) width",
             "height": "level 0 (or surface) height", "depth": "level 0 (or volume) depth", "levels": "level count",
             "usage": "D3DUSAGE_* of creation", "pool": "D3DPOOL of creation", "shader_bytes": "sm_bytes() of the bytecode given to Create*Shader",
             "decl_bytes": "decl_bytes() of the elements given to CreateVertexDeclaration", "data_size": "query_type table size of the CreateQuery type"}
# name -> (arity, meaning). The generator implements exactly these.
FUNCS = {
    "verts": (2, "vertex count of primitive_count primitives of D3DPRIMITIVETYPE; 0 when count is 0 (tables.primitive)"),
    "index_size": (1, "2 for D3DFMT_INDEX16, 4 for D3DFMT_INDEX32"),
    "sm_bytes": (1, "bytes of the shader token stream at ptr: version token .. END token 0x0000FFFF inclusive (tables.sm_walk)"),
    "decl_bytes": (1, "8 * elements of D3DVERTEXELEMENT9 at ptr up to and including the first with Stream==0xFF"),
    "str_bytes": (1, "bytes of the NUL-terminated 8-bit string at ptr, terminator included"),
    "wstr_bytes": (1, "bytes of the NUL-terminated UTF-16 string at ptr, terminator included"),
    "u32": (2, "little-endian DWORD at byte offset (2nd arg) of the buffer at ptr"),
    "ok": (1, "arg if SUCCEEDED(hr) else 0 (only in rules evaluated after the call)"),
    "ok0": (1, "arg if hr == S_OK else 0 (S_FALSE and failures give 0)"),
    "pd_iunknown": (1, "1 if the private data stored on `this` under the GUID parameter was set with D3DSPD_IUNKNOWN (state from SetPrivateData)"),
    "nonnull": (1, "1 if the pointer parameter is not NULL"),
    "min": (2, "minimum"), "max": (2, "maximum"),
    "if": (3, "second arg when first is nonzero else third"),
    "eq": (2, "1 if equal else 0"), "band": (2, "bitwise and"),
    "lvl": (2, "max(1, dim >> level): size of a mip level"),
    "rows": (2, "rows (block rows) of an image h pixels high in D3DFORMAT fmt (tables.format)"),
    "rowbytes": (2, "bytes of one row (block row) of an image w pixels wide in D3DFORMAT fmt"),
    "rect_w": (2, "r.right-r.left of the RECT param, or the 2nd arg when the param is NULL"),
    "rect_h": (2, "r.bottom-r.top of the RECT param, or the 2nd arg when NULL"),
    "box_w": (2, "b.Right-b.Left of the D3DBOX param, or the 2nd arg when NULL"),
    "box_h": (2, "b.Bottom-b.Top of the D3DBOX param, or the 2nd arg when NULL"),
    "box_d": (2, "b.Back-b.Front of the D3DBOX param, or the 2nd arg when NULL"),
}
_TOK = re.compile(r"\s*(0[xX][0-9a-fA-F]+|\d+|[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)?|@out|[-+*/(),])")


def tokens(s):
    out, pos = [], 0
    while pos < len(s):
        m = _TOK.match(s, pos)
        if not m:
            if s[pos:].strip():
                raise ValueError(f"bad character at {pos} in {s!r}")
            break
        out.append(m.group(1)); pos = m.end()
    return out


def parse(s):
    t = tokens(s)
    i = [0]

    def peek():
        return t[i[0]] if i[0] < len(t) else None

    def take(x=None):
        v = peek()
        if v is None or (x is not None and v != x):
            raise ValueError(f"expected {x!r} got {v!r} in {s!r}")
        i[0] += 1
        return v

    def atom():
        v = take()
        if v == "(":
            e = add(); take(")"); return e
        if v == "*":
            n = take()
            if not re.match(r"[A-Za-z_]\w*$", n):
                raise ValueError(f"bad deref in {s!r}")
            out = peek() == "@out"
            if out: take()
            return ("deref", n, out)
        if re.match(r"(0[xX][0-9a-fA-F]+|\d+)$", v):
            return ("num", int(v, 0))
        if peek() == "(":
            take("(")
            args = []
            if peek() != ")":
                args.append(add())
                while peek() == ",":
                    take(","); args.append(add())
            take(")")
            return ("call", v, args)
        if not re.match(r"[A-Za-z_]", v):
            raise ValueError(f"unexpected {v!r} in {s!r}")
        return ("name", v)

    def una():
        if peek() == "-":
            take(); return ("neg", atom())
        return atom()

    def mul():
        e = una()
        while peek() in ("*", "/"):
            op = take(); e = ("bin", op, e, una())
        return e

    def add():
        e = mul()
        while peek() in ("+", "-"):
            op = take(); e = ("bin", op, e, mul())
        return e

    e = add()
    if peek() is not None:
        raise ValueError(f"trailing {peek()!r} in {s!r}")
    return e


def walk(e, acc):
    k = e[0]
    if k == "name": acc["names"].add(e[1])
    elif k == "deref": acc["names"].add(e[1])
    elif k == "call":
        acc["funcs"].append((e[1], len(e[2])))
        for a in e[2]: walk(a, acc)
    elif k == "bin": walk(e[2], acc); walk(e[3], acc)
    elif k == "neg": walk(e[1], acc)
    return acc


def check(s, params, consts, post=False):
    """Errors of rule s for a method with parameter names `params`; consts: set of header constant names."""
    try:
        acc = walk(parse(s), {"names": set(), "funcs": []})
    except ValueError as ex:
        return [str(ex)]
    errs = []
    for n in sorted(acc["names"]):
        if n.startswith("this."):
            if n[5:] not in OBJ_PROPS: errs.append(f"unknown object property {n}")
        elif n not in params and n not in consts:
            errs.append(f"unknown name {n}")
    for f, ar in acc["funcs"]:
        if f not in FUNCS: errs.append(f"unknown function {f}")
        elif FUNCS[f][0] != ar: errs.append(f"{f}: arity {ar} != {FUNCS[f][0]}")
        elif f in ("ok", "ok0") and not post: errs.append(f"{f} outside an output rule")
    return errs


def evaluate(e, env, fn):
    """env: name -> int (also 'this.P' and '*name' / '*name@out' keys); fn: FUNCS name -> python callable."""
    k = e[0]
    if k == "num": return e[1]
    if k == "name": return env[e[1]]
    if k == "deref": return env[f"*{e[1]}@out" if e[2] else f"*{e[1]}"]
    if k == "neg": return -evaluate(e[1], env, fn)
    if k == "call": return fn[e[1]](*[evaluate(a, env, fn) for a in e[2]])
    a, b = evaluate(e[2], env, fn), evaluate(e[3], env, fn)
    return {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b, "/": lambda: a // b if b else 0}[e[1]]()
