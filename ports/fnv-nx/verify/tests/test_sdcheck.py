#!/usr/bin/env python3
"""Tests for verify/sdcheck.py (synthetic trees only). Plain script: exit 0 on pass, PASS/FAIL line per case."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), "sdcheck.py")
ROOT = tempfile.mkdtemp(dir=os.path.expanduser("~"), prefix="sdcheck-test-")
FAILED = testcase.FAILED
_n = [0]
LAYOUT = {"game_root": "switch/fnv", "port_root": "", "writable": ["switch/fnv/user", "switch/fnv/logs"],
          "free_bytes_min": 1}


def fresh(name):
    _n[0] += 1
    p = os.path.join(ROOT, "%s-%d" % (name, _n[0]))
    os.makedirs(p)
    return p


def write(path, data=b"x", mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    if mtime:
        os.utime(path, (mtime, mtime))


def sparse(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.truncate(size)


def run(*args):
    r = subprocess.run([sys.executable, "-I", TOOL] + [str(a) for a in args], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr




def build(extra_game=(), fs="fat32", no_hash=False, layout=None, mutate=None):
    """game dir + port dir -> manifests; SD = copy; mutate(sd) breaks it. Returns (rc, out, sd)."""
    d = fresh("w")
    game, pkg, sd = d + "/game", d + "/pkg", d + "/sd"
    write(game + "/FalloutNV.exe", b"exe")
    write(game + "/Data/a.bsa", b"aaaa")
    write(game + "/Data/sub/b.esm", b"bbbb")
    for rel, data in extra_game:
        write(game + "/" + rel, data)
    write(pkg + "/switch/fnv/fnv.nro", b"nro")
    assert run("manifest", game, "-o", d + "/g.json")[0] == 0
    assert run("package", pkg, "-o", d + "/p.json")[0] == 0
    os.makedirs(sd)
    shutil.copytree(game, sd + "/switch/fnv", dirs_exist_ok=True)
    shutil.copytree(pkg, sd, dirs_exist_ok=True)
    if mutate:
        mutate(sd)
    lay = dict(LAYOUT, **(layout or {}))
    with open(d + "/l.json", "w") as f:
        json.dump(lay, f)
    args = ["check", "--game", d + "/g.json", "--port", d + "/p.json", "--layout", d + "/l.json", "--fs", fs, sd]
    if no_hash:
        args.insert(-1, "--no-hash")
    rc, out = run(*args)
    return rc, out, d


def expect(name, rc_want, words, **kw):
    rc, out, _ = build(**kw)
    case('expect', rc == rc_want and all(w in out for w in words), "rc=%d %s" % (rc, out[-300:]), key=name)


def main():
    expect("equal tree green", 0, ["sdcheck: GREEN"])
    expect("missing", 2, ["sdcheck: RED", "missing", "switch/fnv/Data/a.bsa"],
           mutate=lambda sd: os.remove(sd + "/switch/fnv/Data/a.bsa"))
    expect("extra outside writable", 2, ["RED", "extra", "switch/fnv/Data/new.txt"],
           mutate=lambda sd: write(sd + "/switch/fnv/Data/new.txt"))
    expect("extra inside writable ok", 0, ["GREEN"],
           mutate=lambda sd: write(sd + "/switch/fnv/user/Saves/s1.fos", b"zz"))
    expect("size diff", 2, ["RED", "size-diff", "switch/fnv/Data/a.bsa"],
           mutate=lambda sd: write(sd + "/switch/fnv/Data/a.bsa", b"aaaaa"))
    expect("hash diff same size", 2, ["RED", "hash-diff", "switch/fnv/Data/a.bsa"],
           mutate=lambda sd: write(sd + "/switch/fnv/Data/a.bsa", b"aaab"))
    expect("no-hash incomplete", 3, ["INCOMPLETE", "HASH NOT CHECKED"], no_hash=True)
    expect("no-hash still sees size diff", 2, ["RED", "size-diff"], no_hash=True,
           mutate=lambda sd: write(sd + "/switch/fnv/Data/a.bsa", b"a"))
    # case fold collision: manifest refuses
    d = fresh("cf")
    write(d + "/g/Data/a.bsa")
    write(d + "/g/data/A.bsa")
    rc, out = run("manifest", d + "/g", "-o", d + "/g.json")
    case("case-fold collision manifest error", rc == 2 and "Data/a.bsa" in out and "data/A.bsa" in out, out)
    # collision on the card between a game file and a different-case card file
    expect("case-fold collision on card", 2, ["RED", "collision", "switch/fnv/data/A.bsa"],
           mutate=lambda sd: write(sd + "/switch/fnv/data/A.bsa", b"q"))
    expect("name with colon", 2, ["name-violation", "a:b.txt"], extra_game=[("Data/a:b.txt", b"1")])
    expect("trailing dot", 2, ["name-violation", "x.dot."], extra_game=[("Data/x.dot.", b"1")])
    sys.path.insert(0, os.path.dirname(HERE))
    import sdcheck  # the OS caps names at 255 bytes, so the 255-unit rule is tested on the function
    case("name 255 units ok, 256 violation", sdcheck.name_violation("Data/" + "n" * 255) is None
         and sdcheck.name_violation("Data/" + "n" * 256) is not None
         and sdcheck.name_violation("Data/" + "\U0001F600" * 128) is not None)
    expect("name with colon ok on exfat", 0, ["GREEN"], extra_game=[("Data/a:b.txt", b"1")], fs="exfat")
    big = 2 ** 32 - 1
    for label, size, fs, rc_want, words in (
            ("2^32-1 fat32", big, "fat32", 3, ["INCOMPLETE", "over-limit 0"]),
            ("2^32 fat32", big + 1, "fat32", 2, ["RED", "over-limit", "Data/big.bsa"]),
            ("2^32 exfat", big + 1, "exfat", 3, ["INCOMPLETE", "over-limit 0"])):
        d = fresh("big")
        sparse(d + "/game/Data/big.bsa", size)
        write(d + "/pkg/switch/fnv/fnv.nro")
        rcm, om = run("manifest", d + "/game", "-o", d + "/g.json")
        rcp, _ = run("package", d + "/pkg", "-o", d + "/p.json")
        sd = d + "/sd"
        os.makedirs(sd + "/switch/fnv/Data")
        sparse(sd + "/switch/fnv/Data/big.bsa", size)
        write(sd + "/switch/fnv/fnv.nro")
        json.dump(LAYOUT, open(d + "/l.json", "w"))
        rc, out = run("check", "--game", d + "/g.json", "--port", d + "/p.json", "--layout", d + "/l.json",
                      "--fs", fs, "--no-hash", sd)
        case('check', rcm == 0 and rc == rc_want and all(w in out for w in words), "%d %d %s" % (rcm, rc, out[-300:]), key=label)
        shutil.rmtree(d)
    expect("free space below minimum", 2, ["RED", "free space", "required 99999999999999", "found"],
           layout={"free_bytes_min": 99999999999999})
    # determinism: creation order and mtimes
    outs = []
    for order, mt in (((1, 2, 3), 1000000000), ((3, 1, 2), 1700000000)):
        d = fresh("det")
        for i in order:
            write(d + "/g/Data/f%d.bin" % i, b"c%d" % i, mtime=mt + i)
        run("manifest", d + "/g", "-o", d + "/g.json")
        outs.append(open(d + "/g.json", "rb").read())
    run("manifest", d + "/g", "-o", d + "/g2.json")
    case("manifest deterministic", outs[0] == outs[1] == open(d + "/g2.json", "rb").read())
    # symlink is an error
    d = fresh("sl")
    write(d + "/g/a", b"1")
    os.symlink("a", d + "/g/l")
    rc, out = run("manifest", d + "/g", "-o", d + "/g.json")
    case("symlink error", rc == 2 and "l" in out, out)
    # emit-c
    rc, out, d = build()
    rc, out = run("emit-c", "--game", d + "/g.json", "--port", d + "/p.json", "--layout", d + "/l.json",
                  "-o", d + "/sd_manifest.inc")
    inc = open(d + "/sd_manifest.inc").read()
    with open(d + "/w.c", "w") as f:
        f.write('#include "sd_manifest.inc"\nint main(void){return (int)fnvnx_sd_manifest[0].size;}\n')
    gcc = subprocess.run(["gcc", "-fsyntax-only", "-I" + d, d + "/w.c"], capture_output=True, text=True)
    paths = ["switch/fnv/FalloutNV.exe", "switch/fnv/Data/a.bsa", "switch/fnv/Data/sub/b.esm", "switch/fnv/fnv.nro"]
    case("emit-c compiles and lists all", rc == 0 and gcc.returncode == 0 and all('"%s"' % p in inc for p in paths)
         and "never committed" in inc.split("\n")[0], gcc.stderr + out)
    # speed
    d = fresh("speed")
    for i in range(2000):
        write(d + "/g/Data/d%d/f%d.bin" % (i % 20, i), os.urandom(4096))
    t = time.time()
    rc, _ = run("manifest", d + "/g", "-o", d + "/g.json")
    print("INFO manifest of 2000 files: %.2fs rc=%d" % (time.time() - t, rc))
    shutil.rmtree(ROOT, ignore_errors=True)
    print("%d failed" % len(FAILED) if FAILED else "all passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
