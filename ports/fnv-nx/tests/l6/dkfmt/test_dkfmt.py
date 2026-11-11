#!/usr/bin/env python3
"""test_dkfmt.py: tests of render/backend/nx/dk_fmt.c (NxFormat -> DkImageFormat, 35 rows).
    python3 -I tests/l6/dkfmt/test_dkfmt.py [--no-mutations]        needs gcc and the built deko3d (FNVNX_DK, default /home/user/work/l6/dk)
 a  dk_facts.tsv == a fresh extraction from the pinned headers and library source (the compiler evaluates everything)
 b  dk_fmt.c compiles against the real deko3d.h (-Werror=switch-enum; static_asserts of every enumerator value inside)
 c  per row, against the TSV (pinned format_traits.inc) and an independent layout oracle written from the NxFormat contract (state.h):
    the channel (bit offset, width, numeric kind) every sampled channel reads, after the row swizzle, equals the oracle; bytes per block,
    block size, depth / stencil bits, sRGB view (layout identical to the base, flag set) equal the Dk traits AND the GL backend (gl4_fmt.c)
 d  flags: ROT8 iff the depth words differ, XFILL iff the NxFormat has an x channel, RENDER iff deko3d renders the format and the swizzle is
    the identity; the front's render-target formats (front_fmt.c) without RENDER are listed as NOT CLOSED
 e  the composition with the front's NxFormatInfo swizzle equals an independent evaluation, for every row the front can name
 f  mutations of a copy of dk_fmt.c: every one must be caught (build or check)
Output: 'rows mapped N/35', the not-closed list, mutations caught, ALL OK."""
import os, re, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
BACK = os.path.join(ROOT, "render", "gl4"); NXB = os.path.normpath(os.path.join(BACK, "..", "backend")); NXD = os.path.join(os.path.normpath(os.path.join(BACK, "..", "backend")), "nx"); FRONT = os.path.join(ROOT, "render", "d3d9", "front")
DK = os.environ.get("FNVNX_DK", "/home/user/work/l6/dk")
INC = os.path.join(DK, "deko3d", "include"); SRC = os.path.join(DK, "deko3d", "src", "deko3d")
CROSS = os.path.join(DK, "dkp", "devkitA64", "bin", "aarch64-none-elf-gcc")
TSV = os.path.join(NXD, "dk_facts.tsv")

def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)

# ---- independent oracle: the NxFormat contract of state.h (channels listed from the least significant bit) ----------------------------------
NXN = {1: "RGBA8", 2: "BGRA8", 3: "BC1", 4: "BC2", 5: "BC3", 6: "R8", 7: "RG8", 8: "R5G6B5", 9: "RGBA16F", 10: "R32F", 11: "Z24S8", 12: "Z32F",
       13: "BGRX8", 14: "RGBX8", 15: "A1R5G5B5", 16: "X1R5G5B5", 17: "A4R4G4B4", 18: "RGB10A2", 19: "RG16", 20: "RGBA16", 21: "R16", 22: "RG8S",
       23: "RGBA8S", 24: "RG16S", 25: "RGBA16S", 26: "R16F", 27: "RG16F", 28: "RG32F", 29: "RGBA32F", 30: "BC4", 31: "BC5", 32: "Z16", 33: "Z24X8",
       34: "Z32FS8", 35: "BGR10A2"}
PACK = {"RGBA8": ("RGBA", [8] * 4, "U"), "BGRA8": ("BGRA", [8] * 4, "U"), "BGRX8": ("BGRX", [8] * 4, "U"), "RGBX8": ("RGBX", [8] * 4, "U"),
        "R5G6B5": ("BGR", [5, 6, 5], "U"), "A1R5G5B5": ("BGRA", [5, 5, 5, 1], "U"), "X1R5G5B5": ("BGRX", [5, 5, 5, 1], "U"),
        "A4R4G4B4": ("BGRA", [4] * 4, "U"), "RGB10A2": ("RGBA", [10, 10, 10, 2], "U"), "BGR10A2": ("BGRA", [10, 10, 10, 2], "U"),
        "R8": ("R", [8], "U"), "RG8": ("RG", [8, 8], "U"), "R16": ("R", [16], "U"), "RG16": ("RG", [16, 16], "U"), "RGBA16": ("RGBA", [16] * 4, "U"),
        "RG8S": ("RG", [8, 8], "S"), "RGBA8S": ("RGBA", [8] * 4, "S"), "RG16S": ("RG", [16, 16], "S"), "RGBA16S": ("RGBA", [16] * 4, "S"),
        "R16F": ("R", [16], "F"), "RG16F": ("RG", [16, 16], "F"), "RGBA16F": ("RGBA", [16] * 4, "F"), "R32F": ("R", [32], "F"),
        "RG32F": ("RG", [32, 32], "F"), "RGBA32F": ("RGBA", [32] * 4, "F")}
