"""datapath.py - the game-data side of the emulated boot (tests/s5/emu_guest.py): where the guest's SD paths land on
the host, and how far the game got through its data (S5-data's milestones).

Mounts. The guest sees one SD card ("sdmc:/"), which emu_guest maps to --root. --game DIR mounts the game install
(C:\\FalloutNV: FalloutNV.exe, Data/, Fallout_default.ini) at the guest's sdmc:/switch/fnv (the root itself, NX1's
sdlayout.h) and at sdmc:/fnvnx/game (the layout of guests built before it), so a full Data/ is used where it lies, never
copied. Without --game both prefixes are <root>/fnvnx/game, so "--file fnvnx/game/FalloutNV.exe=..." serves old and
new guests alike. The SD card's FAT/exFAT folds case, and the guest relies on it (os_fs_case_insensitive() = 1, path.cpp skips
its directory scan), so each component is matched case-insensitively here (exact spelling first). Components that do
not exist keep the spelling given. ".." stops at the card's root: nothing outside --root and --game is reachable.

Data milestones (milestones.txt kind 'data'), from the host file calls alone:
  ini-read        a read of Fallout_default.ini, Fallout.ini or FalloutPrefs.ini returned bytes
  archive-list    the first file call on an archive of the archive list (SArchiveList of [Archive] in the game's
                  Fallout_default.ini, overridden by My Games/FalloutNV/Fallout.ini; without either: Data/*.bsa)
  bsa-opened      every archive of the list that exists in Data/ was opened (the summary has k/N)
  esm-header      a read at offset 0 of Data/FalloutNV.esm returned the TES4 record's tag
  masters-loaded  FalloutNV.esm and every .esm that plugins.txt lists and Data/ holds were read up to their end
File faults: every host file call that fails, by call, errno and SD path (a missing optional file is one too; the
summary keeps the counts so a change can be measured).
"""
from __future__ import annotations

import errno as E
import os
import re

GAME_MOUNTS = ("switch/fnv", "fnvnx/game")
# the port's own entries of sdmc:/switch/fnv: they stay on the card (--root), never in --game. The list is NX1's
# FNVNX_SD_PORT_NAMES (toolchain/include/fnvnx/sdlayout.h); the copy below is used only while that header is absent.
SDLAYOUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "toolchain", "include", "fnvnx",
                        "sdlayout.h")
_PORT_NAMES_FALLBACK = ("fnv.nro fnv-exefs.nsp guest.nso fnvnx.ini user drive_* host.log crash.log launcher.log "
                        "override.txt README.txt shaders.nvc0cache nxgpu.log")


def port_names(path=SDLAYOUT):
    """(exact names, prefixes of 'x*' entries), lower-case, from sdlayout.h's FNVNX_SD_PORT_NAMES"""
    text = _PORT_NAMES_FALLBACK
    try:
        with open(path) as f:
            m = re.search(r'#define\s+FNVNX_SD_PORT_NAMES\s+((?:"[^"]*"\s*(?:\\\n\s*)?)+)', f.read())
        if m:
            text = " ".join(re.findall(r'"([^"]*)"', m.group(1)))
    except OSError:
        pass
    names = text.lower().split()
    return {n for n in names if not n.endswith("*")}, tuple(n[:-1] for n in names if n.endswith("*"))


PORT_NAMES, PORT_PREFIXES = port_names()
DATA_MILESTONES = ("ini-read", "archive-list", "bsa-opened", "esm-header", "masters-loaded")
INI_NAMES = {"fallout_default.ini", "fallout.ini", "falloutprefs.ini"}
# the profile: sdmc:/switch/fnv/user (the layout), sdmc:/fnvnx/user (guests built before it)
USER_DIRS = ("switch/fnv/user", "fnvnx/user")
USER_INI = "Documents/My Games/FalloutNV/Fallout.ini"
PLUGINS = "AppData/Local/FalloutNV/plugins.txt"


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


def ini_value(path, section, key):
    """the value of key in [section] of a Windows INI file (last one wins, as GetPrivateProfileString), or None"""
    try:
        with open(path, "rb") as f:
            text = f.read().decode("latin-1")
    except OSError:
        return None
    cur, val = None, None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            cur = s[1:s.find("]")].strip().lower() if "]" in s else None
        elif cur == section.lower() and "=" in s and not s.startswith(";"):
            k, _, v = s.partition("=")
            if k.strip().lower() == key.lower():
                val = v.strip()
    return val


