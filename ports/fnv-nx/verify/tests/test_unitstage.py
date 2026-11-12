#!/usr/bin/env python3
"""Tests for verify/unitstage.py (L0: one translation unit = PP stage on name-complete views + CC stage on the preprocessed
text) and for `runtime.py tool`. Plain script: exit 0 on pass, one PASS/FAIL line per case. Needs root (sandbox) and aarch64-linux-gnu-g++ 13.
Scratch: a fresh mkdtemp under /root/l4 (or ~, same filesystem as the hardlinks), removed at exit."""
import argparse
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import cas  # noqa: E402
import unitstage  # noqa: E402

BASE = tempfile.mkdtemp(dir="/root/l4" if os.path.isdir("/root/l4") else os.path.expanduser("~"), prefix="unit-test-")  # own dir per run: files run concurrently
atexit.register(shutil.rmtree, BASE, ignore_errors=True)
STORE, WORK, TOOLRT = BASE + "/store", BASE + "/work", BASE + "/toolrt.json"
CXX = "/usr/bin/aarch64-linux-gnu-g++"
PY = [sys.executable, "-I"]
FAILED = []


def check(name, ok, detail=""):
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, (": " + str(detail)) if detail and not ok else ""))
    if not ok:
        FAILED.append(name)
    return ok


def write(rel, text):
    p = os.path.join(WORK, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)


def project():
    shutil.rmtree(WORK, ignore_errors=True)
    write("src/u/a.cpp", '#include "a.h"\nint fa() { return A_VAL; }\n')
    write("src/u/b.cpp", '#include "b.h"\nint fb() { return B_VAL + C_VAL; }\n')
    write("src/u/s.cpp", '#include <shadow.h>\nint fs() { return S_VAL; }\n')
    write("inc0/a.h", "#define A_VAL 1\n")
    write("inc0/b.h", '#include "c.h"\n#define B_VAL 2\n')
    write("inc0/c.h", "#define C_VAL 3\n")
    write("inc0/unrelated.h", "#define U 9\n")
    write("inc1/shadow.h", "#define S_VAL 5\n")
    write("inc0/force.h", "#define FORCED 7\n")
    write("src/u/f.cpp", "int ff() { return FORCED; }\n")
    write("inc0/n.h", "#include_next <n.h>\nint n_first;\n")
    write("inc1/n.h", "int n_second;\n")
    write("src/u/n.cpp", "#include <n.h>\n")
    write("src/u/ci.cpp", '#define HDR "c.h"\n#include HDR\n#include HDR\n#include "a.h"\n#include "a.h"\nint fc = C_VAL + A_VAL;\n')
    write("inc0/hn.h", "#if __has_include_next(<q.h>)\nint next_yes;\n#else\nint next_no;\n#endif\n")
    write("src/u/hn.cpp", '#include "hn.h"\n')


def build(units, flags=(), cold=False, extra=(), name="r", toolrt=None, inc=None):
    json.dump({"argv": list(flags)}, open(BASE + "/flags.json", "w"))
    rep = "%s/%s.json" % (BASE, name)
    if os.path.exists(rep):
        os.unlink(rep)
    cmd = PY + [VERIFY + "/unitstage.py", "build", "--store", STORE, "--toolrt", TOOLRT, "--cxx", CXX,
                "--src-root", WORK + "/src@/src", "--flags-file", BASE + "/flags.json", "--report", rep, "--jobs", "2"]
    cmd[cmd.index("--toolrt") + 1] = toolrt or TOOLRT
    for i in inc or [WORK + "/inc0@/inc/0", WORK + "/inc1@/inc/1"]:
        cmd += ["--include", i]
    for u in units:
        cmd += ["--unit", u]
    cmd += (["--cold"] if cold else []) + list(extra)
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    r = json.load(open(rep)) if os.path.exists(rep) else None
    return p.returncode, r, p.stderr


def syms(store_obj_hash, name):
    out = BASE + "/obj-" + name
    shutil.rmtree(out, ignore_errors=True)
    cas.materialize(store_obj_hash, STORE, out)
    return subprocess.run([CXX.replace("g++", "nm"), out + "/unit.o"], stdout=subprocess.PIPE, text=True).stdout


