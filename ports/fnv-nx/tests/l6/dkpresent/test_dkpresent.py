#!/usr/bin/env python3
"""test_dkpresent.py: tests of render/d3d9/backend/nx/dk_present.c (deko3d backend: the compose passes of PRESENT).
    python3 -I tests/l6/dkpresent/test_dkpresent.py [--no-mutations]   needs gcc, the devkitA64 cross compiler and deko3d headers (FNVNX_DK)
 a  static: <= 300 lines, lines <= 160, no stub-class word, no empty function body, no weak symbol, no attribute
 b  cross build (aarch64, -Werror) by build_dk.sh; the union of the undefined nxdk_* symbols of all backend objects (minus what they define) is exactly
    {nxdk_guest_ptr, nxdk_on_present, nxdk_shader_pack}
 c  host run of the real product file (+ dk_fmt.c) under ASan + UBSan against a recording / executing double (host_main.c): the exact recorded
    sequence for several scale rects, with and without ramp, resolve of 2x / 4x sources, the cursor inside / clipped on each edge / on all four /
    fully outside / NULL (no GPU work), the front's final bytes against the model of the reference equation (and the exact bytes uploaded), the
    lifetime of every block and the temporary image (released once, after the wait that consumes it), every error path, the validation
 d  mutations of a copy of the product file: every one must be caught
Output: the check count, mutations caught, ALL OK."""
import concurrent.futures, os, re, shutil, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
BACK = os.path.join(ROOT, "render", "d3d9", "backend"); NXD = os.path.join(BACK, "nx")
DK = os.environ.get("FNVNX_DK", "/home/user/work/l6a/dk")
NM = os.path.join(DK, "dkp", "devkitA64", "bin", "aarch64-none-elf-nm")
FORBID = re.compile(r"stub|todo|not implemented|placeholder|fallback|no-op|noop|weak", re.I)
INC = ["-I" + os.path.join(DK, "dkp", "libnx", "include"), "-I" + os.path.join(DK, "deko3d", "include"), "-I" + NXD, "-I" + BACK,
       "-I" + os.path.join(ROOT, "tests", "l6", "dkcore", "shim")]

def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)
def tail(s, n=8): return "\n".join(s.strip().splitlines()[-n:])
fails = []
def check(ok, msg):
    if not ok: fails.append(msg); print("FAIL", msg)

tmp = tempfile.mkdtemp(prefix="dkpresent_")
SRC = open(os.path.join(NXD, "dk_present.c")).read()

# ---- a ----
check(len(SRC.splitlines()) <= 300, "dk_present.c is longer than 300 lines")
check(max(len(l) for l in SRC.splitlines()) <= 160, "dk_present.c has a line longer than 160 characters")
m = FORBID.search(SRC); check(not m, "dk_present.c contains the forbidden word " + (m.group(0) if m else ""))
check(not re.search(r"\)\s*\{\s*\}", SRC), "dk_present.c has an empty function body")
check("__attribute__" not in SRC, "dk_present.c uses an attribute")

# ---- b ----
out = os.path.join(tmp, "cross")
r = sh(["sh", os.path.join(NXD, "build_dk.sh"), out]); check(r.returncode == 0, "cross build failed: " + tail(r.stderr + r.stdout))
if r.returncode == 0:
    und, dfn = set(), set()
    for f in sorted(os.listdir(out)):
        if not f.endswith(".o"): continue
        for line in sh([NM, os.path.join(out, f)]).stdout.splitlines():
            w = line.split()
            if len(w) == 2 and w[0] == "U": und.add(w[1])
            elif len(w) == 3 and w[1] not in "wWvV": dfn.add(w[2])
            elif len(w) == 3: check(False, "a weak symbol in " + f + ": " + w[2])
    left = {s for s in und - dfn if s.startswith("nxdk_")}
    check(left == {"nxdk_guest_ptr", "nxdk_on_present", "nxdk_shader_pack"}, "undefined nxdk_ symbols of the backend: " + " ".join(sorted(left)))
    po = sh([NM, "-u", os.path.join(out, "dk_present.o")]).stdout.split()
    check({"nxdk_scale_point", "nxdk_sync_begin", "nxdk_sync_end", "nxdk_stage_alloc"} <= set(po), "dk_present.o does not use the sync / scale hooks: " + " ".join(po))

