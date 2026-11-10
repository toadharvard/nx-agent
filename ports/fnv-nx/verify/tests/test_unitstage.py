#!/usr/bin/env python3
"""Tests for verify/unitstage.py (L0: one translation unit = DEPS stage + COMPILE stage on a filtered tree) and for
`runtime.py tool`. Plain script: exit 0 on pass, one PASS/FAIL line per case. Needs root (sandbox) and aarch64-linux-gnu-g++ 13.
Store: /root/l4/unit-test (same filesystem as the hardlinks)."""
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import cas  # noqa: E402
import unitstage  # noqa: E402

BASE = "/root/l4/unit-test"
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


def build(units, flags=(), cold=False, extra=(), name="r"):
    json.dump({"argv": list(flags)}, open(BASE + "/flags.json", "w"))
    rep = "%s/%s.json" % (BASE, name)
    if os.path.exists(rep):
        os.unlink(rep)
    cmd = PY + [VERIFY + "/unitstage.py", "build", "--store", STORE, "--toolrt", TOOLRT, "--cxx", CXX,
                "--src-root", WORK + "/src@/src", "--include", WORK + "/inc0@/inc/0", "--include", WORK + "/inc1@/inc/1",
                "--flags-file", BASE + "/flags.json", "--report", rep, "--jobs", "2"]
    for u in units:
        cmd += ["--unit", u]
    cmd += (["--cold"] if cold else []) + list(extra)
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    r = json.load(open(rep)) if os.path.exists(rep) else None
    return p.returncode, r, p.stderr


def unit(r, rel):
    return r["units"][rel]


