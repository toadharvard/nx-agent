#!/usr/bin/env python3
"""End-to-end test of S2c-vt's slot fill (fnvlink resolve --exe; docs/LINK.md "Vftables from the player's exe"):
hand-written classes and a synthetic exe (test_exevft.make_pe), no game content.

    python3 tools/link/test_slotfill.py     # needs aarch64-linux-gnu-{g++,as,ld,objcopy}, llvm-undname, SSE2NEON

Vb has a virtual destructor and the virtuals a, b, c, d, f, e; only a (and the destructor) is defined under its own
name. The exe's vftable of Vb holds, after the deleting destructor and a: b's body, which the decomp defines as
Shell::impl() (compiled, same arguments); c's body, Shell::impl2(float) (its argument arrives in another register
file than c(int) passes it); f's body, Shell::impl3(int, int) (reads more arguments than the placeholder f()
declares: callers reach the slot through their own declarations, so it is bound and listed); d's body at an address no compiled unit defines; and no slot for e. Der : Vb overrides a and inherits the
rest; its exe vftable holds the same bodies. Checks:
* b is bound to the native definition at the exe slot's address (`__fnv_00401020`, class vslot-exe) in both
  vtables of the object (Vb's and Der's agree), and its trap stub is gone;
* f is bound too (`the definition reads more arguments`); c, d and e stay traps, listed in slot_gaps.tsv with their
  reasons (abi, not compiled, past the exe's slots);
* GCC's destructor pair counts as one MSVC slot (the minus-one rule: b is GCC slot 3, exe slot 2);
* after apply the fixed object's vtable slot names `__fnv_00401020`, the trial link leaves it defined, and the
  stage without the exe (`--exe none`) keeps b a trap;
* vtmetric.py counts what is left: trap slots 6 (c, d, e in both vtables), 2 of them with a native at the exe slot
  (c: the abi gap).
"""
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import layout          # noqa: E402
import test_exevft     # noqa: E402
import vtables         # noqa: E402
import vtmetric        # noqa: E402

FNVLINK = os.path.join(HERE, 'fnvlink.py')
CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
FAILS = []

HEADER = r'''#pragma once
struct Vb { virtual ~Vb(); virtual int a(); virtual int b(); virtual int c(int); virtual int d(); virtual int f();
            virtual int e(); int x; };
struct Der : Vb { int a() override; };
struct Shell { int impl(); int impl2(float); int impl3(int, int); int y; };
'''
SRC = {'src/a.cpp': '#include "h.h"\nVb::~Vb() {}\nint Vb::a() { return 1; }\nint Der::a() { return 2; }\n'
                    'int Shell::impl() { return y; }\nint Shell::impl2(float p) { return (int)p; }\n'
                    'int Shell::impl3(int p, int q) { return p + q; }\n'
                    'Vb *mk_vb() { return new Vb; }\nDer *mk_der() { return new Der; }\n'}
F = lambda a, s: {'address': '0x%08x' % a, 'symbol': s, 'size': 8}   # noqa: E731


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)
    return cond


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        raise SystemExit('FAILED: %s\n%s%s' % (' '.join(cmd), r.stdout, r.stderr))
    return r.stdout


def build_exe(rd, where):
    td = {n: rd.put(struct.pack('<II', 0x0101d000, 0) + n.encode() + b'\0') for n in ('.?AVVb@@', '.?AVDer@@')}

    def chd(names):
        bs = [rd.words(td[n], 0, 0, 0xffffffff, 0, 0x40, 0) for n in names]
        return rd.words(0, 0, len(bs), rd.words(*bs))

    def vft(cls, h, slots):
        col = rd.words(0, 0, 0, td[cls], h)
        rd.words(col)
        v = rd.here()
        rd.words(*slots)
        return v
    # MSVC order: ??_G, a, b, c, d, f (e is missing: the exe vftable is shorter)
    where['Vb'] = vft('.?AVVb@@', chd(['.?AVVb@@']), [0x401008, 0x401010, 0x401020, 0x401030, 0x401060, 0x401038])
    where['Der'] = vft('.?AVDer@@', chd(['.?AVDer@@', '.?AVVb@@']),
                       [0x401050, 0x401040, 0x401020, 0x401030, 0x401060, 0x401038])
    rd.words(0)


