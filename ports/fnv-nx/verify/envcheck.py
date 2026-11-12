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
git verdict.py,l1.py,test_gates; patch recipe.py (stage patch:<root>); ldd,true test_runtime; g++,llvm-config-18 build a64access.cc (objstub.py, test_a64access).
The a64access build set is derived the same way: every header `g++ -M` reports for a64access.cc under `llvm-config-18 --cxxflags`,
and every library file `llvm-config-18 --libfiles` names for A64ACCESS's components (plus cc1plus, libstdc++.so above). The clang set (clang-18 plugins such as vtnames; clang -m32 compiles) is derived the same way: the headers under `llvm-config-18 --includedir` that a plugin includes (CLANG_API) with the whole clang/ tree they sit in, the libraries of CLANG_LIBS under `--libdir`, and every header `clang -m32 -M` reports for a C probe including stdint.h and stdlib.h (gnu/stubs-32.h: libc6-dev-i386). EGL/GL headers: every header `g++ -M` reports for GL_PROBE (libegl-dev, libgl-dev and what they need). Python modules: capstone, unicorn (icount.py, test_icount, test_runtime); pyelftools (stage tools of contract/: objfacts); numpy (lift/test/instest.py packer)."""
import argparse
import ctypes
import hashlib
import json
import os
import platform
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
            "aarch64-linux-gnu-as", "aarch64-linux-gnu-ld", "aarch64-linux-gnu-objcopy", "ar", "as", "ld", "clang", "git", "ldd", "true", "g++", "llvm-config-18", "patch"]
UNIT_CC = "aarch64-linux-gnu-g++"  # the unit stage ingests its trees whole
HOST_GCC_PROGS = ["cc1", "cc1plus", "collect2"]  # gcc -print-prog-name=: what the host gcc runs for the static probe / -fsyntax-only
HOST_GCC_FILES = ["libc.a", "crt1.o", "libgcc.a", "libgcc_eh.a", "libstdc++.so", "libstdc++.a", "libm.a", "crti.o", "crtn.o", "crtbeginT.o", "crtend.o"]  # gcc -print-file-name=: what `gcc -static` links
A64ACCESS = (os.path.join(HERE, "a64access.cc"), ["aarch64", "mc", "mcdisassembler", "support"])  # source, llvm-config components
CLANG_API = ["clang/AST/RecursiveASTVisitor.h", "clang/Frontend/FrontendPluginRegistry.h", "clang/Basic/Version.h"]  # the plugin API vtnames (clang-18 MSVC-ABI facts plugin) includes: headers under `llvm-config-18 --includedir`
CLANG_LIBS = ["libclang-cpp.so.18.1", "libclang.so"]   # under `llvm-config-18 --libdir`: what a clang plugin links / dlopens
GL_PROBE = "#include <EGL/egl.h>\n#include <EGL/eglext.h>\n#include <GL/gl.h>\n#include <GL/glx.h>\nint main(){return 0;}\n"   # g++ -M: the EGL/GL headers of the GL backend root (libegl-dev, libgl-dev, libglx-dev, KHR)
M32_PROBE = "#include <stdint.h>\n#include <stdlib.h>\nint main(){return 0;}\n"   # clang -m32 -M: every header a 32-bit compile of the C library reads (gnu/stubs-32.h: libc6-dev-i386)
PY_MODULES = {"capstone": "capstone", "numpy": "numpy", "unicorn": "unicorn", "pyelftools": "elftools", "z3-solver": "z3"}   # distribution -> import name
WHEEL_SHA256 = {"z3-solver": "e680ce083683abca5d07c3e55c2f620ff9a29cb91c548eeeea27ae29e8060522"}   # sha256 of the .whl (z3_solver-5.4.1.0-py3-none-manylinux_2_27_x86_64)
PY_WHEELS = ["z3-solver"]   # pinned by the sha256 of the wheel (env.json python.wheels) and installed with --require-hashes; the installed files are checked too
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


def tree_digest(d):
    """sha256 of the sorted (relative path, size) listing of tree D."""
    h = hashlib.sha256()
    for r, ds, fs in sorted(os.walk(d)):
        ds.sort()
        for f in sorted(fs + [x for x in ds if os.path.islink(os.path.join(r, x))]):
            p = os.path.join(r, f)
            h.update(("%s %d\n" % (os.path.relpath(p, d), os.lstat(p).st_size)).encode())
    return h.hexdigest()

def _os():
    osr = dict(ln.rstrip("\n").split("=", 1) for ln in open("/etc/os-release") if "=" in ln)
    return {"id": osr.get("ID", "").strip('"'), "version_id": osr.get("VERSION_ID", "").strip('"')}

def _dpkg(pkg):
    p = subprocess.run(["dpkg-query", "-W", "-f", "${Version}", pkg], capture_output=True, text=True)
    return p.stdout or None

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
    for m, w in sorted(doc["python"].get("wheels", {}).items()):
        try:
            have = wheel_digest(m)
        except (metadata.PackageNotFoundError, OSError, ValueError):
            have = "not installed"
        cmp("python wheel files " + m, w["files_sha256"], have)
    for t, dg in sorted(doc["trees"].items()):
        cmp("tree " + t, dg[:12], (tree_digest(t) if os.path.isdir(t) else "missing")[:12])
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
    for need in doc["kernel"]["needs"]:
        ok = need in PROBES and PROBES[need]()
        cmp("kernel " + need, "available", "available" if ok else ("unavailable" if need in PROBES else "unknown capability"))
    return bad

def snapshot():
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
    gl = subprocess.run(["g++", "-x", "c++", "-M", "-"], input=GL_PROBE, capture_output=True, text=True, check=True).stdout
    for h in gl.replace("\\\n", " ").split()[1:]:
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
            "uid": os.geteuid(), "trees": {t: tree_digest(t) for t in trees}, "packages": {p: _dpkg(p) for p in sorted(pk)},
            "python": {"version": platform.python_version(), "modules": {m: metadata.version(m) for m in sorted(PY_MODULES)},
                       "wheels": {m: {"sha256": WHEEL_SHA256[m], "files_sha256": wheel_digest(m)} for m in sorted(PY_WHEELS)}},
            "programs": sorted(PROGRAMS), "kernel": {"needs": KERNEL}}

def main(argv):
    ap = argparse.ArgumentParser(prog="envcheck.py")
    ap.add_argument("cmd", choices=("check", "id", "snapshot"))
    ap.add_argument("--env", default=os.path.join(HERE, "env.json"))
    ap.add_argument("--json")
    ap.add_argument("-o")
    ap.add_argument("--from-code", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "snapshot":
        if not (a.o and a.from_code):
            ap.error("snapshot needs -o FILE --from-code")
        open(a.o, "w").write(json.dumps(snapshot(), sort_keys=True, indent=1) + "\n")
        return 0
    doc = json.load(open(a.env))
    if a.cmd == "id":
        print(env_id(doc))
        return 0
    bad = check(doc)
    print("\n".join(bad + ["envcheck: OK" if not bad else "envcheck: MISMATCH (%d)" % len(bad)]))
    if a.json:
        json.dump({"env_id": env_id(doc), "mismatches": bad}, open(a.json, "w"), sort_keys=True)
    return 2 if bad else 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
