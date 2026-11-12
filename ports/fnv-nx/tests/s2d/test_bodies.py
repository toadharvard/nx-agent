#!/usr/bin/env python3
"""tests/s2d - classification of declared-only functions (tools/link/bodies.py) on hand-written names (no decomp
content).

    python3 -I tests/s2d/test_bodies.py          # needs llvm-undname(-18) and c++filt

Checks: a name bound by address through a class shell (`X_shell`) and through another namespace; a C2
bound to the complete-object definition; two addresses -> missing (ambiguous); a D1 whose only address is a deleting
destructor (addrmap G) is never bound there; special members -> `= default` lines (template class -> missing);
pristine bodies: `ret` / `mov eax, ecx; ret 4` -> msvc-empty, a real body -> msvc-inline with the unit that emitted
it as host; the MSVC decoration filter; bind.tsv / synth.tsv round trip through run(); the fnvlink hook loads both.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'link'))
import bodies   # noqa: E402
import fnvlink  # noqa: E402
import sigs     # noqa: E402
import vtables  # noqa: E402

UNITS = {'units': [
    {'source': 'src/a.cpp', 'functions': [
        {'symbol': '?Get@Foo_shell@@QAEHH@Z', 'address': '00401000'},         # int Foo_shell::Get(int)
        {'symbol': '??0Bar_shell@@QAE@XZ', 'address': '00402000'},           # Bar_shell::Bar_shell()
        {'symbol': '?Run@A@ns1@@QAEXXZ', 'address': '00403000'},              # ns1::A::Run()
        {'symbol': '?Run@A@ns2@@QAEXXZ', 'address': '00403100'},              # ns2::A::Run()
        {'symbol': '?Step@B@ns1@@QAEXH@Z', 'address': '00403200'},            # ns1::B::Step(int)
        {'symbol': '?Ping@Foo@@QAEXXZ', 'address': '00403300'},               # Foo::Ping()
        {'symbol': '?Pong@Foo@fnv_00402000@@QAEXXZ', 'address': '00403400'},  # fnv_00402000::Foo::Pong()
        {'symbol': '?Init@FUN_00401ac0@@QAEXXZ', 'address': '00403500'}],     # FUN_00401ac0::Init()
     'symbols': {'??1Zap@@QAE@XZ': '00404000'}},                              # Zap::~Zap() at a G address
    {'source': 'src/b.cpp', 'functions': [], 'symbols': {}},
]}
ADDRMAP = {0x00401000: 'F', 0x00402000: 'C', 0x00403000: 'F', 0x00403100: 'F', 0x00403200: 'F', 0x00404000: 'G',
           0x00403300: 'F', 0x00403400: 'F', 0x00403500: 'F'}
REFS = [  # source, Itanium name, class
    ('src/b.cpp', '_ZN3Foo3GetEi', 'unresolved'),
    ('src/b.cpp', '_ZN3BarC2Ev', 'unresolved-ctor-dtor'),
    ('src/b.cpp', '_ZN3ns31A3RunEv', 'unresolved'),
    ('src/b.cpp', '_ZN3ns31B4StepEi', 'unresolved'),
    ('src/b.cpp', '_ZN12fnv_004010003Foo4PingEv', 'unresolved'),      # a unit copy -> the global class
    ('src/b.cpp', '_ZN12fnv_004011113Foo4PongEv', 'unresolved'),      # never another unit's copy
    ('src/b.cpp', '_ZN3Foo4PongEv', 'unresolved'),                    # nor a global name -> a unit's copy
    ('src/b.cpp', '_ZN12FUN_00402b404InitEv', 'unresolved'),          # FUN_<addr> classes are not shells
    ('src/b.cpp', '_ZN3ZapD1Ev', 'ctor-dtor-trap'),
    ('src/b.cpp', '_ZN3BazC1ERKS_', 'unresolved-ctor-dtor'),
    ('src/b.cpp', '_ZN3BazaSERKS_', 'unresolved'),
    ('src/b.cpp', '_ZN3ArrIiED1Ev', 'unresolved-ctor-dtor'),
    ('src/b.cpp', '_ZN3Qux5FrobbEv', 'unresolved'),
    ('src/b.cpp', '_ZN5Clock4TickEv', 'unresolved'),
    ('src/b.cpp', '_ZN5Clock4TockEv', 'unresolved'),
    ('src/b.cpp', '_ZN5ClockC1Ev', 'unresolved-ctor-dtor'),
    ('src/b.cpp', '_ZN3Foo3GetEi', 'code'),            # resolved rows are not read
    ('src/b.cpp', '_ZN3Bad5SlotsEv', 'vslot-unresolved'),   # S2c's
]
BODIES = {'?Tick@Clock@@QAEXXZ': (0, b'\xc3'),                  # ret
          '??0Clock@@QAE@XZ': (0, b'\x8b\xc1\xc2\x04\x00'),     # mov eax, ecx; ret 4
          '?Tock@Clock@@QAEHXZ': (1, b'\x8b\x41\x04\xc3')}      # mov eax, [ecx+4]; ret


def tables(names_m, names_i):
    ms = fnvlink.batch_demangle(fnvlink.UNDNAME, sorted(names_m))
    it = fnvlink.batch_demangle(fnvlink.CXXFILT, sorted(names_i))
    return (lambda n: sigs.parse_msvc(n, ms[n])), (lambda n: sigs.parse_itanium(n, it[n]))


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        cls.units_json = os.path.join(d, 'units.json')
        with open(cls.units_json, 'w') as f:
            json.dump(UNITS, f)
        cls.out = os.path.join(d, 'out')
        os.makedirs(cls.out)
        with open(os.path.join(cls.out, 'refs.tsv'), 'w') as f:
            for s, n, c in REFS:
                f.write('%s\t%s\t%s\t\t\t\t\n' % (s, n, c))
        with open(os.path.join(cls.out, 'addrmap.tsv'), 'w') as f:
            f.write('# fnvnx-addrmap 1\n')
            for a, k in sorted(ADDRMAP.items()):
                f.write('%08x\t%s\t__fnv_%08x\tx\tx.o\t\t\t\t\n' % (a, k, a))
        units = fnvlink.load_units(cls.units_json)
        trapped = bodies.load_trapped(os.path.join(cls.out, 'refs.tsv'))
        allms = {n for u in units.values() for n in list(u.refs) + [n for _, n in u.defs]} | set(BODIES)
        msvc, itanium = tables(allms, trapped)
        native = bodies.load_native(os.path.join(cls.out, 'addrmap.tsv'))
        cls.itanium = staticmethod(itanium)
        cls.cl = bodies.Classifier(units, native, msvc, itanium, BODIES, ['src/a.cpp', 'src/b.cpp'])
        cls.rows = {n: cls.cl.classify(n, us) for n, us in trapped.items()}
        cls.plain = bodies.Classifier(units, native, msvc, itanium, None, ['src/a.cpp', 'src/b.cpp'])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_trapped_rows(self):
        self.assertNotIn('_ZN3Bad5SlotsEv', self.rows)
        self.assertEqual(len(self.rows), 16)

    def test_addr_shell(self):
        r = self.rows['_ZN3Foo3GetEi']
        self.assertEqual((r.cls, r.target), ('addr', '__fnv_00401000'))
        r = self.rows['_ZN3BarC2Ev']
        self.assertEqual((r.cls, r.target), ('addr', '__fnv_00402000'))
        self.assertIn('C2 bound to the complete-object definition', r.note)

    def test_addr_namespace(self):
        # other namespaces or classes of the same name are other classes (SaveManager::String, ExtraSave::String)
        self.assertEqual(self.rows['_ZN3ns31B4StepEi'].cls, 'missing')
        self.assertEqual(self.rows['_ZN3ns31A3RunEv'].cls, 'missing')
        r = self.rows['_ZN12fnv_004010003Foo4PingEv']
        self.assertEqual((r.cls, r.target), ('addr', '__fnv_00403300'))
        for n in ('_ZN12fnv_004011113Foo4PongEv', '_ZN3Foo4PongEv', '_ZN12FUN_00402b404InitEv'):
            self.assertEqual(self.rows[n].cls, 'missing', n)

    def test_never_a_deleting_dtor(self):
        r = self.rows['_ZN3ZapD1Ev']
        self.assertEqual(r.cls, 'special-default')
        self.assertEqual(r.host, 'src/b.cpp')

    def test_special_default(self):
        self.assertEqual(self.rows['_ZN3BazC1ERKS_'].cls, 'special-default')
        self.assertEqual(self.rows['_ZN3BazaSERKS_'].cls, 'special-default')
        it = self.itanium
        self.assertEqual(bodies.default_line('copy', it('_ZN3BazC1ERKS_')), '::Baz::Baz(const ::Baz &) = default;')
        self.assertEqual(bodies.default_line('assign', it('_ZN3BazaSERKS_')),
                         '::Baz &::Baz::operator=(const ::Baz &) = default;')
        self.assertEqual(bodies.default_line('dtor', it('_ZN3ZapD1Ev')), '::Zap::~Zap() = default;')
        r = self.rows['_ZN3ArrIiED1Ev']
        self.assertEqual(r.cls, 'missing')
        self.assertIn('template', r.note)
        self.assertEqual(self.rows['_ZN3Qux5FrobbEv'].cls, 'missing')

    def test_pristine(self):
        r = self.rows['_ZN5Clock4TickEv']
        self.assertEqual((r.cls, r.host), ('msvc-empty', 'src/a.cpp'))
        self.assertEqual(self.rows['_ZN5ClockC1Ev'].cls, 'msvc-empty')
        r = self.rows['_ZN5Clock4TockEv']
        self.assertEqual((r.cls, r.host), ('msvc-inline', 'src/b.cpp'))
        # without pristine objects: the ctor is a special member, the others missing
        self.assertEqual(self.plain.classify('_ZN5ClockC1Ev', {'src/b.cpp'}).cls, 'special-default')
        self.assertEqual(self.plain.classify('_ZN5Clock4TockEv', {'src/b.cpp'}).cls, 'missing')

    def test_norm(self):
        self.assertEqual(bodies.norm('NiNode_shell'), 'NiNode')
        for n in ('NiNode_00a1b2c3', 'FUN_00402b40', 'S_0066ba30', 'DAT_01011234', 'thunk_FUN_00401000', 'Ab_00401000'):
            self.assertEqual(bodies.norm(n), n)

    def test_x86_empty(self):
        for code, want in ((b'\xc3', True), (b'\xc2\x08\x00', True), (b'\x8b\xc1\xc3', True),
                           (b'\x89\xc8\xc2\x04\x00', True), (b'\x33\xc0\xc3', False), (b'', False),
                           (b'\x8b\xc1', False)):
            self.assertEqual(bodies.x86_empty(code), want, code)

    def test_decoration_filter(self):
        it = self.itanium
        self.assertTrue(bodies.decoration_tokens('??0Clock@@QAE@XZ') & bodies.tokens_for(it('_ZN5ClockC1Ev')))
        self.assertTrue(bodies.decoration_tokens('??0Bar_shell@@QAE@XZ') &
                        bodies.tokens_for(it('_ZN3BarC2Ev')))
        self.assertFalse(bodies.decoration_tokens('??0Other@@QAE@XZ') & bodies.tokens_for(it('_ZN5ClockC1Ev')))
        self.assertTrue(bodies.decoration_tokens('?Tick@Clock@@QAEXXZ') & bodies.tokens_for(it('_ZN5Clock4TickEv')))

    def test_empty_stubs(self):
        s = bodies.emit_empty([self.rows['_ZN5Clock4TickEv']])
        self.assertIn('.weak "_ZN5Clock4TickEv"', s)
        self.assertIn('    ret', s)

    def test_run_and_hook(self):
        rows = bodies.run(self.out, os.path.dirname(self.units_json), units_json=self.units_json, synth=False)
        self.assertEqual(len(rows), 16)
        bind = bodies.load_bind(self.out)
        self.assertEqual(bind, {'_ZN3Foo3GetEi': ('body-addr', '__fnv_00401000'),
                                '_ZN3BarC2Ev': ('body-addr', '__fnv_00402000'),
                                '_ZN12fnv_004010003Foo4PingEv': ('body-addr', '__fnv_00403300')})
        self.assertEqual(bodies.load_synth(self.out), [])
        with open(os.path.join(self.out, 'bodies', 'stats.txt')) as f:
            stats = f.read()
        self.assertIn('names still undefined after resolve: 16', stats)
        with open(fnvlink.__file__) as f:   # the resolve hook reads both files
            src = f.read()
        self.assertIn('bodies.load_synth(out)', src)
        self.assertIn('bodies.load_bind(out)', src)

    def test_vcall_line_and_stub(self):
        it = self.itanium
        line = bodies.vcall_line(3, it('_ZN3Qux5FrobbEv'))
        self.assertIn('__fnvnx_bd::sel<::Qux>::m(&::Qux::Frobb)', line)
        self.assertIn('__asm__("__fnvnx_bdpm_3")', line)
        self.assertIsNone(bodies.vcall_line(0, it('_ZN3BazC1ERKS_')))      # constructors are never virtual
        s = bodies.emit_vcalls({'__fnvnx_bdvc_00000001': ('_ZN3Qux5FrobbEv', 8, 0),
                                '__fnvnx_bdvc_00000002': ('_ZN3Qux5FrobbEv', 20000, 4)})
        self.assertIn('    ldr w16, [x16, #8]', s)
        self.assertIn('    add w0, w0, w17', s)
        self.assertIn('    ldr w16, [x16, x17]', s)

    @unittest.skipUnless(shutil.which(vtables.CXX), 'needs the aarch64 cross compiler')
    def test_vcall_probe(self):
        """GCC's pointer-to-member constant: slot offsets; a non-virtual member is refused."""
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'src'))
            os.makedirs(os.path.join(d, 'include'))
            with open(os.path.join(d, 'src', 'v.cpp'), 'w') as f:
                f.write('struct A { virtual int a0(int); virtual int a1(int); int na(int); };\n'
                        'struct B { virtual float b0() const; };\n'
                        'struct C : A, B { virtual float b0() const; virtual void c2(char const *); };\n')
            names = ['_ZN1A2a1Ei', '_ZN1A2naEi', '_ZNK1C2b0Ev', '_ZN1C2c2EPKc']
            dem = fnvlink.batch_demangle(fnvlink.CXXFILT, names)
            it = (lambda n: sigs.parse_itanium(n, dem[n]))
            got, why = bodies.probe_vcalls(d, d, 'src/v.cpp', names, it)
        self.assertEqual(got['_ZN1A2a1Ei'], (4, 0))
        self.assertEqual(got['_ZNK1C2b0Ev'], (8, 0))     # an overrider of a secondary base's function: also in
        self.assertEqual(got['_ZN1C2c2EPKc'], (12, 0))   # the primary vtable (Itanium), so no this adjustment
        self.assertEqual(why['_ZN1A2naEi'], 'not virtual')


    @unittest.skipUnless(shutil.which(vtables.CXX), 'needs the aarch64 cross compiler')
    def test_probe_without_prelude(self):
        """A host unit that defines its own std::exception (as decomp units do) still hosts a synthesized
        `= default` member and a vcall probe: the probes include no <typeinfo> before the unit."""
        src = ('namespace std { class exception { public: exception(const char *); virtual ~exception(); }; }\n'
               'struct M { M(); ~M(); int z; };\n'
               'struct K { M m; K(); ~K(); virtual int f(int); };\n'
               'int use(K *k) { K a; return k->f(1); }\n')
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'src'))
            os.makedirs(os.path.join(d, 'include'))
            with open(os.path.join(d, 'src', 'e.cpp'), 'w') as f:
                f.write(src)
            names = ['_ZN1KC1Ev', '_ZN1KD1Ev', '_ZN1K1fEi']
            dem = fnvlink.batch_demangle(fnvlink.CXXFILT, names)
            it = (lambda n: sigs.parse_itanium(n, dem[n]))
            rows = [bodies.Row(n, 'special-default', None, None, 'src/e.cpp', '', 'src/e.cpp') for n in names[:2]]
            path, roots, failed = bodies.synth_host(d, d, 'src/e.cpp', rows, it)
            got, why = bodies.probe_vcalls(d, d, 'src/e.cpp', names[2:], it)
        self.assertEqual(failed, {})
        self.assertEqual(roots, set(names[:2]))
        self.assertEqual(got['_ZN1K1fEi'], (0, 0))   # K's first virtual (its destructor is not virtual)

    def test_conversion_operator_pairs(self):
        """An MSVC conversion operator to a pointer type (`operator class A *(void)`) parses as a function and pairs
        with its Itanium name (it used to be dropped as a function returning a function pointer)."""
        ms, it = '??B?$P@VA@@@@QBEPAVA@@XZ', '_ZNK1PI1AEcvPS0_Ev'
        dm = fnvlink.batch_demangle(fnvlink.UNDNAME, [ms])
        di = fnvlink.batch_demangle(fnvlink.CXXFILT, [it])
        m, i = sigs.parse_msvc(ms, dm[ms]), sigs.parse_itanium(it, di[it])
        self.assertEqual((m.kind, m.qual, m.params, m.cc), ('func', ['P<A>', 'operator A*'], [], 'thiscall'))
        mk = {k for t, k in m.keys() if t in ('exact', 'l1', 'l2')} | {k for _, k in bodies.def_keys(m)}
        ik = {k for t, k in i.keys() if t in ('exact', 'l1', 'l2')} | {k for _, k in bodies.relaxed_keys(i)}
        self.assertTrue(mk & ik)

    def test_star_operators_pair(self):
        """operator* / operator*= end in '*' as well: they parse and pair (the matrix and vector products of
        NiTransform::operator*); a function returning a function pointer still does not parse as a function."""
        cases = [('??DM@@QBE?AV0@ABV0@@Z', '_ZNK1MmlERKS_'), ('??XM@@QAEAAV0@M@Z', '_ZN1MmLEf')]
        dm = fnvlink.batch_demangle(fnvlink.UNDNAME, [m for m, _ in cases] + ['?f@@YAP6AXXZH@Z'])
        di = fnvlink.batch_demangle(fnvlink.CXXFILT, [i for _, i in cases])
        for ms, it in cases:
            m, i = sigs.parse_msvc(ms, dm[ms]), sigs.parse_itanium(it, di[it])
            self.assertEqual(m.kind, 'func', ms)
            mk = {k for t, k in m.keys() if t in ('exact', 'l1', 'l2')} | {k for _, k in bodies.def_keys(m)}
            ik = {k for t, k in i.keys() if t in ('exact', 'l1', 'l2')} | {k for _, k in bodies.relaxed_keys(i)}
            self.assertTrue(mk & ik, ms)
        self.assertNotEqual(sigs.parse_msvc('?f@@YAP6AXXZH@Z', dm['?f@@YAP6AXXZH@Z']).kind, 'func')

    def test_gdel_pass(self):
        """gdel: a D1 bound to its class's ??_G when the native build defines that address as a member function
        taking (unsigned int); a D2 only without virtual bases (no _ZTT of the class known); no member of that
        shape at the address -> unchanged."""
        units = {'units': [{'source': 'src/g.cpp', 'functions': [
            {'symbol': '??_GDel@@UAEPAXI@Z', 'address': '00405000'},
            {'symbol': '?Kill@FUN_00405000@@QAEPAXI@Z', 'address': '00405000'},
            {'symbol': '??_GVb@@UAEPAXI@Z', 'address': '00406000'},
            {'symbol': '?Kill@FUN_00406000@@QAEPAXI@Z', 'address': '00406000'},
            {'symbol': '??_GOdd@@UAEPAXI@Z', 'address': '00407000'},
            {'symbol': '?Run@FUN_00407000@@QAEXXZ', 'address': '00407000'}], 'symbols': {}}]}
        native = {0x00405000: 'F', 0x00406000: 'F', 0x00407000: 'F'}
        names = ['_ZN3DelD1Ev', '_ZN3DelD2Ev', '_ZN2VbD2Ev', '_ZN2VbD1Ev', '_ZN3OddD1Ev']
        with tempfile.TemporaryDirectory() as d:
            uj = os.path.join(d, 'units.json')
            json.dump(units, open(uj, 'w'))
            u = fnvlink.load_units(uj)
            ms = fnvlink.DemangleCache(os.path.join(d, 'msvc.tsv'), fnvlink.UNDNAME)
            msvc, itanium = tables({f['symbol'] for f in units['units'][0]['functions']}, names)
            rows = [bodies.Row(n, 'special-default', None, None, 'src/g.cpp', 'dtor', 'src/g.cpp') for n in names]
            got = {r.name: r for r in bodies.gdel_pass(rows, u, native, itanium, msvc, ms, known={'_ZTT2Vb'})}
        self.assertEqual((got['_ZN3DelD1Ev'].cls, got['_ZN3DelD1Ev'].target), ('gdel', '__fnvnx_bdgd_00405000'))
        self.assertEqual(got['_ZN3DelD2Ev'].cls, 'gdel')
        self.assertEqual(got['_ZN2VbD2Ev'].cls, 'special-default')     # virtual bases: D2 is not ??_G
        self.assertEqual(got['_ZN2VbD1Ev'].cls, 'gdel')
        self.assertEqual(got['_ZN3OddD1Ev'].cls, 'special-default')    # no (unsigned int) member at the address
        self.assertEqual(bodies.vtt_name('_ZN2ns3FooD2Ev'), '_ZTTN2ns3FooE')

    @unittest.skipUnless(shutil.which('qemu-aarch64') and shutil.which('aarch64-linux-gnu-gcc'),
                         'needs qemu-aarch64 and the aarch64 cross compiler')
    def test_gdel_stub_qemu(self):
        """The gdel stub under qemu: the ??_G stand-in sees the same this and flags 0, whatever w1 held (the
        stub's code is the same under LP64, which qemu-user runs)."""
        import subprocess
        c = r"""
#include <stdio.h>
void *seen_this; unsigned seen_flags = 99;
void *__fnv_00405000(void *self, unsigned flags) { seen_this = self; seen_flags = flags; return self; }
void __fnvnx_bdgd_00405000(void *self);
void call(void *p, unsigned junk);
int main(void) { int obj; call(&obj, 1); printf("%d %u\n", seen_this == (void *)&obj, seen_flags); return 0; }
"""
        asm = bodies.emit_gdel({0x00405000}) + '.globl call\ncall: b __fnvnx_bdgd_00405000\n'   # w1 = 1 on entry
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, 'm.c'), 'w').write(c)
            open(os.path.join(d, 's.S'), 'w').write(asm)
            subprocess.run(['aarch64-linux-gnu-gcc', '-static', '-O1', os.path.join(d, 'm.c'), os.path.join(d, 's.S'),
                            '-o', os.path.join(d, 'm')], check=True)
            r = subprocess.run(['qemu-aarch64', os.path.join(d, 'm')], capture_output=True, text=True, check=True)
        self.assertEqual(r.stdout.strip(), '1 0')

    @unittest.skipUnless(shutil.which(vtables.CXX), 'needs the aarch64 cross compiler')
    def test_print_flags_stdout_is_flags_only(self):
        """compile.sh --print-flags (layout.game_flags: the flags of every synthesized body) prints flags only on
        stdout. The order-keys line went there too, every game-flags compile failed, and the bodies step lost
        special-default and msvc-inline (1,329 names 'host does not compile')."""
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            tree, work = os.path.join(d, 'tree'), os.path.join(d, 'work')
            os.makedirs(os.path.join(tree, 'src'))
            os.makedirs(os.path.join(tree, 'build'))
            os.makedirs(os.path.join(tree, 'include'))
            open(os.path.join(tree, 'src', 'a.cpp'), 'w').write('struct S { volatile int v; }; int f(S *s) { return s->v; }\n')
            json.dump({'units': [{'source': 'src/a.cpp', 'functions': [], 'symbols': {}}]},
                      open(os.path.join(tree, 'build', 'units.json'), 'w'))
            r = subprocess.run(['bash', os.path.join(HERE, '..', '..', 'tools', 'link', 'compile.sh'), '--print-flags',
                                tree, work], capture_output=True, text=True, timeout=900)
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        lines = r.stdout.split('\n')[:-1]
        self.assertIn('-c', lines)
        bad = [x for i, x in enumerate(lines) if not x.startswith('-') and not (i and lines[i - 1] == '-include')]
        self.assertEqual(bad, [])