def main():
    with tempfile.TemporaryDirectory() as t:
        exe = os.path.join(t, 'Synthetic.exe')
        where = {}
        test_exevft.make_pe(exe, lambda rd: build_exe(rd, where))
        tree = os.path.join(t, 'tree')
        os.makedirs(os.path.join(tree, 'include'))
        open(os.path.join(tree, 'include', 'h.h'), 'w').write(HEADER)
        fl = [f if f != '-O2' else '-O1' for f in layout.flags(tree)] + ['-fno-inline']
        objs = {}
        for rel, text in SRC.items():
            p = os.path.join(tree, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, 'w').write(text)
            objs[rel] = os.path.join(t, rel.replace('/', '_') + '.o')
            run([CXX, *fl, p, '-o', objs[rel]], cwd=tree)
        units = {'units': [
            {'source': 'src/a.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
                F(0x401000, '??1Vb@@UAE@XZ'), F(0x401008, '??_GVb@@UAEPAXI@Z'), F(0x401010, '?a@Vb@@UAEHXZ'),
                F(0x401020, '?impl@Shell@@QAEHXZ'), F(0x401030, '?impl2@Shell@@QAEHM@Z'), F(0x401038, '?impl3@Shell@@QAEHHH@Z'),
                F(0x401040, '?a@Der@@UAEHXZ'), F(0x401070, '?mk_vb@@YAPAUVb@@XZ'), F(0x401078, '?mk_der@@YAPAUDer@@XZ')],
             'symbols': {'??_7Vb@@6B@': '0x%08x' % where['Vb'], '??_7Der@@6B@': '0x%08x' % where['Der']}},
            {'source': 'src/d.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [   # not compiled: d's body
                F(0x401060, '?dbody@Shell@@QAEHXZ')], 'symbols': {}},
        ]}
        import json
        json.dump(units, open(os.path.join(t, 'units.json'), 'w'))
        with open(os.path.join(t, 'objects.tsv'), 'w') as f:
            for rel in SRC:
                f.write('%s\t%s\tok\n' % (rel, objs[rel]))
        res = {}
        for name, exe_arg in (('none', 'none'), ('exe', exe)):
            out, fixed = os.path.join(t, 'out-' + name), os.path.join(t, name, 'fixed')
            run([sys.executable, FNVLINK, 'resolve', os.path.join(t, 'units.json'), os.path.join(t, 'objects.tsv'),
                 out, '--crt-symbols', 'none', '--imports', 'none', '--exe', exe_arg])
            run([sys.executable, FNVLINK, 'apply', out, fixed])
            chk = run([sys.executable, FNVLINK, 'check', out, fixed])
            refs = {}
            for line in open(os.path.join(out, 'refs.tsv')):
                f = line.rstrip('\n').split('\t')
                if not line.startswith('#'):
                    refs[f[1]] = f
            res[name] = (out, fixed, refs, chk)
        out, fixed, refs, chk = res['exe']
        b = refs.get('_ZN2Vb1bEv')
        check(b is not None and b[2] == 'vslot-exe' and b[5] == '__fnv_00401020',
              'b bound to the native definition at the exe slot: %s' % (b,))
        nb = res['none'][2].get('_ZN2Vb1bEv')
        check(nb is not None and nb[2] == 'vslot-unresolved', 'without the exe b stays a trap: %s' % (nb,))
        for n in ('_ZN2Vb1cEi', '_ZN2Vb1dEv', '_ZN2Vb1eEv'):
            r = refs.get(n)
            check(r is not None and r[2] == 'vslot-unresolved', '%s stays a trap: %s' % (n, r))
        gaps = [l.rstrip('\n').split('\t') for l in open(os.path.join(out, 'slot_gaps.tsv')) if not l.startswith('#')]
        why = {}
        for g in gaps:
            why.setdefault(g[2], set()).add(g[7].split(' (')[0])
        check(why.get('_ZN2Vb1cEi') == {'abi: argument 1 differs'}, 'c: abi gap %s' % why.get('_ZN2Vb1cEi'))
        check(why.get('_ZN2Vb1dEv') == {'not compiled'}, 'd: not compiled %s' % why.get('_ZN2Vb1dEv'))
        check(why.get('_ZN2Vb1eEv') == {'past the exe vftable\'s slots'}, 'e: past the slots %s' % why.get('_ZN2Vb1eEv'))
        fill = [l.rstrip('\n').split('\t') for l in open(os.path.join(out, 'slot_fill.tsv')) if not l.startswith('#')]
        fb = {r[2]: r for r in fill}
        check(set(fb) == {'_ZN2Vb1bEv', '_ZN2Vb1fEv'} and fb['_ZN2Vb1bEv'][5] == 'agree' and
              fb['_ZN2Vb1bEv'][6] == 'same' and '_ZTV2Vb+8:3' in fb['_ZN2Vb1bEv'][7] and
              '_ZTV3Der+8:3' in fb['_ZN2Vb1bEv'][7], 'slot_fill.tsv: b in both vtables, GCC slot 3 (exe slot 2): %s'
              % fill)
        check('_ZN2Vb1fEv' in fb and fb['_ZN2Vb1fEv'][4] == '__fnv_00401038' and
              fb['_ZN2Vb1fEv'][6].startswith('the definition reads more arguments'),
              'f (a placeholder declaration of a body with two arguments) bound and listed: %s' % fb.get('_ZN2Vb1fEv'))
        o = vtables.Obj(os.path.join(fixed, 'src_a.cpp.o'))
        ents = o.ents('_ZTV2Vb')
        check(ents[2 + 3].sym == '__fnv_00401020', 'fixed object: Vb vtable slot 3 names __fnv_00401020 (%s)'
              % ents[2 + 3].sym)
        check(ents[2 + 4].sym.startswith('__fnvnx_vtrap_'), 'fixed object: slot 4 (c) still a trap (%s)' % ents[2 + 4].sym)
        bridges = open(os.path.join(out, 'bridges.S')).read()
        check(nb[5] not in bridges and refs['_ZN2Vb1cEi'][5] in bridges, 'b\'s trap stub dropped, c\'s kept')
        check('__fnv_00401020' not in chk, 'trial link: __fnv_00401020 defined')
        st = vtmetric.measure(_link_dir(t, out, fixed), exe)
        check(st['trap-slots'] == 6 and st['trap-native-at-exe'] == 2,
              'vtmetric: 6 trap slots left, 2 with a native at the exe slot (%s)' % dict(st))
        st0 = vtmetric.measure(_link_dir(t, res['none'][0], res['none'][1]), exe)
        check(st0['trap-slots'] == 10 and st0['trap-native-at-exe'] == 6, 'vtmetric without the fill: 10 / 6 (%s)'
              % dict(st0))
    print('%d failed' % len(FAILS))
    return 1 if FAILS else 0


def _link_dir(t, out, fixed):
    """vtmetric reads <dir>/out and <dir>/fixed (a published link's layout)."""
    d = os.path.join(t, 'link-' + os.path.basename(out))
    os.makedirs(d, exist_ok=True)
    for n, p in (('out', out), ('fixed', fixed)):
        if not os.path.lexists(os.path.join(d, n)):
            os.symlink(p, os.path.join(d, n))
    return d


if __name__ == '__main__':
    sys.exit(main())
