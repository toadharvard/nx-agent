"""Binary test record shared by gen.py / oracle.py (python) and vs_common.h (C): a flat array of 32-bit words, floats as
IEEE bit patterns. Field order = FIELDS; vs_common.h `VsRec` has the same order (the driver prints its word count and
test_ffp_vs.py checks it). One record = one vertex under one full D3D9 state."""
import struct
# (name, number of words, kind)  kind: u = uint32, f = float32
FIELDS = [
    ("decl", 20, "u"),      # NxFfpVsDecl words (ffp_vs_state.h): DECL names below, nTex at 10..17
    ("rs", 210, "u"),       # D3DRS_* values (float states as bit patterns)
    ("tcIndex", 8, "u"), ("ttff", 8, "u"),
    ("view", 16, "f"), ("proj", 16, "f"), ("world0", 16, "f"), ("texm", 128, "f"), ("clip", 24, "f"),
    ("mat", 20, "f"),       # diffuse4 ambient4 specular4 emissive4 power pad3
    ("vp", 4, "f"),         # X Y Width Height
    ("nLights", 1, "u"), ("lights", 16 * 26, "u"),   # NxFfpD3dLight x16: type(u) diffuse4 specular4 ambient4 pos3 dir3 range falloff a0 a1 a2 theta phi
    ("posfix", 4, "f"),
    ("pos", 4, "f"), ("pos1", 4, "f"), ("n1", 4, "f"), ("bw", 4, "f"), ("bi", 4, "f"), ("n", 4, "f"),
    ("c0", 4, "f"), ("c1", 4, "f"), ("psize", 4, "f"), ("tex", 32, "f"),
    ("palette", 256, "f"),  # 16 matrices; the palette of the test is world[i] = P[i % 16]
]
WORDS = sum(n for _, n, _ in FIELDS)
OFF = {}
_o = 0
for _n, _c, _k in FIELDS: OFF[_n] = (_o, _c, _k); _o += _c
DECL = ["posT", "nBlendW", "blendIdx", "hasNormal", "hasPsize", "hasColor0", "hasColor1", "hasPos1", "hasNormal1"]  # decl[0..8]; nTex = decl[10..17]
LIGHT_W = 26

def f32(x): return struct.unpack("<f", struct.pack("<f", x))[0]
def bits(x): return struct.unpack("<I", struct.pack("<f", x))[0]
def fl(u): return struct.unpack("<f", struct.pack("<I", u & 0xFFFFFFFF))[0]

def pack(r):
    """r: dict field -> list of numbers (u: ints, f: floats)"""
    out = []
    for n, c, k in FIELDS:
        v = list(r[n]); assert len(v) == c, (n, len(v), c)
        out += [(x & 0xFFFFFFFF) if k == "u" else bits(x) for x in v]
    return struct.pack("<%dI" % WORDS, *out)

def unpack(b):
    w = struct.unpack("<%dI" % WORDS, b)
    return {n: [w[o + i] if k == "u" else fl(w[o + i]) for i in range(c)] for n, (o, c, k) in OFF.items()}
