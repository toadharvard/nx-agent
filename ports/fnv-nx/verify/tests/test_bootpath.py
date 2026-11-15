#!/usr/bin/env python3
"""Tests of verify/bootpath.py (the guest-path rule). Plain script: exit 0 on pass, one line per case."""
import atexit
import errno
import os
import shutil
import sys
import tempfile

VERIFY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, VERIFY)
import bootpath  # noqa: E402

TMP = tempfile.mkdtemp(prefix="bp-")
atexit.register(shutil.rmtree, TMP, ignore_errors=True)
FAILED, N = [], [0]


def case(name, ok, detail=""):
    N[0] += 1
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else "  " + str(detail)))
    if not ok:
        FAILED.append(name)


def touch(*p):
    f = os.path.join(*p)
    os.makedirs(os.path.dirname(f), exist_ok=True)
    open(f, "w").close()
    return f


def fresh(n):
    d = os.path.join(TMP, n)
    os.makedirs(d)
    return d


# no game: card only
root = fresh("a")
bp = bootpath.BootPath(root)
case("creates switch/fnv", os.path.isdir(os.path.join(root, "switch", "fnv")))
f = touch(root, "Data", "Foo.BIN")
case("prefix sdmc:", bp.resolve("sdmc:/Data/Foo.BIN") == f)
case("prefix SDMC: any case", bp.resolve("SDMC:/Data/Foo.BIN") == f)
case("no prefix", bp.resolve("/Data/Foo.BIN") == f)
case("backslash, dot, empty components", bp.resolve("sdmc:\\Data\\.//Foo.BIN") == f)
case("case folded", bp.resolve("sdmc:/data/FOO.bin") == f, bp.resolve("sdmc:/data/FOO.bin"))
case("exact spelling kept for a missing path", bp.resolve("/Data/New.txt") == os.path.join(root, "Data", "New.txt"))
case("missing path keeps given case", bp.resolve("/Nope/x") == os.path.join(root, "Nope", "x"))
case(".. inside the card", bp.resolve("/Data/../Data/Foo.BIN") == f)
case(".. above the root clamps at the root", bp.resolve("/../../Data/Foo.BIN") == f and
     bp.resolve("sdmc:/../../../x") == os.path.join(root, "x"))
# case collision: exact wins, else the first in sorted order
touch(root, "Dup", "aB")
touch(root, "Dup", "Ab")
bp.changed()
case("collision: exact spelling wins", bp.resolve("/Dup/Ab") == os.path.join(root, "Dup", "Ab"))
case("collision: sorted-first otherwise", bp.resolve("/Dup/AB") == os.path.join(root, "Dup", "Ab"),
     bp.resolve("/Dup/AB"))
# listing cache until changed()
case("cache stale before changed()", bp.resolve("/Dup/zz") == os.path.join(root, "Dup", "zz"))
g = touch(root, "Dup", "ZZ")
case("stale listing", bp.resolve("/Dup/zz") == os.path.join(root, "Dup", "zz"))
bp.changed()
case("fresh listing after changed()", bp.resolve("/Dup/zz") == g)

# --game mount: both prefixes, precedence over the root, port names stay on the card
root, game = fresh("b"), fresh("gm")
gf = touch(game, "Data", "FalloutNV.esm")
touch(root, "switch", "fnv", "Data", "FalloutNV.esm")      # shadowed by the mount
touch(root, "fnvnx", "game", "Data", "x")
bp = bootpath.BootPath(root, game)
case("mount dirs made on the card", all(os.path.isdir(os.path.join(root, *m.split("/")))
                                        for m in bootpath.GAME_MOUNTS))
case("game wins at switch/fnv", bp.resolve("sdmc:/switch/fnv/Data/FalloutNV.esm") == gf)
case("game wins at fnvnx/game, case folded", bp.resolve("sdmc:/FNVNX/Game/data/falloutnv.ESM") == gf,
     bp.resolve("sdmc:/FNVNX/Game/data/falloutnv.ESM"))
case("mount point itself", bp.resolve("/switch/fnv") == game)
case("port entry stays on the card", bp.resolve("/switch/fnv/host.log") == os.path.join(root, "switch", "fnv", "host.log"))
case("port prefix drive_* stays on the card", bp.resolve("/switch/fnv/Drive_C/x") ==
     os.path.join(root, "switch", "fnv", "Drive_C", "x"))
case("outside the mounts: the card", bp.resolve("/other/y") == os.path.join(root, "other", "y"))
case(".. cannot leave the game mount into the card", bp.resolve("/switch/fnv/../../etc/passwd") ==
     os.path.join(root, "etc", "passwd"))

# no --game: install detection
root = fresh("c")
touch(root, "switch", "fnv", "FalloutNV.exe")
case("install on the card: game is switch/fnv", bootpath.BootPath(root).game == os.path.join(root, "switch", "fnv"))
root = fresh("d")
touch(root, "switch", "fnv", "host.log")
case("only port entries: game is fnvnx/game", bootpath.BootPath(root).game == os.path.join(root, "fnvnx", "game"))

# faults
bp = bootpath.BootPath(fresh("e"))
bp.fault("mkdir", "/x", errno.EEXIST)
bp.fault("open", "/x", errno.ENOENT)
bp.fault("open", "/x", errno.ENOENT)
case("faults counted, mkdir EEXIST is none", bp.faults == {("open", "ENOENT", "/x"): 2}, bp.faults)

print("%d failed" % len(FAILED) if FAILED else "all %d passed" % N[0])
sys.exit(1 if FAILED else 0)
