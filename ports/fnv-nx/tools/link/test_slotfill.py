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
* Vk::kp, whose exe slot is the CRT's `_purecall` (a provider row of S4's crt_symbols.tsv), binds to the
  platform's `_purecall`;
* Vk::kq, a placeholder `virtual void kq();` whose exe body is the free cdecl `qbody(int)` (the x86 code reads the
  first stack argument, which a thiscall caller passes after this), goes through a bridge x0 <- x1;
* S2::s2 (defined nowhere) in M's secondary vtable, where the exe's M {for S2} vftable holds an adjustor thunk
  (`sub ecx, 8; jmp M::s2body`) and the native vtable's offset-to-top is -8, gets `sub w0, w0, #8; b __fnv_<a>`;
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
struct Shell { int impl(); int impl2(Agg); int impl3(int, int); void *del(unsigned); int impl4(); int y; };
struct Vd { virtual int g(); virtual ~Vd(); int z; };      // key function g; ~Vd defined nowhere
struct Gd { virtual ~Gd(); int w; };
struct Vp { virtual int h(); virtual void v00(); int u; }; // v00: a placeholder for the exe's ??_G
struct Sh { virtual int p() { return 5; } int s; };         // a shell: its exe vftable has two slots more
struct Vk { virtual int kk(); virtual int k(int); virtual int kz(); virtual int kp(); virtual void kq(); int t; };
void qbody(int);   // kq's body: a free cdecl function taking the slot's first (undeclared) argument
struct S1 { virtual int s1(); int a; };
struct S2 { virtual int s2(); int b; };          // s2 defined nowhere
struct M : S1, S2 { int s1() override; int s2body(); int c; };   // the exe's M overrides s2 (as M::s2body)  // k: stdcall kbody(int); kz: return 0
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
                    'Vb *mk_vb() { return new Vb; }\nDer *mk_der() { return new Der; }\n'}
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
          for n in ('.?AVVb@@', '.?AVDer@@', '.?AVVd@@', '.?AVVp@@', '.?AVSh@@', '.?AVVk@@', '.?AUM@@')}

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
    hm = chd(['.?AUM@@'])
    where['M'] = vft('.?AUM@@', hm, [0x401120])
    col = rd.words(0, 8, 0, td['.?AUM@@'], hm)   # M's vftable {for S2} at +8: an adjustor thunk at 0x401110
    rd.words(col)
    where['M2'] = rd.here()
    rd.words(0x401110)
    rd.words(0)


