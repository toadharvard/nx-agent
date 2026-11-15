#!/usr/bin/env python3
"""Encoding census of one module: every instruction of the recursive ranges, grouped by encoding shape (proof/shape.py).

    python3 -I census.py IMAGE FACTS_DIR OUT.tsv        FACTS_DIR/ranges.tsv names the byte ranges (absolute VAs at the preferred base), FACTS_DIR/codemap.tsv the byte accounting of the same facts commit

OUT.tsv (stage output; sorted, no clock):
    # fnvnx-census/1 image_sha256=... ranges_sha256=... tool_sha256=... forms_sha256=... instructions=N shapes=S form_keys=K
    # shape<TAB>family<TAB>form_key<TAB>count<TAB>first_address
    rows sorted by shape key; count = distinct instruction addresses with that shape; first_address = the lowest, 8 hex digits.
tool_sha256 = sha256(shape.py + census.py); forms_sha256 = sha256(forms.py) (form_key lives there).  (image, ranges, tool, forms) is the R12 stage key.

The ranges are authoritative: exactly the listed byte ranges are decoded (each distinct range once, from its start); a range that does
not decode to whole instructions is an ERROR: it is listed in the header (`# undecodable lo-hi stopped_at=`), the instructions decoded before the
failure are kept, and the exit status is 1.  A range outside the file's section data, or an instruction the shape rule cannot mask, aborts.
The family is computed for EVERY decoded instruction (not for the first of its shape); a key that gets two families makes the census fail (ValueError listing the key, the families and the lowest address of each), so
the family is a function of the key over the whole census by checking the finite set, not by an assumption; the representative encoding is decoded again and must give the same shape and family.  The form key is that of the REPRESENTATIVE (capstone names the predicate of cmpps/cmppd/cmpss/cmpsd by the
value of the imm8, so the mnemonic of an occurrence is not a function of the shape; the representative has imm8 = 0)."""
import hashlib
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from proof import forms, shape  # noqa: E402

BASE_HDR = '# base='


