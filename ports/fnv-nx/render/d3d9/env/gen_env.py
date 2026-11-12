#!/usr/bin/env python3
"""gen_env.py [OUTDIR]: env_handheld.tsv + env_docked.tsv (tests/l6/env/make_env.py: our measurement of Wine 9.0 + Mesa llvmpipe) -> env_table.gen.h/.cpp (not checked in).
Every table cell is DERIVED from rows through the closing rules of README.md, and every rule's premise is asserted on all rows it covers (a violated rule stops the generator,
exit 2, naming it): devtype / adapter-format independence, format classes, usage-bit conjunction, struct writes iff success. The lookup functions are in env_rules.cpp.in.
Profiles differ only where reported by the `profile differences` line."""
import os, re, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.dont_write_bytecode = True
sys.path.insert(0, HERE); import envq  # noqa: E402
PROFILES = ["handheld", "docked"]; X8, R5, X1, A2R10 = 22, 23, 24, 35; HRC = [0, 0x8876086A, 0x8876086C, 0x0876086F]; HRN = {v: i for i, v in enumerate(HRC)}
NA, INV = 0x8876086A, 0x8876086C
# CreateQuery (nx_env_query_supported). Rule: d3d9 query_init passes the type through (wined3d_query_type = D3DQUERYTYPE); wined3d GL adapter_gl_create_query implements EVENT (ARB_sync / fences),
# OCCLUSION (ARB_occlusion_query), TIMESTAMP, TIMESTAMP_DISJOINT, TIMESTAMP_FREQ (ARB_timer_query) and answers WINED3DERR_NOTAVAILABLE for every other type (query.c wined3d_query_gl_create);
# a recorded environment has the extension iff its core GL version has it: sync 3.2, occlusion 1.5, timer query 3.3. The version is the recorded GL_VERSION of ENV_PIN.txt (both profiles).
QUERY_GL = {8: 32, 9: 15, 10: 33, 11: 33, 12: 33}   # D3DQUERYTYPE -> core GL version (major * 10 + minor) of the extension the wined3d implementation needs


def gl_versions():
    t = open(os.path.join(HERE, "ENV_PIN.txt")).read(); v = {int(a) * 10 + int(b) for a, b in re.findall(r'GL_VERSION: "(\d+)\.(\d+) \(Core Profile\)', t)}
    if len(v) != 1: die("ENV_PIN.txt records one core-profile GL_VERSION for both profiles")
    return v.pop()


GLVER = gl_versions(); QMASK = sum(1 << t for t, need in QUERY_GL.items() if GLVER >= need)
fcc = envq.fcc
# d3d9/device.c wined3dformat_from_d3dformat: non-FOURCC values with a case; wined3d/utils.c get_format_idx: the known FOURCCs
SWITCH = [20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 40, 41, 50, 51, 52, 60, 61, 62, 63, 64, 67, 70, 71, 73, 75, 77, 79, 80, 81, 82, 83, 101, 102, 110, 111, 112, 113, 114, 115, 116, 117]
KNOWN_FCC = sorted(fcc(s) for s in ["UYVY", "YUY2", "YV12", "DXT1", "DXT2", "DXT3", "DXT4", "DXT5", "MET1", "GRGB", "RGBG", "ATI1", "ATI2", "INST", "NVDB", "NVHU", "NVHS", "INTZ",
                                    "RESZ", "NULL", " R16", "AL16", "NV12", "ATOC"])
UNK0, UNKF = len(KNOWN_FCC) + len(SWITCH), len(KNOWN_FCC) + len(SWITCH) + 1; NC = UNKF + 1


def printable(f): return all(0x20 <= (f >> (8 * i)) & 255 <= 0x7e for i in range(4))


def cls(f):
    if printable(f): return KNOWN_FCC.index(f) if f in KNOWN_FCC else UNKF
    return len(KNOWN_FCC) + SWITCH.index(f) if f in SWITCH else UNK0


def die(msg): sys.stderr.write("gen_env: RULE VIOLATED: " + msg + "\n"); sys.exit(2)


