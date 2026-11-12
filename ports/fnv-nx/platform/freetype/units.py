#!/usr/bin/env python3
"""FreeType's own unit set, derived by FreeType's own build rules from the pinned tree (no hand list).
    python3 -I units.py TREE PATCHDIR [--check UNITS_FILE]   TREE = unpacked mirror (fnv-nx-build mirror/freetype-src), PATCHDIR = tracked patches (applied in
    file-name order to a temporary copy; the same `patch -p1 -F0` the recipe's patch stage runs). Prints `<path>` lines, sorted; with --check the printed list must equal the file.
Rule (docs/INSTALL.ANY, builds/freetype.mk, src/*/rules.mk): the objects of a build are
  - the base components of builds/freetype.mk: src/base/ftsystem.c, src/base/ftdebug.c, src/base/ftinit.c (FTSYS_SRC and FTDEBUG_SRC default to src/base);
  - the base layer: BASE_SRC_S of src/base/rules.mk (ftbase.c) and, per BASE_EXTENSIONS of modules.cfg, src/base/<name>;
  - per module NAME of modules.cfg (FONT_MODULES, HINTING_MODULES, RASTER_MODULES, AUX_MODULES): the single aggregator <X>_SRC_S of src/NAME/rules.mk (a module without one has one source <X>_DRV_SRC, its own object).
modules.cfg is read after the tracked patches (they enable the modules of the reference package)."""
import os
import re
import shutil
import subprocess
import sys
import tempfile


def derive(tree, patchdir):
    with tempfile.TemporaryDirectory() as tmp:
        work = os.path.join(tmp, "t")
        shutil.copytree(tree, work, ignore=shutil.ignore_patterns(".git"))
        for p in sorted(os.listdir(patchdir)):
            if p.endswith(".patch"):
                with open(os.path.join(patchdir, p), "rb") as f:
                    subprocess.run(["patch", "-d", work, "-p1", "-F0", "--no-backup-if-mismatch", "-s"], stdin=f, check=True)
        cfg = open(os.path.join(work, "modules.cfg")).read()
        mods, ext = [], []
        for line in cfg.split("\n"):
            m = re.match(r"^(FONT|HINTING|RASTER|AUX)_MODULES\s*\+=\s*(\S+)\s*$", line)
            if m:
                mods.append(m[2])
            m = re.match(r"^BASE_EXTENSIONS\s*\+=\s*(\S+)\s*$", line)
            if m:
                ext.append(m[1])
        out = ["src/base/ftsystem.c", "src/base/ftdebug.c", "src/base/ftinit.c"]
        base = open(os.path.join(work, "src/base/rules.mk")).read()
        out += ["src/base/" + x for x in re.findall(r"^BASE_SRC_S\s*:=\s*\$\(BASE_DIR\)/(\S+\.c)\s*$", base, re.M)]
        out += ["src/base/" + e for e in ext]
        for name in mods:
            rules = open(os.path.join(work, "src", name, "rules.mk")).read()
            agg = re.findall(r"^\w+_SRC_S\s*:=\s*\$\(\w+_DIR\)/(\S+\.c)\s*$", rules, re.M)
            if not agg:   # a single-file module: its one source is its object (ftgzip.c, ftlzw.c, ftbzip2.c)
                agg = re.findall(r"^\w+_DRV_SRC\s*:=\s*\$\(\w+_DIR\)/(\S+\.c)\s*$", rules, re.M)
            if len(agg) != 1:
                raise SystemExit("units.py: src/%s/rules.mk names %d aggregators" % (name, len(agg)))
            out.append("src/%s/%s" % (name, agg[0]))
        for u in out:
            if not os.path.isfile(os.path.join(work, u)):
                raise SystemExit("units.py: %s is not in the tree" % u)
        return sorted(set(out))


def main(a):
    chk = None
    if "--check" in a:
        i = a.index("--check")
        chk = a[i + 1]
        del a[i:i + 2]
    units = derive(a[0], a[1])
    if chk is not None:
        want = open(chk).read().split()
        if want != units:
            print("units.py: %s differs from the derived list (%d vs %d)" % (chk, len(want), len(units)))
            return 1
        return 0
    print("\n".join(units))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
