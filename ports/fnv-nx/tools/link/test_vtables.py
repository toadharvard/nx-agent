#!/usr/bin/env python3
"""End-to-end test of the vtable side of the address map on hand-written classes (no decomp content).

    python3 tools/link/test_vtables.py     # needs aarch64-linux-gnu-{g++,as,ld,objcopy,objdump}, llvm-undname, SSE2NEON

Classes (header h.h): Base1 / Base2 (virtual destructors, Base2::g2 pure), Plain (no virtuals); Multi : Base1,
Plain, Base2 (key function in unit a, so GCC emits its group); Key : Plain, Base1, Base2 (key function Key::k is
defined nowhere: its vtable is never emitted, unit b references it); Inl : Base1, Base2 (inline virtuals only, never
constructed: no vtable anywhere); Abs2 : Base2 (abstract, inline virtual); Root (key function defined nowhere,
reached only through the typeinfo of Leaf, which unit c constructs). units.json names their primary and
`{for `B'}` secondary vftables and the scalar deleting destructors of Multi, Key and Base1.

Pipeline: resolve -> vtables (probe) -> resolve -> apply -> check. Checks:
* synthesized _ZTV/_ZTI of Key, Inl, Abs2, Root equal, word for word and relocation for relocation, the ones GCC
  emits in an oracle unit that defines Key::k and Root::r and constructs Inl and a subclass of Abs2;
* every V entry's offset is the address point GCC reports for that base-class subobject (-fdump-lang-class:
  `vptr=((& C::_ZTV...) + N)`, the class's own one for a primary base);
* in the trial link: fnvnx_addrmap[] holds _ZTV<C> + offset for each vftable, with offset-to-top = -(subobject offset)
  and the class's typeinfo before it; every G entry has {D1, D0} in fnvnx_addrmap_deldtors[] equal to the class's
  _ZN..D1Ev / _ZN..D0Ev, D0 also in fnvnx_addrmap[]; nothing but Key::k and Root::r (defined nowhere) and Inl's
  implicit destructors (emitted only where GCC emits its vtable) is left undefined; thunks adjust this and branch to
  their function (generated ones: w0, any offset).
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fnvlink  # noqa: E402
import layout   # noqa: E402
import vtables  # noqa: E402

FNVLINK = os.path.join(HERE, 'fnvlink.py')
CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
OBJDUMP = os.environ.get('OBJDUMP', 'aarch64-linux-gnu-objdump')

HEADER = r'''#pragma once
struct Plain { int p; };
struct Base1 { virtual ~Base1(); virtual int f1(); int a; };
struct Base2 { virtual ~Base2(); virtual int f2(); virtual int g2() = 0; int b; };
struct Multi : Base1, Plain, Base2 { ~Multi(); int f2() override; int g2() override; virtual int m(); int c; };
struct Key : Plain, Base1, Base2 { virtual int k(); ~Key(); int g2() override; int d; };
struct Inl : Base1, Base2 { int g2() override { return 7; } virtual int i() { return 8; } };
struct Abs2 : Base2 { virtual int pure() = 0; int f2() override { return 3; } };
struct Root { virtual int r(); };
struct Leaf : Root { int r() override { return 1; } };
'''
SRC = {
    'src/a.cpp': '#include "h.h"\nBase1::~Base1() {}\nint Base1::f1() { return 1; }\nBase2::~Base2() {}\n'
                 'int Base2::f2() { return 2; }\nMulti::~Multi() {}\nint Multi::f2() { return 3; }\n'
                 'int Multi::g2() { return 4; }\nint Multi::m() { return 5; }\n',
    'src/b.cpp': '#include "h.h"\nKey::~Key() {}\nint Key::g2() { return 6; }\nKey *mk() { return new Key; }\n',
    'src/c.cpp': '#include "h.h"\nint use_inl(Inl *p) { return p->Inl::g2() + p->Inl::i(); }\n'
                 'int use_abs(Abs2 *p) { return p->Abs2::f2(); }\nLeaf *mk_leaf() { return new Leaf; }\n',
}
ORACLE = '#include "h.h"\nint Key::k() { return 9; }\nint Root::r() { return 0; }\nInl *mk_inl() { return new Inl; }\n' \
         'struct AbsX : Abs2 { int pure() override { return 1; } int g2() override { return 2; } };\n' \
         'AbsX *mk_absx() { return new AbsX; }\n'
F = lambda a, s: {'address': '0x%08x' % a, 'symbol': s, 'size': 16}   # noqa: E731
VFT = {   # vftable address -> (MSVC name, class, base named by {for ...} or None)
    0x01000100: ('??_7Multi@@6BBase1@@@', 'Multi', 'Base1'), 0x01000120: ('??_7Multi@@6BBase2@@@', 'Multi', 'Base2'),
    0x01000140: ('??_7Base1@@6B@', 'Base1', None), 0x01000150: ('??_7Base2@@6B@', 'Base2', None),
    0x01000200: ('??_7Key@@6BBase1@@@', 'Key', 'Base1'), 0x01000220: ('??_7Key@@6BBase2@@@', 'Key', 'Base2'),
    0x01000300: ('??_7Inl@@6BBase1@@@', 'Inl', 'Base1'), 0x01000320: ('??_7Inl@@6BBase2@@@', 'Inl', 'Base2'),
    0x01000340: ('??_7Abs2@@6B@', 'Abs2', None),
}
GDD = {0x00401050: 'Multi', 0x00401090: 'Base1', 0x00401120: 'Key'}   # ??_G address -> class
UNITS = {'units': [
    {'source': 'src/a.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        F(0x401000, '??1Base1@@UAE@XZ'), F(0x401010, '?f1@Base1@@UAEHXZ'), F(0x401020, '??1Base2@@UAE@XZ'),
        F(0x401030, '?f2@Base2@@UAEHXZ'), F(0x401040, '??1Multi@@UAE@XZ'), F(0x401050, '??_GMulti@@UAEPAXI@Z'),
        F(0x401060, '?f2@Multi@@UAEHXZ'), F(0x401070, '?g2@Multi@@UAEHXZ'), F(0x401080, '?m@Multi@@UAEHXZ'),
        F(0x401090, '??_GBase1@@UAEPAXI@Z')],
     'symbols': {VFT[a][0]: '0x%08x' % a for a in (0x01000100, 0x01000120, 0x01000140, 0x01000150)}},
    {'source': 'src/b.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        F(0x401100, '?g2@Key@@UAEHXZ'), F(0x401110, '?mk@@YAPAUKey@@XZ'), F(0x401120, '??_GKey@@UAEPAXI@Z'),
        F(0x401130, '??1Key@@UAE@XZ')],
     'symbols': {VFT[a][0]: '0x%08x' % a for a in (0x01000200, 0x01000220)}},
    {'source': 'src/c.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        F(0x401200, '?use_inl@@YAHPAUInl@@@Z'), F(0x401210, '?use_abs@@YAHPAUAbs2@@@Z'),
        F(0x401220, '?mk_leaf@@YAPAULeaf@@XZ')],
     'symbols': {VFT[a][0]: '0x%08x' % a for a in (0x01000300, 0x01000320, 0x01000340)}},
]}
MANGLED = {'Multi': '5Multi', 'Base1': '5Base1', 'Base2': '5Base2', 'Key': '3Key', 'Inl': '3Inl', 'Abs2': '4Abs2',
           'Root': '4Root'}

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


def class_dump(path):
    """-fdump-lang-class -> {class: {'vptr': address point of its own vptr, 'bases': {base: (offset, address point)}}}
    (direct and indirect bases by name; a base marked primary-for shares the class's address point)."""
    out, cur, first = {}, None, False
    lines = open(path).read().split('\n')
    for i, line in enumerate(lines):
        m = re.match(r'^Class (\S+)$', line)
        if m:
            cur, first = out.setdefault(m.group(1), {'vptr': None, 'bases': {}}), True
            continue
        if cur is None:
            continue
        m = re.match(r'^( *)(\S+) \(0x0x[0-9a-f]+\) (\d+)', line)
        if m:
            name, off = m.group(2), int(m.group(3))
            ap = None
            for nxt in lines[i + 1:i + 4]:
                v = re.search(r'vptr=\(\(& \S+\) \+ (\d+)\)', nxt)
                if v:
                    ap = int(v.group(1)); break
                if re.match(r'^ *\S+ \(0x0x', nxt):
                    break
            if first:       # the class itself, then its bases (direct and indirect, preorder)
                cur['vptr'], first = ap, False
            elif 'primary-for' in ''.join(lines[i + 1:i + 2]):
                cur['bases'][name] = (off, 'primary')
            else:
                cur['bases'].setdefault(name, (off, ap))
        elif not line.strip():
            cur = None
    return out


class Elf:
    """Linked ELF32 image: symbol values and 32-bit words at virtual addresses."""

    def __init__(self, path):
        self.o = vtables.Obj(path)
        self.sym = {s[0]: s[1] for s in self.o.syms if s[0] and s[5]}

    def word(self, va):
        for s in self.o.secs:
            if s[1] != 8 and s[3] <= va < s[3] + s[5] and s[3]:
                return int.from_bytes(self.o.data[s[4] + va - s[3]:s[4] + va - s[3] + 4], 'little')
        return None


def main():
    with tempfile.TemporaryDirectory() as t:
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
        oracle_src, oracle_obj = os.path.join(t, 'oracle.cpp'), os.path.join(t, 'oracle.o')
        open(oracle_src, 'w').write(ORACLE)
        dump = os.path.join(t, 'classes.txt')
        run([CXX, *[f if f != '-O2' else '-O0' for f in layout.flags(tree)], '-fdump-lang-class=' + dump, oracle_src,
             '-o', oracle_obj], cwd=tree)
        json.dump(UNITS, open(os.path.join(t, 'units.json'), 'w'))
        with open(os.path.join(t, 'objects.tsv'), 'w') as f:
            for rel in SRC:
                f.write('%s\t%s\tok\n' % (rel, objs[rel]))
        out, fixed = os.path.join(t, 'out'), os.path.join(t, 'fixed')
        resolve = [sys.executable, FNVLINK, 'resolve', os.path.join(t, 'units.json'), os.path.join(t, 'objects.tsv'), out]
        run(resolve)
        reqs = vtables.load_requests(os.path.join(out, 'vtable_requests.tsv'))
        check({'Key', 'Inl', 'Abs2', 'Root'} <= set(reqs), 'requests: %s' % sorted(reqs))
        check('src/b.cpp' in reqs.get('Key', ('', '', []))[2], 'Key requested on the unit that references its vtable')
        run([sys.executable, FNVLINK, 'vtables', out, tree, '2'])
        run(resolve)
        run([sys.executable, FNVLINK, 'apply', out, fixed])
        run([sys.executable, FNVLINK, 'check', out, fixed])

        # 1. synthesized vtables and typeinfo == GCC's own
        oracle = vtables.Obj(oracle_obj)
        synth = {}
        for host, path in vtables.load_synth(out):
            o = vtables.Obj(path)
            for n in o.defs:
                synth[n] = o
        for c in ('Key', 'Inl', 'Abs2', 'Root'):
            for kind in ('_ZTV', '_ZTI'):
                n = kind + MANGLED[c]
                if check(n in synth, '%s synthesized' % n) and check(n in oracle.defs, '%s in the oracle' % n):
                    got, want = synth[n].ents(n), oracle.ents(n)
                    check(got == want, '%s differs from GCC:\n  got  %s\n  want %s' % (n, got, want))
        check('_ZTS3Key' in synth and '_ZTV5Multi' not in synth, 'typeinfo names synthesized, emitted vtables not')

        # 2. address map rows vs. GCC's class layout dump
        dumpd = class_dump(dump)
        rows = {int(r[0], 16): r for r in (l.rstrip('\n').split('\t') for l in open(os.path.join(out, 'addrmap.tsv'))
                                          if not l.startswith('#'))}
        want_ap = {}
        for a, (_, c, base) in VFT.items():
            d = dumpd.get(c, {})
            off, ap = (0, d.get('vptr')) if base is None else d.get('bases', {}).get(base, (None, None))
            if ap == 'primary':
                ap = d.get('vptr')
            want_ap[a] = (c, off, ap)
            r = rows.get(a)
            if check(r is not None and r[1] == 'V', 'V row for %08x (%s)' % (a, VFT[a][0])):
                check(r[2] == '_ZTV' + MANGLED[c] and int(r[6]) == ap, '%08x: %s + %s, GCC says %s + %s'
                      % (a, r[2], r[6], '_ZTV' + MANGLED[c], ap))
        for a, c in GDD.items():
            r = rows.get(a)
            check(r is not None and r[1] == 'G' and r[7] and r[8] == '_ZN%sD1Ev' % MANGLED[c],
                  'G row for %08x has the D1: %s' % (a, r))

        # 3. the linked image
        elf = Elf(os.path.join(out, 'trial.elf'))
        base, n = elf.sym.get('fnvnx_addrmap'), elf.word(elf.sym.get('fnvnx_addrmap_count', 0))
        table = {elf.word(base + 8 * i): elf.word(base + 8 * i + 4) for i in range(n or 0)}
        for a, (c, off, ap) in want_ap.items():
            z = elf.sym.get('_ZTV' + MANGLED[c])
            nat = table.get(a)
            if check(z is not None and nat == z + ap, '%08x -> %s, want _ZTV%s+%s = %s' % (a, nat, MANGLED[c], ap,
                                                                                       z and z + ap)):
                check(elf.word(nat - 8) == (-off) & 0xffffffff, '%08x offset-to-top' % a)
                check(elf.word(nat - 4) == elf.sym.get('_ZTI' + MANGLED[c]), '%08x typeinfo' % a)
        dbase, dn = elf.sym.get('fnvnx_addrmap_deldtors'), elf.word(elf.sym.get('fnvnx_addrmap_deldtor_count', 0))
        dd = {elf.word(dbase + 12 * i): (elf.word(dbase + 12 * i + 4), elf.word(dbase + 12 * i + 8))
              for i in range(dn or 0)}
        check(dn == len(GDD), 'deldtor table: %s entries' % dn)
        for a, c in GDD.items():
            d1, d0 = elf.sym.get('_ZN%sD1Ev' % MANGLED[c]), elf.sym.get('_ZN%sD0Ev' % MANGLED[c])
            check(d1 and d0 and dd.get(a) == (d1, d0) and table.get(a) == d0, '%08x deldtors %s, want %s'
                  % (a, dd.get(a), (d1, d0)))
        undef = set(re.findall(r"undefined reference to `([^']+)'", open(os.path.join(out, 'trial_link.log')).read()))
        undef = {u for u in undef if not fnvlink.RUNTIME.match(u)}
        # left: Key::k is defined nowhere; Inl's implicit destructors are only emitted where GCC emits its vtable or
        # destroys one (then the vtable is emitted too), which no unit does
        check(undef == {'_ZN3Key1kEv', '_ZN3InlD0Ev', '_ZN3InlD1Ev', '_ZN4Root1rEv'},
              'undefined after the link: %s' % sorted(undef))
        th = elf.sym.get('_ZThn8_N3Inl2g2Ev')
        if check(th is not None, 'thunk _ZThn8_N3Inl2g2Ev linked'):
            dis = run([OBJDUMP, '-d', '--no-show-raw-insn', '--start-address=%d' % th, '--stop-address=%d' % (th + 8),
                       os.path.join(out, 'trial.elf')])
            check(re.search(r'sub\t(?P<r>[wx])0, (?P=r)0, #0x8\n.*\tb\t[0-9a-f]+ <_ZN3Inl2g2Ev>', dis) is not None,
                  'thunk code:\n' + dis)
        # generated thunks (for thunks no object emits): this adjustment of the 32-bit this, then the tail call
        src, obj = os.path.join(t, 'th.S'), os.path.join(t, 'th.o')
        open(src, 'w').write(''.join(vtables.emit_thunk(n) for n in ('_ZThn8_N1C1fEv', '_ZThn70000_N1C1gEv',
                                                                     '_ZTh4_N1C1hEv')))
        run([vtables.AS, '-mabi=ilp32', src, '-o', obj])
        dis = run([OBJDUMP, '-dr', '--no-show-raw-insn', obj])
        for pat in (r'<_ZThn8_N1C1fEv>:\n.*sub\tw0, w0, #0x8\n.*\tb\t.*\n.*R_AARCH64_P32_JUMP26\t_ZN1C1fEv',
                    r'<_ZThn70000_N1C1gEv>:\n.*mov\tw16, #0x1170.*\n.*movk\tw16, #0x1, lsl #16\n.*sub\tw0, w0, w16\n'
                    r'.*\tb\t.*\n.*_ZN1C1gEv', r'<_ZTh4_N1C1hEv>:\n.*add\tw0, w0, #0x4\n'):
            check(re.search(pat, dis) is not None, 'generated thunk %s:\n%s' % (pat, dis))
        print('V rows %d, G rows %d, deldtors %d, undefined after link: %s'
              % (sum(r[1] == 'V' for r in rows.values()), sum(r[1] == 'G' for r in rows.values()), dn, sorted(undef)))
    print('ok' if ok else 'FAILED')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
