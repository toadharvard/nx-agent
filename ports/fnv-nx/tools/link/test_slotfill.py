#!/usr/bin/env python3
"""End-to-end test of S2c-vt's slot fill (fnvlink resolve --exe; docs/LINK.md "Vftables from the player's exe"):
hand-written classes and a synthetic exe (test_exevft.make_pe), no game content.

    python3 tools/link/test_slotfill.py     # needs aarch64-linux-gnu-{g++,as,ld,objcopy}, llvm-undname, SSE2NEON

Vb has a virtual destructor and the virtuals a, b, c, d, f, e; only a (and the destructor) is defined under its own
name. The exe's vftable of Vb holds, after the deleting destructor and a: b's body, which the decomp defines as
Shell::impl() (compiled, same arguments); c's body, Shell::impl2(Agg) (an aggregate by value where c(int)
passes an int: no bridge); f's body, Shell::impl3(int, int) (reads more arguments than the placeholder f()
declares: callers reach the slot through their own declarations, so it is bound and listed); d's body at an address no compiled unit defines; and no slot for e. Der : Vb overrides a and inherits the
rest; its exe vftable holds the same bodies. Checks:
* b is bound to the native definition at the exe slot's address (`__fnv_00401020`, class vslot-exe) in both
  vtables of the object (Vb's and Der's agree), and its trap stub is gone;
* f is bound too (`the definition reads more arguments`); c, d and e stay traps, listed in slot_gaps.tsv with their
  reasons (abi, not compiled, past the exe's slots);
* GCC's destructor pair counts as one MSVC slot (the minus-one rule: b is GCC slot 3, exe slot 2);
* after apply the fixed object's vtable slot names `__fnv_00401020`, the trial link leaves it defined, and the
  stage without the exe (`--exe none`) keeps b a trap;
* GCC's D1/D0 of Vd, whose exe ??_G is a decomp function taking the flags (Shell::del(unsigned)), go through flag
  stubs (w1 = 0 / 1); Vp's placeholder slot v00 where the exe has Gd's ??_G goes through a stub that picks D0 or D1
  by the caller's flags;
* Sh (inline virtuals only: a weak vtable) has one native slot where the exe has three: its V row names a strong
  extended copy in zz_bridges.o (p, `__fnv_00401020`, a trap for the address no unit compiles), and the trial
  link has no second definition;
* Der's exe vftable holds another body for b (Shell::impl4, an override the decomp does not declare): b's symbol
  follows the declaring class Vb, and Der's slot gets its own word in a strong copy of Der's vtable;
* Vk::k(int), whose body the decomp defines as `int __stdcall kbody(int)` (the x86 model: the slot's int is the
  stack argument both read), goes through a bridge that moves it from x1 to x0;
* Vk::kz, whose exe body is `xor eax, eax; ret` and which no unit compiles, gets the native stub `return 0`;
* Vk::kq, a placeholder `virtual void kq();` whose exe body is the free cdecl `qbody(int)` (the x86 code reads the
  first stack argument, which a thiscall caller passes after this), goes through a bridge x0 <- x1;
* S2::s2 (defined nowhere) in M's secondary vtable, where the exe's M {for S2} vftable holds an adjustor thunk
  (`sub ecx, 8; jmp M::s2body`) and the native vtable's offset-to-top is -8, gets `sub w0, w0, #8; b __fnv_<a>`;
* a reference that uses the result of the constructor at 0x00401130 (MSVC returns this) binds to a stub that calls
  the C1 and returns this, and the address map's C row names that stub (S5 s5-002 item 4);
* a call bound by address to that adjustor thunk, which units.json places (`?s2@M@@W7AEHXZ`) and GCC never emits,
  binds to a stub at the address, `sub w0, w0, #8; b __fnv_<M::s2body>`, which the address map's F row names (R6);
* a call bound to ??_GNv, the scalar deleting destructor of a class whose destructor is not virtual (GCC emits no
  D0), binds to a stub: Nv's D1, then the global operator delete when flags bit 0 is set (R6);
* Dd's D1, defined nowhere (MSVC folded ~Dd into ??_GDd, which the unit binds to a (this, flags) function), is
  `??_G(this, 0)` through the flag stub (S1-refs-004);
* Eg's D1, defined nowhere, where the unit names ??_GEg at 0x4011e0 and no native body is there: the exe's ??_G is
  `call 0x401190; test byte [esp+8], 1; je; call delete`, 0x401190 has a native plain destructor body (EgS), so D1 binds
  to `__fnv_00401190` - not to the D1 of Hd, which merely shares the ??_G address (the aliasing of the identical-code
  folding picked the wrong body), and without the exe the alias stays (W26);
* vtmetric.py counts what is left: trap slots 6 (c, d, e in both vtables), 2 of them with a native at the exe slot
  (c: the abi gap).
"""
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import layout          # noqa: E402
import exevft          # noqa: E402
import test_exevft     # noqa: E402
import vtables         # noqa: E402
import vtmetric        # noqa: E402

FNVLINK = os.path.join(HERE, 'fnvlink.py')
CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
FAILS = []

