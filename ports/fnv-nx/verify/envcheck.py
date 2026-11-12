#!/usr/bin/env python3
"""envcheck: the declared, reproducible environment of the L1 verdict (verify/env.json, "fnvnx-env/1"). Run as `python3 -I`.
    envcheck.py check [--env F] [--json OUT]   every item of F against this machine; one line per mismatch `env: <item>: want X,
                                               have Y`, then `envcheck: OK` | `envcheck: MISMATCH (n)`; exit 0/2
    envcheck.py id [--env F]                   sha256 of the canonical env.json (the verdict's env_id)
    envcheck.py snapshot -o F --from-code      regenerate F from THIS machine by the rule below; only on a machine set up by
                                               farm/setup_verdict_env.sh, then commit F
check also requires, for the distribution compiler (no external) of every admitted product root, that its plugin headers (`-print-file-name=plugin`, the
gcc-N-plugin-dev package; objstub builds its terminator plugin against them) belong to a package listed in env.json (`plugin headers of <driver>` mismatch).
Rule (packages are DERIVED, never listed by hand): a package is in the environment iff dpkg says it owns a file that a verify tool
or test executes or reads as data: (1) each of PROGRAMS (resolved path + loader closure); (2) python's loader closure (interpreter,
lib-dynload, PY_MODULES); (3) UNIT_CC through unitstage.gcc_set(): driver, `as`, every ELF file of its trees + closure, and EVERY
FILE under its trees (the unit stage ingests them whole: /usr/include, GCC install dir with plugin headers, libexec); the host
gcc's HOST_GCC_PROGS/HOST_GCC_FILES and every file of its plugin directory (HOST_GCC_PLUGIN_DIRS: the plugin headers of the host-root compiler); (4) the renderer's host libraries: RENDER_LIBS (loader cache names, with their loader closure) and RENDER_FILES. The trees also get a listing digest (an extra package adding files there is a mismatch).
A file without a dpkg owner outside python's prefix is an error. Programs and who executes them: strace opentrace.py; busybox
tests/test_graph,test_sandbox; gcc,as,ld test_sandbox,test_sdcheck; aarch64-linux-gnu-g++ test_unitstage (unitstage.py);
aarch64-linux-gnu-nm,readelf icount.py,test_unitstage; aarch64-linux-gnu-as,ld,objcopy test_icount; ar,clang test_coffnorm;
git verdict.py,l1.py,test_gates; ldd,true test_runtime; g++,llvm-config-18 build a64access.cc (objstub.py, test_a64access).
The a64access build set is derived the same way: every header `g++ -M` reports for a64access.cc under `llvm-config-18 --cxxflags`,
and every library file `llvm-config-18 --libfiles` names for A64ACCESS's components (plus cc1plus, libstdc++.so above). The clang set (clang-18 plugins such as vtnames; clang -m32 compiles) is derived the same way: the headers under `llvm-config-18 --includedir` that a plugin includes (CLANG_API) with the whole clang/ tree they sit in, the libraries of CLANG_LIBS under `--libdir`, and every header `clang -m32 -M` reports for a C probe including stdint.h and stdlib.h (gnu/stubs-32.h: libc6-dev-i386). Python modules (version in python.modules, installed-files sha256 in python.installed_files_sha256, see wheel_digest; wheel file sha256 in python.wheel_sha256): capstone, unicorn (icount.py, test_icount, test_runtime); pyelftools (stage tools of contract/: objfacts, perf, check_mirror); pefile (platform/manifest/regsites.py); numpy (lift/test/instest.py packer); z3-solver, keystone-engine, gmpy2 (lift/proof, lift/bench, lift/test)."""
import argparse
import csv
import ctypes
import hashlib
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import sysconfig
from importlib import metadata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
FORMAT = "fnvnx-env/1"
PROGRAMS = ["strace", "busybox", "gcc", "aarch64-linux-gnu-g++", "aarch64-linux-gnu-nm", "aarch64-linux-gnu-readelf",
            "aarch64-linux-gnu-as", "aarch64-linux-gnu-ld", "aarch64-linux-gnu-objcopy", "ar", "as", "ld", "clang", "git", "ldd", "true", "g++", "llvm-config-18", "shfmt"]
UNIT_CC = "aarch64-linux-gnu-g++"  # the unit stage ingests its trees whole
HOST_GCC_PROGS = ["cc1", "cc1plus", "collect2"]  # gcc -print-prog-name=: what the host gcc runs for the static probe / -fsyntax-only
HOST_GCC_FILES = ["libc.a", "crt1.o", "libgcc.a", "libgcc_eh.a", "libstdc++.so"]  # gcc -print-file-name=: what `gcc -static` links
A64ACCESS = (os.path.join(HERE, "a64access.cc"), ["aarch64", "mc", "mcdisassembler", "support"])  # source, llvm-config components
CLANG_API = ["clang/AST/RecursiveASTVisitor.h", "clang/Frontend/FrontendPluginRegistry.h", "clang/Basic/Version.h"]  # the plugin API vtnames (clang-18 MSVC-ABI facts plugin) includes: headers under `llvm-config-18 --includedir`
CLANG_LIBS = ["libclang-cpp.so.18.1", "libclang.so"]   # under `llvm-config-18 --libdir`: what a clang plugin links / dlopens
M32_PROBE = "#include <stdint.h>\n#include <stdlib.h>\nint main(){return 0;}\n"   # clang -m32 -M: every header a 32-bit compile of the C library reads (gnu/stubs-32.h: libc6-dev-i386)
PY_MODULES = {"capstone": "capstone", "gmpy2": "gmpy2", "keystone-engine": "keystone", "numpy": "numpy", "unicorn": "unicorn",
              "pyelftools": "elftools", "z3-solver": "z3", "pefile": "pefile"}   # distribution -> import name