# ---- c ----
consts = os.path.join(tmp, "consts.c")
open(consts, "w").write('#include <stdio.h>\n#include "dk_fmt.h"\nint main(void) { printf("%u %u %u\\n", (unsigned)DkBarrier_Full,'
                        ' (unsigned)(DkInvalidateFlags_Image | DkInvalidateFlags_L2Cache), (unsigned)(DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached)); return 0; }\n')
b = sh(["gcc", "-std=gnu11", "-w"] + INC + [consts, os.path.join(NXD, "dk_fmt.c"), "-o", os.path.join(tmp, "consts")]); check(b.returncode == 0, "consts build: " + tail(b.stderr))
FULL, IMGL2, MEMF = (int(x) for x in sh([os.path.join(tmp, "consts")]).stdout.split()) if b.returncode == 0 else (0, 0, 0)
FW, FH = 40, 30
def base(x, y, c): return (x * 3 + y * 5 + c * 11 + 99) & 255
def spat(x, y, c, ramp): return (x * 7 + y * 13 + c * 31 + ramp * 5) & 255
RAMP = [((i * 257 + c * 4099 + (i >> 3) * 31) ^ (c << 9)) & 0xFFFF for c in range(3) for i in range(256)]
def fnv(bs):
    h = 2166136261
    for x in bs: h = ((h ^ x) * 16777619) & 0xFFFFFFFF
    return h
RAMPH = "%08x" % fnv(b"".join(v.to_bytes(2, "little") for v in RAMP))
def cursor(kind, k, n):
    c = [(i * 37 + k * 11 + (i >> 5)) & 255 for i in range(n)]
    for i in range(3, n, 4):
        if kind == "p": c[i] = ((i >> 2) * 41 + k) & 255 if i & 8 else (255 if i & 4 else 0)
        elif kind == "a": c[i] = 255
        elif kind == "z": c[i] = 0
    return c
# images: id, type, fmt, w, h, samples
IMGS = {0: (0, 1, 64, 48, 1), 1: (0, 1, 64, 48, 4), 2: (0, 1, 64, 48, 2), 3: (0, 1, 64, 48, 8), 4: (0, 17, 64, 48, 2), 5: (1, 1, 64, 48, 1), 6: (0, None, 64, 48, 1),
        7: (0, 1, 64, 48, 1), 8: (0, 1, 64, 48, 1), 9: (0, 17, 64, 48, 1), 10: (0, 2, 32, 32, 2)}

