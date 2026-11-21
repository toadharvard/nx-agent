#!/usr/bin/env python3
"""Candidate assembly check (owner order "defects impossible by construction", rule 8; L1 rulings of 2026-11-21): a candidate takes each file from its owner, and
no source head's edit is lost.
    python3 -I verify/candcheck.py --sources FILE [--candidate REF] [--json OUT]        cwd = the git checkout root
FILE (the candidate's source list, JSON): {"trunk": REF, "sources": [{"head": REV or "self", "patches": [REV..] (optional), "layer": NAME, "trees": [PATH PREFIX..]}],
"superseded": [{"file": PATH, "head": REV, "owner": REV, "reason": TEXT}], "cut": [{"root": PATH, "heads": [REV..], "reason": TEXT}]}.
`self` is the candidate's own line (L1's fixes are committed on it). Nothing else is listed: the files and roots come from git.
Rules. A head changed F when its version differs from that at merge-base(head, trunk). The files are every file a non-self head changed, minus the files under a `cut` root.
1. Trees: the owner of F is the source row with the longest tree prefix of F (a nested prefix is a more specific owner); the same prefix in two rows is RED `overlap`.
   A file in no row's trees is RED `unowned`.
2. The candidate's version of F is its owner's version (the row's head; `self`: the candidate itself), else RED `owner-red`.
3. For every non-owner head G that changed F, merge(owner O's version, G's version) on the base `git merge-base O G` must equal O's version: then G's edit is contained in O.
   Otherwise RED `not-in-owner` (F, G, O), conflict or not. The remedy belongs to O: take G's change into O's head, or a `superseded` record {file, head G, owner O, reason}
   (O's version deliberately replaces G's). A record nothing needs, or without a reason, is RED `record`.
4. Roots: every root with a recipe in a head's verify/admit.json must be admitted in the candidate or in `cut` with that head and a non-empty reason, else RED `root`;
   a `cut` record nothing needs is RED `record`.
Prints one global line with the counts per class. Exit 0 iff nothing is red."""
import json
import os
import subprocess
import sys
import tempfile

ADMIT = "ports/fnv-nx/verify/admit.json"


def git(*a, inp=None):
    r = subprocess.run(["git"] + list(a), capture_output=True, input=inp)
    if r.returncode:
        raise SystemExit("git %s: %s" % (" ".join(a), r.stderr.decode()[-300:]))
    return r.stdout


def rev(x):
    return git("rev-parse", "--verify", x + "^{commit}").decode().strip()


def source_head(s):
    """The commit of a source row: its `head`, with each of `patches` (commits, in order) applied on top by a 3-way merge against the patch's parent
    (a source that is a head plus patch commits, like L2's a36b3c8f2 with its overlay). A patch that conflicts is an error of the source list."""
    h = rev(s["head"])
    env = dict(os.environ, GIT_AUTHOR_NAME="candcheck", GIT_AUTHOR_EMAIL="candcheck@invalid", GIT_COMMITTER_NAME="candcheck", GIT_COMMITTER_EMAIL="candcheck@invalid", GIT_AUTHOR_DATE="2000-01-01T00:00:00Z", GIT_COMMITTER_DATE="2000-01-01T00:00:00Z")
    for pc in s.get("patches", []):
        pc = rev(pc)
        t = git("merge-tree", "--write-tree", "--merge-base=" + pc + "~1", h, pc).decode().split("\n")[0]
        h = subprocess.run(["git", "commit-tree", t, "-p", h, "-m", "candcheck: patch " + pc[:9]], capture_output=True, env=env, check=True).stdout.decode().strip()
    return h


def blobs(c):
    """{path: blob sha} of every file of commit C."""
    out = {}
    for ln in git("ls-tree", "-r", "-z", c).split(b"\0"):
        if ln:
            meta, p = ln.split(b"\t", 1)
            out[p.decode()] = meta.split()[2].decode()
    return out


def admitted(c):
    try:
        d = json.loads(git("show", c + ":" + ADMIT))
    except SystemExit:
        return set()
    return {r["path"] for r in d["roots"] if r.get("recipe")}


def merge3(cur, base, other):
    """git merge-file of blobs CUR, BASE (None: empty), OTHER -> merged bytes, or None on a conflict."""
    with tempfile.TemporaryDirectory() as td:
        for n, b in (("a", cur), ("b", base), ("c", other)):
            open(os.path.join(td, n), "wb").write(git("cat-file", "blob", b) if b else b"")
        r = subprocess.run(["git", "merge-file", "-p", td + "/a", td + "/b", td + "/c"], capture_output=True)
        return r.stdout if r.returncode == 0 else None


def at(c, p):
    """The blob of file P at commit C, or None."""
    r = subprocess.run(["git", "rev-parse", "--verify", "-q", c + ":" + p], capture_output=True)
    return r.stdout.decode().strip() if r.returncode == 0 else None


def expected(p, vers, mbs):
    """VERS [(head name, commit, blob or None)] of the heads that changed P, in source order; MBS(commit, [commits]) = git merge-base -> (blob or None, ok).
    Folding G_i into the result of G_1..G_{i-1}: the base is git merge-base G_i G_1..G_{i-1} (per step, per file), its version of P; ok False on a conflict."""
    cur = vers[0][2]
    for i in range(1, len(vers)):
        _, g, v = vers[i]
        if v == cur:
            continue
        if cur is None or v is None:
            return None, False
        m = merge3(cur, at(mbs(g, [x[1] for x in vers[:i]]), p), v)
        if m is None:
            return None, False
        cur = git("hash-object", "-w", "--stdin", inp=m).decode().strip()
    return cur, True


