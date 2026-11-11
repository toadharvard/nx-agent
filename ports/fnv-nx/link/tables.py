#!/usr/bin/env python3
"""link stage `tables` - vtables as data (L1 CONTRACT, R6 and the R3 resolution on code pointers).

    tables.py --facts facts.json --vtid vtid.json --universe universe.tsv --out <dir>

The exe's vftables stay where they are in the data image and KEEP their words: a slot holds the exe address of
its function.  No code pointer of the image is rewritten (vftable slots, EH tables, jump tables, callbacks):
every indirect call or jump goes through the dispatcher "exe address -> guest entry" (L3; in native code through
L2's compiler plugin).  An object's vptr is the exe vftable address.  So the stage emits only:

  vtsites.tsv    unit, address-point symbol `_ZTV<C>.fnvnx_ap<off>` (one relocation per sub-vtable, emitted by
                 L2's compiler plugin), exe vftable address (contract/vtid.py, R6).  The link rebinds that
                 symbol of that unit to the address; no code is rewritten.
  tables.json    counts only.

Invariant checked here: every relocated word of the image whose value lies in an executable section is either a
code entry of the universe (it then has a guest entry: the universe is BODYSEL's domain) or one of the words
L3's codemap classes as data placed in .text.  A violation is "universe/certificate violated: tool defect".

vtsites.tsv is exe-derived: container and fnv-nx-build only.
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
    with open(os.path.join(a.out, 'vtsites.tsv'), 'w') as fh:
        for (unit, ztv, off), v in sorted(bind.items()):
            fh.write('%s\t%s.fnvnx_ap%d\t%08x\n' % (unit, ztv, off, v))
    c = collections.Counter()
    for addr, t, kind in ptrs:
        c['%s words holding a code entry (kept; dispatched by address)' % kind] += 1
    c['words that point at data in .text (kept as image addresses)'] = len(data)
    c['vtable address points rebound'] = len(bind)
    json.dump(dict(c), open(os.path.join(a.out, 'tables.json'), 'w'), indent=1, sort_keys=True)
    for k, v in sorted(c.items()):
        print('%7d  %s' % (v, k))


if __name__ == '__main__':
    main()
