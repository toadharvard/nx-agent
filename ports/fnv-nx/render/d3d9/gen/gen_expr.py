"""gen_expr.py: compiles the closed length-rule language (d3d9_expr.py) to C++ uint64_t expressions. Unknown names/functions raise GenError."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "spec"))
import d3d9_expr as X


class GenError(Exception):
    pass


# function -> (cpp name, pointer-argument positions)
FN = {"verts": ("d9g_verts", ()), "index_size": ("d9g_index_size", ()), "sm_bytes": ("d9g_sm_bytes", (0,)), "decl_bytes": ("d9x_decl_bytes", (0,)),
      "str_bytes": ("d9x_str_bytes", (0,)), "wstr_bytes": ("d9x_wstr_bytes", (0,)), "u32": ("d9x_u32", (0,)), "min": ("d9x_min", ()), "max": ("d9x_max", ()),
      "lvl": ("d9x_lvl", ()), "rows": ("d9g_rows", ()), "rowbytes": ("d9g_rowbytes", ()), "rect_w": ("d9x_rect_w", (0,)), "rect_h": ("d9x_rect_h", (0,)),
      "box_w": ("d9x_box_w", (0,)), "box_h": ("d9x_box_h", (0,)), "box_d": ("d9x_box_d", (0,))}
SPECIAL = {"ok", "ok0", "pd_iunknown", "nonnull", "if", "eq", "band"}
PROP = {"length": "D9P_LENGTH", "fmt": "D9P_FMT", "width": "D9P_WIDTH", "height": "D9P_HEIGHT", "depth": "D9P_DEPTH", "levels": "D9P_LEVELS", "usage": "D9P_USAGE",
        "pool": "D9P_POOL", "shader_bytes": "D9P_SHADER_BYTES", "decl_bytes": "D9P_DECL_BYTES", "data_size": "D9P_DATA_SIZE"}


class Ctx:
    """params: name -> param dict; consts: tables.constants; props: set of props the object's interface can answer (None = no object)."""
    def __init__(self, m, spec, props, post, what):
        self.p = {q["name"]: q for q in m.get("params", [])}; self.consts = spec["tables"]["constants"]; self.props = props
        self.post = post; self.what = what; self.hr = m.get("ret_kind") == "hresult"; self.pre = set(); self.used_props = set()

    def ptr(self, e):
        if e[0] != "name" or e[1] not in self.p:
            raise GenError(f"{self.what}: pointer argument must be a parameter")
        q = self.p[e[1]]
        return f"(const void*)&{e[1]}" if q["ctype"] in ("REFIID", "REFGUID") else f"(const void*){e[1]}"

    def num(self, e):
        k = e[0]
        if k == "num": return f"UINT64_C({e[1]})"
        if k == "name":
            n = e[1]
            if n.startswith("this."):
                pr = n[5:]
                if self.props is None or pr not in self.props: raise GenError(f"{self.what}: no source for object property {n}")
                self.used_props.add(pr); return f"d9g_prop(o, {PROP[pr]})"
            if n in self.p:
                q = self.p[n]
                if q["kind"] in ("value", "enum", "flags") and q.get("size", 4) == 4 and not q["byref"]:
                    return f"(uint64_t)(int64_t)({n})" if q.get("signed") else f"(uint64_t)(uint32_t)({n})"
                raise GenError(f"{self.what}: parameter {n} of kind {q['kind']} used as a number")
            if n in self.consts: return f"(uint64_t)({n})"
            raise GenError(f"{self.what}: unknown name {n}")
        if k == "deref":
            q = self.p.get(e[1])
            if not q or q["kind"] not in ("value", "enum", "flags") or q.get("size", 4) != 4: raise GenError(f"{self.what}: bad deref *{e[1]}")
            if e[2]:
                if not self.post: raise GenError(f"{self.what}: *{e[1]}@out before the call")
                return f"({e[1]} ? (uint64_t)(uint32_t)*{e[1]} : UINT64_C(0))"
            self.pre.add(e[1]); return f"d9_pre_{e[1]}"
        if k == "neg": return f"(-({self.num(e[1])}))"
        if k == "bin":
            a, b = self.num(e[2]), self.num(e[3])
            return f"d9x_div({a}, {b})" if e[1] == "/" else f"({a} {e[1]} {b})"
        f, args = e[1], e[2]
        if f in ("ok", "ok0"):
            if not (self.post and self.hr): raise GenError(f"{self.what}: {f} needs an output rule of an HRESULT method")
            return f"((d9_ret {'>= 0' if f == 'ok' else '== S_OK'}) ? {self.num(args[0])} : UINT64_C(0))"
        if f == "pd_iunknown": return f"(uint64_t)d9s_pd_iunk(o, {self.ptr(args[0])})"
        if f == "nonnull": return f"(uint64_t)({self.ptr(args[0])} != 0)"
        if f == "if": return f"({self.num(args[0])} ? {self.num(args[1])} : {self.num(args[2])})"
        if f == "eq": return f"(uint64_t)({self.num(args[0])} == {self.num(args[1])})"
        if f == "band": return f"({self.num(args[0])} & {self.num(args[1])})"
        if f not in FN: raise GenError(f"{self.what}: rule function {f} has no emitter")
        cpp, pa = FN[f]
        return f"{cpp}({', '.join(self.ptr(a) if i in pa else self.num(a) for i, a in enumerate(args))})"

    def c(self, s):
        try:
            return self.num(X.parse(s))
        except ValueError as ex:
            raise GenError(f"{self.what}: {ex}")
