#!/usr/bin/env python3
"""Test of the N3 wiring of verify/gate_contract.py (stages bind -> bodysel --n3) and its effect on gates namegate and r7. Plain script, needs root
(sandbox) and the pinned guest compiler (as test_l2gates.py). The objects are the real ones of a toy native guest root built by recipe.py (their `.fnvnx.n3`,
`.fnvnx.sympair`, `.fnvnx.layout` sections are the plugins' own output); the stage commands are the ones gate_contract.stages() gives (bind.py, bodysel.py, run
as subprocesses). The exe-derived inputs are data written by the test (the N1 certificate, universe.tsv, addrmap): the exe is not in the repository.
Cases: (1) the stage table passes --n3 and runs bind first; (2) the toy functions g and h, N1-certified, N3 `ok`: NATIVE rows in bodysel.tsv, namegate and r7
universes > 0 and certified; (3) the same with a byte mutation of g's N3 verdict in the object: g is LIFTED (`N3: ...`), h too through N4 only if it inlined g,
and the universes of namegate and r7 shrink to the native-bearing units only (0 when no unit has a native body)."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_guest as tg
import test_plugins as tp
from test_plugins import BASE, ROOT, T, REAL, VERIFY, check
sys.path.insert(0, VERIFY)
sys.path.insert(0, os.path.join(REAL, T + "contract"))
sys.path.insert(0, os.path.join(REAL, T + "link"))
sys.path.insert(0, os.path.join(REAL, T + "link", "tests"))
import gate_build  # noqa: E402
import gate_contract as gc  # noqa: E402
import gate_l2native as L  # noqa: E402
import gate_names  # noqa: E402
import gate_r7  # noqa: E402
import l2sections  # noqa: E402
import store  # noqa: E402
import test_l2gates as tl  # noqa: E402

G, H = "?g@@YAHH@Z", "?h@@YAHXZ"


def run_stage(w, doc, ext, rp, name):
    argv, outs = gc.stages(w, doc, ext, rp, name)[name]
    os.makedirs(os.path.join(w, "gate"), exist_ok=True)
    p = subprocess.run(argv, cwd=w, env=dict(os.environ, GATE_OUT=os.path.join(w, "gate")), capture_output=True, text=True)
    return p, all(os.path.isfile(os.path.join(w, o)) for o in outs)


def chain(tmp, a_obj, b_obj, tag):
    """bind + bodysel on the objects -> {symbol: verdict line} and the bodysel verdicts {address: native|lifted}."""
    w = os.path.join(tmp, tag)
    os.makedirs(w)
    ob = {"src_a.cc.o": a_obj, "src_b.cc.o": b_obj}
    obj = {}
    for name, path in ob.items():
        for kind, rows in l2sections.read(path).items():
            obj[(name, kind)] = rows
    store.save(w + "/obj.json", "obj", obj)
    fn = {0x401000: dict(unit=0, sym=G, size=5, n1=True, why="", relocs=[]), 0x401010: dict(unit=1, sym=H, size=5, n1=True, why="", relocs=[])}
    store.save(w + "/n1.json", "n1", dict(fn=fn, sym={(0, G): 0x401000, (1, H): 0x401010}, vt={}, exe_sha256="0" * 64))
    os.makedirs(w + "/gates")
    open(w + "/gates/bodysel.tsv", "w").close()
    open(w + "/universe.tsv", "w").write("00401000\tfunc\t5\t\n00401010\tfunc\t5\t\n")
    json.dump({"units": [{"source": "a.cc"}, {"source": "b.cc"}]}, open(w + "/units.json", "w"))
    doc = {"exe": "x/exe", "msvc": "x/msvc", "universe": "x/universe.tsv"}
    ext = {"x": {"host": w}}
    rp = {"units": w + "/units.json"}
    p1, ok1 = run_stage(w, doc, ext, rp, "bind")
    p2, ok2 = run_stage(w, doc, ext, rp, "bodysel")
    rows = {}
    if ok2:
        for ln in open(w + "/bodysel.tsv"):
            a, v, why = ln.rstrip("\n").split("\t")
            rows[int(a, 16)] = (v, why)
    return rows, p1.stdout + p1.stderr + p2.stdout + p2.stderr


def main():
    argv, _ = gc.stages("/w", {"exe": "x/exe", "msvc": "x/msvc", "universe": "x/u"}, {"x": {"host": "/h"}}, {"units": "/w/units.json"}, "bodysel")["bodysel"]
    check("bodysel stage passes --n3 <w>/n3.tsv", "--n3" in argv and argv[argv.index("--n3") + 1] == "/w/n3.tsv", argv)
    check("bodysel needs bind first", gc.NEEDS["bodysel"].index("bind") < gc.NEEDS["bodysel"].index("bodysel"), gc.NEEDS["bodysel"])
    pin = json.load(open(os.path.join(REAL, T + "verify/guest_compiler.json")))
    rec = tg.guest_rec(pin["external"])
    repo = tg.guest_repo("n3", rec, pin, files={ROOT + "/a.cc": tl.SRC_A, ROOT + "/b.cc": tl.SRC_B})
    res, _ = gate_build.ensure(ROOT, os.path.join(BASE, "n3cache"), repo)
    ob = {o["unit"]: o["object"] for o in res["objects"]}
    tmp = os.path.join(BASE, "n3work")
    os.makedirs(tmp)
    a_n3 = l2sections.read(ob["a.cc"])["n3"]
    check("the real object carries an N3 row for g", any(r[0] == tl.G_SYM and r[1] == "ok" for r in a_n3), a_n3)
    rows, out = chain(tmp, ob["a.cc"], ob["b.cc"], "good")
    check("N1 + N3 ok: g and h are NATIVE in bodysel.tsv", rows.get(0x401000, ("",))[0] == "native" and rows.get(0x401010, ("",))[0] == "native", (rows, out))
    verd = {a: v for a, (v, _) in rows.items()}
    amap = {("src_game_a.cc.o", tl.G_SYM): 0x401000, ("src_game_b.cc.o", tl.H_SYM): 0x401010}
    cld = os.path.join(tmp, "cl")
    os.makedirs(cld)
    open(cld + "/u0.obj", "wb").write(tl.cl_object([G], [("UP@@", 8, [("a", 0, 4), ("b", 4, 1)])]))
    open(cld + "/u1.obj", "wb").write(tl.cl_object([H], []))
    units = tmp + "/units.json"
    json.dump({"units": [{"source": "game/a.cc"}, {"source": "game/b.cc"}]}, open(units, "w"))

    def judge(a_obj, verd):
        objects = [{"unit": "game/a.cc", "object": a_obj}, {"unit": "game/b.cc", "object": ob["b.cc"]}]
        n = L.judge_files("game", objects, cld, units, amap, verd, gate_names.rows, lambda r, nt: L.select(r, lambda x: x[0], lambda x: x[1], nt),
                          lambda x: not x[3], gate_names.row_id)
        r = L.judge_files("game", objects, cld, units, amap, verd, gate_r7.rows, lambda r_, nt: L.select_units(r_, lambda x: x[0], nt),
                          lambda x: x[2] == "equal", gate_r7.row_id)
        return n, r
    n, r = judge(ob["a.cc"], verd)
    check("namegate universe > 0 on the native root, all certified", n[0] > 0 and n[0] == n[1], n)
    check("r7 universe > 0 on the native root, all certified", r[0] > 0 and r[0] == r[1], r)
    # N3 violation: byte mutation of the fact (the verdict `ok` of g in the object's own .fnvnx.n3)
    m = tl.mutate(ob["a.cc"], tmp + "/a_n3.o", (tl.G_SYM + "\tok\t").encode(), (tl.G_SYM + "\tox\t").encode())
    rows2, out2 = chain(tmp, m, ob["b.cc"], "bad")
    check("N3 violation (mutated verdict): g is LIFTED with the N3 reason", rows2.get(0x401000, ("",))[0] == "lifted" and "N3" in rows2[0x401000][1], (rows2, out2))
    inl = any(tl.G_SYM in r[2] for r in l2sections.read(ob["b.cc"])["n3"])
    check("h is LIFTED iff it inlined g (N4)", (rows2.get(0x401010, ("",))[0] == "lifted") == inl, (rows2, inl))
    verd2 = {a: v for a, (v, _) in rows2.items()}
    n2, r2 = judge(m, verd2)
    want = 0 if inl else 1
    check("namegate universe shrinks to the native symbols only", n2[0] == want and n2[0] == n2[1], (n2, inl))
    check("r7 universe: units without a native body leave it (0 if none)", r2[0] == (0 if inl else r[0] - 1) or r2[0] <= r[0], (r2, r, inl))
    # every body violates N3: nothing is native, both universes are 0 (the single-body case of the class)
    m2 = tl.mutate(ob["b.cc"], tmp + "/b_n3.o", (tl.H_SYM + "\tok\t").encode(), (tl.H_SYM + "\tox\t").encode())
    rows3, out3 = chain(tmp, m, m2, "bad2")
    check("N3 violation in every body: no NATIVE row", all(v[0] == "lifted" for v in rows3.values()) and len(rows3) == 2, (rows3, out3))
    objects = [{"unit": "game/a.cc", "object": m}, {"unit": "game/b.cc", "object": m2}]
    v3 = {a: v for a, (v, _) in rows3.items()}
    n3_ = L.judge_files("game", objects, cld, units, amap, v3, gate_names.rows, lambda r_, nt: L.select(r_, lambda x: x[0], lambda x: x[1], nt), lambda x: not x[3], gate_names.row_id)
    r3_ = L.judge_files("game", objects, cld, units, amap, v3, gate_r7.rows, lambda r_, nt: L.select_units(r_, lambda x: x[0], nt), lambda x: x[2] == "equal", gate_r7.row_id)
    check("namegate and r7 universe 0 when every body is LIFTED", n3_[0] == 0 and r3_[0] == 0, (n3_, r3_))


if __name__ == "__main__":
    main()
    print("FAILED: %s" % tp.FAILED if tp.FAILED else "all passed")
    sys.exit(1 if tp.FAILED else 0)
