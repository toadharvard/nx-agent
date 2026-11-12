#!/usr/bin/env python3
"""store - the one on-disk encoding of every contract stage output: canonical JSON (no pickle).

    save(path, kind, obj)     write  {"data":<encoded obj>,"format":"fnvnx-store/1","kind":kind}  + LF
    load(path, kind=None)     read it back; the in-memory object is exactly what the stage built (same Python types),
                              so every reader sees the shape its docstring documents.  `kind` is checked when given.

Bytes: json.dump(sort_keys=True, separators=(',', ':')), ensure_ascii, LF.  Equal objects give equal bytes (no set or
dict order, no process state reaches the file).  Encoding rules, closed over the whole file set:
  set                  -> sorted list (elements are ints or strs, never mixed in one set)
  tuple                -> list; the decoder of each kind restores the tuples at the places the kind documents
  dict with int keys   -> JSON object with decimal-string keys (json does this); the decoder applies int()
  dict with tuple keys -> key = the fields joined by TAB; ints in decimal (see KINDS); a TAB inside a text field is an
                          error (save refuses), so the split is unambiguous
Kinds (the explicit per-file encoding):
  facts  exefacts.py   fn/vft/imports keys: decimal address; F['ret'] tuple; the lists calls tails icalls itails stores
                       vrefs data codeptrs imports jt of F hold tuples; secs/ptrs/vft values tuples
  n1     n1.py         fn keys decimal address, F['relocs'] list of tuples;
                       sym keys "unit\\tsymbol" (2-tuple) or "unit\\tindex\\tsymbol" (3-tuple), unit and index decimal;
                       vt keys "unit\\tsymbol"
  obj    objfacts.py   keys "object basename\\t%08x" (function) or "object basename\\t<section kind>", the kind one of
                       SECTION_KINDS; function value (vt [tuples], data set, calls set, other set, vcall list), ztv value
                       {symbol: [tuples]}, sympair / vtpair / n3 values the row lists of l2sections.py (tuples; in n3 rows
                       the inlined and types fields are tuples too)
  bind   bind.py       sym keys "object\\tsymbol" -> address; vt keys "object\\t_ZTV symbol\\toffset" -> address; n3 keys
                       "object\\t%08x" -> [verdict, [addresses]]; unbound sym [(object, symbol, kind, reason)] and
                       vt [(object, _ZTV symbol, offset, reason)]
  objone objfacts_one.py  the same encoding as `obj`, for the entries of one object (one file per object)
  vtid   vtid.py       bind keys "unit\\t_ZTV symbol\\toffset" (offset decimal), errors [tuples]
  eh     ehfacts.py    funcinfo/stubs/owner/kind keys decimal address; unwind_at, catch_at sorted lists (sets in memory)
"""
import json

FORMAT = 'fnvnx-store/1'


def _tab(*fields):
    for f in fields:
        if isinstance(f, str) and '\t' in f:
            raise ValueError('TAB inside a key field: %r' % f)
    return '\t'.join(str(f) for f in fields)


def _tl(x):
    return [tuple(e) for e in x]


def _ik(d, f=lambda v: v):
    return {int(k): f(v) for k, v in d.items()}


# ---- facts -------------------------------------------------------------------------------------------------------
TUPLE_LISTS = ('calls', 'tails', 'icalls', 'itails', 'stores', 'vrefs', 'data', 'codeptrs', 'imports', 'jt')


def _facts_enc(o):
    return o


def _facts_dec(o):
    fn = {}
    for k, F in o['fn'].items():
        F['ret'] = tuple(F['ret'])
        for t in TUPLE_LISTS:
            F[t] = _tl(F[t])
        fn[int(k)] = F
    o['fn'] = fn
    o['secs'] = {k: tuple(v) for k, v in o['secs'].items()}
    o['imports'] = _ik(o['imports'], tuple)
    o['vft'] = _ik(o['vft'], tuple)
    o['ptrs'] = _tl(o['ptrs'])
    return o


# ---- n1 ----------------------------------------------------------------------------------------------------------
def _n1_enc(o):
    return dict(fn=o['fn'], exe_sha256=o['exe_sha256'],
                sym={_tab(*k): v for k, v in o['sym'].items()}, vt={_tab(*k): v for k, v in o['vt'].items()})


def _n1_key(s):
    p = s.split('\t')
    if len(p) == 2:
        return (int(p[0]), p[1])
    if len(p) == 3:
        return (int(p[0]), int(p[1]), p[2])
    raise ValueError('bad n1 key %r' % s)


def _n1_dec(o):
    fn = {}
    for k, F in o['fn'].items():
        F['relocs'] = _tl(F['relocs'])
        fn[int(k)] = F
    return dict(fn=fn, exe_sha256=o['exe_sha256'], sym={_n1_key(k): v for k, v in o['sym'].items()},
                vt={_n1_key(k): v for k, v in o['vt'].items()})


