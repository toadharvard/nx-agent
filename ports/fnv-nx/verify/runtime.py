#!/usr/bin/env python3
"""runtime: a python interpreter and everything it loads, as declared sandbox inputs (rule R12), plus stage documents running a
python script on it. The sandbox has no host /usr, so the interpreter is a content-hashed input (runtime_id is in the key).

    runtime.py python --store S [--python P] [--site PKG]... -o runtime.json
    runtime.py tool --store S --exe AT... [--tree DIR|HOST@AT]... -o toolrt.json
    runtime.py stage --store S --runtime runtime.json --name N --class build|verification [--input AT=HOST]... [--output D]...
                     [--env K=V]... [--cwd D] -o stage.json -- SCRIPT args...

`python` assembles, by rule (no guessing), into the CAS store:
 (a) the interpreter file (resolved real path) at its real path, alone in its directory input (so PATH=<its dir> is under an input).
 (b) its dynamic-loader closure, parsed from the ELF headers (struct): PT_INTERP, then transitively DT_NEEDED. Search order of
     glibc ld.so(8): DT_RPATH of the object (only without DT_RUNPATH; then the RPATHs of the loading chain), DT_RUNPATH of the
     object, the directories of /etc/ld.so.conf (+ include lines), the defaults /lib /usr/lib /lib64 /usr/lib64; $ORIGIN is the
     object's directory; $LIB/$PLATFORM and unresolved names are errors. A symlink cannot be an input, so each file is ingested
     with the real CONTENT and placed at the name the loader looks up (the path where the search found it, e.g. the soname).
     Libraries found inside an included tree need no input. Roots of the closure: the interpreter, every ELF file
     of the extension-module directory (sysconfig DESTSHARED) and of every --site package.
 (c) the standard library: the target interpreter runs once with `-s -B -c` under the stage environment (this is the builder,
     not a stage) and reports sys.prefix, sys.exec_prefix, sys.path, sys.version. Every sys.path entry that exists is included
     whole (directory = tree, zip = file), except directories named exactly `__pycache__` (stages run with -B, inputs read-only). A sys.path entry that is a symlink is kept as the symlink if it
     lies inside another entry (here site-packages -> dist-packages), else an error. Inputs nested in another input are dropped.
 (d) each --site PKG (top-level name): importlib.util.find_spec in the target with `-s` (user-site is off: not found = error).
     Its package directory (or module file) is covered by (c), or else added at its real path.
Output: {"format":"fnvnx-runtime/1","python","version","inputs":[{"at","tree"}] sorted,"env","runtime_id":cas.stage_key(inputs)}.
`tool` is the same for any native tool (a compiler driver): each --exe AT is placed at the path AT as the tool looks it up (a
symlink name such as /usr/aarch64-linux-gnu/bin/as keeps its name; the content is the resolved file; an exe inside a --tree is already there), alone with the other exes of
its directory in a directory input (so PATH can name it); its loader closure is rule (b). Each --tree DIR is ingested whole at
DIR (or the directory HOST of this machine at the sandbox path AT: HOST@AT; the loader closure is then resolved as the loader
resolves it inside the sandbox: paths under AT are the files under HOST, a path under HOST outside AT does not exist, every other
path is a declared host input; an unresolvable library is an error naming object and name); every x86-64 ET_EXEC/ET_DYN ELF file in it is a further root of the closure (other ELF files, e.g. foreign-arch objects, are
data). The caller names the exes and trees (unitstage.gcc_set derives them from the compiler's own -print-* output).
Output: {"format","kind":"tool","exes","inputs","env":{},"runtime_id"}.
`stage` writes the "fnvnx-stage/1" document (+ "tool":"python"): argv = [python,-s,-B]+given, env = runtime env + TZ=UTC LC_ALL=C
SOURCE_DATE_EPOCH=0 COLUMNS=80 PATH=<interpreter dir> + given. PYTHONNOUSERSITE is left out of the stage env (toolflags allows only
PYTHONHASHSEED/PYTHONDONTWRITEBYTECODE; -s does the same job). sandbox.check_doc ACCEPTS the optional key "tool" (and it is part of
the stage key), so the one document serves toolflags.py and `sandbox.py run` alike; there is no second NAME.run.json.
"""
import argparse
import glob
import json
import os
import struct
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cas  # noqa: E402
import sandbox  # noqa: E402

