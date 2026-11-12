#!/usr/bin/env python3
"""test_cc1 - every plugin on a guest C unit (the C front end, cc1): one .so serves both front ends.

Rule (owner's order, L2): every guest unit, C or C++, loads the same plugins with the same arguments. A plugin's rule is about the code, not the
language: it does its rule on a C unit, or finds nothing because C has none of its trees (class vtables, destructors). This test shows which, per
plugin, from the recipe's object (never from assembly text): the plugin loads under cc1 and the unit builds, and the observable result is checked.
Every unit is built by verify/recipe.py stages on the pinned guest compiler (plugbuild)."""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plugbuild as pb  # noqa: E402
from plugbuild import Checks  # noqa: E402

check = Checks('test_cc1')
ALL = ('ftoi', 'order', 'sdd', 'pi', 'dispatch', 'addr', 'names', 'layout')

COMMON = ('struct S { int a; char b; };\nunion U { int i; float f; };\nstruct S gs;\nint g;\n'
          'static inline int in(int x) { return x + 1; }\nint callee(struct S *p);\n'
          'int t(struct S *p, int (*fp)(int)) { return in(p->a) + callee(p) + fp(2) + g; }\n')


def strings(obj, sec):
    """the text of section SEC of the object as lines, or None when the object has no such section"""
    t = pb.section(obj, sec)
    return t.split('\n') if t else None


def build_c(td, name, plugins, text, native=False, args=()):
    src = os.path.join(td, name + '.c')
    open(src, 'w').write(text)
    out = os.path.join(td, name + '.o')
    r = pb.compile(src, out, plugins, args=list(args), native=native)
    return r, out