def check(doc, cand):
    """-> (stats, [(kind, file or root, heads, text)])"""
    trunk, cand = rev(doc["trunk"]), rev(cand)
    rows = doc["sources"]
    heads = [cand if r["head"] == "self" else source_head(r) for r in rows]
    rowheads = [cand if r["head"] == "self" else rev(r["head"]) for r in rows]
    names = [("self" if r["head"] == "self" else rev(r["head"])[:9]) for r in rows]
    layer = {n: r.get("layer", "") for n, r in zip(names, rows)}
    red = []
    seen = {}
    for n, r in zip(names, rows):
        for t in r["trees"]:
            if t in seen and seen[t] != n:
                red.append(("overlap", t, "%s,%s" % (seen[t], n), "the same tree prefix in two rows"))
            seen[t] = n
    owner_of = lambda p: max(((len(t), n) for t, n in seen.items() if p.startswith(t)), default=(0, None))[1]
    cb = blobs(cand)
    hb = [cb if r["head"] == "self" else blobs(h) for r, h in zip(rows, heads)]
    tb = [None if r["head"] == "self" else blobs(git("merge-base", h, trunk).decode().strip()) for r, h in zip(rows, heads)]
    files = set()
    for b, t in zip(hb, tb):
        if t is not None:
            files |= {p for p in set(b) | set(t) if b.get(p) != t.get(p)}
    cutroots = [m["root"].rstrip("/") + "/" for m in doc.get("cut", [])]
    files = {p for p in files if not any(p.startswith(r) for r in cutroots)}   # a cut root takes its files with it
    sup = {(m["file"], m["head"][:9], m["owner"][:9] if m["owner"] != "self" else "self"): m for m in doc.get("superseded", [])}
    used = set()
    idx = {n: i for i, n in enumerate(names)}
    st = {"files": len(files), "owner_equal": 0, "owner_red": 0, "unowned": 0, "not_in_owner": 0, "superseded": 0}
    pairs = {}
    for p in sorted(files):
        o = owner_of(p)
        if o is None:
            st["unowned"] += 1
            red.append(("unowned", p, ",".join(n for n, b, t in zip(names, hb, tb) if t is not None and b.get(p) != t.get(p)), "in no source row's trees"))
            continue
        ob = hb[idx[o]].get(p)
        if cb.get(p) == ob:
            st["owner_equal"] += 1
        else:
            st["owner_red"] += 1
            red.append(("owner-red", p, o, "candidate %s, owner version %s" % ((cb.get(p) or "-")[:9], (ob or "-")[:9])))
        for n, b, t, g in zip(names, hb, tb, heads):
            if t is None or n == o or b.get(p) == t.get(p):
                continue
            gb = b.get(p)
            contained = gb == ob
            if not contained and ob is not None and gb is not None:
                mb = git("merge-base", heads[idx[o]], g).decode().strip()
                m = merge3(ob, at(mb, p), gb)
                contained = m is not None and git("hash-object", "--stdin", inp=m).decode().strip() == ob
            elif not contained and ob is None and gb == at(git("merge-base", heads[idx[o]], g).decode().strip(), p):
                contained = True
            if contained:
                continue
            k = (p, n, o)
            if k in sup and sup[k].get("reason"):
                used.add(k)
                st["superseded"] += 1
                continue
            st["not_in_owner"] += 1
            pairs.setdefault((o, n), []).append(p)
            red.append(("not-in-owner", p, "%s<-%s" % (o, n), "owner %s's version does not contain %s's edit" % (o, n)))
    for k in sorted(set(sup) - used):
        red.append(("record", k[0], "%s<-%s" % (k[2], k[1]), "superseded record nothing needs, or without a reason"))
    ca, nroots, usedc = admitted(cand), 0, set()
    cut = {m["root"]: m for m in doc.get("cut", [])}
    for rh, n in zip(rowheads, names):
        if n == "self":
            continue
        for r in sorted(admitted(rh)):
            nroots += 1
            if r in ca:
                continue
            c = cut.get(r)
            if c and rh in {rev(h) for h in c["heads"]} and c.get("reason"):
                usedc.add(r)
            else:
                red.append(("root", r, n, "admitted by the head, not by the candidate, not cut with a reason"))
    for r in sorted(set(cut) - usedc):
        red.append(("record", r, "", "cut record nothing needs (the candidate admits the root, no listed head does, or no reason)"))
    st.update({"candidate": cand, "trunk": trunk, "sources": len(rows), "roots_checked": nroots, "red": len(red), "pairs": {"%s<-%s" % k: len(v) for k, v in sorted(pairs.items())}})
    st["layers"] = layer
    return st, red


def main(argv):
    a = dict(zip(argv[::2], argv[1::2]))
    st, red = check(json.load(open(a["--sources"])), a.get("--candidate", "HEAD"))
    for k, p, h, t in red:
        print("RED %s %s (%s): %s" % (k, p, h, t))
    print("candcheck: %s" % json.dumps(st, sort_keys=True))
    if "--json" in a:
        json.dump({"stats": st, "red": red}, open(a["--json"], "w"), indent=1)
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
