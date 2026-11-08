#!/usr/bin/env python3
"""Vtable slots bound through MSVC vftables (vslots.py), on hand-written classes compiled by both compilers.

    FNV_DECOMP=<fnv_decomp checkout> FNV_MSVC9_ROOT=... FNV_MSVC9_PREFIX=... python3 tools/link/test_vslots.py

Needs MSVC 2008 under Wine through the decomp's tools/msvc.py (tools/equiv/README.md), aarch64-linux-gnu-g++,
llvm-undname, SSE2NEON; prints SKIP without the MSVC environment. No decomp content: the classes are written here.

Unit a holds the "real" declarations: Ov (overloaded virtuals f(int), f(double), f(char *), a virtual destructor in
the middle), OvD : Ov (overrides, new overloads k, an h(int) beside the inherited h()), A / B / M : A, B (multiple
inheritance, an override reached through the secondary vtable), P (pure virtual) / Q : P, U (virtuals and a
destructor defined nowhere under these names), W : V (W::~W makes GCC call V's base-object destructor, which no
unit names: MSVC inlines it). Unit b reconstructs the same image vftables as local classes whose
virtuals carry their addresses (`FUN_<addr>`), one name per slot in the image's slot order, and defines the bodies
unit a never names; unit c is a third view of Ov's vftable that disagrees with b about one slot. units.json binds
each unit's vftable names to the same image addresses.

Checks: MSVC's slot order (overloads grouped in reverse declaration order inside the introducing class, one deleting
destructor slot) against GCC's (declaration order, D1 + D0), and pair_slots pairing every GCC slot with the MSVC slot
of the same function; the joined table (overrides beat inherited slots and FUN_ names, the disagreement reported);
resolve --msvc-objs binds unit a's undefined slot symbols to the addresses unit b defines (D0 / D1 of U to the
`??_G` address, plain and _D1 variant), c's destructor slots to Ov's `??_G`, the disputed slot and A::a2 (its own
vtable's slot is unknown statically) to stubs through the image vftable slot, and without --msvc-objs none of them
reaches its target (the loose 'arity' tier even binds OvD::k(char *) to k(int)); the trial link has none of them
undefined. The image-slot stubs run under qemu-aarch64: a native target, D0 / D1 through a `(this, flags)` adapter,
a word still in the image's .text going to fnvnx_vslot_trap.
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
import sigs     # noqa: E402
import vslots   # noqa: E402

FNVLINK = os.path.join(HERE, 'fnvlink.py')
CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
# MSVC-like semantics the shim insists on (compile.sh)
SEMANTICS = getattr(layout, 'SEMANTICS', ['-frounding-math', '-fsigned-char', '-fno-strict-aliasing', '-fwrapv',
                                          '-fno-delete-null-pointer-checks', '-fno-sized-deallocation',
                                          '-ffp-contract=off'])

HEADER = r'''#pragma once
struct Ov { virtual int f(int); virtual int g(); virtual int f(double); virtual ~Ov(); virtual int f(char *);
            virtual int h(); int x; };
struct OvD : Ov { virtual int f(double); virtual int k(int); virtual int g(); virtual int k(char *); virtual int h(int); };
struct A { virtual ~A(); virtual int a1(); virtual int a2(); int a; };
struct B { virtual int b1(); virtual int b2(int); int b; };
struct M : A, B { virtual int b2(int); virtual int a1(); virtual int m(); ~M(); int c; };
struct P { virtual int p1() = 0; virtual int p2(); virtual ~P(); };
struct Q : P { virtual int p1(); ~Q(); };
struct U { virtual int key(); virtual int Unk_01(); virtual int Unk_02(int); virtual ~U(); };
struct V { virtual ~V(); virtual int v1(); int q; };
struct W : V { ~W(); int v1(); };
'''
SRC = {
    'src/a.cpp': '#include "h.h"\n'
                 'int Ov::f(int) { return 1; }\nint Ov::g() { return 2; }\nint Ov::f(double) { return 3; }\n'
                 'Ov::~Ov() {}\nint OvD::f(double) { return 13; }\nint OvD::k(int) { return 14; }\n'
                 'A::~A() {}\nint A::a1() { return 21; }\nint M::a1() { return 31; }\nint M::b2(int) { return 32; }\n'
                 'int M::m() { return 33; }\nM::~M() {}\nint P::p2() { return 41; }\nP::~P() {}\n'
                 'int Q::p1() { return 51; }\nQ::~Q() {}\nint U::key() { return 61; }\n'
                 'W::~W() {}\nint W::v1() { return 71; }\n'
                 'void *mk(int i) { switch (i) { case 0: return new OvD; case 1: return new M; case 2: return new Q;\n'
                 '    case 3: return new W; } return new U; }\n',
    'src/b.cpp': r'''struct FUN_00402000_Ov { virtual int FUN_00402040(char *); virtual int FUN_00402030(double);
    virtual int FUN_00402010(int); virtual int FUN_00402020(); virtual ~FUN_00402000_Ov(); virtual int FUN_00402050();
    int x; };
struct FUN_00402100_OvD { virtual int FUN_00402040(char *); virtual int FUN_00402130(double);
    virtual int FUN_00402010(int); virtual int FUN_00402120(); virtual ~FUN_00402100_OvD();
    virtual int FUN_00402050(); virtual int FUN_00402150(char *); virtual int FUN_00402140(int);
    virtual int FUN_00402160(int); int x; };
struct FUN_00403000_A { virtual ~FUN_00403000_A(); virtual int FUN_00403010(); virtual int FUN_00403020(); int a; };
struct FUN_00403100_B { virtual int FUN_00403110(); virtual int FUN_00403120(int); int b; };
struct FUN_00403200_M : FUN_00403000_A, FUN_00403100_B { virtual int FUN_00403120(int); virtual int FUN_00403010();
    virtual int FUN_00403230(); ~FUN_00403200_M(); int c; };
struct FUN_00404000_U { virtual int FUN_00404000(); virtual int FUN_00404010(); virtual int FUN_00404020(int);
    virtual ~FUN_00404000_U(); };
struct FUN_00405000_V { virtual ~FUN_00405000_V(); virtual int FUN_00405010(); int q; };
FUN_00405000_V::~FUN_00405000_V() {}
int FUN_00402000_Ov::FUN_00402040(char *) { return 4; }
int FUN_00402000_Ov::FUN_00402050() { return 5; }
int FUN_00402100_OvD::FUN_00402120() { return 12; }
int FUN_00402100_OvD::FUN_00402150(char *) { return 15; }
int FUN_00402100_OvD::FUN_00402160(int) { return 16; }
int FUN_00403000_A::FUN_00403020() { return 22; }
int FUN_00403100_B::FUN_00403110() { return 23; }
int FUN_00404000_U::FUN_00404010() { return 62; }
int FUN_00404000_U::FUN_00404020(int) { return 63; }
FUN_00404000_U::~FUN_00404000_U() {}
void *mkb(int i) { switch (i) { case 0: return new FUN_00402000_Ov; case 1: return new FUN_00402100_OvD;
    case 2: return new FUN_00403200_M; case 3: return new FUN_00405000_V; } return new FUN_00404000_U; }
''',
    'src/c.cpp': r'''struct FUN_00402900_Ov { virtual int FUN_00402040(char *); virtual int FUN_00402030(double);
    virtual int FUN_00402010(int); virtual int FUN_00402020(); virtual ~FUN_00402900_Ov(); virtual int FUN_00402990();
    int x; };
int FUN_00402900_Ov::FUN_00402040(char *) { return 94; }   // key function: GCC emits the vtable here
int FUN_00402900_Ov::FUN_00402990() { return 9; }
void *mkc() { return new FUN_00402900_Ov; }
''',
}
F = lambda a, s: {'address': '0x%08x' % a, 'symbol': s, 'size': 16}   # noqa: E731
OV, OVD, AV, MA, MB, PV, QV, UV, BV = 0x01000100, 0x01000120, 0x01000300, 0x01000320, 0x01000340, 0x01000400, \
    0x01000420, 0x01000500, 0x01000360
VV, WV = 0x01000600, 0x01000620
UNITS = {'units': [
    {'source': 'src/a.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        F(0x402010, '?f@Ov@@UAEHH@Z'), F(0x402020, '?g@Ov@@UAEHXZ'), F(0x402030, '?f@Ov@@UAEHN@Z'),
        F(0x402060, '??1Ov@@UAE@XZ'), F(0x402070, '??_GOv@@UAEPAXI@Z'), F(0x402130, '?f@OvD@@UAEHN@Z'),
        F(0x402140, '?k@OvD@@UAEHH@Z'), F(0x403000, '??1A@@UAE@XZ'), F(0x403010, '?a1@A@@UAEHXZ'),
        F(0x403210, '?a1@M@@UAEHXZ'), F(0x403220, '?b2@M@@UAEHH@Z'), F(0x403230, '?m@M@@UAEHXZ'),
        F(0x403240, '??1M@@UAE@XZ'), F(0x403310, '?p2@P@@UAEHXZ'), F(0x403320, '??1P@@UAE@XZ'),
        F(0x403410, '?p1@Q@@UAEHXZ'), F(0x403420, '??1Q@@UAE@XZ'), F(0x404000, '?key@U@@UAEHXZ'),
        F(0x405130, '??1W@@UAE@XZ'), F(0x405140, '?v1@W@@UAEHXZ')],
     'symbols': {'??_7Ov@@6B@': OV, '??_7OvD@@6B@': OVD, '??_7A@@6B@': AV, '??_7M@@6BA@@@': MA, '??_7M@@6BB@@@': MB,
                 '??_7B@@6B@': BV, '??_7P@@6B@': PV, '??_7Q@@6B@': QV, '??_7U@@6B@': UV, '??_7V@@6B@': VV,
                 '??_7W@@6B@': WV}},
    {'source': 'src/b.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        F(0x402040, '?FUN_00402040@FUN_00402000_Ov@@UAEHPAD@Z'), F(0x402050, '?FUN_00402050@FUN_00402000_Ov@@UAEHXZ'),
        F(0x402120, '?FUN_00402120@FUN_00402100_OvD@@UAEHXZ'),
        F(0x402150, '?FUN_00402150@FUN_00402100_OvD@@UAEHPAD@Z'),
        F(0x402160, '?FUN_00402160@FUN_00402100_OvD@@UAEHH@Z'), F(0x403020, '?FUN_00403020@FUN_00403000_A@@UAEHXZ'),
        F(0x403110, '?FUN_00403110@FUN_00403100_B@@UAEHXZ'), F(0x404010, '?FUN_00404010@FUN_00404000_U@@UAEHXZ'),
        F(0x404020, '?FUN_00404020@FUN_00404000_U@@UAEHH@Z'), F(0x404030, '??1FUN_00404000_U@@UAE@XZ'),
        F(0x404040, '??_GFUN_00404000_U@@UAEPAXI@Z'), F(0x405030, '??1FUN_00405000_V@@UAE@XZ'),
        F(0x405040, '??_GFUN_00405000_V@@UAEPAXI@Z')],
     'symbols': {'??_7FUN_00402000_Ov@@6B@': OV, '??_7FUN_00402100_OvD@@6B@': OVD,
                 '??_7FUN_00403200_M@@6BFUN_00403000_A@@@': MA, '??_7FUN_00403200_M@@6BFUN_00403100_B@@@': MB,
                 '??_7FUN_00404000_U@@6B@': UV, '??_7FUN_00405000_V@@6B@': VV}},
    {'source': 'src/c.cpp', 'compiler': 'sp1', 'flags': [], 'functions': [
        F(0x402990, '?FUN_00402990@FUN_00402900_Ov@@UAEHXZ')],
     'symbols': {'??_7FUN_00402900_Ov@@6B@': OV}},
]}
for _u in UNITS['units']:
    _u['symbols'] = {n: '0x%08x' % a for n, a in _u['symbols'].items()}
# unit a's slot symbols defined nowhere under their name -> the address unit b's view defines (resolve)
# Ov::h(): the slot of Ov's own vftable is disputed (b and c), so the loaded image decides (stub through slot 5)
EXPECT = {'_ZN2Ov1fEPc': '__fnv_00402040', '_ZN2Ov1hEv': '__fnvnx_vslot_01000100_5', '_ZN3OvD1gEv': '__fnv_00402120',
          '_ZN3OvD1kEPc': '__fnv_00402150', '_ZN3OvD1hEi': '__fnv_00402160',
          # A::a2: a slot of A's vtable (target unknown statically) and of M's (known): A's image slot decides
          '_ZN1A2a2Ev': '__fnvnx_vslot_01000300_2',
          '_ZN1B2b1Ev': '__fnv_00403110', '_ZN1U6Unk_01Ev': '__fnv_00404010', '_ZN1U6Unk_02Ei': '__fnv_00404020',
          '_ZN1UD0Ev': '__fnv_00404040', '_ZN1UD1Ev': '__fnv_00404040_D1',
          # not a slot: W::~W calls V's base-object destructor, which MSVC inlines; V's ??_G (unit b) with flags 0
          '_ZN1VD2Ev': '__fnv_00405040_D1'}
EXPECT_C = {'_ZN15FUN_00402900_OvD0Ev': '__fnv_00402070', '_ZN15FUN_00402900_OvD1Ev': '__fnv_00402070_D1'}
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


def msvc_env():
    d = os.environ.get('FNV_DECOMP')
    if not (d and os.path.exists(os.path.join(d, 'tools', 'msvc.py')) and os.environ.get('FNV_MSVC9_ROOT')
            and os.environ.get('FNV_MSVC9_PREFIX')):
        return None
    return os.path.join(d, 'tools', 'msvc.py')


def demangler(cache):
    def dem(names):
        cache.need(names)
        return {n: cache[n] for n in names}
    return dem


def text_sig(itanium_text):
    """'Ov::f(char*)' from c++filt or llvm-undname text, for comparing slot targets across the two compilers."""
    s = re.sub(r'^(?:public|protected|private): (?:virtual )?(?:\S+ )*?(?:__thiscall )', '', itanium_text or '')
    s = re.sub(r'^\S+ (?=\S+::)', '', s)          # return type
    s = s.replace('(void)', '()').replace(' ', '')
    return re.sub(r'`scalardeletingdtor\'', '{dtor}', s)


def main():
    msvc_py = msvc_env()
    if not msvc_py:
        print('SKIP test_vslots: needs FNV_DECOMP (tools/msvc.py), FNV_MSVC9_ROOT, FNV_MSVC9_PREFIX')
        return
    with tempfile.TemporaryDirectory() as t:
        tree = os.path.join(t, 'tree')
        os.makedirs(os.path.join(tree, 'include'))
        open(os.path.join(tree, 'include', 'h.h'), 'w').write(HEADER)
        msdir = os.path.join(t, 'msvc')
        os.makedirs(msdir)
        fl = [f if f != '-O2' else '-O1' for f in layout.flags(tree)] + ['-fno-inline']
        fl += [f for f in SEMANTICS if f not in fl]
        objs = {}
        for i, (rel, text) in enumerate(SRC.items()):
            p = os.path.join(tree, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, 'w').write(text)
            objs[rel] = os.path.join(t, rel.replace('/', '_') + '.o')
            run([CXX, *fl, p, '-o', objs[rel]], cwd=tree)
            run([sys.executable, msvc_py, '/c', '/nologo', '/O2', '/GR', '/EHsc', '/Gy', '/I' + os.path.join(tree, 'include'),
                 p, '/Fo' + os.path.join(msdir, 'u%d.obj' % i)])
        json.dump(UNITS, open(os.path.join(t, 'units.json'), 'w'))
        with open(os.path.join(t, 'objects.tsv'), 'w') as f:
            for rel in SRC:
                f.write('%s\t%s\tok\n' % (rel, objs[rel]))
        os.makedirs(os.path.join(t, 'cache'))
        ms = fnvlink.DemangleCache(os.path.join(t, 'cache', 'msvc.tsv'), fnvlink.UNDNAME)
        it = fnvlink.DemangleCache(os.path.join(t, 'cache', 'itanium.tsv'), fnvlink.CXXFILT)

        # 1. slot order: MSVC vftables vs GCC vtables of unit a, and the pairing
        coff = vslots.Coff(os.path.join(msdir, 'u0.obj'))
        vft = coff.vftables()
        ms.need([s for v in vft.values() for s in v.slots if s] + list(vft))
        order = {n: [text_sig(ms[s]) if s else None for s in v.slots] for n, v in vft.items()}
        check(order.get('??_7Ov@@6B@') == ['Ov::f(char*)', 'Ov::f(double)', 'Ov::f(int)', 'Ov::g()',
                                           "Ov::`vectordeletingdtor'(unsignedint)", 'Ov::h()'],
              'MSVC slot order of Ov (overloads reversed at the first one): %s' % order.get('??_7Ov@@6B@'))
        check((order.get('??_7OvD@@6B@') or [])[6:] == ['OvD::k(char*)', 'OvD::k(int)', 'OvD::h(int)'],
              'MSVC groups only the overloads one class introduces: %s' % order.get('??_7OvD@@6B@'))
        gv = vslots.gcc_vtables(objs['src/a.cpp'])
        it.need([s for g in gv.values() for _, sl in g for s in sl if s and s.startswith('_Z')] + list(gv) +
                [vslots.slot_function(s) for g in gv.values() for _, sl in g for s in sl if vslots.slot_function(s)])
        ms_by = {}
        for n, v in vft.items():
            m = vslots.VFT_TEXT.match(ms[n] or '')
            ms_by[(m.group(1), v.col.offset)] = v
        pairs_checked, reordered, positional_wrong, extra_itanium = 0, 0, 0, 0
        for z, group in sorted(gv.items()):
            cls = sigs.parse_itanium(z, it[z]).name
            for ott, slots in group:
                v = ms_by.get((cls, -ott))
                if not check(v is not None, 'MSVC vftable for %s at offset %d' % (cls, -ott)):
                    continue
                fns = [vslots.slot_function(s) for s in slots]
                gs = [sigs.parse_itanium(f, it[f]) if f else None for f in fns]
                tg = [coff.weak.get(s, s) for s in v.slots]
                tg = [(vslots.adjustor_target(coff, s) or (s, 0))[0] for s in tg]
                ms.need(tg)
                msg = [(sigs.parse_msvc(s, ms[s]) if s != vslots.PURE else None, vslots.is_msvc_dtor(s)) for s in tg]
                pairs = vslots.pair_slots(gs, msg)
                blind = vslots.pair_slots([None if g is None else _variant_only(g) for g in gs], msg)
                elsewhere = {vslots.slot_function(s) for o, sl in group if o != ott for s in sl}
                for j, (k, how) in enumerate(pairs):
                    if fns[j] is None:
                        continue
                    if k is None and fns[j] in elsewhere:
                        # Itanium only: the primary vtable repeats an overrider of a secondary base's function
                        # (M::b2); MSVC reaches it through the secondary vftable alone
                        extra_itanium += 1
                        continue
                    pairs_checked += 1
                    if not check(k is not None, '%s slot %d (%s) paired' % (z, j, it[fns[j]])):
                        continue
                    want, got = text_sig(it[fns[j]]), text_sig(ms[tg[k]])
                    same = want == got or (gs[j].variant in ('D0', 'D1') and got.startswith(cls + '::{dtor}('))
                    check(same, '%s slot %d: %s paired with MSVC slot %d %s (%s)' % (z, j, it[fns[j]], k, ms[tg[k]], how))
                    reordered += how in ('exact', 'l1', 'l2', 'arity') and k != blind[j][0]
                    positional_wrong += blind[j][0] != k
        check(pairs_checked >= 30, 'GCC slots paired: %d' % pairs_checked)
        check(reordered >= 3, 'slots MSVC orders differently from Itanium (overloads): %d' % reordered)
        check(extra_itanium == 1, 'Itanium-only primary slot of M::b2: %d' % extra_itanium)
        print('pairing: %d GCC slots, %d where MSVC order differs (overloads), position-only would be wrong for %d, '
              '%d Itanium-only' % (pairs_checked, reordered, positional_wrong, extra_itanium))

        # 2. the joined table
        ix = vslots.MsvcIndex(UNITS['units'], msdir, demangler(ms))
        check(len(ix.by_addr.get(OV, [])) == 3, 'three views of Ov\'s vftable: %s' % ix.by_addr.get(OV))
        tab = {a: [b for b, _, _ in ix.table(a)] for a in ix.by_addr}
        check(tab.get(OV, [None] * 6)[0] == 0x402040, 'Ov slot 0 (f(char *)) from unit b: %s' % tab.get(OV))
        check(tab.get(OV, [None] * 6)[5] is None and any(a == OV and j == 5 for a, j, _ in ix.disagreements()),
              'Ov slot 5: b and c disagree, reported: %s' % ix.disagreements())
        check(tab.get(MA, [None] * 4)[1] == 0x403210,
              'M slot 1: the override M::a1 (unit a, def) beats b\'s FUN_00403010 name: %s' % tab.get(MA))
        check(tab.get(MB, [None])[0] == 0x403110, 'M {for B} slot 0 from unit b: %s' % tab.get(MB))
        check(tab.get(UV, [None] * 4)[3] == 0x404040, 'U deleting dtor: b\'s ??_G: %s' % tab.get(UV))

        # 3. resolve with and without the MSVC objects
        def resolve(out, extra):
            run([sys.executable, FNVLINK, 'resolve', os.path.join(t, 'units.json'), os.path.join(t, 'objects.tsv'),
                 out] + extra)
            rows = {}
            for line in open(os.path.join(out, 'refs.tsv')):
                f = line.rstrip('\n').split('\t')
                if not line.startswith('#'):
                    rows[(f[0], f[1])] = (f[2], f[5])
            return rows
        base = resolve(os.path.join(t, 'out0'), [])
        for n, target in EXPECT.items():
            check(base.get(('src/a.cpp', n), ('', ''))[1] != target,
                  'without --msvc-objs %s does not reach %s: %s' % (n, target, base.get(('src/a.cpp', n))))
        # the loose 'arity' tier picks the wrong overload (OvD::k(int) for k(char *)) or none (Ov::f): replaced
        check(base.get(('src/a.cpp', '_ZN3OvD1kEPc'), ('', ''))[1] == '__fnv_00402140' and
              base.get(('src/a.cpp', '_ZN2Ov1fEPc'), ('', ''))[0] == 'ambiguous',
              'baseline: OvD::k(char *) bound to k(int) by parameter count, Ov::f(char *) ambiguous: %s %s'
              % (base.get(('src/a.cpp', '_ZN3OvD1kEPc')), base.get(('src/a.cpp', '_ZN2Ov1fEPc'))))
        out = os.path.join(t, 'out')
        rows = resolve(out, ['--msvc-objs', msdir])
        for (src, exp) in (('src/a.cpp', EXPECT), ('src/c.cpp', EXPECT_C)):
            for n, target in exp.items():
                check(rows.get((src, n), ('', ''))[1] == target, '%s %s -> %s: %s' % (src, n, target, rows.get((src, n))))
        stats = open(os.path.join(out, 'vslots_stats.txt')).read()
        check('disagreements: 1' in stats, 'one disagreement in vslots_stats.txt')
        fixed = os.path.join(t, 'fixed')
        run([sys.executable, FNVLINK, 'apply', out, fixed])
        run([sys.executable, FNVLINK, 'check', out, fixed])
        undef = set(re.findall(r"undefined reference to `([^']+)'", open(os.path.join(out, 'trial_link.log')).read()))
        left = undef & (set(EXPECT.values()) | set(EXPECT_C.values()) | set(EXPECT) | set(EXPECT_C))
        check(not left, 'bound slot symbols defined in the trial link: still undefined %s' % sorted(left))

        # 4. image-slot stubs executed
        n = run_stubs(t)
        check(n == 0, 'image-slot stubs under qemu-aarch64: %d checks failed' % n)
    print('test_vslots: %s' % ('ok' if ok else 'FAILED'))
    if not ok:
        sys.exit(1)


STUB_PROGRAM = r'''
extern "C" int main_();
asm(".globl _start\n_start:\n adrp x9, stack_top\n add x9, x9, :lo12:stack_top\n mov sp, x9\n bl main_\n"
    " mov x8, #93\n svc #0\n");
struct Obj { int v; };
extern "C" int fn_plain(Obj *o, int x) { return o->v + x; }                  // a rewritten slot: native function
extern "C" int deldtor(Obj *o, unsigned flags) { return 100 + (int)flags; }   // kind G adapter: (this, flags)
extern "C" int fnvnx_vslot_trap(Obj *o, const char *text) { return o->v == 40 && text[0] == 'v' ? -7 : -8; }
extern "C" int stub_plain(Obj *, int), stub_d0(Obj *), stub_d1(Obj *), stub_x86(Obj *);
extern "C" { __attribute__((section(".slots"), used)) const void *slots[4] =
    {(const void *)fn_plain, (const void *)deldtor, (const void *)deldtor, (const void *)0x00401234}; }
extern "C" int main_() { Obj o = {40}; int bad = 0;
    bad += stub_plain(&o, 2) != 42; bad += stub_d0(&o) != 101; bad += stub_d1(&o) != 100; bad += stub_x86(&o) != -7;
    return bad; }
extern "C" { __attribute__((aligned(16))) char stack[65536]; }
asm(".globl stack_top\n.set stack_top, stack + 65536\n");
'''
SLOTS_VA, CODE_VA = 0x02300000, 0x02200000   # outside the image's .text, like native code and the mapped image


def run_stubs(t):
    """Execute image-slot stubs under qemu-aarch64 (freestanding ILP32 program wrapped into an ELF64 container at the
    same addresses, as test_bridges.py does): a slot holding a native function, D0 / D1 through the deleting-dtor
    adapter (flags in w1), a slot still holding an x86 .text address (-> fnvnx_vslot_trap). -> failed checks."""
    src, s = os.path.join(t, 'stubs.cpp'), os.path.join(t, 'stubs.S')
    open(src, 'w').write(STUB_PROGRAM)
    open(s, 'w').write(''.join(vslots.emit_image_stub(n, SLOTS_VA + 4 * k, fl, 'vtable slot test %d' % k)
                               for n, k, fl in (('stub_plain', 0, None), ('stub_d0', 1, 1), ('stub_d1', 2, 0),
                                                ('stub_x86', 3, None))))
    run([CXX, '-mabi=ilp32', '-O1', '-ffreestanding', '-fno-exceptions', '-fno-rtti', '-fno-builtin', '-c', src, '-o',
         src + '.o'])
    run([fnvlink.AS, '-mabi=ilp32', s, '-o', s + '.o'])
    exe32, flat = os.path.join(t, 'stubs32'), os.path.join(t, 'stubs.bin')
    run([fnvlink.LD, '-m', 'aarch64linux32', '-N', '-static', '--build-id=none', '-Ttext=0x%x' % CODE_VA,
         '--section-start=.slots=0x%x' % SLOTS_VA, '-e', '_start', src + '.o', s + '.o', '-o', exe32])
    run([fnvlink.OBJCOPY, '-O', 'binary', '--remove-section=.comment', exe32, flat])
    entry = int(next(line.split()[0] for line in run(['aarch64-linux-gnu-nm', exe32]).splitlines()
                     if line.endswith(' _start')), 16)
    open(os.path.join(t, 'w.S'), 'w').write('    .section .img, "awx"\n    .incbin "%s"\n    .space 0x40000\n' % flat)
    run([fnvlink.AS, os.path.join(t, 'w.S'), '-o', os.path.join(t, 'w.o')])
    exe64 = os.path.join(t, 'stubs64')
    run([fnvlink.LD, '-m', 'aarch64linux', '-static', '-z', 'max-page-size=4096', '--section-start=.img=0x%x' % CODE_VA,
         '-e', '0x%x' % entry, os.path.join(t, 'w.o'), '-o', exe64])
    r = subprocess.run(['qemu-aarch64', exe64], capture_output=True, text=True)
    return r.returncode


def _variant_only(sig):
    """A Sig with only its ctor/dtor variant: pairing by position (what a structural model without names gets)."""
    s = sigs.Sig('special')
    s.variant = sig.variant
    return s


if __name__ == '__main__':
    main()