def main():
    shutil.rmtree(BASE, ignore_errors=True)
    os.makedirs(BASE)
    # which trees/exes make the gcc runtime: unitstage.gcc_set runs -print-prog-name=cc1plus / as, -print-search-dirs
    # (install dir) and `echo | g++ -x c++ -E -v -` (built-in include dirs)
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
    check("closure of a.cpp lists its files", rc == 0 and unit(r1, "u/a.cpp")["files"] >= 2)
    rc, r2, _ = build(units, cold=True, name="r2")
    check("two cold runs: identical objects, keys", rc == 0 and all(
        unit(r1, u)["object"] == unit(r2, u)["object"] and unit(r1, u)["compile_key"] == unit(r2, u)["compile_key"]
        and unit(r2, u)["compile_status"] == "run" for u in units))
    for root, _, fs in os.walk(WORK):
        for f in fs:
            os.utime(os.path.join(root, f), (12345, 12345))
    rc, r3, _ = build(units, name="r3")
    check("touching every mtime changes no key, all hit", rc == 0 and all(
        unit(r3, u)["deps_key"] == unit(r1, u)["deps_key"] and unit(r3, u)["compile_key"] == unit(r1, u)["compile_key"]
        and unit(r3, u)["compile_status"] == "hit" and unit(r3, u)["deps_status"] == "hit" for u in units))

    write("inc0/b.h", '#include "c.h"\n#define B_VAL 20\n')
    rc, r4, _ = build(units, name="r4")
    check("editing a header A does not include: A HIT, B recompiles", rc == 0
          and unit(r4, "u/a.cpp")["compile_key"] == unit(r1, "u/a.cpp")["compile_key"] and unit(r4, "u/a.cpp")["compile_status"] == "hit"
          and unit(r4, "u/b.cpp")["compile_key"] != unit(r1, "u/b.cpp")["compile_key"] and unit(r4, "u/b.cpp")["compile_status"] == "run")
    write("inc0/c.h", "#define C_VAL 30\n")
    rc, r5, _ = build(units, name="r5")
    check("editing a header included only through another header recompiles", rc == 0
          and unit(r5, "u/b.cpp")["compile_key"] != unit(r4, "u/b.cpp")["compile_key"] and unit(r5, "u/a.cpp")["compile_status"] == "hit")
    write("inc0/unrelated.h", "#define U 10\n")
    rc, r5b, _ = build(units, name="r5b")
    check("editing a header nobody includes recompiles nothing", rc == 0 and all(
        unit(r5b, u)["compile_status"] == "hit" for u in units))

    write("inc0/shadow.h", "#define S_VAL 6\n")
    rc, r6, _ = build(units, name="r6")
    check("new header earlier in the include path shadows: DEPS result and compile key change", rc == 0
          and unit(r6, "u/s.cpp")["compile_key"] != unit(r5b, "u/s.cpp")["compile_key"]
          and unit(r6, "u/s.cpp")["compile_status"] == "run" and unit(r6, "u/a.cpp")["compile_status"] == "hit")

    os.unlink(WORK + "/inc0/c.h")
    rc, r7, _ = build(units, name="r7")
    b = unit(r7, "u/b.cpp") if r7 else {}
    check("deleting an included header: DEPS fails, nothing compiled", rc == 2 and b.get("status") == "deps-failed"
          and b.get("deps_rc") not in (0, None) and b.get("compile_status") == "skipped" and unit(r7, "u/a.cpp")["status"] == "ok")
    write("inc0/c.h", "#define C_VAL 3\n")

    # deps.d parser (pure function of the text)
    pd = unitstage.parse_deps
    check("parser: continuations", pd("x: /src/a.cpp \\\n /inc/0/a.h \\\n /inc/0/b.h\n") == ["/inc/0/a.h", "/inc/0/b.h", "/src/a.cpp"])
    check("parser: escaped space, hash, dollar", pd("x: /src/a\\ b.cpp /inc/0/c\\#d.h /inc/0/e$$f.h\n")
          == ["/inc/0/c#d.h", "/inc/0/e$f.h", "/src/a b.cpp"])
    check("parser: doubled backslash before a space is a literal backslash then a separator",
          pd("x: /inc/0/a\\\\ /inc/0/b\\\\\\ c.h\n") == ["/inc/0/a\\", "/inc/0/b\\ c.h"])
    check("parser: normalizes and dedupes", pd("x: /a/../b/./c.h /b/c.h\n") == ["/b/c.h"])
    bad = []
    for t in ("y: /a.h\n", "x: /a.h\nz: /b.h\n", "x: /a$b.h\n", "x: rel.h\n", "x: /a.h #c\n"):
        try:
            pd(t)
            bad.append(t)
        except unitstage.UnitError:
            pass
    check("parser: malformed rules are errors", not bad, bad)

    # __has_include: does GCC 13 -M list a header that is only probed?
    write("src/u/h.cpp", '#if __has_include("probed.h")\nint yes_marker;\n#else\nint no_marker;\n#endif\n')
    write("inc0/probed.h", "int probed;\n")
    rc, r8, _ = build(["u/h.cpp"], name="r8")
    h = unit(r8, "u/h.cpp") if r8 else {}
    d = unitstage.deps_of(STORE, h["deps_key"]) if h else []
    listed = "/inc/0/probed.h" in d
    d2 = BASE + "/h.o"
    subprocess.run([CXX, "-I" + WORK + "/inc0", "-c", WORK + "/src/u/h.cpp", "-o", d2], check=True)
    full = subprocess.run([CXX.replace("g++", "nm"), d2], stdout=subprocess.PIPE, text=True).stdout
    out = BASE + "/hobj"
    cas.materialize(h["object"], STORE, out)
    sub = subprocess.run([CXX.replace("g++", "nm"), out + "/unit.o"], stdout=subprocess.PIPE, text=True).stdout
    if not listed and "yes_marker" in full and "no_marker" in sub:
        print("OPEN: GCC 13 -M does not list a header tested only by __has_include; the COMPILE stage (filtered tree) takes the "
              "#else branch, the full tree takes #if. No exact GCC mechanism found; the object differs (full: yes_marker, filtered: no_marker).")
        check("__has_include gap is as documented (expected-open)", True)
    else:
        check("__has_include gap is as documented (expected-open)", False, "listed=%s full=%r sub=%r" % (listed, full, sub))

    # __DATE__ and the flag checker
    write("src/u/d.cpp", 'const char *d = __DATE__;\n')
    rc, r9, _ = build(["u/d.cpp"], name="r9")
    check("__DATE__ fails by -Werror=date-time", rc == 2 and unit(r9, "u/d.cpp")["status"] != "ok" and unit(r9, "u/d.cpp")["object"] is None)
    rc, _, err = build(["u/a.cpp"], flags=["-Wno-error=date-time"], name="r10")
    check("a caller flag that violates toolflags is an error", rc == 1 and "toolflags" in err, err[-200:])
    rc, _, err = build(["u/a.cpp"], flags=["-M"], name="r11")
    check("caller flags that change the stage's job are refused", rc == 1, err[-200:])

    docs = sorted(os.path.join(STORE, "stage-docs", f) for f in os.listdir(STORE + "/stage-docs"))
    tf = subprocess.run(PY + [VERIFY + "/toolflags.py", "check"] + docs, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    check("toolflags clean for every DEPS and COMPILE document", tf.returncode == 0 and len(docs) >= 6, tf.stdout[-300:])

    a_obj = BASE + "/aobj"
    cas.materialize(unit(r1, "u/a.cpp")["object"], STORE, a_obj)
    data = open(a_obj + "/unit.o", "rb").read()
    check("object holds no host path", WORK.encode() not in data and BASE.encode() not in data and b"/root/" not in data)
    shutil.rmtree(WORK, ignore_errors=True)
    print("%d failed" % len(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
