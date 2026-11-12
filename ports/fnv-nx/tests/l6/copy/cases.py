"""cases.py: the python oracle of tests/l6/copy part a. Independent of the C++: it re-states the rules of the product file headers (copy_stretch.cpp S1-S9, copy_clear.cpp R-C1..R-C6 / ColorFill, copy_update.cpp
U1-U6 / T1-T6 / G1-G4, copy_misc.cpp V1-V3 / P1-P2, copy_dc.cpp D1-D4) over descriptors of the zoo (the objects the C++ creates), with the format facts of the env tsv (CheckDeviceFormatConversion,
CheckDeviceFormat) and of the wined3d layout table (texoracle). Writes zoo.txt and ops.txt; every op line ends with the expected HRESULT."""
import os, re, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
import texoracle as T
from texoracle import fcc
OK, INV, FAIL = 0, 0x8876086C, 0x80004005
UNSUPFILTER, CONFLICT = 0x88760822, 0x88760821
A8R8G8B8, X8R8G8B8, R5G6B5, A8, L8, YUY2, DXT1, NULL, D24S8, D16, D24X8, INTZ = 21, 22, 23, 28, 50, fcc("YUY2"), fcc("DXT1"), fcc("NULL"), 75, 80, 77, fcc("INTZ")
DEPTH = {70, 71, 73, 75, 77, 79, 80, 82, 83, fcc("INTZ"), fcc("DF24"), fcc("DF16")}
GDI = {20, 21, 22, 23, 24, 25}
PRO = "docked"


def env():
    conv, cdf, caps = {}, {}, {}
    for l in open(os.path.join(ROOT, "render", "d3d9", "env", f"env_{PRO}.tsv")):
        if l.startswith("CheckDeviceFormatConversion(0,1,"):
            q, r, o = l.rstrip("\n").split("\t"); a = [int(x, 16) for x in re.match(r".*\((.*)\)", q).group(1).split(",")]; conv[(a[2], a[3])] = int(r, 16)
        elif l.startswith("CheckDeviceFormat(0,1,16,"):
            q, r, o = l.rstrip("\n").split("\t"); a = [int(x, 16) for x in re.match(r".*\((.*)\)", q).group(1).split(",")]; cdf[tuple(a[3:])] = int(r, 16)
        elif l.startswith("GetDeviceCaps(0,1)"):
            caps = dict(t.split("=", 1) for t in l.rstrip("\n").split("\t")[2].split(" ") if "=" in t)
    return conv, cdf, {k: int(v, 16) for k, v in caps.items() if k in ("DevCaps", "StretchRectFilterCaps")}


CONV, CDF, CAPS = env()
NATIVE = {}   # D3DFORMAT -> native NxFormat (0 = none), from the compiled front (test_copy.py)


class O:
    """one zoo object: a surface (plain / rt / ds) or a level surface of a texture"""
    def __init__(s, id, kind, fmt, w, h, pool=0, rt=False, ds=False, ms=0, locked=False, tex=None, level=0, usage=0, levels=1, lockable=True):
        s.id, s.kind, s.fmt, s.w, s.h, s.pool, s.rt, s.ds, s.ms, s.locked, s.tex, s.level, s.usage, s.levels, s.lockable = id, kind, fmt, w, h, pool, rt, ds, ms, locked, tex, level, usage, levels, lockable
    @property
    def depth(s): return s.fmt in DEPTH
    @property
    def comp(s): return s.fmt in (fcc("DXT1"), fcc("DXT2"), fcc("DXT3"), fcc("DXT4"), fcc("DXT5"), fcc("ATI1"), fcc("ATI2"))
    @property
    def gpu(s): return s.pool == 0 and NATIVE.get(s.fmt, 0) != 0
    @property
    def null(s): return s.fmt == NULL


