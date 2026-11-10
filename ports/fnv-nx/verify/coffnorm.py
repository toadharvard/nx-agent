#!/usr/bin/env python3
"""coffnorm: zero the time-dependent fields of a COFF object or COFF/ar archive. stdlib only; run as `python3 -I`.

RULE: every time-dependent field of a COFF object / archive is defined by the PE-COFF format specification; the
normalizer sets exactly those fields to zero and changes nothing else. The fields (all little-endian):
  COFF object   IMAGE_FILE_HEADER.TimeDateStamp                         offset 4, 4 bytes
  bigobj        ANON_OBJECT_HEADER_BIGOBJ.TimeDateStamp (Sig1=0, Sig2=0xFFFF, Version>=2, ClassID
                {D1BAA1C7-BAEE-4ba9-AF20-FAF66AA4DCB8})                 offset 8, 4 bytes
  import object IMPORT_OBJECT_HEADER.TimeDateStamp (Sig1=0, Sig2=0xFFFF, Version 0)   offset 8, 4 bytes
  archive       each member header (60 bytes): date (offset 16, 12 ASCII bytes) -> "0" + 11 spaces; and the
                ownership fields as GNU `ar D` writes them (checked with `ar rcD`, binutils 2.42):
                uid (offset 28, 6 bytes) "0     ", gid (offset 34, 6 bytes) "0     ", mode (offset 40, 8 bytes)
                "644     ". Linker members ("/") and the longnames member ("//") are kept; their data holds no time.
  CodeView      .debug$T records LF_TYPESERVER (0x1301), LF_TYPESERVER_ST (0x1501), LF_TYPESERVER2 (0x1515) carry a PDB
                signature/GUID: positively identified, counted, NEVER rewritten; error unless --allow-typeserver.
Nothing else in .debug$S/.debug$T is identified as time-dependent (S_OBJNAME's signature is the PCH signature, not a
time; whether a given compiler fills it with a time is not decided by the format, so it is left alone and unlisted).
PE images (MZ) and anything unrecognised: exit 1. Exit codes: 0 ok, 1 error, 2 `check` found a non-normalized field.

usage: coffnorm.py normalize IN -o OUT [--allow-typeserver] | check FILE... [--allow-typeserver] | fields FILE
"""
import os
import struct
import sys

MACHINES = {0x14c, 0x166, 0x169, 0x1a2, 0x1a3, 0x1c0, 0x1c2, 0x1c4, 0x1f0, 0x1f1, 0x200, 0x266, 0x366, 0x466, 0x8664,
            0xaa64, 0xa641, 0xa64e, 0xebc}
BIGOBJ_CLSID = bytes.fromhex("c7a1bad1eebaa94baf20faf66aa4dcb8")
TYPESERVER = {0x1301: "LF_TYPESERVER", 0x1501: "LF_TYPESERVER_ST", 0x1515: "LF_TYPESERVER2"}
AR_MAGIC = b"!<arch>\n"
AR_FIELDS = (("ar.date", 16, 12, b"0" + b" " * 11), ("ar.uid", 28, 6, b"0     "),
             ("ar.gid", 34, 6, b"0     "), ("ar.mode", 40, 8, b"644     "))


class Bad(Exception):
    pass


def u(buf, fmt, off):
    try:
        return struct.unpack_from(fmt, buf, off)
    except struct.error:
        raise Bad("truncated header at offset %d" % off)


def walk_types(d, base, found):
    """CodeView type stream: u32 signature, then records (u16 length excluding itself, u16 leaf, payload)."""
    if len(d) < 4 or u(d, "<I", 0)[0] not in (1, 2, 4):
        raise Bad(".debug$T: unknown CodeView signature")
    pos = 4
    while pos < len(d):
        rl, leaf = u(d, "<HH", pos)
        if rl < 2 or pos + 2 + rl > len(d):
            raise Bad(".debug$T: corrupt record at section offset %d" % pos)
        if leaf in TYPESERVER:
            found.append((TYPESERVER[leaf], base + pos + 4))
        pos += 2 + rl


def walk_symbols(d):
    """CodeView symbol section: signature 4 = subsections (u32 type, u32 length, data, 4-aligned); 1 = old stream."""
    if len(d) < 4:
        raise Bad(".debug$S: truncated")
    sig = u(d, "<I", 0)[0]
    if sig not in (1, 2, 4):
        raise Bad(".debug$S: unknown CodeView signature")
    pos = 4
    while sig == 4 and pos < len(d):
        _, ln = u(d, "<II", pos)
        if pos + 8 + ln > len(d):
            raise Bad(".debug$S: subsection overruns the section at section offset %d" % pos)
        pos = (pos + 8 + ln + 3) & ~3


def sections(b, base, hdr, nsec, pst, nsym, symsz, found):
    n = len(b)
    if hdr + 40 * nsec > n:
        raise Bad("section table beyond end of file")
    if nsym or pst:
        if pst < hdr + 40 * nsec or pst + symsz * nsym + 4 > n:
            raise Bad("symbol/string table beyond end of file")
    for i in range(nsec):
        name, _, _, rawsz, rawptr = u(b, "<8sIIII", hdr + 40 * i)[:5]
        if rawptr and rawptr + rawsz > n:
            raise Bad("section %d raw data beyond end of file" % i)
        if rawptr and name.rstrip(b"\0") == b".debug$T":
            walk_types(b[rawptr:rawptr + rawsz], base + rawptr, found)
        elif rawptr and name.rstrip(b"\0") == b".debug$S":
            walk_symbols(b[rawptr:rawptr + rawsz])


