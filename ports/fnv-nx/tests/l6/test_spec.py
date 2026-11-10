#!/usr/bin/env python3
"""tests/l6/test_spec.py: checks render/d3d9/spec/d3d9_spec.json against the shim headers and the SDK.
Run: python3 -I tests/l6/test_spec.py    (exit != 0 on any failure; prints counts, plus every unresolved item)"""
import json, os, re, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SPEC = os.path.join(ROOT, "render", "d3d9", "spec")
INC = os.path.join(ROOT, "shim", "include")
sys.path.insert(0, SPEC)
import d3d9_expr as X                                                # noqa: E402

SLOTS = {"IUnknown": 3, "IDirect3D9": 17, "IDirect3D9Ex": 22, "IDirect3DDevice9": 119, "IDirect3DDevice9Ex": 134,
         "IDirect3DSwapChain9": 10, "IDirect3DSwapChain9Ex": 13, "IDirect3DResource9": 11, "IDirect3DBaseTexture9": 17,
         "IDirect3DTexture9": 22, "IDirect3DCubeTexture9": 22, "IDirect3DVolumeTexture9": 22, "IDirect3DSurface9": 17,
         "IDirect3DVolume9": 11, "IDirect3DVertexBuffer9": 14, "IDirect3DIndexBuffer9": 14,
         "IDirect3DVertexDeclaration9": 5, "IDirect3DVertexShader9": 5, "IDirect3DPixelShader9": 5,
         "IDirect3DStateBlock9": 6, "IDirect3DQuery9": 8}
EXPORTS = ["Direct3DCreate9", "Direct3DCreate9Ex", "D3DPERF_BeginEvent", "D3DPERF_EndEvent", "D3DPERF_GetStatus",
           "D3DPERF_QueryRepeatFrame", "D3DPERF_SetMarker", "D3DPERF_SetOptions", "D3DPERF_SetRegion", "DebugSetLevel",
           "DebugSetMute", "Direct3DShaderValidatorCreate9", "PSGPError", "PSGPSampleTexture"]
PKINDS = {"value", "enum", "flags", "float", "ohandle", "handle", "struct", "buffer", "locked"}
FKINDS = {"int", "enum", "flags", "float", "ohandle", "ptr", "char", "struct", "pad"}
CLS = {"det", "env", "pix"}
fails = {}
notes = []


def fail(check, msg):
    fails.setdefault(check, []).append(msg)


def hdr_text():
    return "\n".join(open(os.path.join(INC, f)).read() for f in ("d3d9.h", "d3d9types.h", "d3d9caps.h"))