def zoo():
    Z = []; add = lambda *a, **k: Z.append(O(len(Z), *a, **k))
    for fmt, w in ((A8R8G8B8, 64), (A8R8G8B8, 32), (X8R8G8B8, 64), (DXT1, 64), (A8, 64), (YUY2, 64), (R5G6B5, 64)): add("plain", fmt, w, w)                       # 0-6 DEFAULT plain
    add("plain", A8R8G8B8, 16, 16, locked=True)                                                                                                            # 7 locked default plain
    for fmt, w in ((A8R8G8B8, 64), (A8R8G8B8, 32), (DXT1, 64), (A8, 64), (X8R8G8B8, 64)): add("plain", fmt, w, w, pool=2)                                      # 8-12 SYSTEMMEM plain
    add("plain", A8R8G8B8, 32, 32, pool=2, locked=True)                                                                                                    # 13 locked sysmem
    add("plain", A8R8G8B8, 64, 64, pool=3)                                                                                                                 # 14 SCRATCH
    for fmt, w, ms in ((A8R8G8B8, 64, 0), (A8R8G8B8, 32, 0), (X8R8G8B8, 64, 0), (R5G6B5, 64, 0), (NULL, 64, 0), (A8, 64, 0), (A8R8G8B8, 64, 4), (L8, 64, 0)): add("rt", fmt, w, w, rt=True, ms=ms, lockable=ms == 0)   # 15-22
    add("rt", A8R8G8B8, 64, 64, rt=True, lockable=False)                                                                                                   # 23 non-lockable RT
    for fmt, w in ((D24S8, 64), (D24S8, 32), (D16, 64), (D24X8, 64)): add("ds", fmt, w, w, ds=True)                                                          # 24-27
    for l in range(3): add("tex", A8R8G8B8, 64 >> l, 64 >> l, tex=0, level=l, levels=3)                                                                      # 28-30 default texture
    add("tex", A8R8G8B8, 64, 64, rt=True, tex=1, levels=1)                                                                                                 # 31 RT texture
    for l in range(3): add("tex", A8R8G8B8, 64 >> l, 64 >> l, pool=2, tex=2, level=l, levels=3)                                                             # 32-34 sysmem texture
    for l in range(4): add("tex", DXT1, 64 >> l, 64 >> l, pool=2, tex=3, level=l, levels=4)                                                                  # 35-38 sysmem DXT1
    for l in range(4): add("tex", DXT1, 64 >> l, 64 >> l, tex=4, level=l, levels=4)                                                                          # 39-42 default DXT1
    for l in range(3): add("tex", A8R8G8B8, 64 >> l, 64 >> l, pool=2, tex=5, level=l, levels=3)                                                             # 43-45 second sysmem texture
    return Z


def blocks(o):
    L = T.layout(o.fmt)
    return (L[1], L[2]) if L and L[4] else (1, 1)        # block size of the layout (YUY2 2x1, DXT1 4x4, ...)


def box_ok(o, r):
    if r is None: return True
    l, t, rr, b = r; bw, bh = blocks(o)
    if l < 0 or t < 0 or l >= rr or t >= b or rr > o.w or b > o.h: return False
    if (l % bw) or (t % bh) or ((rr % bw) and rr != o.w) or ((b % bh) and b != o.h): return False
    return True


def whole(o, r): return r is None or r == (0, 0, o.w, o.h)


def stretch(S, sr, D, dr, f, scene=False):
    if S.pool != 0 or D.pool != 0: return INV
    if f not in (0, 1, 2): return INV                                    # StretchRectFilterCaps = MIN/MAG POINT + LINEAR (asserted below)
    if S.depth != D.depth: return INV
    if D.kind != "plain" and not (D.rt or D.ds): return INV
    if D.kind == "plain" and S.kind != "plain": return INV
    if not box_ok(S, sr) or not box_ok(D, dr): return INV
    if D.depth and (scene or not whole(S, sr) or not whole(D, dr) or S.w != D.w or S.h != D.h or S.fmt != D.fmt): return INV
    a = sr or (0, 0, S.w, S.h); b = dr or (0, 0, D.w, D.h); scaled = (a[2] - a[0], a[3] - a[1]) != (b[2] - b[0], b[3] - b[1]); same = S.id == D.id
    if scaled and (same or D.kind == "plain" or S.comp or D.comp): return INV
    if same and max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3]): return INV
    if S.fmt != D.fmt and CONV.get((S.fmt, D.fmt), 1) != 0: return INV
    if S.null: return INV
    if not D.null and S.gpu != D.gpu and S.fmt != D.fmt: return INV
    if (S.comp or D.comp) and NATIVE.get(S.fmt, 0) != NATIVE.get(D.fmt, 0): return INV
    if S.locked or D.locked: return INV
    if D.ms > 1 and not D.null: return INV
    return OK