BC = {"BC1": ("DXT1", 8, "RGBA"), "BC2": ("DXT23", 16, "RGBA"), "BC3": ("DXT45", 16, "RGBA"), "BC4": ("DXN1", 8, "R"), "BC5": ("DXN2", 16, "RG")}
KIND = {"Unorm": "U", "Snorm": "S", "Float": "F", "Uint": "I", "Sint": "J"}

def oracle_channels(name):
    """-> [(offset, width, kind) | 'Z' | 'O' | 'X'] for the sampled r, g, b, a of an uncompressed colour NxFormat; None for others"""
    if name not in PACK: return None
    order, widths, kind = PACK[name]
    pos, off = {}, 0
    for c, w in zip(order, widths): pos[c] = (off, w, kind); off += w
    out = []
    for c in "RGB": out.append(pos[c] if c in pos else "Z")
    out.append(pos["A"] if "A" in pos else ("X" if "X" in pos else "O"))
    return out

# ---- the Dk side from the TSV ---------------------------------------------------------------------------------------------------------------
def load_tsv():
    facts = {"enum": {}, "layout": {}}
    for line in open(TSV).read().splitlines()[1:]:
        k, n, v = line.split("\t")
        if k == "layout":
            facts["layout"][n] = dict(p.split("=", 1) for p in v.split(";"))
        elif k.startswith("Dk"):
            facts["enum"].setdefault(k, {})[n] = int(v)
    return facts

def dk_channels(lay, view):
    """sampled r,g,b,a of a Dk format through the view swizzle (DkImageSwizzle numbers) -> same notation as oracle_channels.
    Positional channels R,G,B,A of the traits row, R in the least significant bits (hardware TIC order); tic_swz[i] selects the channel sampled as i."""
    w = [int(x) for x in lay["bits_rgbazs"].split(",")][:4]
    comp = lay["tic_comp"].split(","); sw = lay["tic_swz"].split(",")
    offs, o = [], 0
    for x in w: offs.append(o); o += x
    def own(i):                      # the format's own sampled channel i
        s = sw[i]
        if s in "RGBA" and len(s) == 1:
            p = "RGBA".index(s); return (offs[p], w[p], KIND[comp[p]])
        return "Z" if s == "Zero" else "O"
    outs = []
    for i in range(4):
        v = view[i]
        outs.append("Z" if v == 0 else "O" if v == 1 else own(v - 2))
    return outs

# ---- builds ----------------------------------------------------------------------------------------------------------------------------------
HARNESS = r'''
#include <stdio.h>
#include "dk_fmt.h"
#include "front_int.h"
int main(void) {
  for (uint32_t n = 0; n <= NxFmt_Count; n++) { DkFmt f = dk_fmt(n);
    printf("R %u %u %u %u %u %u %u %u %u %u\n", n, (unsigned)f.format, (unsigned)f.srgb, f.swizzle[0], f.swizzle[1], f.swizzle[2], f.swizzle[3], f.bpb, f.flags, 0u); }
  printf("R 4096 %u 0 0 0 0 0 0 0 0\n", (unsigned)dk_fmt(4096).format);
  for (uint32_t d = 0; d < 2048; d++) { NxFmtRow r = nxfront_fmt_row(d); if (!r.info.format) continue;
    DkFmt f = dk_fmt(r.info.format); DkImageSwizzle o[4]; dk_fmt_view_swizzle(&f, r.info.swizzle, o);
    printf("F %u %u %u %u %u %u %u %u\n", d, r.info.format, r.info.flags, r.info.swizzle[0], r.info.swizzle[1], r.info.swizzle[2], r.info.swizzle[3], 0u);
    printf("V %u %u %u %u %u\n", d, o[0], o[1], o[2], o[3]); }
  return 0; }
'''
GLH = r'''
#include <stdio.h>
#include "gl4.h"
int main(void) { for (uint32_t n = 1; n < NxFmt_Count; n++) { Gl4Fmt f = gl4_fmt(n);
  printf("G %u %u %u %u %u %u\n", n, f.bpp, f.compressed, f.depth, f.stencil, f.srgb != 0); } return 0; }
'''

