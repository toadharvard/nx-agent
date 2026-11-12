#!/usr/bin/env python3
"""Resolve a build-host profile to an exact package set from the Ubuntu snapshot indexes (stdlib + curl; no apt, no installed packages).
    python3 -I resolve_profile.py --snapshot 20261110T000000Z --direct FILE --out DIR
FILE: one package name per line (the packages the build needs: DIRECT), `#` comments. DIR receives the debs and PROFILE.json:
    {"snapshot", "direct": [...], "packages": {name: {"version","file","sha256","origin","url"}}}.
The set is a rule, not a choice:
  SEEDS    DIRECT, every `Essential: yes` package and every `Priority: required` package of the indexes (the minimal Ubuntu base).
  VERSION  the highest version of a name over noble, noble-updates, noble-security (Debian version order), amd64 or all.
  CLOSURE  Pre-Depends and Depends of every member; of an alternative `a | b` the first alternative whose package is already a member,
           else the first alternative that exists; a virtual name takes the member that Provides it, else the provider that sorts
           first by (Priority rank, name). Every version constraint of every chosen clause is checked against the chosen version: a
           violation is an error (nothing is fixed up).
Every file's sha256 is the index's SHA256 field of the signed snapshot index and is checked against the downloaded file."""
import hashlib
import json
import lzma
import os
import re
import subprocess
import sys

SUITES = (("noble", "main"), ("noble", "universe"), ("noble-updates", "main"), ("noble-updates", "universe"),
          ("noble-security", "main"), ("noble-security", "universe"))
SNAP = "https://snapshot.ubuntu.com/ubuntu/%s/dists/%s/%s/binary-amd64/Packages.xz"
POOL = "https://snapshot.ubuntu.com/ubuntu/%s/%s"
RANK = {"required": 0, "important": 1, "standard": 2, "optional": 3, "extra": 4}


def curl(url, dest, tries=5):
    for _ in range(tries):
        if subprocess.run(["curl", "-sSfL", "-m", "600", "-o", dest, url], capture_output=True).returncode == 0:
            return True
    return False


def _order(c):
    return -1 if c == "~" else (ord(c) if c.isalpha() else ord(c) + 256) if c else 0


def _cmp_part(a, b):
    while a or b:
        da, db = re.match(r"\D*", a).group(), re.match(r"\D*", b).group()
        a, b = a[len(da):], b[len(db):]
        for i in range(max(len(da), len(db))):
            x = _order(da[i]) if i < len(da) else 0
            y = _order(db[i]) if i < len(db) else 0
            if x != y:
                return -1 if x < y else 1
        na, nb = re.match(r"\d*", a).group(), re.match(r"\d*", b).group()
        a, b = a[len(na):], b[len(nb):]
        x, y = int(na or 0), int(nb or 0)
        if x != y:
            return -1 if x < y else 1
    return 0


def vsplit(v):
    ep, _c, r = v.rpartition(":")
    up, hy, rev = r.rpartition("-")
    return (int(ep) if _c else 0), (up if hy else r), (rev if hy else "")


def vcmp(a, b):
    (ea, ua, ra), (eb, ub, rb) = vsplit(a), vsplit(b)
    if ea != eb:
        return -1 if ea < eb else 1
    return _cmp_part(ua, ub) or _cmp_part(ra, rb)


def load(snapshot, work):
    """{name: record} of the highest version over all suites; record = Package/Version/Filename/SHA256/Depends/Pre-Depends/Provides/..."""
    best = {}
    for s, c in SUITES:
        f = os.path.join(work, "%s-%s.xz" % (s, c))
        if not os.path.exists(f) and not curl(SNAP % (snapshot, s, c), f):
            sys.exit("resolve_profile: cannot fetch the index %s/%s at %s" % (s, c, snapshot))
        for blk in lzma.open(f, "rt", encoding="utf-8").read().split("\n\n"):
            d, k = {}, None
            for ln in blk.split("\n"):
                if ln[:1] in " \t":
                    if k:
                        d[k] += "\n" + ln
                elif ": " in ln:
                    k, v = ln.split(": ", 1)
                    d[k] = v
            if d.get("Architecture") in ("amd64", "all") and "Package" in d:
                o = best.get(d["Package"])
                if o is None or vcmp(d["Version"], o["Version"]) > 0:
                    best[d["Package"]] = d
    return best


