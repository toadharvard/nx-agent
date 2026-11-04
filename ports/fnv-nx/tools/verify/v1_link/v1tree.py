"""v1tree.py - V1-link: a hand-made prepared tree of a few units, compiled for ILP32 and run through the code under
test (`tools/link/fnvlink.py resolve`), with the bridges it generates executed by bridge_harness.

    t = Tree(work); t.unit('src/a.cpp', text, functions=[(addr, msvc)], symbols={msvc: addr}); t.header(...)
    out = t.resolve()                  # -> out dir; t.refs(unit) rows, t.abi rows, t.bridges {name: asm}
    t.run_bridge(target, ref_msvc, def_msvc, layouts) -> harness status

No decomp content: callers write the units.
"""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bridge_harness as bh  # noqa: E402
import qemu_ilp32            # noqa: E402

LINK = os.path.join(HERE, '..', '..', 'link', 'fnvlink.py')


class Tree:
    def __init__(self, work):
        self.work = os.path.abspath(work)
        if os.path.isdir(self.work):
            shutil.rmtree(self.work)
        self.tree = os.path.join(self.work, 'tree')
        os.makedirs(os.path.join(self.tree, 'build'))
        self.units = []
        self.texts = {}

    def header(self, rel, text):
        p = os.path.join(self.tree, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'w').write(text)

    def unit(self, rel, text, functions=(), symbols=None):
        self.header(rel, text)
        self.units.append({'source': rel, 'compiler': 'sp1', 'flags': [],
                           'functions': [{'address': '0x%08x' % a, 'symbol': s, 'size': 16} for a, s in functions],
                           'symbols': {s: '0x%08x' % a for s, a in (symbols or {}).items()}})

    def resolve(self):
        objs = os.path.join(self.work, 'obj')
        os.makedirs(objs)
        rows = []
        for u in self.units:
            src = os.path.join(self.tree, u['source'])
            o = os.path.join(objs, u['source'].replace('/', '_') + '.o')
            r = subprocess.run([qemu_ilp32.CXX, '-mabi=ilp32', '-O2', '-fPIC', '-fno-inline', '-c', src, '-o', o,
                                '-I' + os.path.dirname(src)], capture_output=True, text=True)
            if r.returncode:
                raise RuntimeError('compile %s:\n%s' % (u['source'], r.stderr[-3000:]))
            rows.append('%s\t%s\tok' % (u['source'], o))
        uj = os.path.join(self.tree, 'build', 'units.json')
        json.dump({'units': self.units}, open(uj, 'w'))
        otsv = os.path.join(objs, 'objects.tsv')
        open(otsv, 'w').write('\n'.join(rows) + '\n')
        self.out = os.path.join(self.work, 'out')
        r = subprocess.run([sys.executable, LINK, 'resolve', uj, otsv, self.out, '--crt-symbols', 'none',
                            '--imports', 'none', '--units-renames', 'none'], capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError('resolve:\n' + (r.stdout + r.stderr)[-3000:])
        self.refs = []
        for line in open(os.path.join(self.out, 'refs.tsv')):
            if not line.startswith('#'):
                self.refs.append(line.rstrip('\n').split('\t'))
        self.abi = []
        for line in open(os.path.join(self.out, 'abi.tsv')):
            if not line.startswith('#'):
                self.abi.append(line.rstrip('\n').split('\t'))
        self.bridges = {}
        cur, name = [], None
        for line in open(os.path.join(self.out, 'bridges.S')):
            m = re.match(r'^(__fnvnx_br_[0-9a-f]{8}_[0-9a-f]{8}):', line)
            if m:
                name, cur = m.group(1), [line]
            elif name:
                if line.strip().startswith('.size'):
                    self.bridges[name] = '    .globl %s\n' % name + ''.join(cur)
                    name = None
                else:
                    cur.append(line)
        return self.out

    def ref_rows(self, addr):
        return [r for r in self.refs if len(r) > 5 and r[4] == '%08x' % addr]

    def abi_rows(self, addr):
        return [r for r in self.abi if len(r) > 5 and r[2] == '%08x' % addr]

    def run_bridge(self, target, ref_msvc, def_msvc, layouts=None, seeds=(1, 2, 3)):
        """target: the symbol refs.tsv binds to (bridge or __fnv_); def_msvc: a C++ decoration with the
        definition's real declaration (for C names: what the source says)."""
        asm = self.bridges.get(target) if target.startswith('__fnvnx_br_') else None
        dem = bh.undname([ref_msvc, def_msvc])
        lays = {k: bh.Lay(*v) for k, v in (layouts or {}).items()}
        for seed in seeds:
            st, notes = bh.run_case(ref_msvc, def_msvc, lays, lays, asm,
                                    os.path.join(self.work, 'run_' + target[-8:]), seed=seed, dem=dem)
            if st != 'pass':
                return st, notes
        return 'pass', notes
