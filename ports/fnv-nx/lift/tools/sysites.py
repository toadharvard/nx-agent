#!/usr/bin/env python3
"""sysites: the sites, over the whole reached code of each image, that need a field platform/declared/x86sys.json leaves UNDECIDED (or a vector the record does not list).
Rule (one pass over every instruction of the image's ranges.tsv, the census decoder; no list of addresses is typed here): the record says which fields are decided; a site is a row iff its instruction reads or needs
an undecided field:  int N with N not in int_ring3.vectors (class int_other; N = 0x29 and the listed vectors are decided);  sgdt / sidt / sldt (classes sgdt, sidt, sldt);  mov/pop/lds-class writes of a data
segment register (class sreg_load: the loaded value may be the undecided 0x1b; the immediate / register source is not resolved here: every such site is listed);  reads of the gs SELECTOR (class gs_read:
mov r, gs / push gs) and gs-prefixed memory operands (class gs_mem).   usage: sysites.py x86sys.json outdir  image.dll ranges.tsv [image ranges ...]   -> outdir/sysites.tsv, one `image site class text` row per site; totals on stdout."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proof import census, shape
import capstone as CS
from capstone import x86 as X

def classes(g, vecs):
    m = g.mnemonic; o = g.operands; out = []
    if m == 'int' and o[0].imm & 0xff not in vecs: out.append('int_other')
    if m in ('sgdt', 'sidt', 'sldt'): out.append(m)
    sreg = lambda op: op.type == X.X86_OP_REG and g.reg_name(op.reg) in ('es', 'ds', 'fs', 'gs', 'ss')
    if m == 'mov' and sreg(o[0]) or m == 'pop' and sreg(o[0]) or m in ('lds', 'les', 'lfs', 'lgs', 'lss'): out.append('sreg_load')
    if m == 'mov' and len(o) == 2 and sreg(o[1]) and g.reg_name(o[1].reg) == 'gs' or m == 'push' and sreg(o[0]) and g.reg_name(o[0].reg) == 'gs': out.append('gs_read')
    if any(op.type == X.X86_OP_MEM and op.mem.segment and g.reg_name(op.mem.segment) == 'gs' for op in o): out.append('gs_mem')
    return out

def main(a):
    rec = json.load(open(a[0])); vecs = set(rec['int_ring3']['vectors']); os.makedirs(a[1], exist_ok=True)
    rows = []; ninst = 0; imgs = a[2:]
    for img, rp in zip(imgs[::2], imgs[1::2]):
        pe = census.Pe(img); base, rngs = census.read_ranges(rp); md = shape.decoder(); seen = set()
        for lo, hi in rngs:
            for ins in md.disasm(pe.bytes(lo - base + pe.base, hi - lo), lo):
                if ins.address in seen: continue
                seen.add(ins.address)
                for c in classes(ins, vecs): rows.append((os.path.basename(img), ins.address, c, '%s %s' % (ins.mnemonic, ins.op_str)))
        ninst += len(seen)
    with open(os.path.join(a[1], 'sysites.tsv'), 'w') as f:
        for r in sorted(rows): f.write('%s\t%08x\t%s\t%s\n' % r)
    from collections import Counter
    print('instructions', ninst, 'sites', len(rows), dict(Counter((r[0], r[2]) for r in rows)))
if __name__ == '__main__': main(sys.argv[1:])
