#!/usr/bin/env python3
"""apply.py [--stock] [--bump 0|1] SRC PATCHES OUT EXTRA...: the ONE place that composes the patched Sail model (farm/sail_build.sh and the recipe root lift/proof/sailmodel both run it).
SRC = the sail-x86-from-acl2 tree (holds model/ and test-generation-patches/); PATCHES = lift/proof/sail_patches; EXTRA = the files spliced / compiled after the patches
(lift/proof/x86_fetch.sail is spliced; every EXTRA is copied to local/).  OUT is written (it must not exist):
  OUT/model/*.sail   copy of SRC/model/*.sail          OUT/tgp/*.sail    copy of SRC/test-generation-patches/*.sail
  OUT/local/         the patch splices in order, the generated cpuid_dispatch.sail (BUMP=1) and EXTRA
  OUT/SPLICES        one `-splice` file per line, relative to OUT/model, in isla-sail argument order (tgp sorted by name, then the patches, then x86_fetch.sail)
No IR is built.  Default BUMP=1 (the pinned IR); BUMP=0 is the superseded patch list; --stock applies no patch (the unpatched model, plus x86_fetch.sail as before)."""
import os, shutil, subprocess, sys

a = sys.argv[1:]
stock, bump = False, 1
while a and a[0].startswith("--"):
    if a[0] == "--stock":
        stock = True; a = a[1:]
    elif a[0] == "--bump":
        bump = int(a[1]); a = a[2:]
    else:
        sys.exit("apply: unknown option " + a[0])
if len(a) < 3:
    sys.exit(__doc__)
src, pat, out, extra = a[0], a[1], a[2], a[3:]
model, tgpd = os.path.join(src, "model"), os.path.join(src, "test-generation-patches")
if os.path.exists(out):
    sys.exit("apply: OUT exists: " + out)
local = os.path.join(out, "local")
os.makedirs(local)
for d, s in (("model", model), ("tgp", tgpd)):
    os.makedirs(os.path.join(out, d))
    for n in sorted(os.listdir(s)):
        if n.endswith(".sail"):
            shutil.copyfile(os.path.join(s, n), os.path.join(out, d, n))
P = lambda n: os.path.join(pat, n)
B = lambda n: os.path.join(pat, "bump", n)
if stock:
    patches = []
elif bump == 1:
    gen = os.path.join(out, "gen")
    os.makedirs(gen)
    run = lambda *c: subprocess.run([sys.executable, "-I"] + list(c), check=True)
    run(B("cpuid_dispatch.py"), model, os.path.join(gen, "cpuid_dispatch0.sail"))
    run(B("bsf_bsr_dispatch.py"), os.path.join(gen, "cpuid_dispatch0.sail"), os.path.join(gen, "cpuid_dispatch.sail"))
    patches = [P("rotate_count.sail"), B("ror_cf.sail"), P("bt_bitnumber.sail"), P("segment_bases.sail"), B("cmps.sail"), B("rol_count0.sail"),
               B("sar_bigcount.sail"), B("cpuid.sail"), B("bit_bsf_bsr.sail"), os.path.join(gen, "cpuid_dispatch.sail")]
else:
    patches = [P("rotate_count.sail"), P("ror_cf.sail"), P("bt_bitnumber.sail"), P("segment_bases.sail")]
for f in patches + extra:
    shutil.copyfile(f, os.path.join(local, os.path.basename(f)))
if patches and bump == 1 and not stock:
    shutil.rmtree(os.path.join(out, "gen"))
spl = ["../tgp/" + n for n in sorted(os.listdir(os.path.join(out, "tgp")))]
spl += ["../local/" + os.path.basename(f) for f in patches + [e for e in extra if os.path.basename(e) == "x86_fetch.sail"]]
open(os.path.join(out, "SPLICES"), "w").write("\n".join(spl) + "\n")
