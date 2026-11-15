#!/usr/bin/env python3
"""Red cases of gen_units.py:  test_gen_units.py TREE DEB   (the pinned FreeType mirror and the package's debian/). Each case edits a copy of DEB and the
generator must exit non-zero with its own message; the unedited package must pass."""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FAILED = []


def run(tree, deb):
    with tempfile.TemporaryDirectory() as out:
        r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "gen_units.py"), tree, deb, out], capture_output=True, text=True)
    return r.returncode, r.stderr


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else " " + str(info)))
    if not ok:
        FAILED.append(name)


def variant(deb, fn):
    with tempfile.TemporaryDirectory() as t:
        d = os.path.join(t, "debian")
        shutil.copytree(deb, d)
        fn(d)
        return run(TREE, d)


def edit(path, f):
    with open(path) as fh:
        s = fh.read()
    with open(path, "w") as fh:
        fh.write(f(s))


TREE, DEB = sys.argv[1], sys.argv[2]
rc, err = run(TREE, DEB)
check("the package as pinned passes", rc == 0, err)
rc, err = variant(DEB, lambda d: edit(os.path.join(d, "patches/enable-gxvalid-otvalid.patch"), lambda s: s.replace("+AUX_MODULES += gxvalid", "+AUX_MODULES += gxvalid # x")))
check("a modules.cfg change that is not an uncommented module line is red", rc != 0 and "cannot read" in err, err)
rc, err = variant(DEB, lambda d: edit(os.path.join(d, "patches/ftoption.patch"), lambda s: s.replace("+#define PCF_CONFIG_OPTION_LONG_FAMILY_NAMES", "+#define PCF_CONFIG_OPTION_LONG_FAMILY_NAMES 1")))
check("an ftoption.h change that is not an enabled define is red", rc != 0 and "cannot read" in err, err)
rc, err = variant(DEB, lambda d: edit(os.path.join(d, "patches/ftoption.patch"), lambda s: s.replace("-/* #define FT_CONFIG_OPTION_SUBPIXEL_RENDERING */", "-/* #define FT_CONFIG_OPTION_SUBPIXEL_RENDERINGX */")))
check("a patch that does not apply is red", rc != 0 and "does not match" in err, err)
rc, err = variant(DEB, lambda d: edit(os.path.join(d, "patches/series"), lambda s: s + "ftoption.patch\n"))
check("a patch applied twice does not apply (red)", rc != 0, err)
print("FAILED: %d" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
