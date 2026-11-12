#!/usr/bin/env python3
"""test_dkcore.py: tests of render/backend/nx/dk_core.c + dk_swap.c (deko3d backend core: device, queue, memory, dispatch, swapchain, PRESENT).
    python3 -I tests/l6/dkcore/test_dkcore.py [--no-mutations]     needs gcc, the devkitA64 cross compiler and libnx / deko3d headers (FNVNX_DK,
                                                                   default /home/user/work/l6/dk)
 a  static: each .c <= 300 lines; no stub-class words and no empty function body in the product files; nxdk_guest_ptr is not defined (not even weak)
 b  cross build (aarch64, -Werror) by build_dk.sh; the undefined symbols after linking the two objects are exactly the embedder hooks, the
    handlers of files not written yet, and library calls (dk* / nwindow* / vi* / event* / libc); the defined handlers are exactly this task's
 c  host run of the real product files under ASan + UBSan (no recovery) against a recording double of the deko3d / libnx calls (host_main.c):
    every check line 1, the exact number of checks, the named checks present
 d  mutations of copies of the product files: every one must be caught (build error, sanitizer, a failing or a missing check)
Output: the check count, mutations caught, ALL OK."""
import os, re, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
BACK = os.path.join(ROOT, "render", "gl4"); NXB = os.path.normpath(os.path.join(BACK, "..", "backend")); NXD = os.path.join(os.path.normpath(os.path.join(BACK, "..", "backend")), "nx")
DK = os.environ.get("FNVNX_DK", "/home/user/work/l6/dk")
NM = os.path.join(DK, "dkp", "devkitA64", "bin", "aarch64-none-elf-nm")
FILES = ["dk_core.c", "dk_swap.c"]
EXPECT_CHECKS = 114
MUST_NAMES = ["create_ok", "device_flags", "swap_layout", "front_layout", "swap_no_hwcompression", "swapchain_made", "front_zero_filled", "api_slots",
              "info_ok", "shutdown_waits_idle", "mem_ids_dense", "mem_flags_generic", "mem_flags_code", "mem_flags_image", "mem_gpu_addr",
              "mem_create_rejects", "mem_destroy_twice_bad", "mem_capacity_4096", "mem_all_destroyed", "present_ok", "present_order",
              "present_copy_front_to_slot", "present_barrier_before_copy", "present_signals_slot_fence", "present_fence_waited_second_round",
              "interval_4", "interval_5_bad", "depth_image_bad", "src_overflow_bad", "cursor_big_bad", "present_null_struct_bad", "wait_vblank_ok",
              "wait_vblank_bad_output", "destroy_order", "destroy_balanced", "noswap_present_bad", "noswap_balanced", "device_fail_reported",
              "vi_fail_reported"]
PENDING = {"nxdk_image_create", "nxdk_image_create4", "nxdk_image_destroy", "nxdk_image_read", "nxdk_readback", "nxdk_sampler_create",
           "nxdk_sampler_update", "nxdk_shader_create", "nxdk_shader_destroy", "nxdk_shader_create_d3d", "nxdk_submit", "nxdk_wait_frame",
           "nxdk_query_result", "nxdk_img", "nxdk_present_compose"}
EMBEDDER = {"nxdk_guest_ptr", "nxdk_on_present"}
HANDLERS = {"nxdk_mem_create", "nxdk_mem_gpu_addr", "nxdk_mem_destroy", "nxdk_present", "nxdk_wait_vblank", "nxdk_shutdown"}
LIBC = {"memset", "memcpy", "snprintf", "vsnprintf"}
LIBCALL = re.compile(r"^(dk|nwindow|vi|event)[A-Z]\w*$")
FORBID = re.compile(r"stub|todo|not implemented|placeholder|fallback|no-op|noop|weak", re.I)

fails = []
def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)
def check(ok, msg):
    if not ok: fails.append(msg); print("FAIL", msg)
def tail(s, n=12): return "\n".join(s.strip().splitlines()[-n:])