def coff(funcs, externs, data=None):
    """Minimal i386 COFF: one COMDAT code section per function {name: (bytes, {offset: callee})}; data: the same
    for read-only data sections (a vftable: DIR32 words)."""
    import struct
    data = data or {}
    kind = {n: 'code' for n in funcs}
    kind.update({n: 'data' for n in data})
    funcs = dict(funcs, **data)
    names = list(funcs)
    syms, strtab = [], bytearray(4)

    def sname(n):
        if len(n) <= 8:
            return n.encode().ljust(8, b'\0')
        off = len(strtab)
        strtab.extend(n.encode() + b'\0')
        return struct.pack('<II', 0, off)
    order = names + list(externs)
    idx = {n: i for i, n in enumerate(order)}
    hdr_end = 20 + 40 * len(names)
    secs, raw, pos = [], bytearray(), hdr_end
    for k, n in enumerate(names):
        code, calls = funcs[n]
        data_off = pos
        raw += code
        pos += len(code)
        rel_off = pos
        for off, callee in sorted(calls.items()):
            callee, typ = callee if isinstance(callee, tuple) else (callee, 0x14 if kind[n] == 'code' else 0x06)
            raw += struct.pack('<IIH', off, idx[callee], typ)
            pos += 10
        secs.append(struct.pack('<8sIIIIIIHHI', b'.text$%d' % k if kind[n] == 'code' else b'.rdata$%d' % k, 0, 0,
                                len(code), data_off, rel_off, 0, len(calls), 0,
                                0x60501020 if kind[n] == 'code' else 0x40301040))
    symptr = pos
    for n in order:
        sec = names.index(n) + 1 if n in funcs else 0
        syms.append(sname(n) + struct.pack('<IhHBB', 0, sec, 0x20, 2, 0))
    struct.pack_into('<I', strtab, 0, len(strtab))
    head = struct.pack('<HHIIIHH', 0x14c, len(names), 0, symptr, len(order), 0, 0)
    return head + b''.join(secs) + bytes(raw) + b''.join(syms) + bytes(strtab)


