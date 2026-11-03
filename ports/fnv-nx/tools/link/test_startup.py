#!/usr/bin/env python3
"""Link-step side of the startup design (docs/STARTUP.md) on hand-written units (no decomp content).

    python3 tools/link/test_startup.py     # needs aarch64-linux-gnu-{g++,ld,objcopy,nm}, llvm-undname

Unit g is what prepare rule XC1 makes of a generated initializer unit: extern "C" __fnvnx_dyninit_<E> /
__fnvnx_dynexit_<F> for the ??__E / ??__F at E and F (MSVC-only specials in units.json), and a function-local
static with a destructor (GCC registers it with __cxa_atexit). Unit h reaches F by address through a Ghidra-style
name. Checks: E and F are F entries of the address map whose native is the startup symbol; h's reference binds to
it; in the rewritten objects __cxa_atexit is fnvnx_xc_cxa_atexit (game objects only: an override object keeps it).
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FNVLINK = os.path.join(HERE, 'fnvlink.py')
CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
NM = os.environ.get('NM', 'aarch64-linux-gnu-nm')

SRC = {
    'src/generated/g.cpp': 'struct T { T(); ~T(); int v; };\nextern T g_x;\n'
                           'extern "C" void __fnvnx_dynexit_00401310(void) { g_x.~T(); }\n'
                           'extern "C" void fnvnx_xc_atexit(void (*)(void));\n'
                           'extern "C" void __fnvnx_dyninit_00401300(void) { fnvnx_xc_atexit(__fnvnx_dynexit_00401310); }\n'
                           'int counter() { static T t; return ++t.v; }\n',
    'src/h.cpp': 'extern "C" void FUN_00401310(void);\nvoid run_exit() { FUN_00401310(); }\n',
    'native/plat.cpp': 'struct P { ~P(); };\nint plat() { static P p; return 1; }\n',
}
UNITS = {'units': [
    {'source': 'src/generated/g.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        {'address': '0x00401300', 'symbol': '??__Eg_x@@YAXXZ', 'size': 16},
        {'address': '0x00401310', 'symbol': '??__Fg_x@@YAXXZ', 'size': 16},
        {'address': '0x00401320', 'symbol': '?counter@@YAHXZ', 'size': 16}],
     'symbols': {'?g_x@@3UT@@A': '0x01000400'}},
    {'source': 'src/h.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        {'address': '0x00401400', 'symbol': '?run_exit@@YAXXZ', 'size': 16}],
     'symbols': {'_FUN_00401310': '0x00401310'}},
]}

ok = True


def check(cond, what):
    global ok
    if not cond:
        ok = False
        print('FAIL', what)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit('FAILED: %s\n%s%s' % (' '.join(cmd), r.stdout, r.stderr))
    return r.stdout


def syms(path):
    out = {}
    for line in run([NM, path]).splitlines():
        f = line.split()
        out[f[-1]] = (f[-2], int(f[0], 16) if len(f) == 3 else None)
    return out


def main():
    with tempfile.TemporaryDirectory() as t:
        objs = {}
        for rel, text in SRC.items():
            p = os.path.join(t, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, 'w').write(text)
            objs[rel] = p[:-4] + '.o'
            run([CXX, '-mabi=ilp32', '-O1', '-c', p, '-o', objs[rel]])
        json.dump(UNITS, open(os.path.join(t, 'units.json'), 'w'))
        with open(os.path.join(t, 'objects.tsv'), 'w') as f:
            for rel in ('src/generated/g.cpp', 'src/h.cpp'):
                f.write('%s\t%s\tok\n' % (rel, objs[rel]))
        with open(os.path.join(t, 'override.tsv'), 'w') as f:    # a platform object, linked as an override
            f.write('00401500\t_Z4platv\t%s\n' % objs['native/plat.cpp'])
        out, fixed = os.path.join(t, 'out'), os.path.join(t, 'fixed')
        run([sys.executable, FNVLINK, 'resolve', os.path.join(t, 'units.json'), os.path.join(t, 'objects.tsv'), out,
             '--override', os.path.join(t, 'override.tsv')])
        rows = {int(r[0], 16): r for r in (l.rstrip('\n').split('\t') for l in open(os.path.join(out, 'addrmap.tsv'))
                                          if not l.startswith('#'))}
        for a, name in ((0x401300, '__fnvnx_dyninit_00401300'), (0x401310, '__fnvnx_dynexit_00401310')):
            r = rows.get(a)
            check(r is not None and r[1] == 'F' and r[2] == '__fnv_%08x' % a and r[3] == name, 'map row %s' % r)
        refs = [l.split('\t') for l in open(os.path.join(out, 'refs.tsv')) if not l.startswith('#')]
        check(any(r[0] == 'src/h.cpp' and r[1] == 'FUN_00401310' and r[2] == 'code' and r[5] == '__fnv_00401310'
                  for r in refs), 'by-address reference to the ??__F binds to the startup native')
        run([sys.executable, FNVLINK, 'apply', out, fixed])
        run([sys.executable, FNVLINK, 'check', out, fixed])
        g = syms(os.path.join(fixed, os.path.basename(objs['src/generated/g.cpp'])))
        check(g.get('fnvnx_xc_cxa_atexit', ('',))[0] == 'U' and '__cxa_atexit' not in g,
              'game object: __cxa_atexit renamed (%s)' % sorted(n for n in g if 'atexit' in n))
        p = syms(os.path.join(fixed, 'zz_override_' + os.path.basename(objs['native/plat.cpp'])))
        check(p.get('__cxa_atexit', ('',))[0] == 'U' and 'fnvnx_xc_cxa_atexit' not in p,
              'platform object keeps __cxa_atexit')
        e = syms(os.path.join(out, 'trial.elf'))
        for a, name in ((0x401300, '__fnvnx_dyninit_00401300'), (0x401310, '__fnvnx_dynexit_00401310')):
            check(e.get(name, (0, None))[1] is not None and e.get('__fnv_%08x' % a, (0, -1))[1] == e[name][1],
                  '__fnv_%08x == %s in the link' % (a, name))
    print('ok' if ok else 'FAILED')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