class Host:
    def __init__(self, exe):
        self.p = subprocess.Popen([exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  env=dict(os.environ, ASAN_OPTIONS="detect_leaks=1", UBSAN_OPTIONS="print_stacktrace=1"))
    def cmd(self, line):
        self.p.stdin.write(line + "\n"); self.p.stdin.flush(); ev = []
        while True:
            o = self.p.stdout.readline()
            if not o: raise RuntimeError("host died: " + tail(self.p.stderr.read(), 6))
            o = o.rstrip("\n")
            if o.startswith("EV "): ev.append(o[3:])
            else: return o, ev
    def close(self):
        self.p.stdin.close(); rc = self.p.wait(timeout=60); return rc, self.p.stderr.read()

def norm(e): return re.sub(r"(blk|idx)=\d+", r"\1=@", e)

def clip(P):
    """the visible cursor rect (x0, y0, w, h) or None"""
    if P["cur"] in ("none", "nullp") or P["cw"] == 0 or P["ch"] == 0: return None
    x0, y0, x1, y1 = max(P["cx"], 0), max(P["cy"], 0), min(P["cx"] + P["cw"], FW), min(P["cy"] + P["ch"], FH)
    return (x0, y0, x1 - x0, y1 - y0) if x0 < x1 and y0 < y1 else None

def model(P, f0, tmpbase, secbase, rd_after=True):
    """(events, final front) of a successful present"""
    ev, f = [], list(f0)
    sec = secbase; im = IMGS[P["img"]]; sid = P["img"] + 1; ev.append("sync_begin n=%d" % sec)
    tid = None
    if im[4] > 1:
        tid = 101 + tmpbase; ev += ["img_storage id=%d w=%d h=%d fmt=%d samples=1 mips=1 layers=1" % (tid, im[2], im[3], im[1]), "resolve src=%d dst=%d" % (sid, tid),
                                    "barrier mode=%d flags=%d" % (FULL, IMGL2)]; sid = tid
    s, d = P["s"], P["d"]
    ev += ["stage_alloc bytes=816", "scale src=%d:%d:0:0:%d:%d:%d:%d dst=0:0:0:0:%d:%d:%d:%d ramp=%s" % ((sid, P["img"]) + tuple(s) + tuple(d) + (RAMPH if P["ramp"] else "none",))]
    for j in range(d[3]):
        for i in range(d[2]):
            if d[0] + i < FW and d[1] + j < FH:
                for c in range(4): f[((d[1] + j) * FW + d[0] + i) * 4 + c] = spat(d[0] + i, d[1] + j, c, 1 if P["ramp"] else 0)
    v = clip(P)
    if v:
        x0, y0, w, h = v; box = "box im=1 lvl=0 lay=0 %d,%d,0,%d,%d,1" % (x0, y0, w, h)
        ev += ["block_create size=%d flags=%d" % ((w * h * 4 + 4095) // 4096 * 4096, MEMF), box.replace("im=1", "im=0"), "barrier mode=%d flags=%d" % (FULL, IMGL2),
               "i2b im=0 rect=%d,%d,0,%d,%d,1 blk=@ off=0 rl=0 ih=0 flags=0" % (x0, y0, w, h)]
    ev += ["sync_end n=%d" % sec, "stage_free n=1"]
    if tid: ev.append("img_release id=%d" % tid)
    if v:
        x0, y0, w, h = v; sec += 1; cur = cursor(P["cur"][0], int(P["cur"][1:]), P["cw"] * P["ch"] * 4); data = []
        for j in range(h):
            for i in range(w):
                o = ((y0 - P["cy"] + j) * P["cw"] + (x0 - P["cx"] + i)) * 4; sp = cur[o:o + 4]; a = sp[3]; q = ((y0 + j) * FW + x0 + i) * 4
                for c in range(3):
                    val = sp[2 - c]
                    if P["ramp"]: val = RAMP[c * 256 + val] >> 8
                    f[q + c] = (val * a + f[q + c] * (255 - a) + 127) // 255
                data += f[q:q + 4]
        dh = bytes(data).hex() if len(data) <= 1024 else "fnv:%08x" % fnv(data)
        ev += ["sync_begin n=%d" % sec, "stage_alloc bytes=%d" % (w * h * 4), "box im=0 lvl=0 lay=0 %d,%d,0,%d,%d,1" % (x0, y0, w, h),
               "b2i im=0 rect=%d,%d,0,%d,%d,1 blk=@ off=0 rl=0 ih=0 flags=0 data=%s" % (x0, y0, w, h, dh), "block_destroy idx=@ kind=own", "sync_end n=%d" % sec, "stage_free n=1"]
    return ev, f

def front0(): return [base(x, y, c) for y in range(FH) for x in range(FW) for c in range(4)]

def scenarios(exe):
    fl, n = [], [0]
    def ck(ok, msg):
        n[0] += 1
        if not ok: fl.append(msg)
    h = Host(exe)
    for i, im in IMGS.items():
        if im[1] is None: h.cmd("cfmt"); cf = int(h.cmd("cfmt")[0].split()[1]); h.cmd("img %d %d %d %d %d %d" % (i, im[0], cf, im[2], im[3], im[4]))
        else: h.cmd("img %d %d %d %d %d %d" % (i, im[0], im[1], im[2], im[3], im[4]))
    h.cmd("view 7"); h.cmd("dead 8")
    st = {"sec": 0, "tmp": 0, "front": None}
    def reset(): h.cmd("front %d %d" % (FW, FH)); st["front"] = front0()
    def run(P, cur_override=None):
        arg = "present %d %s %s %d %d %d %d %s %s" % (P["img"], " ".join(str(v) for v in P["s"]), " ".join(str(v) for v in P["d"]), P["cx"], P["cy"], P["cw"], P["ch"],
                                                     "p" if P["ramp"] else "none", cur_override or P["cur"])
        r, ev = h.cmd(arg); m = re.match(r"R 0 E (\d+) M (.*)$", r)
        if not m: raise RuntimeError("bad reply: " + r)
        fr, _ = h.cmd("dumpfront 0 0 %d %d" % (FW, FH)); s, _ = h.cmd("state")
        return int(m.group(1)), m.group(2), ev, bytes.fromhex(fr[2:]), s
    def inv(ev, s, what):   # every run: balanced sections, blocks and images, nothing live afterwards, no use after the free
        nb = sum(1 for e in ev if e.startswith("sync_begin")); ne = sum(1 for e in ev if e.startswith("sync_end"))
        mk = sum(1 for e in ev if e.startswith("block_create")); dt = sum(1 for e in ev if e.startswith("block_destroy"))
        im = sum(1 for e in ev if e.startswith("img_storage")); ir = sum(1 for e in ev if e.startswith("img_release"))
        ck(nb == ne and mk == dt and im == ir and s == "S live 0 tmp 0 open 0" and not any("LIFETIME" in e for e in ev),
           "%s: sections %d/%d blocks %d/%d images %d/%d state %s" % (what, nb, ne, mk, dt, im, ir, s))
    D = (0, 0, FW, FH)
    rects = [((0, 0, 64, 48), D), ((5, 6, 17, 9), (3, 4, 20, 11)), ((0, 0, 1, 1), (39, 29, 1, 1)), ((10, 10, 30, 30), (0, 0, 40, 30)), ((0, 0, 64, 48), (7, 8, 9, 10))]
    # 1: the main path: scale rects x ramp on/off x cursor kinds; the exact sequence, the final front, the exact uploaded bytes
    curs = [(10, 7, 8, 6, "p3"), (-3, 5, 8, 6, "p5"), (12, -2, 8, 6, "p7"), (36, 9, 8, 6, "p1"), (14, 27, 8, 6, "p2"), (-3, -2, 8, 6, "p4"), (36, 27, 8, 6, "p6"),
            (-5, -5, 50, 40, "p9"), (0, 0, 40, 30, "a1"), (3, 3, 5, 5, "z2"), (0, 0, 1, 1, "p0"), (39, 29, 1, 1, "p8"), (-1, -1, 2, 2, "p3")]
    k = 0
    for ri, (s, d) in enumerate(rects):
        for ramp in (False, True):
            for cx, cy, cw, ch, cu in (curs if ri == 0 else [curs[(k * 3) % len(curs)], curs[(k * 3 + 1) % len(curs)]]):
                k += 1; P = dict(img=0, s=s, d=d, ramp=ramp, cx=cx, cy=cy, cw=cw, ch=ch, cur=cu); reset()
                want, wf = model(P, st["front"], 0, st["sec"] + 1); e, msg, ev, fr, s_ = run(P)
                ck(e == 0 and [norm(x) for x in ev] == want, "present %s:\n  got  %s\n  want %s" % (P, [norm(x) for x in ev], want))
                ck(list(fr) == wf, "front after %s differs from the reference model" % (P,)); inv(ev, s_, "present %s" % (P,))
                ib = [re.search(r"blk=(\d+)", x).group(1) for x in ev if x.startswith("i2b")]; db = [re.search(r"idx=(\d+)", x).group(1) for x in ev if "kind=own" in x]
                bb = [re.search(r"blk=(\d+)", x).group(1) for x in ev if x.startswith("b2i")]
                ck(ib == db and (not ib or bb != ib), "the read-back block is the one destroyed, the upload block is another: %s %s %s" % (ib, db, bb))
                st["sec"] += 2 if clip(P) else 1
    # 2: cursor outside / NULL / size 0 with NULL: pass 1 only
    for cx, cy, cw, ch, cu in [(40, 0, 8, 6, "p1"), (0, 30, 8, 6, "p1"), (-8, 0, 8, 6, "p1"), (0, -6, 8, 6, "p1"), (-100, -100, 8, 6, "p1"), (2147483647, 0, 8, 6, "p1"),
                               (-2147483647, 5, 8, 6, "p1"), (0, 2147483647, 8, 6, "p1"), (3, 3, 8, 6, "none"), (3, 3, 8, 6, "nullp"), (3, 3, 0, 0, "none"), (3, 3, 9999, 0, "none")]:
        P = dict(img=0, s=(0, 0, 64, 48), d=D, ramp=True, cx=cx, cy=cy, cw=cw, ch=ch, cur=cu); reset(); want, wf = model(P, st["front"], 0, st["sec"] + 1)
        e, msg, ev, fr, s_ = run(P); ck(e == 0 and [norm(x) for x in ev] == want and list(fr) == wf and not any(x.startswith("block_create") for x in ev),
                                        "no GPU work for the cursor %s: %s" % ((cx, cy, cw, ch, cu), [norm(x) for x in ev])); inv(ev, s_, "outside %s" % ((cx, cy),)); st["sec"] += 1
    # 3: multisampled sources: 2x and 4x resolved into a temporary image released after the wait; the scale reads the temporary
    for img, tm in ((1, 0), (2, 1)):
        P = dict(img=img, s=(0, 0, 64, 48), d=D, ramp=False, cx=4, cy=4, cw=6, ch=6, cur="p2"); reset(); want, wf = model(P, st["front"], st["tmp"], st["sec"] + 1)
        e, msg, ev, fr, s_ = run(P); ck(e == 0 and [norm(x) for x in ev] == want and list(fr) == wf, "msaa image %d: %s" % (img, [norm(x) for x in ev])); inv(ev, s_, "msaa %d" % img)
        st["sec"] += 2; st["tmp"] += 1
    # 4: refusals: ONE error, nothing recorded, the front unchanged
    base_ = dict(img=0, s=(0, 0, 64, 48), d=D, ramp=False, cx=4, cy=4, cw=6, ch=6, cur="p2")
    for name, ch_, text, over in [("8x source", dict(img=3), "samples", None), ("2x source without 2D engine", dict(img=4), "2D engine", None),
                                  ("cube source", dict(img=5), "bad image", None), ("compressed source", dict(img=6), "bad image", None), ("view source", dict(img=7), "bad image", None),
                                  ("dead source", dict(img=8), "bad image", None), ("cursor width 0", dict(cw=0), "cursor size", None), ("cursor height 0", dict(ch=0), "cursor size", None),
                                  ("cursor width 4097", dict(cw=4097, ch=1), "cursor size", None), ("cursor height 4097", dict(cw=1, ch=4097), "cursor size", None),
                                  ("NULL present", {}, "bad image", "nullpresent"), ("NULL source", {}, "bad image", "nullsrc")]:
        P = dict(base_); P.update(ch_); reset(); e, msg, ev, fr, s_ = run(P, over)
        ck(e == 1 and text in msg and ev == [] and list(fr) == st["front"], "refused '%s': %s %s %s" % (name, e, msg, ev)); inv(ev, s_, name)
    for img, ok in ((9, True), (10, True)):   # a single-sample non-2D-engine format and a 2x RGBA8 of another size are accepted
        P = dict(base_); P.update(img=img, s=(0, 0, 32, 32)); reset(); want, wf = model(P, st["front"], st["tmp"], st["sec"] + 1); e, msg, ev, fr, s_ = run(P)
        ck(e == 0 and [norm(x) for x in ev] == want and list(fr) == wf, "image %d accepted: %s" % (img, [norm(x) for x in ev])); st["sec"] += 2; st["tmp"] += (1 if img == 10 else 0)
    # 5: failures of the services, pass by pass: ONE error, balanced, what was done stays done, the front is what the executed passes made
    P = dict(img=0, s=(5, 6, 17, 9), d=(3, 4, 20, 11), ramp=True, cx=10, cy=7, cw=8, ch=6, cur="p3")
    def fail(kind, nth, text, keep, last, name, p=P):   # keep: events of the full run kept (the first `keep`), then `last`; the front = model with scale only (or none)
        reset(); h.cmd("fail %s %d" % (kind, nth)); full, wf = model(p, st["front"], st["tmp"], st["sec"] + 1); e, msg, ev, fr, s_ = run(p); h.cmd("fail %s 0" % kind)
        ck(e == 1 and text in msg and [norm(x) for x in ev] == full[:keep] + last, "%s: %s %s\n  got  %s\n  want %s" % (name, e, msg, [norm(x) for x in ev], full[:keep] + last))
        inv(ev, s_, name); return fr, wf
    scale_only = model(dict(P, cur="none"), front0(), 0, 1)[1]
    fr, _ = fail("sync", 1, "command memory", 0, [], "sync_begin 1 refused"); ck(list(fr) == st["front"], "front unchanged after sync_begin 1 refused"); st["sec"] += 0
    fr, _ = fail("sync", 2, "command memory", 9, ["block_destroy idx=@ kind=own"], "sync_begin 2 refused"); ck(list(fr) == scale_only, "pass 1 done, no cursor"); st["sec"] += 1
    fr, _ = fail("end", 1, "fence wait", 9, ["block_destroy idx=@ kind=own"], "sync_end 1 failed"); ck(list(fr) == st["front"], "nothing executed after a failed wait"); st["sec"] += 1
    fr, _ = fail("end", 2, "fence wait", 16, [], "sync_end 2 failed")
    ck(list(fr) == scale_only, "the upload was not executed after its failed wait"); st["sec"] += 2
    fr, _ = fail("scale", 1, "scale: refused", 0, ["sync_begin n=%d" % (st["sec"] + 1), "sync_end n=%d" % (st["sec"] + 1), "stage_free n=0"], "scale refused")
    ck(list(fr) == st["front"], "front unchanged after a refused scale"); st["sec"] += 1
    reset(); h.cmd("fail block 1"); e, msg, ev, fr, s_ = run(P); h.cmd("fail block 0")
    want = model(dict(P, cur="none"), front0(), 0, st["sec"] + 1)[0]   # the scale pass, without the read-back copy
    ck(e == 1 and "cursor block" in msg and [norm(x) for x in ev] == want and list(fr) == scale_only, "read-back block refused: %s %s %s" % (e, msg, [norm(x) for x in ev])); inv(ev, s_, "block refused"); st["sec"] += 1
    reset(); h.cmd("fail stage 2"); full, _ = model(P, st["front"], 0, st["sec"] + 1); e, msg, ev, fr, s_ = run(P); h.cmd("fail stage 0")
    want = full[:9] + ["sync_begin n=%d" % (st["sec"] + 2), "block_destroy idx=@ kind=own", "sync_end n=%d" % (st["sec"] + 2), "stage_free n=0"]
    ck(e == 1 and "staging" in msg and [norm(x) for x in ev] == want and list(fr) == scale_only, "upload staging refused: %s %s %s" % (e, msg, [norm(x) for x in ev])); inv(ev, s_, "stage 2 refused"); st["sec"] += 2
    reset(); h.cmd("fail stage 1"); e, msg, ev, fr, s_ = run(P); h.cmd("fail stage 0")
    ck(e == 1 and "staging" in msg and [norm(x) for x in ev] == ["sync_begin n=%d" % (st["sec"] + 1), "sync_end n=%d" % (st["sec"] + 1), "stage_free n=0"] and list(fr) == st["front"],
       "scale staging refused: %s %s %s" % (e, msg, ev)); inv(ev, s_, "stage 1 refused"); st["sec"] += 1
    Pm = dict(P, img=1)
    reset(); h.cmd("fail img 1"); e, msg, ev, fr, s_ = run(Pm); h.cmd("fail img 0")
    ck(e == 1 and "storage" in msg and [norm(x) for x in ev] == ["sync_begin n=%d" % (st["sec"] + 1), "sync_end n=%d" % (st["sec"] + 1), "stage_free n=0"] and list(fr) == st["front"],
       "temporary image refused: %s %s %s" % (e, msg, ev)); inv(ev, s_, "img refused"); st["sec"] += 1
    reset(); h.cmd("fail end 1"); e, msg, ev, fr, s_ = run(Pm); h.cmd("fail end 0")
    ck(e == 1 and ev[-2] == "img_release id=%d" % (101 + st["tmp"]) and list(fr) == st["front"], "msaa: failed wait still releases the temporary image: %s" % ev); inv(ev, s_, "msaa end"); st["sec"] += 1; st["tmp"] += 1
    # a present after all the failures still works (nothing stayed half open)
    P2 = dict(img=0, s=(0, 0, 64, 48), d=D, ramp=False, cx=1, cy=1, cw=3, ch=3, cur="p1"); reset(); want, wf = model(P2, st["front"], st["tmp"], st["sec"] + 1); e, msg, ev, fr, s_ = run(P2)
    ck(e == 0 and [norm(x) for x in ev] == want and list(fr) == wf, "a present after the failures")
    rc, err = h.close(); ck(rc == 0 and not err.strip(), "host exit %d: %s" % (rc, tail(err, 6)))
    return n[0], fl

def host_build(src, exe):
    return sh(["gcc", "-std=gnu11", "-g", "-O1", "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined", "-Wall", "-Wextra", "-Werror"] + INC +
              [os.path.join(HERE, "host_main.c"), src, os.path.join(NXD, "dk_fmt.c"), "-o", exe])

def run_host(src, tag):
    exe = os.path.join(tmp, "host_" + tag); b = host_build(src, exe)
    if b.returncode: return None, ["build: " + tail(b.stderr, 6)]
    try: return scenarios(exe)
    except Exception as e: return None, ["host: " + str(e)[:300]]

n, bad = run_host(os.path.join(NXD, "dk_present.c"), "base")
for b_ in (bad or [])[:6]: check(False, "host run: " + b_)
if n is not None and not bad: print("checks passed %d/%d (ASan + UBSan)" % (n, n))

# ---- d ----
BOX1 = "if (pc_block(w * h * 4, &rd)) {\n            const uint32_t box[6] = { (uint32_t)x0, (uint32_t)y0,"
BOX2 = "(const uint8_t*)dkMemBlockGetCpuAddr(rd), wr);\n        const uint32_t box[6] = { (uint32_t)x0, (uint32_t)y0,"
MUT = [
    ('ramp shift 7', 'ramp[c * 256 + v] >> 8', 'ramp[c * 256 + v] >> 7'), ('ramp ignored in the blend', 'if (ramp) v = ', 'if (0) v = '),
    ('rounding 126', '+ 127) / 255);', '+ 126) / 255);'), ('dest weight 256', 'd[c] * (255 - a)', 'd[c] * (256 - a)'), ('alpha not kept', 'o[3] = d[3];', 'o[3] = 255;'),
    ('channel order', 'v = s[2 - c];', 'v = s[c];'), ('alpha from the wrong byte', 'const uint32_t a = s[3];', 'const uint32_t a = s[0];'),
    ('cursor row pitch', '(y0 - p->cy + j) * p->cw', '(y0 - p->cy + j) * p->ch'), ('cursor column offset', '(size_t)(x0 - p->cx + i)', '(size_t)(x0 + i)'),
    ('alpha channel blended', 'c < 3; c++', 'c < 4; c++'), ('left clip dropped', 'p->cx < 0 ? 0 : p->cx', 'p->cx'), ('top clip dropped', 'p->cy < 0 ? 0 : p->cy', 'p->cy'),
    ('right clip dropped', 'if (x1 > nxdk.front.w) x1', 'if (0) x1'), ('bottom clip dropped', 'if (y1 > nxdk.front.h) y1', 'if (0) y1'),
    ('empty rect counted visible', 'x0 < x1 && y0 < y1', 'x0 <= x1 && y0 < y1'), ('32-bit right edge', '(int64_t)p->cx + p->cw', '(int32_t)p->cx + p->cw'),
    ('cursor width bound', 'p->cw > 4096', 'p->cw > 4097'), ('cursor height bound', 'p->ch > 4096', 'p->ch > 4097'), ('cursor height 0 allowed', '!p->cw || !p->ch', '!p->cw'),
    ('read-back block uncached flag lost', 'DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached', 'DkMemBlockFlags_CpuUncached'),
    ('block size unaligned', '(bytes + DK_MEMBLOCK_ALIGNMENT - 1) / DK_MEMBLOCK_ALIGNMENT * DK_MEMBLOCK_ALIGNMENT', 'bytes'),
    ('block refusal silent', 'nxdk_err("present: deko3d refused the cursor block of %u bytes", bytes); return NULL;', 'return NULL;'),
    ('resolve only above 2x', 'src->samples > 1) { bad', 'src->samples > 2) { bad'), ('8x accepted', 'src->samples > 4 ||', 'src->samples > 8 ||'),
    ('2D engine check dropped', '& DkImageFlags_Usage2DEngine', '| DkImageFlags_Usage2DEngine'), ('resolve target multisampled', 'tmp->samples = 1;', ''),
    ('resolve dropped', 'dkCmdBufResolveImage(cmd, &sv, &dv);', ''), ('resolve target is the source', 'dkImageViewDefaults(&dv, &tmp->dk);', 'dkImageViewDefaults(&dv, &src->dk);'),
    ('scale reads the unresolved source', 'use = &tmp;', 'use = src;'), ('temporary image leaked', '    nxdk_img_release(&tmp);\n', ''),
    ('failed wait ignored', 'bad |= nxdk_sync_end(&sy);', 'nxdk_sync_end(&sy);'), ('scale error ignored', 'bad = nxdk.errCount != e0;', 'bad = 0;'),
    ('source x / y swapped', '(uint32_t)p->sx, (uint32_t)p->sy, (uint32_t)p->sw', '(uint32_t)p->sy, (uint32_t)p->sx, (uint32_t)p->sw'),
    ('dest w / h swapped', '(uint32_t)p->dw, (uint32_t)p->dh }', '(uint32_t)p->dh, (uint32_t)p->dw }'), ('ramp not given to the scale', '&s, &d, gamma);', '&s, &d, NULL);'),
    ('ramp not given to the cursor', 'pc_cursor_upload(p, cursor, gamma,', 'pc_cursor_upload(p, cursor, NULL,'),
    ('read-back block leaked', '    dkMemBlockDestroy(rd);   /* the read-back', '    (void)rd;   /* the read-back'),
    ('read-back leaked on failure', 'if (bad || !vis) { if (rd) dkMemBlockDestroy(rd); return; }', 'if (bad || !vis) { return; }'),
    ('read-back leaked when the upload cannot start', 'if (nxdk_sync_begin(&up)) { dkMemBlockDestroy(rd); return; }', 'if (nxdk_sync_begin(&up)) { return; }'),
    ('read-back destroyed before its wait', 'bad |= nxdk_sync_end(&sy);', 'if (rd) { dkMemBlockDestroy(rd); rd = NULL; } bad |= nxdk_sync_end(&sy);'),
    ('copy barrier dropped', "            dkCmdBufBarrier(sy.cmd, DkBarrier_Full, DkInvalidateFlags_Image | DkInvalidateFlags_L2Cache);\n", ''),
    ('read-back copy flags', 'dkCmdBufCopyImageToBuffer(sy.cmd, &v, &r, &cb, 0);', 'dkCmdBufCopyImageToBuffer(sy.cmd, &v, &r, &cb, 1);'),
    ('read-back box x / y swapped', BOX1, BOX1.replace("(uint32_t)x0, (uint32_t)y0", "(uint32_t)y0, (uint32_t)x0")),
    ('upload box x / y swapped', BOX2, BOX2.replace("(uint32_t)x0, (uint32_t)y0", "(uint32_t)y0, (uint32_t)x0")),
    ('upload block too small', 'nxdk_stage_alloc(w * h * 4)', 'nxdk_stage_alloc(w * h)'), ('upload copy flags', 'dkCmdBufCopyBufferToImage(up.cmd, &cb, &v, &r, 0);', 'dkCmdBufCopyBufferToImage(up.cmd, &cb, &v, &r, 1);'),
    ('read-back block too small', 'pc_block(w * h * 4, &rd)', 'pc_block(w * h * 3, &rd)'), ('sync_begin failure ignored', 'if (nxdk_sync_begin(&sy)) return;', 'nxdk_sync_begin(&sy);'),
    ('cube accepted', ' || src->type != NxImgType_2D', ''), ('dead image accepted', '!src->used ||', ''), ('view accepted', 'src->isView ||', ''),
    ('compressed accepted', '(dk_fmt(src->fmt).flags & DKFMT_COMPRESSED)', '0'), ('NULL present accepted', 'if (!p || !src', 'if (!src'),
    ('size check without a cursor', 'if (cursor && (!p->cw', 'if (!cursor && (!p->cw'), ('block refusal continues', '} else bad = 1;', '}'),
    ('NULL cursor drawn', 'const int vis = cursor && x0 < x1', 'const int vis = x0 < x1'), ('resolve failure ignored', 'bad = pc_resolve(sy.cmd, src, &tmp);', 'pc_resolve(sy.cmd, src, &tmp);'),
    ('temporary storage failure ignored', 'if (nxdk_img_storage(tmp)) return 1;', 'nxdk_img_storage(tmp);'),
    ('upload staging failure ignored', 'if (wr) {\n        pc_blend', 'if (1) {\n        pc_blend'),
]
caught = 0
if "--no-mutations" not in sys.argv and n is not None and not bad:
    def one(i):
        name, a, b = MUT[i]
        if SRC.count(a) != 1: return i, None, "anchor occurs %d times" % SRC.count(a)
        p = os.path.join(tmp, "mut%d.c" % i); open(p, "w").write(SRC.replace(a, b))
        mn, mbad = run_host(p, "m%d" % i)
        return i, (mn is None or bool(mbad)), (mbad or ["?"])[0][:70]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        for i, c, why in ex.map(one, range(len(MUT))):
            if c is None: check(False, "mutation '%s': %s" % (MUT[i][0], why))
            elif c: caught += 1; print("  mutation %-46s caught (%s)" % (MUT[i][0], why))
            else: check(False, "mutation '%s' NOT CAUGHT" % MUT[i][0])
    print("d  mutations caught %d/%d" % (caught, len(MUT)))
    check(caught == len(MUT) and len(MUT) >= 30, "a mutation was not caught")
shutil.rmtree(tmp, ignore_errors=True)
print("ALL OK" if not fails else "FAILED %d" % len(fails))
sys.exit(1 if fails else 0)
