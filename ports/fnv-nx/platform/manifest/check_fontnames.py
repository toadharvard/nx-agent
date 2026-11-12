#!/usr/bin/env python3 -I
"""Gate: every font file that the declared registry export names exists in the declared font set (L5 declared machine).

The declared machine is a Wine 9.0 prefix, and on it every font file that its registry names is present. So a named file that is
missing from the declared set means the reference set is incomplete: a build defect, closed here, never skipped at run time
(gdifont.cpp ends the process for a missing declared directory or an unreadable listed file; this gate closes the named files).

Rule (mechanical, from the declared inputs only):
  names   every string value of these keys of the .reg exports (Wine's UTF-16LE export format, key names compared without case):
            HKLM\\Software\\Microsoft\\Windows NT\\CurrentVersion\\Fonts                          (the font files)
            HKLM\\Software\\Microsoft\\Windows\\CurrentVersion\\Fonts                              (the same list, Win9x view)
            HKLM\\System\\CurrentControlSet\\Hardware Profiles\\Current\\Software\\Fonts           (the system and bitmap font lists;
                                                                                                    dword values are settings)
            HKLM\\Software\\Microsoft\\Windows NT\\CurrentVersion\\FontSubstitutes                 (the data when it is a file: ends in
                                                                                                    .ttf .ttc .otf .fon)
  where   a name without a drive and without a backslash is a file of the Wine data directory; a Z: path is a path of the host (Z:\\ is
          the host's root in a Wine prefix): below /usr/share/wine/fonts it is the Wine data directory, below /usr/share/fonts it is
          the host font directory; any other path has no declared place and is reported.
  gate    the set of named files must be a subset of the files below the declared wine and host directories (compared without case,
          as Windows paths are). Missing files and unplaceable paths are listed; the exit status is 1 if there is one.
Usage: check_fontnames.py <wine-fonts-dir> <host-fonts-dir> <reg-dir>      |   check_fontnames.py --selftest
"""
import os, sys

KEYS = {
    "hklm\\software\\microsoft\\windows nt\\currentversion\\fonts": "file",
    "hklm\\software\\microsoft\\windows\\currentversion\\fonts": "file",
    "hklm\\system\\currentcontrolset\\hardware profiles\\current\\software\\fonts": "file",
    "hklm\\software\\microsoft\\windows nt\\currentversion\\fontsubstitutes": "ext",
}
FONT_EXT = (".ttf", ".ttc", ".otf", ".fon")
WINE_PFX, HOST_PFX = ("usr", "share", "wine", "fonts"), ("usr", "share", "fonts")


def read_reg(path):
    """[(key lower-case without the HKEY_ prefix form, value name, string data)] of one Wine .reg export (UTF-16LE with BOM)."""
    raw = open(path, "rb").read()
    txt = raw[2:].decode("utf-16-le") if raw[:2] == b"\xff\xfe" else raw.decode("utf-8")
    out, key = [], None
    for line in txt.replace("\r", "").split("\n"):
        if line.startswith("["):
            key = line[1:line.index("]")].lower().replace("hkey_local_machine", "hklm", 1)
        elif key and line.startswith('"'):
            i = 1
            name = ""
            while line[i] != '"':
                if line[i] == "\\": i += 1
                name += line[i]; i += 1
            rest = line[i + 1:]
            if rest.startswith('="'):
                val, j = "", 2
                while j < len(rest) and rest[j] != '"':
                    if rest[j] == "\\": j += 1
                    val += rest[j]; j += 1
                out.append((key, name, val))
    return out