HEADER = r'''#pragma once
struct Agg { int a, b, c, d, e; };
struct Vb { virtual ~Vb(); virtual int a(); virtual int b(); virtual int c(int); virtual int d(); virtual int f();
            virtual int e(); int x; };
struct Der : Vb { int a() override; };
struct Shell { int impl(); int impl2(Agg); int impl3(int, int); void *del(unsigned); int impl4(); int implh(); int implw(); int y; };
struct Vh { virtual int g0(); virtual int g1(); virtual int hh(); int q; };   // hh defined nowhere; another unit's reference names it
struct Wh { virtual int w0(); virtual int w1(); virtual int hw(); int q; };   // hw defined nowhere; another unit's reference names it
struct Wd : Wh { int w0() override; };                       // the exe's Wd overrides hw too; the decomp's Wd does not declare it
struct Vu { virtual int u0(); virtual int u1(); virtual int hu(); int q; };   // hu defined nowhere; the unit's own recipe binds it, no code calls it
struct Vc { inline virtual int c0(); inline virtual int c1(); inline virtual int hc(); int q; };   // hc defined nowhere; c0 calls Vc::hc() directly
struct Vd { virtual int g(); virtual ~Vd(); int z; };      // key function g; ~Vd defined nowhere
struct Gd { virtual ~Gd(); int w; };
struct Vp { virtual int h(); virtual void v00(); int u; }; // v00: a placeholder for the exe's ??_G
struct Sh { virtual int p() { return 5; } int s; };         // a shell: its exe vftable has two slots more
struct Vk { virtual int kk(); virtual int k(int); virtual int kz(); virtual int kp(); virtual void kq(); int t; };
void qbody(int);   // kq's body: a free cdecl function taking the slot's first (undeclared) argument
struct Dd { ~Dd(); void *FUN_00401140(unsigned); int w; };   // ~Dd defined nowhere: MSVC folded it into ??_G
struct Eg { ~Eg(); int w; };                         // ~Eg defined nowhere; the exe's ??_GEg at 0x4011e0 calls EgS's body
struct Hd { ~Hd(); int w; };                         // shares the ??_G address; its own D1 is compiled (another body)
struct EgS { void FUN_00401190(); int w; };          // the destroy body at 0x401190
struct Ct { Ct(); int v; };                          // an MSVC constructor returns this
struct CtShell { void *FUN_00401130(); };             // a unit that calls it for its result
struct Pv { inline virtual int pa(); inline virtual int pb(); int pz; };   // defined nowhere; the recipe places both mid-function
struct Bf { inline virtual int bi(); inline virtual int bc(); int bz; };   // BaseFormComponent: defined nowhere, pure in the exe
struct Bd1 : Bf { int z1; };   // inherit both: the exe's vftables hold other functions (Bd1, Bd2) than each other's
struct Bd2 : Bf { int z2; };
struct Bd3 : Bf { int z3; };   // bi's exe body is compiled by no unit: the exe slot still decides (a trap stub), never an undefined name
struct Bi { void Free(void *); };   // the recipe's `__imp_?Free@Bi@@..` sits on a slot of an exe vftable
struct S1 { virtual int s1(); int a; };
struct S2 { virtual int s2(); int b; };          // s2 defined nowhere
struct M : S1, S2 { int s1() override; int s2body(); int c; };
struct ThkUse { int FUN_00401110(); };   // a unit calling the exe's adjustor thunk 0x401110 by address
struct Nv { ~Nv(); int q; };                       // not virtual: GCC emits no D0, MSVC a ??_G at 0x401160
struct NvUse { void *FUN_00401160(unsigned); };   // a unit calling that ??_G by address
void game_free(void *);   // the exe's M overrides s2 (as M::s2body)  // k: stdcall kbody(int); kz: return 0
int kbody(int);
'''
SRC = {'src/a.cpp': '#include "h.h"\nVb::~Vb() {}\nint Vb::a() { return 1; }\nint Der::a() { return 2; }\n'
                    'int Shell::impl() { return y; }\nint Shell::impl2(Agg p) { return p.e; }\n'
                    'int Shell::impl3(int p, int q) { return p + q; }\nint Shell::impl4() { return -y; }\n'
                    'void *Shell::del(unsigned f) { return f ? this : 0; }\nint Vd::g() { return 3; }\n'
                    'Gd::~Gd() {}\nint Vp::h() { return 4; }\nVd *mk_vd() { return new Vd; }\n'
                    'Gd *mk_gd() { return new Gd; }\nVp *mk_vp() { return new Vp; }\nSh *mk_sh() { return new Sh; }\n'
                    'int Vk::kk() { return 6; }\nint kbody(int q) { return q + 1; }\nVk *mk_vk() { return new Vk; }\n'
                    'int g_q;\nvoid qbody(int q) { g_q = q; }\n'
                    'int S1::s1() { return 1; }\nint M::s1() { return 2; }\nint M::s2body() { return c; }\n'
                    'M *mk_m() { return new M; }\n'
                    'Ct::Ct() { v = 7; }\nvoid *use_ct(CtShell *p) { return p->FUN_00401130(); }\n'
                    'void *Dd::FUN_00401140(unsigned f) { return f ? this : 0; }\nvoid del_dd(Dd *p) { delete p; }\n'
                    'void del_eg(Eg *p) { delete p; }\nHd::~Hd() { w = 0; }\nvoid EgS::FUN_00401190() { w = 1; }\n'
                    'void del_hd(Hd *p) { delete p; }\n'
                    'Vb *mk_vb() { return new Vb; }\nDer *mk_der() { return new Der; }\n'
                    'int call_thk(ThkUse *p) { return p->FUN_00401110(); }\n'
                    'Nv::~Nv() { q = 0; }\nvoid game_free(void *p) { g_q = p != 0; }\n'
                    'void *call_nv(NvUse *p) { return p->FUN_00401160(1); }\n'
                    'Pv *mk_pv() { return new Pv; }\nBd1 *mk_b1() { return new Bd1; }\nBd2 *mk_b2() { return new Bd2; }\nBd3 *mk_b3() { return new Bd3; }\nvoid call_bi(Bi *p) { p->Free(0); }\n'
                    'int Vu::u0() { return 10; }\nint Vu::u1() { return 11; }\nVu *mk_vu() { return new Vu; }\n'
                    'inline int Vc::c0() { return Vc::hc(); }\ninline int Vc::c1() { return 12; }\nVc *mk_vc() { return new Vc; }\n'
                    'int Shell::implw() { return 16; }\nint Wh::w1() { return 17; }\nint Wh::w0() { return 13; }\nint Wd::w0() { return 14; }\nWh *mk_wh() { return new Wh; }\nWd *mk_wd() { return new Wd; }\n'
                    'int Vh::g0() { return 8; }\nint Shell::implh() { return 9; }\nVh *mk_vh() { return new Vh; }\n'}
