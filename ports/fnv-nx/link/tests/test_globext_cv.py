#!/usr/bin/env python3
"""Synthetic check of globext_cv (invented COFF objects with CodeView, invented N1 pins and exe ranges; no game data): sizes from the object's own
types, pins by external and static key, every reason an object is left out, the universe, probes, the overlap error."""
import os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'contract'))
import synth, coffobj, globext, globext_cv as g

fails = []


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r want %r' % (what, got, want))


cv = synth.CV()
rec = lambda kind, prop, size, name, uniq: cv.add(kind, struct.pack('<HHIII', 0, prop, 0, 0, 0) + synth.num(size) + synth.cstr(name) + synth.cstr(uniq))
S = rec(0x1505, 0x200, 8, 'S', '.?AUS@@')
FWD = rec(0x1505, 0x280, 0, 'F', '.?AUF@@')
ARR = cv.add(0x1503, struct.pack('<II', 0x74, 0x75) + synth.num(28) + synth.cstr(''))
PTR = cv.add(0x1002, struct.pack('<II', 0x74, 0x1000a))            # attr: size 4 in bits 13..18 -> (4 << 13) | 0x0a
PTR = cv.add(0x1002, struct.pack('<II', 0x74, (4 << 13) | 0x0a))
MOD = cv.add(0x1001, struct.pack('<IH', S, 1) + b'\0\0')
data = [('?gs@@3US@@A', S, 2, 'G'), ('?arr@@3PAHA', ARR, 2, 'G'), ('?fw@@3UF@@A', FWD, 2, 'G'), ('?p@@3PAHA', PTR, 2, 'G'),
        ('_st', 0x74, 3, 'L'), ('?cs@@3QBUS@@B', MOD, 2, 'G'), ('?prim@@3HA', 0x74, 2, 'G')]
body, relocs = b'', []
base = 4 + 8
for name, ti, cls, kind in data:
    nm = synth.cstr(name)
    payload = struct.pack('<IIH', ti, 0, 0) + nm
    rl = 2 + len(payload)
    relocs.append((base + len(body) + 8, name.encode(), 0x0B))
    body += struct.pack('<HH', rl, 0x110d if kind == 'G' else 0x110c) + payload
dbgS = struct.pack('<I', 4) + struct.pack('<II', 0xF1, len(body)) + body
dbgS += b'\0' * ((-len(dbgS)) % 4)
def build(z7, mutate=None):
    """the invented object: .data and .text, the symbols; z7=True adds the debug sections first (shifting every number) and their section symbols"""
    k0 = 2 if z7 else 0
    d = [dict(name=n.encode(), value=8 * k, sec=1 + k0, type=0, cls=c, naux=0) for k, (n, _t, c, _k) in enumerate(data)]
    d += [dict(name=b'?nocv@@3HA', value=64, sec=1 + k0, type=0, cls=2), dict(name=b'?f@@YAXXZ', value=0, sec=2 + k0, type=0x20, cls=2),
          dict(name=b'?incode@@3HA', value=4, sec=2 + k0, type=0, cls=2), dict(name=b'?undef@@3HA', value=0, sec=0, type=0, cls=2),
          dict(name=b'.data', value=0, sec=1 + k0, type=0, cls=3, naux=1)]
    secs = [dict(name=b'.data', chars=0xC0300040, data=b'\0' * 96), dict(name=b'.text', chars=0x60500020, data=b'\xc3')]
    if mutate:
        d, secs = mutate(d, secs)
    if z7:
        d = [dict(name=b'.debug$S', value=0, sec=1, type=0, cls=3, naux=1), dict(name=b'.debug$T', value=0, sec=2, type=0, cls=3, naux=1)] + d
        secs = [dict(name=b'.debug$S', chars=0x42100040, data=dbgS, relocs=relocs), dict(name=b'.debug$T', chars=0x42100040, data=cv.blob())] + secs
    return synth.coff(secs, d)


zobj, pobj = build(True), build(False)
c = coffobj.parse(zobj, 'z7.obj')
rows = g.unit_objects(c)
by = {r[1]: r for r in rows}
check('symbols of the universe', sorted(by), sorted(['?gs@@3US@@A', '?arr@@3PAHA', '?fw@@3UF@@A', '?p@@3PAHA', '_st', '?cs@@3QBUS@@B', '?prim@@3HA', '?nocv@@3HA']))
check('struct', (by['?gs@@3US@@A'][3], by['?gs@@3US@@A'][4], by['?gs@@3US@@A'][5]), (8, '.?AUS@@', ''))
check('array', by['?arr@@3PAHA'][3:5], (28, 'array:28'))
check('pointer', by['?p@@3PAHA'][3:5], (4, 'pointer:4'))
check('modifier of a struct', by['?cs@@3QBUS@@B'][3], 8)
check('primitive', by['?prim@@3HA'][3:5], (4, 'prim:4'))
check('forward reference', by['?fw@@3UF@@A'][3:], (None, None, 'type size: incomplete type'))
check('no record', by['?nocv@@3HA'][5], 'no S_GDATA32/S_LDATA32 record')
check('static flag', (by['_st'][2], by['?gs@@3US@@A'][2]), (True, False))
# the binding of the debug object to the pristine one
ok, why, m = g.bind_unit(zobj, pobj)
check('z7 equals pristine', (ok, why), (True, ''))
check('index map skips the debug symbols', sorted(m.values()), list(range(len(m))))
pc = coffobj.parse(pobj, 'p.obj')
uni, cvr = g.unit_cv(zobj, pobj)
check('universe is the pristine object\'s', sorted(n for _i, n, _s in uni), sorted(by))
si = {n: i for i, n, _s in uni}
check('cv by pristine index', cvr[si['?gs@@3US@@A']], (8, '.?AUS@@', ''))
check('absent z7', g.unit_cv(None, pobj)[1], 'no z7 object (cl failed or not built)')


