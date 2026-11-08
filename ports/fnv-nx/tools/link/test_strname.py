#!/usr/bin/env python3
"""A `str_<addr>` name binds to the image address in its name only where the synthetic exe holds a C string (S2-bodies
top-down rows 10 / 11), on hand-written units and a synthetic PE (no game content).

    python3 tools/link/test_strname.py     # needs aarch64-linux-gnu-{g++,ld,objcopy}, llvm-undname, c++filt

* src/use.cpp declares four strings in a unit namespace (`extern const char str_00fdf010[]` ...) and reads them;
  units.json has no symbol for any of them (MSVC emitted no object that names them);
* the synthetic exe has a printable string after a NUL at 0x00fdf010 (bound), binary bytes at 0x00fdf030 (not a
  string: stays unresolved), the middle of a string at 0x00fdf015 (stays unresolved), and zero padding at 0x00fdf0f0
  (an empty string: stays unresolved);
* without an exe the name is no evidence either: all four stay unresolved.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_exevft  # noqa: E402

FNVLINK = os.path.join(HERE, 'fnvlink.py')
CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
SRC = ('namespace fnv_00aaaaaa {\n'
       'extern const char str_00fdf010[];\nextern const char str_00fdf015[];\n'
       'extern const char str_00fdf030[];\nextern const char str_00fdf0f0[];\n'
       'int use(int i) { return str_00fdf010[i] + str_00fdf015[i] + str_00fdf030[i] + str_00fdf0f0[i]; }\n}\n')
UNITS = {'units': [{'source': 'src/use.cpp', 'compiler': 'sp1', 'flags': [],
                    'functions': [{'address': '0x00504000', 'symbol': '?use@fnv_00aaaaaa@@YAHH@Z', 'size': 16}],
                    'symbols': {}}]}
NAMES = ['str_00fdf010', 'str_00fdf015', 'str_00fdf030', 'str_00fdf0f0']
ok = True


def check(cond, what):
    global ok
    if not cond:
        ok = False
        print('FAIL', what)
    return cond


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit('FAILED: %s\n%s%s' % (' '.join(cmd), r.stdout, r.stderr))
    return r.stdout


def refs_of(out):
    got = {}
    for line in open(os.path.join(out, 'refs.tsv')):
        f = line.rstrip('\n').split('\t')
        if not line.startswith('#') and len(f) >= 3:
            for n in NAMES:
                if f[1].endswith(n + 'E'):
                    got[n] = f
    return got


def main():
    with tempfile.TemporaryDirectory() as t:
        tree = os.path.join(t, 'tree')
        os.makedirs(os.path.join(tree, 'src'))
        os.makedirs(os.path.join(tree, 'build'))
        p = os.path.join(tree, 'src', 'use.cpp')
        open(p, 'w').write(SRC)
        obj = os.path.join(t, 'use.o')
        run([CXX, '-mabi=ilp32', '-O2', '-fPIC', '-c', p, '-o', obj])
        units = os.path.join(tree, 'build', 'units.json')
        json.dump(UNITS, open(units, 'w'))
        with open(os.path.join(t, 'objects.tsv'), 'w') as f:
            f.write('src/use.cpp\t%s\tok\n' % obj)

        def build(rd):
            rd.buf += b'\0' * 0x10                         # 0x00fdf000..0f: zeros
            rd.put(b'--- %s ---\0', 1)                     # 0x00fdf010
            rd.buf += b'\0' * (0x30 - len(rd.buf))
            rd.put(b'ab\xf8\x80cd\0', 1)                   # 0x00fdf030: binary
        exe = os.path.join(t, 'S.exe')
        test_exevft.make_pe(exe, build)
        for label, extra, want in (('with-exe', ['--exe', exe], {'str_00fdf010'}),
                                   ('no-exe', ['--exe', 'none'], set())):
            out = os.path.join(t, 'out_' + label)
            run([sys.executable, FNVLINK, 'resolve', units, os.path.join(t, 'objects.tsv'), out,
                 '--crt-symbols', 'none', '--imports', 'none'] + extra)
            refs = refs_of(out)
            check(set(refs) == set(NAMES), '%s: the four names are referenced: %s' % (label, sorted(refs)))
            bound = {n for n, r in refs.items() if r[2] != 'unresolved'}
            check(bound == want, '%s: bound %s, expected %s' % (label, sorted(bound), sorted(want)))
            if want:
                r = refs['str_00fdf010']
                check(r[3] == 'name-addr/str' and '00fdf010' in '\t'.join(r),
                      '%s: bound by address 0x00fdf010 (name-addr/str): %s' % (label, r))
    print('ok' if ok else 'FAILED')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
