#!/usr/bin/env python3 -I
"""Gate of the declared font set (L5 declared machine): declared files == the files Wine 9.0's mapper gives the platform's static faces.

Inputs (all pinned by sha256 in fnv-nx-build tools/fonts-wine9/SHA256SUMS; this script prints the hashes it used):
  faces.txt     stdout of platform_faces.py: the faces of the static requests (calls through font_faces.h in the compiled objects)
  wine.inf.in   Wine 9.0 loader/wine.inf.in: [Fonts] FontSubStr lines = FontSubstitutes installed at first start
  font.c        Wine 9.0 dlls/win32u/font.c: default_{serif,fixed,sans}_list (the fallback names of enum_fallbacks, which gdifont enum_fallback must
                equal) and the nls_update_font_list row of the declared machine (ANSI 1252, OEM 437: `shelldlg` = MS Shell Dlg's substitute)
  30-metric-aliases.conf  fontconfig-config 2.15.0-1.1ubuntu2 (libwine Depends libfontconfig1): when a family has no font, win32u freetype.c
                (FcConfigSubstitute) gets the <accept> families of that name; the first present in the closure is used
  fontdirs      the font files of the pinned package closure: fonts-wine (Depends/Recommends of libwine 9.0~repack-4build3 that ship fonts:
                fonts-wine, fonts-liberation), each family read by fc-scan (FreeType, the engine of gdifont.cpp)
Rule: face -> FontSubstitutes target if one is installed (wine.inf.in, or the shelldlg row of font.c for `MS Shell Dlg`), repeated until a name
with no substitute; the family of that name (case-insensitive, as win32u's family_name_tree) -> every file of that family (family-wide weights).
A face with no family in the closure is `no file` and the gate is red. Declared == mapped, 0 extra, 0 missing.
Literal bypass: no string constant in a platform object other than font_faces.cpp.o (read from the object's data sections, as bytes or UTF-16LE)
equals, case-insensitively, a name of the full map: the faces, every FontSubstitutes key and target, the three default lists, the shelldlg names.
Usage: fontgate.py <faces.txt> <wine.inf.in> <font.c> <30-metric-aliases.conf> <declared-dir> <fontdir>... -- <platform .o>...
Assumptions: Win32/Wine 9.0 as documented (ASM-003); the compiler is correct (ASM-001).
"""
import hashlib, os, re, subprocess, sys, tempfile

a = sys.argv[1:]
cut = a.index("--") if "--" in a else len(a)
faces_f, inf, fontc, aliases, declared = a[:5]
fontdirs, objs = a[5:cut], a[cut + 1:]
import xml.etree.ElementTree as ET
acc = {}
for al in ET.parse(aliases).getroot().iter("alias"):
    if al.get("binding") == "same" and al.find("accept") is not None:
        acc.setdefault(al.find("family").text.lower(), []).extend(x.text for x in al.findall("accept/family"))
subs = {}
for k, v in re.findall(r'^HKLM,%FontSubStr%,"([^"]*)",,"([^"]*)"', open(inf).read(), re.M):
    subs[k.lower()] = v.split(",")[0] if "," not in k else None
subs = {k: v for k, v in subs.items() if v}
src = open(fontc).read()
dl = {n: re.findall(r'"([^"]+)"', body) for n, body in re.findall(r"default_(\w+)_list\[3\]\s*=\s*\{(.*?)\}", src, re.S)}
row = re.search(r"/\* Latin 1 \(United States\) \*/\s*\{\s*1252,\s*437,.*?\"([^\"]+)\",\"([^\"]+)\"\s*\}", src, re.S)
shell = row.group(1)
subs["ms shell dlg"] = shell
fam = {}
for d in fontdirs:
    out = subprocess.run(["fc-scan", "--format", "%{file}\t%{family}\n", d], capture_output=True, text=True, check=True).stdout
    for l in out.splitlines():
        f, n = l.split("\t", 1)
        for x in n.split(","):
            fam.setdefault(x.lower(), set()).add(os.path.basename(f))
