#!/usr/bin/env python3
"""Fetch every package=version of verify/env.json as a .deb into a directory and write its manifest (stdlib + curl).
    python3 -I mirror_debs.py --env verify/env.json --out DIR [--snapshot YYYYMMDDTHHMMSSZ]
DIR gets <name>_<version>_<arch>.deb for every entry of env.json "packages" and DIR/debs.json:
    {"format":"fnvnx-debs/1","packages":{name:{"version","file","sha256","origin","url"}}}
Origin of each file (the upstream that the pin documents; the mirror is what setup installs from):
  snapshot  the version is in the Ubuntu snapshot at --snapshot; its Packages index (SHA256 field) must equal the file's sha256
  launchpad Ubuntu pool file of a version that a later update superseded (launchpad.net +files); not in the dated index
  ppa       "+noble1" versions: the deadsnakes PPA pool (ppa.launchpadcontent.net)
A file whose sha256 differs from its index is an error. Nothing is skipped: any package that cannot be fetched is an error."""
import hashlib
import json
import lzma
import os
import subprocess
import sys

SUITES = (("noble", "main"), ("noble", "universe"), ("noble-updates", "main"), ("noble-updates", "universe"),
          ("noble-security", "main"), ("noble-security", "universe"))
SNAP = "https://snapshot.ubuntu.com/ubuntu/%s/dists/%s/%s/binary-amd64/Packages.xz"

def curl(url, dest, tries=5):
    for _ in range(tries):
        if subprocess.run(["curl", "-sSfL", "-m", "600", "-o", dest, url], capture_output=True).returncode == 0:
            return True
    return False

def index(snapshot, work):
    found = {}
    for s, c in SUITES:
        f = os.path.join(work, "%s-%s.xz" % (s, c))
        if not curl(SNAP % (snapshot, s, c), f):
            sys.exit("mirror_debs: cannot fetch the index %s/%s at %s" % (s, c, snapshot))
        for blk in lzma.open(f, "rt", encoding="utf-8").read().split("\n\n"):
            d = dict(l.split(": ", 1) for l in blk.split("\n") if l[:1] not in " \t" and ": " in l)
            if d.get("Architecture") in ("amd64", "all") and "Package" in d:
                found[(d["Package"], d["Version"])] = (d["Filename"], d["SHA256"])
    return found

def main(a):
    o = dict(zip(a[::2], a[1::2]))
    env = json.load(open(o["--env"]))
    snap = o.get("--snapshot") or env["apt_snapshot"]
    out = o["--out"]
    os.makedirs(out, exist_ok=True)
    work = os.path.join(out, ".idx")
    os.makedirs(work, exist_ok=True)
    idx = index(snap, work)
    man, bad = {}, []
    for name, ver in sorted(env["packages"].items()):
        want = None
        if (name, ver) in idx:
            fn, want = idx[(name, ver)]
            origin, url = "snapshot", "https://snapshot.ubuntu.com/ubuntu/%s/%s" % (snap, fn)
            base = os.path.basename(fn)
        else:
            base = None
        if base is None:
            v = ver.split(":", 1)[-1]
            src = name
            cands = ["%s_%s_%s.deb" % (name, v, arch) for arch in ("amd64", "all")]
            if "+noble1" in ver:   # deadsnakes: libpython3.N-dev is built from source package python3.N
                srcname = name.replace("lib", "", 1).replace("-dev", "")
                url_of = lambda b: "https://ppa.launchpadcontent.net/deadsnakes/ppa/ubuntu/pool/main/p/%s/%s" % (srcname, b)
                origin = "ppa"
            else:
                url_of = lambda b: "https://launchpad.net/ubuntu/+archive/primary/+files/" + b
                origin = "launchpad"
            for b in cands:
                if curl(url_of(b), os.path.join(out, b + ".part")):
                    base, url = b, url_of(b)
                    os.replace(os.path.join(out, b + ".part"), os.path.join(out, b))
                    break
            else:
                bad.append("%s=%s: not fetchable (%s)" % (name, ver, cands))
                continue
        else:
            if not os.path.exists(os.path.join(out, base)) and not curl(url, os.path.join(out, base)):
                bad.append("%s=%s: cannot fetch %s" % (name, ver, url))
                continue
        h = hashlib.sha256(open(os.path.join(out, base), "rb").read()).hexdigest()
        if want and h != want:
            bad.append("%s=%s: sha256 %s differs from the index %s" % (name, ver, h, want))
            continue
        man[name] = {"version": ver, "file": base, "sha256": h, "origin": origin, "url": url}
    if bad:
        sys.exit("mirror_debs: %d of %d failed:\n  " % (len(bad), len(env["packages"])) + "\n  ".join(bad))
    json.dump({"format": "fnvnx-debs/1", "snapshot": snap, "packages": man}, open(os.path.join(out, "debs.json"), "w"), indent=1, sort_keys=True)
    print("mirror_debs: %d packages, %d snapshot, %d launchpad, %d ppa" % (
        len(man), *(sum(1 for m in man.values() if m["origin"] == k) for k in ("snapshot", "launchpad", "ppa"))))

if __name__ == "__main__":
    main(sys.argv[1:])
