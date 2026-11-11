#!/usr/bin/env python3
"""S11 host tests for tools/equiv/lemma.py on synthetic i386 COFF objects (no compiler needed).
test_lemma.py <lemma.py> <scratch dir>"""
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys

LM, OUT = Path(sys.argv[1]), Path(sys.argv[2])
spec = importlib.util.spec_from_file_location('lemma', LM)
lm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lm)
fails = []


def check(cond, what):
    if not cond:
        fails.append(what)
        print('FAIL', what)


def coff(funcs, stamp=1, order=None, strings=()):
    """funcs: [(name, code, [(off, target, type)])]; target is an external name or ('str', i) = static string i in
    its own .rdata COMDAT. One COMDAT .text per function (flags as MSVC: 0x60501020), one 'comdat' section symbol."""
    secs, syms = [], []             # secs: (name, flags, data, relocs by symbol key)
    names = {}
    def sym(key, name, value, sec, cls, aux=None):
        syms.append((key, name, value, sec, cls, aux))
    for i, s in enumerate(strings):
        secs.append(('.rdata', 0x40301040, s, []))
        sym(('sec', len(secs)), '.rdata', 0, len(secs), 3, struct.pack('<IHHIHB3x', len(s), 0, 0, 0, 0, 2))
        sym(('str', i), '$SG%d' % (1000 + i * 7 + stamp), 0, len(secs), 3)
    for n, code, rel in funcs:
        secs.append(('.text', 0x60501020, code, rel))
        sym(('sec', len(secs)), '.text', 0, len(secs), 3, struct.pack('<IHHIHB3x', len(code), len(rel), 0, 0, 0, 2))
        sym(('fn', n), n, 0, len(secs), 2)
    ext = {t for _, _, rel in funcs for _, t, _ in rel if isinstance(t, str)}
    for e in sorted(ext):
        sym(('ext', e), e, 0, 0, 2)
    if order:
        syms.sort(key=lambda s: order(s[1]))
    idx, pos = {}, 0
    for s in syms:
        idx[s[0]] = pos
        pos += 1 + (1 if s[5] else 0)
    strtab = b''
    body = b''
    for s in syms:
        n = s[1].encode()
        if len(n) > 8:
            raw = b'\0\0\0\0' + struct.pack('<I', 4 + len(strtab))
            strtab += n + b'\0'
        else:
            raw = n.ljust(8, b'\0')
        body += raw + struct.pack('<IhHBB', s[2], s[3], 0x20 if s[4] == 2 and s[3] > 0 and s[0][0] == 'fn' else 0, s[4], 1 if s[5] else 0)
        if s[5]:
            body += s[5]
    off = 20 + 40 * len(secs)
    raw, hdr = b'', b''
    for si, (n, fl, data, rel) in enumerate(secs):
        rels = b''.join(struct.pack('<IIH', o, idx[('str', t[1]) if isinstance(t, tuple) else ('ext', t)], ty) for o, t, ty in rel)
        hdr += n.encode().ljust(8, b'\0') + struct.pack('<IIIIIIHHI', 0, 0, len(data), off + len(raw), off + len(raw) + len(data) if rel else 0, 0, len(rel), 0, fl)
        raw += data + rels
    symptr = off + len(raw)
    return struct.pack('<HHIIIHH', 0x14c, len(secs), stamp, symptr, pos, 0, 0) + hdr + raw + body + struct.pack('<I', 4 + len(strtab)) + strtab


def run(a, b, units=1):
    d = OUT / ('run%d' % run.n)
    run.n += 1
    for side, o in (('a', a), ('b', b)):
        (d / side).mkdir(parents=True)
        if o is not None:
            (d / side / 'u0.obj').write_bytes(o)
    (d / 'units.json').write_text(json.dumps({'units': [{'source': 'x.cpp'}]}))
    r = subprocess.run([sys.executable, '-s', '-B', str(LM), str(d / 'a'), str(d / 'b'), str(d / 'units.json'), '--out', str(d / 'o')])
    check(r.returncode == 0, 'exit code')
    rows = [l.split('\t') for l in (d / 'o/lemma.tsv').read_text().splitlines()[1:]]
    return {r[2]: (r[3], r[4]) for r in rows}, json.loads((d / 'o/summary.json').read_text())