def build_run(d, name, srcs, incs, code, extra=()):
    c = os.path.join(d, name + ".c"); exe = os.path.join(d, name)
    open(c, "w").write(code)
    r = sh(["gcc", "-std=c11", "-Wall", "-Werror", "-Werror=switch-enum", "-Wno-pragma-once-outside-header", *sum((["-I", i] for i in incs), []), c, *srcs, *extra, "-o", exe])
    if r.returncode: return None, r.stderr[-600:]
    r = sh([exe])
    return (r.stdout if r.returncode == 0 else None), r.stderr[-300:]

def gl_rows(d):
    out, err = build_run(d, "glh", [os.path.join(BACK, "gl4_fmt.c")], [BACK], GLH)
    if out is None: sys.exit("gl4_fmt harness failed: " + err)
    return {int(l.split()[1]): [int(x) for x in l.split()[2:]] for l in out.splitlines()}

def check(d, dkc, gl, facts, quiet=True):
    """-> (failures, report) for one dk_fmt.c"""
    fails, rep = [], {}
    out, err = build_run(d, "dkh", [dkc, os.path.join(FRONT, "front_fmt.c")], [INC, NXD, FRONT, BACK], HARNESS)
    if out is None: return ["build: " + err.strip().splitlines()[-1][:200] if err.strip() else "build failed"], rep
    names = {v: k for k, v in facts["enum"]["DkImageFormat"].items()}
    rows, front, views = {}, {}, {}
    for l in out.splitlines():
        p = l.split(); k = p[0]; v = [int(x) for x in p[1:]]
        if k == "R": rows[v[0]] = v[1:]
        elif k == "F": front[v[0]] = v[1:]
        elif k == "V": views[v[0]] = v[1:]
    if rows[0][0] or rows[36][0] or rows[4096][0]: fails.append("rows 0, Count, 4096 must be the zero row")
    lay = facts["layout"]; mapped = 0
    def L(fid): return lay[names[fid]] if fid in names and names[fid] in lay else None
    for n in range(1, 36):
        nm = NXN[n]; fmt, srgb, s0, s1, s2, s3, bpb, fl, _ = rows[n]; sw = [s0, s1, s2, s3]
        row_fails = len(fails)
        La = L(fmt)
        if not La: fails.append("%s: format %d is no DkImageFormat with a traits row" % (nm, fmt)); continue
        gb, gc, gd, gs, gsrgb = gl[n][0:5]
        if bpb != int(La["bytes_per_block"]) or bpb != gb: fails.append("%s: bytes per block row %d, traits %s, GL %d" % (nm, bpb, La["bytes_per_block"], gb))
        comp = La["block"] != "1x1"
        if comp != bool(fl & 1) or comp != bool(gc): fails.append("%s: compressed flag/block mismatch" % nm)
        depth, stencil = La["depth_kind"] != "-", int(La["bits_rgbazs"].split(",")[5]) > 0
        if depth != bool(fl & 2) or depth != bool(gd): fails.append("%s: depth flag" % nm)
        if stencil != bool(fl & 4) or stencil != bool(gs): fails.append("%s: stencil flag" % nm)
        # sRGB view: layout identical to the base format, the sRGB flag set, present iff the GL backend has one
        if (srgb != 0) != bool(gsrgb): fails.append("%s: sRGB view presence row %d GL %d" % (nm, srgb != 0, gsrgb))
        if srgb:
            Ls = L(srgb)
            if not Ls or Ls["srgb"] != "1" or La["srgb"] != "0": fails.append("%s: sRGB view traits" % nm)
            elif any(Ls[k] != La[k] for k in ("bits_rgbazs", "bytes_per_block", "block", "tic", "tic_comp")) or \
                    (Ls["tic_swz"] != La["tic_swz"]): fails.append("%s: sRGB view layout differs from the base" % nm)
        if nm in BC:
            tic, bb, chans = BC[nm]
            exp = {"RGBA": [2, 3, 4, 5], "R": [2, 0, 0, 1], "RG": [2, 3, 0, 1]}[chans]
            if La["tic"] != tic or bb != bpb or La["block"] != "4x4" or "Unorm" not in La["tic_comp"].split(",")[0]: fails.append("%s: BC layout" % nm)
            if dk_channels(La, sw) != dk_channels(La, exp): fails.append("%s: BC channels" % nm)
            if fl != 1: fails.append("%s: BC flags %d" % (nm, fl))
        elif nm in PACK:
            ora = oracle_channels(nm); got = dk_channels(La, sw)
            if len(PACK[nm][0]) and sum(PACK[nm][1]) != 8 * bpb: fails.append("%s: oracle width sum" % nm)
            for i, (o, g) in enumerate(zip(ora, got)):
                if o == "X" and i == 3: continue                       # an x channel is not data: the front forces alpha to 1
                if o != g: fails.append("%s: sampled %s: oracle %s, row gives %s" % (nm, "rgba"[i], o, g))
            if o == "X" and not La["tic_swz"].endswith("One" + ("Int" if "Int" in La["tic_swz"] else "Float")): fails.append("%s: x not constant 1" % nm)
            x = ora[3] == "X"
            if x != bool(fl & 0x10): fails.append("%s: XFILL flag" % nm)
            rend = La["render"] == "1" and sw == [2, 3, 4, 5]
            if rend != bool(fl & 0x20): fails.append("%s: RENDER flag %d, expected %d" % (nm, bool(fl & 0x20), rend))
            if fl & 0x0f: fails.append("%s: unexpected flags %d" % (nm, fl))
        else:                                                              # depth / stencil
            zb = [int(x) for x in La["bits_rgbazs"].split(",")]; z, s = zb[4], zb[5]
            want_z, want_s = {"Z16": (16, 0), "Z24X8": (24, 0), "Z24S8": (24, 8), "Z32F": (32, 0), "Z32FS8": (32, 8)}[nm]
            if (z, s) != (want_z, want_s): fails.append("%s: depth/stencil widths %d/%d" % (nm, z, s))
            # Dk Z24 words are S8Z24 (render format name, most significant first), the contract is depth24<<8|x8: a rotate by 8
            dk_rot = nm in ("Z24X8", "Z24S8") and La["render_fmt"] == "S8Z24Unorm"
            if dk_rot != bool(fl & 8): fails.append("%s: ROT8 flag" % nm)
            if (nm == "Z24X8") != bool(fl & 0x10): fails.append("%s: XFILL flag" % nm)
            if fl & 0x20 or not fl & 2: fails.append("%s: depth flags" % nm)
            if nm == "Z32FS8" and (La["depth_kind"] != "ZF32_X24S8" or bpb != 8): fails.append("Z32FS8: ZF32_X24S8 layout")
        mapped += len(fails) == row_fails
    rep["mapped"] = mapped
    # d: the front's render-target formats
    nc = set()
    for d_, v in front.items():
        nxf, flags = v[0], v[1]
        if flags & 0x10 and not rows[nxf][7] & 0x20: nc.add((NXN[nxf], d_))
    rep["notclosed"] = sorted({a for a, _ in nc})
    # e: composition of the front swizzle
    for d_, v in front.items():
        nxf, isw = v[0], v[2:6]; row = rows[nxf]; rsw = row[2:6]; exp = []
        for s in isw: exp.append(0 if s == 4 else 1 if s == 5 else rsw[s])
        if views[d_] != exp: fails.append("view swizzle composition, D3DFORMAT %d" % d_); break
    return fails, rep

