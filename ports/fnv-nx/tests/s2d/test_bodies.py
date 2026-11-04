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
import sys
import tempfile
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

    @unittest.skipUnless(shutil.which('aarch64-linux-gnu-as') and shutil.which('aarch64-linux-gnu-ld'),
                         'needs the aarch64 binutils')
    def test_fuzz_image_links_vcall_stubs(self):
        """The fuzz harness's unit image (difffuzz/a64image) links fixed/zz_bd_vcall.o like zz_bridges.o: a call
        of a __fnvnx_bdvc_ stub is not left as a named stub (S1-refs' native-undefined rows)."""
        import subprocess
        sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
        import a64image
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'fixed'))
            os.makedirs(os.path.join(d, 'out'))
            open(os.path.join(d, 'out', 'aliases.ld'), 'w').close()
            for name, src in (('src_u.cpp.o', '.text\n.globl f\nf: b __fnvnx_bdvc_00000001\n'),
                              ('zz_bd_vcall.o', '.text\n.globl __fnvnx_bdvc_00000001\n.type __fnvnx_bdvc_00000001,'
                                                '%function\n__fnvnx_bdvc_00000001: ret\n')):
                sp = os.path.join(d, name + '.s')
                open(sp, 'w').write(src)
                subprocess.run(['aarch64-linux-gnu-as', '-mabi=ilp32', sp, '-o', os.path.join(d, 'fixed', name)],
                               check=True)
            img = a64image.build_many(['src/u.cpp'], d, os.path.join(d, 'w'), 'u')
        self.assertFalse([n for n in img.stubs.values() if n.startswith('__fnvnx_bdvc_')])

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


if __name__ == '__main__':
    unittest.main()