faces = [l.rstrip("\n") for l in open(faces_f) if l.strip()]
want, nofile, rows = set(), [], []
for f in faces:
    n, seen = f, []
    while n.lower() in subs and n.lower() not in seen:
        seen.append(n.lower()); n = subs[n.lower()]
    fs = sorted(fam.get(n.lower(), ()))
    for c in ([] if fs else acc.get(n.lower(), [])):  # fontconfig metric alias: win32u freetype.c asks FcConfigSubstitute for the family
        if fam.get(c.lower()):
            fs = sorted(fam[c.lower()]); n = n + " -> " + c; break
    rows.append((f, n, fs))
    want |= set(fs)
    if not fs: nofile.append(f)
# Wine's enum_fallbacks/set_default_family (font.c:997-1030): each default list is tried in order and the first name whose family exists is the
# one reached; the later names are unreached by construction. A list with no present name stays red (its faces keep `no file`).
unreached = []
fmap = {f: fs for f, n, fs in rows}
for lname, lst in sorted(dl.items()):
    pres = [x for x in lst if fmap.get(x)]
    if not pres:
        continue
    for x in lst[lst.index(pres[0]) + 1:]:
        if x in fmap:
            unreached.append((x, lname, pres[0]))
            for y in fmap[x]:
                if not any(y in fmap[z] for z in fmap if z not in {u[0] for u in unreached}):
                    want.discard(y)
            if not fmap[x] and x in nofile: nofile.remove(x)
have = {x for x in os.listdir(declared) if not x.startswith(("SHA256", "README"))}
extra, missing = sorted(have - want), sorted(want - have)
names = {x.lower() for x in faces} | set(subs) | set(subs.values()) | {x.lower() for v in dl.values() for x in v} | {shell.lower()}
names |= {x.lower().split(",")[0] for x in subs}
names |= {k for k in acc if k in names} | {x.lower() for k, v in acc.items() if k in names for x in v}
lit = []
for o in objs:
    if os.path.basename(o) == "font_faces.cpp.o": continue
    secs = [l.split()[1] for l in subprocess.run(["objdump", "-h", o], capture_output=True, text=True, check=True).stdout.splitlines()
            if re.match(r"\s+\d+\s+\.(rodata|data)", l)]
    for s in secs:
        with tempfile.NamedTemporaryFile() as t:
            if subprocess.run(["objcopy", "--dump-section", s + "=" + t.name, o, "/dev/null"], capture_output=True).returncode: continue
            b = open(t.name, "rb").read()
        bl = b.lower()
        for nm in names:
            for enc, w in (("utf-8", 1), ("utf-16-le", 2)):
                pat = nm.lower().encode(enc) + b"\0" * w
                # a constant is the whole NUL-terminated string: it starts at the section start or right after a terminator; ASCII case folded
                k = bl.find(pat)
                while k >= 0:
                    if k % w == 0 and (k == 0 or b[k - w:k] == b"\0" * w):
                        lit.append((os.path.basename(o), s, nm))
                    k = bl.find(pat, k + 1)
for f, n, fs in rows: print("map\t%s\t%s\t%s" % (f, n, ",".join(fs) or "NO FILE"))
for n in sorted(have & want):
    d = next(d for d in [declared] if os.path.exists(os.path.join(d, n)))
    print("sha256\t%s\t%s" % (hashlib.sha256(open(os.path.join(d, n), "rb").read()).hexdigest(), n))
print("faces %d; mapped files %d; declared files %d; extra %d; missing %d; faces without a file %d; unreached %d; face literals outside font_faces %d; fallback names %s" % (
    len(faces), len(want), len(have), len(extra), len(missing), len(nofile), len(unreached), len(lit), sorted(x for v in dl.values() for x in v) and "ok"), file=sys.stderr)
for u in unreached: print("unreached: %s (default_%s_list: %s is present first, font.c:997-1030)" % (u[0], u[1], u[2]), file=sys.stderr)
for n in nofile: print("no file:", n, file=sys.stderr)
for n in extra: print("extra:", n, file=sys.stderr)
for n in missing: print("missing:", n, file=sys.stderr)
for l in lit: print("literal:", *l, file=sys.stderr)
sys.exit(1 if nofile or extra or missing or lit else 0)
