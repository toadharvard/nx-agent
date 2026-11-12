#!/usr/bin/env python3
"""globext_rows - the certified objects of one build, as the rows globext.py writes (L2).

Pure functions; the inputs are the outputs of the gates, passed in, never read from the exe here.
  parse_objects(data, where) -> [(asm, size, align, elem, itanium, flags)]     the `.fnvnx.objects` section (plugin fnvnx_layout)
  certify(obj, objects, sympair, bind_sym, name_ok, record_ok, scalar_ok, unit) -> (rows, rejected)
      obj        object basename (the key of bind_sym and name_ok)
      objects    parse_objects rows of the object;  sympair  l2sections.parse_sympair rows of the object
      bind_sym   {(obj, symbol): exe address}  bind.bind_all(...)['sym']: the VA from the msvc/exe facts (N1)
      name_ok    {(obj, symbol): gate ids}     the asm <-> msvc name pairs the name gate certified
      record_ok  {(obj, record key): gate ids} the record types the layout and abi gates certified
      scalar_ok  {itanium type: gate ids}      the scalar types the data-model gate certified (a pointer is the type `P`)
      rows       one dict per certified object, the input of globext.build (image `exe`)
      rejected   [(symbol, reason)] for every object row that is not certified: nothing is guessed or filled in
An object is certified only when ALL hold: a `data` `def` row of the sympair section names it (msvc name not empty), the name gate
certified the pair, bind_sym has an address for it (an absolute-value pin, bit 32, is no address), and its type is certified: a record
element by record_ok on its key, a scalar element by scalar_ok, an array by its element type (the array's size is the plugin's
TYPE_SIZE_UNIT, element size times the declared extent; an incomplete array has no row, so it is absent).  certified_by is the union
of the gate ids of the name, the type and `n1` (the address).
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import l2sections

ABSOLUTE = 1 << 32


def parse_objects(data, where):
    out = []
    for n, ln in enumerate(l2sections._lines(data, where), 1):
        asm, size, align, elem, it, flags = l2sections._fields(ln, where, n, 6, 6)
        l2sections._need(asm, where, n, 'asm name')
        l2sections._need(it, where, n, 'type')
        l2sections._need(elem, where, n, 'element key')
        fl = tuple(f for f in flags.split(',') if f)
        for f in fl:
            if f not in ('internal', 'local'):
                raise ValueError('%s: line %d: unknown flag %r' % (where, n, f))
        out.append((asm, l2sections._int(size, where, n, 'size'), l2sections._int(align, where, n, 'align'), elem, it, fl))
    return out


def scalar_of(itanium):
    """the element scalar of an Itanium type string: array prefixes `A<n>_` and cv `V`/`K` dropped; a pointer or reference is `P`."""
    t = itanium
    while True:
        if t[:1] == 'A' and '_' in t:
            t = t[t.index('_') + 1:]
        elif t[:1] in ('V', 'K', 'r'):
            t = t[1:]
        else:
            break
    return 'P' if t[:1] in ('P', 'R', 'O', 'M') else t


def certify(obj, objects, sympair, bind_sym, name_ok, record_ok, scalar_ok, unit):
    data = {sym: ms for sym, ms, kind, df in sympair if kind == 'data' and df == 'def' and ms}
    rows, rejected = [], []
    for asm, size, align, elem, it, flags in objects:
        why = None
        if asm not in data:
            why = 'no data def row with an MSVC name in sympair'
        elif (obj, asm) not in name_ok:
            why = 'name pair not certified'
        elif (obj, asm) not in bind_sym:
            why = 'no exe address'
        elif bind_sym[(obj, asm)] & ABSOLUTE:
            why = 'absolute-value pin, no address'
        elif size < 1:
            why = 'no size'
        if why is None:
            if elem != '-':
                tg = record_ok.get((obj, elem))
                if tg is None:
                    why = 'record type %s not certified' % elem
            else:
                tg = scalar_ok.get(scalar_of(it))
                if tg is None:
                    why = 'scalar type %s not certified' % it
        if why:
            rejected.append((asm, why))
            continue
        gates = sorted(set(name_ok[(obj, asm)]) | set(tg) | {'n1'})
        rows.append({'image': 'exe', 'va': bind_sym[(obj, asm)], 'size': size, 'certified_by': gates, 'type': it, 'symbol': asm, 'unit': unit})
    return rows, rejected
