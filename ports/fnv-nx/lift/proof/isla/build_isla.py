"""Stage script of the lift/proof/isla root: builds isla-execute-function from the pinned sail-mirror inputs, offline, in the step's private dirs.
Reads every pin from lift/proof/sail_pins.txt (input /in/1); nothing is copied from it.  argv: ISLA_TAR_GZ VENDOR_TAR_GZ Z3_WHEEL (the mirror files, /in/2..4)."""
import gzip, hashlib, os, shutil, subprocess, sys, zipfile, tarfile

def die(m):
    sys.exit("isla: " + m)

def pins(path):
    d = {}
    for l in open(path):
        if l.strip() and not l.startswith("#"):
            c = l.split()
            d[c[0]] = c
    return d

def sha(b):
    return hashlib.sha256(b).hexdigest()

P = pins("/in/1")
tar_gz, vendor_gz, wheel = sys.argv[1:4]
W = "/scratch"

def unpack(gz, name, want):
    raw = gzip.open(gz).read()
    if sha(raw) != want:
        die("%s: sha256 of the uncompressed tar differs from the pin" % name)
    t = os.path.join(W, name + ".tar")
    open(t, "wb").write(raw)
    return t

isla_t = unpack(tar_gz, "isla", P["isla"][3])
vend_t = unpack(vendor_gz, "isla-vendor", P["isla-vendor"][3])
whl = open(wheel, "rb").read()
if sha(whl) != P["z3-solver"][3]:
    die("z3 wheel sha256 differs from the pin")
for t in (isla_t, vend_t):
    with tarfile.open(t) as tf:
        tf.extractall(W, filter="data")
zipfile.ZipFile(wheel).extractall(os.path.join(W, "z3whl"))
src = os.path.join(W, "isla")
if sha(open(os.path.join(src, "Cargo.lock"), "rb").read()) != P["isla-cargo-lock"][3]:
    die("Cargo.lock differs from the pin isla-cargo-lock")
z3 = os.path.join(W, "z3whl", "z3")
vend = os.path.join(W, "vendor")
if not os.path.isdir(vend):
    die("the vendor tar has no vendor/ directory")
env = dict(os.environ, CARGO_HOME=os.path.join(W, "cargo-home"), CARGO_TARGET_DIR=os.path.join(W, "target"),
           LIBRARY_PATH=z3 + "/lib", LD_LIBRARY_PATH=z3 + "/lib", C_INCLUDE_PATH=z3 + "/include",
           RUSTFLAGS="--remap-path-prefix=%s=/isla --remap-path-prefix=%s=/vendor" % (src, vend),
           CARGO_BUILD_JOBS=os.environ.get("JOBS", "1"))
cfg = ["--config", 'source.crates-io.replace-with="vendored"', "--config", 'source.vendored.directory="%s"' % vend, "--offline"]
r = subprocess.run(["cargo", "build", "--release", "--locked"] + cfg, cwd=src, env=env)
if r.returncode:
    die("cargo build failed (%d)" % r.returncode)
shutil.copyfile(os.path.join(W, "target", "release", "isla-execute-function"), "/out/isla-execute-function")
os.chmod("/out/isla-execute-function", 0o755)
shutil.copytree(os.path.join(z3, "lib"), "/out/z3lib")
