#!/usr/bin/env python3
"""envq.py: the ONE source of the environment query list. `envq.py OUT.h` writes envq.gen.h = C code (nested loops over the domains below) that
both envprobe2.c (mingw, real D3D9) and hostenv.cpp (our NxDirect3D9) include, so the two sides cannot ask different questions.
Domains are COMPLETE finite sets: every D3DFORMAT enum value of d3d9types.h + the FOURCC list + unknown values; every D3DDEVTYPE/D3DRESOURCETYPE/
D3DMULTISAMPLE_TYPE enum value + invalid ones; every single usage bit of the 32-bit word + all pairs of the bits that are not masked by Wine
(see render/d3d9/env/README.md for why single bits + pairs cover the usage function)."""
import os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.dont_write_bytecode = True
# the port tree: the checkout (this file at render/d3d9/env) or, in a recipe generator stage, the declared inputs mounted at /in/<repo path>
TREE = "/in/ports/fnv-nx" if HERE.startswith("/tool/") else os.path.normpath(os.path.join(HERE, "..", "..", ".."))
HDR = os.path.join(TREE, "shim", "include", "d3d9types.h")
fcc = lambda s: sum(ord(c) << (8 * i) for i, c in enumerate(s))


def d3dformat_enum():
    t = open(HDR).read(); t = t[t.index("typedef enum _D3DFORMAT"):]; t = t[:t.index("} D3DFORMAT;")]; out = []
    for m in re.finditer(r"D3DFMT_\w+\s*=\s*(?:MAKEFOURCC\('(.)',\s*'(.)',\s*'(.)',\s*'(.)'\)|(0x[0-9A-Fa-f]+|\d+))", t):
        out.append(fcc("".join(m.group(i) for i in range(1, 5))) if m.group(1) else int(m.group(5), 0))
    return out


FOURCC_LIST = ["DXT1", "DXT2", "DXT3", "DXT4", "DXT5", "ATI1", "ATI2", "INTZ", "RAWZ", "DF16", "DF24", "NULL", "NVDB", "RESZ", "ATOC", "A2M0", "A2M1", "INST", "R2VB",
               "YUY2", "UYVY", "GRGB", "RGBG", "MET1", "NV12", "YV12", "AYUV", "NVHU", "NVHS", " R16", "AL16"]   # D3DFMT_G8R8_G8B8 = 'GRGB', R8G8_B8G8 = 'RGBG', MULTI2_ARGB8 = 'MET1'
UNKNOWN_FMT = [fcc("ZZZZ"), fcc("abcd"), fcc("~!@#"), 1, 37, 255, 0x7fffffff, 0x41420143]
FMT = sorted(set(d3dformat_enum()) | {fcc(s) for s in FOURCC_LIST} | set(UNKNOWN_FMT))
DEVT = [1, 2, 3, 4]; DEVT_BAD = [0, 5, 0x7fffffff]
RTYPE = [1, 2, 3, 4, 5, 6, 7]; RTYPE_BAD = [0, 8, 11, 0x7fffffff]
MSAA = list(range(0, 17)) + [17, 0x7fffffff]
USAGE_BITS = [1 << i for i in range(32)]
MASK = 0x103ffff0 | 3     # WINED3DUSAGE_MASK | WINED3DUSAGE_QUERY_MASK (+ RT, DS): the bits that reach wined3d_check_device_format's usage logic
EFF = [b for b in USAGE_BITS if b & MASK]
PAIRS = [a | b for i, a in enumerate(EFF) for b in EFF[i + 1:]]
USAGE_ALL = [0] + USAGE_BITS
X8, R5, X1 = 22, 23, 24
RT, DS = 1, 2
QBITS = [0x8000, 0x10000, 0x20000, 0x40000, 0x80000, 0x100000, 0x200000]
ADP_ADAPTER_FMT_SMALL = [X8, R5, X1]
# (name, [per-argument domain: list of values or a domain name])
D = dict(FMT=FMT, DEVT=DEVT, DEVTX=DEVT + DEVT_BAD, RTYPE=RTYPE, RTYPEX=RTYPE + RTYPE_BAD, MSAA=MSAA, USAGE_ALL=USAGE_ALL, PAIRS=PAIRS, WIN=[0, 1],
         A0=[0], HAL=[1], X8=[X8], FMT_AD=[X8, R5, X1], SMALLRT=[1, 3, 4, 5, 6], RT2=[1, 3], USM3=[0, 1, 2], UNI=[0, 1, 3, 5, 6, 7], AFLAG=[0] + [1 << i for i in range(32)] + [0xffffffff], DISP4=[X8, R5, X1, 35],
         SCAN=[0, 1, 2, 3, 0x7fffffff], NZ=[0, 1, 2, 5, 0xffffffff], SZ=[0x18, 0x10, 0, 0x1c, 0xffffffff], FMT12=[21, 22, 23, 75, 80, fcc("DXT1"), fcc("INTZ"), fcc("NULL"), 114, 0, fcc("ZZZZ"), 113],
         USM=[0, 1, 2, 3, 0x40000 | 1, 0x20000, 0x10000, 0x200, 0x400, 0x400 | 1], FMT_SM=[0, 21, 22, 23, 75, 80, fcc("DXT1"), 0x7fffffff],
         OORA=[1, 2, 0x7fffffff, 0xffffffff], SDK=[0, 1, 0x1f, 0x20, 0x21, 0x80000020, 0xffffffff])