def pe(base, text_va, text):
    import struct
    d = bytearray(0x400)
    struct.pack_into('<I', d, 0x3c, 0x80)
    d[0x80:0x84] = b'PE\0\0'
    struct.pack_into('<HH', d, 0x84, 0x14c, 1)
    struct.pack_into('<H', d, 0x80 + 20, 0xe0)
    struct.pack_into('<I', d, 0x80 + 24 + 28, base)
    struct.pack_into('<8sIIII', d, 0x80 + 24 + 0xe0, b'.text', len(text), text_va - base, len(text), 0x400)
    struct.pack_into('<I', d, 0x80 + 24 + 0xe0 + 36, 0x60000020)   # code, execute, read
    return bytes(d) + text


class Exe(unittest.TestCase):
    def test_exe_call_targets(self):
        """A placed function's REL32 call gives the target; a COMDAT reached from it is placed by that call and
        its own call counts; a function whose bytes differ from the exe gives nothing."""
        import struct
        f = b'\x55\xe8\0\0\0\0\xe8\0\0\0\0\x5d\xc3'            # push ebp; call ?h; call ?g; pop ebp; ret
        h = b'\xe8\0\0\0\0\xc3'                                    # COMDAT ?h: call ?k; ret
        bad = b'\x90\xe8\0\0\0\0\xc3'
        obj = coff({'?f@@YAXXZ': (f, {2: '?h@@YAXXZ', 7: '?g@@YAXXZ'}), '?h@@YAXXZ': (h, {1: '?k@@YAXXZ'}),
                    '?b@@YAXXZ': (bad, {2: '?g@@YAXXZ'})}, ['?g@@YAXXZ', '?k@@YAXXZ'])
        text = bytearray(0x100)
        F, H, G, K, B = 0x401000, 0x401040, 0x401080, 0x4010c0, 0x401020
        text[0:len(f)] = f
        struct.pack_into('<i', text, 2, H - (F + 6))
        struct.pack_into('<i', text, 7, G - (F + 11))
        text[0x40:0x40 + len(h)] = h
        struct.pack_into('<i', text, 0x41, K - (H + 5))
        text[0x20:0x20 + len(bad)] = b'\xcc' * len(bad)              # ?b is not the exe's code
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'u0.obj'), 'wb') as fo:
                fo.write(obj)
            with open(os.path.join(d, 'x.exe'), 'wb') as fo:
                fo.write(pe(0x400000, 0x401000, bytes(text)))
            unit = {'functions': [{'symbol': '?f@@YAXXZ', 'address': '%x' % F},
                                  {'symbol': '?b@@YAXXZ', 'address': '%x' % B}]}
            exe = bodies.Pe(os.path.join(d, 'x.exe'))
            self.assertEqual(exe.read(F, 2), b'\x55\xe8')
            got = bodies.exe_call_targets(d, 0, unit, {'?g@@YAXXZ', '?k@@YAXXZ'}, exe)
        self.assertEqual({k: dict(v) for k, v in got.items()}, {'?g@@YAXXZ': {G: 1}, '?k@@YAXXZ': {K: 1}})

    def test_exe_dtor_as_function(self):
        """A destructor the exe calls out of line at a function the decomp spells FUN_<addr>(T*) binds there
        (natively both take the object in x0) instead of `= default`, as does one at an S4 provider's address; one with
        two parameters does not."""
        import struct
        f = b'\xe8\0\0\0\0\xe8\0\0\0\0\xe8\0\0\0\0\xc3'   # call ??1Short; call ??1Long; call ??1Lock; ret
        obj = coff({'?f@@YAXXZ': (f, {1: '??1Short@@QAE@XZ', 6: '??1Long@@QAE@XZ', 11: '??1Lock@@QAE@XZ'})},
                   ['??1Short@@QAE@XZ', '??1Long@@QAE@XZ', '??1Lock@@QAE@XZ'])
        F, G, H, P = 0x401000, 0x401080, 0x4010c0, 0x4010e0
        text = bytearray(0x100)
        text[0:len(f)] = f
        struct.pack_into('<i', text, 1, G - (F + 5))
        struct.pack_into('<i', text, 6, H - (F + 10))
        struct.pack_into('<i', text, 11, P - (F + 15))                # an S4 provider implements P
        units_j = {'units': [{'source': 'src/a.cpp', 'functions': [
            {'symbol': '?f@@YAXXZ', 'address': '%x' % F},
            {'symbol': '?FUN_00401080@@YIXPAX@Z', 'address': '%x' % G},          # void __fastcall (void*)
            {'symbol': '?FUN_004010c0@@YAXPAXH@Z', 'address': '%x' % H}]}]}     # void __cdecl (void*, int)
        rows = [bodies.Row('_ZN5ShortD1Ev', 'special-default', None, None, 'src/a.cpp', 'dtor', 'src/a.cpp'),
                bodies.Row('_ZN4LongD1Ev', 'special-default', None, None, 'src/a.cpp', 'dtor', 'src/a.cpp'),
                bodies.Row('_ZN4LockD1Ev', 'special-default', None, None, 'src/a.cpp', 'dtor', 'src/a.cpp')]
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'u0.obj'), 'wb') as fo:
                fo.write(obj)
            with open(os.path.join(d, 'x.exe'), 'wb') as fo:
                fo.write(pe(0x400000, 0x401000, bytes(text)))
            uj = os.path.join(d, 'units.json')
            with open(uj, 'w') as fo:
                json.dump(units_j, fo)
            ms = fnvlink.DemangleCache(os.path.join(d, 'ms.tsv'), fnvlink.UNDNAME)
            it = fnvlink.DemangleCache(os.path.join(d, 'it.tsv'), fnvlink.CXXFILT)
            it.need([r.name for r in rows])
            got = bodies.exe_pass(rows, os.path.join(d, 'x.exe'), d, uj, ['src/a.cpp'], {F: 'F', G: 'F', H: 'F', P: 'F'},
                                  lambda n: sigs.parse_itanium(n, it[n]), lambda n: sigs.parse_msvc(n, ms[n]), ms,
                                  {P: 'fnvnx_lock_dtor'})
        got = {r.name: r for r in got}
        self.assertEqual((got['_ZN5ShortD1Ev'].cls, got['_ZN5ShortD1Ev'].target), ('exe-addr', '__fnv_00401080'))
        self.assertEqual(got['_ZN4LongD1Ev'].cls, 'special-default')
        self.assertEqual((got['_ZN4LockD1Ev'].cls, got['_ZN4LockD1Ev'].target), ('exe-addr', '__fnv_004010e0'))


    def test_load_provider_params(self):
        """crt_symbols.tsv identifications give S4's providers a parameter count: the object is not counted, a trailing
        [note] is dropped, a row without a parameter list is not listed."""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'crt.tsv')
            with open(p, 'w') as fo:
                fo.write('# address\tclass\tidentification\thow\tunits\tsrc\tprovider\n'
                         '0x00401000\tcpplib\tstd::_Lockit::_Lockit(int)\tn\t2\t3\tfnvnx_lockit_ctor\n'
                         '0x00401010\tcpplib\tstd::_Locinfo::_Locinfo_ctor(_Locinfo*, const std::string&)\ti\t1\t2\tx\n'
                         '0x00401020\tcpplib\tstd::locale::_Getgloballocale() [locale::_Getfacet fallback, locale() _Incref]\ti\t2\t2\tx\n'
                         '0x00401030\tcpplib\tstd::ios_base::_Ios_base_dtor\tn\t4\t4\tx\n'
                         '0x00401040\tcpplib\tstd::bad_cast::bad_cast(const char*)\tn\t1\t1\tx\n'
                         '0x00401050\tcpplib\tstd::map<int, int>::find(std::pair<int, int>)\tn\t1\t1\tx\n')
            got = bodies.load_provider_params(p)
        self.assertEqual(got, {0x401000: 1, 0x401010: 2, 0x401020: 0, 0x401040: 1, 0x401050: 1})

    def test_exe_provider_shape(self):
        """A constructor with a parameter the exe calls at an S4 provider's address (no unit defines it, so there is no
        MSVC definition to compare) binds there when the provider's identification has as many parameters; a
        reference with more does not."""
        import struct
        f = b'\xe8\0\0\0\0\xe8\0\0\0\0\xc3'   # call ??0Lock(int); call ??0Wide(int, int); ret
        obj = coff({'?f@@YAXXZ': (f, {1: '??0Lock@@QAE@H@Z', 6: '??0Wide@@QAE@HH@Z'})},
                   ['??0Lock@@QAE@H@Z', '??0Wide@@QAE@HH@Z'])
        F, P = 0x401000, 0x4010e0
        text = bytearray(0x100)
        text[0:len(f)] = f
        struct.pack_into('<i', text, 1, P - (F + 5))
        struct.pack_into('<i', text, 6, P - (F + 10))
        units_j = {'units': [{'source': 'src/a.cpp', 'functions': [{'symbol': '?f@@YAXXZ', 'address': '%x' % F}]}]}
        rows = [bodies.Row('_ZN4LockC1Ei', 'missing', None, None, 'src/a.cpp', 'no definition', None),
                bodies.Row('_ZN4WideC1Eii', 'missing', None, None, 'src/a.cpp', 'no definition', None)]
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'u0.obj'), 'wb') as fo:
                fo.write(obj)
            with open(os.path.join(d, 'x.exe'), 'wb') as fo:
                fo.write(pe(0x400000, 0x401000, bytes(text)))
            uj = os.path.join(d, 'units.json')
            with open(uj, 'w') as fo:
                json.dump(units_j, fo)
            ms = fnvlink.DemangleCache(os.path.join(d, 'ms.tsv'), fnvlink.UNDNAME)
            it = fnvlink.DemangleCache(os.path.join(d, 'it.tsv'), fnvlink.CXXFILT)
            it.need([r.name for r in rows])
            got = bodies.exe_pass(rows, os.path.join(d, 'x.exe'), d, uj, ['src/a.cpp'], {F: 'F', P: 'F'},
                                  lambda n: sigs.parse_itanium(n, it[n]), lambda n: sigs.parse_msvc(n, ms[n]), ms,
                                  {P: 'fnvnx_lock_ctor'}, {P: 1})
        got = {r.name: r for r in got}
        self.assertEqual((got['_ZN4LockC1Ei'].cls, got['_ZN4LockC1Ei'].target), ('exe-addr', '__fnv_004010e0'))
        self.assertEqual(got['_ZN4WideC1Eii'].cls, 'missing')

    def test_align_targets(self):
        """Call-order alignment: P (placed) calls the unplaced helper C, which MSVC inlined in the exe, then K
        (placed). C calls M, which no placed function calls: the exe's P calls M's address where C's call stood."""
        import struct
        p = b'\xe8\0\0\0\0\xe8\0\0\0\0\xc3'                 # call ?C; call ?K; ret
        c_ = b'\xe8\0\0\0\0\xc3'                                  # call ?M; ret
        obj = coff({'?P@@YAXXZ': (p, {1: '?C@@YAXXZ', 6: '?K@@YAXXZ'}), '?C@@YAXXZ': (c_, {1: '?M@@YAXXZ'})},
                   ['?K@@YAXXZ', '?M@@YAXXZ'])
        P, K, M, Q = 0x401000, 0x401080, 0x4010c0, 0x401100
        text = bytearray(0x200)
        ins = b'\x90\x90\xe8\0\0\0\0\x90\xe8\0\0\0\0\xc3'      # the exe's P: C inlined (with M's call)
        text[0:len(ins)] = ins
        struct.pack_into('<i', text, 3, M - (P + 7))
        struct.pack_into('<i', text, 9, K - (P + 13))
        udata = [{'source': 'src/a.cpp', 'functions': [{'symbol': '?P@@YAXXZ', 'address': '%x' % P},
                                                       {'symbol': '?Q@@YAXXZ', 'address': '%x' % Q},
                                                       {'symbol': '?K@@YAXXZ', 'address': '%x' % K}],
                  'symbols': {}}]
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'u0.obj'), 'wb') as fo:
                fo.write(obj)
            with open(os.path.join(d, 'x.exe'), 'wb') as fo:
                fo.write(pe(0x400000, 0x401000, bytes(text)))
            exe = bodies.Pe(os.path.join(d, 'x.exe'))
            got = bodies.align_targets(d, udata, exe, {'?M@@YAXXZ'})
            self.assertEqual({k: dict(v) for k, v in got.items()}, {'?M@@YAXXZ': {M: 1}})
            struct.pack_into('<i', text, 9, 0x401140 - (P + 13))         # the anchor K no longer matches
            with open(os.path.join(d, 'x.exe'), 'wb') as fo:
                fo.write(pe(0x400000, 0x401000, bytes(text)))
            got = bodies.align_targets(d, udata, bodies.Pe(os.path.join(d, 'x.exe')), {'?M@@YAXXZ'})
            self.assertEqual(dict(got), {})


    def test_exe_sret(self):
        """The exe calls X::Scale(V*, float) where MSVC called V V::operator*(float) const: the hidden return
        pointer written out. -> exe-sret, and the adapter line compiles to the wanted symbol (V trivially copyable and destructible only)."""
        import struct
        f = b'\xe8\0\0\0\0\xc3'
        obj = coff({'?f@@YAXXZ': (f, {1: '??DV@@QBE?AU0@M@Z'})}, ['??DV@@QBE?AU0@M@Z'])
        F, G = 0x401000, 0x401080
        text = bytearray(0x100)
        text[0:len(f)] = f
        struct.pack_into('<i', text, 1, G - (F + 5))
        units_j = {'units': [{'source': 'src/a.cpp', 'functions': [
            {'symbol': '?f@@YAXXZ', 'address': '%x' % F},
            {'symbol': '?Scale@X@@QAEXPAUV@@M@Z', 'address': '%x' % G}]}]}     # void X::Scale(V*, float)
        rows = [bodies.Row('_ZNK1VmlEf', 'missing', None, None, 'src/a.cpp', 'no definition', None)]
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'u0.obj'), 'wb') as fo:
                fo.write(obj)
            with open(os.path.join(d, 'x.exe'), 'wb') as fo:
                fo.write(pe(0x400000, 0x401000, bytes(text)))
            uj = os.path.join(d, 'units.json')
            with open(uj, 'w') as fo:
                json.dump(units_j, fo)
            ms = fnvlink.DemangleCache(os.path.join(d, 'ms.tsv'), fnvlink.UNDNAME)
            it = fnvlink.DemangleCache(os.path.join(d, 'it.tsv'), fnvlink.CXXFILT)
            it.need([r.name for r in rows])
            itan = (lambda n: sigs.parse_itanium(n, it[n]))
            got = bodies.exe_pass(rows, os.path.join(d, 'x.exe'), d, uj, ['src/a.cpp'], {F: 'F', G: 'F'},
                                  itan, lambda n: sigs.parse_msvc(n, ms[n]), ms)
            r = got[0]
            self.assertEqual((r.cls, r.addr, r.host), ('exe-sret', G, 'src/a.cpp'))
            self.assertTrue(r.note.startswith('sret this'))
            line = bodies.sret_line(itan(r.name), G, True)
            gxx = shutil.which('aarch64-linux-gnu-g++') or shutil.which('g++')
            if gxx:
                for v, ok in (('struct V { float x, y, z; V operator*(float) const; };', True),
                              ('struct V { V(); float x, y, z; V operator*(float) const; };', True),
                              ('struct V { ~V(); float x, y, z; V operator*(float) const; };', False)):
                    src = os.path.join(d, 's.cpp')
                    open(src, 'w').write(v + '\n' + line + '\n')
                    p = subprocess.run([gxx, '-c', src, '-o', os.path.join(d, 's.o')], capture_output=True, text=True)
                    self.assertEqual(p.returncode == 0, ok, p.stderr[-400:])
                    if ok:
                        nm = subprocess.run(['nm', os.path.join(d, 's.o')], capture_output=True, text=True).stdout
                        self.assertIn('T _ZNK1VmlEf', nm)
                        self.assertIn('U __fnv_00401080', nm)


    def test_exe_address_taken(self):
        """A DIR32 in a placed function (push offset ?g) gives the target from the exe's word, minus the addend."""
        import struct
        f = b'\x68\x04\0\0\0\xc3'                               # push offset ?g+4; ret
        obj = coff({'?f@@YAXXZ': (f, {1: ('?g@@YAXXZ', 0x06)})}, ['?g@@YAXXZ'])
        F, G = 0x401000, 0x401080
        text = bytearray(0x100)
        text[0:len(f)] = f
        struct.pack_into('<I', text, 1, G + 4)
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'u0.obj'), 'wb') as fo:
                fo.write(obj)
            with open(os.path.join(d, 'x.exe'), 'wb') as fo:
                fo.write(pe(0x400000, 0x401000, bytes(text)))
            got = bodies.exe_call_targets(d, 0, {'functions': [{'symbol': '?f@@YAXXZ', 'address': '%x' % F}]},
                                          {'?g@@YAXXZ'}, bodies.Pe(os.path.join(d, 'x.exe')))
        self.assertEqual({k: dict(v) for k, v in got.items()}, {'?g@@YAXXZ': {G: 1}})

    def test_exe_standalone_copy(self):
        """search=True: a function units.json does not list and no placed function reaches is placed where its bytes
        (relocations masked) occur once in the exe's code; a body shorter than SEARCH_MIN is not."""
        import struct
        f = b'\x55\x8b\xec\x6a\x01\x6a\x02\xe8\0\0\0\0\x83\xc4\x08\x5d\xc3'   # 17 bytes: call ?g
        short = b'\xe8\0\0\0\0\xc3'                                                   # call ?k; ret
        obj = coff({'?f@@YAXXZ': (f, {8: '?g@@YAXXZ'}), '?s@@YAXXZ': (short, {1: '?k@@YAXXZ'})},
                   ['?g@@YAXXZ', '?k@@YAXXZ'])
        F, G, K = 0x401030, 0x4010a0, 0x4010c0
        text = bytearray(b'\xcc' * 0x100)
        text[0x30:0x30 + len(f)] = f
        struct.pack_into('<i', text, 0x38, G - (F + 12))
        text[0x60:0x60 + len(short)] = short
        struct.pack_into('<i', text, 0x61, K - (0x401060 + 5))
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'u0.obj'), 'wb') as fo:
                fo.write(obj)
            with open(os.path.join(d, 'x.exe'), 'wb') as fo:
                fo.write(pe(0x400000, 0x401000, bytes(text)))
            exe = bodies.Pe(os.path.join(d, 'x.exe'))
            args = (d, 0, {'functions': []}, {'?g@@YAXXZ', '?k@@YAXXZ'}, exe)
            self.assertEqual(dict(bodies.exe_call_targets(*args)), {})
            got = bodies.exe_call_targets(*args, search=True)
        self.assertEqual({k: dict(v) for k, v in got.items()}, {'?g@@YAXXZ': {G: 1}})

    def test_exe_data(self):
        """exe-data: a placeholder variable's address taken in placed code (mov ecx, offset ?g_mm) binds to the exe's
        word there; a variant body with another stack offset is placed by shape (loose), a ??_G by the vftable slot
        the unit's recipe names; a C function placeholder called from placed code (REL32) and a variable whose sites
        disagree do not bind."""
        import struct
        f = b'\x55\x8b\xec\x8b\x45\x08\xb9\0\0\0\0\x50\xe8\0\0\0\0\x5d\xc3'   # mov eax,[ebp+8]; mov ecx, offset g_mm
        v = bytearray(f)
        v[5] = 0x0c                                               # the exe's copy: mov eax, [ebp+0xc]
        gd = b'\x56\x8b\xf1\xb9\0\0\0\0\xe8\0\0\0\0\x8b\xc6\x5e\xc2\x04\x00'      # ??_G: mov ecx, offset g_mm2
        st = b'\x55\x8b\xec\xe8\0\0\0\0\x50\xe8\0\0\0\0\x5d\xc3'                # call _stub_a; push eax; call
        two = b'\x55\x8b\xec\xa1\0\0\0\0\x5d\xc3' + b'\x90' * 8              # mov eax, [g_two]
        obj = coff({'?f@@YAXXZ': (bytes(v), {7: ('?g_mm@@3UMM@@A', 0x06), 13: '?Free@MM@@QAEXPAX@Z'}),
                    '??_GC@@UAEPAXI@Z': (gd, {4: ('?g_mm2@@3UMM@@A', 0x06), 9: '?Free@MM@@QAEXPAX@Z'}),
                    '?s@@YAXXZ': (st, {4: '_stub_a', 10: '?Free@MM@@QAEXPAX@Z'}),
                    '?t@@YAXXZ': (two, {4: ('?g_two@@3HA', 0x06)}), '?t2@@YAXXZ': (two, {4: ('?g_two@@3HA', 0x06)}),
                    '?t3@@YAXXZ': (two, {4: ('?ms_k@Pt@@2U1@B', 0x06)})},
                   ['?g_mm@@3UMM@@A', '?g_mm2@@3UMM@@A', '?Free@MM@@QAEXPAX@Z', '_stub_a', '?g_two@@3HA',
                    '?ms_k@Pt@@2U1@B'])
        F, GD, S, T, T2, VFT, FREE, STUB = 0x401000, 0x401040, 0x401080, 0x4010c0, 0x4010e0, 0x401100, 0x401140, 0x401180
        MM, TWO = 0x5f6238, 0x5f7000
        text = bytearray(b'\xcc' * 0x200)
        text[0:len(f)] = f
        struct.pack_into('<I', text, 7, MM)
        struct.pack_into('<i', text, 13, FREE - (F + 17))
        for at in (0x40, 0x1c0):   # the second copy (another class's ??_G): the content search finds two
            text[at:at + len(gd)] = gd
            struct.pack_into('<I', text, at + 4, MM)
            struct.pack_into('<i', text, at + 9, FREE - (0x401000 + at + 13))
        self.assertIsNone(bodies.Pe.find_unique(types.SimpleNamespace(code=[(0x401000, 0, 0x200)], data=bytes(text)),
                                                gd, set(range(4, 8)) | set(range(9, 13))))
        text[0x80:0x80 + len(st)] = st
        struct.pack_into('<i', text, 0x84, STUB - (S + 8))
        struct.pack_into('<i', text, 0x8a, FREE - (S + 14))
        for at, word in ((0xc0, TWO), (0xe0, TWO + 4)):
            text[at:at + len(two)] = two
            struct.pack_into('<I', text, at + 4, word)
        struct.pack_into('<II', text, 0x100, GD, F)           # the image vftable of C: ??_G, then another slot
        text[0x140] = text[0x180] = 0xc3
        T3, PTK = 0x4011a0, 0x5f8000   # a static data member's address taken (mov eax, [Pt::ms_k])
        text[0x1a0:0x1a0 + len(two)] = two
        struct.pack_into('<I', text, 0x1a4, PTK)
        udata = [{'source': 'src/a.cpp', 'functions': [
            {'symbol': '?f@@YAXXZ', 'address': '%x' % F}, {'symbol': '?s@@YAXXZ', 'address': '%x' % S},
            {'symbol': '?t@@YAXXZ', 'address': '%x' % T}, {'symbol': '?t2@@YAXXZ', 'address': '%x' % T2},
            {'symbol': '?t3@@YAXXZ', 'address': '%x' % T3}],
            'symbols': {'??_7C@@6B@': '%x' % VFT}}]
        self.assertTrue(bodies.same_shape(bytes(v), f))
        self.assertFalse(bodies.same_shape(b'\x8b\x45\x08\xc3', b'\x8b\x4d\x08\xc3'))   # another register
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'u0.obj'), 'wb') as fo:
                fo.write(obj)
            with open(os.path.join(d, 'x.exe'), 'wb') as fo:
                fo.write(pe(0x400000, 0x401000, bytes(text)))
            exe = bodies.Pe(os.path.join(d, 'x.exe'))
            strict = bodies.exe_call_targets(d, 0, udata[0], {'?g_mm@@3UMM@@A'}, exe, search=True)
            self.assertEqual(dict(strict), {})                        # bytes differ: not placed without loose
            cn = {'g_mm': {'src/b.cpp'}, 'g_mm2': {'src/b.cpp'}, 'stub_a': {'src/b.cpp'}, 'g_two': {'src/b.cpp'}}
            pr = set()
            rows = bodies.exe_data_pass(cn, os.path.join(d, 'x.exe'), d, udata, lambda a: a >= 0x500000,
                                        placed_refs=pr)
            # placed code references g_two (its sites disagree): decl-data must never give it zeros
            self.assertIn('g_two', pr)
            self.assertIn('g_mm', pr)
            # declared-only C++ variables: matched by the parsed qualified name of the MSVC data name
            msvc, _ = tables({'?ms_k@Pt@@2U1@B', '?g_two@@3HA'}, set())
            mem = {'_ZN2Pt4ms_kE': (('Pt', 'ms_k'), {'src/b.cpp'}), '_ZN2Rt4ms_kE': (('Rt', 'ms_k'), {'src/b.cpp'})}
            mrows = bodies.exe_data_pass({}, os.path.join(d, 'x.exe'), d, udata, lambda a: a >= 0x500000, mem, msvc)
            self.assertEqual(bodies.pristine_data_names(d, 1, {'ms_k', 'zz'}), {'?ms_k@Pt@@2U1@B'})
        self.assertEqual({(r.name, r.cls, r.addr, r.target) for r in rows},
                         {('g_mm', 'exe-data', MM, '__fnv_005f6238'), ('g_mm2', 'exe-data', MM, '__fnv_005f6238')})
        self.assertEqual({(r.name, r.cls, r.addr, r.target, r.units) for r in mrows},
                         {('_ZN2Pt4ms_kE', 'exe-data', PTK, '__fnv_005f8000', 'src/b.cpp')})
        with tempfile.TemporaryDirectory() as d:
            refs = os.path.join(d, 'refs.tsv')
            open(refs, 'w').write('src/a.cpp\tg_mm\tunresolved\t\t\t\t\n'
                                  'src/a.cpp#bodies1\tg_sink_00401000\tunresolved\t\t\t\t\n'
                                  'src/a.cpp\t_ZN1A1fEv\tunresolved\t\t\t\t\n'
                                  'src/a.cpp\tg_ok\tdata\tunit/cname\t005f0000\t__fnv_005f0000\t\n')
            self.assertEqual(bodies.load_cnames(refs), {'g_mm': {'src/a.cpp'}, 'g_sink_00401000': {'src/a.cpp'}})
        bind = {'g_mm': ('body-exe-data', '__fnv_005f6238'), '_ZN1A1fEv': ('body-addr', '__fnv_00401000')}
        self.assertEqual(fnvlink.body_data_addr(bind, 'g_mm'), MM)
        self.assertIsNone(fnvlink.body_data_addr(bind, '_ZN1A1fEv'))
        self.assertIsNone(fnvlink.body_data_addr(bind, 'g_none'))
        with open(fnvlink.__file__) as fo:   # resolve binds it like an address (data, IAT slot, TLS)
            self.assertIn("cls, tier, addr, target = 'addr', 'body/exe-data', body_data_addr(body_bind, un), None",
                          fo.read())

    def test_crt_rule(self):
        """The MSVC STL throwers of the decomp's copies throw through libstdc++; a class placement delete is a
        ret; anything else (another member, a delete with one parameter) is not a crt-rule name."""
        def k(n, d):
            return bodies.crt_rule(sigs.parse_itanium(n, d))
        self.assertEqual(k('_ZN10StringBase5_XlenEv', 'StringBase::_Xlen()'),
                         ('throw', '_ZSt20__throw_length_errorPKc', 'string too long'))
        self.assertEqual(k('_ZNK6Vector5_XranEv', 'Vector::_Xran() const'),
                         ('throw', '_ZSt20__throw_out_of_rangePKc', 'invalid vector<T> position'))
        self.assertEqual(k('_ZN4Obj8dlEPvS0_', 'Obj8::operator delete(void*, void*)'), ('ret', None, None))
        self.assertIsNone(k('_ZN4Obj8dlEPv', 'Obj8::operator delete(void*)'))
        self.assertIsNone(k('_ZN10StringBase4TidyEv', 'StringBase::Tidy()'))
        self.assertIsNone(k('_Z5_Xlenv', '_Xlen()'))            # not a member: no class contract
        rows = [bodies.Row('_ZN10StringBase5_XlenEv', 'missing', None, None, 'src/a.cpp', 'no definition', None),
                bodies.Row('_ZN4Obj8dlEPvS0_', 'missing', None, None, 'src/a.cpp', 'no definition', None),
                bodies.Row('_ZN3Foo3GetEv', 'missing', None, None, 'src/a.cpp', 'no definition', None),
                bodies.Row('_ZN3Bar5_XlenEv', 'exe-addr', 0x401000, '__fnv_00401000', 'src/a.cpp', '', None)]
        dem = {'_ZN10StringBase5_XlenEv': 'StringBase::_Xlen()', '_ZN4Obj8dlEPvS0_': 'Obj8::operator delete(void*, void*)',
               '_ZN3Foo3GetEv': 'Foo::Get()', '_ZN3Bar5_XlenEv': 'Bar::_Xlen()'}

        def it(n):
            return sigs.parse_itanium(n, dem[n])
        got = bodies.crt_pass(rows, it)
        self.assertEqual([r.cls for r in got], ['crt-rule', 'crt-rule', 'missing', 'exe-addr'])
        asm = bodies.emit_crt([r for r in got if r.cls == 'crt-rule'], it)
        self.assertIn('    b _ZSt20__throw_length_errorPKc', asm)
        self.assertIn('.asciz "string too long"', asm)
        self.assertIn('"_ZN4Obj8dlEPvS0_":\n    ret', asm)
        as_ = shutil.which('aarch64-linux-gnu-as')
        if as_:
            with tempfile.TemporaryDirectory() as d:
                open(os.path.join(d, 'c.S'), 'w').write(asm)
                r = subprocess.run([as_, '-mabi=ilp32', os.path.join(d, 'c.S'), '-o', os.path.join(d, 'c.o')],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)


    def test_crt_rule_streambuf_lock(self):
        """The decomp's basic_streambuf copies never define _Lock / _Unlock: MSVC's are `_Mylock._Lock()` on the
        _Mutex at +4 (exe: `add ecx, 4; call 0x00ec3277` in the _Sentry_base constructor). The stub adds 4 to
        `this` and tail-calls S4's mutex function; another class, or a member with parameters, is not the rule."""
        def k(n, d):
            return bodies.crt_rule(sigs.parse_itanium(n, d))
        n = '_ZN12fnv_00ad47f03std15basic_streambufIcNS0_11char_traitsIcEEE5_LockEv'
        d = 'fnv_00ad47f0::std::basic_streambuf<char, fnv_00ad47f0::std::char_traits<char> >::_Lock()'
        self.assertEqual(k(n, d), ('mutex', 'fnvnx_mutex_lock', 'this + 4'))
        n2 = n.replace('5_LockEv', '7_UnlockEv')
        d2 = d.replace('_Lock()', '_Unlock()')
        self.assertEqual(k(n2, d2), ('mutex', 'fnvnx_mutex_unlock', 'this + 4'))
        self.assertIsNone(k('_ZN6Widget5_LockEv', 'Widget::_Lock()'))
        rows = [bodies.Row(n, 'missing', None, None, 'src/a.cpp', 'no definition', None),
                bodies.Row(n2, 'missing', None, None, 'src/a.cpp', 'no definition', None)]
        dem = {n: d, n2: d2}

        def it(x):
            return sigs.parse_itanium(x, dem[x])
        got = bodies.crt_pass(rows, it)
        self.assertEqual([r.cls for r in got], ['crt-rule', 'crt-rule'])
        asm = bodies.emit_crt(got, it)
        self.assertIn('    add x0, x0, #4\n    b fnvnx_mutex_lock', asm)
        self.assertIn('    add x0, x0, #4\n    b fnvnx_mutex_unlock', asm)

    def test_msvc_inline_retry(self):
        """W44 (rows 24 / 54): an msvc-inline name its host did not emit goes on to tree-inline; a host that does not
        compile, a refused = default and a name that is no msvc-inline row do not."""
        R = bodies.Row
        rows = [R('a', 'msvc-inline', None, None, 'u', '', 'h'), R('b', 'msvc-inline', None, None, 'u', '', 'h'),
                R('c', 'special-default', None, None, 'u', '', 'h'), R('d', 'msvc-inline', None, None, 'u', '', 'h')]
        failed = {'a': 'synth-failed (not emitted by the host)', 'b': 'host does not compile',
                  'c': 'synth-failed (not emitted by the host)'}
        self.assertEqual(bodies.msvc_inline_retry(rows, failed), {'a'})

    def test_crt_rule_facet_register(self):
        """W44 (row 22): the decomp's facet copies declare the static `_Register(facet*)` or the member `_Register()`
        and define neither; MSVC's is _Facet_Register(facet) at 0x00ec3599, S4's fnvnx_facet_register. The stub
        tail-calls it with the facet in x0; another class, or another parameter, is not the rule."""
        def k(n, d):
            return bodies.crt_rule(sigs.parse_itanium(n, d))
        n = '_ZN12fnv_00acfab05facet9_RegisterEPS0_'
        d = 'fnv_00acfab0::facet::_Register(fnv_00acfab0::facet*)'
        self.assertEqual(k(n, d), ('facet', 'fnvnx_facet_register', 'the facet'))
        n2 = '_ZN5facet9_RegisterEv'
        d2 = 'facet::_Register()'
        self.assertEqual(k(n2, d2), ('facet', 'fnvnx_facet_register', 'the facet'))
        self.assertIsNone(k('_ZN5facet9_RegisterEi', 'facet::_Register(int)'))
        self.assertIsNone(k('_ZN6Widget9_RegisterEv', 'Widget::_Register()'))
        dem = {n: d, n2: d2}

        def it(x):
            return sigs.parse_itanium(x, dem[x])
        got = bodies.crt_pass([bodies.Row(x, 'missing', None, None, 'src/a.cpp', 'no definition', None)
                               for x in (n, n2)], it)
        self.assertEqual([r.cls for r in got], ['crt-rule', 'crt-rule'])
        asm = bodies.emit_crt(got, it)
        self.assertEqual(asm.count(':\n    b fnvnx_facet_register\n'), 2)

    def test_crt_rule_niptr(self):
        """439a row 51: NiPointer<ProjectileAuxiliaryData>::~NiPointer() (forward-declared T, inlined everywhere in the
        exe) tail-calls NiPointer<NiRefObject>'s D1; NiPointer<NiRefObject> itself, D0 and other classes are not the
        rule; FNVNX_NIPTR=0 turns it off."""
        def k(n, d):
            return bodies.crt_rule(sigs.parse_itanium(n, d))
        n = '_ZN9NiPointerI23ProjectileAuxiliaryDataED1Ev'
        d = 'NiPointer<ProjectileAuxiliaryData>::~NiPointer()'
        self.assertEqual(k(n, d), ('niptr', '_ZN9NiPointerI11NiRefObjectED1Ev', 'NiPointer<NiRefObject>'))
        self.assertEqual(k('_ZN9NiPointerI23ProjectileAuxiliaryDataED2Ev', d)[0], 'niptr')
        self.assertIsNone(k('_ZN9NiPointerI11NiRefObjectED1Ev', 'NiPointer<NiRefObject>::~NiPointer()'))
        self.assertIsNone(k('_ZN7NiLightD1Ev', 'NiLight::~NiLight()'))
        os.environ['FNVNX_NIPTR'] = '0'
        try:
            self.assertIsNone(k(n, d))
        finally:
            del os.environ['FNVNX_NIPTR']
        got = bodies.crt_pass([bodies.Row(n, 'missing', None, None, 'src/a.cpp', 'no definition', None)],
                              lambda x: sigs.parse_itanium(x, d))
        self.assertEqual(got[0].cls, 'crt-rule')
        asm = bodies.emit_crt(got, lambda x: sigs.parse_itanium(x, d))
        self.assertIn('"%s":\n    b _ZN9NiPointerI11NiRefObjectED1Ev\n' % n, asm)

    def test_code_only_externs(self):
        """A name the pristine object names only in a vftable slot is not a direct call (the vcall class may take
        it); one a code section names is."""
        obj = coff({'?f@@YAXXZ': (b'\xe8\0\0\0\0\xc3', {1: '?g@@YAXXZ'})}, ['?g@@YAXXZ', '?v@A@@UAEXXZ'],
                   data={'??_7A@@6B@': (b'\0' * 4, {0: '?v@A@@UAEXXZ'})})
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'u0.obj'), 'wb') as fo:
                fo.write(obj)
            self.assertEqual(bodies.pristine_externs(d, 0), {'?g@@YAXXZ', '?v@A@@UAEXXZ'})
            self.assertEqual(bodies.pristine_externs(d, 0, code_only=True), {'?g@@YAXXZ'})



