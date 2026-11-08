#!/usr/bin/env python3
"""W48 (R2, vtgate 4264bfd7): a `??_G` recipe name no object defines, bound through the unit's `// @fnv 0x<a>` tag.

units.json names the scalar deleting destructor `??_GC@@UAEPAXI@Z` at an address, but the unit's source defines the
code there as a plain function taking the flags (`C *C::FUN_00e22500(unsigned char)`, a free
`FUN_00431980(void *, unsigned)`, a member `C::Destroy(unsigned char)`). resolve binds the tagged definition to the
address (tag_flags_defs), so __fnv_<a> exists and the destructor slots get the flags stubs (vmo_dtor_slots). Not
bound: a constructor spelled at the ??_G address (no body: a D0 to synthesize), a tagged function without the flags
shape, an address tagged twice, and a definition another address already binds. Hand-written units (no decomp content).

    python3 tools/link/test_tagflags.py     # needs aarch64-linux-gnu-g++, llvm-undname, c++filt
"""
import collections
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fnvlink   # noqa: E402

FNVLINK = os.path.join(HERE, 'fnvlink.py')
CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
FAILS = []

SRC = {
    'src/m.cpp': 'extern "C" void Deallocate(void *);\n'
                 'struct M { int x; virtual ~M() {} M *FUN_00401000(unsigned char f); };\n'
                 '// @fnv 0x00401000\n'
                 'M *M::FUN_00401000(unsigned char f) { this->M::~M(); if (f & 1) Deallocate(this); return this; }\n'
                 'struct N { virtual ~N(); N(); };\n'
                 '// @fnv 0x00401100\n'
                 'N::N() {}\n'
                 'struct Q { virtual ~Q() {} Q *Destroy(unsigned char f); };\n'
                 '// Destroys the object.\n'
                 '// @fnv 0x00401300\n'
                 'Q *Q::Destroy(unsigned char f) { this->Q::~Q(); if (f & 1) Deallocate(this); return this; }\n'
                 '// @fnv 0x00401300\n'
                 'void Q_again(Q *q) { q->Destroy(0); }\n',
    'src/f.cpp': 'extern "C" void Deallocate(void *);\n'
                 'struct D { virtual ~D(); };\n'
                 '// @fnv 0x00401200\n'
                 'void *FUN_00401200(void *p, unsigned int flags) { if (flags & 1) Deallocate(p); return p; }\n'
                 'struct R { virtual ~R(); };\n'
                 '// @fnv 0x00401400\n'
                 'void DeleteR(int *r) { Deallocate(r); }\n'
                 'struct E { virtual ~E() {} E *FUN_00401500(unsigned char f); };\n'
                 '// @fnv 0x00401500\n'
                 'E *E::FUN_00401500(unsigned char f) { if (f & 1) Deallocate(this); return this; }\n',
}


def F(a, sym):
    return {'address': '0x%08x' % a, 'symbol': sym, 'size': 16}


UNITS = {'units': [
    {'source': 'src/m.cpp', 'compiler': 'x', 'flags': [], 'symbols': {}, 'functions': [
        F(0x00401000, '??_GM@@UAEPAXI@Z'),           # bound: member (this, unsigned char)
        F(0x00401100, '??_GN@@UAEPAXI@Z'),           # a constructor's spelling at the ??_G: no body
        F(0x00401300, '??_GQ@@UAEPAXI@Z')]},         # tagged twice: not bound
    {'source': 'src/f.cpp', 'compiler': 'x', 'flags': [], 'symbols': {}, 'functions': [
        F(0x00401200, '??_GD@@UAEPAXI@Z'),           # bound: free (void *, unsigned)
        F(0x00401400, '??_GR@@UAEPAXI@Z'),           # tagged function without the flags shape
        F(0x00401600, '?FUN_00401500@E@@QAEPAU1@E@Z'),   # binds E::FUN_00401500 by its own name first
        F(0x00401500, '??_GE@@UAEPAXI@Z')]},         # the tag names a definition already bound: not again
]}


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit('FAILED: %s\n%s%s' % (' '.join(cmd), r.stdout, r.stderr))
    return r.stdout + r.stderr


