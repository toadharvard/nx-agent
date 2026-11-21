#!/usr/bin/env python3
"""Candidate assembly check (owner order "defects impossible by construction", rule 8; L1 rule of 2026-11-21): a whole-tree take must not lose another head's edits.
    python3 -I verify/candcheck.py --sources FILE [--candidate REF] [--json OUT]        cwd = the git checkout root
FILE (the candidate's source list, JSON): {"trunk": REF, "sources": [{"head": REV, "owner": NAME, "base": REV (optional)}], "merged": [{"file": PATH, "heads": [REV..], "reason": TEXT}],
"cut": [{"root": PATH, "heads": [REV..], "reason": TEXT}]}. Nothing else is listed: the files come from git.
Rule. For each source head G, BASE = `base` when the row names one (a patch commit on another source), else merge-base(G, trunk). Every file G changed between BASE and G
(added, modified, deleted) must be in the candidate exactly as in G (same blob; deleted: absent), or be recorded in `merged` with G among its heads (a 3-way merge of BASE,
G and the other heads, made in the assembly commit), else RED, naming the file and G. Admitted roots follow from the same rule: a root with a recipe in G's verify/admit.json
(its rows, not only the ones G changed) must be admitted in the candidate or be recorded in `cut` with G among its heads and a reason, else RED.
A file whose candidate version equals no version written by any head that changed it is `lost` (the loss of the 2026-11-21 cpuid_model.json defect); it is a subset of the per-head reds.
A `merged` or `cut` record that no check needed (nothing differs) is also red: the record names only what the assembly actually did.
Prints one global line: sources, files checked (per head, and unique), red files (per head, and unique), lost files, roots checked, red roots, the candidate commit. Exit 0 iff nothing is red."""
import json
import subprocess
import sys

ADMIT = "ports/fnv-nx/verify/admit.json"


def git(*a):
    r = subprocess.run(["git"] + list(a), capture_output=True)
    if r.returncode:
        raise SystemExit("git %s: %s" % (" ".join(a), r.stderr.decode()[-300:]))
    return r.stdout


def rev(x):
    return git("rev-parse", "--verify", x + "^{commit}").decode().strip()


def blobs(c):
    """{path: blob sha} of every file of commit C."""
    out = {}
    for ln in git("ls-tree", "-r", "-z", c).split(b"\0"):
        if ln:
            meta, p = ln.split(b"\t", 1)
            out[p.decode()] = meta.split()[2].decode()
    return out


def changed(base, head):
    """{path: blob sha of HEAD, or None when deleted} for the files that differ between BASE and HEAD."""
    out = {}
    f = git("diff", "--raw", "--no-abbrev", "--no-renames", "-z", base, head).split(b"\0")
    i = 0
    while i + 1 < len(f):
        meta, p = f[i].decode(), f[i + 1].decode()
        m = meta.split()
        out[p] = None if m[4] == "D" else m[3]
        i += 2
    return out


def admitted(c):
    try:
        d = json.loads(git("show", c + ":" + ADMIT))
    except SystemExit:
        return set()
    return {r["path"] for r in d["roots"] if r.get("recipe")}


def check(doc, cand):
    """-> (stats, [(kind, file or root, head, text)])"""
    trunk, cand = rev(doc["trunk"]), rev(cand)
    cb = blobs(cand)
    merged = {m["file"]: {rev(h) for h in m["heads"]} for m in doc.get("merged", [])}
    cut = {m["root"]: ({rev(h) for h in m["heads"]}, m.get("reason", "")) for m in doc.get("cut", [])}
    ca = admitted(cand)
    red, nfiles, nroots, used_m, used_c, by_file = [], 0, 0, set(), set(), {}
    for s in doc["sources"]:
        g = rev(s["head"])
        base = rev(s["base"]) if s.get("base") else git("merge-base", g, trunk).decode().strip()
        for p, b in sorted(changed(base, g).items()):
            nfiles += 1
            by_file.setdefault(p, {})[s["head"]] = b
            if cb.get(p) == b:
                continue
            if g in merged.get(p, ()):
                used_m.add(p)
                continue
            red.append(("file", p, s["head"], "candidate %s, head %s" % ("lacks the file" if p not in cb else cb[p][:9], "deleted it" if b is None else b[:9])))
        for r in sorted(admitted(g) - (admitted(base) if s.get("base") else set())):
            nroots += 1
            if r in ca:
                continue
            hs, why = cut.get(r, (set(), ""))
            if g in hs and why:
                used_c.add(r)
                continue
            red.append(("root", r, s["head"], "admitted by the head, not by the candidate, not cut with a reason"))
    lost = sorted(p for p, hs in by_file.items() if cb.get(p) not in hs.values() and not (set(rev(h) for h in hs) & merged.get(p, set())))
    for p in lost:   # the candidate holds no version that any head wrote: the edits of every head that changed the file are gone
        red.append(("lost", p, ",".join(sorted(h[:9] for h in by_file[p])), "candidate %s equals no version written by the heads that changed it" % (cb.get(p) or "-")[:9]))
    for p in sorted(set(merged) - used_m):
        red.append(("merged", p, "", "recorded but every listed head's version already equals the candidate's, or the head did not change it"))
    for r in sorted(set(cut) - used_c):
        red.append(("cut", r, "", "recorded but no listed head admits it, or the candidate admits it, or no reason"))
    return {"candidate": cand, "sources": len(doc["sources"]), "files": nfiles, "red_files": sum(1 for k, *_ in red if k == "file"), "roots": nroots, "red_roots": sum(1 for k, *_ in red if k == "root"), "red_records": sum(1 for k, *_ in red if k in ("merged", "cut")), "files_unique": len(by_file), "red_files_unique": len({p for k, p, *_ in red if k == "file"}), "lost_files": len(lost)}, red


def main(argv):
    a = dict(zip(argv[::2], argv[1::2]))
    st, red = check(json.load(open(a["--sources"])), a.get("--candidate", "HEAD"))
    for k, p, h, t in red:
        print("RED %s %s (%s): %s" % (k, p, h[:9], t))
    print("candcheck: %s" % json.dumps(st, sort_keys=True))
    if "--json" in a:
        json.dump({"stats": st, "red": red}, open(a["--json"], "w"), indent=1)
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
