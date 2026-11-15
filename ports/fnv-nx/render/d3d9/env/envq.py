#!/usr/bin/env python3
"""envq.py: the ONE source of the environment query list. emit() / emit_fmt() return envq.gen.h / envfmt.gen.h = C code (nested loops over the domains below), called by the generator that owns them (no entry point of its own), that
both envprobe2.c (mingw, real D3D9) and hostenv.cpp (our NxDirect3D9) include, so the two sides cannot ask different questions.
Domains are COMPLETE finite sets: every D3DFORMAT enum value of d3d9types.h + the FOURCC list + unknown values; every D3DDEVTYPE/D3DRESOURCETYPE/
D3DMULTISAMPLE_TYPE enum value + invalid ones; every single usage bit of the 32-bit word + all pairs of the bits that are not masked by Wine
(see render/d3d9/env/README.md for why single bits + pairs cover the usage function)."""
import os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.dont_write_bytecode = True
fcc = lambda s: sum(ord(c) << (8 * i) for i, c in enumerate(s))


def enum_domain(hdr, typedef, force):
    """(members, complement) of a header enum: members = every enumerator value but the FORCE_DWORD one; complement = representatives of every other 32-bit
    value: min - 1 (when min > 0), max + 1 and 0x7fffffff (the largest signed 32-bit value; the probe rows of the table were taken with it). The representatives stand for the whole complement only where the closing rule of the
    enum (proved at its use in load()) shows that Wine's code treats every value outside the enum alike."""
    t = open(hdr).read(); t = t[t.index("typedef enum " + typedef):]; t = t[:t.index("} " + force[0] + ";")]
    vals = {n: int(v, 0) for n, v in re.findall(r"(\w+)\s*=\s*(0x[0-9A-Fa-f]+|\d+)", t)}
    vals.pop(force[1]); mem = sorted(vals.values())
    return mem, ([mem[0] - 1] if mem[0] > 0 else []) + [mem[-1] + 1, 0x7fffffff]


def d3dformat_enum(hdr):
    """{name: value} of the D3DFORMAT enumerators of the header (MAKEFOURCC ones evaluated)"""
    t = open(hdr).read(); t = t[t.index("typedef enum _D3DFORMAT"):]; t = t[:t.index("} D3DFORMAT;")]; out = {}
    for m in re.finditer(r"(D3DFMT_\w+)\s*=\s*(?:MAKEFOURCC\('(.)',\s*'(.)',\s*'(.)',\s*'(.)'\)|(0x[0-9A-Fa-f]+|\d+))", t):
        out[m.group(1)] = fcc("".join(m.group(i) for i in range(2, 6))) if m.group(2) else int(m.group(6), 0)
    return out


def d3d_results(d3d9h):
    """{name: value} of the D3DERR_* / D3DOK_* defines of d3d9.h: MAKE_D3DHRESULT(code) / MAKE_D3DSTATUS(code) with _FACD3D, evaluated as the header's MAKE_HRESULT does"""
    t = open(d3d9h).read(); fac = int(re.search(r"#define _FACD3D\s+(0x[0-9A-Fa-f]+)", t).group(1), 16)
    return {n: ((1 if k == "HRESULT" else 0) << 31 | fac << 16 | int(c)) for n, k, c in re.findall(r"#define (D3D(?:ERR|OK)_\w+)\s+MAKE_D3D(HRESULT|STATUS)\(\s*(\d+)\s*\)", t)}


def switch_formats(wine, fmtn):
    """the D3DFORMAT values with a case in the switch of d3d9/device.c wined3dformat_from_d3dformat (Wine 9.0), but D3DFMT_UNKNOWN (the id UNKNOWN itself): the case labels resolved through the header"""
    d = open(os.path.join(wine, "dlls", "d3d9", "device.c")).read()
    i = d.index("switch(format)", d.index("wined3dformat_from_d3dformat(D3DFORMAT")); j = d.index("default:", i)
    labels = re.findall(r"case (D3DFMT_\w+):", d[i:j])
    if not labels or any(l not in fmtn for l in labels): raise SystemExit("envq: a case label of wined3dformat_from_d3dformat is not a D3DFORMAT enumerator")
    return sorted(fmtn[l] for l in labels if fmtn[l] != fmtn["D3DFMT_UNKNOWN"])