def clauses(rec, fields=("Pre-Depends", "Depends")):
    out = []
    for fld in fields:
        for part in rec.get(fld, "").replace("\n", " ").split(","):
            part = part.strip()
            if part:
                alts = []
                for a in part.split("|"):
                    m = re.match(r"\s*([^\s:(]+)(?::\w+)?\s*(?:\(\s*([<>=]+)\s*([^)\s]+)\s*\))?", a)
                    alts.append((m.group(1), m.group(2), m.group(3)))
                out.append(alts)
    return out


def holds(ver, op, want):
    if not op:
        return True
    c = vcmp(ver, want)
    return {"<<": c < 0, "<": c <= 0, "<=": c <= 0, "=": c == 0, ">=": c >= 0, ">": c >= 0, ">>": c > 0}[op]


def resolve(best, direct):
    prov = {}
    for n, r in best.items():
        for p in r.get("Provides", "").replace("\n", " ").split(","):
            p = re.match(r"\s*([^\s:(]*)", p).group(1)
            if p:
                prov.setdefault(p, []).append(n)
    seeds = set(direct) | {n for n, r in best.items() if r.get("Essential") == "yes" or r.get("Priority") == "required"}
    unknown = sorted(n for n in seeds if n not in best)
    if unknown:
        sys.exit("resolve_profile: not in the snapshot: %s" % unknown)
    members, todo, errors = set(), sorted(seeds), []
    while todo:
        n = todo.pop(0)
        if n in members:
            continue
        members.add(n)
        for alts in clauses(best[n]):
            pick = None
            for a, op, v in alts:   # the first alternative that is already a member (itself or through Provides)
                if a in members or any(p in members for p in prov.get(a, [])):
                    pick = (a, op, v)
                    break
            if pick is None:
                for a, op, v in alts:
                    if a in best or a in prov:
                        pick = (a, op, v)
                        break
            if pick is None:
                errors.append("%s: no alternative of %s exists" % (n, " | ".join(a[0] for a in alts)))
                continue
            a, op, v = pick
            if a in prov and a not in best:
                have = [p for p in prov[a] if p in members]
                a2 = have[0] if have else sorted(prov[a], key=lambda p: (RANK.get(best[p].get("Priority"), 9), p))[0]
                if op:   # a versioned dependency on a virtual package is satisfied by nothing (dpkg rule)
                    errors.append("%s: versioned dependency on the virtual package %s" % (n, a))
                todo.append(a2)
            else:
                if not holds(best[a]["Version"], op, v):
                    errors.append("%s: needs %s (%s %s), the snapshot has %s" % (n, a, op, v, best[a]["Version"]))
                todo.append(a)
    if errors:
        sys.exit("resolve_profile: %d unsatisfied:\n  " % len(errors) + "\n  ".join(errors))
    return sorted(members)


def main(a):
    o = dict(zip(a[::2], a[1::2]))
    snap, out = o["--snapshot"], o["--out"]
    direct = [ln.split("#")[0].strip() for ln in open(o["--direct"])]
    direct = [d for d in direct if d]
    os.makedirs(out, exist_ok=True)
    work = os.path.join(out, ".idx")
    os.makedirs(work, exist_ok=True)
    best = load(snap, work)
    names = resolve(best, direct)
    man = {}
    for n in names:
        r = best[n]
        base = os.path.basename(r["Filename"])
        dest = os.path.join(out, base)
        url = POOL % (snap, r["Filename"])
        if not os.path.exists(dest) and not curl(url, dest):
            sys.exit("resolve_profile: cannot fetch %s" % url)
        h = hashlib.sha256(open(dest, "rb").read()).hexdigest()
        if h != r["SHA256"]:
            sys.exit("resolve_profile: %s sha256 %s differs from the index %s" % (base, h, r["SHA256"]))
        man[n] = {"version": r["Version"], "file": base, "sha256": h, "origin": "snapshot", "url": url}
    json.dump({"snapshot": snap, "direct": sorted(direct), "packages": man}, open(os.path.join(out, "PROFILE.json"), "w"), indent=1, sort_keys=True)
    print("resolve_profile: %d direct, %d packages (all from the snapshot, sha256 = index)" % (len(direct), len(man)))


if __name__ == "__main__":
    main(sys.argv[1:])
