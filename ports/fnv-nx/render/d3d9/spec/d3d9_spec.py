#!/usr/bin/env python3
"""d3d9_spec.py: builds d3d9_spec.json, the one table that describes every method of every D3D9 COM interface (RD1).
    python3 -I d3d9_spec.py            write d3d9_spec.json next to this script
    python3 -I d3d9_spec.py --stdout   print it (tests/l6/test_spec.py compares the two)
Inputs: shim/include/d3d9*.h (method order, types, structs, enums, constants) + d3d9_rules.py / d3d9_cls.py / d3d9_tables.py /
d3d9_exports.py (the hand tables). Unknown types or invalid rules abort the run: nothing is guessed."""
import json, os, re, sys
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import d3d9_cls as C, d3d9_exports as EX, d3d9_expr as X, d3d9_rules as R, d3d9_structs as ST, d3d9_tables as T   # noqa: E402

IUNK = [("HRESULT", "QueryInterface", "REFIID riid, void** ppvObj"), ("ULONG", "AddRef", ""), ("ULONG", "Release", "")]


def die(msg): raise SystemExit("d3d9_spec: " + msg)


def parse_headers():
    text = open(os.path.join(ST.INC, "d3d9.h")).read()
    ifs = {"IUnknown": ("", [(i, r, n, a) for i, (r, n, a) in enumerate(IUNK)])}
    for m in re.finditer(r"struct (IDirect3D\w+)\s*:\s*public (\w+)\s*\{(.*?)\n\};", text, re.S):
        name, base, body = m.groups()
        ms = [(int(x.group(1)), x.group(2).strip(), x.group(3), " ".join(x.group(4).split()))
              for x in re.finditer(r"/\*\s*(\d+)\s*\*/\s*FNVNX_D3D_METHOD\(([^,]+),\s*(\w+)\)\((.*?)\)\s*=\s*0;", body, re.S)]
        ifs[name] = (base, ms)
    iids = {}
    for m in re.finditer(r"FNVNX_D3D_GUID\(IID_(\w+),\s*(0x\w+),\s*(0x\w+),\s*(0x\w+),\s*\{([^}]*)\}\)", text):
        b = [int(x, 16) for x in m.group(5).split(",")]
        iids[m.group(1)] = "%08x-%04x-%04x-%02x%02x-%s" % (int(m.group(2), 16), int(m.group(3), 16), int(m.group(4), 16), b[0], b[1], "".join("%02x" % x for x in b[2:]))
    iids["IUnknown"] = "00000000-0000-0000-c000-000000000046"
    return ifs, iids


def flatten(ifs):
    def chain(n):
        if n == "IUnknown": return list(ifs[n][1])
        base, ms = ifs[n]
        return chain(base) + ms
    out = {}
    for n in ifs:
        ms = chain(n)
        if [s for s, *_ in ms] != list(range(len(ms))): die(f"{n}: slot numbers are not 0..{len(ms) - 1}")
        out[n] = ms
    return out


def norm(ctype):
    s = ctype.replace("struct ", "")
    return re.sub(r"\bconst\b|\*", "", s).strip(), s.count("*"), "const" in s.split("*")[0].split()


def split_params(args, mname):
    parts = [p.strip() for p in args.split(",")] if args.strip() not in ("", "void") else []
    fix = R.NAME_FIX.get(mname, [])
    out = []
    for i, p in enumerate(parts):
        m = re.match(r"^(.*?)(\w+)$", p)
        if m and m.group(1).strip() not in ("", "const", "struct"): ty, nm = m.group(1).strip(), m.group(2)
        else: ty, nm = p, (fix[i] if i < len(fix) and fix[i] else f"arg{i}")
        out.append((" ".join(ty.split()), nm))
    return out


