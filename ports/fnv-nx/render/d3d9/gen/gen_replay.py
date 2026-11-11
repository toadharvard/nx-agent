#!/usr/bin/env python3
"""gen_replay.py OUTDIR [SPEC.json]: generates OUTDIR/d9replay.gen.cpp (not checked in): the spec-driven tables and call invokers of the G-REPLAY replayer.
Runtime: render/tools/cmp/d9replay_rt.cpp (+ .h).  Every item comes from the spec by rule; exit 2 naming the item when the spec has something with no emitter (no fallbacks)."""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.dont_write_bytecode = True
from gen_expr import Ctx, GenError            # noqa: E402
import gen_tab as T                           # noqa: E402

KIND = {"value": 0, "enum": 1, "flags": 2, "float": 3, "ohandle": 4, "handle": 5, "struct": 6, "buffer": 7, "locked": 8}
DIR = {"in": 0, "out": 1, "inout": 2}
SHAPE = {"linear": 0, "rect": 1, "box": 2, "dc": 3}
REFS = ("REFIID", "REFGUID")


def isptr(p): return p["byref"] or p["kind"] in ("buffer", "locked", "struct")


def local(p, i):
    """C++ declaration of parameter p as a local, from argument i (same names and types as the wrapper's method signature)"""
    n, k, t = p["name"], p["kind"], p["ctype"]
    if isptr(p): return f"const GUID &{n} = *(const GUID *)a[{i}].p;" if t in REFS else f"{t} {n} = ({t})a[{i}].p;"
    if k in ("value", "enum", "flags"): return f"{t} {n} = ({t})(uint32_t)a[{i}].u;"
    if k == "float": return f"{t} {n} = d9r_f(a[{i}].u);"
    if k == "ohandle": return f"{t} {n} = ({t})(uintptr_t)a[{i}].u;"
    return f"{t} {n} = ({t})a[{i}].p;"


def check(m, what, spec, names):
    for p in m["params"]:
        n, k = p["name"], p["kind"]
        if k not in KIND: raise GenError(f"{what}: param {n}: kind {k} has no emitter")
        if k in ("value", "enum", "flags", "float") and p.get("size", 4) != 4: raise GenError(f"{what}: param {n}: value size {p.get('size')} has no emitter")
        if k == "handle" and isptr(p) and p["dir"] == "in": raise GenError(f"{what}: param {n}: pointer to input handle has no emitter")
        if k == "handle" and p["iface"] != "$riid" and p["iface"] not in names: raise GenError(f"{what}: param {n}: unknown interface {p['iface']}")
        if k == "locked" and p["lock"]["shape"] not in SHAPE: raise GenError(f"{what}: param {n}: lock shape {p['lock']['shape']} has no emitter")
        if "canon" in p and p["canon"]["as"] not in ("ohandle", "handle"): raise GenError(f"{what}: param {n}: canon as {p['canon']['as']} has no emitter")
    if m["ret_kind"] not in ("void", "hresult", "value", "enum", "float", "ohandle", "handle"): raise GenError(f"{what}: ret_kind {m['ret_kind']} has no emitter")


