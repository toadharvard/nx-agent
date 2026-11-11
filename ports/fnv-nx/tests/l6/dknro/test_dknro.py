#!/usr/bin/env python3
"""test_dknro.py: tests of render/d3d9/backend/selftest/firstframe_main.c + build_nro.sh (the first-frame NRO over the deko3d backend contract, nx/dk.h).
    python3 -I tests/l6/dknro/test_dknro.py [--no-mutations]     needs the env of /home/user/work/l6a/env.sh (FNVNX_DK, DEVKITPRO, DEKO3D, UAM, toolchain on PATH)
 a  libnx facts the embedder rule rests on (objdump of the pinned libnx.a): the default __appInit calls the weak __nx_win_init, which calls viInitialize and
    is in the object that defines nwindowGetDefault; firstframe_main.o defines no __appInit and calls no vi* itself
 b  build_dk.sh then build_nro.sh into temp dirs; the link has no undefined symbol (TODAY the only allowed failure is nxdk_present_compose: one failing check
    "link: backend whole", the NRO checks are then blocked = counted failed)
 c  the object of firstframe_main.c (-Werror) from its disassembly (objdump -dr of main, order of the bl targets): nxdk_create before nxgpu4_selftest, its result
    tested (cbz/cbnz on x0) right after the call, nxdk_destroy after the selftest; the object defines nxdk_guest_ptr / nxdk_on_present / nxdk_shader_pack once
 d  the NRO: elf defines the three embedder symbols once and contains nxdk_create, nxdk_ext, dkDeviceCreate; NRO0 magic at 0x10, size field = the NRO part
 e  reproducibility: two builds give identical objects (and identical firstframe.nro once the link succeeds)
 f  mutations of a copy of firstframe_main.c: every one must be caught by the checks of c
Output: one line per check, ALL OK or FAILED <n>."""
import hashlib, os, re, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
BACK = os.path.join(ROOT, "render", "d3d9", "backend"); NXD = os.path.join(BACK, "nx"); ST = os.path.join(BACK, "selftest")
ENV = dict(os.environ)
ENV.setdefault("FNVNX_TC", "/root/fnvnx-farm/toolchain-1fc1f7b1ef44/toolchain/prefix")
ENV.setdefault("DEVKITPRO", "/root/fnvnx-farm/toolchain-1fc1f7b1ef44/toolchain/dkp")
ENV.setdefault("DEKO3D", "/home/user/work/l6a/deko3d")
ENV.setdefault("FNVNX_DK", "/home/user/work/l6a/dk")
ENV.setdefault("FNVNX_SHADERS_DEPS", "/home/user/work/l6a")
ENV.setdefault("UAM", "/home/user/work/l6a/uam/bin/uam")
ENV["PATH"] = ENV["FNVNX_TC"] + "/bin:" + ENV["PATH"]
DK = ENV["FNVNX_DK"]
BIN = os.path.join(DK, "dkp", "devkitA64", "bin")
NM, OBJDUMP, GCC = [os.path.join(BIN, "aarch64-none-elf-" + t) for t in ("nm", "objdump", "gcc")]
PENDING = {"nxdk_present_compose"}   # symbol of nx/dk_present.c, which lands later from another worker
EMBEDDER = ["nxdk_guest_ptr", "nxdk_on_present", "nxdk_shader_pack"]

fails, nchecks = [], 0
def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, env=ENV, **kw)
def check(ok, msg):
    global nchecks
    nchecks += 1
    print(("ok   " if ok else "FAIL ") + msg)
    if not ok: fails.append(msg)
    return ok
def tail(s, n=8): return " | ".join(s.strip().splitlines()[-n:])
def sha(p): return hashlib.sha256(open(p, "rb").read()).hexdigest()
def defined(obj, sym):
    return sum(1 for l in sh([NM, obj]).stdout.splitlines() if re.match(r"^\S+\s+[TtWw]\s+%s$" % re.escape(sym), l))