def main():
    path = os.path.join(SPEC, "d3d9_spec.json")
    if not os.path.exists(path):
        for c in ("slots", "regen", "kinds", "names", "structs", "buffers", "outs", "unresolved", "tables", "exports"):
            fail(c, "d3d9_spec.json missing")
        return report()
    raw = open(path, "rb").read()
    S = json.loads(raw)
    H = hdr_text()
    enums = set(re.findall(r"}\s*(\w+)\s*;", " ".join(re.findall(r"typedef enum[^{]*\{.*?\}\s*\w+\s*;", H, re.S))))
    consts = set(re.findall(r"\b(D3D\w+|MAX_\w+)\b", H))
    ifaces, meths = S["interfaces"], S["methods"]
    by = {}
    for m in meths: by.setdefault(m["iface"], []).append(m)
    # 1 slots
    for i, n in SLOTS.items():
        sl = [m["slot"] for m in by.get(i, [])]
        if sl != list(range(n)): fail("slots", f"{i}: {len(sl)} slots, SDK {n}")
        if ifaces.get(i, {}).get("slots") != n: fail("slots", f"{i}: interfaces[] slots field")
    for i in ifaces:
        if i not in SLOTS: fail("slots", f"unexpected interface {i}")
        b = ifaces[i].get("base")
        if b and [m["name"] for m in by[i][:SLOTS[b]]] != [m["name"] for m in by[b]]: fail("slots", f"{i}: vtable prefix differs from base {b}")
    # 2 regen
    r = subprocess.run([sys.executable, "-I", os.path.join(SPEC, "d3d9_spec.py"), "--stdout"], capture_output=True)
    if r.returncode or r.stdout != raw: fail("regen", "d3d9_spec.py --stdout differs from checked-in json (rc=%d)" % r.returncode)
    # 3/4/6/7/8 per method
    for m in meths:
        mid = f"{m['iface']}.{m['name']}"
        pn = {p["name"] for p in m["params"]}
        if len(pn) != len(m["params"]): fail("kinds", f"{mid}: duplicate param names")
        if m.get("unresolved"): fail("unresolved", f"{mid}: {m['unresolved']}")
        if m["ret"] != "void":
            if m.get("ret_cls") not in CLS: fail("outs", f"{mid}: ret_cls")
            elif m["ret_cls"] != "det" and not m.get("ret_why"): fail("outs", f"{mid}: ret_why")
        for e in m.get("effects", []):
            if not re.match(r"(pix|copy)\([\w,]+\)$", e): fail("kinds", f"{mid}: effect {e}")
        for p in m["params"]:
            pid = f"{mid}.{p['name']}"
            k = p.get("kind")
            if p.get("unresolved"): fail("unresolved", f"{pid}: {p['unresolved']}")
            if k not in PKINDS or p.get("dir") not in ("in", "out", "inout") or not p.get("ctype"):
                fail("kinds", f"{pid}: kind/dir/ctype {k} {p.get('dir')}"); continue
            if k in ("value", "ohandle", "float", "enum", "flags") and p.get("size") not in (1, 2, 4, 8): fail("kinds", f"{pid}: size")
            if k == "enum" and p.get("enum") not in enums: fail("names", f"{pid}: enum {p.get('enum')}")
            if k == "flags" and p.get("flags") not in S["flags"]: fail("names", f"{pid}: flags {p.get('flags')}")
            if k == "struct" and p.get("struct") not in S["structs"]: fail("names", f"{pid}: struct {p.get('struct')}")
            if k == "handle":
                if p.get("iface") not in ifaces and p.get("iface") != "$riid": fail("names", f"{pid}: iface {p.get('iface')}")
                if p["dir"] != "in" and p.get("own") not in ("new", "addref"): fail("kinds", f"{pid}: out handle without own")
                if not isinstance(p.get("null"), bool): fail("kinds", f"{pid}: handle null flag")
            if k == "buffer":
                if not isinstance(p.get("elem"), int) or p["elem"] < 1 or not isinstance(p.get("null"), bool): fail("buffers", f"{pid}: elem/null")
                for fld in ("len", "off"):
                    if fld in p or fld == "len":
                        for e in X.check(p.get(fld, ""), pn, consts, post=p["dir"] != "in"): fail("buffers", f"{pid}.{fld}: {e}")
                if p.get("canon") and p["canon"].get("as") != "ohandle": fail("buffers", f"{pid}: canon")
                if p.get("canon"):
                    for e in X.check(p["canon"]["when"], pn, consts): fail("buffers", f"{pid}.canon: {e}")
            if k == "locked":
                L = p.get("lock", {})
                un = L.get("unlock")
                if un not in {x["name"] for x in by[m["iface"]]}: fail("buffers", f"{pid}: unlock {un}")
                if L.get("shape") not in ("linear", "rect", "box", "dc"): fail("buffers", f"{pid}: shape")
                for fld in ("off", "bytes", "w", "h", "d", "on_lock", "on_unlock"):
                    if fld in L:
                        for e in X.check(L[fld], pn, consts): fail("buffers", f"{pid}.{fld}: {e}")
                if L.get("shape") != "dc" and "bytes" not in L and "w" not in L: fail("buffers", f"{pid}: no extent rule")
            if p["dir"] in ("out", "inout"):
                c = p.get("cls")
                if c in CLS:
                    if c != "det" and not p.get("why"): fail("outs", f"{pid}: non-det without why")
                elif not (isinstance(c, str) and c.startswith("by:") and c[3:] in S["tables"]["cls_rules"]): fail("outs", f"{pid}: cls {c}")
                if p.get("when") not in ("ok", "always"): fail("outs", f"{pid}: when")
                for f, fc in p.get("field_cls", {}).items():
                    sf = {x["name"] for x in S["structs"].get(p.get("struct"), {}).get("fields", [])}
                    if f not in sf or fc not in CLS: fail("outs", f"{pid}: field_cls {f}")
    # evaluability smoke: every rule evaluates with stub functions
    class Env(dict):
        def __missing__(self, k): return 3
    stub = {f: (lambda *a: 5) for f in X.FUNCS}
    for m in meths:
        for p in m["params"]:
            rules = [p.get("len"), p.get("off")] + [p.get("lock", {}).get(f) for f in ("off", "bytes", "w", "h", "d")]
            for s in filter(None, rules):
                try: X.evaluate(X.parse(s), Env(), stub)
                except Exception as ex: fail("buffers", f"{m['iface']}.{m['name']}.{p['name']}: eval {ex}")
    # 5 structs: exact cover of sizeof, against a g++ -m32 probe of the shim headers (else the header's own asserts)
    st = S["structs"]
    ref, mode = probe(st)
    notes.append("struct layout reference: " + mode)
    for n, s in st.items():
        pos = 0
        for f in s["fields"]:
            if f["kind"] not in FKINDS: fail("structs", f"{n}.{f['name']}: kind {f['kind']}")
            if f["off"] != pos: fail("structs", f"{n}.{f['name']}: gap/overlap at {pos} vs {f['off']}")
            pos = f["off"] + f["size"]
            if f["kind"] == "struct" and f.get("struct") not in st: fail("names", f"{n}.{f['name']}: struct {f.get('struct')}")
            if f["kind"] == "enum" and f.get("enum") not in enums: fail("names", f"{n}.{f['name']}: enum")
            if f["kind"] == "flags" and f.get("flags") not in S["flags"]: fail("names", f"{n}.{f['name']}: flags")
            if ref.get(f"off_{n}_{f['name']}") not in (None, f["off"]) and f["kind"] != "pad": fail("structs", f"{n}.{f['name']}: offset {f['off']} vs {ref[f'off_{n}_{f['name']}']}")
        if pos != s["size"]: fail("structs", f"{n}: fields cover {pos} of sizeof {s['size']}")
        if n in ref and ref[n] != s["size"]: fail("structs", f"{n}: sizeof {s['size']} vs reference {ref[n]}")
        if n not in ref and mode.startswith("g++"): fail("structs", f"{n}: not probed")
    for fl, pre in S["flags"].items():
        if not re.search(r"#define\s+" + re.escape(pre), H): fail("names", f"flags {fl}: no #define {pre}*")
    # tables
    T = S["tables"]
    for tbl, pat in (("format", r"D3DFMT_\w+"), ("primitive", r"D3DPT_\w+"), ("query_type", r"D3DQUERYTYPE_\w+")):
        have = set(T.get(tbl, {}))
        want = set(re.findall(r"\b(" + pat + r")\s*(?:=|,)", H)) - {"D3DFMT_FORCE_DWORD"}
        if tbl == "primitive": want -= {"D3DPT_FORCE_DWORD"}
        if want - have: fail("tables", f"{tbl}: missing {sorted(want - have)[:4]}")
    for q, row in T.get("query_type", {}).items():
        if row.get("cls") not in CLS or (row["cls"] != "det" and not row.get("why")) or not row.get("size"): fail("tables", f"query {q}")
    for k in ("sm_walk", "cls_rules", "taint", "iids"):
        if k not in T: fail("tables", f"missing {k}")
    for t in T.get("taint", []):
        if t["method"] not in {m["name"] for m in meths}: fail("tables", f"taint: unknown method {t['method']}")
    # exports
    ex = {e["name"]: e for e in S.get("exports", [])}
    for n in EXPORTS:
        e = ex.get(n)
        if not e: fail("exports", f"missing {n}")
        elif not e.get("undocumented") and "params" not in e: fail("exports", f"{n}: no params and not undocumented")
    report(len(meths), len(st))