def main():
    td = tempfile.mkdtemp(prefix='test-cc1-')
    try:
        so = {n: pb.build(n, td) for n in ALL}
        # --- every plugin alone loads under cc1 and builds a C unit (ftoi lifted is the root's, added by the toy root)
        for n in ALL:
            plugins = [so[n]] if n not in ('dispatch',) else [so['names'], so[n]]   # dispatch needs the names of the declarations' conventions
            r, out = build_c(td, 'c_' + n, plugins, COMMON)
            check('%s: loads under cc1 and a C unit builds' % n, r.returncode == 0, r.stderr[-1500:])
        # --- ftoi: the rule is about the conversion, not the language
        r, out = build_c(td, 'f_nat', [so['ftoi']], 'int plain(float f) { return (int)f; }\n', native=True)
        check('ftoi native, C: (int)f is rewritten (_ftol: 0x80000000 for NaN and out of range)', r.returncode == 0
              and '#0x80000000' in '\n'.join(pb.objdump(out).get('plain', [])), r.stderr[-1000:])
        HELP = '__attribute__((fnvnx_f2i)) int helper(float f) { return (int)f; }\n'
        r, out = build_c(td, 'f_help', [so['ftoi']], HELP)
        check('ftoi lifted, C: a conversion inside an fnvnx_f2i helper builds, unrewritten', r.returncode == 0
              and 'fcvtzs' in '\n'.join(pb.objdump(out).get('helper', [])), r.stderr[-1000:])
        for what, text, kind in (('cast', 'int bad(float f) { return (int)f; }\n', 'conv'),
                                 ('lround', 'long bad(float f) { return __builtin_lroundf(f); }\n', 'builtin'),
                                 ('asm', 'float bad(float f) { __asm__ volatile("" : "+w"(f)); return f; }\n', 'asm')):
            r, _ = build_c(td, 'f_bad_' + what, [so['ftoi']], text)
            check('ftoi lifted, C: a %s outside a helper is a build error' % what,
                  r.returncode != 0 and 'FTOI (lifted)' in r.stderr and '(%s)' % kind in r.stderr, r.stderr[-1000:])
        # --- order: MSVC's volatile is ldar/stlr in C too
        r, out = build_c(td, 'o_vol', [so['order']], 'volatile int g;\nint rd(void) { return g; }\nvoid wr(int v) { g = v; }\n')
        d = pb.objdump(out)
        check('order, C: a volatile load is ldar and a volatile store is stlr', r.returncode == 0
              and 'ldar' in '\n'.join(d.get('rd', [])) and 'stlr' in '\n'.join(d.get('wr', [])), r.stderr[-1000:])
        # --- dispatch: an indirect call of C code goes through the dispatcher
        r, out = build_c(td, 'd_ic', [so['names'], so['dispatch']], 'int ic(int (*f)(int)) { return f(1); }\n')
        check('dispatch, C: an indirect call is routed through fnvnx_dispatch', r.returncode == 0
              and any(x[2] == 'fnvnx_dispatch' for v in pb.relocs_of(out).values() for x in v), r.stderr[-1000:])
        # --- addr: an address computed in a 32-bit integer is used as that 32-bit value
        r, out = build_c(td, 'a_add', [so['addr']], 'int ld(unsigned x) { return *(int *)(x + 0x40u * 4096u); }\n')
        txt = '\n'.join(pb.objdump(out).get('ld', [])) if r.returncode == 0 else ''
        check('addr, C: the sum x + 0x40000 stays a 32-bit add (no zero-extended addressing mode)', r.returncode == 0
              and re.search(r'add\s+w\d+, w\d+', txt) is not None and 'uxtw' not in txt, txt or r.stderr[-1000:])
        # --- sdd: C has no classes, so no virtual destructor trees: nothing to do, and nothing is written
        r, out = build_c(td, 's_nothing', [so['sdd']], COMMON)
        check('sdd, C: finds nothing (no class, no virtual destructor): no sdd symbol, no vtable', r.returncode == 0
              and not any('sdd' in x or x.startswith('_ZTV') for x in pb.symbols_of(out)), r.stderr[-1000:])
        # --- pi: the function-level reading, over C trees
        r, out = build_c(td, 'p_n3', [so['pi']], COMMON)
        n3 = strings(out, '.fnvnx.n3') if r.returncode == 0 else None
        check('pi, C: a row for the function with the inlined body and the record it touches', n3 is not None
              and any(l.startswith('t\tok\tin\t') and l.endswith('\tS') for l in n3), n3 or r.stderr[-1000:])
        # --- names: every external symbol of a C unit has C linkage: cl's C names
        r, out = build_c(td, 'n_c', [so['names']], 'int g;\nint f(int a) { return a + g; }\nstatic int hid(void) { return 1; }\nint use(void) { return hid() + f(1); }\n')
        sp = strings(out, '.fnvnx.sympair') if r.returncode == 0 else None
        rows = {l.split('\t')[0]: l.split('\t')[1:] for l in (sp or []) if l}
        check('names, C: g -> _g (data), f -> _f (func); a static function has no row', sp is not None
              and rows.get('g') == ['_g', 'data', 'def'] and rows.get('f') == ['_f', 'func', 'def'] and 'hid' not in rows, sp or r.stderr[-1000:])
        # --- layout: the layout of every complete record type, keyed by cl's name
        r, out = build_c(td, 'l_c', [so['layout']], COMMON)
        lay = strings(out, '.fnvnx.layout') if r.returncode == 0 else None
        keys = {l.split('\t')[0]: l.split('\t')[1:] for l in (lay or []) if l}
        check('layout, C: US@@ is size 8 align 4 fields a:0:4,b:4:1; TU@@ is a union of size 4', lay is not None
              and keys.get('US@@', [None] * 3)[:3] == ['8', '4', 'a:0:4,b:4:1'] and keys.get('TU@@', [None])[0] == '4', lay or r.stderr[-1000:])
        # --- all eight together on one C unit (a native root: the full set with its fixed arguments)
        r, out = build_c(td, 'all', [], COMMON, native=True)
        check('all eight plugins together build a C unit of a native root', r.returncode == 0, r.stderr[-1500:])
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