class Fold(unittest.TestCase):
    """template-fold: pointer instantiations of a template member that units.json places at one native address
    (two or more there) bind a new pointer instantiation to it; one instantiation alone, two addresses, or a
    non-pointer argument do not."""

    def test_fold(self):
        units_j = {'units': [{'source': 'src/a.cpp', 'functions': [
            {'symbol': '??1?$List@PAD@@QAE@XZ', 'address': '00405000'},               # List<char*>::~List()
            {'symbol': '??1?$List@PAH@@QAE@XZ', 'address': '00405000'},               # List<int*>::~List()
            {'symbol': '?Add@?$List@PAD@@QAEXAAPAD@Z', 'address': '00405100'},         # List<char*>::Add(char*&)
            {'symbol': '?Add@?$List@PAH@@QAEXAAPAH@Z', 'address': '00405100'},
            {'symbol': '?Get@?$List@PAD@@QAEPADXZ', 'address': '00405200'},            # one instantiation only
            {'symbol': '?Pop@?$List@PAD@@QAEXXZ', 'address': '00405300'},              # two addresses
            {'symbol': '?Pop@?$List@PAH@@QAEXXZ', 'address': '00405310'}],
            'symbols': {}}]}
        native = {0x00405000: 'X', 0x00405100: 'F', 0x00405200: 'F', 0x00405300: 'F', 0x00405310: 'F'}
        want = {'_ZN4ListIP3FooED1Ev': ('00405000', 'D1'),       # List<Foo*>::~List()
                '_ZN4ListIP3FooED2Ev': ('00405000', 'D2'),
                '_ZN4ListIP3FooE3AddERS1_': ('00405100', None),  # List<Foo*>::Add(Foo*&)
                '_ZN4ListIP3FooE3GetEv': (None, None),
                '_ZN4ListIP3FooE3PopEv': (None, None),
                '_ZN4ListIiED1Ev': (None, None)}                 # List<int>: not a pointer argument
        with tempfile.TemporaryDirectory() as d:
            uj = os.path.join(d, 'units.json')
            with open(uj, 'w') as f:
                json.dump(units_j, f)
            units = fnvlink.load_units(uj)
        allms = {n for u in units.values() for n in list(u.refs) + [n for _, n in u.defs]}
        msvc, itanium = tables(allms, want)
        cl = bodies.Classifier(units, native, msvc, itanium, None, ['src/a.cpp'])
        for n, (a, _) in want.items():
            r = cl.classify(n, {'src/b.cpp'})
            if a:
                self.assertEqual((r.cls, r.target), ('template-fold', '__fnv_' + a), n)
            else:
                self.assertNotEqual(r.cls, 'template-fold', n)

        # a failed msvc-inline synthesis of a foldable name falls back to the fold; others stay failed
        rows = [bodies.Row('_ZN4ListIP3FooED2Ev', 'msvc-inline', None, None, 'src/b.cpp', 'x', 'src/b.cpp'),
                bodies.Row('_ZN4ListIP3FooE3GetEv', 'msvc-inline', None, None, 'src/b.cpp', 'x', 'src/b.cpp'),
                bodies.Row('_ZN4ListIiED1Ev', 'msvc-inline', None, None, 'src/b.cpp', 'x', 'src/b.cpp')]
        failed = {r.name: 'synth-failed (not emitted)' for r in rows}
        out = bodies.fold_fallback(rows, failed, cl, itanium)
        self.assertEqual([(r.cls, r.target) for r in out], [('template-fold', '__fnv_00405000'),
                                                          ('msvc-inline', None), ('msvc-inline', None)])
        self.assertEqual(sorted(failed), ['_ZN4ListIP3FooE3GetEv', '_ZN4ListIiED1Ev'])


