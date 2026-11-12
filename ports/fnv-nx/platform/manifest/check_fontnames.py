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
  gate    the declared machine is the reference machine as exported, so the declared font set is that machine's font files, no more and
          no less (compared without case, as Windows paths are):
            (1) the files below the declared host dir equal the files of the reference's fc-list.txt (below /usr/share/fonts), and the
                files below the declared wine dir equal the file list of the reference's fonts-wine package;
            (2) a named file that the reference listing lacks is absent on the declared machine, derived mechanically from (1): Wine 9.0
                skips it (dlls/win32u/font.c load_system_bitmap_fonts / load_registry_fonts), and so does the product (gdifont.cpp).
                The set is printed with its count. A named file that the listing has is present by (1). A path with no declared place
                (neither Z:\\usr\\share\\wine\\fonts nor Z:\\usr\\share\\fonts) is an error.
          The exit status is 1 for any file that differs in (1) or any unplaceable path.
Usage: check_fontnames.py <wine-fonts-dir> <host-fonts-dir> <reg-dir> <fc-list.txt> <fonts-wine.list>      |   check_fontnames.py --selftest
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


def listing(path, prefix):
    """Files of a reference listing below `prefix` (lower-case parts): fc-list.txt lines `file: family...` or one path per line; # comments."""
    res = set()
    for line in open(path, errors="replace"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        f = line.split(": ")[0]
        if f.startswith(prefix):
            res.add(tuple(p.lower() for p in f[len(prefix):].split("/") if p))
    return res


def check(wine, host, regdir, fclist, winelist):
    """(number of named files, [(kind, text)] errors, [skipped (place, path, data)])"""
    rows = []
    for n in sorted(os.listdir(regdir)):
        if n.endswith(".reg"):
            rows += read_reg(os.path.join(regdir, n))
    nm = named(rows)
    ref = {"wine": listing(winelist, "/usr/share/wine/fonts/"), "host": listing(fclist, "/usr/share/fonts/")}
    have = {"wine": files_below(wine), "host": files_below(host)}
    errs, skipped = [], []
    for w in ("wine", "host"):
        for rel in sorted(have[w] - ref[w]):
            errs.append(("EXTRA", "%s %s is in the declared dir, not in the reference listing" % (w, "/".join(rel))))
        for rel in sorted(ref[w] - have[w]):
            errs.append(("LACKS", "%s %s is in the reference listing, not in the declared dir" % (w, "/".join(rel))))
    for (w, rel), v in sorted(nm.items()):
        if w == "none":
            errs.append(("NOPLACE", "%s (named as %s)" % ("/".join(rel), v[0][2])))
        elif rel not in ref[w]:
            skipped.append((w, "/".join(rel), v[0][2]))
    return len(nm), errs, skipped


def main(argv):
    if argv[1:] == ["--selftest"]:
        return selftest()
    if len(argv) != 6:
        print(__doc__); return 2
    n, errs, skipped = check(*argv[1:])
    for k, t in errs:
        print("%s %s" % (k, t))
    for w, rel, data in skipped:
        print("SKIP %-4s %s   (named as %s; absent on the reference machine, Wine 9.0 skips it)" % (w, rel, data))
    print("font files named by the declared registry: %d; declared set differs from the reference listing: %d; named, absent on the reference (skipped): %d"
          % (n, len(errs), len(skipped)))
    return 1 if errs else 0


def selftest():
    import tempfile
    t = tempfile.mkdtemp()
    for p in ("w/a.fon", "w/B.ttf", "h/opentype/x/y.otf", "h/extra.ttf"):
        os.makedirs(os.path.dirname(os.path.join(t, p)), exist_ok=True); open(os.path.join(t, p), "w").close()
    os.makedirs(os.path.join(t, "r"))
    reg = ('Windows Registry Editor Version 5.00\n\n[HKEY_LOCAL_MACHINE\\Software\\Microsoft\\Windows NT\\CurrentVersion\\Fonts]\n'
           '"A (TrueType)"="a.fon"\n"B"="b.TTF"\n"Y (TrueType)"="Z:\\\\usr\\\\share\\\\fonts\\\\opentype\\\\x\\\\y.otf"\n'
           '"Gone"="gone.fon"\n"Far"="Z:\\\\usr\\\\share\\\\fonts\\\\truetype\\\\far.ttf"\n"Odd"="C:\\\\windows\\\\fonts\\\\odd.ttf"\n'
           '[HKEY_LOCAL_MACHINE\\System\\CurrentControlSet\\Hardware Profiles\\Current\\Software\\Fonts]\n"FONTS.FON"="a.fon"\n"LogPixels"=dword:00000060\n"OEMFONT.FON"="oem.fon"\n'
           '[HKEY_LOCAL_MACHINE\\Software\\Microsoft\\Windows NT\\CurrentVersion\\FontSubstitutes]\n"Arial CE,238"="Arial,238"\n"Sub"="subst.ttf"\n'
           '[HKEY_CURRENT_USER\\Software\\Wine\\Fonts]\n"Ignored"="nowhere.ttf"\n')
    open(os.path.join(t, "r", "1.reg"), "wb").write(b"\xff\xfe" + reg.encode("utf-16-le"))
    # reference: wine {a.fon, b.ttf, missing.fon}, host {opentype/x/y.otf (+ fontconfig text after the colon), truetype/far.ttf}
    open(os.path.join(t, "fc"), "w").write("/usr/share/fonts/opentype/x/y.otf: Y:style=Regular\n/usr/share/fonts/truetype/far.ttf: Far\n/usr/share/fonts/opentype/x/y.otf: Y:style=Bold\n")
    open(os.path.join(t, "wl"), "w").write("# comment\n/usr/share/wine/fonts/A.fon\n/usr/share/wine/fonts/b.ttf\n/usr/share/wine/fonts/missing.fon\n")
    n, errs, skipped = check(os.path.join(t, "w"), os.path.join(t, "h"), os.path.join(t, "r"), os.path.join(t, "fc"), os.path.join(t, "wl"))
    got = sorted(k + ":" + x.split(" ")[0] + " " + x.split(" ")[1] for k, x in errs)
    want = sorted(["EXTRA:host extra.ttf", "LACKS:host truetype/far.ttf", "LACKS:wine missing.fon", "NOPLACE:c:/windows/fonts/odd.ttf (named"])
    assert got == want, (got, want)
    sk = sorted((w, r) for w, r, _ in skipped)
    assert sk == [("wine", "gone.fon"), ("wine", "oem.fon"), ("wine", "subst.ttf")], sk   # named, not in the reference listing
    assert n == 8, n   # distinct files: a.fon (named twice) b.ttf y.otf gone far odd oem subst
    print("fontnames selftest ok: %d named, %d errors, %d skipped" % (n, len(errs), len(skipped)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