RENDER_LIBS = ["libEGL.so.1", "libEGL_mesa.so.0"]   # ldconfig names: the EGL loader the published libnxgl4.so links, and the Mesa vendor it dispatches to (NXGL4_HOST.md section 2); their loader closure (mesa-libgallium, libllvm, ...) is derived
RENDER_FILES = ["/usr/share/glvnd/egl_vendor.d/50_mesa.json"]   # read by libEGL.so.1 to find that vendor
def HOST_GCC_PLUGIN_DIRS(gcc):
    """Every file of the plugin directory of compiler GCC (`gcc -print-file-name=plugin`: the gcc-N-plugin-dev package of a distro compiler)."""
    d = subprocess.run([gcc, "-print-file-name=plugin"], capture_output=True, text=True).stdout.strip()
    return [os.path.join(r, f) for r, _, fs in os.walk(d) for f in fs] if os.path.isdir(d) else []

KERNEL = ["unshare mount+pid+net+uts+ipc namespaces as root", "seccomp filter", "ptrace (strace)"]

def env_id(doc):
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def wheel_files(dist):
    """{path: sha256 hex} of the installed files of distribution DIST that its RECORD hashes (no .pyc, no installer-written files), read from
    the disk: a file that differs from its RECORD entry makes the digest differ (the entry is not trusted, the content is hashed)."""
    import base64
    import csv
    d, out = metadata.distribution(dist), {}
    for row in csv.reader(d.read_text("RECORD").splitlines()):
        path, h = row[0], row[1] if len(row) > 1 else ""
        if not h.startswith("sha256=") or path.endswith(".pyc") or "__pycache__" in path.split("/"):
            continue
        f = d.locate_file(path)
        have = hashlib.sha256(open(f, "rb").read()).hexdigest() if os.path.isfile(f) else "missing"
        want = base64.urlsafe_b64decode(h[7:] + "=" * (-len(h[7:]) % 4)).hex()
        out[path] = have if have == want else "differs-from-record:" + have
    return out


def wheel_digest(dist):
    """sha256 over the sorted (path, content hash) lines of wheel_files(DIST): names the installed wheel by its content."""
    return hashlib.sha256("".join("%s %s\n" % kv for kv in sorted(wheel_files(dist).items())).encode()).hexdigest()


def file_owners():
    """{path: {dpkg package names}} from /var/lib/dpkg/info/*.list (arch qualifier dropped); a path owned by several packages has all of them."""
    own = {}
    info = "/var/lib/dpkg/info"
    for f in os.listdir(info):
        if f.endswith(".list"):
            pk = f[:-5].split(":")[0]
            for ln in open(os.path.join(info, f), encoding="utf-8", errors="replace"):
                own.setdefault(ln.rstrip("\n"), set()).add(pk)
    return own

def pinned_set(names):
    """The packages an environment owns: NAMES and their dependency closure."""
    return set(names) | set(dep_closure(names))

def tree_files(d, pinned=None, owners=None):
    """(counted, foreign): the (relative path, size) entries of tree D. A path owned by dpkg packages none of which is in PINNED is `foreign`
    (it comes from the container image, not from the declared packages) and is left out of the digest; unowned paths and paths of a pinned
    package are counted. PINNED None: everything is counted."""
    cnt, frn = [], []
    for r, ds, fs in sorted(os.walk(d)):
        ds.sort()
        for f in sorted(fs + [x for x in ds if os.path.islink(os.path.join(r, x))]):
            p = os.path.join(r, f)
            ent = ("%s %d\n" % (os.path.relpath(p, d), os.lstat(p).st_size))
            o = owners.get(p) if (pinned is not None and owners) else None
            (frn if o and not (o & pinned) else cnt).append((ent, p, o))
    return cnt, frn

def tree_digest(d, pinned=None, owners=None):
    """sha256 of the sorted (relative path, size) listing of the counted files of tree D (see tree_files)."""
    h = hashlib.sha256()
    for ent, _, _ in tree_files(d, pinned, owners)[0]:
        h.update(ent.encode())
    return h.hexdigest()

def _os():
    osr = dict(ln.rstrip("\n").split("=", 1) for ln in open("/etc/os-release") if "=" in ln)
    return {"id": osr.get("ID", "").strip('"'), "version_id": osr.get("VERSION_ID", "").strip('"')}

