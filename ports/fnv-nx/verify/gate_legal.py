#!/usr/bin/env python3
"""Gate "legal" (fnvnx-gate/1), rule L1: no tracked file of the public repository is a whole-file copy of a private artifact.
    python3 -I gate_legal.py     cwd = checkout root, env GATE_OUT, optional GATE_CACHE; writes GATE_OUT/legal.json
Universe: every file tracked in the repository (`git ls-files`, all of it, not only ports/fnv-nx). One item per path.
Manifest: the private artifacts' content hashes, never committed. It is the external `legal_manifest` of verify/legal_inputs.json (a content-pinned
commit and tree_sha256 of fnv-nx-build, fetched by recipe.fetch_externals like every external), file `manifest.tsv`:
    line 1   `# fnvnx-legal-manifest/1 cap=<bytes>`
    then     `<sha256 of the file bytes> TAB <size>` per private file of size 1..cap, sorted, unique (verify/legal_manifest.py builds it from the
             attached fnv-nx-build branches: gamedata/*, the decomp trees, the objects and links)
Certified (the first rule that applies):
  empty    the file has no bytes: it copies nothing;
  gitlink  a submodule entry: no file content in this repository;
  clean    size <= cap and its sha256 is not in the manifest.
Uncertified: a tracked file whose sha256 is in the manifest (a whole-file copy: `copy`), a file larger than the manifest cap (`above-cap`: the manifest does
not hold files that large, so no certificate exists; this is a statement about the manifest, never a pass), an unreadable file, and every tracked
file when the manifest is not declared, not fetchable, malformed or its tree hash differs (`no-manifest`: no vacuous green).
PROOF.  A tracked file F is a whole-file copy of a private file P iff bytes(F) = bytes(P); then sha256(F) = sha256(P) and size(F) = size(P). The manifest
holds sha256 of every private file of size 1..cap and a file of size > cap is red, so "clean" means no private file of the manifest's classes has the
bytes of F (assumption ASM-002: SHA-256 has no collisions). The classes of private files in the manifest are those the builder was given; this gate
proves the whole-file class only. Embedded substrings and name pairings are other classes (reported to the orchestrator, not judged here).
n/N over all tracked files is the result."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "contract")]
import gatefile  # noqa: E402

NAME = "legal"
INPUTS = "ports/fnv-nx/verify/legal_inputs.json"
EXTERNAL = "legal_manifest"
MANIFEST = "manifest.tsv"
HEADER = "# fnvnx-legal-manifest/1 cap="


def parse_manifest(text):
    """-> (cap, {sha256}) ; ValueError when malformed (a header, then sorted unique `sha256 TAB size` lines)"""
    lines = text.split("\n")
    if not lines or not lines[0].startswith(HEADER) or lines[-1] != "":
        raise ValueError("manifest: no header or no final LF")
    cap = lines[0][len(HEADER):]
    if not cap.isdigit() or int(cap) < 1:
        raise ValueError("manifest: bad cap")
    rows, prev = set(), None
    for n, ln in enumerate(lines[1:-1], 2):
        h, _, s = ln.partition("\t")
        if len(h) != 64 or any(c not in "0123456789abcdef" for c in h) or not s.isdigit() or not 1 <= int(s) <= int(cap):
            raise ValueError("manifest: line %d is not `sha256 TAB size` within the cap" % n)
        if prev is not None and h <= prev:
            raise ValueError("manifest: line %d is not sorted and unique" % n)
        prev = h
        rows.add(h)
    return int(cap), rows


def tracked(repo):
    """[(path, mode)] of `git ls-files -s`"""
    out = subprocess.run(["git", "-C", repo, "ls-files", "-s", "-z"], capture_output=True, check=True).stdout.decode("utf-8", "surrogateescape")
    res = []
    for e in out.split("\0"):
        if e:
            meta, _, path = e.partition("\t")
            res.append((path, meta.split()[0]))
    return sorted(res)


def judge(repo, files, manifest):
    """-> (universe, certified, uncertified ids, detail). MANIFEST (cap, set) or None (no manifest: every file is red)."""
    bad, kinds = [], {}

    def red(p, why):
        bad.append("%s [%s]" % (p, why))
        kinds[why] = kinds.get(why, 0) + 1
    for path, mode in files:
        if manifest is None:
            red(path, "no-manifest")
            continue
        if mode == "160000":
            continue
        fp = os.path.join(repo, path)
        try:
            if os.path.islink(fp):
                data = os.readlink(fp).encode("utf-8", "surrogateescape")
            else:
                size = os.path.getsize(fp)
                if size > manifest[0]:
                    red(path, "above-cap")
                    continue
                with open(fp, "rb") as fh:
                    data = fh.read()
        except OSError:
            red(path, "unreadable")
            continue
        if data and len(data) <= manifest[0] and hashlib.sha256(data).hexdigest() in manifest[1]:
            red(path, "copy")
        elif len(data) > manifest[0]:
            red(path, "above-cap")
    n = len(files)
    return n, n - len(bad), bad, "whole-file copies of private artifacts (rule L1); red by kind %s" % dict(sorted(kinds.items()))


def load_manifest(repo, cache):
    """-> (cap, set) or None; the external is fetched like every external of the verdict"""
    try:
        doc = json.load(open(os.path.join(repo, INPUTS), encoding="utf-8"))
        ext = [e for e in doc["external"] if e["name"] == EXTERNAL]
        if len(ext) != 1:
            return None
        import recipe  # noqa: E402  (the verdict's own fetch of externals)
        ws = tempfile.mkdtemp(prefix="legal-ext-")
        try:
            got = recipe.fetch_externals(ext, os.path.join(cache, "store"), os.path.join(cache, "external"), ws)
        finally:
            shutil.rmtree(ws, ignore_errors=True)
        with open(os.path.join(got[EXTERNAL]["host"], MANIFEST), encoding="utf-8") as fh:
            return parse_manifest(fh.read())
    except Exception:    # not declared, not fetchable, tree hash differs, malformed: no manifest, every file is red (never a pass)
        return None


def main():
    repo = "."
    tmp = None if os.environ.get("GATE_CACHE") else tempfile.mkdtemp(prefix="gate-legal-")
    try:
        cache = os.environ.get("GATE_CACHE") or tmp
        u, c, bad, detail = judge(repo, tracked(repo), load_manifest(repo, cache))
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    gatefile.write(NAME, u, c, bad, detail)
    print("legal: %d/%d tracked files certified; %s" % (c, u, detail))
    return 0 if c == u else 2


if __name__ == "__main__":
    sys.exit(main())