run.n = 0
code1 = bytes(range(16))
F = lambda code=code1, rel=((4, 'ext_a', 20),), name='?f@@YAXXZ': (name, code, list(rel))
G = ('?g@@YAXXZ', b'\x90' * 8, [(1, ('str', 0), 6)])

base = coff([F(), G], strings=[b'hello\0'])
v, s = run(base, base)
check(v == {'?f@@YAXXZ': ('equal', ''), '?g@@YAXXZ': ('equal', '')}, 'identical objects equal: %s' % v)
v, s = run(base, coff([F(), G], stamp=2, strings=[b'hello\0']))      # timestamp and the $SG name differ
check(all(x[0] == 'equal' for x in v.values()), 'timestamp / static symbol names ignored: %s' % v)
v, s = run(base, coff([G, F()], strings=[b'hello\0'], order=lambda n: n[::-1]))   # section and symbol order
check(all(x[0] == 'equal' for x in v.values()) and len(v) == 2, 'reordered sections/symbols equal: %s' % v)
v, s = run(base, coff([F(code=code1[:9] + b'\xff' + code1[10:]), G], strings=[b'hello\0']))
check(v['?f@@YAXXZ'] == ('differs', 'bytes@0x9 code') and v['?g@@YAXXZ'][0] == 'equal', 'byte flip: %s' % v)
v, s = run(base, coff([F(rel=((4, 'ext_b', 20),)), G], strings=[b'hello\0']))
check(v['?f@@YAXXZ'] == ('differs', 'reloc@0x4 code'), 'reloc target name: %s' % v)
v, s = run(base, coff([F(rel=((4, 'ext_a', 6),)), G], strings=[b'hello\0']))
check(v['?f@@YAXXZ'] == ('differs', 'reloc@0x4 code'), 'reloc type: %s' % v)
v, s = run(base, coff([F(rel=((5, 'ext_a', 20),)), G], strings=[b'hello\0']))
check(v['?f@@YAXXZ'] == ('differs', 'reloc@0x4 code'), 'reloc offset: %s' % v)
v, s = run(base, coff([F(), G], strings=[b'hellp\0']))
check(v['?g@@YAXXZ'][0] == 'differs' and v['?f@@YAXXZ'][0] == 'equal', 'static target content differs: %s' % v)
v, s = run(base, coff([F()], strings=[b'hello\0']))
check(v['?g@@YAXXZ'][0] == 'only-pristine', 'only-pristine: %s' % v)
v, s = run(coff([F()]), coff([F(), G], strings=[b'hello\0']))
check(v['?g@@YAXXZ'][0] == 'only-prepared', 'only-prepared: %s' % v)
v, s = run(None, base)
check(s['units']['pristine-missing'] == 1 and s['units']['both'] == 0, 'pristine missing: %s' % s['units'])
v, s = run(base, None)
check(s['units']['prepared-missing'] == 1, 'prepared missing: %s' % s['units'])
v, s = run(base, coff([F(code=code1[:9] + b'\xff' + code1[10:]), G], strings=[b'hello\0']))
check(s['functions'] == dict(equal=1, differs=1, **{'only-pristine': 0, 'only-prepared': 0}), 'summary counts: %s' % s['functions'])
check(s['differs_by_kind'] == {'bytes': 1}, 'differs_by_kind: %s' % s['differs_by_kind'])
bad = OUT / 'bad'
bad.mkdir()
(bad / 'u0.obj').write_bytes(b'not an object' * 4)
(OUT / 'bu.json').write_text(json.dumps({'units': [{'source': 'x.cpp'}]}))
r = subprocess.run([sys.executable, '-s', '-B', str(LM), str(bad), str(bad), str(OUT / 'bu.json'), '--out', str(OUT / 'bo')],
                   capture_output=True)
check(r.returncode != 0, 'unreadable object: non-zero exit')
if fails:
    sys.exit(1)
print('test_lemma: ok')
