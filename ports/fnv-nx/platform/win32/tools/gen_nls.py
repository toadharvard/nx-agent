#!/usr/bin/env python3
"""gen_nls.py - generates platform/win32/nls_tables.inc for platform/win32/nls.cpp.

    python3 platform/win32/tools/gen_nls.py <nls-dir> <out.inc>

<nls-dir> holds the NLS data files of the reference environment, Wine 9.0 (nls/*.nls, LGPL-2.1-or-later; they are not
part of this repository): c_1252, c_437, c_10000, c_20127, c_28591, locale, sortdefault, normnfc (.nls).
No table is derived from any other source: every array in the output is a slice of one of those files, or a lossless
re-encoding of one (256-entry block deduplication of the wide->multibyte tables, a string pool reduced to the strings
the two supported locales reach). nls.cpp applies the algorithms Wine's ntdll/kernelbase apply to the same layouts.
Python 3 standard library only.
"""
import struct
import sys

SRC = sys.argv[1].rstrip("/") + "/"
OUT = []
CODEPAGES = (1252, 437, 10000, 20127, 28591)


def emit(s=""):
    OUT.append(s)


def arr(ctype, name, vals, per=16):
    emit("static const %s %s[%d] = {" % (ctype, name, len(vals)))
    for i in range(0, len(vals), per):
        emit(",".join("0x%x" % v for v in vals[i:i + per]) + ",")
    emit("};")


def words(data, off=0, n=None):
    n = (len(data) - off) // 2 if n is None else n
    return list(struct.unpack_from("<%dH" % n, data, off))


def rd(name):
    return open(SRC + name, "rb").read()


# ---- code pages (c_*.nls): header, multibyte->wide, optional glyph table, wide->multibyte
cpinfo = []
for cp in CODEPAGES:
    d = rd("c_%d.nls" % cp)
    w = words(d)
    hdr = w[0]
    assert w[1] == cp and w[2] == 1, "single-byte code pages only"
    p = hdr + 1                       # words[hdr] = offset of the wide->multibyte table, relative to words[hdr+1]
    mb = w[p:p + 256]
    glyph = w[p + 257:p + 513] if w[p + 256] else []
    wcoff = hdr + 1 + w[hdr]
    wc = d[2 * wcoff:2 * wcoff + 65536]
    assert len(wc) == 65536
    blocks, idx = [], []
    for i in range(0, 65536, 256):
        b = wc[i:i + 256]
        if b not in blocks:
            blocks.append(b)
        idx.append(blocks.index(b))
    cpinfo.append((cp, w[3], w[4], w[5], w[6], len(glyph) > 0))
    arr("uint16_t", "kcp%d_mb" % cp, mb)
    if glyph:
        arr("uint16_t", "kcp%d_glyph" % cp, glyph)
    arr("uint16_t", "kcp%d_wcidx" % cp, idx)
    arr("uint8_t", "kcp%d_wc" % cp, list(b"".join(blocks)), 32)