def has_sym(obj, sym): return any(l.split()[-1] == sym for l in sh([NM, obj]).stdout.splitlines() if l.strip())

def main_calls(obj):
    """[(addr, target)] of the bl instructions of main in order, plus the instruction after each (mnemonic, operands)"""
    d = sh([OBJDUMP, "-dr", "--section=.text.startup.main", "--section=.text.main", obj]).stdout
    insns, calls = [], []
    for l in d.splitlines():
        m = re.match(r"^\s*([0-9a-f]+):\s+[0-9a-f]{8}\s+(\S+)\s*(.*)$", l)
        if m: insns.append([int(m.group(1), 16), m.group(2), m.group(3), None])
        m = re.match(r"^\s*([0-9a-f]+):\s+R_AARCH64_CALL26\s+(\S+)", l)
        if m and insns and insns[-1][0] == int(m.group(1), 16): insns[-1][3] = m.group(2)
    for i, (a, mn, op, tgt) in enumerate(insns):
        if mn == "bl" and tgt: calls.append((i, tgt))
    return insns, calls

def analyze(obj, tag):
    """the checks of group c on one object; returns the list of failed check names (used by the mutation runs too)"""
    bad = []
    def ck(ok, name):
        if not ok: bad.append(name)
        return ok
    insns, calls = main_calls(obj)
    names = [t for _, t in calls]
    ic = names.index("nxdk_create") if "nxdk_create" in names else None
    isf = names.index("nxgpu4_selftest") if "nxgpu4_selftest" in names else None
    idt = names.index("nxdk_destroy") if "nxdk_destroy" in names else None
    ck(ic is not None, "main calls nxdk_create")
    ck(isf is not None, "main calls nxgpu4_selftest")
    ck(ic is not None and isf is not None and ic < isf, "nxdk_create before nxgpu4_selftest")
    ck(names.count("nxdk_create") == 1, "exactly one nxdk_create call")
    if ic is not None:
        tracked, tested = {"x0"}, False   # the result register and its copies (mov xN, x0), up to the next call
        for mn, op in [(x[1], x[2]) for x in insns[calls[ic][0] + 1:]]:
            if mn == "bl": break
            m = re.match(r"(x\d+), x0$", op)
            if mn == "mov" and m: tracked.add(m.group(1))
            m = re.match(r"(x\d+),", op)
            if mn in ("cbz", "cbnz") and m and m.group(1) in tracked: tested = True; break
        ck(tested, "nxdk_create result tested (cbz/cbnz on x0 or its copy) before the next call")
    ck(idt is not None and isf is not None and idt > isf, "nxdk_destroy after nxgpu4_selftest")
    ck(names.count("nxdk_destroy") == 1, "exactly one nxdk_destroy call")
    ck("nxdk_ext" not in names, "main does not call nxdk_ext itself")
    for s in EMBEDDER: ck(defined(obj, s) == 1, "object defines %s once" % s)
    ck(defined(obj, "__appInit") == 0 and not any(re.match(r"^vi[A-Z]|^nwindow", t) for t in names), "no __appInit and no vi*/nwindow* call of its own")
    ck(b"RESULT FAIL nxdk_create: %s" in open(obj, "rb").read(), "failure of nxdk_create is logged as RESULT FAIL nxdk_create: <err>")
    ck(b"PRESENTS %u last_frame %u" in open(obj, "rb").read(), "PRESENTS line is logged")
    return bad