# ---- obj ---------------------------------------------------------------------------------------------------------
SECTION_KINDS = ('ztv', 'sympair', 'vtpair', 'n3')       # the non-function entries (object, kind) of an obj / objone file


def is_function_key(k):
    """True for a key (object, exe address) of a function entry; False for (object, one of SECTION_KINDS)."""
    return k[1] not in SECTION_KINDS


def _n3_row(r):
    return (r[0], r[1], tuple(r[2]), r[3], tuple(r[4]))


def _obj_enc(o):
    out = {}
    for (ob, a), v in o.items():
        if a in SECTION_KINDS:
            out[_tab(ob, a)] = v
        else:
            vt, data, calls, other, vcall = v
            out[_tab(ob, '%08x' % a)] = [vt, sorted(data), sorted(calls), sorted(other), vcall]
    return out


def _obj_dec(o):
    out = {}
    for k, v in o.items():
        ob, a = k.split('\t')
        if a == 'ztv':
            out[(ob, a)] = {n: _tl(pts) for n, pts in v.items()}
        elif a in ('sympair', 'vtpair'):
            out[(ob, a)] = _tl(v)
        elif a == 'n3':
            out[(ob, a)] = [_n3_row(r) for r in v]
        else:
            vt, data, calls, other, vcall = v
            out[(ob, int(a, 16))] = (_tl(vt), set(data), set(calls), set(other), vcall)
    return out


# ---- bind --------------------------------------------------------------------------------------------------------
def _bind_enc(o):
    return dict(sym={_tab(*k): v for k, v in o['sym'].items()}, vt={_tab(*k): v for k, v in o['vt'].items()},
                n3={_tab(ob, '%08x' % a): [v, list(ad)] for (ob, a), (v, ad) in o['n3'].items()},
                unbound_sym=o['unbound_sym'], unbound_vt=o['unbound_vt'])


def _bind_dec(o):
    sym = {}
    for k, v in o['sym'].items():
        ob, s = k.split('\t')
        sym[(ob, s)] = v
    vt = {}
    for k, v in o['vt'].items():
        ob, z, off = k.split('\t')
        vt[(ob, z, int(off))] = v
    n3 = {}
    for k, (v, ad) in o['n3'].items():
        ob, a = k.split('\t')
        n3[(ob, int(a, 16))] = (v, list(ad))
    return dict(sym=sym, vt=vt, n3=n3, unbound_sym=_tl(o['unbound_sym']), unbound_vt=_tl(o['unbound_vt']))


# ---- vtid --------------------------------------------------------------------------------------------------------
def _vtid_enc(o):
    return dict(bind={_tab(*k): v for k, v in o['bind'].items()}, errors=o['errors'])


def _vtid_dec(o):
    bind = {}
    for k, v in o['bind'].items():
        u, z, off = k.split('\t')
        bind[(u, z, int(off))] = v
    return dict(bind=bind, errors=_tl(o['errors']))


# ---- eh ----------------------------------------------------------------------------------------------------------
def _eh_enc(o):
    o = dict(o)
    o['unwind_at'] = sorted(o['unwind_at'])
    o['catch_at'] = sorted(o['catch_at'])
    return o


def _eh_dec(o):
    fi = {}
    for k, f in o['funcinfo'].items():
        f['unwind'] = _tl(f['unwind'])
        f['tries'] = [(lo, hi, ch, _tl(hs)) for lo, hi, ch, hs in f['tries']]
        fi[int(k)] = f
    r = dict(funcinfo=fi, stubs=_ik(o['stubs']), owner=_ik(o['owner']), unwind_at=set(o['unwind_at']),
             catch_at=set(o['catch_at']))
    if 'kind' in o:
        r['kind'] = _ik(o['kind'])
    return r


KINDS = dict(facts=(_facts_enc, _facts_dec), n1=(_n1_enc, _n1_dec), obj=(_obj_enc, _obj_dec), objone=(_obj_enc, _obj_dec),
             bind=(_bind_enc, _bind_dec), vtid=(_vtid_enc, _vtid_dec), eh=(_eh_enc, _eh_dec))


def save(path, kind, obj):
    enc = KINDS[kind][0](obj)
    with open(path, 'w', newline='\n') as fh:
        json.dump(dict(data=enc, format=FORMAT, kind=kind), fh, sort_keys=True, separators=(',', ':'))
        fh.write('\n')


def load(path, kind=None):
    with open(path) as fh:
        d = json.load(fh)
    if d.get('format') != FORMAT or (kind is not None and d.get('kind') != kind):
        raise ValueError('%s: not a %s file of kind %s' % (path, FORMAT, kind))
    return KINDS[d['kind']][1](d['data'])
