#!/usr/bin/env python3
"""Tests for verify/runtime.py (python runtime as sandbox inputs + stage documents). Plain script: exit 0 on pass,
one PASS/FAIL line per case. Needs root (sandbox). Scratch: a fresh mkdtemp under /root/l4 (or ~), removed at exit."""
import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import cas  # noqa: E402

BASE = tempfile.mkdtemp(dir="/root/l4" if os.path.isdir("/root/l4") else os.path.expanduser("~"), prefix="rt-")  # short: the PT_INTERP probe below needs BASE + "/i" within 27 bytes  # own dir per run: files run concurrently
atexit.register(shutil.rmtree, BASE, ignore_errors=True)
STORE = BASE + "/store"
WORK = BASE + "/work"
PY = [sys.executable, "-I"]
FAILED = []
TIMES = {}
SCRIPT = r'''
import argparse, collections, hashlib, json, math, re, struct, sys, zlib
p = argparse.ArgumentParser()
p.add_argument("--mod")
a = p.parse_args()
if a.mod:
    __import__(a.mod)
    sys.exit(0)
r = {"sha": hashlib.sha256(b"fnvnx").hexdigest(), "re": re.sub("a+", "b", "caaat"), "struct": struct.pack("<IH", 1, 2).hex(),
     "ctr": collections.Counter("abracadabra").most_common(2), "zlib": zlib.crc32(zlib.compress(b"x" * 1000)),
     "math": math.floor(math.sqrt(1e6)), "hash_a": hash("a"), "set_order": list({"b", "a", "c"}), "ver": sys.version}
json.dump(r, open("/out/r.json", "w"), sort_keys=True)
'''


def check(name, ok, detail=""):
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, (": " + detail) if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


def run(cmd, **kw):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **kw)


def rt(*args):
    return run(PY + [os.path.join(VERIFY, "runtime.py")] + list(args))


def sbx(*args):
    return run(PY + [os.path.join(VERIFY, "sandbox.py")] + list(args))