class Gen:
    def __init__(self):
        self.db = ST.load()
        self.structs = ST.layout(self.db)
        self.enums = self.db["enums"]
        self.consts = self.db["consts"]
        self.ifs, self.iids = parse_headers()
        self.flat = flatten(self.ifs)
        self.used_consts = set()

    def keys(self, iface, m):
        out, i = [], iface
        while i and i != "IUnknown":
            out.append(f"{i[len('IDirect3D'):]}.{m}")
            i = self.ifs[i][0]
        return out + [m]

    def lookup(self, table, iface, m, name):
        for k in self.keys(iface, m):
            if name in table.get(k, {}): return table[k][name]
        return None

    def elem(self, e):
        return self.structs[e]["size"] if isinstance(e, str) else e

    def param(self, iface, mname, i, ctype, name):
        base, stars, const = norm(ctype)
        spec = self.lookup(R.RULES, iface, mname, name) or R.GLOBAL.get(name)
        p = {"name": name, "ctype": ctype}
        if spec is None:
            if stars == 0 and base in self.enums: spec = {"kind": "enum"}
            elif stars == 0 and base in ("REFGUID", "REFIID"): spec = {"kind": "struct", "struct": "GUID", "d": "in"}
            elif base.startswith("IDirect3D") or base == "IUnknown": spec = {"kind": "handle"}
            elif base in self.structs: spec = {"kind": "struct"}
            elif base in self.enums: spec = {"kind": "enum"}
            elif base in ST.BASE: spec = {"kind": "float" if ST.BASE[base][2] == "float" else "ohandle" if ST.BASE[base][2] == "ohandle" else "value"}
            else: die(f"{iface}.{mname}.{name}: no rule and unknown type {ctype!r}")
        k = spec["kind"]
        d = spec.get("d")
        p["kind"] = k
        if k == "handle":
            out = stars == 2
            p["iface"] = spec.get("iface") or base
            p["dir"] = d or ("out" if out else "in")
            p["byref"] = out
            p["null"] = True if p["dir"] == "out" else bool(spec.get("null"))
            if p["dir"] != "in":
                p["own"] = "new" if mname in R.NEW else "addref" if mname in R.ADDREF else die(f"{iface}.{mname}: out handle without ownership")
                p["when"] = "always"
            if p["iface"] != "$riid" and p["iface"] not in self.ifs: die(f"{iface}.{mname}.{name}: unknown interface {p['iface']}")
        elif k == "struct":
            p["struct"] = spec.get("struct") or base
            if p["struct"] not in self.structs: die(f"{iface}.{mname}.{name}: unknown struct {p['struct']}")
            p["dir"] = d or ("in" if const else "out")
            p["byref"], p["null"] = True, bool(spec.get("null"))
        elif k == "buffer":
            p["elem"], p["len"] = self.elem(spec["elem"]), spec["len"]
            if isinstance(spec["elem"], str): p["elem_struct"] = spec["elem"]
            p["dir"] = d or ("in" if const else "out")
            p["byref"], p["null"] = True, bool(spec.get("null"))
            for f in ("off", "canon"):
                if spec.get(f): p[f] = spec[f]
        elif k == "locked":
            p["lock"], p["dir"], p["byref"] = spec["lock"], "out", True
            if base in self.structs: p["struct"] = base
        else:   # scalar kinds
            p["dir"] = d or ("out" if stars and not const else "in")
            p["byref"] = stars > 0 and base != "void"
            if k == "enum": p["enum"], p["size"] = spec.get("enum") or base, 4
            elif k == "flags": p["flags"], p["size"] = spec["flags"], 4
            elif k == "float": p["size"] = 4
            elif k == "ohandle": p["size"], p["os"] = 4, spec.get("os") or base
            else:
                p["size"] = spec.get("size") or ST.BASE[base][0]
                if base in ST.BASE: p["signed"] = ST.BASE[base][1]
            if spec.get("in_when"): p["in_when"] = spec["in_when"]
            if k in ("ohandle",) and "null" in spec: p["null"] = spec["null"]
        if p["dir"] != "in":
            self.out_class(p, iface, mname, name)
        return p

    def out_class(self, p, iface, mname, name):
        if p["kind"] == "locked":
            p["cls"], p["when"] = "by:taint", "ok"
            fc = C.LOCKED_FIELDS.get(p.get("struct"), {})
            if fc: p["field_cls"], p["field_why"] = {f: c for f, (c, w) in fc.items()}, {f: w for f, (c, w) in fc.items()}
            return
        p.setdefault("when", "always" if (mname == "GetPrivateData" and name == "pSizeOfData") else "ok")
        if mname == "GetData" and name == "pData": p["cls"] = "by:query_type"; return
        hit = None
        for k in self.keys(iface, mname):
            hit = hit or C.CLS.get(f"{k}.{name}")
        hit = hit or C.CLS.get(name) or C.STRUCT.get(p.get("struct")) or C.STRUCT.get(norm(p["ctype"])[0]) or ("det", None)
        p["cls"] = hit[0]
        if hit[0] != "det": p["why"] = hit[1]

    def method(self, iface, slot, ret, name, args):
        ps = [self.param(iface, name, i, t, n) for i, (t, n) in enumerate(split_params(args, name))]
        m = {"iface": iface, "slot": slot, "name": name, "ret": ret, "params": ps}
        m["ret_kind"] = "void" if ret == "void" else "hresult" if ret == "HRESULT" else "enum" if ret in self.enums else \
            "ohandle" if ret == "HMONITOR" else "float" if ret == "float" else "value"
        if ret != "void":
            hit = None
            for k in self.keys(iface, name): hit = hit or C.CLS.get(f"{k}.$ret")
            hit = hit or ("det", None)
            m["ret_cls"] = hit[0]
            if hit[1]: m["ret_why"] = hit[1]
        eff = [x.group(0) for t in T.TAINT if t[0] == name for x in re.finditer(r"(pix|copy)\([^)]*\)", t[1])]
        if eff: m["effects"] = eff
        return m

    def tables(self, methods):
        c = self.consts
        q = {}
        for n, (data, cls, why) in T.QUERY.items():
            size = {"BOOL": 4, "DWORD": 4, "UINT64": 8}.get(data) or self.structs[data]["size"]
            q["D3DQUERYTYPE_" + n] = {"value": c["D3DQUERYTYPE_" + n], "data": data, "size": size, "cls": cls, "why": why}
        walk = json.loads(json.dumps(T.SM_WALK))
        walk["opcodes"] = {n: c[n] for n in sorted(list(walk["params_lt_2"]) + [walk["comment"]["opcode"], walk["end"]] + list(walk["ps_1_4"]))}
        return {"format": T.FORMAT, "primitive": T.PRIMITIVE, "query_type": q, "sm_walk": walk, "cls_rules": T.CLS_RULES,
                "taint": [{"method": m, "effect": e, "why": w} for m, e, w in T.TAINT], "iids": self.iids,
                "object_props": X.OBJ_PROPS, "functions": {k: {"arity": a, "meaning": s} for k, (a, s) in X.FUNCS.items()},
                "prim_enum": {n: c[n] for n in T.PRIMITIVE}}

    def check(self, methods):
        names = lambda m: {p["name"] for p in m["params"]}
        by = {(m["iface"], m["name"]): m for m in methods}
        for m in methods:
            where = f"{m['iface']}.{m['name']}"
            pn = names(m)
            for p in m["params"]:
                pid = f"{where}.{p['name']}"
                rules = [(p.get(f), p["dir"] != "in") for f in ("len", "off", "in_when")] + [(p.get("canon", {}).get("when"), False)]
                if p["kind"] == "locked":
                    L = p["lock"]
                    rules += [(L[f], False) for f in ("off", "bytes", "w", "h", "d", "fmt", "on_lock", "on_unlock") if f in L]
                    if (m["iface"], L["unlock"]) not in by: die(f"{pid}: unlock method {L['unlock']} missing")
                    um = by[(m["iface"], L["unlock"])]
                    for a, b in L["match"]:
                        if a not in pn or b not in names(um): die(f"{pid}: lock/unlock key {a}/{b}")
                    um.setdefault("closes", [])
                    if m["name"] not in um["closes"]: um["closes"].append(m["name"])
                for e, post in rules:
                    if not e: continue
                    errs = X.check(e, pn, set(self.consts), post=post or p["dir"] != "in")
                    if errs: die(f"{pid}: rule {e!r}: {errs}")
                    self.used_consts |= {n for n in X.walk(X.parse(e), {"names": set(), "funcs": []})["names"] if n not in pn and not n.startswith("this.")}
            for e in m.get("effects", []):
                arg = e[e.index("(") + 1:-1].split(",")
                if any(a not in pn and a not in ("rendertargets", "depthstencil", "this") for a in arg): die(f"{where}: effect {e}")

    def build(self):
        methods = []
        for iface in sorted(self.flat):
            for slot, ret, name, args in self.flat[iface]:
                methods.append(self.method(iface, slot, ret, name, args))
        self.check(methods)
        t = self.tables(methods)
        t["constants"] = {n: self.consts[n] for n in sorted(self.used_consts)}
        ifaces = {n: {"base": self.ifs[n][0] or None, "slots": len(self.flat[n]), "iid": self.iids.get(n)}
                  for n in self.flat}
        return {"schema": 1, "interfaces": ifaces, "methods": methods, "structs": self.structs, "enums": self.enums,
                "flags": dict(sorted(ST.FAMILIES.items())), "tables": t, "exports": EX.exports()}


def compact(v): return json.dumps(v, sort_keys=True, separators=(",", ":"))


def dump(v, depth, ind=""):
    if depth == 0 or not isinstance(v, (dict, list)) or not v: return compact(v)
    inner = ind + " "
    if isinstance(v, dict):
        body = [f"{inner}{json.dumps(k)}:{dump(v[k], depth - 1, inner)}" for k in sorted(v)]
        return "{\n" + ",\n".join(body) + "\n" + ind + "}"
    return "[\n" + ",\n".join(inner + dump(x, depth - 1, inner) for x in v) + "\n" + ind + "]"


def render(spec):
    depth = {"methods": 1, "structs": 1, "enums": 1, "interfaces": 1, "tables": 2, "exports": 1, "flags": 1}
    body = [f" {json.dumps(k)}:{dump(spec[k], depth.get(k, 0), ' ')}" for k in sorted(spec)]
    return ("{\n" + ",\n".join(body) + "\n}\n").encode()


if __name__ == "__main__":
    out = render(Gen().build())
    if "--stdout" in sys.argv: sys.stdout.buffer.write(out)
    else: open(os.path.join(HERE, "d3d9_spec.json"), "wb").write(out)
