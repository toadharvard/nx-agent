"""texoracle.py: the independent layout / environment oracle shared by tests/l6/tex and tests/l6/front (python, no C): the wined3d 9.0 format layout table (utils.c formats[],
format_block_info[], format_base_flags[]), the pitch / size rules, and the reader of the env tsv (CheckDeviceFormat / CheckDeviceMultiSampleType / GetDeviceCaps rows).
`layout(f)` is None exactly for the zero-size classes of wined3d (UNKNOWN, INST, RESZ, ATOC, NVDB, unmapped ids): formats that are capability probes, not creatable storage."""
import os, re, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
ENV = os.path.join(ROOT, "render", "d3d9", "env")
INV, NA, OOM, NOTIMPL, OOVM = 0x8876086C, 0x8876086A, 0x8007000E, 0x80004001, 0x8876017C


def fcc(s): return sum(ord(c) << (8 * i) for i, c in enumerate(s))


# ---------------------------------------------------------------- oracle
PLAIN = {20: 3, 21: 4, 22: 4, 23: 2, 24: 2, 25: 2, 26: 2, 27: 1, 28: 1, 29: 2, 30: 2, 31: 4, 32: 4, 33: 4, 34: 4, 35: 4, 36: 8, 40: 2, 41: 1, 50: 1, 51: 2, 52: 1, 60: 2, 61: 2, 62: 4, 63: 4, 64: 4, 67: 4,
         70: 2, 71: 4, 73: 2, 75: 4, 77: 4, 79: 4, 80: 2, 81: 2, 82: 4, 83: 4, 101: 2, 102: 4, 110: 8, 111: 2, 112: 4, 113: 8, 114: 4, 115: 8, 116: 16, 117: 2}
MAPPABLE = {70, fcc("ATI1"), fcc("ATI2"), fcc("INTZ")}


def layout(f):
    """(bytes, bw, bh, bb, blocks, noverify, broken, hs) or None (size 0)"""
    if f in PLAIN: return (PLAIN[f], 1, 1, PLAIN[f], False, False, False, (1, 1))
    t = {"UYVY": (2, 2, 1, 4, True, True, False, (1, 1)), "YUY2": (2, 2, 1, 4, True, True, False, (1, 1)), "YV12": (1, 1, 1, 1, False, False, False, (3, 2)), "NV12": (1, 1, 1, 1, False, False, False, (3, 2)),
         "DXT1": (1, 4, 4, 8, True, False, False, (1, 1)), "DXT2": (1, 4, 4, 16, True, False, False, (1, 1)), "DXT3": (1, 4, 4, 16, True, False, False, (1, 1)), "DXT4": (1, 4, 4, 16, True, False, False, (1, 1)),
         "DXT5": (1, 4, 4, 16, True, False, False, (1, 1)), "MET1": (1, 1, 1, 1, False, False, False, (1, 1)), "GRGB": (1, 1, 1, 1, False, False, False, (1, 1)), "RGBG": (1, 1, 1, 1, False, False, False, (1, 1)),
         "ATI1": (1, 4, 4, 8, True, True, True, (1, 1)), "ATI2": (1, 4, 4, 16, True, True, True, (1, 1)), "NVHU": (2, 1, 1, 2, False, False, False, (1, 1)), "NVHS": (2, 1, 1, 2, False, False, False, (1, 1)),
         "INTZ": (4, 1, 1, 4, False, False, False, (1, 1)), "NULL": (4, 1, 1, 4, False, False, False, (1, 1)), " R16": (2, 1, 1, 2, False, False, False, (1, 1)), "AL16": (4, 1, 1, 4, False, False, False, (1, 1))}
    for k, v in t.items():
        if fcc(k) == f: return v
    return None


def pitch(L, w, h):
    by, bw, bh, bb, blk, nv, brk, hs = L
    if blk: row = (-(-w // bw) * bb + 3) & ~3; sl = row * -(-h // bh)
    else: row = (by * w + 3) & ~3; sl = row * h
    return row, sl * hs[0] // hs[1]


def mappitch(L, w, h):
    if L[6]: return w * L[0], w * L[0] * h
    return pitch(L, w, h)


def size(L, w, h, d):
    return w * h * d * L[0] if L[6] else pitch(L, w, h)[1] * d


def dim(v, l): return max(1, v >> l)
def chain(*d): return max(d).bit_length()


class Env:
    def __init__(self, prof):
        self.cdf, self.ms, self.caps, self.vmem = {}, {}, {}, None
        for l in open(os.path.join(ENV, f"env_{prof}.tsv")):
            if l.startswith("CheckDeviceFormat(0,1,16,"):
                q, r, o = l.rstrip("\n").split("\t"); a = [int(x, 16) for x in re.match(r".*\((.*)\)", q).group(1).split(",")]; self.cdf[tuple(a[3:])] = int(r, 16)
            elif l.startswith("CheckDeviceMultiSampleType(0,1,"):
                q, r, o = l.rstrip("\n").split("\t"); a = [int(x, 16) for x in re.match(r".*\((.*)\)", q).group(1).split(",")]
                m = re.match(r"q=([0-9a-f]+)", o); self.ms[(a[2], a[3], a[4])] = (int(r, 16), int(m.group(1), 16) if m else 1)
            elif l.startswith("GetDeviceCaps(0,1)"):
                d = dict(t.split("=", 1) for t in l.rstrip("\n").split("\t")[2].split(" ") if "=" in t); self.caps = {k: int(d[k], 16) for k in ("MaxTextureWidth", "MaxTextureHeight")}
            elif l.startswith("DEV.GetAvailableTextureMem") and self.vmem is None: self.vmem = int(l.split("\t")[1], 16)

    def ok(self, usage, rtype, fmt): return self.cdf.get((usage, rtype, fmt)) == 0

    def ms_ok(self, fmt, t, q):
        if t == 0: return True
        if t > 16: return False
        r = self.ms.get((fmt, 0, t))
        if r is None or r[0] != 0: return False
        return q < r[1] if t == 1 else q in (0, 0xFFFFFFFF)


