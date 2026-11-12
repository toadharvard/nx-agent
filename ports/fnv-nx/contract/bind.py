#!/usr/bin/env python3
"""bind - the exe addresses of what L2's sections name (L1 CONTRACT; uses l2sections.py rows and the N1 certificate).

    bind.py --obj obj.json --n1 n1.json --units units.json --out bind.json      (prints counts only)

Pure functions; `unit_index` is the index of the object's unit in units.json (n1.py's unit number).
  symbol_addresses(unit_index, sympair_rows, n1) -> ({symbol: address}, [(symbol, kind, reason)])
      address = n1['sym'][(unit_index, msvc name)], as pinned (an absolute-value pin keeps its ABSOLUTE flag bit).
      kinds thunk, vtable, gcc, internal have no exe twin by definition: unbound, reason as in NO_TWIN, not an error.
      Other rows without an address: unbound with the kind's own `unnamed:*` text, 'no MSVC name', or 'not in N1'.
      One symbol with two different addresses or names is a ValueError.
  vtable_addresses(unit_index, vtpair_rows, n1) -> ({(_ZTV symbol, address point): vftable address}, [(ztv, point, reason)])
      through n1['vt'][(unit_index, ??_7 name)]; an empty or unnamed:* name, or a name N1 never pinned, is unbound.
  n3_verdicts(unit_index, n3_rows, symbol_addresses) -> {exe address: (verdict, [inlined exe addresses])}
      the shape bodysel.decide takes as `n3`.  A row whose symbol has no exe address is not an exe function: ignored.
      An ok / ok+E row with an inlined symbol that has no exe address keeps its verdict only when every n3 row of that
      symbol has kind `inline` and verdict ok (that body is part of this one: nothing to list); otherwise the verdict
      is 'inlined body without a verdict'.  Rows of one address (clones, aliases) combine: any non-ok verdict wins
      (the first in sorted order of the reasons), `ok+E` only when all rows say ok+E; inlined lists are unioned, sorted.
The CLI binds every object of obj.json that has section rows (unit = the unit whose source.replace('/', '_') + '.o', or 'src_' + that, is
the object's basename; such an object with no unit is an error; objects without rows, e.g. the link's zz_* helpers, need none) and writes store kind `bind`.
"""
import argparse, collections, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store

NO_TWIN = {'thunk': 'no exe twin', 'gcc': 'no exe twin', 'internal': 'no exe twin',
           'vtable': 'no exe twin (its address points are bound by vtpair)'}
NO_VERDICT = 'inlined body without a verdict'


def symbol_addresses(unit_index, rows, n1):
    out, names, unbound = {}, {}, []
    for sym, ms, kind, _ in rows:
        if names.setdefault(sym, (ms, kind)) != (ms, kind):
            raise ValueError('symbol %s has two rows with different names or kinds' % sym)
        if kind in NO_TWIN:
            unbound.append((sym, kind, NO_TWIN[kind]))
        elif kind.startswith('unnamed:'):
            unbound.append((sym, kind, kind))
        elif not ms:
            unbound.append((sym, kind, 'no MSVC name'))
        elif (unit_index, ms) not in n1['sym']:
            unbound.append((sym, kind, 'not in N1'))
        else:
            out[sym] = n1['sym'][(unit_index, ms)]
    return out, unbound


def vtable_addresses(unit_index, rows, n1):
    out, unbound = {}, []
    for ztv, ap, ms, _ in rows:
        if not ms or ms.startswith('unnamed:'):
            unbound.append((ztv, ap, ms or 'no MSVC name'))
        elif (unit_index, ms) not in n1['vt']:
            unbound.append((ztv, ap, 'not in N1'))
        elif out.setdefault((ztv, ap), n1['vt'][(unit_index, ms)]) != n1['vt'][(unit_index, ms)]:
            raise ValueError('address point %s+%d has two addresses' % (ztv, ap))
    return out, unbound