class RetrySpecial(unittest.TestCase):
    def test_retry(self):
        """An msvc-inline special member whose host emitted nothing is tried again as the implicit member
        (special-default) in the first referencing unit; a plain member or a template's destructor is not."""
        names = ['_ZN16AabbCollectorE15D1Ev', '_ZN3Foo3GetEv', '_ZN4ListIiED1Ev', '_ZN3FooC1ERKS_']
        _, itanium = tables(set(), set(names))
        rows = [bodies.Row(n, 'msvc-inline', None, None, 'src/z.cpp,src/b.cpp', 'x', 'src/b.cpp') for n in names]
        rows.append(bodies.Row('_ZN3BarD1Ev', 'special-default', None, None, 'src/b.cpp', 'x', 'src/b.cpp'))
        failed = {r.name: 'synth-failed (not emitted)' for r in rows}
        _, itanium = tables(set(), set(names) | {'_ZN3BarD1Ev'})
        got = bodies.retry_special(rows, failed, itanium)
        self.assertEqual({h: sorted(r.name for r in rs) for h, rs in got.items()},
                         {'src/b.cpp': ['_ZN16AabbCollectorE15D1Ev', '_ZN3FooC1ERKS_']})


class NotInImage(unittest.TestCase):
    def test_not_in_image(self):
        """A units.json address outside the image (0x0, 0x14, 0x65: values, not addresses) binds nothing: resolve
        leaves the reference unresolved (a trap stub), never `__fnv_00000065`."""
        for a in (0x0, 0x14, 0x65, 0x400fff):
            self.assertFalse(fnvlink.in_image(a), hex(a))
        for a in (0x401000, 0x00e9ecc0, 0x011f6238, 0x01272000):
            self.assertTrue(fnvlink.in_image(a), hex(a))
        with open(fnvlink.__file__) as fo:
            src = fo.read()
        self.assertIn("if addr is not None and cls == 'addr' and not in_image(addr):", src)
        self.assertLess(src.index("and not in_image(addr):"), src.index("ak = kind_of_addr(addr)"))


