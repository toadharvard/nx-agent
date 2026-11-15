#!/usr/bin/env python3
"""abi_native - the join of the native vtable facts with the exe's vftables, shared by the gates thisadj, vslots and vtables (L2 SOURCE, ABI1).

Inputs (all L1 fact bases, read through contract/store.py; nothing is parsed a second time here):
  obj    store kind `obj`  (contract/objfacts.py): per object `ztv` {`_ZTV` symbol: [(address point, d, [slot symbol | None])]}
         (d = minus the Itanium offset-to-top), `sympair` and `vtpair` rows (contract/l2sections.py);
  bind   store kind `bind` (contract/bind.py): `sym` {(object, symbol): exe address}, `vt` {(object, _ZTV, address point): vftable address};
  exe    the PE image (contract/exefacts.Image) -> exevft.scan (RTTI vftables) and exevft.chain (adjustor thunks).

  groups(obj, bind)               -> [Point] one per address point of every `_ZTV` of every object, sorted; `claim` is
                                     'named' (the plugin's vtpair row names a `??_7`), 'unnamed' (the row says `unnamed:<reason>`: the
                                     compiler states there is no MSVC twin) or 'missing' (no vtpair row for the address point)
  role(symbol)                    -> ('fn',) | ('thunk', adj, target symbol) | ('unsupported', why): the Itanium special-name grammar
                                     `_ZT h <nv-offset> _ <encoding>` (call-offset `h`, offset `n<digits>` = negative); the thunk's callee
                                     is `_Z<encoding>` by definition of the production.  `adj` = minus the nv-offset: this_callee = this - adj.
                                     `_ZTv` (virtual-base thunk: the adjustment is read from the vtable at run time) and `_ZTc` (covariant)
                                     are 'unsupported'.
  exe_index(obj, point)           -> ([exe slot index k(j)], [kind per slot]) under the minus-one rule, or ValueError(reason)
  kind_of(obj, object, symbol)    -> the sympair kind of the symbol's row in that object, '' when it has none
RULE for the slot correspondence (the minus-one rule, ARCHITECTURE D22/D26, made a function of compiler facts): MSVC has one deleting
destructor slot where the Itanium vtable has D1 then D0.  A slot whose symbol (the thunk's callee for a thunk) has sympair kind `dtor.d0` shares
the exe slot of the slot before it, which must have kind `dtor.d1`; so k(j) = j - #(d0 slots at or before j).  The kinds are the
plugin's own statements about the symbols, not names.  Open (REMOVE, owner L2): the plugin's kinds are right; removed by gate names against cl objects and gate vslots against the exe's vftables.
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'contract'))
import gatefile
import store

Point = collections.namedtuple('Point', 'object ztv ap d slots claim msvc vfptr vft')


def point_id(p):
    return '%s:%s+%d' % (p.object, p.ztv, p.ap)


def groups(obj, bind):
    out = []
    for ob in sorted({k[0] for k in obj if k[1] == 'ztv'}):
        rows = {(z, ap): (ms, off) for z, ap, ms, off in obj.get((ob, 'vtpair'), [])}
        for z, pts in sorted(obj[(ob, 'ztv')].items()):
            for ap, d, slots in sorted(pts):
                ms, off = rows.get((z, ap), (None, None))
                claim = 'missing' if ms is None else 'unnamed' if (not ms or ms.startswith('unnamed:')) else 'named'
                out.append(Point(ob, z, ap, d, tuple(slots), claim, ms or '', off, bind['vt'].get((ob, z, ap))))
    return out


def _num(s, i):
    j = i
    while j < len(s) and s[j].isdigit():
        j += 1
    return (int(s[i:j]), j) if j > i else (None, i)


def role(sym):
    if sym is None:
        return ('unsupported', 'no relocation in the slot')
    if sym.startswith('_ZTh'):
        i = 4
        neg = sym[i:i + 1] == 'n'
        n, j = _num(sym, i + neg)
        if n is None or sym[j:j + 1] != '_' or len(sym) <= j + 1:
            return ('unsupported', 'malformed non-virtual thunk name')
        return ('thunk', n if neg else -n, '_Z' + sym[j + 1:])
    if sym.startswith(('_ZTv', '_ZTc')):
        return ('unsupported', 'virtual-base or covariant thunk: the adjustment is not a constant')
    return ('fn',)


def kind_of(obj, ob, sym):
    ks = {r[2] for r in obj.get((ob, 'sympair'), []) if r[0] == sym}
    return sorted(ks)[0] if len(ks) == 1 else ''


def callee(obj, ob, sym):
    """The symbol whose body the slot reaches: the thunk's callee, else the symbol itself (None for an unsupported slot)."""
    r = role(sym)
    return sym if r[0] == 'fn' else r[2] if r[0] == 'thunk' else None