def _dpkg(pkg):
    p = subprocess.run(["dpkg-query", "-W", "-f", "${Version}", pkg], capture_output=True, text=True)
    return p.stdout or None

def dep_closure(names):
    """{package: installed version}: NAMES and everything their Pre-Depends/Depends need, resolved on THIS machine's dpkg state. A
    dependency clause `a | b` is satisfied by its first alternative that is installed (itself or through Provides); the walk takes
    that package, so the result is exactly the set of installed packages that the declared ones rely on. A name that is not installed
    is left out (check reports it as a missing package). Rule, not a list: this is what dpkg -i of the pins needs to be complete."""
    recs, prov = {}, {}
    for blk in open("/var/lib/dpkg/status", encoding="utf-8", errors="replace").read().split("\n\n"):
        d, k = {}, None
        for ln in blk.split("\n"):
            m = re.match(r"^([A-Za-z0-9-]+):\s?(.*)$", ln)
            if ln[:1] in " \t":
                if k:
                    d[k] += "\n" + ln
            elif m:
                k = m.group(1)
                d[k] = m.group(2)
        if d.get("Package") and d.get("Status", "").endswith(" installed"):
            recs.setdefault(d["Package"], []).append(d)
    for n, rs in recs.items():
        prov.setdefault(n, set()).add(n)
        for r in rs:
            for p in re.split(r",\s*", r.get("Provides", "")):
                if p.split("(")[0].strip():
                    prov.setdefault(p.split("(")[0].strip(), set()).add(n)
    seen, todo = {}, [n for n in names if n in recs]
    while todo:
        n = todo.pop()
        if n in seen:
            continue
        seen[n] = recs[n][0]["Version"]
        for r in recs[n]:
            for f in ("Pre-Depends", "Depends"):
                for c in re.split(r",\s*", r.get(f, "").replace("\n", " ")):
                    alts = [re.sub(r"\s*\(.*?\)", "", a).split(":")[0].strip() for a in c.split("|") if c.strip()]
                    pick = next((sorted(prov[a])[0] for a in alts if a in prov), None)
                    if pick:
                        todo.append(pick)
    return seen

def _owners():
    """{real path: {package}} from dpkg's own file lists (merged-/usr names resolved)."""
    out, d = {}, "/var/lib/dpkg/info"
    for f in os.listdir(d):
        if f.endswith(".list"):
            pkg = f[:-5].removesuffix(":amd64")
            for ln in open(os.path.join(d, f), errors="replace"):
                out.setdefault(os.path.realpath(ln.rstrip("\n")), set()).add(pkg)
    return out

def _children(fn):
    """Run fn() in a forked child; True iff it returns normally."""
    pid = os.fork()
    if pid == 0:
        try:
            fn()
        except BaseException:
            os._exit(1)
        os._exit(0)
    return os.waitpid(pid, 0)[1] == 0

def _unshare():
    libc = ctypes.CDLL(None)
    if libc.unshare(0x00020000 | 0x04000000 | 0x08000000 | 0x20000000 | 0x40000000):
        raise OSError("unshare")

def _seccomp():
    libc = ctypes.CDLL(None)
    buf = ctypes.create_string_buffer(struct.pack("HBBI", 0x06, 0, 0, 0x7FFF0000))  # BPF_RET|BPF_K SECCOMP_RET_ALLOW
    fprog = struct.pack("HxxxxxxQ", 1, ctypes.addressof(buf))
    if libc.prctl(38, 1, 0, 0, 0) or libc.prctl(22, 2, ctypes.c_char_p(fprog)):
        raise OSError("seccomp")

def _ptrace():
    return subprocess.run(["strace", "-e", "trace=none", "true"], capture_output=True).returncode == 0

PROBES = {KERNEL[0]: lambda: os.geteuid() == 0 and _children(_unshare), KERNEL[1]: lambda: _children(_seccomp), KERNEL[2]: _ptrace}

# files the installer writes into .dist-info (not the wheel's content: they name the installer and how it was asked)
INSTALLER_FILES = ("INSTALLER", "REQUESTED", "direct_url.json")

def wheel_files(dist):
    """{path: sha256 hex} of the installed files of distribution DIST that its RECORD hashes, hashed from the DISK (a file that differs
    from its RECORD entry shows as `differs-from-record:<hash>`, a missing one as `missing`): the RECORD entry is the wheel's own hash,
    the content is what is verified. Not content, so not listed: .pyc and __pycache__, the files pip/uv write themselves (INSTALLER,
    REQUESTED, direct_url.json, RECORD) and rows outside the site directory (`../`: console scripts, whose shebang the installer
    rewrites). The digest is the same for pip, uv, --no-compile, --target."""
    import base64
    d, out = metadata.distribution(dist), {}
    for line in d.read_text("RECORD").splitlines():
        row = next(csv.reader([line]), [])
        path, h = row[0], row[1] if len(row) > 1 else ""
        if (not h.startswith("sha256=") or path.endswith(".pyc") or "__pycache__" in path.split("/") or path.startswith("../")
                or path.rsplit("/", 1)[-1] in INSTALLER_FILES + ("RECORD",)):
            continue
        f = d.locate_file(path)
        have = hashlib.sha256(open(f, "rb").read()).hexdigest() if os.path.isfile(f) else "missing"
        want = base64.urlsafe_b64decode(h[7:] + "=" * (-len(h[7:]) % 4)).hex()
        out[path] = have if have == want else "differs-from-record:" + have
    return out