# ---- a: libnx facts ---------------------------------------------------------------------------------------------------------------------------------
tmp = tempfile.mkdtemp(prefix="dknro_")
libnx = os.path.join(ENV["DEVKITPRO"], "libnx", "lib", "libnx.a")
lx = os.path.join(tmp, "libnx"); os.makedirs(lx)
AR = os.path.join(BIN, "aarch64-none-elf-ar")
sh([AR, "x", libnx, "init.o", "default_window.o"], cwd=lx)
d = sh([OBJDUMP, "-dr", "--section=.text.__appInit", os.path.join(lx, "init.o")]).stdout
check("R_AARCH64_CALL26\t__nx_win_init" in d and "R_AARCH64_LD64_GOT_LO12_NC\t__nx_win_init" in d, "libnx: default __appInit calls the weak __nx_win_init (null-tested GOT slot)")
w = sh([NM, os.path.join(lx, "init.o")]).stdout
check(re.search(r"^\s+w __nx_win_init$", w, re.M) is not None and re.search(r"^\S+ W __appInit$", w, re.M) is not None, "libnx: __nx_win_init is a weak reference, __appInit is a weak definition")
dw = sh([OBJDUMP, "-dr", os.path.join(lx, "default_window.o")]).stdout
wn = sh([NM, os.path.join(lx, "default_window.o")]).stdout
check("viInitialize" in dw and re.search(r" T __nx_win_init$", wn, re.M) and re.search(r" T nwindowGetDefault$", wn, re.M), "libnx: default_window.o defines __nx_win_init (viInitialize) and nwindowGetDefault")

# ---- b: builds -------------------------------------------------------------------------------------------------------------------------------------
dkout = os.path.join(tmp, "dk")
r = sh(["sh", os.path.join(NXD, "build_dk.sh"), dkout])
check(r.returncode == 0 and os.path.exists(os.path.join(dkout, "libnxdk.a")), "build_dk.sh writes libnxdk.a: " + tail(r.stderr + r.stdout, 2))
def build(out):
    e = dict(ENV); e["NXDK_LIB"] = os.path.join(dkout, "libnxdk.a")
    return subprocess.run(["bash", os.path.join(ST, "build_nro.sh"), out], capture_output=True, text=True, env=e)
o1, o2 = os.path.join(tmp, "nro1"), os.path.join(tmp, "nro2")
b1 = build(o1)
und = set(re.findall(r"undefined reference to [`']([^'`]+)'", b1.stderr))
linked = b1.returncode == 0
if linked:
    check(True, "link: backend whole (no undefined symbol)")
else:
    other_err = [l for l in b1.stderr.splitlines() if "error" in l.lower() and "undefined reference" not in l and "collect2" not in l and "ld returned" not in l]
    check(False, "link: backend whole: undefined %s" % sorted(und))
    check(und == PENDING and not other_err, "link: the only undefined symbol is nxdk_present_compose (and no other error) -> %s %s" % (sorted(und), other_err[:2]))
mo = os.path.join(o1, "firstframe_main.o")
check(os.path.exists(mo), "firstframe_main.o built with -Werror")
if not os.path.exists(mo):
    print("FAILED %d" % (len(fails) + 1)); sys.exit(1)

# ---- c ---------------------------------------------------------------------------------------------------------------------------------------------
bad = analyze(mo, "product")
check(not bad, "firstframe_main.o rules (create/order/test/destroy/embedder symbols): " + ", ".join(bad))
src = open(os.path.join(ST, "firstframe_main.c")).read()
check('#include "nx/dk.h"' in src and "uint64_t nxdk_ext(" not in src, "firstframe_main.c includes nx/dk.h and declares no backend symbol itself")
check("nxdk_guest_ptr(uint64_t addr, uint64_t size)" in src, "nxdk_guest_ptr is defined with the dk.h signature (compiler-checked through dk.h)")

# ---- d, e ------------------------------------------------------------------------------------------------------------------------------------------
elf, nro = os.path.join(o1, "firstframe.elf"), os.path.join(o1, "firstframe.nro")
if linked:
    for s in EMBEDDER: check(defined(elf, s) == 1, "elf defines %s exactly once" % s)
    for s in ("nxdk_create", "nxdk_ext", "dkDeviceCreate"): check(has_sym(elf, s), "elf contains " + s)
    data = open(nro, "rb").read()
    check(data[0x10:0x14] == b"NRO0", "NRO0 magic at 0x10")
    size = int.from_bytes(data[0x18:0x1c], "little")
    check(0 < size <= len(data) and (size == len(data) or data[size:size + 4] == b"ASET"), "NRO header size field %d is the NRO part of the %d byte file" % (size, len(data)))