Q = []   # (op, [domains])
Q += [("GetAdapterCount", [])]
Q += [("GetAdapterIdentifier", [["A0"], ["AFLAG"]]), ("GetAdapterMonitor", [["A0"]]), ("GetAdapterDisplayMode", [["A0"]]), ("GetAdapterModeCount", [["A0"], ["FMT"]]),
      ("EnumAdapterModes", [["A0"], ["FMT"], ["NZ"]]), ("GetDeviceCaps", [["A0"], ["DEVTX"]])]
Q += [("CheckDeviceType", [["A0"], ["HAL"], ["FMT"], ["FMT"], ["WIN"]]), ("CheckDeviceType", [["A0"], ["DEVTX"], ["DISP4"], ["FMT"], ["WIN"]])]
Q += [("CheckDeviceFormat", [["A0"], ["HAL"], ["X8"], ["USAGE_ALL"], ["RTYPEX"], ["FMT"]]),        # every single usage bit x every resource type x every format
      ("CheckDeviceFormat", [["A0"], ["HAL"], ["X8"], ["PAIRS"], ["SMALLRT"], ["FMT"]]),         # all pairs of unmasked bits, the five resource-type classes
      ("CheckDeviceFormat", [["A0"], ["HAL"], ["FMT_AD"], ["USM"], ["SMALLRT"], ["FMT"]]),       # the three valid adapter formats
      ("CheckDeviceFormat", [["A0"], ["HAL"], ["FMT"], ["USM3"], ["RT2"], ["FMT12"]]),         # every adapter format value (gate)
      ("CheckDeviceFormat", [["A0"], ["DEVTX"], ["X8"], ["USM"], ["SMALLRT"], ["FMT12"]])]       # every device type value
Q += [("CheckDepthStencilMatch", [["A0"], ["HAL"], ["X8"], ["FMT"], ["FMT"]]), ("CheckDepthStencilMatch", [["A0"], ["HAL"], ["FMT"], ["FMT12"], ["FMT12"]]),
      ("CheckDepthStencilMatch", [["A0"], ["DEVTX"], ["X8"], ["FMT12"], ["FMT12"]])]
Q += [("CheckDeviceMultiSampleType", [["A0"], ["HAL"], ["FMT"], ["WIN"], ["MSAA"]]), ("CheckDeviceMultiSampleType", [["A0"], ["DEVTX"], ["FMT_SM"], ["WIN"], ["MSAA"]])]
Q += [("CheckDeviceFormatConversion", [["A0"], ["HAL"], ["FMT"], ["FMT"]]), ("CheckDeviceFormatConversion", [["A0"], ["DEVTX"], ["FMT12"], ["FMT12"]])]
Q += [("GetAdapterModeCountEx", [["A0"], ["FMT"], ["SCAN"]]), ("EnumAdapterModesEx", [["A0"], ["FMT"], ["SCAN"], ["NZ"]]), ("GetAdapterDisplayModeEx", [["A0"], ["SZ"]]),
      ("GetAdapterLUID", [["A0"]])]
# out-of-range adapter ordinals: every method, a representative argument slice (the ordinal test is the first statement of each Wine function)
Q += [("GetAdapterIdentifier", [["OORA"], ["AFLAG"]]), ("GetAdapterMonitor", [["OORA"]]), ("GetAdapterDisplayMode", [["OORA"]]), ("GetAdapterModeCount", [["OORA"], ["FMT_AD"]]),
      ("EnumAdapterModes", [["OORA"], ["FMT_AD"], ["NZ"]]), ("GetDeviceCaps", [["OORA"], ["DEVTX"]]), ("CheckDeviceType", [["OORA"], ["HAL"], ["DISP4"], ["FMT12"], ["WIN"]]),
      ("CheckDeviceFormat", [["OORA"], ["HAL"], ["X8"], ["USM"], ["SMALLRT"], ["FMT12"]]), ("CheckDepthStencilMatch", [["OORA"], ["HAL"], ["X8"], ["FMT12"], ["FMT12"]]),
      ("CheckDeviceMultiSampleType", [["OORA"], ["HAL"], ["FMT12"], ["WIN"], ["MSAA"]]), ("CheckDeviceFormatConversion", [["OORA"], ["HAL"], ["FMT12"], ["FMT12"]]),
      ("GetAdapterModeCountEx", [["OORA"], ["FMT_AD"], ["SCAN"]]), ("EnumAdapterModesEx", [["OORA"], ["FMT_AD"], ["SCAN"], ["NZ"]]), ("GetAdapterDisplayModeEx", [["OORA"], ["SZ"]]),
      ("GetAdapterLUID", [["OORA"]])]