def wheel_digest(dist):
    """sha256 over the sorted (path, content hash) lines of wheel_files(DIST): names the installed wheel by its content."""
    return hashlib.sha256("".join("%s %s\n" % kv for kv in sorted(wheel_files(dist).items())).encode()).hexdigest()

def _root_compilers(repo=None):
    """Driver names of the distribution compilers (no external) of the admitted product roots of the checkout, from admit.json and the recipes."""
    import recipe
    repo = repo or os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
    try:
        roots = recipe.load_admit(repo)
    except (OSError, ValueError):
        return []
    out = set()
    for r, e in roots.items():
        if e.get("class") == "product":
            rec = recipe.load_recipe(repo, roots, r)
            c = rec.get("compiler") if isinstance(rec, dict) else None
            if isinstance(c, dict) and "external" not in c:
                out |= {c[k] for k in ("cc", "cxx") if isinstance(c.get(k), str)}
    return sorted(out)


READROOTS = os.path.join(HERE, "readroots.json")

def read_roots(repo):
    """[(root name, cwd, [system header path read], error or None)] for every root of verify/readroots.json whose cwd exists in REPO:
    `gcc -M` of the root's flags and sources (globs expanded in its cwd); a root whose sources are not all in the checkout is skipped. The headers are the dependencies under
    /usr (what the preprocessor read); an error is the first lines of gcc's diagnostics (a header that is not installed)."""
    import glob
    try:
        roots = json.load(open(READROOTS))["roots"]
    except (OSError, ValueError, KeyError):
        return []
    out = []
    for r in roots:
        cwd = os.path.join(repo, r["cwd"])
        if not os.path.isdir(cwd):
            continue
        srcs = [sorted(glob.glob(os.path.join(cwd, g))) for g in r["sources"]]
        if not all(srcs):   # the root's sources are not in this checkout (its branch has not landed): nothing to read
            continue
        args = r["flags"] + [os.path.relpath(x, cwd) for g in srcs for x in g]
        p = subprocess.run(["gcc", "-M"] + args, cwd=cwd, capture_output=True, text=True)
        if p.returncode != 0:
            out.append((r["name"], cwd, [], p.stderr.strip().splitlines()[0] if p.stderr.strip() else "gcc -M failed"))
            continue
        toks = [t for t in p.stdout.replace("\\\n", " ").split() if not t.endswith(":")]
        out.append((r["name"], cwd, sorted({os.path.realpath(os.path.join(cwd, t)) for t in toks if os.path.realpath(os.path.join(cwd, t)).startswith("/usr/")}), None))
    return out

def read_owner_packages(repo):
    """The packages that own the system headers the read roots read (what snapshot pins): for each header its owners, the first by name
    when several; headers of packages already chosen cost nothing."""
    own, pk = _owners(), set()
    for _, _, hs, _ in read_roots(repo):
        for h in hs:
            o = own.get(h, set())
            if o and not (o & pk):
                pk.add(sorted(o)[0])
    return pk