def exe_index(obj, p):
    ks = []
    for s in p.slots:
        c = callee(obj, p.object, s)
        ks.append(kind_of(obj, p.object, c) if c else '')
    out, n0 = [], 0
    for j, k in enumerate(ks):
        if k == 'dtor.d0':
            if j == 0 or ks[j - 1] != 'dtor.d1':
                raise ValueError('slot %d is a deleting destructor without the complete destructor before it' % j)
            n0 += 1
        elif k == 'dtor.d1' and not (j + 1 < len(ks) and ks[j + 1] == 'dtor.d0'):
            raise ValueError('slot %d is a complete destructor without the deleting destructor after it' % j)
        out.append(j - n0)
    return out, ks


def load_store(path, kind):
    """store.load with every structural defect of the file (not an object, missing keys) reported as ValueError."""
    try:
        return store.load(path, kind)
    except (AttributeError, KeyError, TypeError, IndexError) as e:
        raise ValueError('%s: malformed %s file (%s)' % (path, kind, e))


def load(obj_path, bind_path):
    return load_store(obj_path, 'obj'), load_store(bind_path, 'bind')


def common_args(ap):
    for n in ('exe', 'obj', 'bind'):
        ap.add_argument('--' + n, required=True)
    return ap


def finish(name, universe, red, detail):
    """Write the gate file (universe = list of item ids, red = {id: reason}); exit status 0 iff nothing is red."""
    gatefile.write(name, len(universe), len(universe) - len(red), ['%s [%s]' % (i, r) for i, r in sorted(red.items())], detail)
    print('%s: %d items, %d uncertified. %s' % (name, len(universe), len(red), detail))
    return 1 if red else 0


SlotFact = collections.namedtuple('SlotFact', 'id point j k symbol role callee addr entry adj target kind direct err')


def slot_facts(obj, bind, vfts, img):
    """One SlotFact per slot of every `named` address point (sorted).  `err` is the structural reason the slot cannot be related to an exe slot
    (unbound point, not an exe vftable, offsets disagree, broken destructor pairs, index past the exe vftable); '' when it can.
    `direct` = exevft.entry_offsets of the exe: {function address: COL offsets of the vftables holding it as a slot word}."""
    import exevft
    direct = exevft.entry_offsets(vfts)
    out = []
    for p in groups(obj, bind):
        if p.claim != 'named':
            continue
        e = vfts.get(p.vft) if p.vft is not None else None
        err, ks, kinds = '', None, [''] * len(p.slots)
        if p.vft is None:
            err = 'address point unbound'
        elif e is None:
            err = '%08x is not an RTTI vftable of the exe' % p.vft
        else:
            try:
                ks, kinds = exe_index(obj, p)
            except ValueError as ex:
                err = str(ex)
        for j, s in enumerate(p.slots):
            r = role(s)
            c = callee(obj, p.object, s)
            f = dict(id='%s#%d' % (point_id(p), j), point=p, j=j, k=None, symbol=s, role=r, callee=c,
                     addr=bind['sym'].get((p.object, c)) if c else None, entry=None, adj=None, target=None, kind=kinds[j], direct=direct, err=err)
            if not err:
                if ks[j] >= len(e.slots):
                    f['err'] = 'native slot %d is exe slot %d, the exe vftable has %d' % (j, ks[j], len(e.slots))
                else:
                    f['k'] = ks[j]
                    f['entry'] = e.slots[ks[j]]
                    f['adj'], f['target'], _ = exevft.chain(img, f['entry'])
            out.append(SlotFact(**f))
    return out
