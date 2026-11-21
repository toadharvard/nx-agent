#!/usr/bin/env python3
"""Candidate assembly check (owner order "defects impossible by construction", rule 8; L1 rule of 2026-11-21): a take must not lose any source head's edits.
    python3 -I verify/candcheck.py --sources FILE [--candidate REF] [--json OUT]        cwd = the git checkout root
FILE (the candidate's source list, JSON): {"trunk": REF, "sources": [{"head": REV, "patches": [REV..] (optional), "owner": NAME}], "merged": [{"file": PATH, "heads": [REV..], "result": BLOB, "reason": TEXT}],
"cut": [{"root": PATH, "heads": [REV..], "reason": TEXT}]}. Nothing else is listed: files and roots come from git.
Rule, derived per file F that any source head changed. A head changed F when its version differs from that at merge-base(head, trunk). Files under a `cut` root are out of Expected.
1. Expected(F) = the clean N-way merge of the versions of the heads that changed F, folded pairwise in source-list order with `git merge-file`; folding G_i into
   G_1..G_{i-1}, the base is `git merge-base G_i G_1..G_{i-1}` (per step, per file), its version of F; one changer: that head's version; a deletion by all changers: absent.
2. Clean: the candidate must equal Expected, else RED `merge-red` naming F and the heads whose changes are missing.
3. Conflict (a pairwise fold conflicts, or one head deletes what another changes): a `merged` record {file, heads, result, reason} is needed and the candidate must hold its
   `result` blob; a conflict with no record is RED `conflict`; a record whose merge is actually clean, or that names heads that did not change F, is RED `record`. A deliberate
   supersession is the same record: `result` is the owner's blob and `reason` says why the other heads' change is dropped.
4. Roots: every root with a recipe in a head's verify/admit.json must be admitted in the candidate or be recorded in `cut` with that head and a non-empty reason, else RED `root`;
   a `cut` record nothing needs is RED `record`.
`lost` (the candidate's blob is none of the heads' versions) is printed as a diagnostic count only; rule 2 covers it.
Prints one global line (sources, files checked, clean-equal, clean-red, conflicts, records, roots, the candidate commit). Exit 0 iff nothing is red."""
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
    heads = [source_head(s) for s in doc["sources"]]
    rowheads = [rev(s["head"]) for s in doc["sources"]]
    memo = {}

    def mbs(g, prev):
        k = (g, tuple(prev))
        if k not in memo:
            memo[k] = git("merge-base", g, *prev).decode().strip()
        return memo[k]
    cb = blobs(cand)
    hb = [blobs(h) for h in heads]
    tb = [blobs(git("merge-base", h, trunk).decode().strip()) for h in heads]   # each head's own base on the trunk: what it changed
    merged = {m["file"]: m for m in doc.get("merged", [])}
    cut = {m["root"]: m for m in doc.get("cut", [])}
    red, st = [], {"clean_equal": 0, "clean_red": 0, "conflicts": 0, "conflict_recorded": 0, "lost": 0}
    files = set()
    for b, t in zip(hb, tb):
        files |= {p for p in set(b) | set(t) if b.get(p) != t.get(p)}
    cutroots = [m["root"].rstrip("/") + "/" for m in doc.get("cut", [])]
    files = {p for p in files if not any(p.startswith(r) for r in cutroots)}   # a cut root takes its files with it
    for p in sorted(files):
        vers = [(h[:9], h, b.get(p)) for h, b, t in zip(heads, hb, tb) if b.get(p) != t.get(p)]
        exp, ok = expected(p, vers, mbs)
        if cb.get(p) not in {v for _, _, v in vers}:
            st["lost"] += 1
        if not ok:
            st["conflicts"] += 1
            m = merged.get(p)
            names = ",".join(h for h, _, _ in vers)
            if m is None:
                red.append(("conflict", p, names, "the heads' changes conflict and no merged record gives the result"))
            elif cb.get(p) != m["result"] or not m.get("reason"):
                red.append(("conflict", p, names, "candidate %s, record result %s%s" % ((cb.get(p) or "-")[:9], m["result"][:9], "" if m.get("reason") else ", no reason")))
            else:
                st["conflict_recorded"] += 1
            continue
        if p in merged:
            red.append(("record", p, "", "merged record for a clean merge or for a file no head changed"))
        if cb.get(p) == exp:
            st["clean_equal"] += 1
        else:
            st["clean_red"] += 1
            red.append(("merge-red", p, ",".join(h for h, _, _ in vers), "candidate %s, expected %s" % ((cb.get(p) or "-")[:9], (exp or "-")[:9])))
    for p in sorted(set(merged) - files):
        red.append(("record", p, "", "merged record for a file no head changed"))
    ca, nroots, used = admitted(cand), 0, set()
    for g, rh in zip(heads, rowheads):
        for r in sorted(admitted(g)):
            nroots += 1
            if r in ca:
                continue
            c = cut.get(r)
            if c and rh in {rev(h) for h in c["heads"]} and c.get("reason"):
                used.add(r)
            else:
                red.append(("root", r, rh[:9], "admitted by the head, not by the candidate, not cut with a reason"))
    for r in sorted(set(cut) - used):
        red.append(("record", r, "", "cut record nothing needs (the candidate admits the root, no listed head does, or no reason)"))
    st.update({"candidate": cand, "trunk": trunk, "sources": len(heads), "files": len(files), "roots_checked": nroots, "red": len(red)})
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
