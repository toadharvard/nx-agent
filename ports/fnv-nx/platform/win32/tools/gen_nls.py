#!/usr/bin/env python3
"""gen_nls.py - generates platform/win32/nls_tables.inc for platform/win32/nls.cpp.

    python3 platform/win32/tools/gen_nls.py > platform/win32/nls_tables.inc

Python 3 standard library only (codecs, unicodedata, sys). Every table derives from the codecs / the Unicode database
of the Python that runs this script (sys.version in the output header). That is an ASSUMPTION relative to Windows 7's
own tables; nls.cpp's header lists the places where it matters.
"""
import codecs
import sys
import unicodedata as ud

OUT = []


def emit(s=""):
    OUT.append(s)


def arr(ctype, name, vals, per=12, fmt="0x%04X"):
    emit("static const %s %s[%d] = {" % (ctype, name, len(vals)))
    for i in range(0, len(vals), per):
        emit("    " + ", ".join(fmt % v for v in vals[i:i + per]) + ",")
    emit("};")


def pairs(name, items, ctype="struct nls_pair"):
    emit("static const %s %s[%d] = {" % (ctype, name, len(items)))
    for i in range(0, len(items), 6):
        emit("    " + " ".join("{0x%04X,0x%04X}," % t for t in items[i:i + 6]))
    emit("};")


def sbcs(codec, override=None):
    t = []
    for b in range(0x80, 0x100):
        try:
            c = ord(bytes([b]).decode(codec))
        except UnicodeDecodeError:
            c = b                       # Windows 1252 maps its five undefined bytes to U+0081.. (C1 controls)
        t.append(c)
    for b, c in (override or {}).items():
        t[b - 0x80] = c
    return t


# ---- code pages (bytes 0x80..0xFF; 0x00..0x7F are ASCII in all of them)
cp1252, cp437 = sbcs("cp1252"), sbcs("cp437")
cp10000 = sbcs("mac_roman", {0xDB: 0x00A4})   # Windows 10000 maps 0xDB to the currency sign (Python: euro)
GLYPH = [0x263A, 0x263B, 0x2665, 0x2666, 0x2663, 0x2660, 0x2022, 0x25D8, 0x25CB, 0x25D9, 0x2642, 0x2640, 0x266A,
         0x266B, 0x263C, 0x25BA, 0x25C4, 0x2195, 0x203C, 0x00B6, 0x00A7, 0x25AC, 0x21A8, 0x2191, 0x2193, 0x2192,
         0x2190, 0x221F, 0x2194, 0x25B2, 0x25BC]   # IBM PC glyphs of bytes 0x01..0x1F (MB_USEGLYPHCHARS), 0x7F: house
GLYPH_7F = 0x2302
repertoire = set(range(0x80)) | set(cp1252) | set(cp437) | set(cp10000) | set(range(0xA0, 0x100))

BMP = range(0x10000)
cat = [ud.category(chr(c)) for c in BMP]

# ---- simple case mapping (1:1 only)
upper, lower = [], []
for c in BMP:
    if 0xD800 <= c <= 0xDFFF:
        continue
    ch = chr(c)
    u, l = ch.upper(), ch.lower()
    if c == 0x130:
        l = "i"                          # Windows lower-cases dotted capital I to i
    if len(u) == 1 and ord(u) != c and ord(u) < 0x10000:
        upper.append((c, ord(u)))
    if len(l) == 1 and ord(l) != c and ord(l) < 0x10000:
        lower.append((c, ord(l)))

# ---- character types, as runs (start, value)
def ct1(c):
    k, ch = cat[c], chr(c)
    if k == "Cs" or k == "Cn":
        return 0
    v = 0x200
    if k == "Lu" or k == "Lt":
        v |= 1
    if k == "Ll" or k == "Lt":
        v |= 2
    if k == "Nd" or (k == "No" and ud.digit(ch, None) is not None):
        v |= 4
    if k in ("Zs", "Zl", "Zp") or 9 <= c <= 13 or c == 0x85:
        v |= 8
    if k[0] in "PS":
        v |= 0x10
    if k in ("Cc", "Cf"):
        v |= 0x20
    if k == "Zs" or c == 9:
        v |= 0x40
    if c < 0x80 and ch in "0123456789abcdefABCDEF" or 0xFF10 <= c <= 0xFF19 or 0xFF21 <= c <= 0xFF26 or 0xFF41 <= c <= 0xFF46:
        v |= 0x80
    if k[0] == "L":
        v |= 0x100
    return v


