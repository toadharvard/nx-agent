#!/usr/bin/env python3
"""link stage `tables` - vtables as data (L1 CONTRACT, M2).  No compile, no probe.

    tables.py --facts facts.pkl --vtid vtid.pkl --addrmap out/addrmap.tsv --out <dir>

The exe's vftables stay where they are in the data image; an object's vptr is the exe vftable address.  So the
stage emits only data:

  image_ptrs.S   one pair of words per relocated code pointer of the data image (vftable slots, callbacks,
                 __xc ...): the image address and `__fnv_<target>`.  The loader stores the native address there.
                 A target with no body is an undefined symbol: the link fails until BODYSEL gives it a body.
  vtid.ld        `__fnvt_<vftable>_<addend> = <vftable> - <addend>;` one absolute symbol per bound
                 (vftable, addend): a `_ZTV` GOT reference rebound to it makes the code's `+ addend` land on the
                 exe vftable.
  vtsites.tsv    object, `_ZTV` symbol, addend, vftable, how: 'symbol' (rebind the reference), 'imm' (the symbol
                 carries several sub-vtables whose exe distances differ from the Itanium ones: rebind to the
                 lowest addend's base and rewrite the other `add` immediates), 'lift' (not encodable).
  tables.json    counts only.

image_ptrs.S, vtid.ld and vtsites.tsv are exe-derived: container and fnv-nx-build only.
"""
import argparse, collections, json, os, pickle


def load_bodies(addrmap):
    have = set()
    for ln in open(addrmap):
        p = ln.rstrip('\n').split('\t')
        if p[0][:1] != '#' and len(p) > 3 and p[3]:
            have.add(int(p[0], 16))
    return have


def image_pointers(facts, have):
    """[(image address, target, has body, kind)] kind: 'slot' (inside an RTTI vftable), 'xc', 'ptr'."""
    slot = set()
    for v, (_, _, slots, _) in facts['vft'].items():
        slot.update(range(v, v + 4 * len(slots), 4))
    xc0 = facts['meta']['xc_va']
    xcs = set(range(xc0, xc0 + 4 * len(facts['xc']), 4))
    return [(a, t, t in have, 'slot' if a in slot else 'xc' if a in xcs else 'ptr') for a, t in facts['ptrs']]


def site_plan(bind):
    """{(object, symbol, addend): (vftable, how, base, immediate)}"""
    g = collections.defaultdict(dict)
    for (ob, sym, add), v in bind.items():
        g[(ob, sym)][add] = v
    out = {}
    for (ob, sym), d in g.items():
        known = [x for x in d if x is not None]
        a0 = min(known) if known else 0
        base = d[a0] - a0 if known else 0
        for add, v in d.items():
            if add is None:                      # the value is not formed by a plain add: no site to rewrite
                out[(ob, sym, -1)] = (v, 'lift', 0, 0)
                continue
            imm = v - base
            how = 'symbol' if imm == add else 'imm' if -4096 < imm < 4096 else 'lift'
            out[(ob, sym, add)] = (v, how, base, imm)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--facts', required=True)
    ap.add_argument('--vtid', required=True)
    ap.add_argument('--addrmap', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    facts = pickle.load(open(a.facts, 'rb'))
    bind = pickle.load(open(a.vtid, 'rb'))['bind']
    have = load_bodies(a.addrmap)
    os.makedirs(a.out, exist_ok=True)
    ptrs = image_pointers(facts, have)
    with open(os.path.join(a.out, 'image_ptrs.S'), 'w') as fh:
        fh.write('    .section .rodata.fnvnx_image_ptrs,"a"\n    .p2align 2\n    .globl fnvnx_image_ptrs\nfnvnx_image_ptrs:\n')
        for addr, t, ok, kind in ptrs:
            fh.write('    .word 0x%08x, __fnv_%08x\n' % (addr, t))
        fh.write('    .word 0, 0\n')
    plan = site_plan(bind)
    with open(os.path.join(a.out, 'vtid.ld'), 'w') as fh:
        for base, add in sorted({(p[2], k[2]) if p[1] == 'symbol' else (p[2], 0) for k, p in plan.items()}):
            fh.write('__fnvt_%08x_%d = 0x%08x;\n' % ((base + add) & 0xffffffff, add, base))
    with open(os.path.join(a.out, 'vtsites.tsv'), 'w') as fh:
        for (ob, sym, add), (v, how, base, imm) in sorted(plan.items()):
            fh.write('%s\t%s\t%d\t%08x\t%s\t%d\n' % (ob, sym, add, v, how, imm))
    c = collections.Counter()
    for addr, t, ok, kind in ptrs:
        c['%s words: %s' % (kind, 'body' if ok else 'NO BODY')] += 1
    c['distinct targets without a body'] = len({t for _, t, ok, _ in ptrs if not ok})
    for p in plan.values():
        c['vtable sites: ' + p[1]] += 1
    json.dump(dict(c), open(os.path.join(a.out, 'tables.json'), 'w'), indent=1, sort_keys=True)
    for k, v in sorted(c.items()):
        print('%7d  %s' % (v, k))


if __name__ == '__main__':
    main()