def sha_file(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def tool_sha():
    h = hashlib.sha256()
    for n in ('shape.py', 'census.py'):
        with open(os.path.join(HERE, n), 'rb') as fh:
            h.update(fh.read())
    return h.hexdigest()


class Pe:
    """The section data of a PE32 file: bytes(va, n) of the image at its preferred base, from the raw data of the sections."""

    def __init__(self, path):
        with open(path, 'rb') as fh:
            d = self.d = fh.read()
        pe = struct.unpack_from('<I', d, 0x3c)[0]
        if d[pe:pe + 4] != b'PE\0\0' or struct.unpack_from('<H', d, pe + 24)[0] != 0x10b:
            raise ValueError('%s: not a PE32 image' % path)
        nsec, soh = struct.unpack_from('<H', d, pe + 6)[0], struct.unpack_from('<H', d, pe + 20)[0]
        self.base = struct.unpack_from('<I', d, pe + 52)[0]
        self.secs = []
        o = pe + 24 + soh
        for _ in range(nsec):
            vs, va, rs, ro = struct.unpack_from('<IIII', d, o + 8)
            self.secs.append((self.base + va, min(vs, rs) if vs else rs, ro))
            o += 40
        self.secs.sort()

    def bytes(self, va, n):
        for lo, size, ro in self.secs:
            if lo <= va and va + n <= lo + size:
                return self.d[ro + va - lo:ro + va - lo + n]
        raise ValueError('range %08x+%d is not inside the raw data of one section' % (va, n))


def read_ranges(path):
    """(base, sorted distinct [(lo, hi)]) from ranges.tsv; the header line `# base=0x...` states the base of the addresses."""
    base, out = None, set()
    with open(path) as fh:
        for ln in fh:
            ln = ln.rstrip('\n')
            if ln.startswith(BASE_HDR):
                base = int(ln[len(BASE_HDR):].split(':')[0], 16)
            if not ln or ln[0] == '#':
                continue
            f = ln.split('\t')
            if len(f) != 2:
                raise ValueError('ranges.tsv: bad line %r' % ln)
            for r in f[1].split(',') if f[1] else ():          # an entry with an empty range list contributes no bytes
                lo, hi = r.split('-')
                lo, hi = int(lo, 16), int(hi, 16)
                if hi <= lo:
                    raise ValueError('ranges.tsv: empty range %r' % r)
                out.add((lo, hi))
    if base is None:
        raise ValueError('ranges.tsv: no "# base=" header')
    return base, sorted(out)


DATA_CLASSES = ('data-reloc', 'data-unreached', 'data-pe-header', 'hole')       # codemap classes (L3a tools/codemap.py CLS, tools/modlift.py CN) that are bytes, not code


def read_codemap(path):
    """sorted [(start, end, class)] from codemap.tsv (`start<TAB>length<TAB>class<TAB>detail`, hex start, decimal length): the byte-accounting of the facts commit"""
    out = []
    with open(path) as fh:
        for ln in fh:
            f = ln.rstrip('\n').split('\t')
            if len(f) < 3 or not f[0] or f[0][0] == '#':
                continue
            out.append((int(f[0], 16), int(f[0], 16) + int(f[1]), f[2]))
    out.sort()
    return out


def codemap_class(cm, va):
    """the class of byte va ('' when the codemap does not cover it)"""
    import bisect
    i = bisect.bisect_right(cm, (va, 1 << 40, '')) - 1
    return cm[i][2] if i >= 0 and cm[i][0] <= va < cm[i][1] else ''


def census(image, ranges_path, codemap_path=None):
    """({shape: [family, form_key, count, first_address]}, instructions, [(lo, hi, stopped_at)] ranges that do not decode to whole instructions)"""
    pe = Pe(image)
    base, rngs = read_ranges(ranges_path)
    if base != pe.base:
        raise ValueError('ranges base %08x != image base %08x' % (base, pe.base))
    md = shape.decoder()
    shapes, seen, bad, split, skipped = {}, set(), [], {}, []
    cm = read_codemap(codemap_path) if codemap_path else None
    for lo, hi in rngs:
        if cm is not None and codemap_class(cm, lo) in DATA_CLASSES:          # the code rule: a range whose first byte the codemap accounts as data is not code (it has no lifted body)
            skipped.append((lo, hi, codemap_class(cm, lo)))
            continue
        code = pe.bytes(lo, hi - lo)
        pos = lo
        for ins in md.disasm(code, lo):
            if ins.address != pos:
                raise ValueError('decoder desynchronised at %08x' % pos)
            pos += ins.size
            if ins.address in seen:
                continue
            seen.add(ins.address)
            k = shape.shape_key(ins)
            e = shapes.get(k)
            fam = shape.family(ins)
            if e is None:
                shapes[k] = [fam, None, 1, ins.address]
            else:
                if fam != e[0]:                   # family is computed for EVERY instruction, not for the first one: a key with two families is a census error
                    split.setdefault(k, {e[0]: e[3]})[fam] = min(ins.address, split.get(k, {}).get(fam, ins.address))
                e[2] += 1
                if ins.address < e[3]:
                    e[3] = ins.address
        if pos != hi:
            bad.append((lo, hi, pos))
    if split:
        raise ValueError('shape keys with two families (the key is too coarse): ' + '; '.join('%s: %s' % (k, ', '.join('%s@%08x' % (f, a) for f, a in sorted(v.items()))) for k, v in sorted(split.items())))
    for k in sorted(shapes):                      # the representative encoding decodes to the same shape and family; its form key is the shape's
        r = shape.rep_bytes(k)
        got = list(md.disasm(r, shape.ADDR))
        if len(got) != 1 or got[0].size != len(r) or shape.shape_key(got[0]) != k or shape.family(got[0]) != shapes[k][0]:      # (the family of every instruction was already checked above)
            raise ValueError('shape %s: the representative encoding does not reproduce the shape' % k)
        shapes[k][1] = forms.form_key(got[0])
    return shapes, len(seen), bad, skipped


def write(path, image, ranges_path, shapes, n, bad, codemap_path=None, skipped=()):
    keys = sorted({v[1] for v in shapes.values()})
    with open(forms.__file__, 'rb') as fh:
        fsha = hashlib.sha256(fh.read()).hexdigest()
    with open(path, 'w', newline='\n') as fh:
        fh.write('# fnvnx-census/1 image_sha256=%s ranges_sha256=%s tool_sha256=%s forms_sha256=%s codemap_sha256=%s instructions=%d shapes=%d form_keys=%d undecodable_ranges=%d data_ranges_skipped=%d\n'
                 % (sha_file(image), sha_file(ranges_path), tool_sha(), fsha, sha_file(codemap_path) if codemap_path else '-', n, len(shapes), len(keys), len(bad), len(skipped)))
        for lo, hi, at in bad:
            fh.write('# undecodable %08x-%08x stopped_at=%08x\n' % (lo, hi, at))
        fh.write('# shape\tfamily\tform_key\tcount\tfirst_address\n')
        for k in sorted(shapes):
            f, fk, c, a = shapes[k]
            fh.write('%s\t%s\t%s\t%d\t%08x\n' % (k, f, fk, c, a))


def main(argv):
    if len(argv) != 3:
        sys.stderr.write(__doc__)
        return 2
    image, facts, out = argv
    rp = os.path.join(facts, 'ranges.tsv')
    cp = os.path.join(facts, 'codemap.tsv')
    shapes, n, bad, skipped = census(image, rp, cp)          # the codemap of the same facts commit and image: one source for code (the lifter's)
    write(out, image, rp, shapes, n, bad, cp, skipped)
    fam = {}
    for v in shapes.values():
        fam[v[0]] = fam.get(v[0], 0) + 1
    sys.stdout.write('instructions %d shapes %d form_keys %d %s\n' % (n, len(shapes), len({v[1] for v in shapes.values()}),
                                                                      ' '.join('%s=%d' % (k, fam[k]) for k in sorted(fam))))
    if bad:
        sys.stderr.write('ERROR: %d range(s) do not decode to whole instructions (listed in the output header)\n' % len(bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