def main():
    if not (shutil.which(CXX) and shutil.which(fnvlink.UNDNAME) and shutil.which(fnvlink.CXXFILT)):
        print('skip: needs %s, %s and %s' % (CXX, fnvlink.UNDNAME, fnvlink.CXXFILT))
        return 0
    tn = fnvlink.tag_names('// @fnv 0x00e22500\nFNVNX_KEEP C *C::FUN_00e22500(unsigned char f)\n{\n'
                           '// note\n// @fnv 0x00431980\n\nFNVNX_KEEP void FUN_00431980(void *p, unsigned f) {}\n'
                           '// @fnv 0x00401000\nC::C() {}\n')
    check(tn == {0x00e22500: ['C::FUN_00e22500'], 0x00431980: ['FUN_00431980'], 0x00401000: ['C::C']},
          'tag_names: the qualified name after each tag: %s' % dict(tn))
    with tempfile.TemporaryDirectory() as t:
        tree = os.path.join(t, 'tree')
        os.makedirs(os.path.join(tree, 'build'))
        objs = {}
        for rel, text in SRC.items():
            p = os.path.join(tree, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, 'w').write(text)
            objs[rel] = os.path.join(t, rel.replace('/', '_') + '.o')
            run([CXX, '-mabi=ilp32', '-O2', '-fPIC', '-c', p, '-o', objs[rel]])
        units = os.path.join(tree, 'build', 'units.json')
        json.dump(UNITS, open(units, 'w'))
        with open(os.path.join(t, 'objects.tsv'), 'w') as f:
            for rel in SRC:
                f.write('%s\t%s\tok\n' % (rel, objs[rel]))
        out = os.path.join(t, 'out')
        log = run([sys.executable, FNVLINK, 'resolve', units, os.path.join(t, 'objects.tsv'), out,
                   '--crt-symbols', 'none', '--imports', 'none', '--units-renames', 'none'])
        rows = {int(r[0], 16): r for r in (l.rstrip('\n').split('\t') for l in open(os.path.join(out, 'addrmap.tsv'))
                                           if not l.startswith('#')) if len(r) > 3}
        nat = lambda a: rows.get(a, [''] * 4)[1:4]   # noqa: E731
        check(nat(0x00401000) == ['G', '__fnv_00401000', '_ZN1M12FUN_00401000Eh'],
              '??_GM -> the tagged member M::FUN_00401000(unsigned char), a G entry: %s' % nat(0x00401000))
        check(nat(0x00401200) == ['G', '__fnv_00401200', '_Z12FUN_00401200Pvj'],
              '??_GD -> the tagged free FUN_00401200(void *, unsigned): %s' % nat(0x00401200))
        check(0x00401100 not in rows, 'a constructor spelled at the ??_G address is not bound: %s' % rows.get(0x00401100))
        check(0x00401300 not in rows, 'an address tagged twice is not bound: %s' % rows.get(0x00401300))
        check(0x00401400 not in rows, 'a tagged definition without the flags shape is not bound')
        check(nat(0x00401600)[2] == '_ZN1E12FUN_00401500Eh' and 0x00401500 not in rows,
              'a definition bound by its own name is not bound a second time: %s / %s'
              % (nat(0x00401600), rows.get(0x00401500)))
        names = collections.Counter(r[3] for r in rows.values() if r[1] in ('F', 'G'))
        check(not [n for n, c in names.items() if c > 1], 'no native symbol bound to two addresses')
        check('tag-flags' in log, 'resolve logs the tag-flags census')

        # the destructor slots of the bound ??_G: D1 / D0 through the flags stubs into __fnv_<a>
        nd = dict(unit='src/m.cpp', sym=fnvlink.elfsyms.Sym('_ZN1M12FUN_00401000Eh', 0, 0, 'func', 'global', 1,
                                                             '.text', False),
                  variants={}, tier='tag-flags', msvc='??_GM@@UAEPAXI@Z')
        bridges, traps, st = {}, {}, collections.Counter()
        pair = fnvlink.vmo_dtor_slots(0x00401000, {0x00401000: nd}, lambda n: None, bridges, traps, st, 'w')
        check(pair == (fnvlink.FLAG_SYM % (0x00401000, 0), fnvlink.FLAG_SYM % (0x00401000, 1)) and
              'mov w1, #1' in bridges.get(fnvlink.FLAG_SYM % (0x00401000, 1), '') and
              'b __fnv_00401000' in bridges.get(fnvlink.FLAG_SYM % (0x00401000, 1), ''),
              'vmo_dtor_slots: D1 / D0 are the flags-0 / flags-1 stubs into __fnv_00401000: %s' % (pair,))
        local = dict(nd, msvc='??_GFUN_0048ef40_shell@?1??FUN_0048ef60@@YAPAXPAXI@Z@UAEPAXI@Z')
        check(fnvlink.deleting_native(local, lambda n: None),
              'a tag-flags definition is a deleting native even when undname gives its ??_G no class')
    print('%d failed' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