# ---- a ------------------------------------------------------------------------------------------------------------------------------------------
for f in FILES + ["dk.h"]:
    p = os.path.join(NXD, f)
    if not os.path.exists(p): check(False, "missing product file " + f); continue
    txt = open(p).read()
    if f.endswith(".c"): check(len(txt.splitlines()) <= 300, f + " is longer than 300 lines")
    m = FORBID.search(txt); check(not m, f + " contains the forbidden word " + (m.group(0) if m else ""))
    check(not re.search(r"\)\s*\{\s*\}", txt), f + " has an empty function body")
    check(not re.search(r"nxdk_guest_ptr\s*\([^;{]*\)\s*\{", txt) and "__attribute__" not in txt, f + " defines nxdk_guest_ptr or uses an attribute")
if fails: print("ALL OK" if False else "FAILED %d" % len(fails)); sys.exit(1)

# ---- b ------------------------------------------------------------------------------------------------------------------------------------------
tmp = tempfile.mkdtemp(prefix="dkcore_")
out = os.path.join(tmp, "cross")
r = sh(["sh", os.path.join(NXD, "build_dk.sh"), out])
check(r.returncode == 0, "cross build failed: " + tail(r.stderr + r.stdout))
if r.returncode == 0:
    und, dfn = set(), set()
    for line in open(os.path.join(out, "undefined.txt")).read().splitlines():
        t = line.split()
        if len(t) == 2 and t[0] == "U": und.add(t[1])
    for line in open(os.path.join(out, "defined.txt")).read().splitlines():
        t = line.split()
        if len(t) == 3 and t[1] in "TtDdBbRrCc": dfn.add(t[2])
    ext = {s for s in und if s not in dfn}
    nxs = {s for s in ext if s.startswith("nxdk")}
    check(nxs == PENDING | EMBEDDER, "undefined nxdk symbols differ: extra %s missing %s" % (sorted(nxs - PENDING - EMBEDDER), sorted(PENDING | EMBEDDER - nxs)))
    other = {s for s in ext if not s.startswith("nxdk")}
    bad = {s for s in other if not (LIBCALL.match(s) or s in LIBC)}
    check(not bad, "unexpected undefined symbols: %s" % sorted(bad))
    check(HANDLERS <= dfn and "nxdk_guest_ptr" not in dfn and "nxdk_on_present" not in dfn, "defined handlers wrong")
    nw = sh([NM, "-C", os.path.join(out, "dk_core.o"), os.path.join(out, "dk_swap.o")]).stdout
    check(not re.search(r"^\S*\s+[Ww]\s", nw, re.M), "a weak symbol is present")

# ---- c ------------------------------------------------------------------------------------------------------------------------------------------
def host(srcdir, exe):
    inc = ["-I" + os.path.join(HERE, "shim"), "-I" + os.path.join(DK, "dkp", "libnx", "include"), "-I" + os.path.join(DK, "deko3d", "include"),
           "-I" + NXD, "-I" + BACK, "-I" + NXB]
    cmd = ["gcc", "-std=gnu11", "-g", "-O1", "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined", "-Wall", "-Wextra", "-Werror"] + inc + \
          [os.path.join(HERE, "host_main.c")] + [os.path.join(srcdir, f) for f in FILES] + ["-o", exe]
    return sh(cmd)

def run_host(srcdir, tag):
    exe = os.path.join(tmp, "host_" + tag)
    b = host(srcdir, exe)
    if b.returncode: return None, "build: " + tail(b.stderr, 6)
    r = sh([exe], env=dict(os.environ, ASAN_OPTIONS="detect_leaks=1", UBSAN_OPTIONS="print_stacktrace=1"), timeout=120)
    if r.returncode: return None, "exit %d: %s" % (r.returncode, tail(r.stderr, 6))
    chk = {}
    n = 0
    for line in r.stdout.splitlines():
        m = re.match(r"CHK (\S+) ([01])$", line)
        if m: chk[m.group(1)] = chk.get(m.group(1), 1) & int(m.group(2))
        m = re.match(r"NCHK (\d+)$", line)
        if m: n = int(m.group(1))
    bad = [k for k, v in chk.items() if not v]
    if bad: return None, "failed checks: " + " ".join(bad[:8])
    if n != EXPECT_CHECKS or len(chk) < EXPECT_CHECKS - 40: return None, "check count %d (%d names), expected %d" % (n, len(chk), EXPECT_CHECKS)
    miss = [k for k in MUST_NAMES if k not in chk]
    if miss: return None, "missing checks: " + " ".join(miss[:8])
    return n, ""

