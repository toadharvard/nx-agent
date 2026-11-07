#!/usr/bin/env python3
"""tests/s6b runner: assemble the corpus, pack synthetic .sdp packages, run sdp2dksh, check.

  tests/s6b/run.py [--build DIR] [--out DIR] [--v6n-bin DIR | --no-exec]

DIR defaults follow render/shaders/build.sh (FNVNX_SHADERS_BUILD). Prints a Markdown matrix:
per shader: assemble, translate (front end + GLSL), uam DKSH (+ header validation), expectations,
and the baseline numbers for a future optimising backend (D3D slots, GLSL statements, Maxwell
instructions, GPRs, code bytes).

Execution tests (tests/s6b/exec): VS + PS scenes whose shaders are written as "@name" (exec/<name>.vsa
/ .psa); the bytecode runs on V6n's oracle (Gallium Nine, v6n_nine) and the translated GLSL on
V6n's GL executor (v6n_gl), compared by render/verify/pixdiff.py. Needs V6n's binaries
(default build/v6n/bin); skipped, and reported as such, when they are absent.
"""
import argparse, glob, os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))

def parse_corpus(path):
    tests, cur = [], None
    for line in open(path):
        if line.startswith("=== "):
            cur = {"name": line[4:].strip(), "src": [], "expect": [], "json": [], "fail": None, "absent": []}
            tests.append(cur)
        elif cur is not None:
            cur["src"].append(line)
            s = line.strip()
            if s.startswith("// expect: "): cur["expect"].append(s[len("// expect: "):])
            elif s.startswith("// json: "): cur["json"].append(s[len("// json: "):])
            elif s.startswith("// expect-fail: "): cur["fail"] = s[len("// expect-fail: "):]
            elif s.startswith("// expect-not: "): cur["absent"].append(s[len("// expect-not: "):])
    return tests

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default=os.environ.get("FNVNX_SHADERS_BUILD", os.path.join(ROOT, "render/shaders/build/host")))
    ap.add_argument("--out", default=os.path.join(ROOT, "render/shaders/build/s6b-out"))
    ap.add_argument("--v6n-bin", default=os.path.join(ROOT, "build/v6n/bin"))
    ap.add_argument("--no-exec", action="store_true")
    a = ap.parse_args()
    asm, tool, unit, cent = (os.path.join(a.build, x) for x in ("s6b_asm", "sdp2dksh", "s6b_unit", "s6b_centroid"))
    for t in (asm, tool, unit, cent):
        if not os.path.exists(t):
            sys.exit("missing %s: run render/shaders/build.sh --tests" % t)
    os.makedirs(a.out, exist_ok=True)

    u = subprocess.run([unit], capture_output=True, text=True)
    print(u.stdout.strip())
    unit_ok = u.returncode == 0
    u = subprocess.run([cent], capture_output=True, text=True)       # centroid on handwritten tokens
    print(u.stdout.strip())
    unit_ok = unit_ok and u.returncode == 0

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
            missing = [e for e in t["expect"] if e not in glsl] + [j for j in t["json"] if j not in js] + \
                      ["(not) " + e for e in t["absent"] if e in glsl]
            if t["fail"]:
                ok = r["status"] == "fail" and t["fail"] in r["err"]
                chk = "expected fail" if ok else "unexpected"
            else:
                ok = dk_ok and not missing
                n = len(t["expect"]) + len(t["json"]) + len(t["absent"])
                chk = "%d/%d" % (n - len(missing), n)
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
    ex = "skipped"
    if not a.no_exec and all(os.path.exists(os.path.join(a.v6n_bin, x)) for x in ("v6n_nine", "v6n_gl")):
        ex = "pass" if run_exec(asm, tool, a.out, a.v6n_bin) else "FAIL"
    print("shaders_tests=%d shaders_pass=%d unit=%s exec=%s" % (len(tests), npass, "pass" if unit_ok else "FAIL", ex))
    return 0 if npass == len(tests) and unit_ok and ex != "FAIL" else 1

def run_exec(asm, tool, out, v6n_bin):
    src = os.path.join(HERE, "exec"); work = os.path.join(out, "exec")
    shutil.rmtree(work, ignore_errors=True); os.makedirs(work)
    paths = {}
    for f in sorted(glob.glob(os.path.join(src, "*.vsa")) + glob.glob(os.path.join(src, "*.psa"))):
        n = os.path.basename(f)[:-4]; b = os.path.join(work, n + ".bin")
        subprocess.run([asm, f, b], check=True)
        sdp = os.path.join(work, n + ".sdp"); ext = ".vso" if f.endswith(".vsa") else ".pso"
        subprocess.run([tool, "--pack", sdp, n + ext + "=" + b], check=True, capture_output=True)
        od = os.path.join(work, n + "_t"); os.makedirs(od)
        # the oracle is Gallium Nine (v6n_nine): its numerics at 0
        subprocess.run([tool, "-o", od, "--glsl", "--numerics", "nine", sdp], check=True, capture_output=True)
        g = glob.glob(os.path.join(od, "*.glsl"))
        if not g:
            print("exec: %s does not translate" % n); return False
        paths[n] = (b, g[0])
    scenes = []
    for f in sorted(glob.glob(os.path.join(src, "*.scene.in"))):
        t = open(f).read()
        for n in sorted(paths, key=len, reverse=True):
            t = t.replace("@" + n + "\n", "%s %s\n" % paths[n])
        o = os.path.join(work, os.path.basename(f)[:-3]); open(o, "w").write(t); scenes.append(o)
    r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "render/verify/pixdiff.py"), "--bin", v6n_bin,
                        "--work", os.path.join(work, "out"), "-v"] + scenes, capture_output=True, text=True)
    print("exec (Nine vs translated GLSL):")
    print(r.stdout.strip())
    return r.returncode == 0

if __name__ == "__main__":
    sys.exit(main())