def mutations(src):
    M = [("BGRA8 row uses RGBA8_Unorm", "ROW(BGRA8_Unorm, DkImageFormat_BGRA8_Unorm_sRGB", "ROW(RGBA8_Unorm, DkImageFormat_BGRA8_Unorm_sRGB"),
         ("R5G6B5 uses RGBA4", "ROW(BGR565_Unorm", "ROW(RGBA4_Unorm"),
         ("A4R4G4B4 loses its swizzle", "ROW(RGBA4_Unorm, NS, BGRA", "ROW(RGBA4_Unorm, NS, RGBA"),
         ("BGR10A2 claims render", "ROW(RGB10A2_Unorm, NS, BGRA, 4, 0)", "ROW(RGB10A2_Unorm, NS, BGRA, 4, CR)"),
         ("Z24S8 loses ROT8", "DKFMT_DEPTH | DKFMT_STENCIL | DKFMT_ROT8", "DKFMT_DEPTH | DKFMT_STENCIL"),
         ("RGBA32F bytes", "ROW(RGBA32_Float, NS, RGBA, 16", "ROW(RGBA32_Float, NS, RGBA, 8"),
         ("BC3 sRGB view is BC2", "DkImageFormat_RGBA_BC3_sRGB, RGBA, 16", "DkImageFormat_RGBA_BC2_sRGB, RGBA, 16"),
         ("X1R5G5B5 loses XFILL", "CR | DKFMT_XFILL);\n    case NxFmt_A4R4G4B4", "CR);\n    case NxFmt_A4R4G4B4"),
         ("enumerator assert value", "DkImageFormat_RG8_Unorm == 13", "DkImageFormat_RG8_Unorm == 14"),
         ("Z32F becomes ZF32_X24S8", "ROW(ZF32, NS", "ROW(ZF32_X24S8, NS"),
         ("RG16 uses RG16_Snorm", "ROW(RG16_Unorm", "ROW(RG16_Snorm"),
         ("BC1 sRGB view dropped", "DkImageFormat_RGBA_BC1_sRGB, RGBA", "NS, RGBA"),
         ("Z24X8 stencil flag", "DKFMT_DEPTH | DKFMT_ROT8 | DKFMT_XFILL", "DKFMT_DEPTH | DKFMT_STENCIL | DKFMT_ROT8 | DKFMT_XFILL"),
         ("swizzle composition ignores constants", "s == NxSwz_0 ? DkImageSwizzle_Zero : s == NxSwz_1 ? DkImageSwizzle_One : ", "")]
    for desc, a, b in M:
        if src.count(a) != 1: sys.exit("mutation %r: pattern occurs %d times" % (desc, src.count(a)))
    return [(d, src.replace(a, b)) for d, a, b in M]

