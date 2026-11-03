#!/usr/bin/env python3
"""tests/s6d runner: nine-precompile + cache test over the S6b corpus and the S6d corpus.

  tests/s6d/run.py --host DIR [--asm S6B_ASM] [--out DIR] [--variants all|common|ctor]

--host: render/nine/host/build_host.sh's <work>/out (bin/nine-precompile, bin/s6d_cache_test).
--asm:  S6b's assembler (render/shaders/build.sh --tests -> s6b_asm); default $S6B_ASM.
Steps: assemble tests/s6b/shaders.s6b + tests/s6d/shaders.s6d, pack vs/ps into two .sdp packages
(S6b format), run nine-precompile (cache + manifest), run s6d_cache_test (fresh vs miss vs hit,
byte-identical), print a Markdown matrix and the totals. Exit 1 on any cache-test failure.
"""
import argparse, collections, os, struct, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))


def parse_corpus(path):
    tests, cur = [], None
    for line in open(path):
        if line.startswith("=== "):
            cur = {"name": line[4:].strip(), "src": []}
            tests.append(cur)
        elif cur is not None:
            cur["src"].append(line)
    return tests


def write_sdp(path, entries):
    body = b"".join(name.encode().ljust(256, b"\0") + struct.pack("<I", len(d)) + d for name, d in entries)
    open(path, "wb").write(struct.pack("<III", 100, len(entries), len(body)) + body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--asm", default=os.environ.get("S6B_ASM", os.path.join(ROOT, "render/shaders/build/host/s6b_asm")))
    ap.add_argument("--out", default=os.path.join(ROOT, "render/nine/build/s6d-out"))
    ap.add_argument("--variants", default="all")
    a = ap.parse_args()
    tool = os.path.join(a.host, "bin", "nine-precompile")
    test = os.path.join(a.host, "bin", "s6d_cache_test")
    for t in (a.asm, tool, test):
        if not os.path.exists(t):
            sys.exit("missing %s" % t)
    os.makedirs(a.out, exist_ok=True)

    tests = []
    for corpus, origin in ((os.path.join(ROOT, "tests/s6b/shaders.s6b"), "s6b"), (os.path.join(HERE, "shaders.s6d"), "s6d")):
        for t in parse_corpus(corpus):
            t["origin"] = origin
            tests.append(t)
    packs = {"vs": [], "ps": []}
    asm_fail = []
    for t in tests:
        src = os.path.join(a.out, t["name"] + ".asm")
        binp = os.path.join(a.out, t["name"] + ".bin")
        open(src, "w").write("".join(t["src"]))
        r = subprocess.run([a.asm, src, binp], capture_output=True, text=True)
        t["asm_ok"] = r.returncode == 0
        if not t["asm_ok"]:
            asm_fail.append((t["name"], (r.stderr.strip().splitlines() or [""])[0]))
            continue
        ext = ".vso" if t["name"].startswith("vs") else ".pso"
        packs[t["name"][:2]].append((t["name"] + ext, open(binp, "rb").read()))
    sdps = []
    for i, k in enumerate(("vs", "ps")):
        p = os.path.join(a.out, "shaderpackage%03d.sdp" % (i + 1))
        write_sdp(p, packs[k])
        sdps.append(p)

    cache = os.path.join(a.out, "nvc0_shaders.cache")
    man = os.path.join(a.out, "manifest.tsv")
    r = subprocess.run([tool, "-o", cache, "--fresh", "--manifest", man, "--variants", a.variants, "--quiet"] + sdps,
                       capture_output=True, text=True)
    pre_summary = r.stdout.strip()
    rows = [l.split("\t") for l in open(man).read().splitlines()[1:]]
    per = collections.OrderedDict()
    for f in rows:
        name = f[1].rsplit(".", 1)[0]
        d = per.setdefault(name, {"model": f[2], "ctor": None, "n": 0, "ok": 0, "keys": set(), "code": 0, "gprs": 0, "fail": []})
        d["n"] += 1
        st = f[5]
        good = st in ("stored", "cached", "dup")
        if f[3] == "ctor" or st.startswith("crash"):
            d["ctor"] = st
            d["code"], d["gprs"] = int(f[7]), int(f[8])
        if good:
            d["ok"] += 1
            d["keys"].add(f[9])
        else:
            d["fail"].append("%s:%s" % (f[3], st))

    t = subprocess.run([test, a.out] + sdps, capture_output=True, text=True)
    ident = collections.defaultdict(lambda: [0, 0])
    for l in t.stdout.splitlines()[1:]:
        f = l.split("\t")
        if len(f) >= 3 and "identical=" in l:
            ident[f[0].rsplit(".", 1)[0]][0] += 1
            ident[f[0].rsplit(".", 1)[0]][1] += l.endswith("identical=yes")
        elif len(f) >= 3 and "crash" in f[2]:
            ident[f[0].rsplit(".", 1)[0]] = None
    fails = [l for l in t.stdout.splitlines() if l.startswith("FAIL")]

    print("| shader | from | model | ctor compile | variants ok | distinct programs | ctor code B | ctor GPRs | cache hit == fresh |")
    print("|---|---|---|---|---|---|---|---|---|")
    origin = {x["name"]: x["origin"] for x in tests}
    for name, d in per.items():
        idv = ident.get(name)
        idc = "crash (stock compiler)" if idv is None else "%d/%d" % (idv[1], idv[0])
        print("| %s | %s | %s | %s | %d/%d | %d | %d | %d | %s |" % (name, origin.get(name, "?"), d["model"], d["ctor"],
              d["ok"], d["n"], len(d["keys"]), d["code"], d["gprs"], idc))
    for n, e in asm_fail:
        print("| %s | %s | - | assemble failed: %s | - | - | - | - | - |" % (n, origin[n], e))
    print()
    print(pre_summary)
    print("cache file: %s (%d bytes)" % (cache, os.path.getsize(cache)))
    print(t.stdout.strip().splitlines()[-1])
    for l in fails:
        print(l)
    sys.exit(1 if t.returncode else 0)


if __name__ == "__main__":
    main()