# ---- sortdefault.nls: sort keys (the default sort), casemaps, ctype tables, expansions, jamo
sd = rd("sortdefault.nls")
sk, cm, ct, sid = struct.unpack_from("<4I", sd, 0)
keys = list(struct.unpack_from("<65536I", sd, sk))
casemap = words(sd[cm:ct])
c = struct.unpack_from("<2H", sd, ct)
ctw = words(sd, ct, c[1] // 2 + 1)
ctypes = ctw[2:2 + (c[1] - 2) // 2]
ctidx = list(sd[ct + c[1] + 2:sid])
nguid = struct.unpack_from("<I", sd, sid + 4)[0]
g0 = sid + 8                         # the first GUID is the default sort (00000001-57ee-1e5c-00b4-d0000bb1e11e)
assert sd[g0:g0 + 16].hex() == "01000000ee575c1e00b4d0000bb1e11e"
gflags, gcompr, gexc, glexc, gcase = struct.unpack_from("<5I", sd, g0 + 16)
assert gcompr == 0xFFFFFFFF and gexc == 0, "default sort: no compression table, no exception table"
tbl = sid + 8 + nguid * 36
nexp = struct.unpack_from("<I", sd, tbl)[0]
expansions = words(sd, tbl + 4, nexp * 2)
tbl += 4 + nexp * 4
ncompr = struct.unpack_from("<I", sd, tbl)[0]
comprs = [struct.unpack_from("<IHH8H", sd, tbl + 4 + i * 24) for i in range(ncompr)]
last = comprs[-1]
t = tbl + 4 + ncompr * 24 + last[0] * 2
for i in range(7):
    t += last[3 + i] * ((i + 5) // 2) * 4
t += 4 * (1 + struct.unpack_from("<I", sd, t)[0] // 2)   # multiple weights
njamo = struct.unpack_from("<I", sd, t)[0]
jamo = list(sd[t + 4:t + 4 + njamo * 8])
arr("uint32_t", "ksort_keys", keys, 8)
arr("uint16_t", "ksort_expansions", expansions)
arr("uint8_t", "ksort_jamo", jamo, 32)
arr("uint16_t", "ksort_casemap", casemap)
arr("uint16_t", "kctypes", ctypes)
arr("uint8_t", "kctype_idx", ctidx, 32)
emit("enum { KSORT_FLAGS = 0x%x, KSORT_LING_CASEMAP = %d };" % (gflags, gcase))

# ---- normnfc.nls (decomposition and composition tables used by MB_COMPOSITE, WC_COMPOSITECHECK, NORM_IGNORENONSPACE)
nfc = rd("normnfc.nls")
arr("uint16_t", "knorm", words(nfc), 16)

# ---- locale.nls: charmaps (hiragana, katakana, halfwidth, fullwidth, traditional, simplified), two locale records
ln = rd("locale.nls")
hdr = struct.unpack_from("<8I", ln, 0)
p = hdr[5]
cmaps = []
for i in range(8):
    n = struct.unpack_from("<H", ln, p)[0]
    cmaps.append(words(ln, p + 2, n - 1))
    p += 2 * n
for i, name in ((2, "hira"), (3, "kata"), (4, "half"), (5, "full"), (6, "trad"), (7, "simp")):
    arr("uint16_t", "kcharmap_" + name, cmaps[i])

lo = hdr[4]
(_, _, ver, magic, _, _, _, _, nlc, nloc, lsz, locoff, nlcn, _, lcid_off, lcname_off, _, ncal, calsz, caloff,
 stroff, _, _) = struct.unpack_from("<7I4HI2H3I2H4I", ln, lo)
assert magic == 0x5344534e and ver == 7 and lsz == 328
pool_src = lo + stroff
lcids = {}
for i in range(nlc):
    ident, idx, _ = struct.unpack_from("<IHH", ln, lo + lcid_off + 8 * i)
    lcids[ident] = idx
lcnames = {}
for i in range(nlcn):
    name, idx, ident = struct.unpack_from("<HHI", ln, lo + lcname_off + 8 * i)
    lcnames[name] = idx


def record(idx):
    return list(struct.unpack_from("<164H", ln, lo + locoff + idx * lsz))


def pool_str(off):                  # a string: length word, UTF-16 units, NUL
    n = struct.unpack_from("<H", ln, pool_src + 2 * off)[0]
    return words(ln, pool_src + 2 * off, n + 2)


def sname(rec):
    s = pool_str(rec[0] | rec[1] << 16)
    return "".join(chr(x) for x in s[1:-1])


def resolve(lcid):                  # NlsValidateLocale: a neutral locale resolves to its ssortlocale
    rec = record(lcids[lcid])
    if rec[12] or lcid == 0x7F:
        return rec
    name = pool_str(rec[146] | rec[147] << 16)
    for key, idx in lcnames.items():
        if "".join(chr(x) for x in pool_str(key)[1:-1]).lower() == "".join(chr(x) for x in name[1:-1]).lower():
            return record(idx)
    raise SystemExit("ssortlocale not found")


# field kinds of the 164-word record: S string, A string array (count, dwords of strings), F raw (count=len words),
# N plain 32-bit number, anything else a 16-bit value copied as is
kinds = {}
for off in (0, 2, 18, 20, 22, 24, 26, 28, 30, 32, 34, 36, 38, 40, 62, 64, 66, 68, 70, 72, 74, 76, 78, 80, 82, 84, 88,
            90, 92, 118, 120, 122, 124, 126, 128, 132, 134, 138, 146, 148, 150, 152, 156, 158, 162):
    kinds[off] = "S"
for off in (42, 44, 46, 48, 50, 52, 94, 96, 98, 100, 102, 104, 106, 108, 140, 142, 160):
    kinds[off] = "A"
kinds[86] = "F"
kinds[154] = "N"
for off in (116, 130, 136, 144):
    kinds[off] = "N"

pool = [0, 0]                       # offset 0: the empty string


def put(ws, align_dword=False):
    for i in range(0, len(pool) - len(ws) + 1):     # reuse an identical run
        if pool[i:i + len(ws)] == ws and (not align_dword or (i + 1) % 2 == 0):
            return i
    if align_dword and (len(pool) + 1) % 2:
        pool.append(0)
    pos = len(pool)
    pool.extend(ws)
    return pos


def copy_string(off):
    return 0 if off == 0 else put(pool_str(off))


def copy_record(rec):
    out = list(rec)
    for off, k in kinds.items():
        v = rec[off] | rec[off + 1] << 16
        if k == "S":
            nv = copy_string(v)
        elif k == "A":
            if v == 0:
                nv = 0
            else:
                n = struct.unpack_from("<H", ln, pool_src + 2 * v)[0]
                offs = struct.unpack_from("<%dI" % n, ln, pool_src + 2 * v + 2)
                nv = put([n] + sum(([copy_string(o) & 0xFFFF, copy_string(o) >> 16] for o in offs), []), True)
        elif k == "F":
            n = struct.unpack_from("<H", ln, pool_src + 2 * v)[0]
            nv = put(words(ln, pool_src + 2 * v, 1 + n // 2 * 2))
        else:
            nv = v
        out[off], out[off + 1] = nv & 0xFFFF, nv >> 16
    return out


us, inv = copy_record(resolve(0x409)), copy_record(resolve(0x7F))
arr("uint16_t", "klocale_us", us)
arr("uint16_t", "klocale_inv", inv)
arr("uint16_t", "klocale_pool", pool)

# ---- the calendar records of locale.nls: both locales use the Gregorian calendar (id 1, ICALENDARTYPE) and have no
# optional one; the era name (CAL_SERASTRING, the "g" of a date picture) is the first element of its era-string array
for rec in (us, inv):
    cal_pos = rec[62] | rec[63] << 16
    assert pool[cal_pos + 1] == 1 and pool[cal_pos + 2] == 0, "Gregorian calendar only, no optional calendar"
for c in range(ncal):
    cal = struct.unpack_from("<18I", ln, lo + caloff + c * calsz)
    if cal[0] & 0xFFFF == 1:
        n = struct.unpack_from("<H", ln, pool_src + 2 * cal[4])[0]
        assert n >= 1
        era = struct.unpack_from("<I", ln, pool_src + 2 * cal[4] + 2)[0]
        arr("uint16_t", "kcal_gregorian_era", pool_str(era)[1:])
        break
else:
    raise SystemExit("calendar id 1 not found")

emit("struct nls_cp { uint16_t cp, def, unidef, transdef, transunidef; const uint16_t *mb, *glyph, *wcidx; "
     "const uint8_t *wc; };")
emit("static const struct nls_cp kcodepages[%d] = {" % len(cpinfo))
for cp, d1, d2, d3, d4, g in cpinfo:
    emit("{%d,%d,%d,%d,%d,kcp%d_mb,%s,kcp%d_wcidx,kcp%d_wc}," % (cp, d1, d2, d3, d4, cp,
                                                                  "kcp%d_glyph" % cp if g else "0", cp, cp))
emit("};")

body = "\n".join(OUT) + "\n"
head = ("/* nls_tables.inc - GENERATED by platform/win32/tools/gen_nls.py; do not edit by hand.\n"
        " * Source: the .nls files of Wine 9.0 (c_1252 c_437 c_10000 c_20127 c_28591 locale sortdefault normnfc),\n"
        " * LGPL-2.1-or-later; the tables below are data derived from those files.\n"
        " * Command: python3 platform/win32/tools/gen_nls.py <nls-dir> platform/win32/nls_tables.inc */\n")
open(sys.argv[2], "w").write(head + body)
