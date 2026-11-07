#!/usr/bin/env python3
"""tests of T1-fastbench's MSVC object cache (tools/equiv/unitcache.py through equiv.compile_unit with
FNVNX_EQUIV_CACHE): a stand-in for the decomp's tools/msvc.py "compiles" by concatenating the source and the headers it
includes (and reports them like cl.exe /showIncludes, as Wine paths); it counts its runs. No MSVC, no Wine.

    python3 -I tests/t1/test_equiv_cache.py
"""
import json, os, shutil, sys, tempfile, unittest
from pathlib import Path
HERE = os.path.dirname(os.path.abspath(__file__))
EQ = os.path.join(HERE, '..', '..', 'tools', 'equiv')
sys.path.insert(0, EQ)

FAKE_MSVC = r'''
import os, re, sys
from pathlib import Path
ROOTS = {'sp1': 'FNV_MSVC9_ROOT', 'rtm': 'FNV_MSVC9_RTM_ROOT', 'vc8': 'FNV_MSVC8_ROOT'}
def compiler_root(name):
    return Path(os.environ[ROOTS[name]])
if __name__ == '__main__':
    a = sys.argv[1:]
    open(os.environ['FAKE_RUNS'], 'a').write('run\n')
    inc = [x[2:] for x in a if x.startswith('/I')]
    src = [x for x in a if x.endswith('.cpp')][0]
    out = [x[3:] for x in a if x.startswith('/Fo')][0]
    show = '/showIncludes' in a
    body = []
    def read(p):
        t = open(p).read(); body.append(t)
        for h in re.findall(r'#include "([^"]+)"', t):
            for d in [os.path.dirname(p)] + inc:
                q = os.path.join(d, h)
                if os.path.isfile(q):
                    if show: print('Note: including file: Z:' + os.path.abspath(q).replace('/', '\\'))
                    read(q); break
            else:
                print('error C1083: %s' % h); sys.exit(2)
    read(src)
    if 'BROKEN' in ''.join(body):
        print('error C2000: broken'); sys.exit(2)
    open(out, 'w').write(''.join(body))
    print(os.path.basename(src))
'''


class EquivCache(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp(prefix='t1eq.'))
        (self.t / 'decomp/tools').mkdir(parents=True)
        (self.t / 'decomp/tools/msvc.py').write_text(FAKE_MSVC)
        for c in ('sp1', 'rtm'):
            (self.t / c / 'VC/bin').mkdir(parents=True); (self.t / c / 'SDK/Include').mkdir(parents=True)
            (self.t / c / 'VC/bin/cl.exe').write_text('cl ' + c)
        os.environ.update(FNV_MSVC9_ROOT=str(self.t / 'sp1'), FNV_MSVC9_RTM_ROOT=str(self.t / 'rtm'),
                          FAKE_RUNS=str(self.t / 'runs'), FNVNX_EQUIV_CACHE=str(self.t / 'cache'),
                          XDG_CACHE_HOME=str(self.t / 'xdg'))
        import equiv
        equiv._CACHES.clear()
        self.eq = equiv

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def tree(self, name, files):
        d = self.t / name
        for rel, text in files.items():
            (d / rel).parent.mkdir(parents=True, exist_ok=True); (d / rel).write_text(text)
        return d

    def runs(self):
        p = self.t / 'runs'
        return len(p.read_text().splitlines()) if p.exists() else 0

    def build(self, tree, src='src/a.cpp', **kw):
        obj = self.t / ('o%d.obj' % self.runs())
        unit = {'source': src, 'compiler': 'sp1', 'flags': ['/O2']}
        ok, text = self.eq.compile_unit(str(self.t / 'decomp'), tree, unit, obj, False, plain=True, **kw)
        return ok, text, obj.read_text() if obj.exists() else None

    def test_hit_miss_and_paths(self):
        files = {'src/a.cpp': '#include "h.h"\nint a;\n', 'include/h.h': '#include "g.h"\nint h;\n',
                 'include/g.h': 'int g;\n', 'include/unrelated.h': 'int u;\n'}
        t1 = self.tree('tree1', files)
        ok, text, o1 = self.build(t1)
        self.assertTrue(ok); self.assertEqual(self.runs(), 1); self.assertNotIn('including file', text)
        ok, text2, o2 = self.build(t1)
        self.assertEqual(self.runs(), 1); self.assertEqual(o2, o1); self.assertEqual(text2, text)
        # the same content at another path (the next head's tree): a hit
        t2 = self.tree('tree2', files)
        self.assertEqual(self.build(t2)[2], o1); self.assertEqual(self.runs(), 1)
        # an edit to a header it does not include: a hit; to one it includes (two levels down): a miss
        (t2 / 'include/unrelated.h').write_text('int u2;\n')
        self.build(t2); self.assertEqual(self.runs(), 1)
        (t2 / 'include/g.h').write_text('int g2;\n')
        ok, _, o3 = self.build(t2); self.assertEqual(self.runs(), 2); self.assertIn('g2', o3)
        # another variant of the same unit is its own entry
        self.build(t2, eh_off=True); self.assertEqual(self.runs(), 3)
        self.build(t2, eh_off=True); self.assertEqual(self.runs(), 3)
        # a header added on the include path: a miss (it could shadow)
        (t2 / 'include/new.h').write_text('int n;\n')
        self.build(t2); self.assertEqual(self.runs(), 4)

    def test_failure_not_cached_and_compiler_in_key(self):
        t1 = self.tree('tree1', {'src/a.cpp': 'BROKEN\n'})
        ok, text, _ = self.build(t1); self.assertFalse(ok)
        self.build(t1); self.assertEqual(self.runs(), 2)
        (t1 / 'src/a.cpp').write_text('int a;\n')
        self.build(t1); self.build(t1); self.assertEqual(self.runs(), 3)
        (self.t / 'sp1/VC/bin/cl.exe').write_text('cl sp1 v2')
        self.eq._CACHES.clear()
        self.build(t1); self.assertEqual(self.runs(), 4)


if __name__ == '__main__':
    unittest.main()