class UnitTarg(unittest.TestCase):
    def test_unit_targ(self):
        """A template member instantiated over a unit-local copy of a class (`Ptr<fnv_00401000::Item>`) binds to the
        global instantiation's address only where the layout probe gives the class one size in both units (and no
        template argument two sizes); a D1 also binds where the native build spells that address FUN_<a>(T*) or
        C::FUN_<a>(). Definitions are never indexed under the relaxed key (a unit copy of the template)."""
        units_j = {'units': [
            {'source': 'src/def.cpp', 'functions': [
                {'symbol': '??1?$Ptr@VItem@@@@QAE@XZ', 'address': '00406000'},             # Ptr<Item>::~Ptr()
                {'symbol': '?Get@?$Ptr@VItem@@@@QBEPAVItem@@XZ', 'address': '00406100'},  # Ptr<Item>::Get()
                {'symbol': '??1?$Ptr@VOther@@@@QAE@XZ', 'address': '00406200'},            # Ptr<Other>::~Ptr()
                {'symbol': '??1?$Ptr@VNode@fnv_00402222@@@@QAE@XZ', 'address': '00406300'}], 'symbols': {}},
            {'source': 'src/ref.cpp', 'functions': [], 'symbols': {}}]}
        native = {0x00406000: 'F', 0x00406100: 'F', 0x00406200: 'F', 0x00406300: 'X'}
        native_names = {0x00406000: '_Z12FUN_00406000PPc', 0x00406100: '_ZNK3PtrI4ItemE3GetEv',
                        0x00406200: '_Z12FUN_00406200ii'}
        layouts = {('src/ref.cpp', 'Ptr<fnv_00401000::Item>'): {4}, ('src/def.cpp', 'Ptr<Item>'): {4},
                   ('src/ref.cpp', 'fnv_00401000::Item'): {8}, ('src/def.cpp', 'Item'): {8},
                   ('src/ref.cpp', 'Ptr<fnv_00401000::Other>'): {4}, ('src/def.cpp', 'Ptr<Other>'): {4},
                   ('src/ref.cpp', 'Ptr<fnv_00401000::Big>'): {4}, ('src/def.cpp', 'Ptr<Big>'): {4},
                   ('src/ref.cpp', 'fnv_00401000::Big'): {12}, ('src/def.cpp', 'Big'): {16}}
        units_j['units'][0]['functions'].append({'symbol': '??1?$Ptr@VBig@@@@QAE@XZ', 'address': '00406000'})
        want = {'_ZN3PtrIN12fnv_004010004ItemEED1Ev': '00406000',    # D1 at FUN_00406000(char**): this only
                '_ZNK3PtrIN12fnv_004010004ItemEE3GetEv': '00406100',  # a member at an F address
                '_ZN3PtrIN12fnv_004010005OtherEED1Ev': None,           # FUN_00406200(int, int): not this only
                '_ZN3PtrIN12fnv_004010003BigEED1Ev': None,             # the argument has two sizes
                '_ZN3PtrIN12fnv_004010004NodeEED1Ev': None,            # no layout evidence (unsized)
                '_ZN3PtrI4NodeED1Ev': None}                            # never another unit's copy
        with tempfile.TemporaryDirectory() as d:
            uj = os.path.join(d, 'units.json')
            with open(uj, 'w') as f:
                json.dump(units_j, f)
            units = fnvlink.load_units(uj)
        allms = {n for u in units.values() for n in list(u.refs) + [n for _, n in u.defs]}
        msvc, itanium = tables(allms, set(want) | set(native_names.values()))
        cl = bodies.Classifier(units, native, msvc, itanium, None, ['src/def.cpp', 'src/ref.cpp'], layouts,
                               native_names)
        for n, a in want.items():
            r = cl.classify(n, {'src/ref.cpp'})
            if a:
                self.assertEqual((r.cls, r.target), ('addr', '__fnv_' + a), n)
                self.assertIn('via unit-targ', r.note, n)
            else:
                self.assertNotEqual(r.cls, 'addr', n)
        # without the layout probe the tier binds nothing
        bare = bodies.Classifier(units, native, msvc, itanium, None, ['src/def.cpp', 'src/ref.cpp'], None,
                                 native_names)
        self.assertNotEqual(bare.classify('_ZNK3PtrIN12fnv_004010004ItemEE3GetEv', {'src/ref.cpp'}).cls, 'addr')
        self.assertEqual([t for t, _ in bodies.def_keys(msvc('??1?$Ptr@VNode@fnv_00402222@@@@QAE@XZ'))],
                         ['shell', 'global'])
        self.assertEqual(bodies.targ_types(['Ptr<fnv_00401000::Item*,4>', '{dtor}']),
                         ['Ptr<fnv_00401000::Item*,4>', 'fnv_00401000::Item'])


