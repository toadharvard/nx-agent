#!/usr/bin/env python3
"""Tests for the L2 per-root gates abi, namegate, r7 (verify/gate_abi.py, gate_namegate.py, gate_r7gate.py, gate_l2native.py). Plain script, needs root
(sandbox) and the pinned guest compiler (as test_argorder.py). Objects are the real ones of a toy native guest root built by recipe.py; every negative case is
a byte mutation of such an object. The only synthetic input is the cl REFERENCE side (COFF with the names / CodeView records of the same toy source, link/tests/synth.py):
the pristine MSVC objects are exe-derived and not in the repository. Cases: no root -> universe 0 green (3 gates); abi positive / mutated contract name /
missing `.fnvnx.sympair`; namegate and r7 on files (positive, mutated object red, symbol LIFTED outside the universe, symbol with no exe address outside);
a root with `game` and no link result is red naming the input; a root without `game` is universe 0."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_guest as tg
import test_plugins as tp
from test_plugins import BASE, ROOT, T, REAL, VERIFY, git, w, REC

sys.path.insert(0, VERIFY)
sys.path.insert(0, os.path.join(REAL, T + "link", "tests"))
import gate_abi  # noqa: E402
import gate_l2native as L  # noqa: E402
import gate_build  # noqa: E402
import gate_names  # noqa: E402
import gate_r7  # noqa: E402
import synth  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

GATES = {"abi": "gate_abi.py", "namegate": "gate_namegate.py", "r7": "gate_r7gate.py"}
SRC_A = "struct P { int a; char b; };\nint g(int x) { P p; p.a = x; return p.a; }\n"
SRC_B = "int g(int);\nint h() { return g(1); }\n"
G_SYM, H_SYM = "_Z1gi", "_Z1hv"


def gate(name, repo, cache):
    out = os.path.join(BASE, "out-%s-%s" % (name, os.path.basename(repo)))
    os.makedirs(out, exist_ok=True)
    p = subprocess.run([sys.executable, "-I", os.path.join(VERIFY, GATES[name])], cwd=repo, env=dict(os.environ, GATE_OUT=out, GATE_CACHE=cache),
                       capture_output=True, text=True)
    f = os.path.join(out, name + ".json")
    return p.returncode, (json.load(open(f)) if os.path.exists(f) else {}), p.stdout + p.stderr


def mutate(src, dst, old, new):
    """Copy of the real object SRC with the bytes OLD (which occur exactly once) replaced by NEW (same length)."""
    d = open(src, "rb").read()
    assert len(old) == len(new) and d.count(old) == 1, (old, d.count(old))
    open(dst, "wb").write(d.replace(old, new))
    return dst


def cl_object(names, layouts):
    """The cl reference side of the toy: externals defined in .text and one /Z7 record per layout (key, size, [(name, off, size)])."""
    cv = synth.CV()
    for key, size, mem in layouts:
        fl = cv.add(0x1203, synth.fieldlist(*[synth.member({4: 0x74, 1: 0x10}[sz], off, n) for n, off, sz in mem]))
        synth.record(cv, "s", key, fl, size)
    syms = [dict(name=n.encode(), value=0, sec=1, type=0x20, cls=2) for n in names]
    return synth.coff([dict(name=b".text", chars=0x60000020, data=b"\xc3"), dict(name=b".debug$T", chars=0x40000040, data=cv.blob())], syms)


def main():
    pin = json.load(open(os.path.join(REAL, T + "verify/guest_compiler.json")))
    rec = tg.guest_rec(pin["external"])
    cache = os.path.join(BASE, "gate-cache")
    # --- no native guest root: universe 0, green, for each gate
    host = tg.guest_repo("l1", REC, pin, target="host")
    for n in GATES:
        rc, r, o = gate(n, host, cache)
        case("%s: no native guest root: universe 0, green", rc == 0 and r["status"] == "green" and r["universe"] == 0, o, key=n)
    # --- the toy native guest root, real compiler
    repo = tg.guest_repo("l2", rec, pin, files={ROOT + "/a.cc": SRC_A, ROOT + "/b.cc": SRC_B})
    rc, r, o = gate("abi", repo, cache)
    case("abi: native guest root on the pinned compiler: green, universe >= 1", rc == 0 and r["status"] == "green" and r["universe"] >= 1 and r["certified"] == r["universe"], (rc, r, o))
    res, _ = gate_build.ensure(ROOT, cache, repo)
    ob = {o["unit"]: o["object"] for o in res["objects"]}
    tmp = os.path.join(BASE, "mut")
    os.makedirs(tmp)
    objs = [("a.cc", ob["a.cc"]), ("b.cc", ob["b.cc"])]
    u, c, ids, d = gate_abi.judge_objects(objs)
    case("abi: the real objects: all items certified", u >= 1 and u == c, (u, c, ids, d))
    bm = mutate(ob["b.cc"], tmp + "/b_ms.o", b"?g@@YAHH@Z\tfunc\tref\n", b"?g@@YAHM@Z\tfunc\tref\n")
    u2, c2, ids2, d2 = gate_abi.judge_objects([objs[0], ("b.cc", bm)])
    case("abi: byte mutation of the reference's contract name (int -> float parameter): red, names the row", c2 < u2 and any("b.cc:" + G_SYM in i for i in ids2), (u2, c2, ids2))
    bn = mutate(ob["b.cc"], tmp + "/b_nosec.o", b".fnvnx.sympair\x00", b".fnvnx.sympaiX\x00")
    u3, c3, ids3, d3 = gate_abi.judge_objects([objs[0], ("b.cc", bn)])
    case("abi: object without .fnvnx.sympair: red, names the object", c3 < u3 and any(i.startswith("b.cc:sympair") for i in ids3), (u3, c3, ids3))
    # --- namegate / r7 on files: real GCC objects, cl reference side of the same source
    cld = os.path.join(tmp, "cl")
    os.makedirs(cld)
    open(cld + "/u0.obj", "wb").write(cl_object(["?g@@YAHH@Z"], [("UP@@", 8, [("a", 0, 4), ("b", 4, 1)])]))
    open(cld + "/u1.obj", "wb").write(cl_object(["?h@@YAHXZ"], []))
    units = tmp + "/units.json"
    json.dump({"units": [{"source": "game/a.cc"}, {"source": "game/b.cc"}]}, open(units, "w"))
    amap = {("src_game_a.cc.o", G_SYM): 0x401000, ("src_game_b.cc.o", H_SYM): 0x401010}
    nat = {0x401000: "native", 0x401010: "native"}

    def judge(kind, o_a, o_b, verd, am=amap):
        objects = [{"unit": "game/a.cc", "object": o_a}, {"unit": "game/b.cc", "object": o_b}]
        if kind == "namegate":
            return L.judge_files("game", objects, cld, units, am, verd, gate_names.rows,
                                 lambda rows, nt: L.select(rows, lambda r: r[0], lambda r: r[1], nt), lambda r: not r[3], gate_names.row_id)
        return L.judge_files("game", objects, cld, units, am, verd, gate_r7.rows, lambda rows, nt: L.select_units(rows, lambda r: r[0], nt),
                             lambda r: r[2] == "equal", gate_r7.row_id)
    r1 = judge("namegate", ob["a.cc"], ob["b.cc"], nat)
    case("namegate: native bodies g and h carry the cl names: 2/2", r1[:2] == (2, 2), r1)
    am_ = mutate(ob["a.cc"], tmp + "/a_ms.o", b"?g@@YAHH@Z\tfunc\tdef\n", b"?g@@YAHM@Z\tfunc\tdef\n")
    r2 = judge("namegate", am_, ob["b.cc"], nat)
    case("namegate: byte mutation of the name of a native body: red, 1 uncertified", r2[0] == 2 and r2[1] == 1 and len(r2[2]) == 1 and "_Z1gi" in r2[2][0], r2)
    r3 = judge("namegate", am_, ob["b.cc"], {0x401000: "lifted", 0x401010: "native"})
    case("namegate: the uncertified symbol is LIFTED: outside the universe, green", r3[:2] == (1, 1), r3)
    r4 = judge("namegate", am_, ob["b.cc"], nat, {("src_game_b.cc.o", H_SYM): 0x401010})
    case("namegate: a symbol with no exe address is outside the universe", r4[:2] == (1, 1), r4)
    q1 = judge("r7", ob["a.cc"], ob["b.cc"], nat)
    case("r7: native-bearing unit a.cc: record UP@@ equal to cl's: 1/1", q1[:2] == (1, 1), q1)
    lm = mutate(ob["a.cc"], tmp + "/a_lay.o", b"UP@@\t8\t4\t", b"UP@@\t9\t4\t")
    q2 = judge("r7", lm, ob["b.cc"], nat)
    case("r7: byte mutation of the record size: red, names the record", q2[0] == 1 and q2[1] == 0 and "UP@@" in q2[2][0], q2)
    q3 = judge("r7", lm, ob["b.cc"], {0x401000: "lifted", 0x401010: "native"})
    case("r7: the unit has no native body (a.cc lifted): outside the universe, green", q3[:2] == (0, 0), q3)
    # --- a root with `game` but no link: red naming the input; a root without `game`: universe 0
    withgame = dict(rec, game="game")
    gm = tg.guest_repo("l3", withgame, pin, files={ROOT + "/a.cc": SRC_A})
    for n in ("namegate", "r7"):
        rc, r, o = gate(n, gm, cache)
        case("%s: root with game and no link result: red item <root>:inputs", rc == 2 and r["uncertified"] == [ROOT + ":inputs"], (rc, r, o), key=n)
        rc, r, o = gate(n, repo, cache)
        case("%s: root without game: universe 0, green", rc == 0 and r["universe"] == 0 and r["status"] == "green", (rc, r, o), key=n)


if __name__ == "__main__":
    main()
    print("FAILED: %s" % tp.FAILED if tp.FAILED else "all passed")
    sys.exit(1 if tp.FAILED else 0)