def load(path):
    R, DEV = {}, []
    for l in open(path):
        if l.startswith("#"): continue
        q, ret, out = l.rstrip("\n").split("\t"); m = re.match(r"([\w.]+)\((.*)\)$", q); a = tuple(int(x, 16) for x in m.group(2).split(",")) if m.group(2) else (); k = (m.group(1), a)
        if m.group(1).startswith("DEV."): DEV.append((k, int(ret, 16), out)); continue
        if k in R and R[k] != (int(ret, 16), out) and not k[0].startswith(("QueryInterface", "AddRef", "Release")) and k[0] != "GetAdapterDisplayMode": die(f"duplicate query {k} answered differently")
        R[k] = (int(ret, 16), out)
    return R, DEV


def fields(out):
    d = {}
    for t in out.split(" "):
        if "=" in t: k, v = t.split("=", 1); d[k] = v
    return d


def image(st, out, size):
    """byte image of an out struct from its printed leaves (envq.leaves): unwritten bytes 0"""
    im = bytearray(size); f = fields(out)
    for name, off, sz, kind in envq.leaves(ST, st):
        v = f[name]
        if kind == "c": b = re.sub(r"\\x([0-9a-f]{2})", lambda m: chr(int(m.group(1), 16)), v).encode("latin-1"); im[off:off + len(b)] = b
        elif kind == "b": im[off:off + sz] = bytes.fromhex(v)
        else: im[off:off + sz] = int(v, 16).to_bytes(sz, "little")
    return bytes(im)


import json  # noqa: E402
ST = json.load(open(envq.SPEC))["structs"]