def named(rows):
    """{(place, relative path as a tuple of lower-case parts or None): [(key, value name, data)]}"""
    res = {}
    for key, name, data in rows:
        kind = KEYS.get(key)
        if kind is None or not data:
            continue
        if kind == "ext" and not data.lower().endswith(FONT_EXT):
            continue
        d = data.replace("/", "\\")
        if len(d) > 1 and d[1] == ":" or "\\" in d:
            parts = [p for p in d.split("\\") if p]
            low = [p.lower() for p in parts]
            if d[:3].lower() == "z:\\" and tuple(low[1:5]) == WINE_PFX:
                where = ("wine", tuple(low[5:]))
            elif d[:3].lower() == "z:\\" and tuple(low[1:4]) == HOST_PFX:
                where = ("host", tuple(low[4:]))
            else:
                where = ("none", tuple(low))
        else:
            where = ("wine", (d.lower(),))
        res.setdefault(where, []).append((key, name, data))
    return res


def files_below(d):
    s = set()
    for r, _, fs in os.walk(d):
        for f in fs:
            s.add(tuple(p.lower() for p in os.path.relpath(os.path.join(r, f), d).split(os.sep)))
    return s


def check(wine, host, regdir):
    rows = []
    for n in sorted(os.listdir(regdir)):
        if n.endswith(".reg"):
            rows += read_reg(os.path.join(regdir, n))
    nm = named(rows)
    have = {"wine": files_below(wine), "host": files_below(host), "none": set()}
    missing = sorted((w, rel, v[0][2]) for (w, rel), v in nm.items() if rel not in have[w])
    return len(nm), missing


def main(argv):
    if argv[1:] == ["--selftest"]:
        return selftest()
    if len(argv) != 4:
        print(__doc__); return 2
    n, missing = check(*argv[1:])
    for w, rel, data in missing:
        print("MISSING %-4s %s   (named as %s)" % (w, "/".join(rel), data))
    print("font files named by the declared registry: %d, missing from the declared set: %d" % (n, len(missing)))
    return 1 if missing else 0


def selftest():
    import tempfile
    t = tempfile.mkdtemp()
    for p in ("w/a.fon", "w/B.ttf", "h/opentype/x/y.otf"):
        os.makedirs(os.path.dirname(os.path.join(t, p)), exist_ok=True); open(os.path.join(t, p), "w").close()
    os.makedirs(os.path.join(t, "r"))
    reg = ('Windows Registry Editor Version 5.00\n\n[HKEY_LOCAL_MACHINE\\Software\\Microsoft\\Windows NT\\CurrentVersion\\Fonts]\n'
           '"A (TrueType)"="a.fon"\n"B"="b.TTF"\n"Y (TrueType)"="Z:\\\\usr\\\\share\\\\fonts\\\\opentype\\\\x\\\\y.otf"\n'
           '"Gone"="gone.fon"\n"Far"="Z:\\\\usr\\\\share\\\\fonts\\\\truetype\\\\far.ttf"\n"Odd"="C:\\\\windows\\\\fonts\\\\odd.ttf"\n'
           '[HKEY_LOCAL_MACHINE\\System\\CurrentControlSet\\Hardware Profiles\\Current\\Software\\Fonts]\n"FONTS.FON"="a.fon"\n"LogPixels"=dword:00000060\n"OEMFONT.FON"="oem.fon"\n'
           '[HKEY_LOCAL_MACHINE\\Software\\Microsoft\\Windows NT\\CurrentVersion\\FontSubstitutes]\n"Arial CE,238"="Arial,238"\n"Sub"="subst.ttf"\n'
           '[HKEY_CURRENT_USER\\Software\\Wine\\Fonts]\n"Ignored"="nowhere.ttf"\n')
    open(os.path.join(t, "r", "1.reg"), "wb").write(b"\xff\xfe" + reg.encode("utf-16-le"))
    n, missing = check(os.path.join(t, "w"), os.path.join(t, "h"), os.path.join(t, "r"))
    got = sorted((w, "/".join(r)) for w, r, _ in missing)
    want = sorted([("wine", "gone.fon"), ("host", "truetype/far.ttf"), ("none", "c:/windows/fonts/odd.ttf"), ("wine", "oem.fon"), ("wine", "subst.ttf")])
    assert got == want, (got, want)
    assert n == 8, n   # distinct files: a.fon (named twice) b.ttf y.otf gone far odd oem subst
    print("fontnames selftest ok: %d named, %d missing" % (n, len(missing)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
