"""Stage script of the lift/proof/sailmodel root: the patched Sail model directory (no IR).  argv: SAIL_X86_TAR_GZ PATCHES_DIR EXTRA_FILE... ; /in/1 = lift/proof/sail_pins.txt.
The tar's uncompressed sha256 is checked against the pin `sail-x86-from-acl2`; the composition is sail_patches/apply.py (the script farm/sail_build.sh runs too)."""
import gzip, hashlib, os, subprocess, sys, tarfile

pin = [l.split() for l in open("/in/1") if l.split()[:1] == ["sail-x86-from-acl2"]]
if len(pin) != 1:
    sys.exit("model: sail_pins.txt has no single sail-x86-from-acl2 row")
tar_gz, pat, extra = sys.argv[1], sys.argv[2], sys.argv[3:]
raw = gzip.open(tar_gz).read()
if hashlib.sha256(raw).hexdigest() != pin[0][3]:
    sys.exit("model: sha256 of the uncompressed tar differs from the pin")
open("/scratch/m.tar", "wb").write(raw)
with tarfile.open("/scratch/m.tar") as tf:
    tf.extractall("/scratch/src", filter="data")
subprocess.run([sys.executable, "-I", os.path.join(pat, "apply.py"), "/scratch/src/sail-x86-from-acl2", pat, "/out/model"] + extra, check=True)
