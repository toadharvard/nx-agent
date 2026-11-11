#!/usr/bin/env python3
"""Synthetic check of l2sections.py: every parser branch and error, and the ELF read when the cross tools exist."""
import os, shutil, subprocess, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import l2sections as L

fails = []


def check(what, got, want):
    if got != want:
        fails.append('%s: got %r want %r' % (what, got, want))


def err(what, fn, data, part):
    try:
        fn(data, 'o.o')
    except ValueError as e:
        if part not in str(e) or not str(e).startswith('o.o: line '):
            fails.append('%s: message %r lacks %r' % (what, str(e), part))
        return
    fails.append('%s: no error' % what)


SP = b'_Z1fv\t?f@@YAXXZ\tfunc\tdef\n_ZTV1A\t\tvtable\tdef\n_ZN1AC1Ev\t??0A@@QAE@XZ\tctor.c1\tref\n_Z3gccv\t\tunnamed:local scope number\tdef\n'
check('sympair', L.parse_sympair(SP, 'o.o'), [('_Z1fv', '?f@@YAXXZ', 'func', 'def'), ('_ZTV1A', '', 'vtable', 'def'),
                                              ('_ZN1AC1Ev', '??0A@@QAE@XZ', 'ctor.c1', 'ref'), ('_Z3gccv', '', 'unnamed:local scope number', 'def')])
check('sympair empty', L.parse_sympair(b'', 'o.o'), [])
err('sympair 3 fields', L.parse_sympair, SP + b'a\tb\tfunc\n', 'line 5')
err('sympair kind', L.parse_sympair, b'a\tb\tfunx\tdef\n', 'unknown kind')
err('sympair unnamed no reason', L.parse_sympair, b'a\t\tunnamed:\tdef\n', 'unknown kind')
err('sympair defref', L.parse_sympair, b'a\tb\tfunc\tdefx\n', 'neither def nor ref')
err('sympair no name', L.parse_sympair, b'a\t\tfunc\tdef\n', 'without an MSVC name')
err('sympair no symbol', L.parse_sympair, b'\tb\tfunc\tdef\n', 'symbol')
err('sympair no LF', L.parse_sympair, b'a\tb\tfunc\tdef', 'no final LF')
err('sympair blank line', L.parse_sympair, b'a\tb\tfunc\tdef\n\n', 'line 2')
err('sympair utf8', L.parse_sympair, b'\xff\n', 'UTF-8')

VT = b'_ZTV2D1\t8\t??_7D1@@6BB1@@@\t0\n_ZTV2D1\t24\tunnamed:why\t-4\n_ZTV1X\t8\t\t4\n'
check('vtpair', L.parse_vtpair(VT, 'o.o'), [('_ZTV2D1', 8, '??_7D1@@6BB1@@@', 0), ('_ZTV2D1', 24, 'unnamed:why', -4), ('_ZTV1X', 8, '', 4)])
err('vtpair fields', L.parse_vtpair, b'a\t8\tb\n', 'line 1')
err('vtpair offset', L.parse_vtpair, b'a\tx8\tb\t0\n', 'address point')
err('vtpair vfptr', L.parse_vtpair, b'a\t8\tb\t\n', 'vfptr offset')
err('vtpair symbol', L.parse_vtpair, b'\t8\tb\t0\n', '_ZTV symbol')

N3 = (b'_Z3topP1R\tok\t_ZL3midP1R,_ZL4leafP1R\t\t1R\n_ZL3midP1R\tok\t\tinline\t1R\n_Z2dvii\tdiv\n_ZL2cpii\tok\t\tconstprop.0\n'
      b'_ZN1AD2Ev\tok+E\t\tsdd\t\n_Z1gv\tok\t\talias\t1A,1B\n')
check('n3', L.parse_n3(N3, 'o.o'), [('_Z3topP1R', 'ok', ('_ZL3midP1R', '_ZL4leafP1R'), '', ('1R',)), ('_ZL3midP1R', 'ok', (), 'inline', ('1R',)),
                                    ('_Z2dvii', 'div', (), '', ()), ('_ZL2cpii', 'ok', (), 'constprop.0', ()), ('_ZN1AD2Ev', 'ok+E', (), 'sdd', ()),
                                    ('_Z1gv', 'ok', (), 'alias', ('1A', '1B'))])
check('n3 empty', L.parse_n3(b'', 'o.o'), [])
err('n3 1 field', L.parse_n3, b'a\n', '1 fields')
err('n3 6 fields', L.parse_n3, b'a\tok\t\t\t\tx\n', '6 fields')
err('n3 verdict', L.parse_n3, b'a\t\n', 'verdict')
err('n3 inlined', L.parse_n3, b'a\tok\tx,,y\n', 'inlined symbol')
err('n3 kind', L.parse_n3, b'a\tok\t\tbad kind\n', 'clone suffix')
err('n3 types', L.parse_n3, b'a\tok\t\t\t1A,\n', 'type')
err('n3 line number', L.parse_n3, b'a\tok\nb\n', 'line 2')

how = 'parsers only (no aarch64-linux-gnu-gcc / objcopy)'
gcc, oc = shutil.which('aarch64-linux-gnu-gcc'), shutil.which('aarch64-linux-gnu-objcopy')
if gcc and oc:
    how = 'parsers and ELF read'
    with tempfile.TemporaryDirectory() as td:
        c, o, o2 = (os.path.join(td, x) for x in ('t.c', 't.o', 't2.o'))
        open(c, 'w').write('int f(void){return 1;}\n')
        subprocess.run([gcc, '-c', '-o', o, c], check=True)
        check('read, no sections', L.read(o), dict(sympair=[], vtpair=[], n3=[]))
        cmd = [oc]
        for name, data in (('sympair', SP), ('vtpair', VT), ('n3', N3)):
            p = os.path.join(td, name)
            open(p, 'wb').write(data)
            cmd.append('--add-section=.fnvnx.%s=%s' % (name, p))
        subprocess.run(cmd + [o, o2], check=True)
        check('read', L.read(o2), dict(sympair=L.parse_sympair(SP, ''), vtpair=L.parse_vtpair(VT, ''), n3=L.parse_n3(N3, '')))
        p = os.path.join(td, 'bad')
        open(p, 'wb').write(b'x\n')
        subprocess.run([oc, '--add-section=.fnvnx.vtpair=' + p, o, os.path.join(td, 't3.o')], check=True)
        try:
            L.read(os.path.join(td, 't3.o'))
            fails.append('read of a malformed section: no error')
        except ValueError as e:
            check('read error names object and section', str(e).startswith('t3.o:.fnvnx.vtpair: line 1'), True)

for f in fails:
    print('FAIL', f)
print('path: ' + how, file=sys.stderr)
print('ok' if not fails else 'FAILED %d' % len(fails))
sys.exit(1 if fails else 0)
