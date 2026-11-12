#!/usr/bin/env python3
"""The Ubuntu packages that provide the system headers a compile reads (stdlib + curl + gcc + dpkg-deb; run by whoever pins them).
    python3 -I header_owners.py --snapshot TS --cwd DIR --out DIR2 -- GCC-ARGS...
Preprocesses `gcc -M -MG GCC-ARGS` in --cwd. -MG lists a header that does not exist under the name it was written with: that name is
looked up in the Contents index of the Ubuntu archive (usr/include/NAME), the owning package is fetched at its snapshot version (the
snapshot Packages index gives its file and sha256; the downloaded file must match), unpacked under DIR2 and DIR2/usr/include is added
as -isystem; the preprocess is repeated until no header is missing (a fixpoint: a header found only through another new header is
reached on the next round). Several packages owning one path: the first by name. The Contents index only names candidates: the
preprocess with the unpacked package then verifies that the header is there. Output (stdout, JSON):
    {"packages": {name: {"version","file","sha256","url","files": N}}, "missing": [...]}
`files` counts the headers read from that package; headers read from packages installed on this machine are listed under "local"."""
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import resolve_profile as rp  # noqa: E402

CONTENTS = "http://archive.ubuntu.com/ubuntu/dists/noble/Contents-amd64.gz"


def deps(cwd, args, extra):
    cmd = ["gcc", "-M", "-MG"] + [x for d in extra for x in ("-isystem", d)] + args
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit("header_owners: gcc -M -MG failed:\n" + p.stderr[-800:])
    toks = p.stdout.replace("\\\n", " ").split()
    return [t for t in toks[1:] if not t.endswith(":")]


def main(a):
    sep = a.index("--")
    o = dict(zip(a[:sep:2], a[1:sep:2]))
    args, cwd, out = a[sep + 1:], os.path.abspath(o["--cwd"]), os.path.abspath(o["--out"])
    os.makedirs(out, exist_ok=True)
    cf = os.path.join(out, "Contents-amd64.gz")
    if not os.path.exists(cf) and not rp.curl(CONTENTS, cf):
        sys.exit("header_owners: cannot fetch " + CONTENTS)
    idx = os.path.join(out, ".idx")
    os.makedirs(idx, exist_ok=True)
    best = rp.load(o["--snapshot"], idx)
    path_pkgs = {}
    extra, got, missing = [], {}, []
    for _ in range(12):
        reads = deps(cwd, args, extra)
        miss = sorted({r for r in reads if not os.path.exists(r if os.path.isabs(r) else os.path.join(cwd, r))
                       and not any(os.path.exists(os.path.join(cwd, i[2:], r)) for i in args if i.startswith("-I"))})
        if not miss:
            break
        want = {"usr/include/" + m: m for m in miss}
        if not path_pkgs:
            for ln in gzip.open(cf, "rt", encoding="utf-8", errors="replace"):
                pth, _, pk = ln.rstrip("\n").rpartition(" ")
                pth = pth.rstrip()
                if pth in want:
                    path_pkgs.setdefault(pth, []).append(pk)
        new = False
        for pth, m in sorted(want.items()):
            cand = sorted(x.split("/")[-1] for pk in path_pkgs.get(pth, []) for x in pk.split(","))
            if not cand:
                missing.append(m)
                continue
            name = cand[0]
            if name in got:
                continue
            r = best[name]
            f = os.path.join(out, os.path.basename(r["Filename"]))
            url = rp.POOL % (o["--snapshot"], r["Filename"])
            if not os.path.exists(f) and not rp.curl(url, f):
                sys.exit("header_owners: cannot fetch " + url)
            h = hashlib.sha256(open(f, "rb").read()).hexdigest()
            if h != r["SHA256"]:
                sys.exit("header_owners: %s sha256 %s differs from the index %s" % (f, h, r["SHA256"]))
            root = os.path.join(out, "x-" + name)
            os.makedirs(root, exist_ok=True)
            subprocess.run(["dpkg-deb", "-x", f, root], check=True)
            extra.append(os.path.join(root, "usr/include"))
            got[name] = {"version": r["Version"], "file": os.path.basename(f), "sha256": h, "url": url, "files": 0, "root": root}
            new = True
        if not new:
            break
    reads = deps(cwd, args, extra)
    local = {}
    for r in reads:
        pa = os.path.realpath(r if os.path.isabs(r) else os.path.join(cwd, r))
        hit = [n for n, g in got.items() if pa.startswith(g["root"] + "/")]
        if hit:
            got[hit[0]]["files"] += 1
        elif pa.startswith("/usr/"):
            local[pa] = 1
    for g in got.values():
        del g["root"]
    print(json.dumps({"packages": got, "local_reads": len(local), "missing": sorted(set(missing))}, indent=1, sort_keys=True))


if __name__ == "__main__":
    main(sys.argv[1:])