class CopyPairs(unittest.TestCase):
    """copy-addr / copy_key / LayoutGate (s2d crt-import, not-compiled): a member of one unit-local copy of a class
    binds to the address units.json gives the same member of another copy, only with one layout on both sides."""

    class Gate:
        def __init__(self, sizes):
            self.sizes_ = sizes

        def same(self, ref_units, ref_types, def_unit, def_types):
            for u in ref_units:
                for rt, dt in zip(ref_types, def_types):
                    a, b = self.sizes_.get((u, rt)), self.sizes_.get((def_unit, dt))
                    if a is None or b is None or a != b:
                        return False, 'no'
            return True, 'sizes'

    def test_copy_addr(self):
        units_j = {'units': [
            {'source': 'src/def.cpp', 'functions': [
                {'symbol': '?Flush@?$Os@D@std@fnv_00a00010@@QAEAAV123@XZ', 'address': '00405000'},
                {'symbol': '?Put@?$Os@D@std@fnv_00a00010@@QAEAAV123@D@Z', 'address': '00405100'},
                {'symbol': '?Put@?$Os@D@std@fnv_00a00030@@QAEAAV123@D@Z', 'address': '00405200'}],
             'symbols': {}},
            {'source': 'src/ref.cpp', 'functions': [], 'symbols': {}}]}
        native = {0x00405000: 'F', 0x00405100: 'F', 0x00405200: 'F'}
        with tempfile.TemporaryDirectory() as d:
            uj = os.path.join(d, 'units.json')
            with open(uj, 'w') as f:
                json.dump(units_j, f)
            units = fnvlink.load_units(uj)
        flush, put, other = ('_ZN12fnv_00a000203std2OsIcE5FlushEv', '_ZN12fnv_00a000203std2OsIcE3PutEc',
                             '_ZN12fnv_00a000203std2OsIcE5FlushEi')
        allms = {n for u in units.values() for _, n in u.defs}
        msvc, itanium = tables(allms, {flush, put, other})
        cl = bodies.Classifier(units, native, msvc, itanium, None, ['src/def.cpp', 'src/ref.cpp'])
        self.assertEqual(bodies.copy_key(itanium(flush), variant=False),
                         bodies.copy_key(msvc('?Flush@?$Os@D@std@fnv_00a00010@@QAEAAV123@XZ'), variant=False))
        self.assertEqual(cl.by_copy(itanium(flush))[0], 0x00405000)
        self.assertIsNone(cl.by_copy(itanium(put)))      # two copies at two addresses
        self.assertIsNone(cl.by_copy(itanium(other)))    # another overload
        self.assertEqual(bodies.copy_types(itanium(put)), ['fnv_00a00020::std::Os<char>'])
        rows = [bodies.Row(n, 'missing', None, None, 'src/ref.cpp', 'x', None) for n in (flush, put)]
        same = self.Gate({('src/ref.cpp', 'fnv_00a00020::std::Os<char>'): 56,
                          ('src/def.cpp', 'fnv_00a00010::std::Os<char>'): 56})
        got = {r.name: r for r in bodies.copy_addr_pass(rows, cl, itanium, same)}
        self.assertEqual((got[flush].cls, got[flush].target), ('copy-addr', '__fnv_00405000'))
        self.assertEqual(got[put].cls, 'missing')
        differ = self.Gate({('src/ref.cpp', 'fnv_00a00020::std::Os<char>'): 1,
                            ('src/def.cpp', 'fnv_00a00010::std::Os<char>'): 56})
        r = bodies.copy_addr_pass(rows[:1], cl, itanium, differ)[0]
        self.assertEqual(r.cls, 'missing')
        self.assertIn('copy-addr 00405000', r.note)

    def test_tree_inline_copy_rename(self):
        """A global name whose body is in a unit copy of its class: renamed from the copy's emitted symbol when the
        gate gives both one size; refused (stays missing) when the sizes differ."""
        src = ('struct Mm { int a; void Free(void *); };\n'
               'namespace fnv_00a1cb80 { struct Mm { int a; void Free(void *p) { a = (int)(long)p; } }; }\n'
               'namespace fnv_00a1cb80 { void use(Mm *m) { m->Free(0); } }\n')
        want = '_ZN2Mm4FreeEPv'
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'src'))
            os.makedirs(os.path.join(d, 'include'))
            with open(os.path.join(d, 'src', 'm.cpp'), 'w') as f:
                f.write(src)
            dem = {}

            def it(n):
                if n not in dem:
                    dem.update(fnvlink.batch_demangle(fnvlink.CXXFILT, [n]))
                return sigs.parse_itanium(n, dem[n])
            rows = [bodies.Row(want, 'missing', None, None, 'src/r.cpp', '', None)]
            ok = self.Gate({('src/r.cpp', 'Mm'): 4, ('src/m.cpp', 'fnv_00a1cb80::Mm'): 4})
            path, roots, failed = bodies.synth_host(d, d, 'src/m.cpp', rows, it, True, gate=ok)
            self.assertEqual((roots, failed), ({want}, {}))
            no = self.Gate({('src/r.cpp', 'Mm'): 1, ('src/m.cpp', 'fnv_00a1cb80::Mm'): 4})
            path, roots, failed = bodies.synth_host(d, d, 'src/m.cpp', rows, it, True, gate=no)
            self.assertEqual(roots, set())
            self.assertIn(want, failed)


class Thunks(unittest.TestCase):
    def test_thunks(self):
        """Thunk names parse to (this adjustment, vcall offset, target); refs.tsv's undefined thunks load; the stubs
        assemble (ILP32) and branch to the target by name."""
        self.assertEqual(bodies.thunk_parts('_ZThn24_N3Own3FunEv'), (-24, None, '_ZN3Own3FunEv'))
        self.assertEqual(bodies.thunk_parts('_ZTh8_N1A1gEv'), (8, None, '_ZN1A1gEv'))
        self.assertEqual(bodies.thunk_parts('_ZTv0_n12_N1A1fEv'), (0, 12, '_ZN1A1fEv'))
        self.assertIsNone(bodies.thunk_parts('_ZN1A1fEv'))
        with tempfile.TemporaryDirectory() as d:
            refs = os.path.join(d, 'refs.tsv')
            open(refs, 'w').write('src/a.cpp\t_ZThn24_N3Own3FunEv\tspecial-unresolved:non-virtual-thunk-to\t\t\t\t\n'
                                  'src/b.cpp#x\t_ZTv0_n12_N1A1fEv\tspecial-unresolved:virtual-thunk-to\t\t\t\t\n'
                                  'src/a.cpp\t_ZTV1A\tspecial-unresolved:vtable-for\t\t\t\t\n')
            got = bodies.load_thunks(refs)
            self.assertEqual(got, {'_ZThn24_N3Own3FunEv': {'src/a.cpp'}, '_ZTv0_n12_N1A1fEv': {'src/b.cpp'}})
            asm = bodies.emit_thunks(got)
            self.assertIn('    sub w0, w0, #24\n    b "_ZN3Own3FunEv"', asm)
            as_ = shutil.which('aarch64-linux-gnu-as')
            if as_:
                open(os.path.join(d, 't.S'), 'w').write(asm)
                r = subprocess.run([as_, '-mabi=ilp32', os.path.join(d, 't.S'), '-o', os.path.join(d, 't.o')],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)