def main():
    with tempfile.TemporaryDirectory() as t:
        t = os.environ.get("FNVNX_TEST_KEEP") or t
        exe = os.path.join(t, 'Synthetic.exe')
        where = {}
        # .text: int3, except Vk::kz's body at 0x4010d0, `xor eax, eax; ret` (no unit defines it)
        test_exevft.make_pe(exe, lambda rd: build_exe(rd, where), {
            0x4010d0: b'\x33\xc0\xc3',                              # xor eax, eax; ret
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
                F(0x401120, '?s1@M@@UAEHXZ'), F(0x401100, '?s2body@M@@QAEHXZ'), F(0x401128, '?s1@S1@@UAEHXZ')],
             'symbols': {'??_7Vb@@6B@': '0x%08x' % where['Vb'], '??_7Der@@6B@': '0x%08x' % where['Der'],
                         '??_7Vd@@6B@': '0x%08x' % where['Vd'], '??_7Vp@@6B@': '0x%08x' % where['Vp'],
                         '??_7Sh@@6B@': '0x%08x' % where['Sh'], '??_7Vk@@6B@': '0x%08x' % where['Vk'],
                         '??_7M@@6BS1@@@': '0x%08x' % where['M'], '??_7M@@6BS2@@@': '0x%08x' % where['M2']}},
            {'source': 'src/d.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [   # not compiled: d's body
                F(0x401060, '?dbody@Shell@@QAEHXZ')], 'symbols': {}},
        ]}
        import json
        json.dump(units, open(os.path.join(t, 'units.json'), 'w'))
        with open(os.path.join(t, 'objects.tsv'), 'w') as f:
            for rel in SRC:
                f.write('%s\t%s\tok\n' % (rel, objs[rel]))
        crt = os.path.join(t, 'crt_symbols.tsv')   # S4's table: the exe's _purecall has a platform provider
        open(crt, 'w').write('0x004010e0\tcrt\t_purecall\tn\t0\t0\tplatform/crt: _purecall\n')
        res = {}
        for name, exe_arg in (('none', 'none'), ('exe', exe)):
            out, fixed = os.path.join(t, 'out-' + name), os.path.join(t, name, 'fixed')
            run([sys.executable, FNVLINK, 'resolve', os.path.join(t, 'units.json'), os.path.join(t, 'objects.tsv'),
                 out, '--crt-symbols', crt, '--imports', 'none', '--exe', exe_arg])
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
        check(why.get('_ZN2Vb1cEi') == {'abi: aggregate or varargs argument 1 differs'}, 'c: abi gap %s'
              % why.get('_ZN2Vb1cEi'))
        check(why.get('_ZN2Vb1dEv') == {'not compiled'}, 'd: not compiled %s' % why.get('_ZN2Vb1dEv'))
        check(why.get('_ZN2Vb1eEv') == {'past the exe vftable\'s slots'}, 'e: past the slots %s' % why.get('_ZN2Vb1eEv'))
        fill = [l.rstrip('\n').split('\t') for l in open(os.path.join(out, 'slot_fill.tsv')) if not l.startswith('#')]
        fb = {r[2]: r for r in fill if not r[5].startswith('per slot')}
        check({'_ZN2Vb1bEv', '_ZN2Vb1fEv'} <= set(fb) and fb['_ZN2Vb1bEv'][5] == 'declaring class' and
              fb['_ZN2Vb1bEv'][6] == 'same' and '_ZTV2Vb+8:3' in fb['_ZN2Vb1bEv'][7] and
              '_ZTV3Der+8:3' in fb['_ZN2Vb1bEv'][7], 'slot_fill.tsv: b by Vb\'s vtable, GCC slot 3 (exe slot 2): %s'
              % fill)
        per = [r for r in fill if r[5].startswith('per slot')]
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
        rp = refs.get('_ZN2Vk2kpEv')
        check(rp is not None and rp[2] == 'vslot-exe' and rp[5] == '_purecall',
              'kp (the exe slot is _purecall, a CRT provider) -> the platform\'s _purecall: %s' % (rp,))
        rq = refs.get('_ZN2Vk2kqEv')
        check(rq is not None and rq[5].startswith('__fnvnx_br_004010f0_') and
              ('%s:\n    mov x0, x1\n    b __fnv_004010f0' % rq[5]) in bridges_,
              'kq (a placeholder slot; its body is the free cdecl qbody(int)) through a bridge x0 <- x1: %s' % (rq,))
        rs2 = refs.get('_ZN2S22s2Ev')
        check(rs2 is not None and rs2[5] == '__fnvnx_vadj_00401110' and
              '__fnvnx_vadj_00401110:\n    sub w0, w0, #8\n    b __fnv_00401100' in bridges_,
              'S2::s2 in M\'s secondary vtable: the exe\'s adjustor thunk (sub ecx, 8; jmp M::s2body) -> '
              'sub w0, w0, #8; b __fnv_00401100: %s' % (rs2,))
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
        check(st['trap-slots'] == 7 and st['trap-native-at-exe'] == 2 and st['primary-shorter'] == 0,
              'vtmetric: 7 trap slots left (Sh\'s extension holds one), 2 with a native at the exe slot, no short '
              'primary (%s)' % dict(st))
        st0 = vtmetric.measure(_link_dir(t, res['none'][0], res['none'][1]), exe)
        check(st0['trap-slots'] == 18 and st0['trap-native-at-exe'] == 12 and st0['primary-shorter'] == 1,
              'vtmetric without the fill: 18 / 12, Sh short (%s)'
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