def emit(spec, m, key, iface, export, names, out):
    what = m["name"] if export is not None else f"{iface}::{m['name']}"; P = m["params"]; check(m, what, spec, names)
    args = []
    for i, p in enumerate(P):
        t, k = p["ctype"], p["kind"]
        args.append(f"*(const GUID *)a[{i}].p" if isptr(p) and t in REFS else f"({t})a[{i}].p" if isptr(p) or k == "handle" else f"d9r_f(a[{i}].u)" if k == "float"
                    else f"({t})(uintptr_t)a[{i}].u" if k == "ohandle" else f"({t})(uint32_t)a[{i}].u")
    rk = m["ret_kind"]; rtype = m["ret"]
    if export is not None: call = f"d9wrap_{m['name']}(({rtype} (WINAPI *)({', '.join(q['ctype'] for q in P) or 'void'}))fn{''.join(', ' + x for x in args)})"
    else: call = f"(({iface} *)self)->{m['name']}({', '.join(args)})"
    out.append(f"static uintptr_t inv_{key}(void *self, void *fn, const D9A *a) {{ (void)self; (void)fn; " + (f"return (uintptr_t){call}; }}" if rk == "handle" else f"(void)({call}); return 0; }}"))
    ctx = Ctx(m, spec, T.props_of(iface) if iface else None, False, what + " (replay rule)"); cases = []; rows = []
    for i, p in enumerate(P):
        if "off" in p: cases.append(f"case {i}: return (uint64_t)({ctx.c(p['off'])});")
        if "canon" in p: cases.append(f"case {64 + i}: return (uint64_t)({ctx.c(p['canon']['when'])});")
        k = p["kind"]; st = p.get("struct"); ptro = 0
        if k == "locked" and p["lock"]["shape"] in ("rect", "box"): ptro = next(f["off"] for f in spec["structs"][st]["fields"] if f["kind"] == "ptr")
        npit = sum(1 for f in spec["structs"][st]["fields"] if f["kind"] == "int") if k == "locked" and p["lock"]["shape"] in ("rect", "box") else 0
        ssz = spec["structs"][st]["size"] if st else 16
        ifc = (names.index(p["iface"]) if p["iface"] != "$riid" else -1) if k == "handle" else names.index(p["canon"]["iface"]) if p.get("canon", {}).get("as") == "handle" else -1
        rows.append(f'{{"{p["name"]}", {KIND[k]}, {DIR[p["dir"]]}, {int(p["byref"])}, {int("in_when" in p)}, {0 if "canon" not in p else 1 if p["canon"]["as"] == "ohandle" else 2}, '
                    f'{SHAPE[p["lock"]["shape"]] if k == "locked" else 0}, {npit}, {int("off" in p)}, {ifc}, {ssz}, {ptro}, {"&d9_sd_" + st if st else "0"}}}')
    ev = "0"
    if cases:
        out.append(f"static uint64_t ev_{key}(const D9A *a, D9Obj *o, unsigned kind, unsigned pi) {{ (void)o;")
        out += ["  " + local(p, i) for i, p in enumerate(P)] + [f"  uint64_t d9_pre_{n} = {n} ? (uint64_t)(uint32_t)*{n} : 0;" for n in sorted(ctx.pre)]
        out += ["  switch (kind * 64 + pi) {"] + ["  " + c for c in cases] + ["  default: return 0; } }"]; ev = f"ev_{key}"
    out.append(f"static const D9RP p_{key}[] = {{{', '.join(rows) or '{0}'}}};")
    riid = [i for i, p in enumerate(P) if p["ctype"] in REFS]
    if any(p["kind"] == "handle" and p["iface"] == "$riid" for p in P) and not riid: raise GenError(f"{what}: $riid output without a riid parameter")
    if rk == "handle":
        t = rtype.replace("const ", "").replace("struct ", "").strip().rstrip("*").strip()
        if t not in names: raise GenError(f"{what}: handle return type {rtype} is not a spec interface")
        rif = names.index(t)
    else: rif = -1
    return f'{{"{m["name"]}", {names.index(iface) if iface else -1}, {m.get("slot", export if export is not None else 0)}, {len(P)}, p_{key}, {2 if rk == "handle" else 0 if rk == "void" else 1}, {int("closes" in m)}, {rif}, {riid[0] if riid else -1}, inv_{key}, {ev}}}'


def generate(spec, raw, outdir):
    names = sorted(spec["interfaces"]); L = ['#include "d9replay_rt.h"'] + [f"extern const D9SDesc d9_sd_{n};" for n in spec["structs"]]; rows = []; base = [0]
    for n in names:
        ms = sorted([m for m in spec["methods"] if m["iface"] == n], key=lambda m: m["slot"])
        if [m["slot"] for m in ms] != list(range(spec["interfaces"][n]["slots"])): raise GenError(f"{n}: methods do not cover slots 0..{spec['interfaces'][n]['slots'] - 1}")
        for m in ms: rows.append(emit(spec, m, f"{names.index(n)}_{m['slot']}", n, None, names, L))
        base.append(len(rows))
    L += ["const D9RM d9r_methods[] = {" + ",\n  ".join(rows) + "};", f"const unsigned d9r_niface = {len(names)}, d9r_ibase[] = {{{', '.join(map(str, base))}}};"]; ex = []
    for i, e in enumerate(spec["exports"]):
        if "params" not in e or "ret_kind" not in e: ex.append(f'{{"{e["name"]}", -1, {i}, 0, 0, 0, 0, -1, -1, 0, 0}}')       # no signature in the spec: cannot be recorded, so cannot be replayed
        else: ex.append(emit(spec, e, f"x{i}", None, i, names, L))
    L += ["const D9RM d9r_exports[] = {" + ",\n  ".join(ex) + "};", f"const unsigned d9r_nexports = {len(spec['exports'])};"]
    os.makedirs(outdir, exist_ok=True); open(os.path.join(outdir, "d9replay.gen.cpp"), "w").write("// generated by gen_replay.py from d3d9_spec.json; do not edit\n" + "\n".join(L) + "\n")
    return len(L)


def main():
    if len(sys.argv) < 2: sys.exit("usage: gen_replay.py OUTDIR [d3d9_spec.json]")
    raw = open(sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "..", "spec", "d3d9_spec.json"), "rb").read()
    try: n = generate(json.loads(raw), raw, sys.argv[1])
    except GenError as ex: print(f"gen_replay: FAIL: {ex}", file=sys.stderr); sys.exit(2)
    print(f"gen_replay: wrote {n} lines to {sys.argv[1]}")


if __name__ == "__main__":
    main()