def other(f):
    return lambda d, s: f(d, s) or (d, s)


def chg_data(d, s):
    s[0] = dict(s[0], data=b'\1' + b'\0' * 95)
def chg_size(d, s):
    s[0] = dict(s[0], data=b'\0' * 100)
def chg_val(d, s):
    d[0] = dict(d[0], value=d[0]['value'] + 4)
def chg_cls(d, s):
    d[1] = dict(d[1], cls=3)
def chg_chars(d, s):
    s[0] = dict(s[0], chars=s[0]['chars'] | 0x1000)
def extra_sec(d, s):
    s.append(dict(name=b'.xdata', chars=0x40300040, data=b'\0' * 4))
for nm, f, want in (('contents', chg_data, 'sections differ'), ('size', chg_size, 'sections differ'), ('symbol value', chg_val, 'symbols differ'),
                    ('storage class', chg_cls, 'symbols differ'), ('COMDAT characteristic', chg_chars, 'sections differ'), ('an extra section', extra_sec, 'sections differ')):
    ok, why, _m = g.bind_unit(zobj, build(False, other(f)))
    check('z7 != pristine: ' + nm, (ok, why), (False, want))
    check('unit absent: ' + nm, g.unit_cv(zobj, build(False, other(f)))[1], 'z7!=pristine: ' + want)
ok, why, _m = g.bind_unit(b'\0' * 4, pobj)
check('unreadable', (ok, why.startswith('unreadable')), (False, True))
n1 = {(0, '?gs@@3US@@A'): 0x1000, (0, '?arr@@3PAHA'): 0x1010, (0, '?fw@@3UF@@A'): 0x1040, (0, si['_st'], '_st'): 0x1050, (0, '?p@@3PAHA'): 0x1060,
      (0, '?cs@@3QBUS@@B'): 0x1070, (0, '?prim@@3HA'): 5 | g.ABSOLUTE, (0, '?nocv@@3HA'): 0x1080}
res = g.certify_all([(0, 'u0', uni, cvr), (1, 'u1', uni, cvr), (2, 'u2', uni, 'z7!=pristine: sections differ')], {**n1, (1, '?gs@@3US@@A'): 0x1000, (2, '?nocv@@3HA'): 0x1090}, [(0x1000, 0x2000)], [0x1004, 0x1040, 0x1050, 0x1080, 0x3000], (0x1000, 0x1060))
check('certified addresses', [(o[1], o[2], o[3]) for o in res['objects']], [(0x1000, 8, 'n1+cv+z7eq'), (0x1010, 28, 'n1+cv+z7eq'), (0x1050, 4, 'n1+cv+z7eq'), (0x1060, 4, 'n1+cv+z7eq'), (0x1070, 8, 'n1+cv+z7eq')])
check('the unit of a shared object', res['objects'][0][6], 'u0,u1')
check('uncertified reasons', sorted({w for _s, _n, w in res['uncertified']}),
      ['absolute-value pin, no address', 'no S_GDATA32/S_LDATA32 record', 'type size: incomplete type', 'z7!=pristine: sections differ'])
check('counts', (res['counts']['certified'], res['counts']['universe'], res['counts']['universe_certified']), (5, 8, 5))
check('range', res['counts']['range'], dict(lo=0x1000, hi=0x1060, certified=3, universe=4))
check('probe in', res['probes'][0x1004], 'certified ?gs@@3US@@A [0x1000,0x1008) n1+cv+z7eq')
check('probe forward', res['probes'][0x1040], 'none certified; universe: ?fw@@3UF@@A@u0: type size: incomplete type')
check('probe no record', res['probes'][0x1080].startswith('none certified; universe: ?nocv@@3HA@u0: no S_GDATA32/S_LDATA32 record'), True)
check('probe none', res['probes'][0x3000], 'none certified; no pinned data symbol')
try:
    g.certify_all([(0, 'u0', uni, cvr)], {(0, '?gs@@3US@@A'): 0x1000, (0, '?arr@@3PAHA'): 0x1004}, [(0x1000, 0x2000)])
    fails.append('overlap: no error')
except ValueError as e:
    check('overlap names both', ('?gs@@3US@@A' in str(e), '?arr@@3PAHA' in str(e)), (True, True))
bad = synth.coff([dict(name=b'.debug$S', chars=0x42100040, data=b'\1\0\0\0')], [])
try:
    g.unit_objects(coffobj.parse(bad, 'bad.obj'))
    fails.append('bad signature: no error')
except ValueError:
    pass
if fails:
    print('\n'.join(fails))
    sys.exit(1)
print('globext_cv: ok')
