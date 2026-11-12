#!/usr/bin/env python3
"""patchtree: the stage program of recipe key "sources" (RECIPE.md, Rule A): copy the tree SRC to OUT, then apply the patches in
list order with GNU patch -p1 (fuzz 0, no backups). Any patch that does not apply cleanly ends the stage with patch's exit status.
    python3 -s -B patchtree.py SRC OUT PATCH..."""
import os
import shutil
import subprocess
import sys

PATCH = "/ptool/patch"   # GNU patch of env.json, a declared input of the stage


def copy(src, dst):
    """Every directory, regular file (with its mode) and symlink of SRC, in sorted order, below DST."""
    os.makedirs(dst, exist_ok=True)
    for n in sorted(os.listdir(src)):
        a, b = os.path.join(src, n), os.path.join(dst, n)
        if os.path.islink(a):
            os.symlink(os.readlink(a), b)
        elif os.path.isdir(a):
            copy(a, b)
        else:
            shutil.copyfile(a, b)
            os.chmod(b, 0o755 if os.access(a, os.X_OK) else 0o644)


def main(args):
    src, out, patches = args[0], args[1], args[2:]
    copy(src, out)
    for p in patches:
        r = subprocess.run([PATCH, "-p1", "-F0", "--no-backup-if-mismatch", "-d", out, "-i", p])
        if r.returncode:
            print("patchtree: %s does not apply cleanly" % p, file=sys.stderr)
            return r.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