class DataPath:
    def __init__(self, root, game=None):
        self.root, self.game = root, game or os.path.join(root, "fnvnx", "game")
        self.listing = {}
        self.faults = {}                 # (call, errno name, sd path) -> count
        self.fd_path = {}                # fd -> (sd path lower-case, host path)
        self.read_end = {}               # host path -> the furthest byte read
        self.opened = set()              # host paths opened successfully
        self.touched = set()             # host paths of any file call
        self.events = {}                 # data milestone -> how
        self.notify = None               # callable(name, how), set by the emulator
        self._targets = None

    # -- paths
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

    def _names(self, d):
        if d not in self.listing:
            try:
                self.listing[d] = os.listdir(d)
            except OSError:
                self.listing[d] = None
        return self.listing[d]

    def resolve(self, sd):
        """the host path of an SD path ("sdmc:/x/y" or "/x/y")"""
        if sd.lower().startswith("sdmc:"):
            sd = sd[5:]
        base, rest = self._mount(_parts(sd))
        cur = base
        for c in rest:
            nxt = os.path.join(cur, c)
            if not os.path.lexists(nxt):
                names = self._names(cur)
                hit = [n for n in names or () if n.casefold() == c.casefold()]
                if hit:
                    nxt = os.path.join(cur, sorted(hit)[0])
            cur = nxt
        return cur

    def changed(self):
        """a call that creates, removes or renames: drop the cached directory listings"""
        self.listing.clear()

    # -- events (the emulator calls these from its fs host calls)
    def fault(self, call, sd, err):
        if call == "mkdir" and err == E.EEXIST:   # path.cpp's mkdirs: making a directory that exists is no fault
            return
        k = (call, E.errorcode.get(err, str(err)), sd)
        self.faults[k] = self.faults.get(k, 0) + 1

    def touch(self, host):
        self.touched.add(os.path.normcase(host))
        self._check("archive-list")

    def on_open(self, fd, sd, host):
        self.fd_path[fd] = host
        self.opened.add(os.path.normcase(host))
        self.touch(host)
        self._check("bsa-opened")

    def on_close(self, fd):
        self.fd_path.pop(fd, None)

    def on_read(self, fd, off, data):
        host = self.fd_path.get(fd)
        if host is None or not data:
            return
        key = os.path.normcase(host)
        end = off + len(data)
        if end > self.read_end.get(key, 0):
            self.read_end[key] = end
        name = os.path.basename(host).lower()
        if name in INI_NAMES:
            self._hit("ini-read", f"read {name}")
        if name == "falloutnv.esm" and off == 0 and data[:4] == b"TES4":
            self._hit("esm-header", "read FalloutNV.esm @0 = TES4")
        if name.endswith(".esm"):
            self._check("masters-loaded")

    # -- the milestones
    def _hit(self, name, how):
        if name not in self.events:
            self.events[name] = how
            if self.notify:
                self.notify(name, how)

    def data_dir(self):
        return self.resolve("/switch/fnv/Data")

    def targets(self):
        """(archives of the list that exist, masters that exist), host paths; computed once, on first use"""
        if self._targets is None:
            data = self.data_dir()
            gdir = os.path.dirname(data)
            lst = None
            for u in USER_DIRS:
                lst = lst if lst is not None else ini_value(self.resolve(f"/{u}/{USER_INI}"), "Archive",
                                                            "SArchiveList")
            if lst is None:
                lst = ini_value(os.path.join(gdir, "Fallout_default.ini"), "Archive", "SArchiveList")
            names = ([n.strip() for n in lst.split(",") if n.strip()] if lst is not None else
                     [n for n in (self._names(data) or ()) if n.lower().endswith(".bsa")])
            bsas = [p for p in (os.path.join(data, n) for n in names) if os.path.isfile(p)]
            bsas = [self.resolve_host(p) for p in bsas]
            masters = ["FalloutNV.esm"]
            try:
                plug = [p for p in (self.resolve(f"/{u}/{PLUGINS}") for u in USER_DIRS) if os.path.isfile(p)]
                with open(plug[0] if plug else "", encoding="latin-1") as f:
                    for line in f:
                        n = line.split("#", 1)[0].strip()
                        if n.lower().endswith(".esm") and n.lower() != "falloutnv.esm":
                            masters.append(n)
            except OSError:
                pass
            ms = [self.resolve_host(os.path.join(data, n)) for n in masters]
            self._targets = ([os.path.normcase(p) for p in bsas],
                             [os.path.normcase(p) for p in ms if os.path.isfile(p)])
        return self._targets

    def resolve_host(self, p):
        d, n = os.path.split(p)
        hit = [x for x in self._names(d) or () if x.casefold() == n.casefold()]
        return os.path.join(d, sorted(hit)[0]) if hit and not os.path.exists(p) else p

    def _check(self, name):
        if name in self.events:
            return
        bsas, masters = self.targets()
        if name == "archive-list" and any(b in self.touched for b in bsas):
            self._hit(name, "file call on " + os.path.basename(next(b for b in bsas if b in self.touched)))
        elif name == "bsa-opened" and bsas and all(b in self.opened for b in bsas):
            self._hit(name, f"{len(bsas)} archives opened")
        elif name == "masters-loaded" and masters and all(
                self.read_end.get(m, 0) >= os.path.getsize(m) for m in masters):
            self._hit(name, f"{len(masters)} masters read to the end")

    def counts(self):
        bsas, masters = self.targets()
        nf = sum(self.faults.values())
        return {
            "bsa_opened": sum(b in self.opened for b in bsas), "bsa_listed": len(bsas),
            "masters_read": sum(self.read_end.get(m, 0) >= os.path.getsize(m) for m in masters),
            "masters": len(masters),
            "file_faults": nf, "file_faults_distinct": len(self.faults),
            "file_faults_enoent": sum(v for (c, e, p), v in self.faults.items() if e == "ENOENT"),
        }

    def lines(self):
        c = self.counts()
        out = [f"data-counts\tbsa {c['bsa_opened']}/{c['bsa_listed']}\tmasters {c['masters_read']}/{c['masters']}"
               f"\tfile-faults {c['file_faults']} ({c['file_faults_distinct']} distinct, {c['file_faults_enoent']} "
               f"ENOENT)"]
        for (call, err, sd), n in sorted(self.faults.items(), key=lambda kv: (-kv[1], kv[0])):
            out.append(f"file-fault\t{n}\t{call}\t{err}\t{sd}")
        return out

