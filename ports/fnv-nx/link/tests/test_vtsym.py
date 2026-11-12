#!/usr/bin/env python3
"""VTSYM1 of toolchain/sdd/fnvnx_sdd.cc on the game's compiler (msvcmodel, all 8 plugins, the one build via realobj.build): wherever `_ZTV<C>` is
emitted, every address point `_ZTV<C>.fnvnx_ap<off>` is defined with it, weak like the table, in the table's own section and comdat group, with
or without RTTI (-fno-rtti leaves no `_ZTI` slot) and with -fdata-sections; two translation units that both emit the table link with no undefined
and no duplicate ap symbol. Cases: a class without a key function (inline virtuals), a COM-style interface (pure virtuals only), a key-function
class, a class with a virtual base. Plain script: exit 0 on pass. Run: python3 -I <this file>  (needs the pinned compiler: ARTIFACTS_URL)."""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import realobj

FAILED = []


def check(name, ok, detail=''):
    print('%s %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + str(detail)[:700]) if detail and not ok else ''), flush=True)
    if not ok:
        FAILED.append(name)
    return ok


COM = '''struct IUnk { virtual long Q(int) = 0; virtual unsigned AddRef() = 0; virtual unsigned Rel() = 0; };
struct IDev : IUnk { virtual long Clear(int) = 0; virtual long Present() = 0; };
struct Dev : IDev { long Q(int) { return 0; } unsigned AddRef() { return 1; } unsigned Rel() { return 0; } long Clear(int) { return 0; } long Present() { return 0; } int m; };
'''
INL = '''struct I { virtual int f() = 0; virtual ~I() {} };
struct C : I { int f() { return 1; } int v; };
struct Base { virtual void a() {} int x; };
struct Der : Base { virtual void b() {} };
'''
KEY = '''struct K { virtual void g(); virtual void h() {} };
void K::g() {}
'''
VBASE = '''struct VB { virtual void p() {} int a; };
struct L : virtual VB { virtual void q() {} };
struct R : virtual VB { virtual void r() {} };
struct D : L, R { virtual void s() {} };
'''
USE = '''
%(com)s%(inl)s%(vb)s
long use_%(n)s(IDev *d, C *c, Der *e, D *dd) { c->f(); e->a(); dd->s(); dd->p(); return d->Present(); }
IDev *mk_%(n)s() { return new Dev; }
C *mkc_%(n)s() { return new C; }
Der *mkd_%(n)s() { return new Der; }
D *mkdd_%(n)s() { return new D; }
'''


def src(n):
    return (USE % {'com': COM.replace('struct ', 'struct ') if n == 'a' else COM, 'inl': INL, 'vb': VBASE, 'n': n}).replace(
        'struct ', 'struct ') + (KEY if n == 'a' else '')


def readelf(args, o):
    return subprocess.run(['aarch64-linux-gnu-readelf'] + args + [o], capture_output=True, text=True, check=True).stdout


def syms(o):
    out = {}
    for l in readelf(['-sW'], o).splitlines():
        f = l.split()
        if len(f) >= 8 and f[0].endswith(':') and f[0][:-1].isdigit():
            out[f[7]] = dict(value=int(f[1], 16), bind=f[4], ndx=f[6])
    return out


def main():
    for label, flags in (('rtti', []), ('no-rtti no-exceptions data-sections', ['-fno-rtti', '-fno-exceptions', '-fdata-sections', '-fPIE'])):
        res = realobj.build({'a': src('a'), 'b': src('b')}, flags=flags)
        if not check('[%s] two units with inline-emitted vtables build on msvcmodel with the 8 plugins' % label, res['status'] == 'green', res.get('failure')):
            continue
        objs = {os.path.splitext(o['unit'])[0]: o['object'] for o in res['objects']}
        for u, o in sorted(objs.items()):
            s = syms(o)
            tabs = sorted(n for n in s if n.startswith('_ZTV') and '.fnvnx_ap' not in n and s[n]['ndx'] not in ('UND',))
            undef = sorted(n for n in s if '.fnvnx_ap' in n and s[n]['ndx'] == 'UND')
            check('[%s] %s: no address point is undefined (tables defined here: %d)' % (label, u, len(tabs)), tabs and not undef, undef)
            bad = []
            groups = readelf(['-gW'], o)
            for t in tabs:
                aps = [n for n in s if n.startswith(t + '.fnvnx_ap')]
                if not aps:
                    bad.append(t + ': no ap')
                for a in aps:
                    if s[a]['bind'] != s[t]['bind']:
                        bad.append('%s binding %s vs %s' % (a, s[a]['bind'], s[t]['bind']))
                    if s[a]['ndx'] != s[t]['ndx']:
                        bad.append('%s section %s vs table %s' % (a, s[a]['ndx'], s[t]['ndx']))
            check('[%s] %s: every table has its address points, weak like it, in its own section' % (label, u), not bad, bad[:5])
            if s.get('_ZTV1C', {}).get('bind') == 'WEAK':
                check('[%s] %s: the weak vtable sits in a comdat group together with its address points' % (label, u), 'COMDAT' in groups, groups[:300])
            vtmap = readelf(['-p', '.fnvnx.vtmap'], o)
            check('[%s] %s: .fnvnx.vtmap has a line per defined table' % (label, u), all(t in vtmap for t in tabs if t.startswith('_ZTV')), tabs)
        # the link of the two: no undefined and no duplicate ap symbol
        r = subprocess.run(['aarch64-linux-gnu-ld', '-m', 'aarch64linux32', '-r', '-o', os.path.join(os.path.dirname(objs['a']), 'link-%s.o' % label.split()[0]), objs['a'], objs['b']],
                           capture_output=True, text=True)
        out = os.path.join(os.path.dirname(objs['a']), 'link-%s.o' % label.split()[0])
        ls = syms(out) if r.returncode == 0 else {}
        check('[%s] the link of the two units: ld -r succeeds (no duplicate symbol)' % label, r.returncode == 0, r.stderr)
        check('[%s] the link of the two units: no undefined address point' % label, r.returncode == 0 and not [n for n in ls if '.fnvnx_ap' in n and ls[n]['ndx'] == 'UND'])
        k = syms(objs['a'])
        check('[%s] key-function class: its table and ap are defined in the unit of the key function' % label, '_ZTV1K' in k and '_ZTV1K.fnvnx_ap8' in k and k['_ZTV1K.fnvnx_ap8']['ndx'] != 'UND')
        check('[%s] virtual-base class: every sub-table has an address point (>= 2)' % label, len([n for n in k if n.startswith('_ZTV1D.fnvnx_ap')]) >= 2, [n for n in k if n.startswith('_ZTV1D')])


if __name__ == '__main__':
    main()
    print('FAILED: %s' % FAILED if FAILED else 'all passed')
    sys.exit(1 if FAILED else 0)
