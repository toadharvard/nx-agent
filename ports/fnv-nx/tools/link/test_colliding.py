#!/usr/bin/env python3
"""A global FUN_<addr> defined by an unrelated body binds by address, never by name (S5 s5-003 class 2), on
hand-written units (no decomp content).

    python3 tools/link/test_colliding.py     # needs aarch64-linux-gnu-{g++,ld,objcopy}, llvm-undname, c++filt

* src/nat.cpp defines the native at 0x00501000 (a static member, `Fmt::FUN_00501000`: the same native argument
  shape as the `(int)` reference, so the name-addr ABI check finds nothing to flag; a thiscall member would get
  code-manual, f in x0 where the member expects this);
* src/col.cpp keeps an unrelated noinline helper under the global name `FUN_00501000(int)` and calls it; a variant
  unit (src/col_v.cpp) defines the same helper again;
* src/use.cpp references `FUN_00501000(int)` with no binding in its recipe (the plain linker would bind it by name);
* the controls: src/ok.cpp's global `FUN_00502000(int)` is the native at its address; its `FUN_00505000(int)`
  names an address no unit places (no native): both are referenced from use.cpp and are not this class.

Checks: colliding.tsv lists the helper only; the defining objects' definitions become references renamed to
__fnv_00501000 (.undefine / .redefine, not localized); use.cpp's reference is code at 0x00501000; after apply and
the trial link every call reaches the native; the controls bind as before (by recipe, and by name).
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vtables  # noqa: E402

FNVLINK = os.path.join(HERE, 'fnvlink.py')
CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
HELPER = '__attribute__((noinline)) int FUN_00501000(int f) { return f == 1 ? 8 : 16; }\n'
SRC = {
    'src/nat.cpp': 'struct Fmt { static int FUN_00501000(int); static int b; };\nint Fmt::b = 3;\n'
                   'int Fmt::FUN_00501000(int f) { return b * f; }\n',
    'src/col.cpp': HELPER + 'int blocks(int f) { return FUN_00501000(f) + 1; }\n',
    'src/col_v.cpp': HELPER + 'int blocks_v(int f) { return FUN_00501000(f) + 2; }\n',
    'src/use.cpp': 'int FUN_00501000(int);\nint FUN_00502000(int);\nint FUN_00505000(int);\n'
                   'int use(int f) { return FUN_00501000(f) + FUN_00502000(f) + FUN_00505000(f); }\n',
    'src/ok.cpp': 'int FUN_00502000(int f) { return f + 3; }\nint FUN_00505000(int f) { return f + 4; }\n',
}
F = lambda a, s: {'address': '0x%08x' % a, 'symbol': s, 'size': 16}   # noqa: E731
UNITS = {'units': [
    {'source': 'src/nat.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [F(0x501000, '?FUN_00501000@Fmt@@SAHH@Z')],
     'symbols': {}},
    {'source': 'src/col.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [F(0x503000, '?blocks@@YAHH@Z')],
     'symbols': {}},
    {'source': 'src/col_v.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [F(0x503100, '?blocks_v@@YAHH@Z')],
     'symbols': {}},
    {'source': 'src/use.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [F(0x504000, '?use@@YAHH@Z')],
     'symbols': {}},
    {'source': 'src/ok.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [F(0x502000, '?FUN_00502000@@YAHH@Z')],
     'symbols': {}},
]}
ok = True


def check(cond, what):
    global ok
    if not cond:
        ok = False
        print('FAIL', what)
    return cond


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        raise SystemExit('FAILED: %s\n%s%s' % (' '.join(cmd), r.stdout, r.stderr))
    return r.stdout


def reloc_targets(path, func):
    o = vtables.Obj(path)
    s = [x for x in o.syms if x[0] == func and x[5]][0]
    return {o.syms[si][0] for off, (si, _, _) in o.rel(s[5]).items() if s[1] <= off < s[1] + s[2]}


def main():
    with tempfile.TemporaryDirectory() as t:
        tree = os.path.join(t, 'tree')
        os.makedirs(os.path.join(tree, 'build'))
        objs = {}
        for rel, text in SRC.items():
            p = os.path.join(tree, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, 'w').write(text)
            objs[rel] = os.path.join(t, rel.replace('/', '_') + '.o')
            run([CXX, '-mabi=ilp32', '-O2', '-fPIC', '-c', p, '-o', objs[rel]])   # like compile.sh
        check('_Z12FUN_00501000i' in reloc_targets(objs['src/col.cpp'], '_Z6blocksi'),
              'fixture: col.cpp calls its own helper by name')
        units = os.path.join(tree, 'build', 'units.json')
        json.dump(UNITS, open(units, 'w'))
        with open(os.path.join(t, 'objects.tsv'), 'w') as f:
            for rel in SRC:
                f.write('%s\t%s\tok\n' % (rel, objs[rel]))
        out, fixed = os.path.join(t, 'out'), os.path.join(t, 'fixed')
        run([sys.executable, FNVLINK, 'resolve', units, os.path.join(t, 'objects.tsv'), out, '--crt-symbols', 'none',
             '--imports', 'none'])
        col = [l.rstrip('\n').split('\t') for l in open(os.path.join(out, 'colliding.tsv')) if not l.startswith('#')]
        check([r[:3] for r in col] == [['_Z12FUN_00501000i', '00501000', '_ZN3Fmt12FUN_00501000Ei']] and
              set(col[0][3].split()) == {'src/col.cpp', 'src/col_v.cpp'} if col else False,
              'colliding.tsv: the helper only, both defining objects: %s' % col)
        refs = {(r[0], r[1]): r for r in (l.rstrip('\n').split('\t') for l in open(os.path.join(out, 'refs.tsv'))
                                          if not l.startswith('#'))}
        r_ = refs.get(('src/use.cpp', '_Z12FUN_00501000i'), [''] * 6)
        check(r_[2] == 'code' and r_[3] == 'name-addr/colliding' and r_[5] == '__fnv_00501000',
              'another unit\'s reference binds by address: %s' % r_)
        r_ = refs.get(('src/use.cpp', '_Z12FUN_00502000i'), [''] * 6)
        check(r_[2] == 'code' and r_[3] != 'name-addr/colliding', 'control: the native\'s own name: %s' % r_)
        r_ = refs.get(('src/use.cpp', '_Z12FUN_00505000i'), [''] * 6)
        check(r_[2] == 'native' and not r_[5], 'control: no native at the address, stays by name: %s' % r_)
        for rel in ('src/col.cpp', 'src/col_v.cpp'):
            base = os.path.join(out, 'objcopy', os.path.basename(objs[rel]))
            und = open(base + '.undefine').read().split()
            red = dict(l.split() for l in open(base + '.redefine') if l.strip())
            args = open(base + '.args').read()
            check(und == ['_Z12FUN_00501000i'] and red.get('_Z12FUN_00501000i') == '__fnv_00501000' and
                  'localize-symbol=_Z12FUN_00501000i' not in args,
                  '%s: definition made a reference to __fnv_00501000: %s %s' % (rel, und, red))
        run([sys.executable, FNVLINK, 'apply', out, fixed])
        run([sys.executable, FNVLINK, 'check', out, fixed])
        for rel, fn in (('src/col.cpp', '_Z6blocksi'), ('src/col_v.cpp', '_Z8blocks_vi'), ('src/use.cpp', '_Z3usei')):
            tg = reloc_targets(os.path.join(fixed, os.path.basename(objs[rel])), fn)
            check('__fnv_00501000' in tg and '_Z12FUN_00501000i' not in tg, '%s calls by address: %s' % (rel, tg))
        tg = reloc_targets(os.path.join(fixed, os.path.basename(objs['src/use.cpp'])), '_Z3usei')
        check('_Z12FUN_00505000i' in tg and '__fnv_00502000' in tg, 'the controls: %s' % tg)
        log = open(os.path.join(out, 'trial_link.log')).read()
        check('multiple definition' not in log and 'FUN_00501000' not in log, 'trial link: %s' % log[:2000])
        e = vtables.Obj(os.path.join(out, 'trial.elf'))
        sym = {s[0]: s for s in e.syms if s[0]}
        check(sym.get('__fnv_00501000', (0, -1))[1] == sym.get('_ZN3Fmt12FUN_00501000Ei', (0, -2))[1],
              '__fnv_00501000 is the native')
    print('ok' if ok else 'FAILED')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
