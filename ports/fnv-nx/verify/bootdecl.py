"""bootdecl.py - declared inputs of the boot stage: port content (not game content) pinned by commit and a checksum list, verified,
then mounted read-only on the SD root.

  bootdecl.py verify --pins verify/boot_inputs.json --src NAME=DIR ...      (verify only)
  bootdecl.py mount --pins verify/boot_inputs.json --root SDROOT [--src NAME=DIR ...] [--artifacts URL] [--record OUT.json]

A pin ("fnvnx-boot-inputs/1", verify/boot_inputs.json) is {name, repo, ref, commit, path, sums, sums_sha256, mount}. `ref` is
documentation; the commit decides. The source tree of a pin is `path` and the file `sums` of that commit (the sums file lies at the
commit's root, `path` is a directory below it). `sums` is `sha256sum` format; its own sha256 must equal `sums_sha256`.
Rule (verify): the mounted set is the entries of `sums` whose name starts with `path/`; the files that exist under the source `path`
are exactly that set (an extra or a missing file is an error); each file's sha256 equals its entry. Other entries of `sums` (files of
the commit outside `path`, e.g. its documentation inputs) are not mounted and not read. Mount: copy to <root>/<mount>, files 0444,
directories 0555 (the boot's path model refuses writes under it, bootpath.BootPath.read_only). Any failure is an InputError: the boot
does not start. Source: `--src NAME=DIR` (a checkout holding `path` and `sums`), else `git fetch --depth 1 <URL> <commit>` of the
exact commit (URL: --artifacts, $ARTIFACTS_URL, else https://github.com/toadharvard/fnv-nx-build). The record
{name, commit, sums_sha256, files} of each input is what OUT.json "inputs" gets.
"""
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

FORMAT = "fnvnx-boot-inputs/1"
KEYS = {"name", "repo", "ref", "commit", "path", "sums", "sums_sha256", "mount"}
DEFAULT_URL = "https://github.com/toadharvard/fnv-nx-build"


class InputError(Exception):
    pass


def load_pins(path):
    try:
        d = json.load(open(path))
    except (OSError, ValueError) as e:
        raise InputError("%s: %s" % (path, e))
    if d.get("format") != FORMAT or not isinstance(d.get("inputs"), list):
        raise InputError("%s: format is not %s" % (path, FORMAT))
    names = []
    for p in d["inputs"]:
        if not isinstance(p, dict) or set(p) != KEYS or not all(isinstance(v, str) for v in p.values()):
            raise InputError("%s: an input needs exactly the string keys %s" % (path, ", ".join(sorted(KEYS))))
        for k, rx in (("commit", "[0-9a-f]{40}"), ("sums_sha256", "[0-9a-f]{64}"), ("name", "[a-z0-9._-]+")):
            if not re.fullmatch(rx, p[k]):
                raise InputError("%s: input %r: %s is not %s" % (path, p["name"], k, rx))
        for k in ("path", "mount", "sums"):
            parts = p[k].split("/")
            if not p[k] or p[k].startswith("/") or "" in parts or "." in parts or ".." in parts:
                raise InputError("%s: input %r: %s %r is not a plain relative path" % (path, p["name"], k, p[k]))
        names.append(p["name"])
    if len(set(names)) != len(names):
        raise InputError("%s: duplicate input names" % path)
    return d["inputs"]


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def parse_sums(text):
    out = {}
    for i, line in enumerate(text.splitlines(), 1):
        m = re.fullmatch(r"([0-9a-f]{64}) [ *]([^\n]+)", line)
        if not m:
            raise InputError("sums line %d is not `sha256  name`: %r" % (i, line[:80]))
        if m.group(2) in out:
            raise InputError("sums name %r listed twice" % m.group(2))
        out[m.group(2)] = m.group(1)
    return out


def verify(pin, src):
    """src holds `pin.path` and `pin.sums`; -> {name: sha256} of the mounted files, or InputError"""
    sf = os.path.join(src, pin["sums"])
    if not os.path.isfile(sf):
        raise InputError("%s: %s is missing from the source" % (pin["name"], pin["sums"]))
    if _sha(sf) != pin["sums_sha256"]:
        raise InputError("%s: sha256(%s) is %s, the pin says %s" % (pin["name"], pin["sums"], _sha(sf), pin["sums_sha256"]))
    entries = parse_sums(open(sf, encoding="utf-8").read())
    pre = pin["path"] + "/"
    want = {n: s for n, s in entries.items() if n.startswith(pre)}
    if not want:
        raise InputError("%s: %s lists no file under %s" % (pin["name"], pin["sums"], pre))
    base = os.path.join(src, pin["path"])
    have = set()
    for dp, dn, fn in os.walk(base):
        for f in fn:
            full = os.path.join(dp, f)
            if os.path.islink(full) or not os.path.isfile(full):
                raise InputError("%s: %s is not a regular file" % (pin["name"], full))
            have.add(pin["path"] + "/" + os.path.relpath(full, base).replace(os.sep, "/"))
        dn[:] = [d for d in dn if not os.path.islink(os.path.join(dp, d))]
    if have - set(want):
        raise InputError("%s: files not in %s: %s" % (pin["name"], pin["sums"], ", ".join(sorted(have - set(want))[:5])))
    if set(want) - have:
        raise InputError("%s: files of %s missing: %s" % (pin["name"], pin["sums"], ", ".join(sorted(set(want) - have)[:5])))
    for n in sorted(want):
        if _sha(os.path.join(src, n)) != want[n]:
            raise InputError("%s: %s: sha256 differs from %s" % (pin["name"], n, pin["sums"]))
    return {n[len(pre):]: s for n, s in want.items()}