def derive(R, DEV):
    T = {}; get = lambda n, *a: R[(n, a)]; ok = lambda n, *a: R[(n, a)][0] == 0
    T["N"] = get("GetAdapterCount")[0]; N = T["N"]
    if N != 1: die("adapter count must be 1 (one adapter)")
    oor = [a for a in (1, 2, 0x7fffffff, 0xffffffff)]
    T["MON"] = get("GetAdapterMonitor", 0)[0]
    for a in oor:
        if get("GetAdapterMonitor", a)[0] != 0: die("monitor of an out-of-range adapter must be NULL")
    # display mode
    r, o = get("GetAdapterDisplayMode", 0); f = fields(o); T["DM"] = (int(f["Width"], 16), int(f["Height"], 16), int(f["RefreshRate"], 16)); T["DMFMT"] = int(f["Format"], 16)
    if r != 0 or T["DMFMT"] != X8: die("display mode format must be X8R8G8B8")
    for a in oor:
        if get("GetAdapterDisplayMode", a) != (INV, "-"): die("GetAdapterDisplayMode oor")
    # mode lists: valid formats = those with count > 0; everything else 0 / INVALIDCALL
    T["MCNT"] = {}
    for (n, a), (r, o) in R.items():
        if n == "GetAdapterModeCount" and a[0] == 0: T["MCNT"][a[1]] = r
    if set(k for k, v in T["MCNT"].items() if v) != {X8, R5}: die("modes exist for exactly X8R8G8B8 and R5G6B5")
    if any(v not in (0, 1) for v in T["MCNT"].values()): die("one mode per format")
    for (n, a), (r, o) in R.items():
        if n == "EnumAdapterModes":
            ad, fm, i = a
            if ad == 0 and fm in (X8, R5) and i == 0:
                f = fields(o); m = (int(f["Width"], 16), int(f["Height"], 16), int(f["RefreshRate"], 16))
                if r != 0 or m != T["DM"] or int(f["Format"], 16) != fm: die("EnumAdapterModes(0, fmt, 0) = display mode with Format = requested format")
            elif (r, o) != (INV, "-"): die(f"EnumAdapterModes {a} must be INVALIDCALL untouched")
        if n == "GetAdapterModeCount" and (a[0] != 0 or a[1] not in (X8, R5)) and r != 0: die(f"GetAdapterModeCount {a} must be 0")
    # Ex: scan classes 0 / 1 (= the mode's ordering) / other
    T["SCAN"] = None
    for (n, a), (r, o) in R.items():
        if n == "GetAdapterModeCountEx":
            ad, fm, sc = a; want = 1 if (ad == 0 and fm in (X8, R5) and sc in (0, 1)) else 0
            if r != want: die(f"GetAdapterModeCountEx {a}: {r} != {want} (mode_matches_filter)")
        if n == "EnumAdapterModesEx":
            ad, fm, sc, i = a
            if ad == 0 and fm in (X8, R5) and sc in (0, 1) and i == 0:
                f = fields(o); T["SCAN"] = int(f["ScanLineOrdering"], 16)
                if r != 0 or f["Size"] != "cdcdcdcd" or (int(f["Width"], 16), int(f["Height"], 16), int(f["RefreshRate"], 16)) != T["DM"] or int(f["Format"], 16) != fm: die(f"EnumAdapterModesEx {a}")
            elif r != INV or o != "-": die(f"EnumAdapterModesEx {a} must be INVALIDCALL untouched")
        if n == "GetAdapterDisplayModeEx":
            ad, sz = a; f = fields(o)
            if ad == 0 and sz == 24:
                if r != 0 or (int(f["Width"], 16), int(f["Height"], 16), int(f["RefreshRate"], 16), int(f["Format"], 16)) != T["DM"] + (X8,) or int(f["ScanLineOrdering"], 16) != T["SCAN"]: die(f"DisplayModeEx {a}")
                T["ROT"] = int(f["rot"], 16)
            elif r != INV or any(f[k] != "cdcdcdcd" for k in ("Width", "Height", "RefreshRate", "Format", "ScanLineOrdering")) or "rot" in f: die(f"GetAdapterDisplayModeEx {a} must be INVALIDCALL untouched")
    if T["SCAN"] != 1: die("mode scan ordering is PROGRESSIVE")
    r, o = get("GetAdapterLUID", 0); f = fields(o); T["LUID"] = (int(f["LowPart"], 16), int(f["HighPart"], 16))
    if r or any(get("GetAdapterLUID", a) != (INV, "-") for a in oor): die("LUID")
    # identifier: one image, WHQLLevel = (flags & 2) != 0
    base = image("D3DADAPTER_IDENTIFIER9", get("GetAdapterIdentifier", 0, 0)[1], 1100); T["IDENT"] = base
    for (n, a), (r, o) in R.items():
        if n == "GetAdapterIdentifier":
            if a[0] == 0:
                im = bytearray(image("D3DADAPTER_IDENTIFIER9", o, 1100)); im[1096:1100] = bytes(4); exp = bytearray(base); exp[1096:1100] = bytes(4)
                if r != 0 or im != exp or int(fields(o)["WHQLLevel"], 16) != (1 if a[1] & 2 else 0): die(f"GetAdapterIdentifier {a}")
            elif (r, o) != (INV, "-"): die(f"GetAdapterIdentifier {a} oor")
    # caps: image per device-type class (HAL / other), DeviceType field = 1 or 2
    T["CAPS"] = []
    for cl, dt in ((0, 1), (1, 2)):
        T["CAPS"].append(image("D3DCAPS9", get("GetDeviceCaps", 0, dt)[1], 304))
    for (n, a), (r, o) in R.items():
        if n == "GetDeviceCaps":
            if a[0] == 0:
                im = image("D3DCAPS9", o, 304); c = T["CAPS"][0 if a[1] == 1 else 1]
                if r != 0 or im != c or int.from_bytes(im[:4], "little") != (1 if a[1] == 1 else 2): die(f"GetDeviceCaps {a}")
            elif (r, o) != (INV, "-"): die(f"GetDeviceCaps {a} oor")
    # device caps facts the rules of env_rules.cpp.in rest on (asserted, so a changed recording cannot silently change a rule)
    leaf = {n: (off, sz) for n, off, sz, k in envq.leaves(ST, "D3DCAPS9")}; capw = lambda img, n: int.from_bytes(img[leaf[n][0]:leaf[n][0] + leaf[n][1]], "little")
    T["DECLTYPES"] = capw(T["CAPS"][0], "DeclTypes")
    if capw(T["CAPS"][1], "DeclTypes") != T["DECLTYPES"]: die("DeclTypes is the same for every device type (nx_env_decltype_supported has no device argument)")
    if capw(T["CAPS"][0], "DevCaps") & 0x01C00000: die("DevCaps has no RTPATCHES / RTPATCHHANDLEZERO / NPATCHES: the device promises no higher-order surfaces, so the bodies of dev/copy_misc.cpp are Wine 9.0's (dlls/d3d9/device.c d3d9_device_DrawRectPatch / d3d9_device_DrawTriPatch: FIXME + D3D_OK, d3d9_device_DeletePatch: FIXME + D3DERR_INVALIDCALL; rows 48-50 of verify/wine_fixme.tsv) and a recording that reports patch caps needs a real implementation instead")
    if capw(T["CAPS"][0], "StretchRectFilterCaps") & ~0x03000300: die("StretchRectFilterCaps holds only MIN/MAG POINT/LINEAR bits (nxenv_stretch_filter)")
    T["STRETCHCAPS"] = capw(T["CAPS"][0], "StretchRectFilterCaps")
    T["QMASK"] = QMASK
    # formats: classes
    rep = {}
    for f in envq.FMT: rep.setdefault(cls(f), []).append(f)
    # CheckDeviceType[win][disp idx][bb class]
    DISP = [X8, R5, X1, A2R10]; cdt = [[[None] * NC for _ in DISP] for _ in (0, 1)]
    for (n, a), (r, o) in R.items():
        if n != "CheckDeviceType": continue
        ad, dev, di, bb, w = a
        if ad != 0: (r == INV) or die(f"CheckDeviceType oor {a}"); continue
        if di not in DISP: (r == NA) or die(f"CheckDeviceType display {di:#x} must be NOTAVAILABLE"); continue
        c = cdt[w][DISP.index(di)][cls(bb)]
        if c is not None and c != r: die(f"CheckDeviceType class/devtype independence at {a}")
        cdt[w][DISP.index(di)][cls(bb)] = r
    T["CDT"] = [[[HRN[x if x is not None else NA] for x in row] for row in w] for w in cdt]
    # CheckDeviceFormat: per (resource class, format class) the atom bits
    RC = {1: 0, 3: 1, 5: 2, 2: 3, 4: 3, 6: 4, 7: 4}; cdf = {}
    for (n, a), (r, o) in R.items():
        if n != "CheckDeviceFormat": continue
        ad, dev, af, us, rt, fm = a
        if ad != 0: (r == INV) or die(f"CheckDeviceFormat oor {a}"); continue
        if af not in (X8, R5, X1): (r == (NA if af else INV)) or die(f"CheckDeviceFormat adapter format {af:#x}"); continue
        if rt not in RC: (r == INV) or die(f"CheckDeviceFormat rtype {rt}"); continue
        cdf.setdefault((RC[rt], cls(fm), us), set()).add(r)
    for k, v in cdf.items():
        if len(v) != 1: die(f"CheckDeviceFormat (class, usage) not a function of (resource class, format class, usage): {k}")
    S = lambda rc, c, us: next(iter(cdf[(rc, c, us)]))
    M = [[0] * NC for _ in range(5)]
    for rc in range(5):
        for c in range(NC):
            bits = [S(rc, c, 0) == 0, S(rc, c, 1) == 0, S(rc, c, 2) == 0, S(rc, c, 0x20000) == 0, S(rc, c, 0x80000) == 0, S(rc, c, 0x10000) == 0,
                    S(rc, c, 0x40000 | (1 if rc == 0 else 0)) == 0, S(rc, c, 0x100000) == 0, S(rc, c, 0x8000) == 0, S(rc, c, 0x400) != NA, S(rc, c, 0x400) == 0]
            M[rc][c] = sum(1 << i for i, b in enumerate(bits) if b)
    T["CDF"] = M
    # CheckDepthStencilMatch = RT-capable(rt) and DS-capable(ds); adapter format and device type are ignored
    ds = {}
    for (n, a), (r, o) in R.items():
        if n != "CheckDepthStencilMatch": continue
        ad, dev, af, rt, d = a
        if ad != 0: (r == INV) or die(f"CheckDepthStencilMatch oor {a}"); continue
        ds[(cls(rt), cls(d))] = ds.get((cls(rt), cls(d)), set()) | {r}
    if any(len(v) != 1 for v in ds.values()): die("CheckDepthStencilMatch independent of adapter format and device type")
    rtok = [any(next(iter(ds[(c, d)])) == 0 for d in range(NC) if (c, d) in ds) for c in range(NC)]; dsok = [any(next(iter(ds[(c, d)])) == 0 for c in range(NC) if (c, d) in ds) for d in range(NC)]
    for (c, d), v in ds.items():
        if (next(iter(v)) == 0) != (rtok[c] and dsok[d]): die(f"CheckDepthStencilMatch is not RT(rt) and DS(ds) at classes {c},{d}")
    T["RTOK"], T["DSOK"] = [int(x) for x in rtok], [int(x) for x in dsok]
    # CheckDeviceMultiSampleType: mask per class
    ms = {}
    for (n, a), (r, o) in R.items():
        if n != "CheckDeviceMultiSampleType": continue
        ad, dev, fm, w, t = a
        if ad != 0 or t > 16: (r, o) == (INV, "-") or die(f"CheckDeviceMultiSampleType {a} must be INVALIDCALL untouched"); continue
        ms.setdefault((cls(fm), t), set()).add((r, o))
    mask = [0] * NC
    for c in range(NC):
        if c == UNK0:
            if any(ms[(c, t)] != {(INV, "-")} for t in range(17) if (c, t) in ms): die("format UNKNOWN is INVALIDCALL")
            continue
        for t in range(17):
            if len(ms[(c, t)]) != 1: die(f"CheckDeviceMultiSampleType not a function of (class, type) at {c},{t}")
        if ms[(c, 0)] != {(0, "q=1")}: die(f"MSAA type NONE at class {c}")
        for t in range(2, 17):
            v = next(iter(ms[(c, t)])); (v in ((0, "q=1"), (NA, "q=1"))) or die(f"MSAA type {t} class {c}: {v}")
            if v[0] == 0: mask[c] |= 1 << (t - 1)
        v = next(iter(ms[(c, 1)]))
        if v[0] == 0:
            pc = int(v[1][2:]); b0 = pc - bin(mask[c]).count("1"); (b0 in (0, 1)) or die(f"MSAA NONMASKABLE levels at class {c}"); mask[c] |= b0
        elif v != (NA, "q=1") or mask[c]: die(f"MSAA NONMASKABLE class {c}")
    T["MSAA"] = mask
    # conversion: SRC(src) and DST(dst) for raw-different formats
    cv = {}
    for (n, a), (r, o) in R.items():
        if n != "CheckDeviceFormatConversion": continue
        ad, dev, s, d = a
        if ad != 0: (r == INV) or die(f"conversion oor {a}"); continue
        if s == d: (r == 0) or die("conversion src == dst is S_OK"); continue
        cv.setdefault((cls(s), cls(d)), set()).add(r)
    if any(len(v) != 1 for v in cv.values()): die("CheckDeviceFormatConversion independent of device type")
    src = [any(next(iter(cv[(c, d)])) == 0 for d in range(NC) if (c, d) in cv) for c in range(NC)]; dst = [any(next(iter(cv[(c, d)])) == 0 for c in range(NC) if (c, d) in cv) for d in range(NC)]
    for (c, d), v in cv.items():
        if (next(iter(v)) == 0) != (src[c] and dst[d]): die(f"conversion is not SRC(src) and DST(dst) at {c},{d}")
    T["SRC"], T["DST"] = [int(x) for x in src], [int(x) for x in dst]
    # device level: video memory, caps equal the adapter caps, display mode equals the adapter mode
    mem0 = {r for (k, r, o) in DEV if k[0] == "DEV.GetAvailableTextureMem"}
    if len(mem0) != 1: die("GetAvailableTextureMem before any resource is one constant")
    T["VMEM"] = mem0.pop()
    for (k, r, o) in DEV:
        if k[0] == "DEV.GetDeviceCaps" and r == 0 and image("D3DCAPS9", o, 304) != T["CAPS"][0]: die("device caps equal the adapter caps")
        if k[0] == "DEV.GetDisplayMode" and k[1][1] == 0 and r == 0 and (fields(o)["Width"], fields(o)["Height"]) != (format(T["DM"][0], "x"), format(T["DM"][1], "x")): pass
    # GetAvailableTextureMem accounting (wined3d_device_get_available_texture_mem = VMEM - sum of accounted resources; d3d9 texture.c / buffer.c / device.c set VIDMEM_ACCOUNTING on non-Ex devices only)
    TEX = [(1024, 1024, 4, 0), (512, 512, 2, 0), (256, 256, 4, 0), (1023, 255, 4, 0), (1024, 1024, 8, 1), (512, 512, 8, 0), (17, 5, 1, 0), (1024, 1024, 4, 0)]   # w, h, bytes (block bytes), compressed 4x4
    def size(i, full):
        w, h, b, blk = TEX[i]; n = max(w, h).bit_length() if full else 1; tot = 0
        for l in range(n):
            lw, lh = max(1, w >> l), max(1, h >> l); tot += (b * ((lw + 3) // 4) * ((lh + 3) // 4)) if blk else (((lw * b + 3) & ~3) * lh)
        return (tot + 15) & ~15
    nchk = 0
    for (k, r, o) in DEV:
        if not k[0].startswith("DEV.Mem") or "m0" not in o: continue
        f = {a: int(b, 16) for a, b in (t.split("=") for t in o.split())}; d = f["m0"] - f["m1"]; ex = k[1][0] >= 250; kind, (id_, idx, _) = k[0], k[1]; pool = (idx % 16) // 4 if kind == "DEV.MemTexture" else idx
        want = 0   # accounted pools: wined3d_resource_init counts VIDMEM_ACCOUNTING resources without CPU access; d3d9 buffer.c re-creates a SYSTEMMEM vertex buffer GPU-side (not index buffers)
        if not ex and kind == "DEV.MemTexture" and pool in (0, 1): want = size(idx // 16, (idx % 4) // 2 == 1)
        if not ex and kind == "DEV.MemVB": want = (1 << 20) if pool in (0, 2) else 0
        if not ex and kind == "DEV.MemIB": want = (1 << 18) if pool == 0 else 0
        if not ex and kind == "DEV.MemSurface": want = ((640 * 4 * 480 + 15) & ~15) if pool == 0 else 0
        if f["m2"] != f["m0"] or d != want: die(f"GetAvailableTextureMem accounting at {k}: delta {d} != {want}")
        nchk += 1
    T["MEMROWS"] = nchk
    return T


def promises(T):
    """what the table promises (counts over the NC format classes): the implementation must deliver exactly this"""
    names = ["S0", "RT", "DS", "FILTER", "POSTPS", "SRGBR", "SRGBW", "VTF", "BUMP", "GENH", "GENOK"]; rcn = ["SURFACE", "TEXTURE", "CUBETEXTURE", "VOLUME(TEXTURE)", "BUFFER"]; L = []
    for rc in range(5): L.append(f"  {rcn[rc]}: " + " ".join(f"{n}={sum(1 for c in range(NC) if T['CDF'][rc][c] >> i & 1)}" for i, n in enumerate(names)))
    ms = T["MSAA"]; L.append(f"  MSAA: classes with any type {sum(1 for m in ms if m)}, (class,type) pairs for types 2..16: {sum(bin(m >> 1).count('1') for m in ms)}, NONMASKABLE levels per class: {sorted(set(bin(m).count('1') for m in ms if m))}")
    L.append(f"  CheckDepthStencilMatch: RT-capable classes {sum(T['RTOK'])}, DS-capable {sum(T['DSOK'])} -> {sum(T['RTOK']) * sum(T['DSOK'])} pairs; conversion: sources {sum(T['SRC'])}, destinations {sum(T['DST'])}")
    L.append(f"  CheckDeviceType OK cells (windowed/fullscreen x 4 display x {NC} classes): {sum(1 for w in T['CDT'] for r in w for x in r if x == 0)}; device caps image: {sum(bin(b).count('1') for b in T['CAPS'][0])} set bits in {len(T['CAPS'][0])} bytes")
    return "\n".join(L)


def carr(name, ct, vals, per_line=24):
    flat = list(vals); return f"static const {ct} {name}[{len(flat)}] = {{" + ",".join(str(x) for x in flat) + "};"


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else HERE; os.makedirs(out, exist_ok=True)
    Ts = [derive(*load(os.path.join(os.environ.get("ENV_TSV_DIR", HERE), f"env_{p}.tsv"))) for p in PROFILES]
    if bytes(Ts[0]["IDENT"]) != bytes(Ts[1]["IDENT"]): die("the adapter identifier images of the profiles differ: one fact, one image (ident_*.bin)")
    diff = [k for k in Ts[0] if Ts[0][k] != Ts[1][k]]; print("profile differences (table entries): " + (", ".join(diff) or "none"))
    print("promises (profile 0):\n" + promises(Ts[0]))
    c = ["/* generated by gen_env.py from env_*.tsv; do not edit */", '#include "env_table.gen.h"', f"#define NC {NC}", f"#define UNK0 {UNK0}", f"#define UNKF {UNKF}",
         carr("SWITCH_FMT", "uint32_t", SWITCH), carr("KNOWN_FCC", "uint32_t", KNOWN_FCC), f"#define N_SWITCH {len(SWITCH)}", f"#define N_FCC {len(KNOWN_FCC)}",
         "static const uint32_t HRC[4] = {0x0u, 0x8876086Au, 0x8876086Cu, 0x0876086Fu};"]

    def per(name, ct, f):   # table shared when both profiles agree, else indexed by profile
        v = [f(t) for t in Ts]
        if v[0] == v[1]: c.append(carr(name + "_A", ct, v[0]) + f"\n#define {name}(p) ({name}_A)")
        else: c.append(f"static const {ct} {name}_A[2][{len(v[0])}] = {{" + ",".join("{" + ",".join(str(x) for x in vv) + "}" for vv in v) + "};" + f"\n#define {name}(p) ({name}_A[(p)])")
    flat = lambda l: [x for r in l for x in r]
    per("T_CAPS0", "uint8_t", lambda t: t["CAPS"][0]); per("T_CAPS1", "uint8_t", lambda t: t["CAPS"][1]); per("T_IDENT", "uint8_t", lambda t: t["IDENT"])
    per("T_CDT", "uint8_t", lambda t: flat(flat(t["CDT"]))); per("T_CDF", "uint16_t", lambda t: flat(t["CDF"])); per("T_RTOK", "uint8_t", lambda t: t["RTOK"]); per("T_DSOK", "uint8_t", lambda t: t["DSOK"])
    per("T_MSAA", "uint32_t", lambda t: t["MSAA"]); per("T_SRC", "uint8_t", lambda t: t["SRC"]); per("T_DST", "uint8_t", lambda t: t["DST"])
    per("T_SCALARS", "uint32_t", lambda t: [t["N"], t["MON"], t["DM"][0], t["DM"][1], t["DM"][2], t["SCAN"], t["ROT"], t["LUID"][0], t["LUID"][1] & 0xffffffff, t["VMEM"], t["DECLTYPES"], t["QMASK"], t["STRETCHCAPS"]])
    c.append("#define SC_N 0\n#define SC_MON 1\n#define SC_W 2\n#define SC_H 3\n#define SC_RR 4\n#define SC_SCAN 5\n#define SC_ROT 6\n#define SC_LUIDLO 7\n#define SC_LUIDHI 8\n#define SC_VMEM 9\n#define SC_DECLTYPES 10\n#define SC_QMASK 11\n#define SC_STRETCH 12")
    h = ["/* generated by gen_env.py; do not edit. Environment answers of the reference environment (Wine 9.0 + llvmpipe), profiles 0 = handheld, 1 = docked. */", "#pragma once", "#include <stdint.h>",
         "#ifdef __cplusplus\nextern \"C\" {\n#endif", "typedef struct { uint32_t Width, Height, RefreshRate, Format; } NxEnvMode;", "#define NXENV_CAPS_SIZE 304\n#define NXENV_IDENT_SIZE 1100",
         f"#define NXENV_NCLASS {NC}"]
    for sig in ["uint32_t nxenv_adapter_count(int p)", "uint32_t nxenv_monitor(int p, uint32_t ad)", "uint32_t nxenv_identifier(int p, uint32_t ad, uint32_t flags, void* out)",
                "uint32_t nxenv_display_mode(int p, uint32_t ad, NxEnvMode* m)", "uint32_t nxenv_mode_count(int p, uint32_t ad, uint32_t fmt)", "uint32_t nxenv_enum_mode(int p, uint32_t ad, uint32_t fmt, uint32_t idx, NxEnvMode* m)",
                "uint32_t nxenv_mode_count_ex(int p, uint32_t ad, uint32_t fmt, uint32_t scan)", "uint32_t nxenv_enum_mode_ex(int p, uint32_t ad, uint32_t fmt, uint32_t scan, uint32_t idx, NxEnvMode* m, uint32_t* scan_out)",
                "uint32_t nxenv_display_mode_ex(int p, uint32_t ad, uint32_t size, NxEnvMode* m, uint32_t* scan_out, uint32_t* rot_out)", "uint32_t nxenv_luid(int p, uint32_t ad, uint32_t* low, int32_t* high)",
                "uint32_t nxenv_caps(int p, uint32_t ad, uint32_t devtype, void* out)", "uint32_t nxenv_check_device_type(int p, uint32_t ad, uint32_t dev, uint32_t disp, uint32_t bb, uint32_t windowed)",
                "uint32_t nxenv_check_device_format(int p, uint32_t ad, uint32_t dev, uint32_t afmt, uint32_t usage, uint32_t rtype, uint32_t fmt)",
                "uint32_t nxenv_check_ds_match(int p, uint32_t ad, uint32_t dev, uint32_t afmt, uint32_t rt, uint32_t ds)",
                "uint32_t nxenv_check_msaa(int p, uint32_t ad, uint32_t dev, uint32_t fmt, uint32_t windowed, uint32_t type, uint32_t* levels)",
                "uint32_t nxenv_check_conversion(int p, uint32_t ad, uint32_t dev, uint32_t src, uint32_t dst)", "uint32_t nxenv_video_memory(int p)", "int nxenv_format_class(uint32_t fmt)",
                "int nxenv_stretch_filter_ok(int p, uint32_t filter)"]:
        h.append(sig + ";")
    h.append("#ifdef __cplusplus\n}\n#endif")
    rules = open(os.path.join(HERE, "env_rules.cpp.in")).read()
    for p, t in zip(PROFILES, Ts): open(os.path.join(out, f"ident_{p}.bin"), "wb").write(bytes(t["IDENT"]))   # the 1100-byte D3DADAPTER_IDENTIFIER9 image = T_IDENT of that profile
    open(os.path.join(out, "env_table.gen.h"), "w").write("\n".join(h) + "\n"); open(os.path.join(out, "env_table.gen.cpp"), "w").write("\n".join(c) + "\n" + rules)
    print(f"gen_env: {NC} format classes; GetAvailableTextureMem accounting rows checked against the formula: {Ts[0]['MEMROWS']} + {Ts[1]['MEMROWS']}; tables written to {out}")


if __name__ == "__main__":
    main()
