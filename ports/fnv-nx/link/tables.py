#!/usr/bin/env python3
"""link stage `tables` - vtables as data (L1 CONTRACT, M2).  No compile, no probe.

    tables.py --facts facts.json --vtid vtid.json --universe universe.tsv --out <dir>

The exe's vftables stay where they are in the data image; an object's vptr is the exe vftable address.  So the
stage emits only data:

  image_ptrs.S   one pair of words per relocated code pointer of the data image (vftable slots, callbacks,
                 __xc ...): the image address and `__fnv_<target>`.  The loader stores the native address there.
                 Invariant, not a decision: every target has a guest entry (R5) and every native body passed
                 N1, so an unresolved `__fnv_<target>` here means "N1/certificate violated: tool defect".
                 Expected count 0; the selector, not the linker, decides bodies.
  vtsites.tsv    unit, address-point symbol `_ZTV<C>.fnvnx_ap<off>` (one relocation per sub-vtable, emitted by
                 L2's compiler plugin), exe vftable address (contract/vtid.py, R6).  The link rebinds that
                 symbol of that unit to the address; no code is rewritten.
  tables.json    counts only.

image_ptrs.S and vtsites.tsv are exe-derived: container and fnv-nx-build only.
"""
import argparse, collections, json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'contract'))
import store


def load_universe(path):
    out = set()
    for ln in open(path):
        p = ln.rstrip('\n').split('\t')
        if len(p) >= 2 and p[0][:1] != '#':
            out.add(int(p[0], 16))
    return out


def image_pointers(facts, universe):
    """[(image address, target, kind)] for the relocated words that hold a CODE ENTRY: kind 'slot' (inside a
    vftable), 'xc', 'ptr'.  A relocated word whose value lies in .text but is not an entry of the universe
    points at data placed in .text; it stays an image address and is returned in the second list."""
    slot = set()
    for v, (_, _, slots, _) in facts['vft'].items():
        slot.update(range(v, v + 4 * len(slots), 4))
    xc0 = facts['meta']['xc_va']
    xcs = set(range(xc0, xc0 + 4 * len(facts['xc']), 4))
    code, data = [], []
    for a, t in facts['ptrs']:
        if t in universe:
            code.append((a, t, 'slot' if a in slot else 'xc' if a in xcs else 'ptr'))
        else:
            data.append((a, t))
    return code, data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--facts', required=True)
    ap.add_argument('--vtid', required=True)
    ap.add_argument('--universe', required=True, help="L3's universe.tsv")
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    facts = store.load(a.facts, 'facts')
    bind = store.load(a.vtid, 'vtid')['bind']
    os.makedirs(a.out, exist_ok=True)
    ptrs, data = image_pointers(facts, load_universe(a.universe))
    with open(os.path.join(a.out, 'image_ptrs.S'), 'w') as fh:
        fh.write('    .section .rodata.fnvnx_image_ptrs,"a"\n    .p2align 2\n    .globl fnvnx_image_ptrs\nfnvnx_image_ptrs:\n')
        for addr, t, kind in ptrs:
            fh.write('    .word 0x%08x, __fnv_%08x\n' % (addr, t))
        fh.write('    .word 0, 0\n')
    with open(os.path.join(a.out, 'vtsites.tsv'), 'w') as fh:
        for (unit, ztv, off), v in sorted(bind.items()):
            fh.write('%s\t%s.fnvnx_ap%d\t%08x\n' % (unit, ztv, off, v))
    c = collections.Counter()
    for addr, t, kind in ptrs:
        c['%s words rewritten to a native entry' % kind] += 1
    c['words that point at data in .text (kept as image addresses)'] = len(data)
    c['vtable address points rebound'] = len(bind)
    json.dump(dict(c), open(os.path.join(a.out, 'tables.json'), 'w'), indent=1, sort_keys=True)
    for k, v in sorted(c.items()):
        print('%7d  %s' % (v, k))


if __name__ == '__main__':
    main()