FORMAT = "fnvnx-runtime/1"
ENV = {"PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
X86_64 = (2, 1, 62)  # EI_CLASS ELFCLASS64, EI_DATA ELFDATA2LSB, EM_X86_64
DEFAULT_DIRS = ["/lib", "/usr/lib", "/lib64", "/usr/lib64"]
INFO = ("import sys,os,json,sysconfig,importlib.util\n"
        "r={'prefix':sys.prefix,'exec_prefix':sys.exec_prefix,'path':sys.path,'version':sys.version,"
        "'real':os.path.realpath(sys.executable),'dynload':sysconfig.get_config_var('DESTSHARED'),'sites':{}}\n"
        "for n in sys.argv[1:]:\n s=importlib.util.find_spec(n)\n"
        " r['sites'][n]=None if s is None else [s.origin,list(s.submodule_search_locations or [])]\n"
        "print(json.dumps(r))\n")

class RtError(Exception):
    pass

def ident(path):
    """None if not an ELF file; else (EI_CLASS, EI_DATA, e_machine). Two ELF objects can load each other only if these are equal."""
    with open(path, "rb") as f:
        h = f.read(20)
    if h[:4] != b"\x7fELF" or len(h) < 20:
        return None
    return h[4], h[5], struct.unpack_from("<H" if h[5] == 1 else ">H", h, 18)[0]

def elf(path):
    """None if not an ELF file; else (interp or None, needed [str], rpath [str], runpath [str]) of an x86-64 ELF64 LE file."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"\x7fELF":
        return None
    if data[4:6] != b"\x02\x01" or struct.unpack_from("<H", data, 18)[0] != 62:
        raise RtError("%s: not ELF64 little-endian x86-64" % path)
    phoff, = struct.unpack_from("<Q", data, 32)
    esz, num = struct.unpack_from("<HH", data, 54)
    loads, dyn, interp = [], None, None
    for k in range(num):
        t, _, off, va, _, fsz, msz, _ = struct.unpack_from("<IIQQQQQQ", data, phoff + k * esz)
        if t == 1:
            loads.append((va, fsz, off))
        elif t == 2:
            dyn = (off, fsz)
        elif t == 3:
            interp = data[off:off + fsz].split(b"\0")[0].decode()
    tags = [struct.unpack_from("<qQ", data, o) for o in range(dyn[0], dyn[0] + dyn[1], 16)] if dyn else []
    tags = tags[:[t for t, _ in tags].index(0)] if dyn else tags
    strtab = next((v for t, v in tags if t == 5), None)

    def s(v):
        for va, fsz, off in loads:
            if va <= strtab < va + fsz:
                p = strtab - va + off + v
                return data[p:data.index(b"\0", p)].decode()
        raise RtError("%s: string table outside PT_LOAD" % path)
    col = lambda tg: [x for t, v in tags if t == tg for x in s(v).split(":") if x]  # noqa: E731
    return interp, [s(v) for t, v in tags if t == 1], col(15), col(29)

def conf_dirs(path="/etc/ld.so.conf"):
    out = []
    for line in open(path):
        line = line.split("#")[0].strip()
        if line.startswith("include "):
            for g in sorted(glob.glob(os.path.join(os.path.dirname(path), line[8:].strip()))):
                out += conf_dirs(g)
        elif line:
            out.append(line)
    return out

def origin(dirs, base):
    out = [d.replace("${ORIGIN}", base).replace("$ORIGIN", base) for d in dirs]
    if any("$" in d for d in out):
        raise RtError("unsupported $LIB/$PLATFORM in %s" % out)
    return out

def under(p, d):
    return p == d or p.startswith(d.rstrip("/") + "/")

def closure(roots, trees, chain0=(), maps=()):
    """{at: source} of every file the loader needs for ROOTS (sandbox paths), without those inside TREES (sandbox paths).
    MAPS [(host, at)]: a tree that lives at HOST on this machine and appears at AT in the sandbox. The loader is simulated as it runs
    INSIDE the sandbox: a path under AT is the file under HOST; a path under HOST (but not AT) does not exist there; every other path
    is a host file that becomes a declared input at that same path. A library that is found nowhere is an error naming the
    object and the name (a path that does not exist in the sandbox is never taken from the host)."""
    def real(p):
        for h, a in maps:
            if under(p, a):
                return h + p[len(a):]
        return None if any(under(p, h) for h, _ in maps) else p
    isfile = lambda p: real(p) is not None and os.path.isfile(real(p))  # noqa: E731
    out, seen, cdirs = {}, set(), conf_dirs()
    todo = [(r, tuple(chain0)) for r in roots]
    while todo:
        path, chain = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        info = elf(real(path))
        if info is None:
            raise RtError("%s: not an ELF file" % path)
        interp, needed, rpath, runpath = info
        want = ident(real(path))
        if interp and interp not in out and not any(under(interp, t) for t in trees):
            if not isfile(interp) or ident(real(interp)) != want:
                raise RtError("PT_INTERP %s of %s: missing or not of the same ELF class/encoding/machine" % (interp, path))
            out[interp] = os.path.realpath(real(interp))
        base = os.path.dirname(path)
        rp = tuple(origin(rpath, base)) if not runpath else ()
        dirs = list(rp) + list(chain) + origin(runpath, base) + cdirs + DEFAULT_DIRS
        for n in needed:
            cand = [n] if "/" in n else [os.path.join(d, n) for d in dirs]
            # like ld.so: a candidate of another class/data encoding/machine is skipped and the search goes on
            hit = next((c for c in cand if isfile(c) and ident(real(c)) == want), None)
            if hit is None:
                raise RtError("unresolved DT_NEEDED %s of %s: no ELF of class/data/machine %s in %s" % (n, path, want, ", ".join(dict.fromkeys(os.path.dirname(c) for c in cand))))
            if not any(under(hit, t) for t in trees):
                out[hit] = os.path.realpath(real(hit))
            todo.append((hit, tuple(chain) + rp))
    return out

def is_elf(p):
    return os.path.isfile(p) and not os.path.islink(p) and open(p, "rb").read(4) == b"\x7fELF"

def elf_files(d, want):
    """ELF files under D that are loadable by an object of ident WANT; ELF files of another class/encoding/machine are data."""
    return sorted(os.path.join(r, f) for r, ds, fs in os.walk(d) if "__pycache__" not in r for f in fs
                  if is_elf(os.path.join(r, f)) and ident(os.path.join(r, f)) == want)

def ingest_tree(d, store):
    ex = []
    for root, dirs, _ in os.walk(d):
        ex += [os.path.relpath(os.path.join(root, x), d) for x in dirs if x == "__pycache__"]
        dirs[:] = [x for x in dirs if x != "__pycache__"]
    return cas.ingest(d, store, ex)

def build(store, python, sites):
    real = os.path.realpath(python)
    p = subprocess.run([real, "-s", "-B", "-c", INFO] + sites, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode:
        raise RtError("cannot query %s: %s" % (real, p.stderr.strip()[-300:]))
    info = json.loads(p.stdout)
    want = ident(real)
    trees = sorted({e for e in info["path"] if e.startswith("/") and os.path.lexists(e)})
    extra, roots = {}, []
    for n in sites:
        if "." in n or info["sites"][n] is None:
            raise RtError("--site %s: not a top-level module findable with -s (user-site is disabled)" % n)
        origin_, locs = info["sites"][n]
        for loc in locs or [origin_]:
            if not loc or loc in ("built-in", "frozen"):
                raise RtError("--site %s: no file location" % n)
            loc = os.path.realpath(loc)
            if not any(under(loc, t) for t in trees):
                extra[loc] = loc
            roots += elf_files(loc, want) if os.path.isdir(loc) else [loc] * (is_elf(loc) and ident(loc) == want)
    dyn = info["dynload"]
    if dyn and os.path.isdir(dyn) and any(under(dyn, t) for t in trees):
        roots += elf_files(dyn, want)
    kept = [t for t in trees if not any(t != u and under(t, u) for u in trees)]
    for e in kept:
        if os.path.islink(e):
            raise RtError("sys.path entry %s is a symlink outside every other entry" % e)
    stage_dir = tempfile.mkdtemp(prefix="rt-python-")  # the interpreter alone, so PATH=<its dir> lies under a declared input
    shutil.copy2(real, os.path.join(stage_dir, os.path.basename(real)))
    bdir = os.path.dirname(real)
    top = kept + [e for e in extra if not any(under(e, t) for t in kept)] + [bdir]
    src = {t: t for t in top}
    src[bdir] = stage_dir
    ip = elf(real)
    libs = closure([real], top)
    libs.update(closure(roots, top, tuple(origin(ip[2], bdir)) if not ip[3] else ()))
    src.update({at: f for at, f in libs.items() if not any(under(at, t) for t in top)})
    for a in src:
        if any(b != a and under(a, b) for b in src):
            raise RtError("inputs nest: %s" % a)
    inputs = [{"at": at, "tree": ingest_tree(s, store) if os.path.isdir(s) else cas.ingest(s, store)} for at, s in sorted(src.items())]
    shutil.rmtree(stage_dir)
    return {"format": FORMAT, "python": real, "version": info["version"], "inputs": inputs, "env": dict(ENV),
            "runtime_id": cas.stage_key(inputs)}

def build_tool(store, exes, trees):
    """Tool runtime (see the docstring): exes = [AT], trees = [DIR or HOST@AT]."""
    def loadable(p):
        h = open(p, "rb").read(20)
        return ident(p) == X86_64 and struct.unpack_from("<H", h, 16)[0] in (2, 3)
    ats = sorted({os.path.normpath(os.path.abspath(e)) for e in exes})
    def hostat(t):
        h, sep, at = t.partition("@")
        return os.path.normpath(os.path.abspath(h)), os.path.normpath(os.path.abspath(at if sep else h))
    maps = sorted({hostat(t) for t in trees}, key=lambda m: m[1])
    for h, _ in maps:
        if not os.path.isdir(os.path.realpath(h)):
            raise RtError("--tree %s is not a directory" % h)
    tdirs = [a for _, a in maps]
    tmp, src, roots = tempfile.mkdtemp(prefix="rt-tool-"), {a: os.path.realpath(h) for h, a in maps}, []
    try:
        for at in ats:
            inside = next(((h, a) for h, a in maps if under(at, a)), None)
            if not os.path.isfile(os.path.realpath(inside[0] + at[len(inside[1]):] if inside else at)):
                raise RtError("--exe %s is not a file%s" % (at, " (in the tree at %s)" % inside[1] if inside else ""))
            if inside:
                continue  # the tree provides it; its ELF files are roots below
            d = src.setdefault(os.path.dirname(at), os.path.join(tmp, str(len(src))))
            os.makedirs(d, exist_ok=True)
            shutil.copy2(os.path.realpath(at), os.path.join(d, os.path.basename(at)))
            roots.append(at)
        for h, a in maps:
            roots += [a + p[len(os.path.realpath(h)):] for p in (os.path.join(r, f) for r, _, fs in os.walk(os.path.realpath(h)) for f in fs)
                      if os.path.isfile(p) and not os.path.islink(p) and loadable(p)]
        tops = list(src)
        libs = closure(roots, tops, (), maps)
        src.update({at: f for at, f in libs.items() if not any(under(at, t) for t in tops)})
        for a in src:
            if any(b != a and under(a, b) for b in src):
                raise RtError("inputs nest: %s" % a)
        inputs = [{"at": at, "tree": cas.ingest(s, store)} for at, s in sorted(src.items())]
    finally:
        shutil.rmtree(tmp)
    return {"format": FORMAT, "kind": "tool", "exes": ats, "inputs": inputs, "env": {}, "runtime_id": cas.stage_key(inputs)}

def make_stage(a):
    rt = json.load(open(a["runtime"]))
    py = rt["python"]
    env = {k: v for k, v in rt["env"].items() if k != "PYTHONNOUSERSITE"}
    env.update({"TZ": "UTC", "LC_ALL": "C", "SOURCE_DATE_EPOCH": "0", "COLUMNS": "80", "PATH": os.path.dirname(py)})
    env.update(a["env"])
    ins = rt["inputs"] + [{"at": at, "tree": cas.ingest(h, a["store"])} for at, h in a["input"]]
    doc = {"format": "fnvnx-stage/1", "stage": a["name"], "argv": [py, "-s", "-B"] + a["argv"], "cwd": a["cwd"], "env": env,
           "inputs": sorted(ins, key=lambda i: i["at"]), "outputs": sorted(a["output"]), "class": a["cls"], "tool": "python"}
    sandbox.check_doc(doc)
    o = a["o"]
    open(o, "wb").write(cas.canon(doc) + b"\n")

def parse(argv):
    k = argv.index("--") if "--" in argv else len(argv)
    p = argparse.ArgumentParser(prog="runtime.py")
    for n in ("--store", "--runtime", "--name", "--cwd", "-o"):
        p.add_argument(n, dest=n.lstrip("-"), default="/work" if n == "--cwd" else None)
    p.add_argument("--class", dest="cls")
    p.add_argument("--python", default=sys.executable)
    for n in ("site", "input", "output", "env", "exe", "tree"):
        p.add_argument("--" + n, action="append", default=[])
    a = vars(p.parse_args(argv[:k]))
    a["input"] = [tuple(x.split("=", 1)) for x in a["input"]]
    a["env"] = dict(x.split("=", 1) for x in a["env"])
    a["argv"] = argv[k + 1:]
    return a

def main(argv):
    try:
        a = parse(argv[1:])
        if not a["store"] or not a["o"]:
            raise RtError("--store and -o are required")
        if argv[:1] == ["python"]:
            doc = build(a["store"], a["python"], a["site"])
            open(a["o"], "wb").write(json.dumps(doc, sort_keys=True, indent=1).encode() + b"\n")
            print("runtime %s: %d inputs" % (doc["runtime_id"], len(doc["inputs"])))
        elif argv[:1] == ["tool"] and (a["exe"] or a["tree"]):
            doc = build_tool(a["store"], a["exe"], a["tree"])
            open(a["o"], "wb").write(json.dumps(doc, sort_keys=True, indent=1).encode() + b"\n")
            print("toolrt %s: %d inputs" % (doc["runtime_id"], len(doc["inputs"])))
        elif argv[:1] == ["stage"] and a["runtime"] and a["name"] and a["cls"] in ("build", "verification") and a["argv"]:
            make_stage(a)
        else:
            raise RtError("usage: see the module docstring")
        return 0
    except (RtError, sandbox.SbxError, cas.CasError, OSError, ValueError, KeyError) as e:
        print("runtime: error: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