def mount(pin, src, root):
    """verify src, copy its files to root/<mount> (replacing what is there), make them read-only; -> record"""
    files = verify(pin, src)
    dest = os.path.join(root, *pin["mount"].split("/"))
    if os.path.lexists(dest):
        for dp, dn, fn in os.walk(dest):
            os.chmod(dp, 0o755)
        shutil.rmtree(dest)
    for n in sorted(files):
        t = os.path.join(dest, *n.split("/"))
        os.makedirs(os.path.dirname(t), exist_ok=True)
        shutil.copyfile(os.path.join(src, pin["path"], *n.split("/")), t)
        os.chmod(t, 0o444)
    for dp, dn, fn in os.walk(dest, topdown=False):
        os.chmod(dp, 0o555)
    return {"name": pin["name"], "commit": pin["commit"], "sums_sha256": pin["sums_sha256"], "files": len(files)}


def fetch(pin, url, tmp):
    """the exact commit of the pin, `path` and `sums` extracted into tmp/<name>"""
    g, d = os.path.join(tmp, "repo-" + pin["name"]), os.path.join(tmp, pin["name"])
    os.makedirs(d)
    try:
        for cmd in (["init", "-q", g], ["-C", g, "fetch", "-q", "--depth", "1", url, pin["commit"]]):
            subprocess.run(["git"] + cmd, check=True, capture_output=True)
        got = subprocess.run(["git", "-C", g, "rev-parse", "FETCH_HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        if got != pin["commit"]:
            raise InputError("%s: fetched %s, the pin says %s" % (pin["name"], got, pin["commit"]))
        tar = subprocess.run(["git", "-C", g, "archive", pin["commit"], pin["path"], pin["sums"]], check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", d], input=tar, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        raise InputError("%s: cannot fetch %s of %s: %s" % (pin["name"], pin["commit"][:12], url, (e.stderr or b"").decode()[-200:].strip()))
    return d


def mount_all(pins_path, root, srcs=None, url=None):
    """verify-and-mount every pin of pins_path into root; srcs {name: dir} replaces the fetch; -> [record]"""
    pins, recs = load_pins(pins_path), []
    srcs = srcs or {}
    unknown = set(srcs) - {p["name"] for p in pins}
    if unknown:
        raise InputError("--src names no input: %s" % ", ".join(sorted(unknown)))
    tmp = tempfile.mkdtemp(prefix="bootdecl-")
    try:
        for p in pins:
            src = srcs.get(p["name"]) or fetch(p, url or os.environ.get("ARTIFACTS_URL") or DEFAULT_URL, tmp)
            recs.append(mount(p, src, root))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return recs


def verify_all(pins_path, srcs):
    """verify (no mount) every pin of pins_path against srcs {name: dir}; -> [record]"""
    pins = load_pins(pins_path)
    missing = [p["name"] for p in pins if p["name"] not in srcs]
    if missing:
        raise InputError("no --src for input(s) %s" % ", ".join(missing))
    return [{"name": p["name"], "commit": p["commit"], "sums_sha256": p["sums_sha256"], "files": len(verify(p, srcs[p["name"]]))} for p in pins]


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    vp = sub.add_parser("verify", help="verify the sources only (the job's early input-error), mount nothing")
    vp.add_argument("--pins", required=True)
    vp.add_argument("--src", action="append", default=[], metavar="NAME=DIR")
    sp = sub.add_parser("mount")
    sp.add_argument("--pins", required=True)
    sp.add_argument("--root", required=True)
    sp.add_argument("--src", action="append", default=[], metavar="NAME=DIR")
    sp.add_argument("--artifacts")
    sp.add_argument("--record")
    a = ap.parse_args(argv)
    if a.cmd == "verify":
        try:
            for r in verify_all(a.pins, dict(s.split("=", 1) for s in a.src)):
                print("bootdecl: %s@%s sums %s files %d verified" % (r["name"], r["commit"][:12], r["sums_sha256"][:12], r["files"]))
            return 0
        except (InputError, ValueError, OSError) as e:
            print("bootdecl: %s" % e, file=sys.stderr)
            return 1
    try:
        srcs = dict(s.split("=", 1) for s in a.src)
        recs = mount_all(a.pins, a.root, srcs, a.artifacts)
    except (InputError, ValueError, OSError) as e:
        print("bootdecl: %s" % e, file=sys.stderr)
        return 1
    if a.record:
        with open(a.record, "w") as f:
            f.write(json.dumps(recs, sort_keys=True, indent=1) + "\n")
    for r in recs:
        print("bootdecl: %s@%s sums %s files %d mounted" % (r["name"], r["commit"][:12], r["sums_sha256"][:12], r["files"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
