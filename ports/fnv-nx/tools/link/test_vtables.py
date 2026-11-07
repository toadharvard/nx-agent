#!/usr/bin/env python3
"""End-to-end test of the vtable side of the address map on hand-written classes (no decomp content).

    python3 tools/link/test_vtables.py     # needs aarch64-linux-gnu-{g++,as,ld,objcopy,objdump}, llvm-undname, SSE2NEON

Classes (header h.h): Base1 / Base2 (virtual destructors, Base2::g2 pure), Plain (no virtuals); Multi : Base1,
Plain, Base2 (key function in unit a, so GCC emits its group); Key : Plain, Base1, Base2 (key function Key::k is
defined nowhere: its vtable is never emitted, unit b references it); Inl : Base1, Base2 (inline virtuals only, never
constructed: no vtable anywhere); Abs2 : Base2 (abstract, inline virtual); Root (key function defined nowhere,
reached only through the typeinfo of Leaf, which unit c constructs); KeyImp, KeyDel (key function defined nowhere,
implicit virtual destructor, constructed by unit c; KeyDel has its own operator delete). units.json names their
primary and
`{for `B'}` secondary vftables and the scalar deleting destructors of Multi, Key and Base1.

Pipeline: resolve -> vtables (probe) -> resolve -> apply -> check. Checks:
* synthesized _ZTV/_ZTI of Key, Inl, Abs2, Root equal, word for word and relocation for relocation, the ones GCC
  emits in an oracle unit that defines Key::k and Root::r and constructs Inl and a subclass of Abs2;
* every V entry's offset is the address point GCC reports for that base-class subobject (-fdump-lang-class:
  `vptr=((& C::_ZTV...) + N)`, the class's own one for a primary base);
* in the trial link: fnvnx_addrmap[] holds _ZTV<C> + offset for each vftable, with offset-to-top = -(subobject offset)
  and the class's typeinfo before it; every G entry has {D1, D0} in fnvnx_addrmap_deldtors[] equal to the class's
  _ZN..D1Ev / _ZN..D0Ev, D0 also in fnvnx_addrmap[]; nothing is left undefined: the inline virtual and deleting
  destructors of KeyImp/KeyDel (vtables compiled code uses) are emitted from the host unit (D1, then the global
  sized or the class's operator delete; a call of the host's own function stays a reference), the slots of
  virtuals defined nowhere and of Inl (address map only) call trap stubs; thunks adjust this and branch to their
  function (generated ones: w0, any offset).
"""
import hashlib
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
int helper_c();
struct Inl : Base1, Base2 { int g2() override { return 7; } virtual int i() { return 8; } };
struct Abs2 : Base2 { virtual int pure() = 0; int f2() override { return 3; } };
struct Root { virtual int r(); };
struct Leaf : Root { int r() override { return 1; } };
struct KeyImp : Base1 { virtual int ki(); virtual int j() { return helper_c(); } int x; };
struct KeyDel : Base1 { virtual int kd(); static void operator delete(void *); };
struct KeyOut : Base1 { virtual int ko(); ~KeyOut(); };
'''
SRC = {
    'src/a.cpp': '#include "h.h"\nBase1::~Base1() {}\nint Base1::f1() { return 1; }\nBase2::~Base2() {}\n'
                 'int Base2::f2() { return 2; }\nMulti::~Multi() {}\nint Multi::f2() { return 3; }\n'
                 'int Multi::g2() { return 4; }\nint Multi::m() { return 5; }\n',
    'src/b.cpp': '#include "h.h"\nKey::~Key() {}\nint Key::g2() { return 6; }\nKey *mk() { return new Key; }\n'
                 'extern "C" char DAT_01000a80[];\n'           # S13 00404920: a free function's vptr store
                 'void *init_free(void *self)\n{\n    *(void **)self = DAT_01000a80;\n    return self;\n}\n',
    'src/c.cpp': '#include <typeinfo>\n#include "h.h"\nint use_inl(Inl *p) { return p->Inl::g2() + p->Inl::i(); }\n'
                 'int helper_c() { return 9; }\n'
                 'const std::type_info &ti_inl() { return typeid(Inl); }\n'   # a compiled copy of Inl's typeinfo
                 'static int s_init = helper_c();\nint get_init() { return s_init; }\n'
                 'int use_abs(Abs2 *p) { return p->Abs2::f2(); }\nLeaf *mk_leaf() { return new Leaf; }\n'
                 'void KeyDel::operator delete(void *p) { ::operator delete(p); }\n'
                 'KeyImp *mk_ki() { return new KeyImp; }\nKeyDel *mk_kd() { return new KeyDel; }\n'
                 'KeyOut *mk_ko() { return new KeyOut; }\n',
}
# Unit e: the classes link 9f865b0a left as zero blocks. It carries its own std::exception (the decomp's staging
# units do), so it does not compile after HEAD's <typeinfo>: probed with HEAD_BARE. FUN_e1 is hidden by a function of
# the same name (elaborated name).
EHEADER = r'''#pragma once
namespace std { class exception { public: exception(const char *m) : msg(m) {} virtual ~exception() {}
    virtual const char *what() const { return msg; } const char *msg; }; }
