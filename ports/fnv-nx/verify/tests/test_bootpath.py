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

# declared FalloutPrefs.ini: bootpath.py PORT_NAMES: port entries of sdmc:/switch/fnv stay on root
PREFS = "sdmc:/switch/fnv/user/Documents/My Games/FalloutNV/FalloutPrefs.ini"


def listing(d):
    return sorted(os.path.join(a, n) for a, ds, fs in os.walk(d) for n in ds + fs)


root, game = fresh("p1"), fresh("p1g")
touch(game, "Data", "FalloutNV.esm")
bp = bootpath.BootPath(root, game)
want = os.path.join(root, "switch", "fnv", "user", "Documents", "My Games", "FalloutNV", "FalloutPrefs.ini")
case("prefs with game: under root/switch/fnv/user (bootpath.py PORT_NAMES: port entries stay on root)",
     bp.resolve(PREFS) == want, bp.resolve(PREFS))
case("prefs with game: not under game", not bp.resolve(PREFS).startswith(game + os.sep))
# a missing path keeps the given spelling, so before creation compare case-folded; after it, exactly
case("prefs with game: any case of User stays on root (bootpath.py PORT_NAMES)",
     all(bp.resolve(PREFS.replace("/user/", "/" + u + "/")).casefold() == want.casefold() and
         not bp.resolve(PREFS.replace("/user/", "/" + u + "/")).startswith(game + os.sep)
         for u in ("User", "USER", "uSeR")))
# a file created through the resolution lands on root; game unchanged
before = listing(game)
h = bp.resolve(PREFS)
os.makedirs(os.path.dirname(h), exist_ok=True)   # the platform's own mkdir of the profile dir
bp.changed()
with open(h, "w") as fh:
    fh.write("[Display]\n")
case("prefs created: lands on root (bootpath.py PORT_NAMES)", os.path.isfile(want) and bp.resolve(PREFS) == want)
case("prefs created: game listing unchanged", listing(game) == before)
bp.changed()
case("prefs created: any case of User resolves to the same file",
     all(bp.resolve(PREFS.replace("/user/", "/" + u + "/")) == want for u in ("User", "USER", "uSeR")))
case("prefs created: other spelling finds the same file",
     bp.resolve(PREFS.replace("FalloutPrefs.ini", "falloutprefs.INI").replace("/user/", "/User/")) == want)

# no game: the same path under root/switch/fnv/user
root = fresh("p2")
bp = bootpath.BootPath(root)
case("prefs without game: under root/switch/fnv/user (bootpath.py PORT_NAMES)",
     bp.resolve(PREFS) == os.path.join(root, "switch", "fnv", "user", "Documents", "My Games", "FalloutNV",
                                       "FalloutPrefs.ini"))

# BootPath creates neither the user dir nor the file (the platform does)
for n, g in (("p3", None), ("p4", fresh("p4g"))):
    root = fresh(n)
    bootpath.BootPath(root, g)
    u = os.path.join(root, "switch", "fnv", "user")
    case("BootPath(%s) does not create user dir or FalloutPrefs.ini" % ("root, game" if g else "root"),
         not os.path.exists(u))

# declared input: sdmc:/switch/fnv/fonts (bootpath.py PORT_NAMES, READ_ONLY): on the card's root in every mode, never in the game
RO = ["switch/fnv/fonts"]   # the mount list of verify/boot_inputs.json (boot.py passes it)
for tag, with_game in (("with game", True), ("without game", False)):
    root = fresh("f1" if with_game else "f2")
    game = fresh("f1g") if with_game else None
    touch(root, "switch", "fnv", "fonts", "Sub", "LiberationSans-Regular.ttf")
    if with_game:
        touch(game, "fonts", "game-own.ttf")        # the game directory's own "fonts" must never be reached
    bp = bootpath.BootPath(root, game, read_only=RO)
    want = os.path.join(root, "switch", "fnv", "fonts", "Sub", "LiberationSans-Regular.ttf")
    case("fonts %s: resolves under root/switch/fnv/fonts" % tag, bp.resolve("sdmc:/switch/fnv/fonts/Sub/LiberationSans-Regular.ttf") == want,
         bp.resolve("sdmc:/switch/fnv/fonts/Sub/LiberationSans-Regular.ttf"))
    case("fonts %s: case-folded FONTS/SUB/..." % tag, bp.resolve("SDMC:/Switch/FNV/FONTS/SUB/liberationsans-regular.TTF") == want,
         bp.resolve("SDMC:/Switch/FNV/FONTS/SUB/liberationsans-regular.TTF"))
    case("fonts %s: backslashes and .. stay on the same file" % tag, bp.resolve("sdmc:\\switch\\fnv\\x\\..\\fonts\\Sub\\LiberationSans-Regular.ttf") == want)
    if with_game:
        case("fonts with game: the game's own fonts/ is not reached", bp.resolve("sdmc:/switch/fnv/fonts/game-own.ttf") ==
             os.path.join(root, "switch", "fnv", "fonts", "game-own.ttf") and not os.path.exists(bp.resolve("sdmc:/switch/fnv/fonts/game-own.ttf")))
        case("fonts with game: the alias mount fnvnx/game/fonts is the game's", bp.resolve("sdmc:/fnvnx/game/fonts/game-own.ttf") ==
             os.path.join(game, "fonts", "game-own.ttf"))
    case("fonts %s: read_only for the dir, below it, any case, ..-normalised" % tag,
         all(bp.read_only(s) for s in ("sdmc:/switch/fnv/fonts", "/switch/fnv/fonts/Sub/x.ttf", "SDMC:/SWITCH/FNV/Fonts/new", "/a/../switch/fnv/fonts/q")))
    case("fonts %s: read_only is false elsewhere (sibling, parent, game-side alias)" % tag,
         not any(bp.read_only(s) for s in ("/switch/fnv", "/switch/fnv/fonts2", "/switch/fnv/user/fonts", "/fnvnx/game/fonts/x", "/fonts", "/switch")))

# the read-only set is the mount list given, not a fonts-only check
bp = bootpath.BootPath(fresh("f4"), None, read_only=["switch/fnv/fonts", "Switch/FNV/other/dir"])
case("read_only follows the mount list: two mounts, case-folded", bp.read_only("/switch/fnv/other/dir/x") and bp.read_only("/SWITCH/fnv/FONTS")
     and not bp.read_only("/switch/fnv/other") and not bp.read_only("/switch/fnv/other/dirx"))
case("no mounts: nothing is read-only (even fonts)", not bootpath.BootPath(fresh("f5")).read_only("/switch/fnv/fonts/a.ttf"))

# fonts alone in switch/fnv does not make it an install: no game -> the game dir stays root/fnvnx/game
root = fresh("f3")
touch(root, "switch", "fnv", "fonts", "a.ttf")
bp = bootpath.BootPath(root)
case("a card holding only fonts has no install in switch/fnv", bp.game == os.path.join(root, "fnvnx", "game"), bp.game)

print("%d failed" % len(FAILED) if FAILED else "all %d passed" % N[0])
sys.exit(1 if FAILED else 0)