class AddrHost(unittest.TestCase):
    def test_gdel_line_and_stubs(self):
        """code-defmissing rows load by address; a ??_G line destroys / deletes like MSVC's and compiles to
        __fnv_<addr>; a template class is refused; the stub branches to the emitted symbol."""
        with tempfile.TemporaryDirectory() as d:
            refs = os.path.join(d, 'refs.tsv')
            open(refs, 'w').write('src/a.cpp\t_ZN1X1fEi\tcode-defmissing\tunit/exact\t004459e0\t__fnv_004459e0\t?f@X@@QAEHH@Z\n'
                                  'src/b.cpp#bd\t_ZN1Y1fEi\tcode-defmissing\tunit/exact\t004459e0\t__fnv_004459e0\t\n'
                                  'src/a.cpp\t_ZN1Z1fEv\tcode\tunit/exact\t00401000\t__fnv_00401000\t\n')
            self.assertEqual(bodies.load_defmissing(refs), {0x004459e0: {'src/a.cpp', 'src/b.cpp'}})
            ms = fnvlink.DemangleCache(os.path.join(d, 'ms.tsv'), fnvlink.UNDNAME)
            names = ['??_GRec@@QAEPAXI@Z', '??_G?$L@H@@QAEPAXI@Z', '??_GV@@UAEPAXI@Z']
            ms.need(names)
            line = bodies.gdel_line(0x004459e0, sigs.parse_msvc(names[0], ms[names[0]]))
            self.assertIn('extern "C" void *__fnv_004459e0(::Rec *p, unsigned int f)', line)
            self.assertIn('p->::Rec::~Rec(); if (f & 1) __fnvnx_gd::del(p, 0);', line)
            self.assertNotIn('delete p', line)   # never a virtual delete-expression
            self.assertIsNone(bodies.gdel_line(0x00401000, sigs.parse_msvc(names[1], ms[names[1]])))
            gxx = shutil.which('aarch64-linux-gnu-g++') or shutil.which('g++')
            if gxx:
                src = os.path.join(d, 'g.cpp')
                open(src, 'w').write('struct Rec { int k; ~Rec(); };\n' + bodies.GDEL_HELPER + '\n' + line + '\n')
                r = subprocess.run([gxx, '-std=gnu++17', '-c', src, '-o', os.path.join(d, 'g.o')],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-400:])
                nm = subprocess.run(['nm', os.path.join(d, 'g.o')], capture_output=True, text=True).stdout
                self.assertIn('T __fnv_004459e0', nm)
                self.assertIn('U _ZdlPv', nm)                      # no class delete: the global one
                # R6 0x00ddeff0: a virtual destructor and the class's own sized operator delete: called directly
                # with sizeof(C) after the non-virtual destructor, never through the vtable (D0)
                lv = bodies.gdel_line(0x00ddeff0, sigs.parse_msvc(names[2], ms[names[2]]))
                open(src, 'w').write('typedef __SIZE_TYPE__ size_t; void release(void *, size_t);\n'
                                     'struct V { int a[21]; virtual ~V(); void operator delete(void *p, size_t n) '
                                     '{ release(p, n); } };\n' + bodies.GDEL_HELPER + '\n' + lv + '\n')
                r = subprocess.run([gxx, '-std=gnu++17', '-O1', '-c', src, '-o', os.path.join(d, 'v.o')],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-400:])
                nm = subprocess.run(['nm', os.path.join(d, 'v.o')], capture_output=True, text=True).stdout
                self.assertIn('release', nm)                       # the class's delete (inlined), not ::delete
                self.assertNotIn('_ZdlPv', nm)
                self.assertIn('U _ZN1VD1Ev', nm)                   # the destructor itself, not D0
                self.assertNotIn('D0Ev', nm)
            asm = bodies.emit_addr_stubs({0x0046ffd0: '_ZNK7NiPoint2mlEf', 0x004459e0: '__fnv_004459e0'})
            self.assertIn('"__fnv_0046ffd0":\n    b "_ZNK7NiPoint2mlEf"', asm)
            self.assertNotIn('"__fnv_004459e0":', asm)



class DeclData(unittest.TestCase):
    def test_data_line(self):
        """A global, a namespace or class member get a typed zero definition; a template member does not;
        refs.tsv's unresolved C names load except address-named ones and mangled names."""
        self.assertEqual(bodies.data_line(['g_count']), 'decltype(g_count) g_count {};')
        self.assertEqual(bodies.data_line(['C', 's_n']), 'decltype(C::s_n) C::s_n {};')
        self.assertIsNone(bodies.data_line(['L<int>', 's_n']))
        with tempfile.TemporaryDirectory() as d:
            refs = os.path.join(d, 'refs.tsv')
            open(refs, 'w').write('src/a.cpp\twblk_u\tunresolved\t\t\t\t\n'
                                  'src/a.cpp\tFUN_00401000\tunresolved\t\t\t\t\n'
                                  'src/a.cpp\t_ZN1A1fEv\tunresolved\t\t\t\t\n'
                                  'src/b.cpp\twblk_u\tunresolved\t\t\t\t\n')
            self.assertEqual(bodies.load_cdata(refs), {'wblk_u': {'src/a.cpp', 'src/b.cpp'}})
            gxx = shutil.which('aarch64-linux-gnu-g++') or shutil.which('g++')
            if gxx:
                src = os.path.join(d, 'd.cpp')
                open(src, 'w').write('extern float wblk_u[4]; struct C { static int s_n; };\n' +
                                     bodies.data_line(['wblk_u']) + '\n' + bodies.data_line(['C', 's_n']) + '\n')
                r = subprocess.run([gxx, '-c', src, '-o', os.path.join(d, 'd.o')], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-400:])
                nm = subprocess.run(['nm', '-S', os.path.join(d, 'd.o')], capture_output=True, text=True).stdout
                self.assertRegex(nm, r'00000010 B wblk_u')



class Sigs(unittest.TestCase):
    def test_conversion_to_pointer_and_unary_star(self):
        """MSVC and Itanium spellings of a conversion operator to a namespaced pointer type and of a unary operator*
        give the same exact key (R6: 82 names left unresolved on link 9f865b0a)."""
        pairs = [('??BSpherePtr@fnv_009614b0@@QBEPAUSphere@1@XZ', '_ZNK12fnv_009614b09SpherePtrcvPNS_6SphereEEv'),
                 ('??DMatView@@QAEAAMXZ', '_ZN7MatViewdeEv')]
        ms = fnvlink.batch_demangle(fnvlink.UNDNAME, [m for m, _ in pairs])
        it = fnvlink.batch_demangle(fnvlink.CXXFILT, [i for _, i in pairs])
        for m, i in pairs:
            a, b = sigs.parse_msvc(m, ms[m]), sigs.parse_itanium(i, it[i])
            self.assertEqual((a.kind, b.kind), ('func', 'func'), m)
            self.assertTrue({k for t, k in a.keys() if t == 'exact'} & {k for t, k in b.keys() if t == 'exact'}, m)


class LinkShape(unittest.TestCase):
    """R2 recon2-005: a name units.json maps to an address defined under another MSVC shape (synthetic names)."""

    def setUp(self):
        self.ms_names = ['??BHandle@@QBEPAUObj@@XZ',      # Obj* Handle::operator Obj*() const (thiscall)
                         '?FUN_00501000@@YIHPAH@Z',       # int __fastcall FUN_00501000(int*)
                         '?Get@Handle@@QAEPAUObj@@H@Z',   # Obj* Handle::Get(int) (thiscall, one stack word)
                         '??RFunctor@@QAEXPAX@Z',         # void Functor::operator()(void*)
                         '??_EBox@@QAEPAXI@Z',            # Box::`vector deleting dtor'(unsigned)
                         '?f@@YAP6AXXZH@Z']               # void (*f(int))(void)
        self.dm = fnvlink.batch_demangle(fnvlink.UNDNAME, self.ms_names)
        self.di = fnvlink.batch_demangle(fnvlink.CXXFILT, ['_ZN7FunctorclEPv', '_ZN3Box12__vecDelDtorEj',
                                                           '_ZN3Box7__otherEj'])

    def ms(self, n):
        return sigs.parse_msvc(n, self.dm[n])

    def it(self, n):
        return sigs.parse_itanium(n, self.di[n])

    def test_register_compatible_shapes_are_same(self):
        """thiscall `this` in ecx against a fastcall first argument in ecx: x0 on AArch64 on both sides, an integer
        or pointer result in eax / x0 -> no bridge; a reference that also pushes a stack word stays x86-inconsistent."""
        import abi
        d = self.ms('?FUN_00501000@@YIHPAH@Z')
        self.assertEqual(abi.compare(self.ms('??BHandle@@QBEPAUObj@@XZ'), d).kind, 'same')
        v = abi.compare(self.ms('?Get@Handle@@QAEPAUObj@@H@Z'), d)
        self.assertEqual(v.kind, 'x86-inconsistent')
        self.assertIn('pop-mismatch', v.classes)

    def test_call_operator_parses(self):
        """`X::operator()` ends in ')' and is not a function returning a function pointer: it parses and pairs."""
        m, i = self.ms('??RFunctor@@QAEXPAX@Z'), self.it('_ZN7FunctorclEPv')
        self.assertEqual((m.kind, m.qual, m.params, m.cc), ('func', ['Functor', 'operator()'], ['void *'], 'thiscall'))
        self.assertTrue({k for t, k in m.keys() if t == 'exact'} & {k for t, k in i.keys() if t == 'exact'})
        self.assertNotEqual(self.ms('?f@@YAP6AXXZH@Z').kind, 'func')

    def test_vec_del_dtor_member_binds_to_vdd(self):
        """A decomp member named `__vecDelDtor` is MSVC's `??_E`: classify_ref finds its address in the unit; another
        member name of the class does not."""
        idx = fnvlink.KeyIndex()
        idx.add(self.ms('??_EBox@@QAEPAXI@Z'), 0x00502000)
        r = fnvlink.classify_ref('_ZN3Box12__vecDelDtorEj', self.it('_ZN3Box12__vecDelDtorEj'), idx,
                                 fnvlink.KeyIndex(), fnvlink.KeyIndex(), set(), self.ms)
        self.assertEqual(r[:3], ('addr', 'unit/exact', 0x00502000))
        self.assertEqual(r[4], '??_EBox@@QAEPAXI@Z')
        r = fnvlink.classify_ref('_ZN3Box7__otherEj', self.it('_ZN3Box7__otherEj'), idx,
                                 fnvlink.KeyIndex(), fnvlink.KeyIndex(), set(), self.ms)
        self.assertEqual(r[0], 'unresolved')


if __name__ == '__main__':
    unittest.main()