def colorfill(S, r):
    if S.pool != 0 or (S.kind == "tex" and not S.rt) or S.ds: return INV
    if r is not None and not box_ok(S, r): return INV
    if CDF.get((1, 1, S.fmt), 1) != 0: return INV                         # CheckDeviceFormat(RENDERTARGET, SURFACE)
    return OK


def update_surface(S, sr, D, pt):
    if D.pool != 0 or S.pool != 2: return INV
    if S.ms > 1 or D.ms > 1 or S.locked or D.locked: return INV
    if S.fmt != D.fmt or D.depth: return INV
    w = (sr[2] - sr[0]) if sr else S.w; h = (sr[3] - sr[1]) if sr else S.h; sx, sy = (sr[0], sr[1]) if sr else (0, 0); dx, dy = pt or (0, 0)
    if sr and (sr[0] < 0 or sr[1] < 0 or w <= 0 or h <= 0 or sr[2] > S.w or sr[3] > S.h): return INV
    if dx < 0 or dy < 0 or dx + w > D.w or dy + h > D.h: return INV
    bw, bh = blocks(S)
    if (bw, bh) != (1, 1):
        wb = w == S.w and w == D.w and h == S.h and h == D.h
        if dx % bw or dy % bh or sx % bw or sy % bh or (not wb and (w % bw or h % bh)): return INV
    if S.fmt in (fcc("ATI1"), fcc("ATI2")) and not (w == S.w == D.w and h == S.h == D.h): return INV
    return OK


def rt_data(S, D):
    if S.pool != 0 or D.pool != 2 or S.depth or D.locked: return INV
    if S.ms > 1: return INV
    if (S.w, S.h) != (D.w, D.h) or S.fmt != D.fmt or S.null: return INV
    return OK


def tex_groups(Z):
    g = {}
    for o in Z:
        if o.tex is not None: g.setdefault(o.tex, []).append(o)
    return g


def update_texture(A, B):
    """A, B = level lists of two textures (level 0 first)"""
    a, b = A[0], B[0]
    if a is b: return INV
    if a.pool != 2 or b.pool != 0: return INV
    if a.fmt != b.fmt: return INV
    ssz, dsz, skip = max(a.w, a.h), max(b.w, b.h), 0
    while ssz > dsz: ssz >>= 1; skip += 1
    if max(1, a.w >> skip) != b.w or max(1, a.h >> skip) != b.h or skip >= a.levels: return INV
    return OK


def clear(flags, nrect, has_rects, ds, rt0, hasrt0=True):
    """-> hr (R-C1, R-C2); the pixel effect is part b"""
    if nrect and not has_rects: nrect = 0
    if not nrect and has_rects: return OK
    flags &= 7
    if flags & 6:
        if ds is None: return INV
        if flags & 1 and rt0 and (ds.w < rt0.w or ds.h < rt0.h): return OK
    return OK


RECTS = {"-": None, "sub": (4, 4, 12, 12), "sub2": (8, 8, 24, 24), "flip": (12, 12, 4, 4), "out": (0, 0, 1000, 1000), "empty": (4, 4, 4, 8), "neg": (-4, 0, 8, 8), "full32": (0, 0, 32, 32), "odd": (1, 1, 7, 7)}
PTS = {"-": None, "p8": (8, 8), "neg": (-1, 0), "far": (60, 60), "p4": (4, 4)}


def r2s(r): return "-" if r is None else "%d %d %d %d" % r
def p2s(p): return "-" if p is None else "%d %d" % p