Q += [("Direct3DCreate9", [["SDK"]]), ("Direct3DCreate9Ex", [["SDK"]])]
OPS = ["GetAdapterCount", "GetAdapterIdentifier", "GetAdapterMonitor", "GetAdapterDisplayMode", "GetAdapterModeCount", "EnumAdapterModes", "GetDeviceCaps", "CheckDeviceType",
       "CheckDeviceFormat", "CheckDepthStencilMatch", "CheckDeviceMultiSampleType", "CheckDeviceFormatConversion", "GetAdapterModeCountEx", "EnumAdapterModesEx",
       "GetAdapterDisplayModeEx", "GetAdapterLUID", "Direct3DCreate9", "Direct3DCreate9Ex"]
NARGS = {"GetAdapterCount": 0, "GetAdapterIdentifier": 2, "GetAdapterMonitor": 1, "GetAdapterDisplayMode": 1, "GetAdapterModeCount": 2, "EnumAdapterModes": 3, "GetDeviceCaps": 2,
         "CheckDeviceType": 5, "CheckDeviceFormat": 6, "CheckDepthStencilMatch": 5, "CheckDeviceMultiSampleType": 5, "CheckDeviceFormatConversion": 4, "GetAdapterModeCountEx": 3,
         "EnumAdapterModesEx": 4, "GetAdapterDisplayModeEx": 2, "GetAdapterLUID": 1, "Direct3DCreate9": 1, "Direct3DCreate9Ex": 1}


def emit():
    o = ["/* generated by envq.py; do not edit. The environment query list: ENVQ(op, nargs, a0..a5) is defined by the includer. */"]
    for n, v in D.items(): o.append(f"static const unsigned DOM_{n}[] = {{ " + ", ".join(f"0x{x:x}u" for x in v) + f" }}; /* {len(v)} */")
    o.append("#define N_(a) (sizeof(a) / sizeof(a[0]))")
    o.append("static void envq_all(void) {")
    for k, (op, doms) in enumerate(Q):
        ds = [d[0] for d in doms]; o.append("  { unsigned i%s;" % ",i".join(map(str, range(len(ds)))) if ds else "  {")
        for j, d in enumerate(ds): o.append(f"    for (i{j} = 0; i{j} < N_(DOM_{d}); i{j}++)")
        o.append(f"    ENVQ({OPS.index(op)}, {len(ds)}, " + ", ".join(f"DOM_{d}[i{j}]" for j, d in enumerate(ds)) + (", " if ds and len(ds) < 6 else "") + ", ".join(["0"] * (6 - len(ds))) + "); }")
    o.append("}")
    return "\n".join(o) + "\n"


SPEC = os.path.join(HERE, "..", "spec", "d3d9_spec.json")
OUT_STRUCTS = ["D3DCAPS9", "D3DADAPTER_IDENTIFIER9", "D3DDISPLAYMODE", "D3DDISPLAYMODEEX", "LUID"]


def leaves(st, name, pre="", base=0):
    r = []
    for f in st[name]["fields"]:
        if f["kind"] == "pad": continue
        if f["kind"] == "struct": r += leaves(st, f["struct"], pre + f["name"] + ".", base + f["off"])
        else: r.append((pre + f["name"], base + f["off"], f["size"], "c" if f["kind"] == "char" else ("b" if f.get("count", 1) > 1 or f["size"] > 4 else "i")))
    return r


def emit_fmt():
    import json
    st = json.load(open(SPEC))["structs"]; o = ["/* generated by envq.py from the spec: leaf fields of the out structs */"]
    for n in OUT_STRUCTS:
        o.append(f"static const EnvLeaf LEAVES_{n}[] = {{ " + ", ".join(f'{{"{a}", {b}, {c}, \'{d}\'}}' for a, b, c, d in leaves(st, n)) + " };")
    o.append("static const char* const ENVQ_NAMES[] = { " + ", ".join(f'"{n}"' for n in OPS) + " };")
    return "\n".join(o) + "\n"


def count():
    n = 0
    for op, doms in Q:
        k = 1
        for d in doms: k *= len(D[d[0]])
        n += k
    return n


if __name__ == "__main__":
    if len(sys.argv) > 1: open(sys.argv[1], "w").write(emit()); open(sys.argv[1].replace("envq.gen.h", "envfmt.gen.h"), "w").write(emit_fmt())
    print(f"envq: {len(Q)} groups, {count()} queries, {len(FMT)} formats, {len(PAIRS)} usage pairs", file=sys.stderr)