def n3_verdicts(unit_index, rows, addrs):
    inline = collections.defaultdict(list)
    for sym, verdict, _, kind, _ in rows:
        inline[sym].append(kind == 'inline' and verdict == 'ok')
    parts = collections.defaultdict(list)
    for sym, verdict, inl, _, _ in rows:
        if sym not in addrs:
            continue
        lst = []
        for s in inl:
            if s in addrs:
                lst.append(addrs[s])
            elif verdict in ('ok', 'ok+E') and not (s in inline and all(inline[s])):
                verdict = NO_VERDICT
        parts[addrs[sym]].append((verdict, lst))
    out = {}
    for a, ps in sorted(parts.items()):
        bad = sorted(v for v, _ in ps if v not in ('ok', 'ok+E'))
        v = bad[0] if bad else ('ok+E' if all(v == 'ok+E' for v, _ in ps) else 'ok')
        out[a] = (v, sorted({x for _, l in ps for x in l}))
    return out


def unit_indices(units, objects):
    by = {}
    for i, un in enumerate(units['units']):
        for pre in ('', 'src_'):      # the link directory names the game's objects src_<unit>.o (gate_contract.assemble)
            by.setdefault(pre + un['source'].replace('/', '_') + '.o', i)
    missing = sorted(o for o in objects if o not in by)
    if missing:
        raise ValueError('no unit in units.json for object(s): %s' % ', '.join(missing))
    return {o: by[o] for o in objects}


def bind_all(obj, n1, units):
    objects = sorted({k[0] for k in obj if k[1] in ('sympair', 'vtpair', 'n3') and obj[k]})   # an object without rows binds nothing
    ui = unit_indices(units, objects)
    res = dict(sym={}, vt={}, n3={}, unbound_sym=[], unbound_vt=[])
    for ob in objects:
        sa, us = symbol_addresses(ui[ob], obj.get((ob, 'sympair'), []), n1)
        va, uv = vtable_addresses(ui[ob], obj.get((ob, 'vtpair'), []), n1)
        res['sym'].update({(ob, s): a for s, a in sa.items()})
        res['vt'].update({(ob, z, p): a for (z, p), a in va.items()})
        res['n3'].update({(ob, a): v for a, v in n3_verdicts(ui[ob], obj.get((ob, 'n3'), []), sa).items()})
        res['unbound_sym'] += [(ob,) + u for u in us]
        res['unbound_vt'] += [(ob,) + u for u in uv]
    return res


def n3_by_address(res, n1, units):
    """{exe address: (verdict, [inlined exe addresses])}: bodysel.decide's `n3`.  One exe address can have rows in
    several objects (inline functions).  The exe's copy is the one the N1 certificate records: n1['fn'][a]['unit'].
    Only the row of that unit's object counts, so the result is a function of the address.  An address whose unit's
    object has no row has no verdict (bodysel: LIFTED)."""
    ui = unit_indices(units, sorted({ob for ob, _ in res['n3']}))
    return {a: tuple(v) for (ob, a), v in res['n3'].items() if a in n1['fn'] and n1['fn'][a]['unit'] == ui[ob]}


def write_n3(path, n3):
    with open(path, 'w', newline='\n') as fh:
        for a, (v, inl) in sorted(n3.items()):
            fh.write('\t'.join(['%08x' % a, v] + ['%08x' % x for x in inl]) + '\n')


def counts(res):
    c = collections.Counter(u[3] for u in res['unbound_sym'])
    h = collections.Counter(v for v, _ in res['n3'].values())
    return ('symbols bound %d, unbound %d %s; address points bound %d, unbound %d; n3 verdicts %d %s'
            % (len(res['sym']), len(res['unbound_sym']), json.dumps(dict(sorted(c.items()))), len(res['vt']),
               len(res['unbound_vt']), len(res['n3']), json.dumps(dict(sorted(h.items())))))


def main():
    ap = argparse.ArgumentParser()
    for n in ('obj', 'n1', 'units', 'out'):
        ap.add_argument('--' + n, required=True)
    ap.add_argument('--n3-out', help='n3.tsv for bodysel.py --n3: address, verdict, inlined addresses')
    a = ap.parse_args()
    try:
        n1, units = store.load(a.n1, 'n1'), json.load(open(a.units))
        res = bind_all(store.load(a.obj, 'obj'), n1, units)
        if a.n3_out:
            write_n3(a.n3_out, n3_by_address(res, n1, units))
    except ValueError as e:
        print('bind: ' + str(e), file=sys.stderr)
        sys.exit(1)
    store.save(a.out, 'bind', res)
    print(counts(res))


if __name__ == '__main__':
    main()
