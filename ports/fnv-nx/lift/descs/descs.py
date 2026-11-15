#!/usr/bin/env python3
"""descs.py - the `descs` fact of the DISPATCH1 bridges: the set of call-shape descriptors of every indirect call of native code.

    descs.py [OBJS_DIR [OUT]]        defaults /objs and /out/descs.tsv (the generate stage's mounts, recipe `objects_of`)

Input: every ELF object under OBJS_DIR (`<root with / as __>/<unit>.o`, the objects of every admitted product root but the ones the recipe excepts).
The section `.fnvnx.disp` of an object is the fact toolchain/dispatch/fnvnx_dispatch.cc writes: ASCII, LF-terminated, sorted, unique, one descriptor
per line, `conv:ret:args`:
    conv  this | fast | stack
    ret   v | i | q | f | d | a<N><X><Y>   X = R | H (the x86 side: registers / hidden pointer), Y = g | m | h<elt> (the native side: general registers /
          memory through x8 / a homogeneous aggregate); BNF in toolchain/dispatch/fnvnx_dispatch.cc
    args  a comma list, possibly empty, of  i | q | f | d | s<N>g | s<N>h<elt> | s<N>r
    elt   f d q h v8 v16
An object without the section has no indirect calls.
Output: descs.tsv, the sorted union, one descriptor per line, LF line ends, no duplicates; the empty set is the empty file.
Red (exit 1, every problem listed): a file that is no ELF32/ELF64 little-endian object, a section without the final LF, a line outside the grammar,
a section that is not sorted and unique, a non-ASCII byte. The grammar is the syntax only; a descriptor no bridge can be generated for is bridgegen's
error (the descriptor is listed, nothing is generated).
Pure: stdlib only, reads only OBJS_DIR, writes only OUT; the output is a function of the objects' bytes.
"""
import os
import re
import struct
import sys

SECTION = ".fnvnx.disp"
N = r"[0-9]+"
ELT = r"(?:f|d|q|h|v8|v16)"
RET = r"(?:v|i|q|f|d|a%s[RH](?:g|m|h%s))" % (N, ELT)
ARG = r"(?:i|q|f|d|s%sg|s%sh%s|s%sr)" % (N, N, ELT, N)
LINE = re.compile(r"(?:this|fast|stack):%s:(?:%s(?:,%s)*)?" % (RET, ARG, ARG))


def section(path, name):
    """The bytes of section NAME of ELF32/ELF64 little-endian object PATH, or None. ValueError for a file that is no such object."""
    d = open(path, "rb").read()
    if d[:4] != b"\x7fELF" or d[4] not in (1, 2) or d[5] != 1:
        raise ValueError("not an ELF32/ELF64 little-endian object")
    w64 = d[4] == 2
    try:
        shoff, = struct.unpack_from("<Q" if w64 else "<I", d, 0x28 if w64 else 0x20)
        esz, num, stri = struct.unpack_from("<HHH", d, 0x3a if w64 else 0x2e)
        secs = [struct.unpack_from("<IIQQQQIIQQ" if w64 else "<IIIIIIIIII", d, shoff + i * esz) for i in range(num)]
        st = secs[stri]
        strtab = d[st[4]:st[4] + st[5]]
        found = [s for s in secs if strtab[s[0]:strtab.index(b"\0", s[0])] == name.encode()]
    except (struct.error, IndexError, ValueError):
        raise ValueError("a damaged section header table")
    if len(found) > 1:
        raise ValueError("more than one section %s" % name)
    if not found:
        return None
    s = found[0]
    if s[4] + s[5] > len(d):
        raise ValueError("section %s lies outside the file" % name)
    return d[s[4]:s[4] + s[5]]


def read_section(data):
    """[descriptor] of one section's bytes; ValueError naming the first problem."""
    if not data:
        return []
    if data[-1:] != b"\n":
        raise ValueError("%s: no final LF" % SECTION)
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        raise ValueError("%s: not ASCII" % SECTION)
    lines = text[:-1].split("\n")
    for k, ln in enumerate(lines, 1):
        if not LINE.fullmatch(ln):
            raise ValueError("%s: line %d %r is outside the grammar conv:ret:args" % (SECTION, k, ln[:60]))
    if lines != sorted(set(lines)):
        raise ValueError("%s: the lines are not sorted and unique" % SECTION)
    return lines


def collect(objs):
    """(sorted union [descriptor], [problem]) of the objects under directory OBJS."""
    descs, err, files = set(), [], []
    for base, dirs, names in os.walk(objs):
        dirs.sort()
        files += [os.path.join(base, n) for n in sorted(names)]
    for p in files:
        rel = os.path.relpath(p, objs)
        try:
            data = section(p, SECTION)
            descs.update(read_section(data or b""))
        except ValueError as e:
            err.append("%s: %s" % (rel, e))
    return sorted(descs), err


def main(argv):
    objs = argv[0] if argv else "/objs"
    out = argv[1] if len(argv) > 1 else "/out/descs.tsv"
    descs, err = collect(objs)
    if err:
        sys.stderr.write("descs: %d problem(s)\n%s\n" % (len(err), "\n".join(err)))
        return 1
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", newline="") as f:
        f.write("".join(d + "\n" for d in descs))
    print("descs: %d descriptors" % len(descs))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
