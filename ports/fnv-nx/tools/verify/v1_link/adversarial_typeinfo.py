#!/usr/bin/env python3
"""adversarial_typeinfo.py - V1-link round 3: break J-0081 fix 4 (each class keeps its typeinfo from the object whose
vtable is kept; `fnvlink.pair_typeinfo`, run by apply) with hand-made units that reconstruct classes differently,
linked like `fnvlink.py check` (sorted names, aarch64linux32, --noinhibit-exec) and read back from the image.

    python3 adversarial_typeinfo.py <work dir>     # exit 1 when a case's image still mixes views

The check per class C (own, independent of the tool): every sub-vtable of the kept _ZTV<C> has offset-to-top -o
and the kept _ZTI<C>'s hierarchy (walked through the base typeinfos in the image) has the base the vtable's own
unit puts at +o, by name. Cases:
  t1 multiple inheritance, the other unit's view (one base) links first                         (J-0074 shape)
  t2 t1 linked in reverse order (another link order than check's: the guest link, an archive)
  t2b a third view that sorts after the vtable's object, reverse link order (its copy is left defined)
  t3 virtual inheritance: the vtable unit has D : W, virtual V; the other unit's D : virtual V only
  t4 nested: C : B in both units, B's second base differs; B's vtable kept from the other unit
  t6 as t4, but the C unit only references B's typeinfo (B has a key function defined in the other unit, whose
     view of B differs): no copy of its own to keep, the kept B is the other view
  t5 the vtable's object defines no typeinfo (synthesized vtable objects did before J-0102), another view does:
     the pass cannot repair it and must report it ('vtable object defines no typeinfo'; c4_definers counts it)
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'link'))
import c4_vtables  # noqa: E402
import elf32       # noqa: E402
import elfsyms     # noqa: E402  (only to build t5's input the way the tool turns definitions into references)
import fnvlink     # noqa: E402
import qemu_ilp32  # noqa: E402

CASES = {
    't1': ([('a_view.cpp', '''#include <typeinfo>
struct B1 { virtual ~B1() {} int x; };
struct C : B1 { virtual void f() {} int y; };
const std::type_info &ti_a() { return typeid(C); }'''),
            ('b_vtable.cpp', '''struct B1 { virtual ~B1() {} int x; };
struct B2 { virtual void g() {} int z; };
struct C : B1, B2 { virtual void f() {} int y; };
C *make_c() { return new C; }''')], 'C', {4 * 2: 'B2'}, False),
    't2b': ([('a_view.cpp', '''#include <typeinfo>
struct B1 { virtual ~B1() {} int x; };
struct C : B1 { virtual void f() {} int y; };
const std::type_info &ti_a() { return typeid(C); }'''),
             ('b_vtable.cpp', '''struct B1 { virtual ~B1() {} int x; };
struct B2 { virtual void g() {} int z; };
struct C : B1, B2 { virtual void f() {} int y; };
C *make_c() { return new C; }'''),
             ('c_view.cpp', '''#include <typeinfo>
struct B1 { virtual ~B1() {} int x; };
struct C : B1 { virtual void h() {} int y; };
const std::type_info &ti_c() { return typeid(C); }''')], 'C', {8: 'B2'}, False),
    't3': ([('a_view.cpp', '''#include <typeinfo>
struct V { virtual ~V() {} int v; };
struct D : virtual V { virtual void f() {} int d; };
const std::type_info &ti_a() { return typeid(D); }'''),
            ('b_vtable.cpp', '''struct V { virtual ~V() {} int v; };
struct W { virtual void g() {} int w; };
struct D : W, virtual V { virtual void f() {} int d; };
D *make_d() { return new D; }''')], 'D', {0: 'W'}, False),
    't4': ([('a_bview.cpp', '''struct P { virtual ~P() {} int p; };
struct Q { virtual void q() {} int qq; };
struct B : P, Q { virtual void b() {} };
B *make_b() { return new B; }'''),
            ('b_cview.cpp', '''struct P { virtual ~P() {} int p; };
struct R { virtual void r() {} int rr; };
struct B : P, R { virtual void b() {} };
struct C : B { virtual void c() {} int cc; };
C *make_c() { return new C; }''')], 'C', {8: 'R'}, False),
    't6': ([('a_bview.cpp', '''struct P { virtual ~P() {} int p; };
struct Q { virtual void q() {} int qq; };
struct B : P, Q { virtual void b(); };
void B::b() {}
B *make_b() { return new B; }'''),
             ('b_cview.cpp', '''struct P { virtual ~P() {} int p; };
struct R { virtual void r() {} int rr; };
struct B : P, R { virtual void b(); };
struct C : B { virtual void c() {} int cc; };
C *make_c() { return new C; }''')], 'C', {8: 'R'}, False),
    't5': ([('a_view.cpp', '''#include <typeinfo>
struct B1 { virtual ~B1() {} int x; };
struct C : B1 { virtual void f() {} int y; };
const std::type_info &ti_a() { return typeid(C); }'''),
            ('b_vtable.cpp', '''struct B1 { virtual ~B1() {} int x; };
struct B2 { virtual void g() {} int z; };
struct C : B1, B2 { virtual void f() {} int y; };
C *make_c() { return new C; }''')], 'C', {8: 'B2'}, True),
}


def mangled(n):
    return '%d%s' % (len(n), n)


def check_image(elf, cls, want):
    """-> list of problems: sub-vtable offsets of _ZTV<cls> without the expected base in the typeinfo walk."""
    img = elf32.Image(elf)
    ti = c4_vtables.TI(img)
    vt = img.by_name.get('_ZTV' + mangled(cls))
    t = img.by_name.get('_ZTI' + mangled(cls))
    if not vt or not t:
        return ['vtable or typeinfo not in the image']
    probs = []
    # every (offset-to-top, typeinfo) header in the group
    k = vt[0]
    offs = []
    while k + 8 <= vt[0] + vt[1]:
        if img.u32(k + 4) == t[0] and (img.i32(k) or 0) <= 0:
            offs.append(-img.i32(k))
        k += 4
    seen = {}
    q = [(t[0], 0)]
    has_virtual = False
    while q:
        a, off = q.pop(0)
        for b, o, virt, pub in (ti.bases(a) or []):
            n = ti.name_of(b)
            n = n.split('.')[0] if n else n          # a local view copy _ZTI<B>.fnvnx_view names B
            if virt:
                has_virtual = True       # its offset is a vbase-offset slot, not a constant
                continue
            seen.setdefault(off + o, set()).add(n)
            q.append((b, off + o))
    for o, base in want.items():
        names = seen.get(o, set())
        if '_ZTI' + mangled(base) not in names:
            probs.append('vtable has a sub-vtable at +%d (%s in the vtable unit), the kept typeinfo has %s there' % (
                o, base, sorted(x for x in names if x) or 'nothing'))
    for o in ([] if has_virtual else offs):
        if o and o not in seen:
            probs.append('sub-vtable at offset-to-top -%d: no base there in the kept typeinfo' % o)
    return probs


def build(work, units, no_ti_in_vtable_obj):
    if os.path.isdir(work):
        shutil.rmtree(work)
    fixed = os.path.join(work, 'fixed')
    os.makedirs(fixed)
    for name, text in units:
        src = os.path.join(work, name)
        open(src, 'w').write(text + '\n')
        subprocess.run([qemu_ilp32.CXX, '-mabi=ilp32', '-O2', '-fPIC', '-c', src, '-o',
                        os.path.join(fixed, name + '.o')], check=True)
    if no_ti_in_vtable_obj:
        o = os.path.join(fixed, [n for n, _ in units if 'vtable' in n][0] + '.o')
        elfsyms.undefine_symbols(o, o + '.t', {'_ZTI1C'})
        os.replace(o + '.t', o)
    return fixed


def link(work, fixed, reverse=False):
    elf = os.path.join(work, 'trial%s.elf' % ('_rev' if reverse else ''))
    objs = sorted((os.path.join(fixed, n) for n in os.listdir(fixed)), reverse=reverse)
    subprocess.run([qemu_ilp32.LD, '-m', 'aarch64linux32', '--noinhibit-exec', '-e', '0', '-o', elf, *objs],
                   capture_output=True)
    return elf


def main():
    work = os.path.abspath(sys.argv[1])
    bad = 0
    runs = [('t1', False), ('t2', True), ('t2b', True), ('t3', False), ('t4', False), ('t5', False),
            ('t6', False)]
    for cid, reverse in runs:
        units, cls, want, no_ti = CASES['t1' if cid == 't2' else cid]
        w = os.path.join(work, cid)
        fixed = build(w, units, no_ti)
        before = check_image(link(w, fixed, reverse), cls, want)
        rows, cnt = fnvlink.pair_typeinfo(fixed)             # the code under test (apply's last pass)
        after = check_image(link(w, fixed, reverse), cls, want)
        ok = not after
        if cid == 't5' and after and cnt.get('vtable object defines no typeinfo'):
            # J-0102: synthesis now always emits the vtable's own typeinfo, so the state is not produced; where it
            # still occurs the pass must say so (the farm audit checks that counter is 0)
            ok = True
        bad += 0 if ok else 1
        print('%-6s %s %s: before %s | tool: %d classes %s | after: %s' % (
            'ok' if ok else 'BROKEN', cid, '(reverse link order)' if reverse else '', before or 'consistent',
            len(rows), dict(cnt), after or 'consistent'))
    print('broken: %d' % bad)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