def probe(st):
    """sizeof/offsetof of every struct as the shim headers lay it out under g++ -m32 (4-byte long, pack(4)); {name: size, off_S_f: n}"""
    src = ["#include <d3d9.h>"]
    for n, s in st.items():
        src.append(f"extern const int sz_{n} = (int)sizeof({n});")
        for f in s["fields"]:
            if f["kind"] != "pad": src.append(f"extern const int off_{n}_{f['name']} = (int)__builtin_offsetof({n}, {f['name']});")
    with tempfile.TemporaryDirectory() as d:
        cpp, asm = os.path.join(d, "p.cpp"), os.path.join(d, "p.s")
        open(cpp, "w").write("\n".join(src) + "\n")
        r = subprocess.run(["g++", "-m32", "-ffreestanding", "-w", "-S", "-DWINAPI=", "-I", INC, "-o", asm, cpp], capture_output=True)
        if r.returncode:
            return headers_sizes(), "header FNVNX_D3D_SIZE asserts only (g++ -m32 probe unavailable)"
        out = {}
        for m in re.finditer(r"^(\w+):\n(?:\t\.[a-z0-9]+[^\n]*\n)*?\t\.long\t(-?\d+)", open(asm).read(), re.M):
            k = m.group(1)
            out[k[3:] if k.startswith("sz_") else k] = int(m.group(2))
        return out, "g++ -m32 -ffreestanding probe of the shim headers (pack 4, ILP32)"


def headers_sizes():
    return {m.group(1): int(m.group(2)) for m in re.finditer(r"FNVNX_D3D_SIZE\((\w+),\s*(\d+)\)", hdr_text())}


def report(nm=0, ns=0):
    total = 0
    for c in ("slots", "regen", "kinds", "names", "structs", "buffers", "outs", "unresolved", "tables", "exports"):
        n = len(fails.get(c, []))
        total += n
        print(f"{c}: {n} failing")
        for msg in fails.get(c, [])[: 60 if c == "unresolved" else 5]:
            print("   ", msg)
    for n in notes: print(n)
    print(f"methods={nm} structs={ns} TOTAL failures={total}")
    sys.exit(1 if total else 0)


main()
