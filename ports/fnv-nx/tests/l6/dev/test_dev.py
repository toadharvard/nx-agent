#!/usr/bin/env python3
"""test_dev.py: tests of render/d3d9/dev (D1+D2).  python3 -I tests/l6/dev/test_dev.py [--no-mutations] [--gate-strict]
 a  gate G-METHOD: spec methods vs definitions found by `nm` in the PRODUCT objects (obj, state_rt, state_ext, d9impl_unk.gen, state.gen), per interface
 b..e  test_dev (C++, g++ -m32): defaults / get-after-set / validation / state blocks / refcounts (test-only factory in t_factory.cpp)
 f  mutation checks: one rule broken in a copy of dev/ -> the named part must fail
TEST builds link with -Wl,--unresolved-symbols=ignore-all: the spec methods without a definition (the gate's missing count) stay unresolved; the tests never call them."""
import concurrent.futures as cf, json, os, re, shutil, subprocess, sys, tempfile
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import san; san.install(); import mutsrc
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DEV = os.path.join(ROOT, "render", "d3d9", "dev"); GEN = os.path.join(ROOT, "render", "d3d9", "gen"); SPEC = os.path.join(ROOT, "render", "d3d9", "spec", "d3d9_spec.json")
INC = os.path.join(ROOT, "shim", "include"); FRONT = os.path.join(ROOT, "render", "d3d9", "front"); BACKEND = os.path.join(ROOT, "render", "gl4"); NXB = os.path.normpath(os.path.join(BACKEND, "..", "backend")); FRONT_C = sorted(f for f in os.listdir(FRONT) if f.endswith(".c"))
LIB = ["obj.cpp", "app_unk_native.cpp", "state_rt.cpp", "state_ext.cpp"]; GENS = ["d9impl_unk.gen.cpp", "state.gen.cpp"]; NATIVE = os.path.join(ROOT, "tests", "l6", "native")
TESTS = ["test_dev.cpp", "t_factory.cpp", "t_defaults.cpp", "t_valid.cpp", "t_blocks.cpp", "t_refs.cpp"]
fails = []


def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


def build(dev, tmp, with_ext=True, jobs=8):
    """regenerate from `dev` (+ gen_impl.py) into tmp, compile everything; returns (exe, product object list)"""
    os.makedirs(tmp, exist_ok=True)
    for cmd in ([sys.executable, "-I", os.path.join(GEN, "gen_impl.py"), tmp, SPEC], [sys.executable, "-I", os.path.join(GEN, "gen_wine_ids.py"), tmp], [sys.executable, "-I", os.path.join(dev, "state_model.py"), tmp, SPEC]):
        r = sh(cmd)
        if r.returncode: return None, r.stderr[-800:]
    fl = ["g++", "-m32", "-std=c++17", "-fno-exceptions", "-fno-rtti", "-O0", "-w", "-I", INC, "-I", tmp, "-I", HERE, "-I", dev]
    jobs_ = [(os.path.join(NATIVE if s == "app_unk_native.cpp" else dev, s), s) for s in LIB] + [(os.path.join(tmp, s), s) for s in GENS] + [(os.path.join(HERE, s), s) for s in TESTS]
    cmds = [fl + ["-c", src, "-o", os.path.join(tmp, n + ".o")] for src, n in jobs_]
    cmds += [["gcc", "-m32", "-std=c11", "-w", "-I", BACKEND, "-I", NXB, "-I", FRONT, "-c", os.path.join(FRONT, c), "-o", os.path.join(tmp, "nxfront_" + c[:-2] + ".o")] for c in FRONT_C]
    with cf.ThreadPoolExecutor(jobs) as ex:
        rs = list(ex.map(sh, cmds))
    for c, r in zip(cmds, rs):
        if r.returncode: return None, " ".join(c[-3:]) + "\n" + r.stderr[-1500:]
    prod = [os.path.join(tmp, n + ".o") for _, n in jobs_ if n in LIB + GENS]
    allo = prod + [os.path.join(tmp, n + ".o") for n in TESTS] + [os.path.join(tmp, "nxfront_" + c[:-2] + ".o") for c in FRONT_C]
    if not with_ext: prod = [o for o in prod if "state_ext" not in o]
    r = sh(["g++", "-m32", "-no-pie", "-o", os.path.join(tmp, "test_dev")] + allo + ["-Wl,--unresolved-symbols=ignore-all"])
    return (os.path.join(tmp, "test_dev"), prod) if r.returncode == 0 else (None, r.stderr[-800:])


def gate(tmp, prod):
    want = [l.strip() for l in open(os.path.join(tmp, "d9impl_methods.txt")) if l.strip()]
    nm = sh(["nm", "-C", "--defined-only"] + prod).stdout; nm_u = sh(["nm", "-C", "-u"] + prod).stdout; have = set()
    for l in nm.split("\n"):
        m = re.match(r"^[0-9a-f]+ [TtWwRrDdBb] (.*)$", l)
        if m: have.add(m.group(1).split("(")[0])
    und = set()  # references of the product objects that no product object defines (a forwarder whose target is missing is NOT a definition)
    for l in nm_u.split("\n"):
        m = re.match(r"^\s+U (.*)$", l)
        if m and m.group(1).split("(")[0] not in have: und.add(m.group(1).split("(")[0])
    cores = ("NxDeviceCore", "NxStateBlockCore")
    miss = [w for w in want if w not in have or any(c + "::" + w.split("::")[1] in und for c in cores)]
    return want, miss, sorted(u for u in und if u.startswith(("Nx", "nx")) and u not in set(want))