def main():
    muts = "--no-mutations" not in sys.argv
    facts = load_tsv(); n_ok = 0
    # a: the facts are a fresh extraction
    r = sh([sys.executable, "-I", os.path.join(NXD, "dk_facts.py"), "--inc", INC, "--sha", open(TSV).read().split("\n")[1].split("\t")[2], "--src", SRC,
            "--cross", CROSS, "--check", TSV])
    print("a  dk_facts.tsv == fresh extraction (host probe + aarch64 static asserts):", "ok" if r.returncode == 0 else "FAIL " + (r.stderr or r.stdout)[-300:])
    if r.returncode: sys.exit(1)
    for k, v in sorted(facts["enum"].items()): pass
    with tempfile.TemporaryDirectory() as d:
        gl = gl_rows(d)
        fails, rep = check(d, os.path.join(NXD, "dk_fmt.c"), gl, facts)
        for f in fails: print("FAIL", f)
        print("c/d/e  rows mapped %d/35; not closed (front render-target format without a deko3d render equivalent): %s" % (rep.get("mapped", 0), ", ".join(rep.get("notclosed", [])) or "none"))
        if fails: sys.exit(1)
        if muts:
            src = open(os.path.join(NXD, "dk_fmt.c")).read(); caught = 0; ms = mutations(src)
            for desc, text in ms:
                c = os.path.join(d, "mut"); os.makedirs(c, exist_ok=True); p = os.path.join(c, "dk_fmt.c"); open(p, "w").write(text)
                mf, _ = check(d, p, gl, facts)
                print("  mutation %-42s %s" % (desc, "caught (" + mf[0][:60] + ")" if mf else "NOT CAUGHT"))
                caught += bool(mf)
            print("f  mutations caught %d/%d" % (caught, len(ms)))
            if caught != len(ms): sys.exit(1)
    print("ALL OK")

if __name__ == "__main__":
    main()
