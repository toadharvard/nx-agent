"""bootdecl.py - the declared input of the boot stage: the pinned font mirror (port content, not game content), fetched by the recipe's own
external mechanism, verified by its tree hash, and mounted read-only at FNVNX_SD_FONTS on the SD root.

  bootdecl.py mount --pin manifest/fonts_mirror.json --root SDROOT [--record OUT.json]

The pin is manifest/fonts_mirror.json (L5's file): {name, repo, ref, commit, tree_sha256, at, mount_path[, note]}. Its keys name, repo, ref,
commit, tree_sha256 and at are the recipe's external entry (recipe.EXT_KEYS); `mount_path` is the subdirectory of the verified tree whose files
are the contents of FNVNX_SD_FONTS (gdifont.cpp lists that directory flat). Nothing of the pin is restated here: no path, no font list, no hash.
Fetch and verification: recipe.fetch_externals (the exact commit, the CAS tree hash of the whole checkout = tree_sha256, the verified pair
stored as a fact in the cache), in the cache GATE_CACHE names (a fresh temporary directory when it is unset, removed after). Mount point: the
string macro FNVNX_SD_FONTS of hostabi/include/fnvnx/sdlayout.h through verify/hostabirec.py, without its device prefix. Mount: copy of
<checkout>/<mount_path> to <root>/<mount point>, files 0444, directories 0555 (the boot's path model refuses writes under it,
bootpath.BootPath.read_only). Any failure is an InputError: the boot does not start. The record {name, commit, tree_sha256, files} is what
OUT.json "inputs.declared" gets.
"""
import json
import os
import shutil
import sys
import tempfile

import hostabirec
import recipe

REQUIRED = recipe.EXT_KEYS | {"mount_path"}
OPTIONAL = {"note"}
SD_PREFIX = "sdmc:/"
MOUNT_MACRO = "FNVNX_SD_FONTS"   # the one declared input of the boot stage: the macro of its mount point, read by name


class InputError(Exception):
    pass


def load_pin(path):
    try:
        d = json.load(open(path))
    except (OSError, ValueError) as e:
        raise InputError("%s: %s" % (path, e))
    if not isinstance(d, dict) or not REQUIRED <= set(d) <= REQUIRED | OPTIONAL or not all(isinstance(v, str) for v in d.values()):
        raise InputError("%s: the pin needs the string keys %s (and may carry %s)" % (path, ", ".join(sorted(REQUIRED)), ", ".join(sorted(OPTIONAL))))
    for k, n in (("commit", 40), ("tree_sha256", 64)):
        if len(d[k]) != n or any(c not in "0123456789abcdef" for c in d[k]):
            raise InputError("%s: %s is not %d lower-case hex digits" % (path, k, n))
    parts = d["mount_path"].split("/")
    if not d["mount_path"] or d["mount_path"].startswith("/") or "" in parts or "." in parts or ".." in parts:
        raise InputError("%s: mount_path %r is not a plain relative path" % (path, d["mount_path"]))
    return d


def mount_point():
    """the guest path of the mount without its device prefix: "switch/fnv/fonts" (read from sdlayout.h, never typed here)"""
    p = hostabirec.record().string(MOUNT_MACRO)
    if not p.lower().startswith(SD_PREFIX):
        raise InputError("%s is %r, not a path on %s" % (MOUNT_MACRO, p, SD_PREFIX))
    return p[len(SD_PREFIX):].strip("/")


def fetch(pin, cache):
    """the verified checkout of the pin in CACHE (recipe.fetch_externals) -> its directory, or InputError"""
    ext = {k: pin[k] for k in recipe.EXT_KEYS}
    try:
        got = recipe.fetch_externals([ext], os.path.join(cache, "store"), os.path.join(cache, "external"), os.path.join(cache, "ws"))
    except recipe.BuildRed as e:
        raise InputError("%s: %s" % (pin["name"], " ".join(str(x) for x in (e.args[0].get("stderr") or ["cannot fetch"])[-3:])[-400:]))
    return got[pin["name"]]["host"]


def mount(pin, src, root):
    """copy SRC/<mount_path> to root/<mount point> (replacing what is there), read-only; -> record"""
    base = os.path.join(src, *pin["mount_path"].split("/"))
    if not os.path.isdir(base) or os.path.islink(base):
        raise InputError("%s: mount_path %r is not a directory of the verified tree" % (pin["name"], pin["mount_path"]))
    dest = os.path.join(root, *mount_point().split("/"))
    if os.path.lexists(dest):
        for dp, dn, fn in os.walk(dest):
            os.chmod(dp, 0o755)
        shutil.rmtree(dest)
    n = 0
    for dp, dn, fn in os.walk(base):
        dn.sort()
        rel = os.path.relpath(dp, base)
        os.makedirs(os.path.join(dest, rel), exist_ok=True)
        for f in sorted(fn):
            s = os.path.join(dp, f)
            if os.path.islink(s):
                raise InputError("%s: %s is a symlink" % (pin["name"], s))
            shutil.copyfile(s, os.path.join(dest, rel, f))
            os.chmod(os.path.join(dest, rel, f), 0o444)
            n += 1
    for dp, dn, fn in os.walk(dest, topdown=False):
        os.chmod(dp, 0o555)
    return {"name": pin["name"], "commit": pin["commit"], "tree_sha256": pin["tree_sha256"], "files": n}


def mount_all(pin_path, root, cache=None):
    """load, fetch, verify and mount the pin of PIN_PATH into ROOT; CACHE (else $GATE_CACHE, else a temporary one) holds the fetch -> [record]"""
    pin = load_pin(pin_path)
    cache = cache or os.environ.get("GATE_CACHE")
    tmp = None if cache else tempfile.mkdtemp(prefix="bootdecl-")
    try:
        return [mount(pin, fetch(pin, cache or tmp), root)]
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("mount")
    sp.add_argument("--pin", required=True)
    sp.add_argument("--root", required=True)
    sp.add_argument("--cache")
    sp.add_argument("--record")
    a = ap.parse_args(argv)
    try:
        recs = mount_all(a.pin, a.root, a.cache)
    except (InputError, ValueError, OSError) as e:
        print("bootdecl: %s" % e, file=sys.stderr)
        return 1
    if a.record:
        with open(a.record, "w") as f:
            f.write(json.dumps(recs, sort_keys=True, indent=1) + "\n")
    for r in recs:
        print("bootdecl: %s@%s tree %s files %d mounted" % (r["name"], r["commit"][:12], r["tree_sha256"][:12], r["files"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