def run(exe):
    r = sh([exe]); parts = {m.group(1): (int(m.group(2)), int(m.group(3))) for m in re.finditer(r"PART (\w): (\d+) checks, (\d+) failed", r.stdout)}
    return r, parts


def main():
    strict = "--gate-strict" in sys.argv; tmp = tempfile.mkdtemp(prefix="d9dev_")
    exe, prod = build(DEV, tmp)
    if not exe: print("BUILD FAILED\n" + prod); return 1
    want, miss, unres = gate(tmp, prod); N = len(want); M = N - len(miss)
    print(f"G-METHOD: methods in spec {N}, defined {M}, missing {N - M}")
    spec = json.load(open(SPEC)); per = {}
    for m in spec["methods"]: per.setdefault(m["iface"], [0, 0])[0] += 1
    cn = {}
    for w in want: cn[w] = None
    ifs = {i: i for i in spec["interfaces"]}
    def cls(i):
        r = i[len("IDirect3D"):] if i != "IUnknown" else None
        return "NxUnknown" if i == "IUnknown" else "Nx" + (i[1:] if r[:1].isdigit() else r)
    missing_by = {}
    for w in miss: missing_by[w.split("::")[0]] = missing_by.get(w.split("::")[0], 0) + 1
    print("  per interface (spec / missing): " + ", ".join(f"{i[1:] if i != 'IUnknown' else i} {per[i][0]}/{missing_by.get(cls(i), 0)}" for i in sorted(per)))
    print(f"  unresolved product references (backend hooks): {unres}")
    if strict and miss: fails.append(f"a: {len(miss)} methods undefined")
    r, parts = run(exe); print(r.stdout[-2500:])
    for p in "bcde":
        if p not in parts or parts[p][1] or not parts[p][0]: fails.append(f"{p}: {parts.get(p)}")
    # gate sensitivity: without state_ext's object the defined count must drop
    _, prod2 = prod, [o for o in prod if "state_ext" not in o]
    _, miss2, _u = gate(tmp, prod2); print(f"gate sensitivity: without state_ext.o missing {len(miss2)} (> {len(miss)}: {len(miss2) > len(miss)})")
    if not len(miss2) > len(miss): fails.append("a: gate insensitive to a removed object")
    if "--no-mutations" not in sys.argv:
        muts = [("b", "state_model.py", "ZFUNC D3DCMP_LESSEQUAL P DEPTH", "ZFUNC D3DCMP_LESS P DEPTH"), ("b", "state_model.py", "DMAPOFFSET 0 V SAMP", "DMAPOFFSET 256 V SAMP"),
                ("d", "state_model.py", "LASTPIXEL 1 P RASTER", "LASTPIXEL 1 - RASTER"), ("d", "state_model.py", "dict(name=\"viewport\", ct=\"D3DVIEWPORT9\", n=\"1\", shape=\"val0\", default=\"@viewport\", blocks=\"\"", "dict(name=\"viewport\", ct=\"D3DVIEWPORT9\", n=\"1\", shape=\"val0\", default=\"@viewport\", blocks=\"-\""),
                ("c", "state_model.py", 'SAMP_RANGES = [("0", "16", 0)', 'SAMP_RANGES = [("0", "17", 0)'), ("c", "state_gen.py", "k >= r[i].lo && k < r[i].hi", "k >= r[i].lo && k <= r[i].hi"),
                ("e", "obj.cpp", "if (n == 1 && owner) owner->addref();", ""), ("e", "obj.cpp", "if (!managed() || ci->rtype == D3DRTYPE_SURFACE) return 0;", "if (ci->rtype == D3DRTYPE_SURFACE) return 0;"),
                ("e", "obj.cpp", "    if (unk) { e->u = *(IUnknown* const*)data; uref(e); }", "    if (unk) { e->u = *(IUnknown* const*)data; }"),
                ("d", "state_rt.cpp", "if (nw) nx_obj_of(nw)->bindRef();", ""), ("d", "state_rt.cpp", "if (e.has_light) { D3DLIGHT9 l = e.light; d->SetLight(e.idx, &l); }", ""),
                ("b", "state_ext.cpp", "d.Direction.z = 1.0f;", "d.Direction.z = 0.0f;"), ("d", "state_rt.cpp", "if (rec && bit(nx_tab.mask[0], slot))", "if (false)"),
                ("c", "state_ext.cpp", "case 3: case 4: break;", "case 3: break;")]
        def one(i):
            part, f, old, new = muts[i]; mdev = os.path.join(tmp, f"m{i}_dev"); shutil.copytree(DEV, mdev, ignore=shutil.ignore_patterns("__pycache__"))
            txt = open(os.path.join(mdev, f)).read()
            open(os.path.join(mdev, f), "w").write(mutsrc.apply(txt, old, new, f"{f} #{i}"))
            x, pr = build(mdev, os.path.join(tmp, f"m{i}"), jobs=2)
            if not x: return i, None, pr
            rr, ps = run(x); return i, ps, rr.returncode
        with cf.ThreadPoolExecutor(3) as ex:
            for i, ps, rc in ex.map(one, range(len(muts))):
                part = muts[i][0]; ok = ps is not None and (ps.get(part, (0, 0))[1] > 0 or rc != 0 and part not in ps)
                print(f"mutation {i} ({muts[i][1]}: {muts[i][2][:50]!r} -> part {part}): {'caught' if ok else 'NOT CAUGHT'} {ps if ps is None or not ok else ''}{rc if ps is None else ''}")
                if not ok: fails.append(f"f: mutation {i} not caught ({ps} {rc})")
    shutil.rmtree(tmp, ignore_errors=True)
    print("FAILED: " + "; ".join(fails) if fails else "ALL OK")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
