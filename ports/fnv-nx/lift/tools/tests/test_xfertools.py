#!/usr/bin/env python3
"""tools/xfertally.py and tools/xferpp.py on a synthetic pack: units of lifted buffers (the real lifter's text) placed at the address of the exe and of a module row.
  (1) the tally attributes every function to its image by the module table's range, counts every form call by name (hooked / plain) and every legacy sequence of a text lifted before the forms;
  (2) the per-image selection takes the units with the most transfer sites, ties by unit name, and a unit without a site is never selected;
  (3) the token comparison drops line markers, is whitespace-insensitive, and finds the first differing token; the diff of two builds is 0 iff every unit has a PP result in both and the tokens are equal.
    test_xfertools.py"""
import collections, os, re, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(LIFT, 'fnvlift', 'tests')); import _inputs; _inputs.configure()
sys.path.insert(0, os.path.join(LIFT, 'tools'))
import xfertally, xferpp
import fnvlift as L
from fnvlift.image import Image
FAILS = []


def check(n, ok):
    print('%-78s %s' % (n, 'ok' if ok else 'FAIL'))
    if not ok: FAILS.append(n)


def lift(code, base):
    t = L.lift(Image.from_bytes(code, base), base, len(code))[0]
    return t


rel = lambda a, t: ((t - (a + 5)) & 0xffffffff).to_bytes(4, 'little')
EXE, MOD = 0x401000, 0x10001000
d = tempfile.mkdtemp(prefix='xfertools-'); os.makedirs(d + '/u')
open(d + '/modules.tsv', 'w').write('# name sha size pref base sizeimg tls entry static\nm.dll\t' + '0' * 64 + '\t1\t10000000\t10000000\t100000\t0\t10001000\t0\n')
call = b'\xe8' + rel(EXE, 0x409000) + b'\xff\xd0' + b'\xc3'           # 2 dispatcher calls, 1 ret
jmp = b'\xe9' + rel(MOD, 0x40a000)                                      # 1 tail/non-local jump
tx = {'u/000.src': lift(call, EXE), 'u/001.src': lift(jmp, MOD), 'u/002.src': lift(b'\x90\xc3', EXE)}
t0, t1, t2 = tx['u/000.src'], tx['u/001.src'], tx['u/002.src']
for n, t in tx.items(): open(d + '/' + n, 'w').write('#include "fnvl_prelude.h"\n' + t)
rows = xfertally.modules(d + '/modules.tsv')
check('module row range read: [0x10000000, 0x10100000)', rows == [('m.dll', 0x10000000, 0x10100000)])
check('image_of: module address -> row, any other -> exe', xfertally.image_of(rows, MOD) == 'm.dll' and xfertally.image_of(rows, EXE) == 'exe')
r = xfertally.tally(d, rows)
check('tally: 3 units, every function counted', r['units'] == 3 and r['total']['functions'] == 3)
ex, mo = r['images']['exe']['forms'], r['images']['m.dll']['forms']
check('tally: exe forms %s' % ex, ex.get('CALLD_plain') == 2 and ex.get('RET_plain') == 2 and sum(ex.values()) == 4)
check('tally: module forms %s' % mo, sum(mo.values()) == 1)
check('tally: bytes add up to the files', r['total']['bytes'] == sum(os.path.getsize(d + '/' + n) for n in tx))
check('tally: no legacy sequence in a lifter-v2 text', not any(r['total']['legacy'].values()))
# a hooked form (extended hook) is counted apart from a plain one
imgs = collections.defaultdict(xfertally.new_image)
xfertally.tally_text('extern "C" void L_00401000(void)\n{ FNVL_CALLX(c->fx[0]=s0;SYNC_OUT;,SYNC_IN;,00402000,0x401005u) FNVL_RETP(t) }\n', rows, imgs)
check('tally: hooked and plain names are counted apart', dict(imgs['exe']['forms']) == {'CALLX_hooked': 1, 'RET_plain': 1})
# legacy text (before the forms)
LG = ('extern "C" void L_00401000(void)\n{ DECL;\n{ esp-=4; M32(esp)=0x401005u; SYNC_OUT; REGS(); CALLX(00402000); REGS(); SYNC_IN; if(c->ra!=0x401005u) goto nonlocal; }\n'
      'SYNC_OUT; REGS(); fnvl_dispatch(eax)(); REGS(); SYNC_IN; if(c->ra!=0x401007u) goto nonlocal;\n{ SYNC_OUT; REGS(); CALLX(00403000); REGS(); SYNC_IN; goto after_tail; }\n'
      '{ c->ra=0x409000u; goto nonlocal; }\nc->ra=ra; SYNC_OUT; return;\n}\n')
imgs = collections.defaultdict(xfertally.new_image); xfertally.tally_text(LG, rows, imgs)
check('tally: legacy sequences %s' % dict(imgs['exe']['legacy']), dict(imgs['exe']['legacy']) == {'call_entry': 1, 'call_dispatch': 1, 'tail': 1, 'jmp_nonlocal': 1, 'ret': 1})
# (2) selection
sel = xferpp.select(d, rows, 1)
check('select: one unit per image with the most sites (exe: u/000, module: u/001)', sel == ['u/000.cpp', 'u/001.cpp'])
check('select: even with a large K every picked unit has a site (u/002 has one ret: it is picked, never an empty unit)', xferpp.select(d, rows, 5) == ['u/000.cpp', 'u/001.cpp', 'u/002.cpp'])
# (3) tokens
a = '# 1 "/src/u/000.cpp"\nint f( ) { c->ra = 5 ; }\n'; b = '# 7 "/src/u/000.cpp" 2\nint f(){c->ra=5;}\n'
check('tokens: line markers and whitespace do not matter', xferpp.tokens(a) == xferpp.tokens(b))
check('tokens: a different token is a difference', xferpp.tokens(a) != xferpp.tokens(b.replace('5', '6')))
ii = {('B', 'u/000.cpp'): a, ('A', 'u/000.cpp'): b, ('B', 'u/001.cpp'): a, ('A', 'u/001.cpp'): b.replace('5', '6')}
xferpp.ii_of = lambda repo, out, store, unit: ii.get((out, unit))
check('diff: equal tokens -> 0', xferpp.diff('r', 'B', 's', 'A', 's', ['u/000.cpp']) == 0)
check('diff: a differing unit -> 1', xferpp.diff('r', 'B', 's', 'A', 's', ['u/000.cpp', 'u/001.cpp']) == 1)
check('diff: a unit without a PP result -> 1', xferpp.diff('r', 'B', 's', 'A', 's', ['u/000.cpp', 'u/009.cpp']) == 1)
sys.exit(1 if FAILS else 0)