def check(doc, repo=None):
    """List of mismatch lines (empty = the machine is the declared environment)."""
    bad = []
    def cmp(item, want, have):
        if want != have:
            bad.append("env: %s: want %s, have %s" % (item, want, have))
    cmp("format", FORMAT, doc.get("format"))
    for k, v in dict(_os(), arch=platform.machine(), uid=os.geteuid()).items():
        cmp(("os." + k) if k in doc["os"] else k, doc["os"][k] if k in doc["os"] else doc[k], v)
    for pkg, ver in sorted(doc["packages"].items()):
        cmp("package " + pkg, ver, _dpkg(pkg) or "not installed")
    cmp("python.version", doc["python"]["version"], platform.python_version())
    for m, ver in sorted(doc["python"]["modules"].items()):
        try:
            have = metadata.version(m)
        except metadata.PackageNotFoundError:
            have = "not installed"
        cmp("python module " + m, ver, have)
    for m, dg in sorted(doc["python"].get("installed_files_sha256", {}).items()):
        try:
            have = wheel_digest(m)
        except metadata.PackageNotFoundError:
            have = "not installed"
        cmp("python module %s installed files sha256" % m, dg[:12], have[:12])
    for m in sorted(set(doc["python"]["modules"]) - set(doc["python"].get("installed_files_sha256", {}))):
        bad.append("env: python module %s: no installed-files sha256 in python.installed_files_sha256" % m)
    for m in sorted(doc["python"]["modules"]):
        h = doc["python"].get("wheel_sha256", {}).get(m)
        if not (isinstance(h, str) and len(h) == 64 and all(c in "0123456789abcdef" for c in h)):
            bad.append("env: python module %s: no wheel sha256 in python.wheel_sha256 (setup installs by hash)" % m)
        f = doc["python"].get("wheel_files", {}).get(m)
        if not (isinstance(f, str) and f.endswith(".whl") and "/" not in f):
            bad.append("env: python module %s: no wheel file name in python.wheel_files (setup installs from the mirror)" % m)
    bad += deb_pins(doc)
    bad += program_classes(doc)
    for pn, pf in sorted(doc.get("profiles", {}).items()):
        bad += profile_pins(pn, pf)
    pinned, owners = pinned_set(doc["packages"]), file_owners()
    for t, dg in sorted(doc["trees"].items()):
        cmp("tree " + t, dg[:12], (tree_digest(t, pinned, owners) if os.path.isdir(t) else "missing")[:12])
    own = None
    for prog in doc["programs"]:
        path = shutil.which(prog)
        if path is None:
            cmp("program " + prog, "in PATH", "missing")
            continue
        real = os.path.realpath(path)
        own = own or _owners()
        pk = own.get(real, set())
        if not (pk & set(doc["packages"])) and real != os.path.realpath(sys.executable):
            cmp("program " + prog, "owned by a listed package", "%s owned by %s" % (real, sorted(pk) or "no package"))
    own = own or _owners()
    for drv in _root_compilers(repo):   # a distro compiler of an admitted root: its plugin headers (objstub's plugin) belong to a listed package
        files = HOST_GCC_PLUGIN_DIRS(shutil.which(drv) or drv)
        miss = sorted({p for f in files for p in [sorted(own.get(os.path.realpath(f), {"no package"}))[0]] if p not in doc["packages"]})
        cmp("plugin headers of " + drv, "owned by listed packages", "no plugin headers" if not files else ("owned by %s" % miss if miss else "owned by listed packages"))
    pinned, own = pinned_set(doc["packages"]), own
    for name, cwd, hs, err in read_roots(repo or os.path.dirname(os.path.dirname(os.path.dirname(HERE)))):
        if err:
            bad.append("env: read root %s: %s" % (name, err))
        foreign = sorted({(h, sorted(own.get(h, {"no package"}))[0]) for h in hs if not (own.get(h, set()) & pinned)})
        bad += ["env: read root %s reads %s, owned by %s (not a package of env.json)" % (name, h, o) for h, o in foreign]
    for need in doc["kernel"]["needs"]:
        ok = need in PROBES and PROBES[need]()
        cmp("kernel " + need, "available", "available" if ok else ("unavailable" if need in PROBES else "unknown capability"))
    return bad

HEX64 = "0123456789abcdef"

def deb_pins(doc):
    """Mismatch lines of doc["debs"], the mirror pin of the packages (installed by farm/install_mirror.py, no package index): the
    mirror commit and tree hashes, and an entry for EVERY package of the set S = doc["packages"] plus its dependency closure on this
    machine (dep_closure: what dpkg -i needs to be complete), each with the version (doc["packages"] for a declared package, else the
    installed one), a file name, a 64-hex sha256 and a url; no entry outside S. The installed content is not compared with the deb
    (not derivable)."""
    out, dp = [], doc.get("debs")
    def hx(h, n):
        return isinstance(h, str) and len(h) == n and all(c in HEX64 for c in h)
    if not isinstance(dp, dict) or not isinstance(dp.get("mirror"), dict) or not isinstance(dp.get("packages"), dict):
        return ["env: debs: missing (the deb mirror pin: debs.mirror and debs.packages)"]
    m = dp["mirror"]
    if not (isinstance(m.get("repo"), str) and hx(m.get("commit"), 40) and hx(m.get("tree_sha256"), 64)
            and hx(m.get("wheels_tree_sha256"), 64)):
        out.append("env: debs.mirror: needs repo, a 40-hex commit, a 64-hex tree_sha256 (debs/) and wheels_tree_sha256 (wheels/)")
    want = dict(dep_closure(doc["packages"]), **doc["packages"])
    for p in sorted(set(want) ^ set(dp["packages"])):
        out.append("env: debs.packages: %s %s" % (p, "has no deb pin" if p in want else "is not in the packages or their dependency closure"))
    for p, e in sorted(dp["packages"].items()):
        if p in want and not (isinstance(e, dict) and e.get("version") == want[p] and isinstance(e.get("file"), str)
                              and hx(e.get("sha256"), 64) and isinstance(e.get("url"), str)):
            out.append("env: debs.packages.%s: needs version %s, file, sha256 (64 hex) and url" % (p, want[p]))
    return out

