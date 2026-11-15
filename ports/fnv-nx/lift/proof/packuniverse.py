#!/usr/bin/env python3
"""The shape universe of a lifted pack: the encoding shapes (shape.shape_key, the census's own function) of the instructions the pack lifted.

    python3 -I packuniverse.py PACKDIR IMAGES.tsv OUT.tsv
PACKDIR: the pack (u/*.cpp).  Every lifted instruction has one label line `L_<8 hex digits>: ;` in the unit that holds its body; the set of label addresses is the set of instructions the lifter emitted
(a parent range shared by several entries is lifted once per entry: the same address under several entries is one instruction).  No form name, no census, no facts list is consulted.
IMAGES.tsv: `module<TAB>image path<TAB>load base<TAB>preferred base` (hex), one row per image of the pack; a label address a belongs to the image with load <= a < load + image size,
and the bytes of the instruction are those of the image at the preferred base (a - load + preferred; the relocation patches only fields, which the shape key masks).
A label whose whole body is `fnvl_raise(KIND, ...)` (undecodable bytes, an invalid LOCK) is not a lifted instruction: it is counted in the header (raise_only, by kind) and not in the universe.
Every other label address must belong to exactly one image and decode to one instruction (capstone, or the reserved-NOP gap fnvlift.f_sys.nop_gap, counted separately as family `nop-gap`); otherwise the run fails.
OUT.tsv: the format of census.py (`shape<TAB>family<TAB>form_key<TAB>count<TAB>first_address`, count = distinct label addresses), header with the sha256 of every input."""
import glob
import hashlib
import multiprocessing
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))
from proof import census, forms, shape  # noqa: E402
import pemod  # noqa: E402


class Loaded:
    """the image as the loader maps it at its load base in the pack (pemod.load: the dword of every HIGHLOW fixup shifted by load - preferred): the bytes the lifter decoded, relocated data in .text included"""
    def __init__(self, path, load):
        self.M = pemod.load(path, load); self.secs = [(a, min(vs, rs), 0) for _, a, vs, _, rs in self.M.secs]; self.base = self.M.base

    def bytes(self, a, n):
        x, y = self.M.off(a), self.M.off(a + n - 1)
        if x is None or y != x + n - 1:
            raise ValueError('range %08x+%d is not inside the raw data of one section' % (a, n))
        return bytes(self.M.d[x:x + n])

LABEL = re.compile(rb'^L_([0-9a-f]{8}): ;( SYNC_OUT;(?:SYNC_X_OUT;)? REGS\(\); fnvl_raise\((FNVL_\w+),)?', re.M)
IMGS = []


def labels(pack):
    """(sorted addresses of the labels with a lifted body, {address: raise kind} of the labels whose whole body is `fnvl_raise(KIND, ...)`: the lifter emitted no semantics for them, only the architectural fault)"""
    out, raised = set(), {}
    for f in sorted(glob.glob(os.path.join(pack, 'u', '*.cpp'))):
        with open(f, 'rb') as fh:
            for m in LABEL.finditer(fh.read()):
                a = int(m.group(1), 16)
                if m.group(3): raised.setdefault(a, set()).add(m.group(3).decode())
                else: out.add(a)
    both = out & set(raised)
    for a in both:
        raised[a].add('BODY')                       # an address with a raise label in one unit and a body in another: it has a body, so it is a lifted instruction
    return sorted(out), {a: sorted(k) for a, k in raised.items() if a not in out}


def image_of(a):
    for i, (mod, pe, load, pref, size) in enumerate(IMGS):
        if load <= a < load + size:
            return i
    return -1


def work(chunk):
    md = shape.decoder()
    from fnvlift import f_sys
    shapes, nop, bad, sites = {}, [], [], []
    for a in chunk:
        i = image_of(a)
        if i < 0:
            bad.append((a, 'no image')); continue
        mod, pe, load, pref, size = IMGS[i]
        va = a - load + pref
        code = None
        for n in range(15, 0, -1):
            try:
                code = pe.bytes(va, n); break
            except ValueError:
                continue
        if code is None:
            bad.append((a, 'bytes')); continue
        ins = next(iter(md.disasm(code, a, 1)), None)
        if ins is None:
            g = f_sys.nop_gap(code)
            if g is None:
                bad.append((a, 'undecodable')); continue
            nop.append((a, bytes(g.bytes).hex())); continue
        k = shape.shape_key(ins); e = shapes.get(k)
        if shape.family(ins) == 'simd': sites.append((a, k))
        if e is None:
            shapes[k] = [shape.family(ins), 1, a]
        else:
            e[1] += 1; e[2] = min(e[2], a)
    return shapes, nop, bad, sites


