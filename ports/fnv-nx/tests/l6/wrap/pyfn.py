"""pyfn.py: python model of the D3D9 length-rule vocabulary (independent of the wrapper's C++), for evaluating the spec rules in the test."""
import os, struct, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "render", "d3d9", "spec"))
import d3d9_expr as X                                              # noqa: E402

FMT = {"D3DFMT_A8R8G8B8": 21, "D3DFMT_R5G6B5": 23, "D3DFMT_DXT1": 0x31545844, "D3DFMT_INDEX16": 101, "D3DFMT_INDEX32": 102}   # SDK values
PROFILE_FMTS = [21, 0x31545844, 23]


def make_fns(spec, pd_iunk, hr):
    t = spec["tables"]; fm = {FMT[k]: v for k, v in t["format"].items() if k in FMT}; pe = {v: k for k, v in t["prim_enum"].items()}
    w = t["sm_walk"]; op = w["opcodes"]

    def sm_bytes(b):
        ver = struct.unpack_from("<I", b, 0)[0]; major = (ver >> 8) & 255; minor = ver & 255; i = 1
        while True:
            tok = struct.unpack_from("<I", b, 4 * i)[0]; o = tok & 0xFFFF
            if o == op["D3DSIO_END"]: return 4 * (i + 1)
            if o == op["D3DSIO_COMMENT"]: i += 1 + ((tok >> 16) & 0x7FFF); continue
            names = [k for k, v in op.items() if v == o]
            n = (tok >> 24) & 15 if major >= 2 else w["params_lt_2"][names[0]]
            i += 1 + n

    def rect(b, i, d, base=0):
        return d if b is None else struct.unpack_from("<4i", b)[i]
    fn = {
        "verts": lambda ty, n: 0 if n == 0 else n * t["primitive"][pe[ty]][0] + t["primitive"][pe[ty]][1],
        "index_size": lambda f: {101: 2, 102: 4}.get(f, 0), "sm_bytes": sm_bytes,
        "decl_bytes": lambda b: next(8 * (i + 1) for i in range(len(b) // 8) if struct.unpack_from("<H", b, 8 * i)[0] == 0xFF),
        "str_bytes": lambda b: b.index(0) + 1, "wstr_bytes": lambda b: 2 * (next(i for i in range(0, len(b), 2) if b[i:i + 2] == b"\0\0") // 2 + 1),
        "u32": lambda b, o: struct.unpack_from("<I", b, o)[0], "ok": lambda x: x if hr >= 0 else 0, "ok0": lambda x: x if hr == 0 else 0,
        "pd_iunknown": lambda g: 1 if pd_iunk.get(bytes(g)) else 0, "nonnull": lambda p: 0 if p is None else 1,
        "min": min, "max": max, "if": lambda c, a, b: a if c else b, "eq": lambda a, b: int(a == b), "band": lambda a, b: a & b,
        "lvl": lambda d, l: max(1, d >> l) if l < 32 else 1, "rows": lambda f, h: -(-h // fm[f][1]), "rowbytes": lambda f, wd: -(-wd // fm[f][0]) * fm[f][2],
        "rect_w": lambda r, d: d if r is None else struct.unpack_from("<4i", r)[2] - struct.unpack_from("<4i", r)[0],
        "rect_h": lambda r, d: d if r is None else struct.unpack_from("<4i", r)[3] - struct.unpack_from("<4i", r)[1],
        "box_w": lambda r, d: d if r is None else struct.unpack_from("<6I", r)[2] - struct.unpack_from("<6I", r)[0],
        "box_h": lambda r, d: d if r is None else struct.unpack_from("<6I", r)[3] - struct.unpack_from("<6I", r)[1],
        "box_d": lambda r, d: d if r is None else struct.unpack_from("<6I", r)[5] - struct.unpack_from("<6I", r)[4]}
    assert set(fn) == set(X.FUNCS), set(X.FUNCS) ^ set(fn)
    return fn


def ev(spec, s, env, pd_iunk=None, hr=0):
    """evaluate rule s: env maps parameter names / 'this.P' / '*p' / '*p@out' (header constants come from tables.constants)."""
    e = dict(spec["tables"]["constants"]); e.update(env)
    return X.evaluate(X.parse(s), e, make_fns(spec, pd_iunk or {}, hr))


def refs(m):
    """parameter names used by any rule of method m (len/off/lock/canon/in_when)."""
    import re
    s = " ".join(str(v) for p in m["params"] for k, v in ([("len", p.get("len", "")), ("off", p.get("off", "")), ("in_when", p.get("in_when", ""))] +
                 list(p.get("lock", {}).items()) + list(p.get("canon", {}).items())) if isinstance(v, str))
    return {n for n in re.findall(r"[A-Za-z_][A-Za-z_0-9]*", s)}