def profile_pins(name, prof):
    """Mismatch lines of the STRUCTURE of build profile NAME (doc["profiles"][NAME]; checked on every machine, in the verdict too): the
    package set is the whole set (the base and the closure are in it, nothing is resolved at install time), each package has a version
    and a deb pin {version, file, sha256 (64 hex), origin, url}, debs.packages and packages name the same packages at the same
    versions, and debs.mirror names the commit and the tree sha256 of debs/. Programs the build invokes are listed."""
    out, pre = [], "env: profile %s: " % name
    pk, dp = prof.get("packages"), prof.get("debs")
    if not (isinstance(pk, dict) and pk and isinstance(dp, dict) and isinstance(dp.get("packages"), dict) and isinstance(dp.get("mirror"), dict)):
        return [pre + "needs packages, debs.mirror and debs.packages"]
    m = dp["mirror"]
    hx = lambda h, n: isinstance(h, str) and len(h) == n and all(c in HEX64 for c in h)
    if not (isinstance(m.get("repo"), str) and hx(m.get("commit"), 40) and hx(m.get("tree_sha256"), 64)):
        out.append(pre + "debs.mirror needs repo, a 40-hex commit and a 64-hex tree_sha256")
    for p in sorted(set(pk) ^ set(dp["packages"])):
        out.append(pre + "debs.packages: %s %s" % (p, "has no deb pin" if p in pk else "is not in packages"))
    for p, e in sorted(dp["packages"].items()):
        if p in pk and not (isinstance(e, dict) and e.get("version") == pk[p] and isinstance(e.get("file"), str)
                            and hx(e.get("sha256"), 64) and isinstance(e.get("url"), str) and isinstance(e.get("origin"), str)):
            out.append(pre + "debs.packages.%s: needs version %s, file, sha256 (64 hex), origin and url" % (p, pk[p]))
    fa = prof.get("foreign_arch")
    for p in sorted(pk):
        if ":" in p and p.partition(":")[2] != fa:
            out.append(pre + "package %s: a `name:ARCH` package needs foreign_arch = ARCH in the profile" % p)
    if not (isinstance(prof.get("programs"), list) and prof["programs"]):
        out.append(pre + "programs: the programs the build invokes (each must be owned by a pinned package)")
    if not set(prof.get("prerequisites", [])) <= set(prof.get("programs", [])):
        out.append(pre + "programs lack prerequisites %s" % sorted(set(prof.get("prerequisites", [])) - set(prof.get("programs", []))))
    return out

def program_classes(doc):
    """Mismatch lines of farm/program_classes.json (the classification of the programs a scanned script may invoke): every record has a
    class (leaf, runner, opaque), a doc citation, and a `package` that is a package of a build profile (the documentation is the pinned
    version's); a runner has a rule of kind argv (skip, optarg), flag (flags) or string (flags)."""
    try:
        recs = json.load(open(os.path.join(os.path.dirname(HERE), "farm", "program_classes.json")))["programs"]
    except (OSError, ValueError, KeyError):
        return ["env: farm/program_classes.json: missing or not a classification"]
    pk = {p for pf in doc.get("profiles", {}).values() for p in pf.get("packages", {})}
    out = []
    for n, r in sorted(recs.items()):
        pre = "env: program_classes.%s: " % n
        if r.get("class") not in ("leaf", "runner", "opaque") or not (isinstance(r.get("doc"), str) and r["doc"]):
            out.append(pre + "needs class (leaf, runner, opaque) and a doc citation")
        if pk and r.get("package") not in pk:
            out.append(pre + "package %s is not a package of a build profile" % r.get("package"))
        if r.get("class") == "runner":
            rl = r.get("rule") or {}
            ok = (rl.get("kind") == "argv" and isinstance(rl.get("skip"), int) and isinstance(rl.get("optarg"), list)) or \
                 (rl.get("kind") in ("flag", "string") and isinstance(rl.get("flags"), list) and rl["flags"])
            if not ok:
                out.append(pre + "a runner needs a rule: argv (skip, optarg), flag (flags) or string (flags)")
    for pn, pf in sorted(doc.get("profiles", {}).items()):   # totality: every program of a profile has a record
        out += ["env: profile %s: program %s has no record in farm/program_classes.json" % (pn, x) for x in sorted(set(pf.get("programs", [])) - set(recs))]
    return out

def check_scripts(doc, name, files):
    """Mismatch lines of the programs that shell scripts FILES invoke (farm/script_programs.py: a real shfmt parse) against build profile
    NAME: every program must be in the profile's `programs` (which must be in PATH and owned by a pinned package in the chroot), and a
    command whose name is not a literal (a variable, an expansion) is a mismatch: it cannot be derived statically."""
    prof = doc.get("profiles", {}).get(name)
    if prof is None:
        return ["env: no profile %s" % name]
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "farm"))
    import script_programs
    bad, have = [], set(prof.get("programs", []))
    for f in files:
        progs, dyn, unc = script_programs.programs(open(f, encoding="utf-8").read())
        bad += ["env: profile %s: %s runs %s, not in the profile's programs" % (name, f, p) for p in sorted(progs - have)]
        bad += ["env: profile %s: %s runs %s, which has no record in farm/program_classes.json" % (name, f, p) for p in sorted(unc)]
        bad += ["env: profile %s: %s: command name is not literal: %s" % (name, f, d) for d in dyn]
    return bad

