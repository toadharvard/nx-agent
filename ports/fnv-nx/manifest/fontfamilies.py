"""Family names of font files, read from the files' own bytes (pure Python: no host tool, no clock; a function of the file contents).

  families(data) -> sorted list of family names, or [] when the file is no font this reader knows
Formats (the ones FreeType's sfnt and winfonts drivers read, which is what fc-scan and gdifont.cpp use):
  sfnt (TrueType/OpenType): `name` table, nameID 1 (family) and 16 (typographic family), platform 3 (UTF-16BE) and platform 1 (MacRoman = ASCII here)
  Windows font resource (.fon, NE or PE): every RT_FONT (type 8) resource is an FNT whose dfFace (u32 at +105) points at the NUL-terminated face name
Assumption: Windows font formats as documented (ASM-003).
"""
import struct


def _name_table(d):
    n = struct.unpack(">H", d[4:6])[0]
    out = set()
    for i in range(n):
        tag, _, off, ln = struct.unpack(">4sIII", d[12 + 16 * i:28 + 16 * i])
        if tag != b"name":
            continue
        cnt, so = struct.unpack(">HH", d[off + 2:off + 6])
        for k in range(cnt):
            pl, enc, lang, nid, l2, o2 = struct.unpack(">6H", d[off + 6 + 12 * k:off + 18 + 12 * k])
            if nid in (1, 16):
                raw = d[off + so + o2:off + so + o2 + l2]
                if pl == 3:
                    out.add(raw.decode("utf-16-be", "replace"))
                elif pl == 1:
                    out.add(raw.decode("latin-1"))
    return out


def _fnt_face(f):
    if len(f) < 109:
        return None
    o = struct.unpack("<I", f[105:109])[0]
    e = f.find(b"\0", o)
    return f[o:e].decode("latin-1") if 0 < o < len(f) and e > o else None


def _ne_fonts(d, ne):
    rt = ne + struct.unpack("<H", d[ne + 0x24:ne + 0x26])[0]
    shift = struct.unpack("<H", d[rt:rt + 2])[0]
    p, out = rt + 2, []
    while True:
        t = struct.unpack("<H", d[p:p + 2])[0]
        if t == 0:
            return out
        cnt = struct.unpack("<H", d[p + 2:p + 4])[0]
        for i in range(cnt):
            off, ln = struct.unpack("<HH", d[p + 8 + 12 * i:p + 12 + 12 * i])
            if t == 0x8008:
                out.append(d[off << shift:(off << shift) + (ln << shift)])
        p += 8 + 12 * cnt


def _pe_fonts(d, pe):
    nsec = struct.unpack("<H", d[pe + 6:pe + 8])[0]
    opt = pe + 24
    magic = struct.unpack("<H", d[opt:opt + 2])[0]
    ddir = opt + (96 if magic == 0x10b else 112)
    rva, _ = struct.unpack("<II", d[ddir + 16:ddir + 24])
    secs = [struct.unpack("<IIII", d[opt + struct.unpack("<H", d[pe + 20:pe + 22])[0] + 40 * i + 8:opt + struct.unpack("<H", d[pe + 20:pe + 22])[0] + 40 * i + 24]) for i in range(nsec)]

    def off(r):
        for vs, va, rs, ro in secs:
            if va <= r < va + max(vs, rs):
                return r - va + ro
        return None
    base = off(rva)
    if base is None:
        return []

    def entries(o):
        n = struct.unpack("<HH", d[o + 12:o + 16])
        return [struct.unpack("<II", d[o + 16 + 8 * i:o + 24 + 8 * i]) for i in range(n[0] + n[1])]
    out = []
    for nid, sub in entries(base):
        if nid != 8:
            continue
        for _, s2 in entries(base + (sub & 0x7fffffff)):
            for _, s3 in entries(base + (s2 & 0x7fffffff)):
                dr, sz, _, _ = struct.unpack("<IIII", d[base + s3:base + s3 + 16])
                o = off(dr)
                if o is not None:
                    out.append(d[o:o + sz])
    return out


def families(d):
    if d[:4] in (b"\0\1\0\0", b"true", b"OTTO", b"ttcf"):
        return sorted(_name_table(d))
    if d[:2] == b"MZ":
        e = struct.unpack("<I", d[0x3c:0x40])[0]
        fs = _ne_fonts(d, e) if d[e:e + 2] == b"NE" else _pe_fonts(d, e) if d[e:e + 4] == b"PE\0\0" else []
        return sorted({x for x in (_fnt_face(f) for f in fs) if x})
    return []
