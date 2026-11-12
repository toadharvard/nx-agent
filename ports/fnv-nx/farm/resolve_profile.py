#!/usr/bin/env python3
"""Resolve a build-host profile to an exact package set from the Ubuntu snapshot indexes (stdlib + curl; no apt, no installed packages).
    python3 -I resolve_profile.py --snapshot 20261110T000000Z --direct FILE --out DIR [--foreign i386]
FILE: one package name per line (the packages the build needs: DIRECT; `name:i386` for a package of the foreign architecture), `#`
comments. DIR receives the debs and PROFILE.json (a foreign package is keyed `name:i386`, a native or `all` package by its name):
    {"snapshot", "direct": [...], "packages": {name: {"version","file","sha256","origin","url"}}}.
The set is a rule, not a choice:
  SEEDS    DIRECT, every `Essential: yes` package and every `Priority: required` package of the indexes (the minimal Ubuntu base).
  VERSION  the highest version of a name over noble, noble-updates, noble-security (Debian version order), amd64 or all.
  CLOSURE  Pre-Depends and Depends of every member; of an alternative `a | b` the first alternative whose package is already a member,
           else the first alternative that exists; a virtual name takes the member that Provides it, else the provider that sorts
           first by (Priority rank, name). Every version constraint of every chosen clause is checked against the chosen version: a
           violation is an error (nothing is fixed up).
MULTIARCH (--foreign A): the index binary-A of the same suites is read too. A foreign member's clause is satisfied by, in this order, a
           member already chosen, the `all` package of the name, the native package when the name is Multi-Arch: foreign (dpkg's rule:
           a foreign package satisfies a dependency of any architecture) or the clause says `name:any`, else the package of A; a native
           member's clause `name:A` takes the package of A. Provides are looked up in the member's own architecture.
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


def load(snapshot, work, arch="amd64"):
    """{name: record} of the highest version over all suites; record = Package/Version/Filename/SHA256/Depends/Pre-Depends/Provides/...
    arch amd64: the amd64 and `all` packages; another arch: that architecture's own packages only."""
    best = {}
    for s, c in SUITES:
        f = os.path.join(work, "%s-%s%s.xz" % (s, c, "" if arch == "amd64" else "-" + arch))
        if not os.path.exists(f) and not curl((SNAP % (snapshot, s, c)).replace("binary-amd64", "binary-" + arch), f):
            sys.exit("resolve_profile: cannot fetch the index %s/%s (%s) at %s" % (s, c, arch, snapshot))
        for blk in lzma.open(f, "rt", encoding="utf-8").read().split("\n\n"):
            d, k = {}, None
            for ln in blk.split("\n"):
                if ln[:1] in " \t":
                    if k:
                        d[k] += "\n" + ln
                elif ": " in ln:
                    k, v = ln.split(": ", 1)
                    d[k] = v
            if d.get("Architecture") in (("amd64", "all") if arch == "amd64" else (arch,)) and "Package" in d:
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
                    m = re.match(r"\s*([^\s:(]+)(?::(\w+))?\s*(?:\(\s*([<>=]+)\s*([^)\s]+)\s*\))?", a)
                    alts.append((m.group(1), m.group(2), m.group(3), m.group(4)))
                out.append(alts)
    return out


def holds(ver, op, want):
    if not op:
        return True
    c = vcmp(ver, want)
    return {"<<": c < 0, "<": c <= 0, "<=": c <= 0, "=": c == 0, ">=": c >= 0, ">": c >= 0, ">>": c > 0}[op]


def provides(best):
    prov = {}
    for n, r in best.items():
        for p in r.get("Provides", "").replace("\n", " ").split(","):
            p = re.match(r"\s*([^\s:(]*)", p).group(1)
            if p:
                prov.setdefault(p, []).append(n)
    return prov


def resolve(best, direct, foreign=None, best_f=None):
    """Member keys: `name` (native or all), `name:ARCH` (foreign). See the module doc for the clause rule."""
    best_f = best_f or {}
    prov, prov_f = provides(best), provides(best_f)
    def rec(k):
        n, _s, a = k.partition(":")
        return best_f[n] if a else best[n]
    def key(n, a):   # a: the architecture the clause asks for (None: that of the member)
        return "%s:%s" % (n, foreign) if a == "f" else n
    seeds = {d for d in direct} | {n for n, r in best.items() if r.get("Essential") == "yes" or r.get("Priority") == "required"}
    unknown = sorted(k for k in seeds if k not in (best if ":" not in k else {x + ":" + foreign for x in best_f}))
    if unknown:
        sys.exit("resolve_profile: not in the snapshot: %s" % unknown)
    members, todo, errors = set(), sorted(seeds), []
    while todo:
        k = todo.pop(0)
        if k in members:
            continue
        members.add(k)
        fm = ":" in k   # a foreign member
        for alts in clauses(rec(k)):
            def target(n, q):   # the member key a clause alternative asks for, "~n" for a virtual name, None: no such package
                if q == "any":
                    return n if n in best else (n + ":" + foreign if n in best_f else ("~" + n if n in prov or n in prov_f else None))
                if fm and n in best_f and q in (None, foreign):   # the foreign package, or the native one when it is Multi-Arch: foreign
                    return n if (best_f[n].get("Multi-Arch") == "foreign" and n in best) else n + ":" + foreign
                if q == foreign and n in best_f:
                    return n + ":" + foreign
                if n in best:   # native or `all`
                    return n
                return "~" + n if (n in prov_f if fm else False) or n in prov else None
            pick = None
            for a, q, op, v in alts:   # the first alternative that is already a member (itself or through Provides)
                t = target(a, q)
                if t is None:
                    continue
                if t in members or any(p in members for p in ([x + ":" + foreign for x in prov_f.get(a, [])] if fm else []) + prov.get(a, [])):
                    pick = (a, q, op, v, t)
                    break
            if pick is None:
                for a, q, op, v in alts:
                    t = target(a, q)
                    if t is not None:
                        pick = (a, q, op, v, t)
                        break
            if pick is None:
                errors.append("%s: no alternative of %s exists" % (k, " | ".join(a[0] for a in alts)))
                continue
            a, q, op, v, t = pick
            if t.startswith("~"):   # a virtual name
                cands = [x + ":" + foreign for x in prov_f.get(a, [])] if fm else []
                cands = cands + list(prov.get(a, [])) if not (fm and cands) else cands
                have = [p for p in cands if p in members]
                def rank(p):
                    return (RANK.get(rec(p).get("Priority"), 9), p)
                a2 = have[0] if have else sorted(cands, key=rank)[0]
                if op:   # a versioned dependency on a virtual package is satisfied by nothing (dpkg rule)
                    errors.append("%s: versioned dependency on the virtual package %s" % (k, a))
                todo.append(a2)
            else:
                if not holds(rec(t)["Version"], op, v):
                    errors.append("%s: needs %s (%s %s), the snapshot has %s" % (k, a, op, v, rec(t)["Version"]))
                todo.append(t)
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
    fa = o.get("--foreign")
    best_f = load(snap, work, fa) if fa else {}
    names = resolve(best, direct, fa, best_f)
    man = {}
    for n in names:
        r = best_f[n.partition(":")[0]] if ":" in n else best[n]
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
