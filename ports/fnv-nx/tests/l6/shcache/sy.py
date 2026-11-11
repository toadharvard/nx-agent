"""sy.py: synthetic inputs of test_shcache.py: hand-encoded SM3 shader bytecode (no game data) and a minimal D9S1 writer (STREAM.md) for the
calls d9s_shaders.py reads (Create*Shader, Set*Shader, Draw*, Present, Reset, state blocks)."""
import hashlib, json, os, struct

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
SPEC_PATH = os.path.join(ROOT, "render", "d3d9", "spec", "d3d9_spec.json")
USAGE = {n: i for i, n in enumerate(["position", "blendweight", "blendindices", "normal", "psize", "texcoord", "tangent", "binormal", "tessfactor", "positiont", "color", "fog", "depth", "sample"])}


def dst(rtype, num, mask=0xF): return 0x80000000 | (rtype & 7) << 28 | ((rtype >> 3) & 3) << 11 | mask << 16 | num
def src(rtype, num): return 0x80000000 | (rtype & 7) << 28 | ((rtype >> 3) & 3) << 11 | 0xE4 << 16 | num
def dcl(usage, index, d): return [31 | 2 << 24, 0x80000000 | USAGE[usage] | index << 16, d]


def shader(stage, decls, seed=0):
    """stage 'vs'|'ps' (3_0). vs: decls = output (usage, index) list, one input position v0, every output = v0 (+ seed constants so that
    different seeds give different bytecode). ps: decls = input (usage, index) list; oC0 = sum of inputs. Returns the bytes."""
    t = [0xFFFE0300 if stage == "vs" else 0xFFFF0300]
    if stage == "vs":
        t += dcl("position", 0, dst(1, 0))
        for k, (u, i) in enumerate(decls): t += dcl(u, i, dst(6, k))
        for k in range(len(decls)): t += [1 | 2 << 24, dst(6, k), src(1, 0)]
    else:
        for k, (u, i) in enumerate(decls): t += dcl(u, i, dst(1, k))
        t += [1 | 2 << 24, dst(0, 0), src(1, 0)]
        for k in range(1, len(decls)): t += [2 | 3 << 24, dst(0, 0), src(0, 0), src(1, k)]
        t += [1 | 2 << 24, dst(8, 0), src(0, 0)]
    for k in range(seed): t += [1 | 2 << 24, dst(0, 1 + k % 3), src(2, k)]
    return struct.pack("<%dI" % (len(t) + 1), *t, 0xFFFF)


class Writer:
    """STREAM.md writer for the calls above; ids are assigned by the test (objects named by their number)."""
    def __init__(s, spec_path=SPEC_PATH):
        raw = open(spec_path, "rb").read(); s.spec = json.loads(raw); s.names = sorted(s.spec["interfaces"])
        s.out = bytearray(b"D9S1" + struct.pack("<I", 1) + hashlib.sha256(raw).digest() + struct.pack("<I", 0)); s.seq = 0; s.nid = 0
        s.m = {(m["iface"], m["name"]): m for m in s.spec["methods"]}

    def rec(s, iface, name, this, ins=b"", ret=0, outs=b"", depth=0, new=(), thread=0, hdr_ret=True):
        m = s.m[(iface, name)]; body = bytearray(ins)
        if m["ret_kind"] != "void": body += struct.pack("<II", 0x100, ret & 0xFFFFFFFF)
        body += outs
        h = struct.pack("<IIIIII", s.seq, thread, s.names.index(iface) | m["slot"] << 16, this, depth, len(new))
        for i, f in new: h += struct.pack("<II", i, 0xFFFF if f is None else s.names.index(f))
        h += struct.pack("<I", 0)
        s.out += struct.pack("<I", 4 + len(h) + len(body)) + h + body; s.seq += 1

    @staticmethod
    def buf(b): return struct.pack("<II", 1, len(b)) + b + b"\0" * (-len(b) & 3)

    def create(s, dev, stage, bc, obj, hr=0, **kw):
        n = "CreateVertexShader" if stage == "vs" else "CreatePixelShader"; f = "IDirect3DVertexShader9" if stage == "vs" else "IDirect3DPixelShader9"
        s.rec("IDirect3DDevice9", n, dev, s.buf(bc) + struct.pack("<I", 1), hr, struct.pack("<II", 0x100, obj if hr >= 0 else 0), new=[(obj, f)] if hr >= 0 else (), **kw)

    def set(s, dev, stage, obj, hr=0, **kw): s.rec("IDirect3DDevice9", "SetVertexShader" if stage == "vs" else "SetPixelShader", dev, struct.pack("<I", obj), hr, **kw)
    def draw(s, dev, hr=0, **kw): s.rec("IDirect3DDevice9", "DrawPrimitive", dev, struct.pack("<III", 4, 0, 1), hr, **kw)
    def present(s, dev, **kw): s.rec("IDirect3DDevice9", "Present", dev, struct.pack("<IIII", 0, 0, 0, 0), 0, **kw)
    def create_sb(s, dev, typ, obj): s.rec("IDirect3DDevice9", "CreateStateBlock", dev, struct.pack("<II", typ, 1), 0, struct.pack("<II", 0x100, obj), new=[(obj, "IDirect3DStateBlock9")])
    def begin_sb(s, dev): s.rec("IDirect3DDevice9", "BeginStateBlock", dev)
    def end_sb(s, dev, obj): s.rec("IDirect3DDevice9", "EndStateBlock", dev, struct.pack("<I", 1), 0, struct.pack("<II", 0x100, obj), new=[(obj, "IDirect3DStateBlock9")])
    def sb(s, name, obj): s.rec("IDirect3DStateBlock9", name, obj)

    def reset(s, dev):
        pl = b"\0" * sum(f["size"] for f in s.spec["structs"]["D3DPRESENT_PARAMETERS"]["fields"])
        s.rec("IDirect3DDevice9", "Reset", dev, struct.pack("<I", 1) + pl, 0, struct.pack("<I", 0x101) + pl)