def check_profile(doc, name):
    """Mismatch lines of THIS machine against build profile NAME: it is run inside the profile's chroot (farm/setup_verdict_env.sh
    --profile). The installed package set must be EXACTLY the profile's (name and version, nothing else installed), the os and arch
    must match, and every program the build invokes must be in PATH and owned by a pinned package (a program of no pinned package is
    a build input that the profile does not declare)."""
    prof = doc.get("profiles", {}).get(name)
    if prof is None:
        return ["env: no profile %s" % name]
    bad = profile_pins(name, prof)
    pre = "env: profile %s: " % name
    def cmp(what, want, have):
        if want != have:
            bad.append("%s%s: want %s, have %s" % (pre, what, want, have))
    cmp("os", _os(), prof.get("os"))
    cmp("arch", prof.get("arch"), platform.machine())
    have = {}
    for ln in subprocess.run(["dpkg-query", "-W", "-f=${Package}\t${Architecture}\t${Version}\t${Status}\n"], capture_output=True, text=True).stdout.splitlines():
        n, a, v, st = ln.split("\t")
        if st.endswith(" installed"):
            have[n if a in ("all", "amd64") else "%s:%s" % (n, a)] = v   # a foreign package is `name:ARCH`
    for n in sorted(set(prof.get("packages", {})) | set(have)):
        w, h = prof.get("packages", {}).get(n), have.get(n)
        if w != h:
            bad.append("%spackage %s: want %s, have %s" % (pre, n, w or "not installed", h or "not installed"))
    own = _owners()
    for prog in prof.get("programs", []):
        path = shutil.which(prog)
        if path is None:
            bad.append("%sprogram %s: want in PATH, have missing" % (pre, prog))
        elif not (own.get(os.path.realpath(path), set()) & set(prof.get("packages", {}))):
            bad.append("%sprogram %s: %s is owned by no pinned package" % (pre, prog, os.path.realpath(path)))
    return bad

def wheel_pins(prev):
    """(python.wheel_sha256, python.wheel_files) of the new snapshot: the sha256 and the file name of each module's wheel FILE (what
    farm/install_mirror.py installs from the mirror's wheels/ with pip --no-index --require-hashes). The installed package does not
    carry them, so they are taken from the previous env.json PREV for the same version; a new module or version needs its wheel added
    to the mirror and both entries written by hand (a deliberate act), else this is an error."""
    old = (prev or {}).get("python", {})
    sha, files = {}, {}
    for m in sorted(PY_MODULES):
        h, f = old.get("wheel_sha256", {}).get(m), old.get("wheel_files", {}).get(m)
        if old.get("modules", {}).get(m) != metadata.version(m) or not h or not f:
            raise SystemExit("snapshot: %s %s: no wheel sha256/file in the previous env.json for this version (add the wheel to the mirror: "
                             "pip download --no-deps %s==%s; sha256sum)" % (m, metadata.version(m), m, metadata.version(m)))
        sha[m], files[m] = h, f
    return sha, files