def coff(b, base, found):
    """One object (plain, bigobj or import) -> field list [(name, abs offset, width, target)]."""
    if len(b) < 20:
        raise Bad("truncated: shorter than a COFF header")
    if b[:2] == b"MZ":
        raise Bad("PE image (MZ): out of scope, only COFF objects and archives are handled")
    if u(b, "<HH", 0) == (0, 0xFFFF):
        ver, mach = u(b, "<HH", 4)
        if ver == 0:
            size = u(b, "<I", 12)[0]
            if mach not in MACHINES or 20 + size != len(b):
                raise Bad("import object: bad machine or size")
            return [("import.TimeDateStamp", base + 8, 4, bytes(4))]
        if ver >= 2 and b[12:28] == BIGOBJ_CLSID:
            if len(b) < 56 or mach not in MACHINES:
                raise Bad("bigobj: truncated or bad machine")
            nsec, pst, nsym = u(b, "<III", 44)
            sections(b, base, 56, nsec, pst, nsym, 20, found)
            return [("bigobj.TimeDateStamp", base + 8, 4, bytes(4))]
        raise Bad("anonymous object of unrecognised version/ClassID")
    mach, nsec, _, pst, nsym, opt, chars = u(b, "<HHIIIHH", 0)
    if mach not in MACHINES or opt != 0 or chars & 2:
        raise Bad("not a COFF object (machine 0x%x, optional header %d)" % (mach, opt))
    sections(b, base, 20, nsec, pst, nsym, 18, found)
    return [("coff.TimeDateStamp", base + 4, 4, bytes(4))]


def archive(b, found):
    fields, pos = [], 8
    while pos < len(b):
        h = b[pos:pos + 60]
        if len(h) < 60 or h[58:60] != b"`\n":
            raise Bad("archive: bad member header at offset %d" % pos)
        try:
            size = int(h[48:58])
        except ValueError:
            raise Bad("archive: bad member size at offset %d" % pos)
        start, end = pos + 60, pos + 60 + size
        if end > len(b):
            raise Bad("archive: member at offset %d overruns the file" % pos)
        fields += [(nm, pos + o, w, t) for nm, o, w, t in AR_FIELDS]
        if h[:16].rstrip() not in (b"/", b"//"):
            fields += coff(b[start:end], start, found)
        pos = end + (size & 1)
    return fields


def scan(b):
    found = []
    if b[:2] == b"MZ":
        raise Bad("PE image (MZ): out of scope, only COFF objects and archives are handled")
    if b[:8] == AR_MAGIC:
        return archive(b, found), found
    if b[:7] == b"!<thin>":
        raise Bad("thin archive: unsupported")
    return coff(b, 0, found), found


def load(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        raise Bad("%s: %s" % (path, e.strerror))


def show(b, off, w):
    v = b[off:off + w]
    return "%r" % v.decode("ascii") if w > 4 else "0x%x" % int.from_bytes(v, "little")


def typeserver_gate(path, found, allow):
    for kind in sorted({k for k, _ in found}):
        print("%s: %s x%d (PDB signature at %s, left untouched)" % (
            path, kind, sum(1 for k, _ in found if k == kind), ",".join("0x%x" % o for k, o in found if k == kind)),
            file=sys.stderr)
    if found and not allow:
        raise Bad("object references a PDB (compile with /Z7 or without debug info)")


def cmd_normalize(src, out, allow):
    if os.path.realpath(src) == os.path.realpath(out):
        raise Bad("refusing to normalize in place")
    b = load(src)
    fields, found = scan(b)
    typeserver_gate(src, found, allow)
    r = bytearray(b)
    for _, off, w, t in fields:
        r[off:off + w] = t
    with open(out, "wb") as f:
        f.write(bytes(r))
    return 0


def cmd_check(paths, allow):
    rc = 0
    for p in paths:
        try:
            b = load(p)
            fields, found = scan(b)
            typeserver_gate(p, found, allow)
        except Bad as e:
            print("error: %s: %s" % (p, e), file=sys.stderr)
            rc = 1
            continue
        for nm, off, w, t in fields:
            if b[off:off + w] != t and rc != 1:
                rc = 2
            if b[off:off + w] != t:
                print("%s: %s at 0x%x = %s (normalized: %s)" % (p, nm, off, show(b, off, w), show(t, 0, w)))
    return rc


def cmd_fields(path):
    b = load(path)
    fields, found = scan(b)
    for nm, off, w, _ in fields:
        print("0x%08x %d %s %s" % (off, w, nm, show(b, off, w).replace(" ", "_")))
    for kind, off in found:
        print("0x%08x %d %s %s" % (off, 4, kind + ".signature", show(b, off, 4)))
    return 0


def main(argv):
    allow = "--allow-typeserver" in argv
    args = [a for a in argv if a != "--allow-typeserver"]
    try:
        if len(args) == 4 and args[0] == "normalize" and args[2] == "-o":
            return cmd_normalize(args[1], args[3], allow)
        if len(args) >= 2 and args[0] == "check":
            return cmd_check(args[1:], allow)
        if len(args) == 2 and args[0] == "fields":
            return cmd_fields(args[1])
        print(__doc__.splitlines()[-2], file=sys.stderr)
        return 1
    except Bad as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    except OSError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
