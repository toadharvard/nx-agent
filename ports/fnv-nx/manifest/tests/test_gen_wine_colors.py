#!/usr/bin/env python3
"""tests of manifest/gen_wine_colors.py on a miniature Wine tree"""
import os, subprocess, sys, tempfile
GEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gen_wine_colors.py")
SYS = """static struct sysparam_rgb_entry system_colors[] =
{
#define RGB_ENTRY(name,val,reg) { { get_rgb_entry }, (val) }
    RGB_ENTRY( COLOR_SCROLLBAR, RGB(212, 208, 200), "Scrollbar" ),
    RGB_ENTRY( COLOR_BACKGROUND, RGB(58, 110, 165), "Background" )
#undef RGB_ENTRY
};
"""
IDS = "#define COLOR_SCROLLBAR\t0\n#define COLOR_BACKGROUND\t1\n"
def dib(n1=2, n4=16, n8=256):
    r = lambda n: "".join("{ 0x%02x, 0x00, 0x80 }, " % (i % 256) for i in range(n))
    return ("const RGBQUAD *get_default_color_table( int bpp )\n{\n static const RGBQUAD table_1[2] = { %s };\n static const RGBQUAD table_4[16] = { %s };\n"
            " static const RGBQUAD table_8[256] = { %s };\n}\n" % (r(n1), r(n4), r(n8)))
def run(sysc=SYS, ids=IDS, d_=None):
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(d + "/dlls/win32u"); os.makedirs(d + "/include")
        open(d + "/dlls/win32u/sysparams.c", "w").write(sysc)
        open(d + "/dlls/win32u/dib.c", "w").write(d_ or dib())
        open(d + "/include/winuser.h", "w").write(ids)
        r = subprocess.run([sys.executable, "-I", GEN, d, d + "/o.inc"], capture_output=True, text=True)
        return r, (open(d + "/o.inc").read() if r.returncode == 0 else "")
r, o = run(); assert r.returncode == 0, r.stderr
assert "0xC8D0D4,   /* COLOR_SCROLLBAR" in o and "0xA56E3A" in o and "k_pal8[256]" in o and "0x800001" in o, o
r, _ = run(ids="#define COLOR_SCROLLBAR\t1\n#define COLOR_BACKGROUND\t0\n"); assert r.returncode == 1 and "row 0" in r.stderr
r, _ = run(d_=dib(n4=15)); assert r.returncode == 1 and "table_4" in r.stderr
print("ok")