# UNKNOWN_FMT: representatives of the complement of (enum values + known FOURCCs). Closing proof, Wine 9.0 (tools/wine-9.0-d3d9-formats 348f306e, = tools/wine-9.0 c8373ee4):
#  - a value of four printable bytes is passed through unchanged (dlls/d3d9/device.c:112-113), then dlls/wined3d/utils.c get_format_idx returns -1 for every FOURCC not in format_index_remap
#    (utils.c:1932-1948), which selects the UNKNOWN format: one behaviour for every unknown printable FOURCC (ZZZZ, abcd, ~!@# stand for it);
#  - any other value goes through the switch of wined3dformat_from_d3dformat (device.c:115) whose `default:` is WINED3DFMT_UNKNOWN: one behaviour for every unmapped number (1, 37, 255, 0x7fffffff, 0x41420143).
UNKNOWN_FMT = [fcc("ZZZZ"), fcc("abcd"), fcc("~!@#"), 1, 37, 255, 0x7fffffff, 0x41420143]
FMT = None   # set by load(): every D3DFORMAT enum value of the header + the known FOURCCs + UNKNOWN_FMT
WU = DU = MSAA_MAX = None   # set by load(): WINED3DUSAGE_* (wined3d.h) and D3DUSAGE_* (d3d9types.h) by name; the largest D3DMULTISAMPLE_TYPE enumerator
KNOWN_FCC = None   # set by load(): the FOURCCs Wine 9.0 knows, sorted
# DEVT, RTYPE, MSAA (and their *X / complement forms) are derived in load() from the enums of the mounted d3d9types.h (enum_domain)
USAGE_BITS = [1 << i for i in range(32)]   # the 32 single bits of the DWORD usage word; PAIRS, MASK and the usage domains below are derived in load()
X8 = R5 = X1 = A2R10 = None   # D3DFMT_X8R8G8B8, R5G6B5, X1R5G5B5, A2R10G10B10: read by name from the header in load()
# (name, [per-argument domain: list of values or a domain name])
D = dict(FMT=None, DEVT=None, DEVTX=None, RTYPE=None, RTYPEX=None, MSAA=None, USAGE_ALL=[0] + USAGE_BITS, PAIRS=None, WIN=[0, 1],
         A0=[0], HAL=[1], X8=None, FMT_AD=None, SMALLRT=None, RT2=None, USM3=None, AFLAG=[0] + [1 << i for i in range(32)] + [0xffffffff], DISP4=None,
         SCAN=[0, 1, 2, 3, 0x7fffffff], NZ=[0, 1, 2, 5, 0xffffffff], SZ=[0x18, 0x10, 0, 0x1c, 0xffffffff], FMT12=None,
         USM=None, FMT_SM=None,
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


def c_defines_enum(hdr, typedef):
    """{suffix: value} of an enum of the header: the enumerator names without their D3D..._ prefix (D3DRTYPE_SURFACE -> SURFACE)"""
    t = open(hdr).read(); t = t[t.index("typedef enum " + typedef):]; t = t[:t.index("}")]
    return {n.split("_", 1)[1]: int(v, 0) for n, v in re.findall(r"(\w+)\s*=\s*(0x[0-9A-Fa-f]+|\d+)", t)}


def c_defines(path, prefix):
    """{name: value} of the `#define PREFIX_NAME 0x..` lines of a header (hex or decimal, an optional L / U suffix)"""
    return {n: int(v.rstrip("LUlu"), 0) for n, v in re.findall(r"#define (%s\w*)\s+\(?(0x[0-9A-Fa-f]+[LUlu]*|\d+[LUlu]*)\)?\s*$" % prefix, open(path).read(), re.M)}


def known_fourcc(wine):
    """the FOURCC formats Wine 9.0 knows, derived: the names in wined3d/utils.c format_index_remap, each resolved to its WINEMAKEFOURCC of include/wine/wined3d.h.
    wine = the directory of the pinned mirror (tools/wine-9.0-d3d9-formats)."""
    u = open(os.path.join(wine, "dlls", "wined3d", "utils.c")).read(); i = u.index("format_index_remap[] ="); blk = u[i:u.index("};", i)]
    h = open(os.path.join(wine, "include", "wine", "wined3d.h")).read(); out = []
    for n in re.findall(r"\{(WINED3DFMT_\w+),\s*WINED3D_FORMAT_FOURCC_BASE", blk):
        m = re.search(r"\b" + n + r"\s*=\s*WINEMAKEFOURCC\('(.)',\s*'(.)',\s*'(.)',\s*'(.)'\)", h)
        if not m: raise SystemExit("envq: %s of format_index_remap has no WINEMAKEFOURCC in wined3d.h" % n)
        out.append(fcc("".join(m.groups())))
    if not out: raise SystemExit("envq: format_index_remap not found")
    return out


def load(hdr, spec, wine, d3d9h):
    """the one entry: hdr = d3d9types.h, spec = the interface table (D9_SPEC), wine = the pinned Wine mirror directory; all are the caller's arguments (no default, no path of this file).
    Builds FMT (and its domain D["FMT"]) and sets SPEC; nothing of this module is valid before it is called."""
    global WU, DU, MSAA_MAX, FMT, SPEC, X8, R5, X1, A2R10, SWITCH, NA, INV, NOAUTOGEN
    global KNOWN_FCC
    FN = d3dformat_enum(hdr)
    KNOWN_FCC = sorted(known_fourcc(wine))
    SWITCH = switch_formats(wine, FN)
    X8, R5, X1, A2R10 = (FN["D3DFMT_" + n] for n in ("X8R8G8B8", "R5G6B5", "X1R5G5B5", "A2R10G10B10"))
    R = d3d_results(d3d9h); NA, INV, NOAUTOGEN = R["D3DERR_NOTAVAILABLE"], R["D3DERR_INVALIDCALL"], R["D3DOK_NOAUTOGEN"]
    D["X8"], D["FMT_AD"], D["DISP4"] = [X8], [X8, R5, X1], [X8, R5, X1, A2R10]
    # usage: the bits that reach wined3d_check_device_format's usage logic = WINED3DUSAGE_MASK | WINED3DUSAGE_QUERY_MASK (wined3d.h, Wine 9.0) + D3DUSAGE_RENDERTARGET | DEPTHSTENCIL (they become bind flags)
    WU = c_defines(os.path.join(wine, "include", "wine", "wined3d.h"), "WINED3DUSAGE_"); DU = c_defines(hdr, "D3DUSAGE_")
    MASK = WU["WINED3DUSAGE_MASK"] | WU["WINED3DUSAGE_QUERY_MASK"] | DU["D3DUSAGE_RENDERTARGET"] | DU["D3DUSAGE_DEPTHSTENCIL"]
    EFF = [b for b in USAGE_BITS if b & MASK]
    MSAA_MAX = max(enum_domain(hdr, "_D3DMULTISAMPLE_TYPE", ("D3DMULTISAMPLE_TYPE", "D3DMULTISAMPLE_FORCE_DWORD"))[0])   # D3DMULTISAMPLE_16_SAMPLES
    D["PAIRS"] = [a | b for i, a in enumerate(EFF) for b in EFF[i + 1:]]
    RTV, DSV = DU["D3DUSAGE_RENDERTARGET"], DU["D3DUSAGE_DEPTHSTENCIL"]; q = lambda n: WU["WINED3DUSAGE_QUERY_" + n]
    D["USM3"] = [0, RTV, DSV]
    D["USM"] = [0, RTV, DSV, RTV | DSV, q("SRGBWRITE") | RTV, q("FILTER"), q("SRGBREAD"), WU["WINED3DUSAGE_DYNAMIC"], q("GENMIPMAP"), q("GENMIPMAP") | RTV]
    # resource-type classes (README: SURFACE, TEXTURE, CUBE, VOLUME(=VOLUMETEXTURE), BUFFER(=VB=IB)), one representative each, by name; RT2 = the two that can be render targets
    RN = c_defines_enum(hdr, "_D3DRESOURCETYPE")
    D["SMALLRT"] = [RN[n] for n in ("SURFACE", "TEXTURE", "VOLUMETEXTURE", "CUBETEXTURE", "VERTEXBUFFER")]
    D["RT2"] = [RN["SURFACE"], RN["TEXTURE"]]
    # FMT12 / FMT_SM: the formats of the 12-value and 8-value probe axes, each by its name in the header (a FOURCC by its four characters; a value of the complement by UNKNOWN_FMT)
    f = lambda n: FN["D3DFMT_" + n]
    D["FMT12"] = [f("A8R8G8B8"), f("X8R8G8B8"), f("R5G6B5"), f("D24S8"), f("D16"), fcc("DXT1"), fcc("INTZ"), fcc("NULL"), f("R32F"), f("UNKNOWN"), UNKNOWN_FMT[0], f("A16B16G16R16F")]
    D["FMT_SM"] = [f("UNKNOWN"), f("A8R8G8B8"), f("X8R8G8B8"), f("R5G6B5"), f("D24S8"), f("D16"), fcc("DXT1"), 0x7fffffff]
    FMT = sorted(set(FN.values()) | set(KNOWN_FCC) | set(UNKNOWN_FMT))
    D["FMT"] = FMT
    # D3DDEVTYPE: Wine 9.0 (tools/wine-9.0 c8373ee4) never reads the value except in dlls/wined3d/directx.c:2320 (wined3d_get_device_caps: DeviceType = HAL ? HAL : REF,
    #   the result is the same for every value other than HAL) and passes it through by cast (dlls/d3d9/d3d9_private.h:409); so every value outside the enum behaves as REF.
    DEVT, devt_bad = enum_domain(hdr, "_D3DDEVTYPE", ("D3DDEVTYPE", "D3DDEVTYPE_FORCE_DWORD"))
    # D3DRESOURCETYPE: dlls/d3d9/directx.c:301-326 d3d9_CheckDeviceFormat: the switch names the 7 values, `default:` returns WINED3DERR_INVALIDCALL: uniform for every other value.
    RTYPE, rtype_bad = enum_domain(hdr, "_D3DRESOURCETYPE", ("D3DRESOURCETYPE", "D3DRTYPE_FORCE_DWORD"))
    # D3DMULTISAMPLE_TYPE: dlls/d3d9/directx.c:363 d3d9_CheckDeviceMultiSampleType: `type > D3DMULTISAMPLE_16_SAMPLES` returns D3DERR_INVALIDCALL: uniform for every value above the enum.
    MSAA, msaa_bad = enum_domain(hdr, "_D3DMULTISAMPLE_TYPE", ("D3DMULTISAMPLE_TYPE", "D3DMULTISAMPLE_FORCE_DWORD"))
    D["DEVT"], D["DEVTX"] = DEVT, DEVT + devt_bad
    D["RTYPE"], D["RTYPEX"] = RTYPE, RTYPE + rtype_bad
    D["MSAA"] = MSAA + msaa_bad
    SPEC = spec


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


SPEC = None   # the interface table (D9_SPEC of bridgefacts): set by the caller from its argument
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
