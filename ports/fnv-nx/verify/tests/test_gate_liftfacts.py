#!/usr/bin/env python3
# reads: ports/fnv-nx/verify
"""Tests for verify/gate_liftfacts.py on toy GATES directories (the producers' file shapes). Plain script: exit 0 on pass."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import gate_liftfacts as G  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)))
    fails += 0 if ok else 1


CM = {"schema": "fnvnx-gate/1", "name": "l3.codemap.x", "status": "green", "universe": 10, "certified": 10, "uncertified": 0, "entries_on_data": 0}
PC = {"schema": "fnvnx-gate/1", "name": "l3.pcsrc.x", "status": "green", "red": 0, "s4_red": 0}


def put(d, rel, doc):
    p = os.path.join(d, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(doc if isinstance(doc, str) else json.dumps(doc))


def toy(images=("exe", "a"), cm=None, pc=None, **over):
    d = tempfile.mkdtemp(prefix="lf-")
    put(d, "summary.tsv", "# " + "\t".join(G.SUMMARY_COLS) + "\n" + "".join("%s\tgreen\t10\t10\t0\t0\t0\t0\n" % i for i in images) + "total\tgreen\t20\t20\t0\t0\t0\t0\n")
    for i in images:
        put(d, G.image_dir(i) + "/gate_codemap.json", dict(CM, **(cm or {})))
        put(d, G.image_dir(i) + "/gate_pcsrc.json", dict(PC, **(pc or {})))
    return d


u, c, bad, err = G.judge(toy())
check("green: 2 images certified", (u, c, bad, err) == (2, 2, [], None), (u, c, bad, err))
d = toy()
put(d, "a/gate_codemap.json", dict(CM, uncertified=12))
u, c, bad, err = G.judge(d)
check("uncertified bytes above 0 are red", c == 1 and len(bad) == 1 and "uncertified 12" in bad[0], bad)
d = toy()
put(d, "exe/gate_codemap.json", dict(CM, entries_on_data=3))
check("entries_on_data above 0 is red", "entries_on_data 3" in G.judge(d)[2][0])
d = toy()
put(d, "a/gate_pcsrc.json", dict(PC, s4_red=2))
check("s4 rows above 0 are red", "s4 rows 2" in G.judge(d)[2][0])
d = toy()
put(d, "a/gate_pcsrc.json", dict(PC, status="red"))
check("a status that is not green is red", "pcsrc status 'red'" in G.judge(d)[2][0])
d = toy()
put(d, "a/gate_codemap.json", {k: v for k, v in CM.items() if k != "entries_on_data"})
check("an unchecked invariant (entries_on_data absent) is red", "entries_on_data absent" in G.judge(d)[2][0])
d = toy()
os.remove(os.path.join(d, "a", "gate_pcsrc.json"))
check("an image missing from GATES is red", "gate_pcsrc.json missing" in G.judge(d)[2][0])
d = toy()
import shutil
shutil.rmtree(os.path.join(d, "a"))
u, c, bad, err = G.judge(d)
check("an image whose directory is gone: universe stays the manifest's, the image is red", (u, c) == (2, 1) and "missing" in bad[0], (u, c, bad))
d = toy()
put(d, "a/gate_codemap.json", "{not json")
check("a malformed file is red", "malformed" in G.judge(d)[2][0])
d = toy()
put(d, "a/gate_codemap.json", "[1]")
check("a file that is not an object is red", "malformed" in G.judge(d)[2][0])
d = toy()
os.remove(os.path.join(d, "summary.tsv"))
check("no manifest is an error", G.judge(d)[3] is not None)
d = toy(images=())
check("an empty image list is an error", G.judge(d)[3] is not None)
d = toy()
put(d, "summary.tsv", {"images": [{"image": "a.dll"}, {"nope": 1}]})
check("a malformed image row is an error", G.judge(d)[3] is not None)

# wiring: the loader root's MODULES_TSV is read from the published out dir; absent is an error
import json as _j, tempfile as _t
w = _t.mkdtemp()
rec = os.path.join(w, "build.json")
open(rec, "w").write(_j.dumps({"outputs": {"MODULES_TSV": "gen/modules.tsv"}}))
roots = [{"path": G.LOADER_ROOT, "recipe": rec}]
check("no loader root is an error", G.modules_text_of(w, "T", [])[1] is not None)
check("an unpublished table is an error", G.modules_text_of(w, "T", roots)[1] is not None)
pub = os.path.join(w, "objects", "T", G.LOADER_ROOT.replace("/", "__"), "gen")
os.makedirs(pub)
open(os.path.join(pub, "modules.tsv"), "w").write("# x\n")
check("a published table is read", G.modules_text_of(w, "T", roots) == ("# x\n", None))
open(rec, "w").write("{}")
check("a recipe without the output is an error", G.modules_text_of(w, "T", roots)[1] is not None)
sys.exit(1 if fails else 0)
