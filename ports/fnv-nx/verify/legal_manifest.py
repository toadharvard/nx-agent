#!/usr/bin/env python3
"""legal_manifest - builds the manifest that gate legal (rule L1) reads. Runs where the private artifacts are (a checkout or bare clone of fnv-nx-build).
    python3 -I legal_manifest.py <git-dir> <out manifest.tsv> <cap> <ref-prefix>...
Every blob of every tree of the refs under the given prefixes (`git for-each-ref <prefix>`: a prefix names every ref below it), of size 1..cap bytes, is hashed (sha256 of its bytes); the result is the
sorted unique `sha256 TAB size` lines under the header `# fnvnx-legal-manifest/1 cap=<cap>`. Blobs are read once each (git cat-file --batch), not
kept. A blob larger than the cap is not in the manifest, and gate legal makes a tracked file that large red. The manifest is never committed to the
public repository; it is published to the private fnv-nx-build as the external `legal_manifest` of verify/legal_inputs.json."""
import hashlib
import subprocess
import sys


def blobs(gitdir, refs):
    """{blob id: size} of every blob reachable in the trees of REFS"""
    out = {}
    for ref in refs:
        p = subprocess.run(["git", "--git-dir", gitdir, "ls-tree", "-r", "-l", "-z", ref], capture_output=True, check=True).stdout.decode("utf-8", "surrogateescape")
        for e in p.split("\0"):
            if e:
                meta = e.partition("\t")[0].split()
                if meta[1] == "blob":
                    out[meta[2]] = int(meta[3])
    return out


def build(gitdir, refs, cap):
    todo = [b for b, s in sorted(blobs(gitdir, refs).items()) if 1 <= s <= cap]
    hashes = {}
    if todo:
        p = subprocess.Popen(["git", "--git-dir", gitdir, "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        for b in todo:
            p.stdin.write(b.encode() + b"\n")
            p.stdin.flush()
            head = p.stdout.readline().split()
            size = int(head[2])
            data = p.stdout.read(size)
            p.stdout.read(1)
            hashes[hashlib.sha256(data).hexdigest()] = size
        p.stdin.close()
        p.wait()
    return "# fnvnx-legal-manifest/1 cap=%d\n" % cap + "".join("%s\t%d\n" % (h, s) for h, s in sorted(hashes.items()))


def main(argv):
    gitdir, out, cap, globs = argv[0], argv[1], int(argv[2]), argv[3:]
    refs = subprocess.run(["git", "--git-dir", gitdir, "for-each-ref", "--format=%(refname)"] + globs, capture_output=True, check=True).stdout.decode().split()
    open(out, "w", newline="\n").write(build(gitdir, refs, cap))
    print("legal_manifest: %d refs" % len(refs))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