F = lambda a, s: {'address': '0x%08x' % a, 'symbol': s, 'size': 8}   # noqa: E731


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)
    return cond


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        raise SystemExit('FAILED: %s\n%s%s' % (' '.join(cmd), r.stdout, r.stderr))
    return r.stdout


def build_exe(rd, where):
    td = {n: rd.put(struct.pack('<II', 0x0101d000, 0) + n.encode() + b'\0')
          for n in ('.?AVVb@@', '.?AVDer@@', '.?AVVd@@', '.?AVVp@@', '.?AVSh@@', '.?AVVk@@', '.?AUM@@', '.?AVPv@@', '.?AVVi@@', '.?AVVh@@', '.?AVVu@@', '.?AVVc@@', '.?AVWh@@', '.?AVWd@@', '.?AVBf@@', '.?AVBd1@@', '.?AVBd2@@', '.?AVBd3@@')}

    def chd(names):
        bs = [rd.words(td[n], 0, 0, 0xffffffff, 0, 0x40, 0) for n in names]
        return rd.words(0, 0, len(bs), rd.words(*bs))

    def vft(cls, h, slots):
        col = rd.words(0, 0, 0, td[cls], h)
        rd.words(col)
        v = rd.here()
        rd.words(*slots)
        return v
    # MSVC order: ??_G, a, b, c, d, f (e is missing: the exe vftable is shorter)
    where['Vb'] = vft('.?AVVb@@', chd(['.?AVVb@@']), [0x401008, 0x401010, 0x401020, 0x401030, 0x401060, 0x401038])
    where['Der'] = vft('.?AVDer@@', chd(['.?AVDer@@', '.?AVVb@@']),
                       [0x401050, 0x401040, 0x401028, 0x401030, 0x401060, 0x401038])
    # Vd: its ??_G is the decomp's Shell::del(unsigned); Vp's placeholder v00 is Gd's ??_G
    where['Vd'] = vft('.?AVVd@@', chd(['.?AVVd@@']), [0x401080, 0x401088])
    where['Vp'] = vft('.?AVVp@@', chd(['.?AVVp@@']), [0x401090, 0x401098])
    # Sh: p, then Shell::impl's address and an address no compiled unit defines
    where['Sh'] = vft('.?AVSh@@', chd(['.?AVSh@@']), [0x4010b0, 0x401020, 0x401060])
    where['Vk'] = vft('.?AVVk@@', chd(['.?AVVk@@']), [0x4010c0, 0x4010c8, 0x4010d0, 0x4010e0, 0x4010f0])
    # Pv: pa is a `_purecall` slot (the provider row), pb holds Shell::impl; Vi's slot 0 is a thunk
    # `mov ecx, 0x00500000; jmp qbody` (a heap's free)
    where['Pv'] = vft('.?AVPv@@', chd(['.?AVPv@@']), [0x4010e0, 0x401020])
    where['Vi'] = vft('.?AVVi@@', chd(['.?AVVi@@']), [0x4010f8, 0x4010f0])
    # Vh: g0, then Shell::implh; the units.json of another unit names `Vh::hh` as a function at Shell::impl's address
    where['Vh'] = vft('.?AVVh@@', chd(['.?AVVh@@']), [0x4011a0, 0x4011a0, 0x4011a8])
    # Wh, Wd (W32): both vtables name Wh::hw; the exe's Wh holds Shell::implh there, its Wd another function
    where['Wh'] = vft('.?AVWh@@', chd(['.?AVWh@@']), [0x4011b0, 0x401180, 0x4011a8])
    where['Wd'] = vft('.?AVWd@@', chd(['.?AVWd@@', '.?AVWh@@']), [0x4011b8, 0x401180, 0x401198])
    # Vu, Vc: u0 / c0, then Shell::implh (0x4011a8); the unit's own units.json entry names hu and hc as Shell::impl's
    where['Vu'] = vft('.?AVVu@@', chd(['.?AVVu@@']), [0x4011c0, 0x4011d0, 0x4011a8])
    where['Vc'] = vft('.?AVVc@@', chd(['.?AVVc@@']), [0x4011c8, 0x4011d8, 0x4011a8])
    # Bf: both virtuals pure; Bd1 / Bd2 (derived, inheriting both names) hold real functions, different per class
    where['Bf'] = vft('.?AVBf@@', chd(['.?AVBf@@']), [0x4010e0, 0x4010e0])
    where['Bd1'] = vft('.?AVBd1@@', chd(['.?AVBd1@@', '.?AVBf@@']), [0x401020, 0x401028])
    where['Bd2'] = vft('.?AVBd2@@', chd(['.?AVBd2@@', '.?AVBf@@']), [0x401010, 0x4011a8])
    where['Bd3'] = vft('.?AVBd3@@', chd(['.?AVBd3@@', '.?AVBf@@']), [0x401060, 0x401020])
    hm = chd(['.?AUM@@'])
    where['M'] = vft('.?AUM@@', hm, [0x401120])
    col = rd.words(0, 8, 0, td['.?AUM@@'], hm)   # M's vftable {for S2} at +8: an adjustor thunk at 0x401110
    rd.words(col)
    where['M2'] = rd.here()
    rd.words(0x401110)
    rd.words(0)


