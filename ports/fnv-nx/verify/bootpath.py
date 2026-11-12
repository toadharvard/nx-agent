"""bootpath.py - the guest-path rule of the boot stage: where a guest SD path lands on the host.

A guest path is a string, "sdmc:/a/b" (prefix case-insensitive) or "/a/b"; "\\" counts as "/". Components "" and "." are
dropped; ".." removes the previous component and stops at the card's root, so nothing outside the mounts is reachable
(the path is refused by clamping, not by an error: "/../x" is "/x").
Mounts. The guest sees one SD card, mapped to `root`. The game install (`game`) is mounted at sdmc:/switch/fnv and at
sdmc:/fnvnx/game: a path under either prefix (compared case-folded) continues inside `game`, except that the port's own
entries of sdmc:/switch/fnv (PORT_NAMES, PORT_PREFIXES: logs, profile, launcher files) stay on `root`. Without `game`
it is <root>/switch/fnv when that directory holds anything besides the port's own entries, else <root>/fnvnx/game.
Read-only. The mounts of the declared inputs (verify/boot_inputs.json, bootdecl.py; the font set is sdmc:/switch/fnv/fonts) are on
`root` in every mode, never in `game` (their first component under switch/fnv is in PORT_NAMES), and read_only(sd) is true for a mount
and everything below it (mounts compared case-folded, after the ".." clamping of resolve). The mount list is the `read_only` argument
(boot.py passes the mounts of boot_inputs.json). The file calls that create, change or remove under it answer EACCES (boothost.py).
Case. The SD card folds case. Each component is matched against the host entries: if the exact spelling exists it is
used (os.path.lexists); else the host entries equal under str.casefold() are collected and the first in sorted order
(code-point order, deterministic) is taken; with no match the spelling given is kept (a path to be created).
The old driver took `sorted(hit)[0]` too, so two entries differing only by case resolve the same way here.
Directory listings are cached per directory until changed() (call after create/remove/rename).
"""
import errno as E
import os

GAME_MOUNTS = ("switch/fnv", "fnvnx/game")
PORT_NAMES = {"fnv.nro", "fnv-exefs.nsp", "guest.nso", "fnvnx.ini", "user", "host.log", "crash.log", "launcher.log",
              "override.txt", "readme.txt", "shaders.nvc0cache", "nxgpu.log", "fonts"}
PORT_PREFIXES = ("drive_",)


def _parts(p):
    out = []
    for c in p.replace("\\", "/").split("/"):
        if c in ("", "."):
            continue
        if c == "..":
            if out:
                out.pop()
            continue
        out.append(c)
    return out


class BootPath:
    def __init__(self, root, game=None, read_only=()):
        self.ro = [[c.lower() for c in _parts(m)] for m in read_only]
        sw = os.path.join(root, "switch", "fnv")
        inst = os.path.isdir(sw) and any(not (n.lower() in PORT_NAMES or n.lower().startswith(PORT_PREFIXES))
                                         for n in os.listdir(sw))
        self.root, self.game = root, game or (sw if inst else os.path.join(root, "fnvnx", "game"))
        if game:   # the card's own side of the mount points: the port's entries are made under them
            for m in GAME_MOUNTS:
                os.makedirs(os.path.join(root, *m.split("/")), exist_ok=True)
        else:      # the device always has sdmc:/switch/fnv
            os.makedirs(sw, exist_ok=True)
        self.listing = {}
        self.faults = {}                 # (call, errno name, sd path) -> count

    def _mount(self, parts):
        low = [c.lower() for c in parts]
        for m in GAME_MOUNTS:
            mp = m.split("/")
            if low[:len(mp)] == mp:
                rest = low[len(mp):]
                if m == "switch/fnv" and rest and (rest[0] in PORT_NAMES or rest[0].startswith(PORT_PREFIXES)):
                    break
                return self.game, parts[len(mp):]
        return self.root, parts

    def read_only(self, sd):
        """true for a guest path at or below a read-only mount (case-folded, after the same normalisation as resolve)"""
        if sd.lower().startswith("sdmc:"):
            sd = sd[5:]
        low = [c.lower() for c in _parts(sd)]
        return any(low[:len(m)] == m for m in self.ro)

    def _names(self, d):
        if d not in self.listing:
            try:
                self.listing[d] = os.listdir(d)
            except OSError:
                self.listing[d] = None
        return self.listing[d]

    def resolve(self, sd):
        """the host path of a guest path"""
        if sd.lower().startswith("sdmc:"):
            sd = sd[5:]
        base, rest = self._mount(_parts(sd))
        cur = base
        for c in rest:
            nxt = os.path.join(cur, c)
            if not os.path.lexists(nxt):
                hit = [n for n in self._names(cur) or () if n.casefold() == c.casefold()]
                if hit:
                    nxt = os.path.join(cur, sorted(hit)[0])
            cur = nxt
        return cur

    def changed(self):
        """a call that creates, removes or renames: drop the cached directory listings"""
        self.listing.clear()

    def fault(self, call, sd, err):
        """count a failed host file call; making a directory that exists is no fault"""
        if call == "mkdir" and err == E.EEXIST:
            return
        k = (call, E.errorcode.get(err, str(err)), sd)
        self.faults[k] = self.faults.get(k, 0) + 1