n, why = run_host(NXD, "base")
check(n is not None, "host run: " + why)
if n is not None: print("checks passed %d/%d (ASan + UBSan)" % (n, n))

# ---- d ------------------------------------------------------------------------------------------------------------------------------------------
MUT = [
    ("swap images with HwCompression", "dk_swap.c", "DkImageFlags_UsageRender | DkImageFlags_UsagePresent;", "DkImageFlags_UsageRender | DkImageFlags_UsagePresent | DkImageFlags_HwCompression;"),
    ("4 KiB size check dropped", "dk_core.c", "(d->size & (DK_MEMBLOCK_ALIGNMENT - 1))", "0"),
    ("null argument array accepted", "dk_core.c", "n < e->argc || !a", "n < e->argc"),
    ("mem_destroy of a dead id", "dk_core.c", 'if (!m) { nxdk_err("mem_destroy:', 'if (0) { nxdk_err("mem_destroy:'),
    ("vsync event not cleared", "dk_swap.c", "    eventClear(&nxdk.vsync);\n", ""),
    ("interval limit 5", "dk_swap.c", "p->interval > 4", "p->interval > 5"),
    ("shutdown without wait idle", "dk_core.c", "{ dkQueueWaitIdle(nxdk.q); return 0; }", "{ return 0; }"),
    ("front not zero filled", "dk_swap.c", " | DkMemBlockFlags_ZeroFillInit", ""),
    ("depth range minus one to one", "dk_core.c", "DkDeviceFlags_DepthZeroToOne", "DkDeviceFlags_DepthMinusOneToOne"),
    ("backend id 2", "dk_core.c", "api->v3.info.backend = 1", "api->v3.info.backend = 2"),
    ("slot fence never waited", "dk_swap.c", "nxdk.fenceLive[slot] && dkFenceWait", "0 && dkFenceWait"),
    ("rect sum in 32 bits", "dk_swap.c", "(int64_t)x + w <= (int64_t)iw", "x + w <= (int64_t)iw"),
    ("barrier before the flip dropped", "dk_swap.c", "    dkCmdBufBarrier(nxdk.cmd, DkBarrier_Full, DkInvalidateFlags_Image | DkInvalidateFlags_L2Cache);\n", ""),
    ("depth image accepted", "dk_swap.c", " || (src->flags & NxImg_Depth)", ""),
    ("queue destroyed before the idle wait", "dk_core.c", "    if (nxdk.q) dkQueueWaitIdle(nxdk.q);\n    nxdk_swap_destroy();", "    nxdk_swap_destroy();\n    if (nxdk.q) dkQueueWaitIdle(nxdk.q);"),
    ("storage alignment check dropped", "dk_core.c", " || ((uintptr_t)host & (DK_MEMBLOCK_ALIGNMENT - 1))", ""),
]
caught = 0
if "--no-mutations" not in sys.argv and n is not None:
    for i, (name, f, a, b) in enumerate(MUT):
        d = os.path.join(tmp, "mut%d" % i); os.makedirs(d)
        for g in FILES: shutil.copy(os.path.join(NXD, g), d)
        txt = open(os.path.join(d, f)).read()
        if txt.count(a) != 1:
            check(False, "mutation '%s': anchor occurs %d times" % (name, txt.count(a))); continue
        open(os.path.join(d, f), "w").write(txt.replace(a, b))
        m, why = run_host(d, "m%d" % i)
        if m is None: caught += 1; print("  caught: %s (%s)" % (name, why.splitlines()[0][:90]))
        else: check(False, "mutation not caught: " + name)
    print("mutations caught %d/%d" % (caught, len(MUT)))
shutil.rmtree(tmp, ignore_errors=True)
if fails: print("FAILED %d" % len(fails)); sys.exit(1)
print("ALL OK")