def main():
    shutil.rmtree(BASE, ignore_errors=True)
    os.makedirs(WORK + "/in")
    open(WORK + "/in/calc.py", "w").write(SCRIPT)
    real = os.path.realpath(sys.executable)
    t = time.time()
    p = rt("python", "--store", STORE, "--python", real, "-o", WORK + "/runtime.json")
    TIMES["build cold"] = time.time() - t
    check("build runtime (cold store)", p.returncode == 0 and os.path.exists(WORK + "/runtime.json"), p.stdout[-400:])
    if p.returncode:
        return
    t = time.time()
    p = rt("python", "--store", STORE, "--python", real, "-o", WORK + "/runtime2.json")
    TIMES["build warm"] = time.time() - t
    r1, r2 = json.load(open(WORK + "/runtime.json")), json.load(open(WORK + "/runtime2.json"))
    check("runtime_id stable across two builds", p.returncode == 0 and r1 == r2 and r1["runtime_id"] == cas.stage_key(r1["inputs"]))
    check("runtime.json shape", r1["format"] == "fnvnx-runtime/1" and r1["python"] == real and r1["version"] == sys.version
          and r1["env"] == {"PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
          and [i["at"] for i in r1["inputs"]] == sorted(i["at"] for i in r1["inputs"]))
    ats = [i["at"] for i in r1["inputs"]]
    check("no input nests in another", not any(a != b and a.startswith(b + "/") for a in ats for b in ats))
    check("no __pycache__ in the runtime", not any("__pycache__" in r for i in r1["inputs"] for r, _ in cas.listing(i["tree"], STORE)))
    n = size = 0
    for i in r1["inputs"]:
        for r, e in cas.listing(i["tree"], STORE):
            if e["t"] == "f":
                n += 1
                size += os.path.getsize(os.path.join(STORE, "obj", e["h"][:2], e["h"] + (".x" if e["x"] else "")))
    TIMES["files"], TIMES["MB"] = n, round(size / 1e6, 1)
    # closure == ldd
    import sysconfig
    ldd = ""
    for f in [real] + sorted(os.path.join(r, n) for r, _, ns in os.walk(sysconfig.get_config_var("DESTSHARED")) for n in ns if n.endswith(".so")):
        ldd += run(["ldd", f]).stdout
    want = {os.path.realpath(m) for m in re.findall(r"=> (/\S+)", ldd)} | {os.path.realpath(m) for m in re.findall(r"^\s*(/\S+) \(", ldd, re.M)}
    got = {os.path.realpath(i["at"]) for i in r1["inputs"] if cas.manifest(i["tree"], STORE).get("t") == "f"} - {real}
    check("loader closure (interpreter + extension modules) equals ldd (real files)", want == got, "only ldd %s; only runtime %s" % (sorted(want - got), sorted(got - want)))
    # stage
    args = ["stage", "--store", STORE, "--runtime", WORK + "/runtime.json", "--name", "rt-probe", "--class", "verification",
            "--input", "/in=" + WORK + "/in", "--output", "/out", "-o", WORK + "/stage.json", "--", "/in/calc.py"]
    p = rt(*args)
    check("stage document written", p.returncode == 0, p.stdout[-400:])
    d = json.load(open(WORK + "/stage.json"))
    check("stage argv/env", d["argv"] == [real, "-s", "-B", "/in/calc.py"] and d["tool"] == "python" and d["class"] == "verification"
          and d["env"]["PYTHONHASHSEED"] == "0" and d["env"]["PATH"] == os.path.dirname(real) and d["env"]["TZ"] == "UTC")
    p = run(PY + [os.path.join(VERIFY, "toolflags.py"), "check", WORK + "/stage.json"])
    check("stage passes toolflags", p.returncode == 0, p.stdout[-600:])
    run_doc = WORK + "/stage.json"
    check("the same document (with its \"tool\" key) is accepted by sandbox.check_doc; no second file", not os.path.exists(WORK + "/stage.run.json"))
    t = time.time()
    p = sbx("run", "--store", STORE, run_doc, "--result", WORK + "/res0.json")
    TIMES["sandbox run cold"] = time.time() - t
    check("sandbox run rc 0", p.returncode == 0 and json.load(open(WORK + "/res0.json"))["rc"] == 0, p.stdout[-600:])
    t = time.time()
    p = sbx("run", "--store", STORE, run_doc, "--result", WORK + "/res1.json")
    TIMES["sandbox run HIT"] = time.time() - t
    base = json.load(open(WORK + "/res0.json"))
    check("HIT returns the same result", p.returncode == 0 and json.load(open(WORK + "/res1.json")) == base)
    hs = []
    for k in range(2):
        p = sbx("run", "--store", STORE, run_doc, "--cold", "--result", WORK + "/resc%d.json" % k)
        hs.append((p.returncode, json.load(open(WORK + "/resc%d.json" % k))["outputs"]["/out"] if p.returncode == 0 else p.stdout[-300:]))
    check("--cold twice: identical output hashes (hash seed pinned)", all(h == (0, base["outputs"]["/out"]) for h in hs), str(hs))
    sbx("get", "--store", STORE, base["key"], "/out", WORK + "/got")
    got_json = json.load(open(WORK + "/got/r.json"))
    check("output carries computed values", got_json["hash_a"] == int(run(["python3", "-s", "-B", "-c", "print(hash('a'))"],
          env={"PYTHONHASHSEED": "0", "PATH": os.environ["PATH"]}).stdout) and len(got_json["set_order"]) == 3 and got_json["math"] == 1000)
    # a module outside the runtime
    os.makedirs(WORK + "/hostmod", exist_ok=True)
    open(WORK + "/hostmod/zz_hostonly.py", "w").write("X = 1\n")
    sys.path.insert(0, WORK + "/hostmod")
    p = run(["python3", "-s", "-B", "-c", "import zz_hostonly"], env={"PYTHONPATH": WORK + "/hostmod", "PATH": os.environ["PATH"]})
    check("probe module importable on the host", p.returncode == 0)
    open(WORK + "/in/imp.py", "w").write(SCRIPT)
    d2 = json.load(open(run_doc))
    d2["stage"], d2["argv"] = "rt-missing", d2["argv"][:-1] + ["/in/calc.py", "--mod", "zz_hostonly"]
    json.dump(d2, open(WORK + "/miss.json", "w"))
    p = sbx("run", "--store", STORE, WORK + "/miss.json")
    check("import of a module outside the runtime fails in the sandbox", p.returncode != 0 and '"rc":0' not in p.stdout.replace(" ", ""), p.stdout[-300:])
    # a package that is installed on this machine but not declared (--site) is not part of the runtime: whatever else is installed is
    # neither an input nor part of runtime_id (pip lives in the machine's dist-packages, like every pip/apt-installed package)
    p = run(["python3", "-s", "-B", "-c", "import pip"], env={"PATH": os.environ["PATH"]})
    check("probe: pip is installed on the host", p.returncode == 0)
    d3 = json.load(open(run_doc))
    d3["stage"], d3["argv"] = "rt-undeclared", d3["argv"][:-1] + ["/in/calc.py", "--mod", "pip"]
    json.dump(d3, open(WORK + "/undecl.json", "w"))
    p = sbx("run", "--store", STORE, WORK + "/undecl.json")
    check("import of an installed but undeclared package fails in the sandbox", p.returncode != 0 and '"rc":0' not in p.stdout.replace(" ", ""), p.stdout[-300:])
    # site package (capstone is part of the declared environment: its absence is a failure, not a skip)
    if True:
        p = rt("python", "--store", STORE, "--python", real, "--site", "capstone", "-o", WORK + "/runtime-cs.json")
        check("build runtime with --site capstone", p.returncode == 0, p.stdout[-400:])
        a2 = ["stage", "--store", STORE, "--runtime", WORK + "/runtime-cs.json", "--name", "rt-capstone", "--class", "verification",
              "--input", "/in=" + WORK + "/in", "--output", "/out", "-o", WORK + "/stage-cs.json", "--", "/in/calc.py", "--mod", "capstone"]
        rt(*a2)
        p = sbx("run", "--store", STORE, WORK + "/stage-cs.json", "--result", WORK + "/res-cs.json")
        check("import capstone works inside the sandbox", p.returncode == 0 and json.load(open(WORK + "/res-cs.json"))["rc"] == 0, p.stdout[-300:])


def hdr(cls, data, machine, etype=3):
    """A minimal ELF header (no program headers) of the given EI_CLASS/EI_DATA/e_machine."""
    import struct
    e = "<" if data == 1 else ">"
    ident = b"\x7fELF" + bytes([cls, data, 1]) + b"\0" * 9
    return ident + struct.pack(e + "HHI", etype, machine, 1) + b"\0" * 64


def loader_rule():
    """The dynamic loader skips candidates of another class/encoding/machine and goes on (ld.so(8))."""
    import runtime
    d = BASE + "/loader"
    shutil.rmtree(d, ignore_errors=True)
    for n in ("w32", "w64", "wbe", "warm", "only32"):
        os.makedirs(d + "/" + n)
    libc = os.path.realpath("/lib/x86_64-linux-gnu/libc.so.6")
    exe = os.path.realpath("/usr/bin/true")
    for n, h in (("w32", hdr(1, 1, 3)), ("wbe", hdr(2, 2, 62)), ("warm", hdr(2, 1, 183)), ("only32", hdr(1, 1, 3))):
        open("%s/%s/libc.so.6" % (d, n), "wb").write(h)
    shutil.copy(libc, d + "/w64/libc.so.6")
    check("ident: x86-64 / i386 / big-endian / arm64 differ", len({runtime.ident(p) for p in
          (libc, d + "/w32/libc.so.6", d + "/wbe/libc.so.6", d + "/warm/libc.so.6")}) == 4)
    old = (runtime.conf_dirs, runtime.DEFAULT_DIRS)
    runtime.conf_dirs, runtime.DEFAULT_DIRS = (lambda *a: []), [d + "/w64", "/lib64"]
    try:
        out = runtime.closure([exe], [], (d + "/w32", d + "/wbe", d + "/warm"))
        check("wrong-arch candidates earlier in the search order are skipped, the x86-64 one is chosen",
              sorted(out) == ["/lib64/ld-linux-x86-64.so.2", d + "/w64/libc.so.6"], str(out)[:300])
        runtime.DEFAULT_DIRS = [d + "/only32"]
        try:
            runtime.closure([exe], [], (d + "/w32", d + "/wbe", d + "/warm"))
            check("only wrong-arch candidates: error", False, "no error")
        except runtime.RtError as e:
            m = str(e)
            check("only wrong-arch candidates: error names the library and the directories searched",
                  "libc.so.6" in m and d + "/w32" in m and d + "/only32" in m, m)
        # PT_INTERP: the loader file must be of the object's class too
        data = open(exe, "rb").read()
        old_i = b"/lib64/ld-linux-x86-64.so.2\0"
        check("probe executable has the usual PT_INTERP", old_i in data)
        bad = d + "/badinterp"
        open(BASE + "/i", "wb").write(hdr(1, 1, 3))
        new_i = (BASE + "/i").encode()  # must fit the 27 bytes of the original string
        open(bad, "wb").write(data.replace(old_i, new_i + b"\0" * (len(old_i) - len(new_i))))
        os.chmod(bad, 0o755)
        runtime.DEFAULT_DIRS = [d + "/w64", "/lib64"]
        try:
            runtime.closure([bad], [], (d + "/w64",))
            check("PT_INTERP of another class: error", False, "no error")
        except runtime.RtError as e:
            check("PT_INTERP of another class: error", "PT_INTERP" in str(e), str(e))
    finally:
        runtime.conf_dirs, runtime.DEFAULT_DIRS = old
    # roots: a foreign-arch ELF file in a --site package directory or a tool tree is data, not a root of the closure
    open(d + "/w32/data.so", "wb").write(hdr(1, 1, 3))
    check("elf_files(want) leaves foreign-arch files out", runtime.elf_files(d + "/w32", runtime.ident(libc)) == []
          and runtime.elf_files(d + "/w64", runtime.ident(libc)) == [d + "/w64/libc.so.6"])


def tool_tree_map():
    """--tree HOST@AT: a toolchain tree that lives at HOST here and at /opt/tc in the sandbox; its binaries find a private library
    through an absolute RUNPATH into their own prefix (/opt/tc/lib), another through $ORIGIN, a third through an old-style RPATH."""
    import runtime
    h = BASE + "/tc"
    shutil.rmtree(h, ignore_errors=True)
    for n in ("bin", "lib", "lib2", "lib3", "src"):
        os.makedirs(h + "/" + n)
    for n, body in (("priv", "int priv(void){return 40;}"), ("sec", "int sec(void){return 2;}"), ("old", "int old(void){return 100;}")):
        open("%s/src/%s.c" % (h, n), "w").write(body)
    open(h + "/src/d.c", "w").write('#include <stdio.h>\nint priv(void);int sec(void);int old(void);\n'
                                   'int main(void){FILE*f=fopen("/out/r.txt","w");fprintf(f,"%d\\n",priv()+sec()+old());return fclose(f);}\n')
    cc = lambda *a: run(["gcc"] + list(a), cwd=h + "/src")  # noqa: E731
    for n, d in (("priv", "lib"), ("sec", "lib2"), ("old", "lib3")):
        p = cc("-shared", "-fPIC", "-Wl,-soname,lib%s.so" % n, "-o", "../%s/lib%s.so" % (d, n), n + ".c")
        check("toolchain fixture: lib%s built" % n, p.returncode == 0, p.stdout)
    link = lambda out, *fl: cc("-o", "../bin/" + out, "d.c", "-L../lib", "-L../lib2", "-L../lib3", "-lpriv", "-lsec", "-lold", *fl)  # noqa: E731
    p = link("driver", "-Wl,--enable-new-dtags", "-Wl,-rpath,/opt/tc/lib:$ORIGIN/../lib2:/opt/tc/lib3")
    check("toolchain fixture: driver (absolute + $ORIGIN RUNPATH) built", p.returncode == 0, p.stdout)
    p = link("drv-rpath", "-Wl,--disable-new-dtags", "-Wl,-rpath,/opt/tc/lib:$ORIGIN/../lib2:/opt/tc/lib3")
    check("toolchain fixture: drv-rpath (DT_RPATH) built", p.returncode == 0, p.stdout)
    check("host has no /opt/tc (the sandbox path exists only through the mapping)", not os.path.exists("/opt/tc"))
    store = BASE + "/store-tc"
    for exe in ("driver", "drv-rpath"):
        for how in ("cli", "lib"):
            if how == "cli":
                p = rt("tool", "--store", store, "--exe", "/opt/tc/bin/" + exe, "--tree", h + "@/opt/tc", "-o", WORK + "/tc.json")
                ok, doc = p.returncode == 0, p.returncode == 0 and json.load(open(WORK + "/tc.json"))
            else:
                doc = runtime.build_tool(store, ["/opt/tc/bin/" + exe], [h + "@/opt/tc"])
                ok = True
            check("tool runtime with --tree HOST@AT (%s, %s)" % (exe, how), ok and [i["at"] for i in doc["inputs"]].count("/opt/tc") == 1
                  and all(i["at"] == "/opt/tc" or i["at"].startswith(("/lib", "/usr/lib")) for i in doc["inputs"]), p.stdout if how == "cli" else "")
        d = {"format": "fnvnx-stage/1", "stage": "tc-" + exe, "argv": ["/opt/tc/bin/" + exe], "cwd": "/", "env": {"PATH": "/opt/tc/bin"},
             "inputs": doc["inputs"], "outputs": ["/out"], "class": "build"}
        json.dump(d, open(WORK + "/tc-run.json", "w"))
        p = sbx("run", "--store", store, WORK + "/tc-run.json", "--result", WORK + "/tc-res.json")
        r = json.load(open(WORK + "/tc-res.json")) if p.returncode == 0 else {}
        check("%s runs in the sandbox with its libraries found through the mapped paths" % exe, r.get("rc") == 0, p.stdout[-400:])
        if r.get("rc") == 0:
            sbx("get", "--store", store, r["key"], "/out", WORK + "/tc-got")
            check("%s computed 142 from three libraries" % exe, open(WORK + "/tc-got/r.txt").read().strip() == "142")
            shutil.rmtree(WORK + "/tc-got")
    # errors: a missing library names object and name; a HOST path in RUNPATH is not a sandbox path
    shutil.copytree(h, BASE + "/tc-miss", symlinks=True)
    os.unlink(BASE + "/tc-miss/lib2/libsec.so")
    try:
        runtime.build_tool(store, [], [BASE + "/tc-miss@/opt/tc"])
        check("missing library: error", False, "no error")
    except runtime.RtError as e:
        check("missing library: error names the object and the missing name", "libsec.so" in str(e) and "/opt/tc/bin/driver" in str(e), str(e))
    shutil.copytree(h, BASE + "/tc-host", symlinks=True)
    for n in ("driver", "drv-rpath"):
        os.unlink(BASE + "/tc-host/bin/" + n)
    th = BASE + "/tc-host"
    p = link("drv-host", "-Wl,--enable-new-dtags", "-Wl,-rpath,%s/lib:%s/lib2:%s/lib3" % (th, th, th))
    check("fixture: host-path RUNPATH binary", p.returncode == 0, p.stdout)
    shutil.move(h + "/bin/drv-host", th + "/bin/drv-host")
    try:
        runtime.build_tool(store, [], [BASE + "/tc-host@/opt/tc"])
        check("RUNPATH naming the HOST location: not taken from the host by accident", False, "no error")
    except runtime.RtError as e:
        check("RUNPATH naming the HOST location: error names object and name", "libpriv.so" in str(e) and "/opt/tc/bin/drv-host" in str(e), str(e))


if os.geteuid() != 0:
    print("FAIL prerequisites: need root (declared environment uid 0)")
    sys.exit(1)
loader_rule()
main()
tool_tree_map()
print("TIMES " + json.dumps(TIMES, sort_keys=True))
print("%d failed: %s" % (len(FAILED), FAILED) if FAILED else "ALL PASS")
sys.exit(1 if FAILED else 0)