BIDI = {"L": 1, "R": 2, "AL": 2, "EN": 3, "ES": 4, "ET": 5, "AN": 6, "CS": 7, "B": 8, "S": 9, "WS": 10, "ON": 11}


def ct2(c):
    return BIDI.get(ud.bidirectional(chr(c)), 0)


def ct3(c):
    k, ch = cat[c], chr(c)
    nm = ud.name(ch, "")
    v = 0
    if k == "Mn":
        v |= 1
    if k == "Sk" or (k == "Lm" and 0x2B0 <= c <= 0x2FF) or (k == "Mn" and 0x300 <= c <= 0x36F):
        v |= 2
    if k[0] == "M" and "VOWEL" in nm:
        v |= 4
    if k[0] in "PS":
        v |= 8
    if "KATAKANA" in nm or 0xFF66 <= c <= 0xFF9F:
        v |= 0x10
    if "HIRAGANA" in nm:
        v |= 0x20
    if ud.east_asian_width(ch) == "H":
        v |= 0x40
    if ud.east_asian_width(ch) == "F":
        v |= 0x80
    if "IDEOGRAPH" in nm and "CJK" in nm or 0xF900 <= c <= 0xFAFF:
        v |= 0x100
    if c == 0x640:
        v |= 0x200
    if k == "Pd" or c in (0x27, 0x2019):
        v |= 0x400
    if 0xD800 <= c <= 0xDBFF:
        v |= 0x800
    if 0xDC00 <= c <= 0xDFFF:
        v |= 0x1000
    if k[0] == "L":
        v |= 0x8000
    return v


def runs(f):
    out, prev = [], None
    for c in BMP:
        v = f(c)
        if v != prev:
            out.append((c << 16) | v)
            prev = v
    return out


# ---- canonical decomposition with an ASCII base letter and 1..2 nonspacing marks (collation, MB_COMPOSITE)
decomp = []
for c in BMP:
    d = ud.normalize("NFD", chr(c))
    if len(d) in (2, 3) and d[0].isascii() and d[0].isalpha() and all(ud.category(m) == "Mn" for m in d[1:]):
        decomp.append((c,) + tuple(ord(x) for x in d) + (0,) * (3 - len(d)))

# ---- best fit: NFKD without nonspacing marks is a single, different, character the pages can represent
bestfit = []
for c in BMP:
    if cat[c] in ("Cs", "Co", "Cn") or c < 0x80:
        continue
    d = "".join(m for m in ud.normalize("NFKD", chr(c)) if ud.category(m) != "Mn")
    if len(d) == 1 and ord(d) != c and ord(d) in repertoire:
        bestfit.append((c, ord(d)))

# ---- width forms: (form, base, kind) kind 1 = <wide> (fullwidth form of base), 2 = <narrow> (halfwidth form)
width = []
for c in BMP:
    dd = ud.decomposition(chr(c)).split()
    if len(dd) == 2 and dd[0] in ("<wide>", "<narrow>"):
        width.append((c, int(dd[1], 16), 1 if dd[0] == "<wide>" else 2))

emit("/* nls_tables.inc - GENERATED by platform/win32/tools/gen_nls.py; do not edit by hand. */")
emit("/* Source: Python %s, unicodedata %s. */" % (sys.version.split()[0], ud.unidata_version))
emit("struct nls_pair { uint16_t a, b; };")
emit("struct nls_decomp { uint16_t c, d[3]; };")
emit("struct nls_width { uint16_t form, base, kind; };")
arr("uint16_t", "kcp1252", cp1252)
arr("uint16_t", "kcp437", cp437)
arr("uint16_t", "kcp10000", cp10000)
arr("uint16_t", "kglyph437", [0] + GLYPH + [GLYPH_7F])
pairs("kupper", upper)
pairs("klower", lower)
pairs("kbestfit", bestfit)
for n, f in (("kct1", ct1), ("kct2", ct2), ("kct3", ct3)):
    arr("uint32_t", n, runs(f), 8, "0x%08X")
emit("static const struct nls_decomp kdecomp[%d] = {" % len(decomp))
for i in range(0, len(decomp), 4):
    emit("    " + " ".join("{0x%04X,{0x%04X,0x%04X,0x%04X}}," % t for t in decomp[i:i + 4]))
emit("};")
emit("static const struct nls_width kwidth[%d] = {" % len(width))
for i in range(0, len(width), 4):
    emit("    " + " ".join("{0x%04X,0x%04X,%d}," % t for t in width[i:i + 4]))
emit("};")
sys.stdout.write("\n".join(OUT) + "\n")