else:
    for n in ("elf defines nxdk_guest_ptr, nxdk_on_present, nxdk_shader_pack once each", "elf contains nxdk_create, nxdk_ext, dkDeviceCreate",
              "NRO0 magic at 0x10", "NRO header size field = the NRO part"):
        check(False, "BLOCKED by nxdk_present_compose (no link): " + n)
b2 = build(o2)
same = all(os.path.exists(os.path.join(o2, f)) and sha(os.path.join(o1, f)) == sha(os.path.join(o2, f)) for f in ("firstframe_main.o", "nxgpu4_selftest.o", "ff_dksh.o"))
check(same, "reproducible: two builds give identical objects")
if linked: check(b2.returncode == 0 and sha(nro) == sha(os.path.join(o2, "firstframe.nro")), "reproducible: two builds give identical firstframe.nro")
else: check(False, "BLOCKED by nxdk_present_compose (no link): reproducible firstframe.nro")

# ---- f: mutations ----------------------------------------------------------------------------------------------------------------------------------
CREATE = "int created = nxdk_create(docked ? 1920 : 1280, docked ? 1080 : 720, cerr, sizeof cerr) != NULL;"
MUT = [
    ("nxdk_create call dropped", CREATE, "int created = 1; (void)cerr;"),
    ("selftest before create", CREATE + "\n    int st = 1;\n    if (created) st = run_selftest();\n    else h_log",
     "int st = run_selftest();\n    int created = nxdk_create(docked ? 1920 : 1280, docked ? 1080 : 720, cerr, sizeof cerr) != NULL;\n    if (!created) h_log"),
    ("nxdk_shader_pack definition removed", "const void* nxdk_shader_pack(uint64_t* size) { *size = 0; return NULL; }", ""),
    ("NULL from nxdk_create ignored", CREATE, "int created = (nxdk_create(docked ? 1920 : 1280, docked ? 1080 : 720, cerr, sizeof cerr), 1);"),
    ("nxdk_destroy dropped", "    if (created) nxdk_destroy();\n", ""),
    ("create failure not logged", '"RESULT FAIL nxdk_create: %s"', '"RESULT FAIL %s"'),
]
caught = 0
if "--no-mutations" not in sys.argv:
    cf = ["-march=armv8-a+crc+crypto", "-mtune=cortex-a57", "-mtp=soft", "-fPIE", "-O2", "-g", "-Wall", "-Wextra", "-Werror", "-ffunction-sections", "-fdata-sections",
          "-D__SWITCH__", '-DFNV_BUILD_ID="mut"', "-I" + ST, "-I" + BACK, "-I" + ENV["DEKO3D"] + "/include", "-I" + ENV["DEVKITPRO"] + "/libnx/include"]
    for i, (name, a, b) in enumerate(MUT):
        d = os.path.join(tmp, "mut%d" % i); os.makedirs(d)
        if src.count(a) != 1: check(False, "mutation '%s': anchor occurs %d times" % (name, src.count(a))); continue
        p = os.path.join(d, "firstframe_main.c"); open(p, "w").write(src.replace(a, b))
        r = sh([GCC] + cf + ["-c", p, "-o", os.path.join(d, "m.o")])
        if r.returncode != 0: caught += 1; print("  caught (build error): %s" % name); continue
        bad = analyze(os.path.join(d, "m.o"), name)
        if bad: caught += 1; print("  caught: %s (%s)" % (name, bad[0]))
        else: check(False, "mutation not caught: " + name)
    print("mutations caught %d/%d" % (caught, len(MUT)))
shutil.rmtree(tmp, ignore_errors=True)
print("checks run: %d" % nchecks)
if fails: print("FAILED %d" % len(fails)); sys.exit(1)
print("ALL OK")