def write(d, native=None):
    global NATIVE
    if native is not None: NATIVE = dict(native)
    Z = zoo(); n = 0
    with open(os.path.join(d, "zoo.txt"), "w") as f:
        for o in Z:
            if o.kind == "tex" and (o.level == 0):
                f.write("T %d %d %d %d %d %d %d\n" % (o.tex, o.fmt, o.w, o.h, o.levels, o.pool, 1 if o.rt else 0))
            f.write("Z %d %s %d %d %d %d %d %d %d %d %d %d\n" % (o.id, o.kind, o.fmt, o.w, o.h, o.pool, int(o.rt), int(o.ds), o.ms, int(o.locked), -1 if o.tex is None else o.tex, o.level))
    g = tex_groups(Z)
    with open(os.path.join(d, "ops.txt"), "w") as f:
        def w(s): nonlocal n; f.write(s + "\n"); n += 1
        for S in Z:
            for D in Z:
                for sk, dk in (("-", "-"), ("sub", "sub"), ("sub", "sub2"), ("sub", "-"), ("-", "sub"), ("flip", "-"), ("-", "flip"), ("out", "-"), ("-", "out"), ("empty", "-"), ("sub2", "full32"), ("neg", "-"), ("odd", "odd")):
                    for fl in ((0, 1, 2, 3, 0xbadf) if (sk, dk) == ("-", "-") else (1,)):
                        w("SR %d %s %d %s %d %x" % (S.id, r2s(RECTS[sk]), D.id, r2s(RECTS[dk]), fl, stretch(S, RECTS[sk], D, RECTS[dk], fl)))
                for sk in ("-", "sub", "out", "empty", "neg"):
                    for pk in ("-", "p8", "neg", "far", "p4"):
                        w("US %d %s %d %s %x" % (S.id, r2s(RECTS[sk]), D.id, p2s(PTS[pk]), update_surface(S, RECTS[sk], D, PTS[pk])))
                w("RD %d %d %x" % (S.id, D.id, rt_data(S, D)))
            for rk in ("-", "sub", "flip", "out", "empty", "neg", "full32"):
                w("CF %d %s %x" % (S.id, r2s(RECTS[rk]), colorfill(S, RECTS[rk])))
            w("GD %d %x" % (S.id, OK if (S.fmt in GDI and S.ms <= 1 and not S.locked and not (S.tex is not None and any(o.locked for o in g[S.tex]))) else INV))
        for ta in g:
            for tb in g:
                w("UT %d %d %x" % (ta, tb, update_texture(g[ta], g[tb])))
        for S in Z:          # DS-depth StretchRect while in a scene
            for D in Z:
                if S.depth and D.depth: w("SRS %d %d %x" % (S.id, D.id, stretch(S, None, D, None, 1, scene=True)))
        # Clear: device state variants
        rts = [o for o in Z if o.kind == "rt" and not o.null and o.id != 21][:6]; dss = [None] + [o for o in Z if o.kind == "ds"]
        for rt in rts:
            for ds in dss:
                for flags in range(0, 9):
                    for nr, hr_ in ((0, 0), (1, 1), (2, 1), (1, 0), (0, 1)):
                        w("CL %d %d %d %d %d %x" % (rt.id, -1 if ds is None else ds.id, flags, nr, hr_, clear(flags, nr, bool(hr_), ds, rt)))
        # ValidateDevice moved to render/d3d9/dev/m1_validate.cpp (tests/l6/m1 part a)
        for k in range(3): w("PT %d %x" % (k, 0 if k < 2 else INV))   # Wine 9.0 d3d9 device.c: DrawRectPatch / DrawTriPatch return D3D_OK, DeletePatch D3DERR_INVALIDCALL (all three log a FIXME)
        # CreateOffscreenPlainSurfaceEx domain
        for pool in (0, 1, 2, 3, 4):
            for usage in (0, 1, 2, 3, 0x200, 0x10):
                for fmt in (A8R8G8B8, DXT1, YUY2, A8, 0, NULL, fcc("INST")):
                    for (cw, ch) in ((16, 16), (0, 4), (15, 15)):
                        w("CE %d %d %d %d %d" % (cw, ch, fmt, pool, usage))
    # part b: render-target formats and legal pairs (env conversion table), only formats the C++ reference knows
    known = {21, 22, 32, 33, 23, 24, 25, 26, 31, 35, 34, 36, 28, 50, 20, 60, 63, 64, 110, 111, 112, 113, 114, 115, 116, 101, 102}
    rtf = sorted(f for f in known if CDF.get((1, 1, f)) == 0 and NATIVE.get(f, 0))
    with open(os.path.join(d, "rtfmts.txt"), "w") as f: f.write("\n".join(str(x) for x in rtf) + "\n")
    with open(os.path.join(d, "pairs.txt"), "w") as f:
        for a in rtf:
            for b in rtf:
                if a != b and CONV.get((a, b)) == 0: f.write("%d %d\n" % (a, b))
    # caps facts the oracle rests on
    assert CAPS["StretchRectFilterCaps"] == 0x3000300 and not CAPS["DevCaps"] & 0x01C00000
    return n
