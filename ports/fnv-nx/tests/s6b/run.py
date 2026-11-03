#!/usr/bin/env python3
"""tests/s6b runner: assemble the corpus, pack synthetic .sdp packages, run sdp2dksh, check.

  tests/s6b/run.py [--build DIR] [--out DIR]

DIR defaults follow render/shaders/build.sh (FNVNX_SHADERS_BUILD). Prints a Markdown matrix:
per shader: assemble, translate (front end + GLSL), uam DKSH (+ header validation), expectations,
and the baseline numbers for a future optimising backend (D3D slots, GLSL statements, Maxwell
instructions, GPRs, code bytes).
"""
import argparse, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))

def parse_corpus(path):
    tests, cur = [], None
    for line in open(path):
        if line.startswith("=== "):
            cur = {"name": line[4:].strip(), "src": [], "expect": [], "json": [], "fail": None}
            tests.append(cur)
        elif cur is not None:
            cur["src"].append(line)
            s = line.strip()
            if s.startswith("// expect: "): cur["expect"].append(s[len("// expect: "):])
            elif s.startswith("// json: "): cur["json"].append(s[len("// json: "):])
            elif s.startswith("// expect-fail: "): cur["fail"] = s[len("// expect-fail: "):]
    return tests

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default=os.environ.get("FNVNX_SHADERS_BUILD", os.path.join(ROOT, "render/shaders/build/host")))
    ap.add_argument("--out", default=os.path.join(ROOT, "render/shaders/build/s6b-out"))
    a = ap.parse_args()
    asm, tool, unit = (os.path.join(a.build, x) for x in ("s6b_asm", "sdp2dksh", "s6b_unit"))
    for t in (asm, tool, unit):
        if not os.path.exists(t):
            sys.exit("missing %s: run render/shaders/build.sh --tests" % t)
    os.makedirs(a.out, exist_ok=True)

    u = subprocess.run([unit], capture_output=True, text=True)
    print(u.stdout.strip())
    unit_ok = u.returncode == 0

    tests = parse_corpus(os.path.join(HERE, "shaders.s6b"))
    packs = {"vs": [], "ps": []}
    for t in tests:
        src = os.path.join(a.out, t["name"] + ".asm")
        open(src, "w").write("".join(t["src"]))
        t["bin"] = os.path.join(a.out, t["name"] + ".bin")
        r = subprocess.run([asm, src, t["bin"]], capture_output=True, text=True)
        t["asm_ok"] = r.returncode == 0
        t["asm_err"] = r.stderr.strip()
        if t["asm_ok"]:
            packs[t["name"][:2]].append("%s.%so=%s" % (t["name"], t["name"][:2][0], t["bin"]))

    rows = {}
    for i, (k, entries) in enumerate(sorted(packs.items())):
        sdp = os.path.join(a.out, "shaderpackage%03d.sdp" % (i + 1))
        subprocess.run([tool, "--pack", sdp] + entries, check=True)
        od = os.path.join(a.out, k)
        os.makedirs(od, exist_ok=True)
        r = subprocess.run([tool, "-o", od, "--glsl", sdp], capture_output=True, text=True)
        sys.stderr.write(r.stderr)
        for line in open(os.path.join(od, "index.tsv")).read().splitlines()[1:]:
            f = line.split("\t")
            rows[f[2].rsplit(".", 1)[0]] = dict(hash=f[0], model=f[3], status=f[4], d3d=f[5], glsl=f[6],
                                                dk=f[7], gprs=f[8], code=f[9], err=f[10], dir=od)

    print()
    print("| shader | model | asm | GLSL | DKSH | checks | d3d | glsl stmts | dk instr | gprs | code B |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    npass = 0
    for t in tests:
        r = rows.get(t["name"])
        notes = []
        if not t["asm_ok"]:
            ok = False; cells = ("-", "FAIL", "-", "-"); notes.append(t["asm_err"].splitlines()[0] if t["asm_err"] else "")
            r = {}
        else:
            base = os.path.join(r["dir"], r["hash"])
            glsl = open(base + ".glsl").read() if os.path.exists(base + ".glsl") else ""
            js = open(base + ".json").read() if os.path.exists(base + ".json") else ""
            have_dksh = os.path.exists(base + ".dksh")
            gl_ok = bool(glsl)
            dk_ok = r["status"] == "ok" and have_dksh
            missing = [e for e in t["expect"] if e not in glsl] + [j for j in t["json"] if j not in js]
            if t["fail"]:
                ok = r["status"] == "fail" and t["fail"] in r["err"]
                chk = "expected fail" if ok else "unexpected"
            else:
                ok = dk_ok and not missing
                chk = "%d/%d" % (len(t["expect"]) + len(t["json"]) - len(missing), len(t["expect"]) + len(t["json"]))
            if missing: notes.append("missing: " + "; ".join(missing))
            if r["status"] != "ok" and not t["fail"]: notes.append(r["err"])
            cells = ("ok", "ok" if gl_ok else "FAIL", "ok" if dk_ok else ("-" if t["fail"] and not gl_ok else "FAIL"), chk)
        npass += ok
        print("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            t["name"], r.get("model", "-"), cells[0], cells[1], cells[2], cells[3],
            r.get("d3d", "-"), r.get("glsl", "-"), r.get("dk", "-"), r.get("gprs", "-"), r.get("code", "-")))
        for n in notes:
            if n: print("|  | ^ %s |||||||||" % n.replace("|", "/")[:300])
    print()
    print("shaders_tests=%d shaders_pass=%d unit=%s" % (len(tests), npass, "pass" if unit_ok else "FAIL"))
    return 0 if npass == len(tests) and unit_ok else 1

if __name__ == "__main__":
    sys.exit(main())