struct ExcE : std::exception { ExcE(const char *m) : std::exception(m), code(1) {} virtual ~ExcE(); int code; };
void FUN_e1();
struct FUN_e1 { virtual ~FUN_e1(); virtual int v() { return 2; } int a; };
'''
SRC['src/e.cpp'] = ('#include "e.h"\nvoid FUN_e1() {}\n'
                    'int catch_e(void (*f)()) { try { f(); } catch (ExcE &e) { return e.code; } return 0; }\n'
                    'struct FUN_e1 *mk_e1() { return new struct FUN_e1; }\n')
EORACLE = '#define FNVNX_ORACLE\n#include "e.h"\nExcE::~ExcE() {}\nFUN_e1::~FUN_e1() {}\n'
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
    0x01000360: ('??_7KeyImp@@6B@', 'KeyImp', None), 0x01000380: ('??_7KeyDel@@6B@', 'KeyDel', None),
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
        F(0x401130, '??1Key@@UAE@XZ'), F(0x401140, '?init_free@@YAPAXPAX@Z')],
     'symbols': dict({VFT[a][0]: '0x%08x' % a for a in (0x01000200, 0x01000220)}, _DAT_01000a80='0x01000a80')},
    {'source': 'src/c.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        F(0x401200, '?use_inl@@YAHPAUInl@@@Z'), F(0x401210, '?use_abs@@YAHPAUAbs2@@@Z'),
        F(0x401230, '?mk_ki@@YAPAUKeyImp@@XZ'), F(0x401240, '?mk_kd@@YAPAUKeyDel@@XZ'),
        F(0x401250, '?mk_ko@@YAPAUKeyOut@@XZ'),
        F(0x401220, '?mk_leaf@@YAPAULeaf@@XZ')],
     'symbols': {VFT[a][0]: '0x%08x' % a for a in (0x01000300, 0x01000320, 0x01000340, 0x01000360, 0x01000380)}},
    {'source': 'src/d.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [   # not compiled: KeyOut's destructor
        F(0x401500, '??1KeyOut@@UAE@XZ')], 'symbols': {}},
    {'source': 'src/e.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        F(0x401600, '?FUN_e1@@YAXXZ'), F(0x401610, '?catch_e@@YAHP6AXXZ@Z')], 'symbols': {}},
]}
MANGLED = {'Multi': '5Multi', 'Base1': '5Base1', 'Base2': '5Base2', 'Key': '3Key', 'Inl': '3Inl', 'Abs2': '4Abs2',
           'Root': '4Root', 'KeyImp': '6KeyImp', 'KeyDel': '6KeyDel', 'KeyOut': '6KeyOut'}
# unit e: the names GCC emits for these classes with their key functions (EORACLE) == the synthesized ones
E_NAMES = ['_ZTI4ExcE', '_ZTV4ExcE', '_ZTV6FUN_e1', '_ZTI6FUN_e1']

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
    keep = os.environ.get('FNVNX_TEST_KEEP')   # a directory to keep the test's files in (debugging)
    with tempfile.TemporaryDirectory() as tmp:
        t = keep or tmp
        tree = os.path.join(t, 'tree')
        os.makedirs(os.path.join(tree, 'include'), exist_ok=True)
        open(os.path.join(tree, 'include', 'h.h'), 'w').write(HEADER)
        open(os.path.join(tree, 'include', 'e.h'), 'w').write(EHEADER)
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
        eoracle_src, eoracle_obj = os.path.join(t, 'eoracle.cpp'), os.path.join(t, 'eoracle.o')
        open(eoracle_src, 'w').write(EORACLE)
        run([CXX, *[f if f != '-O2' else '-O0' for f in layout.flags(tree)], eoracle_src, '-o', eoracle_obj], cwd=tree)
        json.dump(UNITS, open(os.path.join(t, 'units.json'), 'w'))
        with open(os.path.join(t, 'objects.tsv'), 'w') as f:
            for rel in SRC:
                f.write('%s\t%s\tok\n' % (rel, objs[rel]))
        out, fixed = os.path.join(t, 'out'), os.path.join(t, 'fixed')
        resolve = [sys.executable, FNVLINK, 'resolve', os.path.join(t, 'units.json'), os.path.join(t, 'objects.tsv'), out,
                   '--crt-symbols', 'none', '--imports', 'none']   # hand-written addresses: not S4's tables
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
        # unit e (HEAD_BARE, elaborated name)
        eoracle = vtables.Obj(eoracle_obj)
        for n in E_NAMES:
            if check(n in synth, '%s synthesized' % n) and check(n in eoracle.defs, '%s in the oracle' % n):
                got, want = synth[n].ents(n), eoracle.ents(n)
                check(got == want, '%s differs from GCC:\n  got  %s\n  want %s' % (n, got, want))
        for n in ('_ZN6FUN_e1D1Ev', '_ZN4ExcED1Ev'):   # defined nowhere: never taken from the probe
            check(n not in synth, '%s not defined by the synthesized objects' % n)
        # S2c-vt item 5: emitted slot functions are game code, compiled with compile.sh's own flags (--print-flags)
        gf = layout.game_flags(tree, os.path.join(out, 'vtables', 'probe'))
        check(any(f.endswith('fnvnx_ftoi.so') for f in gf) and any(f.endswith('fnvnx_order.so') for f in gf) and
              '-mno-outline-atomics' in gf and '-fPIC' in gf, 'emitted code: compile.sh\'s flags and plugins: %s' % gf)
        # V1 round 3 t5: unit c holds a compiled copy of Inl's typeinfo (typeid), yet the synthesized vtable object
        # carries its own (checked against GCC above) and that is the one apply keeps with the vtable
        pairs = [l.split('\t') for l in open(os.path.join(out, 'typeinfo_pairs.tsv')) if not l.startswith('#')]
        check(not any(r[0] == '3Inl' and r[1] != os.path.basename(synth['_ZTI3Inl'].path) for r in pairs),
              'Inl\'s typeinfo kept from the synthesized vtable object: %s' % [r for r in pairs if r[0] == '3Inl'])
        for host, path in vtables.load_synth(out):   # unit c has a static initializer: it must never run twice
            secs = vtables.Obj(path).secnames
            check(not any(n.endswith(('.init_array', '.ctors', '.fini_array', '.dtors')) for n in secs),
                  'no static-initializer table in %s' % os.path.basename(path))

        # 2. address map rows vs. GCC's class layout dump
        dumpd = class_dump(dump)
        rows = {int(r[0], 16): r for r in (l.rstrip('\n').split('\t') for l in open(os.path.join(out, 'addrmap.tsv'))
                                          if not l.startswith('#'))}
        check(0x01000a80 not in rows, 'a free function\'s vptr store is no V entry of the class before it: %s'
              % rows.get(0x01000a80))
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
        log = open(os.path.join(out, 'trial_link.log')).read()
        check('multiple definition' not in log, 'multiple definitions in the trial link')
        undef = set(re.findall(r"undefined reference to `([^']+)'", log))
        undef = {u for u in undef if not fnvlink.RUNTIME.match(u)}
        check(undef >= {'__fnv_00401500'}, 'KeyOut::~KeyOut bound by address (pending): %s' % sorted(undef))
        undef.discard('__fnv_00401500')
        # the slot functions of the vtables compiled objects use (KeyImp, KeyDel: unit c constructs them) are
        # emitted from the host unit; the virtuals defined nowhere (Key::k, Root::r, KeyImp::ki, KeyDel::kd) and
        # the slots of vtables only the address map uses (Inl's implicit destructors) call trap stubs
        check(not undef, 'undefined after the link: %s' % sorted(undef))
        dis = run([OBJDUMP, '-d', '--no-show-raw-insn', os.path.join(out, 'trial.elf')])
        for c, fn in (('Key', '_ZN3Key1kEv'), ('Root', '_ZN4Root1rEv'), ('KeyImp', '_ZN6KeyImp2kiEv'),
                      ('Inl', '_ZN3InlD0Ev')):
            stub = fnvlink.TRAP_SYM % int(hashlib.sha1(fn.encode()).hexdigest()[:8], 16)
            ents = synth['_ZTV' + MANGLED[c]].ents('_ZTV' + MANGLED[c])
            i = next((i for i, e in enumerate(ents) if e.sym == fn), None)
            z = elf.sym.get('_ZTV' + MANGLED[c])
            if check(i is not None and z is not None and stub in elf.sym, 'trap stub %s for %s' % (stub, fn)):
                check(elf.word(z + 4 * i) == elf.sym[stub], '%s slot -> %s' % (fn, stub))
                body = re.search(r'<%s>:\n((?:.*\n){3})' % stub, dis)
                check(body is not None and 'adrp' in body.group(1) and '<fnvnx_vslot_trap>' in body.group(1),
                      'trap stub code: %s' % (body and body.group(1)))
        # deleting destructors of classes whose key function is never compiled: D1, then the right operator delete
        # an emitted inline virtual calls the host's own function: a reference, not a copy of its body
        j = synth.get('_ZN6KeyImp1jEv')
        if check(j is not None and j.bind('_ZN6KeyImp1jEv') == 'weak', 'KeyImp::j emitted from the host (weak)'):
            check('_Z8helper_cv' not in j.defs and any(s[0] == '_Z8helper_cv' and s[5] == 0 for s in j.syms),
                  'helper_c stays a reference in the emitted object')
        # KeyImp/KeyDel: GCC's own D0 (-fkeep-inline-functions; KeyDel's operator delete body inlined: the
        # unsized ::operator delete it calls, never the global sized one); KeyOut (destructor defined in a unit
        # that does not compile): the emulated D0 calls D1, bound by address, then the sized global delete
        for c, dealloc in (('KeyImp', ('_ZdlPvm',)), ('KeyDel', ('_ZN6KeyDeldlEPv', '_ZdlPv')),
                           ('KeyOut', ('_ZdlPvm',))):
            d0 = '_ZN%sD0Ev' % MANGLED[c]
            if check(d0 in synth, '%s emitted' % d0):
                sdis = run([OBJDUMP, '-dr', '--no-show-raw-insn', synth[d0].path])
                body = re.search(r'<%s>:\n(.*?)(?:\n\n|\Z)' % re.escape(d0), sdis, re.S)
                calls = re.findall(r'R_AARCH64_P32_(?:CALL|JUMP)26\t(\S+)', body.group(1)) if body else []
                # D1 (or, inlined, the base destructor it calls), then the class's or the global sized delete
                check(len(calls) == 2 and calls[0] in ('_ZN%sD1Ev' % MANGLED[c], '_ZN5Base1D2Ev') and calls[1] in dealloc,
                      '%s destroys, then calls %s: %s' % (d0, dealloc, calls))
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