def reference(rel, flags=()):
    """One sandbox compile of the whole trees (no stages): what the unit's object must equal."""
    json.dump({"argv": list(flags)}, open(BASE + "/flags.json", "w"))
    a = argparse.Namespace(store=STORE, cold=False, cxx=CXX, toolrt=TOOLRT, flags_file=BASE + "/flags.json",
                           src_root=WORK + "/src@/src", include=[WORK + "/inc0@/inc/0", WORK + "/inc1@/inc/1"])
    b = unitstage.Build(a)
    doc = b.doc("ref", rel, b.roots, ["-c", "-o", "/out/unit.o"], "/src/" + rel)
    return unitstage.run_stage(STORE, doc, False)[1]["outputs"]["/out"]


def unit(r, rel):
    return r["units"][rel]


def main():
    shutil.rmtree(BASE, ignore_errors=True)
    os.makedirs(BASE)
    exes, trees = unitstage.gcc_set(CXX)
    cmd = PY + [VERIFY + "/runtime.py", "tool", "--store", STORE, "-o", TOOLRT]
    for e in exes:
        cmd += ["--exe", e]
    for t in trees:
        cmd += ["--tree", t]
    t0 = time.time()
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    rt = json.load(open(TOOLRT)) if p.returncode == 0 else {}
    check("runtime.py tool builds the gcc runtime", p.returncode == 0 and rt.get("inputs") and CXX in rt.get("exes", []), p.stderr)
    print("INFO toolrt: %d inputs, %.1f s" % (len(rt.get("inputs", [])), time.time() - t0))

    project()
    units = ["u/a.cpp", "u/b.cpp", "u/s.cpp"]
    rc, r1, err = build(units)
    check("cold build ok", rc == 0 and all(unit(r1, u)["status"] == "ok" for u in units), err[-300:])
    check("closure of a.cpp lists its files", rc == 0 and unit(r1, "u/a.cpp")["files"] == 2
          and sorted(unit(r1, "u/a.cpp")["closure"]) == ["/inc/0/a.h", "/src/u/a.cpp"])
    check("no hint: 2 PP iterations (full tree, then minimal view); cc runs", rc == 0 and all(
        unit(r1, u)["pp_iterations"] == 2 and unit(r1, u)["pp_status"] == "run" and unit(r1, u)["cc_status"] == "run"
        and unit(r1, u)["ii_bytes"] > 0 for u in units))
    check("name_tree_id is one value per build", len({unit(r1, u)["name_tree_id"] for u in units}) == 1 and r1["totals"].get("name_tree_id"))
    rc, r2, _ = build(units, cold=True, name="r2")
    check("two cold runs: identical objects, keys", rc == 0 and all(
        unit(r1, u)["object"] == unit(r2, u)["object"] and unit(r1, u)["cc_key"] == unit(r2, u)["cc_key"]
        and unit(r1, u)["pp_key"] == unit(r2, u)["pp_key"] and unit(r2, u)["cc_status"] == "run" for u in units))
    for root, _, fs in os.walk(WORK):
        for f in fs:
            os.utime(os.path.join(root, f), (12345, 12345))
    rc, r3, _ = build(units, name="r3")
    check("touching every mtime changes no key, all hit", rc == 0 and all(
        unit(r3, u)["pp_key"] == unit(r1, u)["pp_key"] and unit(r3, u)["cc_key"] == unit(r1, u)["cc_key"]
        and unit(r3, u)["cc_status"] == "hit" and unit(r3, u)["pp_status"] == "hit" and unit(r3, u)["pp_iterations"] == 1 for u in units))
    check("staged object == one-shot sandbox compile of the whole trees", all(
        unit(r1, u)["object"] == reference(u) for u in units))

    # edits: the PP stage is keyed on the name trees and the content of the closure only
    write("inc0/unrelated.h", "#define U 10\n")
    rc, r4, _ = build(units, name="r4")
    check("editing a header no unit reads: every PP stage and CC stage is a HIT", rc == 0 and all(
        unit(r4, u)["pp_status"] == "hit" and unit(r4, u)["pp_key"] == unit(r1, u)["pp_key"] and unit(r4, u)["cc_status"] == "hit" for u in units))
    write("inc0/b.h", '#include "c.h"\n#define B_VAL 2 // only a comment\n')
    rc, r5, _ = build(units, name="r5")
    check("comment-only edit of a read header: PP re-runs, CC is a HIT, object identical", rc == 0
          and unit(r5, "u/b.cpp")["pp_status"] == "run" and unit(r5, "u/b.cpp")["pp_key"] != unit(r1, "u/b.cpp")["pp_key"]
          and unit(r5, "u/b.cpp")["cc_status"] == "hit" and unit(r5, "u/b.cpp")["cc_key"] == unit(r1, "u/b.cpp")["cc_key"]
          and unit(r5, "u/b.cpp")["object"] == unit(r1, "u/b.cpp")["object"]
          and unit(r5, "u/a.cpp")["pp_status"] == "hit" and unit(r5, "u/a.cpp")["cc_status"] == "hit")
    write("inc0/b.h", '#include "c.h"\n#define B_VAL 20\n')
    rc, r6, _ = build(units, name="r6")
    check("semantic edit of a read header: PP and CC re-run, object changes", rc == 0
          and unit(r6, "u/b.cpp")["pp_status"] == "run" and unit(r6, "u/b.cpp")["cc_status"] == "run"
          and unit(r6, "u/b.cpp")["object"] != unit(r1, "u/b.cpp")["object"] and unit(r6, "u/a.cpp")["cc_status"] == "hit")
    write("inc0/c.h", "#define C_VAL 30\n")
    rc, r7, _ = build(units, name="r7")
    check("editing a header read only through another header re-runs", rc == 0
          and unit(r7, "u/b.cpp")["cc_key"] != unit(r6, "u/b.cpp")["cc_key"] and unit(r7, "u/a.cpp")["cc_status"] == "hit")
    write("inc0/c.h", "#define C_VAL 3\n")

    write("inc0/shadow.h", "#define S_VAL 6\n")
    rc, r8, _ = build(units, name="r8")
    check("new file earlier in the search path: name tree changes, PP re-runs and picks it", rc == 0
          and unit(r8, "u/s.cpp")["name_tree_id"] != unit(r1, "u/s.cpp")["name_tree_id"]
          and unit(r8, "u/s.cpp")["pp_status"] == "run" and "/inc/0/shadow.h" in unit(r8, "u/s.cpp")["closure"]
          and unit(r8, "u/s.cpp")["object"] != unit(r1, "u/s.cpp")["object"]
          and unit(r8, "u/s.cpp")["object"] == reference("u/s.cpp"))
    os.unlink(WORK + "/inc0/shadow.h")

    # hints change the number of iterations, never the result
    b1 = unit(r1, "u/b.cpp")
    b6 = unit(r6, "u/b.cpp")
    shutil.rmtree(STORE + "/hints", ignore_errors=True)
    unitstage.write_hint(STORE, "u/b.cpp", ["/src/u/b.cpp", "/inc/0/b.h"])
    rc, r9, _ = build(["u/b.cpp"], name="r9")
    e = unit(r9, "u/b.cpp") if r9 else {}
    check("wrong hint (misses c.h): succeeds with >= 2 iterations, same keys and object as without", rc == 0
          and e["pp_iterations"] >= 2 and e["pp_key"] == b6["pp_key"] and e["cc_key"] == b6["cc_key"] and e["object"] == b6["object"]
          and sorted(e["closure"]) == sorted(b6["closure"]), e)
    unitstage.write_hint(STORE, "u/b.cpp", ["/src/u/b.cpp", "/inc/0/b.h", "/inc/0/c.h", "/inc/0/a.h", "/inc/0/unrelated.h", "/inc/1/shadow.h",
                                            "/inc/0/gone-since.h"])
    rc, r10, _ = build(["u/b.cpp"], name="r10")
    e = unit(r10, "u/b.cpp") if r10 else {}
    check("hint with extra files (and a missing one): after minimisation the same pp_key, 2 iterations", rc == 0
          and e["pp_key"] == b6["pp_key"] and e["pp_iterations"] == 2 and e["object"] == b6["object"], e)
    unitstage.write_hint(STORE, "u/b.cpp", ["/src/u/b.cpp", "/inc/0/b.h", "/inc/0/c.h"])
    rc, r11, _ = build(["u/b.cpp"], name="r11")
    check("exact hint: one PP iteration", rc == 0 and unit(r11, "u/b.cpp")["pp_iterations"] == 1)

    # soundness list
    rc, rs, err = build(["u/n.cpp", "u/ci.cpp", "u/hn.cpp"], name="rs")
    n, ci = unit(rs, "u/n.cpp") if rs else {}, unit(rs, "u/ci.cpp") if rs else {}
    check("#include_next chain: both headers in the closure, object has both symbols", rc == 0
          and {"/inc/0/n.h", "/inc/1/n.h"} <= set(n["closure"]) and "n_first" in syms(n["object"], "n") and "n_second" in syms(n["object"], "n"), err[-200:])
    write("inc1/n.h", "int n_second_v2;\n")
    rc, rs2, _ = build(["u/n.cpp"], name="rs2")
    check("editing the #include_next target re-runs", rc == 0 and unit(rs2, "u/n.cpp")["cc_status"] == "run"
          and "n_second_v2" in syms(unit(rs2, "u/n.cpp")["object"], "n2"))
    check("computed include, headers included twice: closure has each once, object == full-tree compile", rc == 0
          and ci["closure"].count("/inc/0/c.h") == 1 and ci["object"] == reference("u/ci.cpp"))
    hn = unit(rs, "u/hn.cpp")
    check("__has_include_next, probed header absent: else branch == full tree", hn["status"] == "ok" and "next_no" in syms(hn["object"], "hn")
          and hn["object"] == reference("u/hn.cpp"))
    write("inc1/q.h", "int q;\n")
    rc, rs3, _ = build(["u/hn.cpp"], name="rs3")
    hn2 = unit(rs3, "u/hn.cpp")
    check("__has_include_next, probed header added later: name tree, PP key, object change; == full tree", rc == 0
          and hn2["name_tree_id"] != hn["name_tree_id"] and hn2["pp_key"] != hn["pp_key"] and "next_yes" in syms(hn2["object"], "hn2")
          and hn2["object"] == reference("u/hn.cpp"))
    rc, rf, _ = build(["u/f.cpp"], flags=["-include", "/inc/0/force.h"], name="rf")
    check("force-included header is in the closure; CC needs no include root", rc == 0 and "/inc/0/force.h" in unit(rf, "u/f.cpp")["closure"]
          and unit(rf, "u/f.cpp")["object"] == reference("u/f.cpp", ["-include", "/inc/0/force.h"]))

    # __has_include: closed
    write("src/u/h.cpp", '#if __has_include("probed.h")\nint yes_marker;\n#else\nint no_marker;\n#endif\n')
    rc, h1, _ = build(["u/h.cpp"], name="h1")
    e1 = unit(h1, "u/h.cpp") if h1 else {}
    check("__has_include, file absent: staged object == full-tree object (else branch)", rc == 0 and e1["object"] == reference("u/h.cpp")
          and "no_marker" in syms(e1["object"], "h1"))
    write("inc0/probed.h", "int probed;\n")
    rc, h2, _ = build(["u/h.cpp"], name="h2")
    e2 = unit(h2, "u/h.cpp") if h2 else {}
    check("__has_include, file added later: name_tree_id, PP key and object change; staged == full-tree object", rc == 0
          and e2["name_tree_id"] != e1["name_tree_id"] and e2["pp_key"] != e1["pp_key"] and e2["object"] != e1["object"]
          and e2["object"] == reference("u/h.cpp") and "yes_marker" in syms(e2["object"], "h2"), e2)
    check("a probed-only file is not read: it is not in the closure", "/inc/0/probed.h" not in e2.get("closure", ["/inc/0/probed.h"]))

    # errors
    os.unlink(WORK + "/inc0/c.h")
    rc, r12, _ = build(units, name="r12")
    b = unit(r12, "u/b.cpp") if r12 else {}
    check("deleting a needed header: the compiler's own error is reported, nothing compiled", rc == 2 and b.get("status") == "pp-failed"
          and "c.h" in b.get("error", "") and b.get("cc_status") == "skipped" and b.get("object") is None and unit(r12, "u/a.cpp")["status"] == "ok")
    write("inc0/c.h", "#define C_VAL 3\n")
    write("inc0/e.h", "#error boom_from_header\n")
    write("src/u/e.cpp", '#include "e.h"\nint e;\n')
    write("src/u/e2.cpp", "#error boom_in_unit\n")
    rc, r13, _ = build(["u/e.cpp", "u/e2.cpp"], name="r13")
    e, e2_ = (unit(r13, "u/e.cpp"), unit(r13, "u/e2.cpp")) if r13 else ({}, {})
    check("a genuine #error is the unit's error, not looped (no hint: 1 iteration)", rc == 2 and e.get("status") == "pp-failed"
          and "boom_from_header" in e["error"] and e["pp_iterations"] == 1 and "boom_in_unit" in e2_.get("error", "") and e2_["pp_iterations"] == 1, e)
    unitstage.write_hint(STORE, "u/e.cpp", ["/src/u/e.cpp"])
    rc, r14, _ = build(["u/e.cpp"], name="r14")
    e = unit(r14, "u/e.cpp") if r14 else {}
    check("a genuine #error behind a placeholder: closure grows once, then the real error is reported", rc == 2
          and e.get("status") == "pp-failed" and e["pp_iterations"] == 2 and "boom_from_header" in e["error"]
          and "outside the declared closure" not in e["error"], e)
    check("failed units leave no hint", not os.path.exists(unitstage.hint_path(STORE, "u/e2.cpp")))

    # __DATE__ and the flag checker
    write("src/u/d.cpp", 'const char *d = __DATE__;\n')
    rc, r15, _ = build(["u/d.cpp"], name="r15")
    check("__DATE__ fails by -Werror=date-time (at the PP stage)", rc == 2 and unit(r15, "u/d.cpp")["status"] == "pp-failed"
          and unit(r15, "u/d.cpp")["object"] is None)
    rc, _, err = build(["u/a.cpp"], flags=["-Wno-error=date-time"], name="r16")
    check("a caller flag that violates toolflags is an error", rc == 1 and "toolflags" in err, err[-200:])
    rc, _, err = build(["u/a.cpp"], flags=["-M"], name="r17")
    check("caller flags that change the stage's job are refused", rc == 1, err[-200:])

    # (a) -I of a path inside an external input (a declared input of the tool runtime); (b) .S/.s units; (c) failure record
    write("ext/x/inc/xe.h", "#define XE 11\n")
    write("ext/x/inc/a.h", "#define A_VAL 99\n")
    xrt = BASE + "/toolrt-x.json"
    cmd = PY + [VERIFY + "/runtime.py", "tool", "--store", STORE, "-o", xrt, "--tree", WORK + "/ext/x@/ext/x"]
    for e in exes:
        cmd += ["--exe", e]
    for t in trees:
        cmd += ["--tree", t]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    check("tool runtime with an external tree at /ext/x", p.returncode == 0, p.stderr)
    write("src/u/x.cpp", '#include "xe.h"\n#include "a.h"\nint fx() { return XE + A_VAL; }\n')
    incs = [WORK + "/inc0@/inc/0", "/ext/x/inc", WORK + "/inc1@/inc/1"]
    rc, rx, err = build(["u/x.cpp"], name="rx", toolrt=xrt, inc=incs)
    x = unit(rx, "u/x.cpp") if rx else {}
    check("-I of an external path: header found there (covered by the declared input, not the closure), search order kept (/inc/0 a.h wins)", rc == 0 and x.get("status") == "ok"
          and x["closure"] == ["/inc/0/a.h", "/src/u/x.cpp"], (err, x))
    sx = (rx or {}).get("units", {}).get("u/x.cpp", {}).get("object")
    check("-I of an external path: object computes XE + A_VAL from both", sx and "fx" in syms(sx, "x"))
    write("ext/x/inc/xe.h", "#define XE 12\n")
    xrt2 = BASE + "/toolrt-x2.json"
    subprocess.run(cmd[:cmd.index("-o") + 1] + [xrt2] + cmd[cmd.index("-o") + 2:], check=True, stdout=subprocess.PIPE)
    rc, rx2, err = build(["u/x.cpp"], name="rx2", toolrt=xrt2, inc=incs)
    x2 = unit(rx2, "u/x.cpp") if rx2 else {}
    check("changed external content: PP and CC keys change (stage keys stay content-addressed)", rc == 0 and x2.get("pp_key") != x.get("pp_key")
          and x2.get("cc_key") != x.get("cc_key"), err)
    rc, _, err = build(["u/x.cpp"], name="rx3", toolrt=TOOLRT, inc=incs)
    check("-I of a path in no declared input is an error naming the path", rc == 1 and "/ext/x/inc" in err, err[-200:])
    write("src/u/s1.S", '#include "sasm.h"\n.text\n.globl s1\ns1:\n mov w0, #SV\n ret\n')
    write("inc0/sasm.h", "#define SV 5\n")
    write("src/u/s2.s", ".text\n.globl s2\ns2:\n mov w0, #3\n ret\n")
    write("src/u/s3.S", '#include "sasm.h"\n.text\n.globl s3\ns3:\n mov w0, #SV+1\n ret\n')
    au = ["u/s1.S", "u/s2.s", "u/s3.S"]
    rc, ra, err = build(au, name="ra")
    check("asm units: .S preprocessed in its own stage, .s assembled alone; all ok with symbols", rc == 0 and all(unit(ra, u)["status"] == "ok" for u in au)
          and "T s1" in syms(unit(ra, "u/s1.S")["object"], "s1") and "T s2" in syms(unit(ra, "u/s2.s")["object"], "s2"), (err, ra))
    s1 = unit(ra, "u/s1.S")
    check(".S closure is exactly the headers it includes", s1.get("closure") == ["/inc/0/sasm.h", "/src/u/s1.S"], s1.get("closure"))
    write("inc0/unrelated.h", "#define U 10\n")
    rc, ra2, _ = build(au, name="ra2")
    check("editing a file no asm unit reads: every stage of the asm units is a HIT", rc == 0 and all(
        unit(ra2, u)["pp_status" if u.endswith(".S") else "cc_status"] == "hit" and unit(ra2, u)["cc_status"] == "hit" for u in au), ra2)
    write("inc0/sasm.h", "#define SV 6\n")
    rc, ra3, _ = build(au, name="ra3")
    check("editing the included header: the two .S units re-run, the .s unit is a HIT", rc == 0 and unit(ra3, "u/s1.S")["cc_status"] == "run"
          and unit(ra3, "u/s3.S")["cc_status"] == "run" and unit(ra3, "u/s2.s")["cc_status"] == "hit"
          and unit(ra3, "u/s1.S")["object"] != s1["object"], ra3)
    write("src/u/s4.s", '.include "sasm.h"\n')
    rc, ra4, _ = build(["u/s4.s"], name="ra4")
    check(".s with .include: reads nothing outside its own file, the assembler's error is the unit's error", rc == 2
          and unit(ra4, "u/s4.s")["status"] == "cc-failed" and "sasm.h" in unit(ra4, "u/s4.s")["error"], ra4)
    write("src/u/many.cpp", "".join("#error boom_%d\n" % i for i in range(30)))
    rc, rm, _ = build(["u/many.cpp"], name="rm")
    em = unit(rm, "u/many.cpp") if rm else {}
    check("failure record keeps the last 20 lines of stderr", rc == 2 and 20 <= len(em.get("error", "").splitlines()) <= 21 and "boom_29" in em["error"]
          and "boom_0\n" not in em["error"] + "\n" and len(em["error"]) > 300, em.get("error", "")[:300])

    docs = sorted(os.path.join(r, f) for r, _, fs in os.walk(STORE + "/stage-docs") for f in fs)
    tf = subprocess.run(PY + [VERIFY + "/toolflags.py", "check"] + docs, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    check("toolflags clean for every PP and CC document", tf.returncode == 0 and len(docs) >= 12, tf.stdout[-300:])

    # deps.d parser (pure function of the text)
    pd = unitstage.parse_deps
    check("parser: continuations", pd("x: /src/a.cpp \\\n /inc/0/a.h \\\n /inc/0/b.h\n") == ["/inc/0/a.h", "/inc/0/b.h", "/src/a.cpp"])
    check("parser: escaped space, hash, dollar", pd("x: /src/a\\ b.cpp /inc/0/c\\#d.h /inc/0/e$$f.h\n")
          == ["/inc/0/c#d.h", "/inc/0/e$f.h", "/src/a b.cpp"])
    check("parser: doubled backslash before a space is a literal backslash then a separator",
          pd("x: /inc/0/a\\\\ /inc/0/b\\\\\\ c.h\n") == ["/inc/0/a\\", "/inc/0/b\\ c.h"])
    check("parser: dedupes", pd("x: /b/c.h /b/c.h\n") == ["/b/c.h"])
    bad = []
    for t in ("y: /a.h\n", "x: /a.h\nz: /b.h\n", "x: /a$b.h\n", "x: rel.h\n", "x: /a.h #c\n"):
        try:
            pd(t)
            bad.append(t)
        except unitstage.UnitError:
            pass
    check("parser: malformed rules are errors", not bad, bad)

    # no host path in the preprocessed text or the object
    a = unit(r1, "u/a.cpp")
    ii = open(unitstage.result_file(STORE, a["pp_key"], "unit.ii"), "rb").read()
    obj = syms(a["object"], "a") and open(BASE + "/obj-a/unit.o", "rb").read()
    check("unit.ii and unit.o hold no host path", all(x.encode() not in blob for blob in (ii, obj) for x in (WORK, BASE, "/root/")) and b'"/src/u/a.cpp"' in ii)
    shutil.rmtree(WORK, ignore_errors=True)
    print("%d failed" % len(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