def main(argv):
    if len(argv) not in (3, 4):
        sys.stderr.write(__doc__); return 2
    pack, imgs, out = argv[:3]
    shas = {}
    with open(imgs) as fh:
        for ln in fh:
            if not ln.strip() or ln[0] == '#':
                continue
            mod, path, load, pref = ln.rstrip('\n').split('\t')
            load, pref = int(load, 16), int(pref, 16)
            raw = census.Pe(path)
            if raw.base != pref:
                raise ValueError('%s: preferred base %08x != image base %08x' % (mod, pref, raw.base))
            pe = Loaded(path, load)
            IMGS.append((mod, pe, load, load, max(a + s for a, s, _ in raw.secs) - raw.base)); shas[mod] = census.sha_file(path)
    spans = sorted((load, load + size, mod) for mod, pe, load, pref, size in IMGS)
    for x, y in zip(spans, spans[1:]):
        if x[1] > y[0]:
            raise ValueError('images overlap in the pack address space: %s %s' % (x[2], y[2]))
    ls, raised = labels(pack)
    n = 8 * 4
    chunks = [ls[i::n] for i in range(n)]
    with multiprocessing.Pool(4) as p:
        res = p.map(work, chunks)
    shapes, nop, bad, sites = {}, [], [], []
    for s, np_, b, st in res:
        nop += np_; bad += b; sites += st
        for k, (fam, c, a) in s.items():
            e = shapes.get(k)
            if e is None:
                shapes[k] = [fam, c, a]
            else:
                if e[0] != fam: raise ValueError('shape %s has two families' % k)
                e[1] += c; e[2] = min(e[2], a)
    rk = {}
    for a, k in raised.items():
        for x in k: rk[x] = rk.get(x, 0) + 1
    if bad:
        import collections
        by = collections.Counter((IMGS[image_of(a)][0] if image_of(a) >= 0 else '?', why) for a, why in bad)
        raise SystemExit('%d label addresses do not decode: %s; first %s' % (len(bad), sorted(by.items()), ['%08x' % a for a, _ in sorted(bad)[:8]]))
    md = shape.decoder(); fk = {}
    for k in shapes:
        r = shape.rep_bytes(k); got = list(md.disasm(r, shape.ADDR))
        assert len(got) == 1 and got[0].size == len(r) and shape.shape_key(got[0]) == k, k
        fk[k] = forms.form_key(got[0])
    fam = {}
    for v in shapes.values(): fam[v[0]] = fam.get(v[0], 0) + 1
    with open(out, 'w', newline='\n') as fh:
        fh.write('# fnvnx-packuniverse/1 labels=%d shapes=%d nop_gap=%d raise_only=%d raise_kinds=%s families=%s tool_sha256=%s images=%s\n' % (
            len(ls), len(shapes), len(nop), len(raised), ','.join('%s=%d' % x for x in sorted(rk.items())) or '-', ' '.join('%s=%d' % x for x in sorted(fam.items())), census.tool_sha(),
            ' '.join('%s:%s' % x for x in sorted(shas.items()))))
        fh.write('# shape\tfamily\tform_key\tcount\tfirst_address\n')
        for k in sorted(shapes):
            f, c, a = shapes[k]
            fh.write('%s\t%s\t%s\t%d\t%08x\n' % (k, f, fk[k], c, a))
    sys.stdout.write('labels %d raise_only %d shapes %d nop_gap %d %s\n' % (len(ls), len(raised), len(shapes), len(nop), ' '.join('%s=%d' % x for x in sorted(fam.items()))))
    if len(argv) == 4:                                        # SITES.tsv: every address of a simd-family instruction: module:address<TAB>shape key
        with open(argv[3], 'w', newline='\n') as fh:
            fh.write('# fnvnx-packsites/1 simd-family sites of the pack, module:address (pack load address)\tshape\n')
            for a, k in sorted(sites):
                fh.write('%s:%08x\t%s\n' % (IMGS[image_of(a)][0], a, k))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