def snapshot(prev=None):
    import runtime
    import unitstage
    own, pk = _owners(), set()
    def add(path, strict=True):
        real = os.path.realpath(path)
        if real in own:
            pk.update(own[real])
        elif strict:
            raise SystemExit("envcheck: %s has no dpkg owner (environment not reproducible by packages)" % real)
    def loads(roots, trees=()):
        for at in runtime.closure(sorted(roots), list(trees)):
            add(at)
    py, mods = os.path.realpath(sys.executable), [os.path.dirname(__import__(PY_MODULES[m]).__file__) for m in sorted(PY_MODULES)]
    dyn = sysconfig.get_config_var("DESTSHARED")
    roots = [py] + [f for d in [dyn] + mods for f in runtime.elf_files(d, runtime.ident(py))]
    loads(roots, [os.path.dirname(os.__file__), os.path.dirname(os.path.dirname(py))] + mods)
    for name, _cwd, hs, err in read_roots(os.path.dirname(os.path.dirname(os.path.dirname(HERE)))):   # the system headers that the host roots read (readroots.json)
        if err:
            raise SystemExit("envcheck: read root %s: %s" % (name, err))
        for h in hs:
            add(h)
    for prog in PROGRAMS:
        real = os.path.realpath(shutil.which(prog) or sys.exit("envcheck: program %s not in PATH" % prog))
        add(real)
        if runtime.ident(real):
            loads([real])
    exes, trees = unitstage.gcc_set(shutil.which(UNIT_CC))
    for e in exes:
        add(e)
    files = [os.path.join(r, f) for t in trees for r, _, fs in os.walk(t) for f in fs]
    for p in files:
        add(p)
    loads([os.path.realpath(e) for e in exes] + [p for p in files if os.path.isfile(p) and not os.path.islink(p)
          and runtime.ident(p) == runtime.X86_64 and open(p, "rb").read(18)[16:18] in (b"\x02\x00", b"\x03\x00")])
    src, comps = A64ACCESS
    cxx = subprocess.run(["llvm-config-18", "--cxxflags"], capture_output=True, text=True, check=True).stdout.split()
    deps = subprocess.run(["g++", "-std=c++17"] + cxx + ["-M", src], capture_output=True, text=True, check=True).stdout
    for h in deps.replace("\\\n", " ").split()[2:]:
        add(h)
    for lib in subprocess.run(["llvm-config-18", "--libfiles"] + comps, capture_output=True, text=True, check=True).stdout.split():
        add(lib)
    inc, lib = [subprocess.run(["llvm-config-18", f], capture_output=True, text=True, check=True).stdout.strip() for f in ("--includedir", "--libdir")]
    for h in CLANG_API:   # the API headers and the whole clang/ tree they sit in (a plugin may include any sibling)
        add(os.path.join(inc, h))
        for r, _, fs in os.walk(os.path.join(inc, h.split("/")[0])):
            for f in fs:
                add(os.path.join(r, f))
    for l in CLANG_LIBS:
        add(os.path.join(lib, l))
    m32 = subprocess.run(["clang", "-m32", "-x", "c", "-M", "-"], input=M32_PROBE, capture_output=True, text=True, check=True).stdout
    for h in m32.replace("\\\n", " ").split()[1:]:
        add(h)
    gcc = shutil.which("gcc")
    for flag, names in (("-print-prog-name=", HOST_GCC_PROGS), ("-print-file-name=", HOST_GCC_FILES)):
        for n in names:
            add(subprocess.run([gcc, flag + n], capture_output=True, text=True).stdout.strip())
    for p in HOST_GCC_PLUGIN_DIRS(gcc):   # objstub builds its terminator plugin against the plugin headers of the root compiler (host roots: the distro gcc)
        add(p)
    ld = subprocess.run(["/sbin/ldconfig", "-p"], capture_output=True, text=True, check=True).stdout
    for n in RENDER_LIBS:   # the renderer's host libraries (verify/bootgpu.py loads libnxgl4.so; test_bootgpu, test_boot_job, boot_job.sh)
        hit = [l.split("=>")[1].strip() for l in ld.splitlines() if l.strip().startswith(n + " ") and "x86-64" in l]
        if not hit:
            raise SystemExit("envcheck: %s is not in the loader cache (the renderer's host library)" % n)
        add(os.path.realpath(hit[0]))
        loads([os.path.realpath(hit[0])])
    for f in RENDER_FILES:
        add(f)
    return {"format": FORMAT, "os": _os(), "arch": platform.machine(),
            "uid": os.geteuid(), "trees": {t: tree_digest(t, pinned_set(pk), file_owners()) for t in trees}, "packages": {p: _dpkg(p) for p in sorted(pk)},
            "python": {"version": platform.python_version(), "modules": {m: metadata.version(m) for m in sorted(PY_MODULES)},
                       "installed_files_sha256": {m: wheel_digest(m) for m in sorted(PY_MODULES)},
                       "wheel_sha256": wheel_pins(prev)[0], "wheel_files": wheel_pins(prev)[1]},
            "programs": sorted(PROGRAMS), "kernel": {"needs": KERNEL}, "debs": (prev or {}).get("debs"), "profiles": (prev or {}).get("profiles", {})}

def main(argv):
    ap = argparse.ArgumentParser(prog="envcheck.py")
    ap.add_argument("cmd", choices=("check", "id", "snapshot", "scripts"))
    ap.add_argument("files", nargs="*", help="scripts: the shell scripts whose programs the profile must provide")
    ap.add_argument("--env", default=os.path.join(HERE, "env.json"))
    ap.add_argument("--json")
    ap.add_argument("-o")
    ap.add_argument("--from-code", action="store_true")
    ap.add_argument("--profile", help="check/id of this build profile (doc[\"profiles\"][NAME]) instead of the verdict environment")
    a = ap.parse_args(argv)
    if a.cmd == "snapshot":
        if not (a.o and a.from_code):
            ap.error("snapshot needs -o FILE --from-code")
        open(a.o, "w").write(json.dumps(snapshot(json.load(open(a.env)) if os.path.exists(a.env) else None), sort_keys=True, indent=1) + "\n")
        return 0
    doc = json.load(open(a.env))
    if a.profile and a.cmd == "id":
        print(env_id(doc.get("profiles", {}).get(a.profile, {})))
        return 0
    if a.cmd == "id":
        print(env_id(doc))
        return 0
    if a.cmd == "scripts":
        if not a.profile:
            ap.error("scripts needs --profile NAME FILE...")
        bad = check_scripts(doc, a.profile, a.files)
    else:
        bad = check_profile(doc, a.profile) if a.profile else check(doc)
    print("\n".join(bad + ["envcheck: OK" if not bad else "envcheck: MISMATCH (%d)" % len(bad)]))
    if a.json:
        json.dump({"env_id": env_id(doc), "mismatches": bad}, open(a.json, "w"), sort_keys=True)
    return 2 if bad else 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
