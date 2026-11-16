#!/usr/bin/env python3
"""Selftest of gen_fontmachine.py on synthetic input: the accepted case, determinism (two runs equal byte for byte), and every refusal.

  check_gen_fontmachine.py            (run with python3 -I)
"""
import os, struct, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GEN = os.path.join(HERE, "gen_fontmachine.py")
INF = '[Fonts]\nHKLM,%FontSubStr%,"Helv",,"MS Sans Serif"\nHKLM,%FontSubStr%,"Arial CE,238",,"Arial,238"\n'
FONTC = '''    /* Latin 1 (United States) */
    { 1252, 437, "vgaoem.fon", "vgafix.fon", "vgasys.fon",
      "coure.fon", "serife.fon", "smalle.fon", "sserife.fon", "sseriff.fon",
      "Tahoma","Times New Roman"
    },
'''
CONF = '<fontconfig><alias binding="same"><family>Arial</family><accept><family>Arimo</family><family>Liberation Sans</family></accept></alias>' \
       '<alias binding="same"><family>Absent</family><accept><family>Nothing</family></accept></alias></fontconfig>'
fails = []


def run(td, inf=INF, fontc=FONTC, conf=CONF):
    for n, t in (("w.inf", inf), ("f.c", fontc), ("a.conf", conf)):
        open(os.path.join(td, n), "w").write(t)
    o = os.path.join(td, "o.inc")
    if os.path.exists(o):
        os.remove(o)
    r = subprocess.run([sys.executable, "-I", GEN, os.path.join(td, "w.inf"), os.path.join(td, "f.c"), os.path.join(td, "a.conf"), os.path.join(td, "fonts"), o],
                       capture_output=True, text=True)
    return r.returncode, r.stderr, (open(o).read() if os.path.exists(o) else None)


def want(name, ok):
    if not ok:
        fails.append(name)


def sfnt(family):   # a minimal sfnt whose `name` table holds one nameID 1 record (platform 3, UTF-16BE): the family is read from the file's own bytes
    raw = family.encode("utf-16-be")
    name = struct.pack(">HHH", 0, 1, 18) + struct.pack(">6H", 3, 1, 0x409, 1, len(raw), 0) + raw
    return struct.pack(">IHHHH", 0x00010000, 1, 16, 0, 0) + struct.pack(">4sIII", b"name", 0, 28, len(name)) + name


with tempfile.TemporaryDirectory() as td:
    os.mkdir(os.path.join(td, "fonts"))
    rc, e, _ = run(td)
    want("refuses empty fonts dir", rc != 0 and "no font file" in e)
    FAM = "Liberation Sans"
    open(os.path.join(td, "fonts", "x.ttf"), "wb").write(sfnt(FAM))
    conf = CONF
    rc, e, a = run(td, conf=conf)
    want("accepts", rc == 0 and a and "kAcp = 1252" in a and '{ "MS Shell Dlg", "Tahoma" }' in a and '{ "Arial CE,238", "Arial,238" }' in a
         and '{ "MS Sans Serif", "sserife.fon" }' in a and '{ "FONTS.FON", "vgasys.fon" }' in a)
    want("replacement only for a family with a file", a is not None and ('{ "Arial", "%s" }' % FAM) in a and "Absent" not in a)
    rc2, _, b = run(td, conf=conf)
    want("deterministic", a == b)
    rc, e, _ = run(td, fontc="/* none */", conf=conf)
    want("refuses absent row", rc != 0 and "absent" in e)
    rc, e, _ = run(td, fontc=FONTC.replace('"sseriff.fon",', ""), conf=conf)
    want("refuses short row", rc != 0 and "9 string fields" in e)
    rc, e, _ = run(td, inf="[Fonts]\n", conf=conf)
    want("refuses no substitutes", rc != 0 and "no FontSubstitutes" in e)
if fails:
    print("check_gen_fontmachine FAILED:", ", ".join(fails)); sys.exit(1)
print("check_gen_fontmachine: ok")