def main():
    os.environ['FNVNX_VTFILL'] = '0'   # the builder's own checks; VTFILL's are in test_d0slot's VTFILL section
    with tempfile.TemporaryDirectory() as t:
        t = os.environ.get("FNVNX_TEST_KEEP") or t
        exe = os.path.join(t, 'Synthetic.exe')
        where = {}
        # .text: int3, except Vk::kz's body at 0x4010d0, `xor eax, eax; ret` (no unit defines it)
        test_exevft.make_pe(exe, lambda rd: build_exe(rd, where), {
            0x4010d0: b'\x33\xc0\xc3',                              # xor eax, eax; ret
            0x4010f8: b'\xb9' + (0x500000).to_bytes(4, 'little') + b'\xe9' +
                      (0x4010f0 - 0x401102).to_bytes(4, 'little', signed=True),   # mov ecx, imm; jmp qbody
            # ??_GEg: push esi; mov esi, ecx; call EgS body; test byte [esp+8], 1; je; push esi; call delete;
            # add esp, 4; mov eax, esi; pop esi; ret 4
            0x4011e0: b'\x56\x8b\xf1\xe8' + (0x401190 - 0x4011e8).to_bytes(4, 'little', signed=True) +
                      b'\xf6\x44\x24\x08\x01\x74\x09\x56\xe8' +
                      (0x401170 - 0x4011f6).to_bytes(4, 'little', signed=True) + b'\x83\xc4\x04\x8b\xc6\x5e\xc2\x04\x00',
            0x401110: b'\x83\xe9\x08\xe9' + (0x401100 - 0x401118).to_bytes(4, 'little', signed=True)})  # sub ecx, 8; jmp
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
        units = {'units': [
            {'source': 'src/a.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
                F(0x401000, '??1Vb@@UAE@XZ'), F(0x401008, '??_GVb@@UAEPAXI@Z'), F(0x401010, '?a@Vb@@UAEHXZ'),
                F(0x401020, '?impl@Shell@@QAEHXZ'), F(0x401030, '?impl2@Shell@@QAEHUAgg@@@Z'), F(0x401038, '?impl3@Shell@@QAEHHH@Z'),
                F(0x401040, '?a@Der@@UAEHXZ'), F(0x401070, '?mk_vb@@YAPAUVb@@XZ'), F(0x401078, '?mk_der@@YAPAUDer@@XZ'),
                F(0x401080, '?g@Vd@@UAEHXZ'), F(0x401088, '?del@Shell@@QAEPAXI@Z'), F(0x401090, '?h@Vp@@UAEHXZ'),
                F(0x401098, '??_GGd@@UAEPAXI@Z'), F(0x4010a0, '??1Gd@@UAE@XZ'), F(0x401028, '?impl4@Shell@@QAEHXZ'),
                F(0x4010c0, '?kk@Vk@@UAEHXZ'), F(0x4010c8, '?kbody@@YGHH@Z'), F(0x4010f0, '?qbody@@YAXH@Z'),
                F(0x401120, '?s1@M@@UAEHXZ'), F(0x401100, '?s2body@M@@QAEHXZ'), F(0x401128, '?s1@S1@@UAEHXZ'),
                F(0x401130, '??0Ct@@QAE@XZ'), F(0x401138, '?use_ct@@YAPAXPAUCtShell@@@Z'),
                F(0x401140, '?FUN_00401140@Dd@@QAEPAXI@Z'), F(0x401148, '?del_dd@@YAXPAUDd@@@Z'),
                F(0x401110, '?s2@M@@W7AEHXZ'), F(0x401150, '?call_thk@@YAHPAUThkUse@@@Z'),
                F(0x401160, '??_GNv@@QAEPAXI@Z'), F(0x401170, '?game_free@@YAXPAX@Z'),
                F(0x401178, '?call_nv@@YAPAXPAUNvUse@@@Z'), F(0x4011a0, '?g0@Vh@@UAEHXZ'),
                F(0x4011a8, '?implh@Shell@@QAEHXZ'), F(0x4011c0, '?u0@Vu@@UAEHXZ'),
                F(0x4011c8, '?c0@Vc@@UAEHXZ'),
                F(0x4011d0, '?u1@Vu@@UAEHXZ'), F(0x4011d8, '?c1@Vc@@UAEHXZ'),
                F(0x401190, '?FUN_00401190@EgS@@QAEXXZ'), F(0x4011b0, '?w0@Wh@@UAEHXZ'), F(0x401180, '?w1@Wh@@UAEHXZ'),
                F(0x4011b8, '?w0@Wd@@UAEHXZ'), F(0x401198, '?implw@Shell@@QAEHXZ')],
             'symbols': {'??_7Vb@@6B@': '0x%08x' % where['Vb'], '??_7Der@@6B@': '0x%08x' % where['Der'],
                         '??_7Vd@@6B@': '0x%08x' % where['Vd'], '??_7Vp@@6B@': '0x%08x' % where['Vp'],
                         '??_7Sh@@6B@': '0x%08x' % where['Sh'], '??_7Vk@@6B@': '0x%08x' % where['Vk'],
                         '??_7M@@6BS1@@@': '0x%08x' % where['M'], '??_7M@@6BS2@@@': '0x%08x' % where['M2'],
                         '?FUN_00401130@CtShell@@QAEPAXXZ': '0x00401130', '??_GDd@@QAEPAXI@Z': '0x00401140',
                         '?FUN_00401110@ThkUse@@QAEHXZ': '0x00401110', '?FUN_00401160@NvUse@@QAEPAXI@Z': '0x00401160',
                         '??3@YAXPAX@Z': '0x00401170', '??_7Pv@@6B@': '0x%08x' % where['Pv'],
                         '??_7Bf@@6B@': '0x%08x' % where['Bf'], '??_7Bd1@@6B@': '0x%08x' % where['Bd1'],
                         '??_7Bd2@@6B@': '0x%08x' % where['Bd2'], '??_7Bd3@@6B@': '0x%08x' % where['Bd3'], '?bi@Bf@@UAEHXZ': '0x00401022',
                         '?bc@Bf@@UAEHXZ': '0x00401029', '?pa@Pv@@UAEHXZ': '0x004010e8', '?pb@Pv@@UAEHXZ': '0x004010ec',
                         '__imp_?Free@Bi@@QAEXPAX@Z': '0x%08x' % where['Vi'], '??_7Vh@@6B@': '0x%08x' % where['Vh'],
                         '??_7Wh@@6B@': '0x%08x' % where['Wh'], '??_7Wd@@6B@': '0x%08x' % where['Wd'],
                         '??_7Vu@@6B@': '0x%08x' % where['Vu'], '?hu@Vu@@UAEHXZ': '0x00401020',
                         '??_7Vc@@6B@': '0x%08x' % where['Vc'], '?hc@Vc@@UAEHXZ': '0x00401020',
                         '??_GEg@@QAEPAXI@Z': '0x004011e0', '??_GHd@@QAEPAXI@Z': '0x004011e0'}},
            {'source': 'src/d.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [   # not compiled: d's body
                F(0x401060, '?dbody@Shell@@QAEHXZ')], 'symbols': {'?hh@Vh@@QAEHXZ': '0x00401020', '?hw@Wh@@QAEHXZ': '0x00401020'}},
        ]}
        import json
        json.dump(units, open(os.path.join(t, 'units.json'), 'w'))
        with open(os.path.join(t, 'objects.tsv'), 'w') as f:
            for rel in SRC:
                f.write('%s\t%s\tok\n' % (rel, objs[rel]))
        res = {}
        for name, exe_arg in (('none', 'none'), ('exe', exe)):
            out, fixed = os.path.join(t, 'out-' + name), os.path.join(t, name, 'fixed')
            run([sys.executable, FNVLINK, 'resolve', os.path.join(t, 'units.json'), os.path.join(t, 'objects.tsv'),
                 out, '--imports', 'none', '--exe', exe_arg])
            run([sys.executable, FNVLINK, 'apply', out, fixed])
            chk = run([sys.executable, FNVLINK, 'check', out, fixed])
            refs = {}
            for line in open(os.path.join(out, 'refs.tsv')):
                f = line.rstrip('\n').split('\t')
                if not line.startswith('#'):
                    refs[f[1]] = f
            res[name] = (out, fixed, refs, chk)
        out, fixed, refs, chk = res['exe']
        b = refs.get('_ZN2Vb1bEv')
        check(b is not None and b[2] == 'vslot-exe' and b[5] == '__fnv_00401020',
              'b bound to the native definition at the exe slot: %s' % (b,))
        nb = res['none'][2].get('_ZN2Vb1bEv')
        check(nb is not None and nb[2] == 'vslot-unresolved', 'without the exe b stays a trap: %s' % (nb,))
        for n in ('_ZN2Vb1cEi', '_ZN2Vb1dEv', '_ZN2Vb1eEv'):
            r = refs.get(n)
            check(r is not None and r[2] == 'vslot-unresolved', '%s stays a trap: %s' % (n, r))
        gaps = [l.rstrip('\n').split('\t') for l in open(os.path.join(out, 'slot_gaps.tsv')) if not l.startswith('#')]
        why = {}
        for g in gaps:
            why.setdefault(g[2], set()).add(g[7].split(' (')[0])
        check(len(why.get('_ZN2Vb1cEi') or ()) == 1 and next(iter(why['_ZN2Vb1cEi'])).startswith(
            'abi: aggregate or varargs argument 1 differs'), 'c: abi gap %s'
              % why.get('_ZN2Vb1cEi'))
        check(why.get('_ZN2Vb1dEv') == {'not compiled'}, 'd: not compiled %s' % why.get('_ZN2Vb1dEv'))
        check(why.get('_ZN2Vb1eEv') == {'past the exe vftable\'s slots'}, 'e: past the slots %s' % why.get('_ZN2Vb1eEv'))
        fill = [l.rstrip('\n').split('\t') for l in open(os.path.join(out, 'slot_fill.tsv')) if not l.startswith('#')]
        fb = {r[2]: r for r in fill if not r[5].startswith('per slot')}
        check({'_ZN2Vb1bEv', '_ZN2Vb1fEv'} <= set(fb) and fb['_ZN2Vb1bEv'][5] == 'declaring class' and
              fb['_ZN2Vb1bEv'][6] == 'same' and '_ZTV2Vb+8:3' in fb['_ZN2Vb1bEv'][7] and
              '_ZTV3Der+8:3' in fb['_ZN2Vb1bEv'][7], 'slot_fill.tsv: b by Vb\'s vtable, GCC slot 3 (exe slot 2): %s'
              % fill)
        per = [r for r in fill if r[5].startswith('per slot') and r[2] == '_ZN2Vb1bEv']
        check(len(per) == 1 and per[0][2] == '_ZN2Vb1bEv' and per[0][4] == '__fnv_00401028' and
              per[0][7] == '_ZTV3Der+8:3', 'Der\'s slot 3 gets the exe\'s override per slot: %s' % per)
        zb_ = vtables.Obj(os.path.join(fixed, 'zz_bridges.o'))
        de = zb_.ents('_ZTV3Der') if '_ZTV3Der' in zb_.defs else []
        check(len(de) >= 6 and de[5].sym == '__fnv_00401028' and zb_.bind('_ZTV3Der') == 'global',
              'a strong copy of Der\'s vtable with __fnv_00401028 in slot 3: %s' % (de,))
        check('_ZN2Vb1fEv' in fb and fb['_ZN2Vb1fEv'][4] == '__fnv_00401038' and
              fb['_ZN2Vb1fEv'][6].startswith('the definition reads more arguments'),
              'f (a placeholder declaration of a body with two arguments) bound and listed: %s' % fb.get('_ZN2Vb1fEv'))
        for n, want in (('_ZN2VdD1Ev', '__fnvnx_vflag_00401088_0'), ('_ZN2VdD0Ev', '__fnvnx_vflag_00401088_1'),
                        ('_ZN2Vp3v00Ev', '__fnvnx_vgdel_00401098')):
            r = refs.get(n)
            check(r is not None and r[2] == 'vslot-exe' and r[5] == want, '%s -> %s: %s' % (n, want, r))
        bridges_ = open(os.path.join(out, 'bridges.S')).read()
        rz = refs.get('_ZN2Vk2kzEv')
        check(rz is not None and rz[5] == '__fnvnx_vconst_004010d0' and
              '__fnvnx_vconst_004010d0:\n    mov w0, #0\n    ret' in bridges_,
              'kz (the exe\'s `xor eax, eax; ret`, compiled nowhere) -> a native `return 0` stub: %s' % (rz,))
        rq = refs.get('_ZN2Vk2kqEv')
        check(rq is not None and rq[5].startswith('__fnvnx_br_004010f0_') and
              ('%s:\n    mov x0, x1\n    b __fnv_004010f0' % rq[5]) in bridges_,
              'kq (a placeholder slot; its body is the free cdecl qbody(int)) through a bridge x0 <- x1: %s' % (rq,))
        rs2 = refs.get('_ZN2S22s2Ev')
        check(rs2 is not None and rs2[5] == '__fnvnx_vadj_00401110' and
              '__fnvnx_vadj_00401110:\n    sub w0, w0, #8\n    b __fnv_00401100' in bridges_,
              'S2::s2 in M\'s secondary vtable: the exe\'s adjustor thunk (sub ecx, 8; jmp M::s2body) -> '
              'sub w0, w0, #8; b __fnv_00401100: %s' % (rs2,))
        rt = refs.get('_ZN6ThkUse12FUN_00401110Ev')
        trow = {l.split('\t')[0]: l.split('\t') for l in open(os.path.join(out, 'addrmap.tsv'))
                if not l.startswith('#')}.get('00401110', [''] * 6)
        check(rt is not None and rt[2] == 'code-adjthunk' and rt[5] == '__fnv_00401110' and
              '__fnv_00401110:\n    sub w0, w0, #8\n    b __fnv_00401100' in bridges_ and trow[1] == 'F' and
              trow[2] == '__fnv_00401110' and '__fnv_00401110' not in chk,
              'a call bound to the placed adjustor thunk 0x401110 (M::s2 `W7`, no GCC counterpart) -> its stub '
              'sub w0, w0, #8; b __fnv_00401100, an F row, defined in the trial link: %s %s' % (rt, trow))
        rn = refs.get('_ZN5NvUse12FUN_00401160Ej')
        check(rn is not None and rn[2] == 'code-sddstub' and rn[5] == '__fnv_00401160' and
              '__fnv_00401160:\n    stp x29, x30, [sp, #-32]!' in bridges_ and
              '    bl _ZN2NvD1Ev\n    tbz w20, #0, 1f\n    mov w0, w19\n    bl __fnv_00401170\n' in bridges_ and
              '__fnv_00401160' not in chk,
              'a call bound to the non-virtual ??_GNv (no GCC D0) -> a stub: D1, then the global delete if flags & 1: '
              '%s' % (rn,))
        rpa, rpb = refs.get('_ZN2Pv2paEv'), refs.get('_ZN2Pv2pbEv')
        check(rpb is not None and rpb[2] == 'vslot-exe' and rpb[5] == '__fnv_00401020',
              'Pv::pb (extern-text: the recipe\'s addresses lie inside other functions) -> the exe slot, '
              'Shell::impl: %s' % (rpb,))
        # Bf::bi / bc (extern-text, defined nowhere, pure in Bf's own exe vftable): the derived classes inherit both and
        # the exe holds real functions in their vftables, different per class. Bd1, Bd2: the per-slot targets; Bd3's
        # slot 0 is a body no unit compiles, so the fill leaves it and the copy takes the exe slot (a trap stub)
        import re as re_
        blk = lambda z_: (re_.search(r'%s:\n((?:    \.word [^\n]*\n)+)' % z_, bridges_) or [None, ''])[1].split('\n')  # noqa: E731
        w1, w2, w3 = blk('_ZTV3Bd1'), blk('_ZTV3Bd2'), blk('_ZTV3Bd3')
        # the exe's slot 0 (0x401020 / 0x401010) is int3 padding here, not a deleting destructor: the old byte search
        # ran into 0x4011e0's ??_G and made these minus-one (FNVNX_DELDTOR_STRICT); exe order, slot by slot
        check('__fnv_00401020' in w1[2] and '__fnv_00401028' in w1[3] and
              '__fnv_00401010' in w2[2] and '__fnv_004011a8' in w2[3],
              'Bd1 / Bd2: Bf::bi and bc take the exe functions of each class (per slot), exe order: %s %s' % (w1, w2))
        check(len(w3) > 3 and 'vtrap' in w3[2] and '__fnv_00401020' in w3[3] and
              all('_ZN2Bf2' not in x for x in w3),
              'Bd3: Bf::bi (the exe body no unit compiles) -> a trap stub for the exe address, Bf::bc -> the exe '
              'function; no undefined Bf name is left in the copy: %s' % (w3,))
        rbi = refs.get('_ZN2Bi4FreeEPv')
        check(rbi is not None and rbi[2] == 'code' and rbi[5] == '__fnvnx_vthis_004010f8' and
              '__fnvnx_vthis_004010f8:\n    movz x0, #0x0\n    movk x0, #0x50, lsl #16\n    b __fnv_004010f0' in bridges_,
              'an __imp_ name on slot 0 of an exe vftable holding `mov ecx, 0x500000; jmp qbody` -> x0 = 0x500000, '
              'b qbody: %s' % (rbi,))
        rt0 = res['none'][2].get('_ZN6ThkUse12FUN_00401110Ev')
        check(rt0 is not None and rt0[2] == 'code-defmissing', 'without the exe: code-defmissing %s' % (rt0,))
        rc = refs.get('_ZN7CtShell12FUN_00401130Ev')
        check(rc is not None and rc[5] == '__fnvnx_cthis_00401130' and
              '__fnvnx_cthis_00401130:\n    stp x29, x30, [sp, #-32]!\n    mov x29, sp\n    str x0, [sp, #16]\n'
              '    bl __fnv_00401130\n    ldr x0, [sp, #16]' in bridges_,
              'a reference using the constructor 0x00401130\'s result -> the stub returning this: %s' % (rc,))
        crow = {l.split('\t')[0]: l.split('\t') for l in open(os.path.join(out, 'addrmap.tsv'))
                if not l.startswith('#')}.get('00401130', [''] * 6)
        check(crow[1] == 'C' and crow[2] == '__fnvnx_cthis_00401130', 'the C row names the stub: %s' % (crow,))
        rd = refs.get('_ZN2DdD1Ev')
        check(rd is not None and rd[2] == 'dtor-by-deldtor' and rd[5] == '__fnvnx_vflag_00401140_0',
              'Dd\'s D1 (defined nowhere; the unit binds ??_GDd to a (this, flags) function) -> ??_G(this, 0): %s'
              % (rd,))
        re_, re0 = refs.get('_ZN2EgD1Ev'), res['none'][2].get('_ZN2EgD1Ev')
        check(re_ is not None and re_[2] == 'dtor-by-exe-deldtor' and re_[4] == '00401190' and
              re_[5] == '__fnv_00401190' and re0 is not None and re0[2] == 'ctor-dtor-alias' and
              re0[5] == '_ZN2HdD1Ev',
              'Eg\'s D1 (the exe\'s ??_GEg calls 0x401190, a native plain body) -> __fnv_00401190, not the D1 of Hd '
              'that shares the ??_G address; without the exe the alias stays: %s / %s' % (re_, re0))
        rh1 = refs.get('_ZN2HdD1Ev')
        check(rh1 is None or rh1[2] not in ('dtor-by-exe-deldtor',), 'Hd\'s own D1 is untouched: %s' % (rh1,))
        check(res['exe'][0] and exevft.deleting_dtor_callee(exe, 0x4011e0) == 0x401190 and
              exevft.deleting_dtor_callee(exe, 0x401088) is None and exevft.deleting_dtor_callee(exe, 0x401170) is None,
              'exevft.deleting_dtor_callee: 0x4011e0 -> 0x401190, a non-??_G shape -> None')
        rh, rh0 = refs.get('_ZN2Vh2hhEv'), res['none'][2].get('_ZN2Vh2hhEv')
        check(rh is not None and rh[2] == 'vslot-exe' and rh[5] == '__fnv_004011a8' and
              rh0 is not None and rh0[2] == 'code' and rh0[5] == '__fnv_00401020',
              'Vh::hh, whose name another unit binds to 0x401020 (S2d-vslot): the exe slot\'s 0x4011a8 decides, and '
              'without the exe the name keeps its address: %s / %s' % (rh, rh0))
        ru, ru0 = refs.get('_ZN2Vu2huEv'), res['none'][2].get('_ZN2Vu2huEv')
        check(ru is not None and ru[2] == 'vslot-exe' and ru[5] == '__fnv_004011a8' and
              ru0 is not None and ru0[3].startswith('unit/') and ru0[2] == 'code' and ru0[5] == '__fnv_00401020',
              'W20 (b) Vu::hu, bound by the unit\'s own recipe and called by no code: the exe slot\'s 0x4011a8 '
              'decides, and without the exe the name keeps its address: %s / %s' % (ru, ru0))
        rc_, rc0 = refs.get('_ZN2Vc2hcEv'), res['none'][2].get('_ZN2Vc2hcEv')
        check(rc_ is not None and rc_[3].startswith('unit/') and rc_[2] == 'code' and rc_[5] == '__fnv_00401020' and
              rc0 is not None and rc0[5] == '__fnv_00401020',
              'W20 (a) Vc::hc, bound by the unit\'s own recipe and called by Vc::c0: the symbol keeps 0x401020 '
              '(with or without the exe): %s / %s' % (rc_, rc0))
        zbc = vtables.Obj(os.path.join(fixed, 'zz_bridges.o'))
        ce = zbc.ents('_ZTV2Vc') if '_ZTV2Vc' in zbc.defs else []
        # D26DS: the fixture's exe slot 0 is a deleting destructor that the unit defines, so the copy is minus-one
        # (D1 / D0 = the flags stubs) and exe slot 2 is native slot 3 (word 5); in the exe's order it is word 4
        ci = 5 if len(ce) > 3 and (ce[2].sym or '').startswith('__fnvnx_vflag_') else 4
        check(len(ce) > ci and ce[ci].sym == '__fnv_004011a8' and zbc.bind('_ZTV2Vc') == 'global',
              'W20 (a) Vc\'s vtable: a strong copy whose hc slot holds the exe\'s __fnv_004011a8: %s' % (ce,))
        cf = [r for r in fill if r[2] == '_ZN2Vc2hcEv']
        check(len(cf) == 1 and cf[0][4] == '__fnv_004011a8' and cf[0][5].startswith('per slot (exe vftable') and
              cf[0][7] == '_ZTV2Vc+8:2',
              'W20 (a) slot_fill.tsv lists the per-slot target: %s' % cf)
        rw, rw0 = refs.get('_ZN2Wh2hwEv'), res['none'][2].get('_ZN2Wh2hwEv')
        check(rw is not None and rw[2] == 'vslot-exe' and rw[5] == '__fnv_004011a8' and
              rw0 is not None and rw0[2] == 'code' and rw0[5] == '__fnv_00401020',
              'W32 Wh::hw (a gref name shared by Wh\'s and Wd\'s vtables): the declaring class\'s exe slot 0x4011a8 '
              'decides the symbol, without the exe the name keeps its address: %s / %s' % (rw, rw0))
        zbw = vtables.Obj(os.path.join(fixed, 'zz_bridges.o'))
        we = zbw.ents('_ZTV2Wd') if '_ZTV2Wd' in zbw.defs else []
        wi = 5 if len(we) > 3 and (we[2].sym or '').startswith('__fnvnx_vflag_') else 4   # D26DS, as for Vc
        check(len(we) > wi and we[wi].sym == '__fnv_00401198' and zbw.bind('_ZTV2Wd') == 'global',
              'W32 Wd\'s vtable: a strong copy whose hw slot holds the exe Wd\'s override __fnv_00401198: %s' % (we,))
        wb = vtables.Obj(os.path.join(fixed, 'src_a.cpp.o'))
        wh = wb.ents('_ZTV2Wh') if '_ZTV2Wh' in wb.defs else []
        check(len(wh) >= 5 and wh[4].sym == '__fnv_004011a8',
              'W32 Wh\'s own vtable keeps the symbol\'s address: %s' % (wh,))
        wf = [r for r in fill if r[2] == '_ZN2Wh2hwEv' and r[5].startswith('per slot')]
        check(len(wf) == 1 and wf[0][4] == '__fnv_00401198' and wf[0][7] == '_ZTV2Wd+8:2',
              'W32 slot_fill.tsv lists the per-slot target of Wd: %s' % wf)
        wam = {l.split('\t')[0]: l.split('\t') for l in open(os.path.join(out, 'addrmap.tsv'))
               if not l.startswith('#')}.get('%08x' % where['Wd'], [''] * 6)
        check(wam[4] == 'zz_bridges.o', 'W32 the V row of Wd names the strong copy: %s' % wam[4])
        rk = refs.get('_ZN2Vk1kEi')
        check(rk is not None and rk[2] == 'vslot-exe' and rk[5].startswith('__fnvnx_br_004010c8_') and
              ('%s:\n    mov x0, x1\n    b __fnv_004010c8' % rk[5]) in bridges_,
              'k (a stdcall stand-in taking the slot\'s int) through a bridge x0 <- x1: %s' % (rk,))
        check('__fnvnx_vflag_00401088_1:\n    mov w1, #1\n    b __fnv_00401088' in bridges_ and
              '__fnvnx_vgdel_00401098:\n    tbz w1, #0, 1f\n    b __fnv_00401098\n1:  b __fnv_00401098_D1' in bridges_,
              'flag and deleting-destructor stubs in bridges.S')
        # Sh's vtable extended: a strong copy in zz_bridges.o with the exe's two extra slots
        zb = vtables.Obj(os.path.join(fixed, 'zz_bridges.o'))
        se = zb.ents('_ZTV2Sh') if '_ZTV2Sh' in zb.defs else []
        check(len(se) == 5 and se[3].sym == '__fnv_00401020' and (se[4].sym or '').startswith('__fnvnx_vtrap_') and
              zb.bind('_ZTV2Sh') == 'global', 'Sh extended: p, __fnv_00401020, a trap: %s' % (se,))
        am = {l.split('\t')[0]: l.split('\t') for l in open(os.path.join(out, 'addrmap.tsv')) if not l.startswith('#')}
        check(am.get('%08x' % where['Sh'], [''] * 6)[4] == 'zz_bridges.o', 'the V row of Sh names the extended copy')
        check('multiple definitions: 0' in chk, 'trial link: no multiple definition of _ZTV2Sh')
        o = vtables.Obj(os.path.join(fixed, 'src_a.cpp.o'))
        ents = o.ents('_ZTV2Vb')
        check(ents[2 + 3].sym == '__fnv_00401020', 'fixed object: Vb vtable slot 3 names __fnv_00401020 (%s)'
              % ents[2 + 3].sym)
        check(ents[2 + 4].sym.startswith('__fnvnx_vtrap_'), 'fixed object: slot 4 (c) still a trap (%s)' % ents[2 + 4].sym)
        bridges = open(os.path.join(out, 'bridges.S')).read()
        check(nb[5] not in bridges and refs['_ZN2Vb1cEi'][5] in bridges, 'b\'s trap stub dropped, c\'s kept')
        check('__fnv_00401020' not in chk, 'trial link: __fnv_00401020 defined')
        st = vtmetric.measure(_link_dir(t, out, fixed), exe)
        # W19: the fixture's unreferenced RTTI vftable 0x00fdf38c (exe slot 0 not a destructor, one slot) now has a
        # minus-one copy and a V row: its D1 and its slot 0 are traps (one with a native at the exe slot), +2
        # Bf's own exe vftable (pure slots) is unreferenced too: its minus-one copy's slot 0 is a trap with a native at
        # the exe address (the fixture's 0x4010e0 is also Vk::kp's), and Bd3's slot 0 the exe body no unit compiles
        # R2 (vtgate): a destructor slot that holds the CRT's _purecall (S4's provider) is that provider, not a trap: -1
        # strict deleting_dtor: Bf's unreferenced copy (slot 0 0x4010e0, int3 here) is no longer minus-one: no D1 trap, -1
        check(st['trap-slots'] == 9 and st['trap-native-at-exe'] == 2 and st['primary-shorter'] == 0,
              'vtmetric: 9 trap slots left (Sh\'s extension holds one, the unreferenced vftables\' copies three, '
              'Bd3\'s slot 0), 4 with a native at the exe slot, no short primary (%s)' % dict(st))
        check(st['slot-mismatch'] == 0 and st['slot-compared'] >= 1,
              'vtmetric: no native slot differs from the exe\'s function (%s)' % dict(st))
        st0 = vtmetric.measure(_link_dir(t, res['none'][0], res['none'][1]), exe)
        check(st0['slot-mismatch'] == 5 and st0['slot-mismatch-pairs'] == 2,
              'vtmetric without the fill: Vh::hh, Vu::hu, Vc::hc and Wh::hw (0x401020 against the exe\'s 0x4011a8) '
              'and Wd\'s hw (against 0x401198) are the mismatches (%s)' % dict(st0))
        check(st0['trap-slots'] == 19 and st0['trap-native-at-exe'] == 13 and st0['primary-shorter'] == 1,
              'vtmetric without the fill: 19 / 13, Sh short (%s)'
              % dict(st0))
    print('%d failed' % len(FAILS))
    return 1 if FAILS else 0


def _link_dir(t, out, fixed):
    """vtmetric reads <dir>/out and <dir>/fixed (a published link's layout)."""
    d = os.path.join(t, 'link-' + os.path.basename(out))
    os.makedirs(d, exist_ok=True)
    for n, p in (('out', out), ('fixed', fixed)):
        if not os.path.lexists(os.path.join(d, n)):
            os.symlink(p, os.path.join(d, n))
    return d


if __name__ == '__main__':
    sys.exit(main())
