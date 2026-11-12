"""Tests of gate fpexact check 3c (the bytes of every inline asm) and of the encoding-field table fp_to_int, on real objects. Every object is built by the one
build (verify/recipe.py) as a toy guest root with the game's compiler and the product plugin set (link/tests/realobj.py): C++ units through the plugin set, assembler
units (.S) through the game's driver and assembler. The negative cases are units the recipe builds or byte mutations of its objects. No compiler, assembler or plugin is
called directly and nothing replaces a product file.

  python3 -I lift/proof/tests/test_fpexact_asm.py
"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = os.path.dirname(os.path.dirname(HERE))
TOOL = os.path.dirname(LIFT)
REAL = os.path.dirname(os.path.dirname(TOOL))
sys.path.insert(0, os.path.join(TOOL, 'link', 'tests'))
sys.path.insert(0, os.path.join(LIFT, 'proof'))
sys.path.insert(0, LIFT)
import realobj  # noqa: E402
import gate_fpexact as G  # noqa: E402
from elftools.elf.elffile import ELFFile  # noqa: E402

RT = 'ports/fnv-nx/lift/rt'
ASMARCH = '.arch armv8.3-a+fp16\n'      # the instruction set the unit uses, stated in the unit: the recipe's asm flags are the game's, not per root
CXXFLAGS = ['-std=gnu++17', '-DFNVNX_SDD=1']

POSITIVE = [   # (instruction text, group of the Arm ARM it belongs to)
    ('fcvtzs w0, s1', 'int'), ('fcvtzs x0, d1', 'int'), ('fcvtzu w0, s1', 'int'), ('fcvtzu x0, d1', 'int'), ('fcvtzs w0, h1', 'int'),
    ('fcvtns w0, s1', 'int'), ('fcvtnu x0, d1', 'int'), ('fcvtas w0, s1', 'int'), ('fcvtau x0, d1', 'int'),
    ('fcvtms w0, s1', 'int'), ('fcvtmu x0, d1', 'int'), ('fcvtps w0, s1', 'int'), ('fcvtpu x0, d1', 'int'), ('fjcvtzs w0, d1', 'int'),
    ('fcvtzs w0, s1, #3', 'fixed'), ('fcvtzu x0, d1, #7', 'fixed'),
    ('fcvtzs s0, s1', 'scalar'), ('fcvtzu d0, d1', 'scalar'), ('fcvtns s0, s1', 'scalar'), ('fcvtnu d0, d1', 'scalar'), ('fcvtas s0, s1', 'scalar'),
    ('fcvtau d0, d1', 'scalar'), ('fcvtms s0, s1', 'scalar'), ('fcvtmu d0, d1', 'scalar'), ('fcvtps s0, s1', 'scalar'), ('fcvtpu d0, d1', 'scalar'),
    ('fcvtzs h0, h1', 'scalar16'), ('fcvtns h0, h1', 'scalar16'), ('fcvtau h0, h1', 'scalar16'), ('fcvtmu h0, h1', 'scalar16'), ('fcvtps h0, h1', 'scalar16'),
    ('fcvtzs v0.4s, v1.4s', 'vector'), ('fcvtzu v0.2d, v1.2d', 'vector'), ('fcvtns v0.4s, v1.4s', 'vector'), ('fcvtnu v0.2s, v1.2s', 'vector'),
    ('fcvtas v0.4s, v1.4s', 'vector'), ('fcvtau v0.2d, v1.2d', 'vector'), ('fcvtms v0.4s, v1.4s', 'vector'), ('fcvtmu v0.2d, v1.2d', 'vector'),
    ('fcvtps v0.4s, v1.4s', 'vector'), ('fcvtpu v0.2d, v1.2d', 'vector'),
    ('fcvtzs v0.8h, v1.8h', 'vector16'), ('fcvtzu v0.4h, v1.4h', 'vector16'), ('fcvtms v0.8h, v1.8h', 'vector16'), ('fcvtpu v0.4h, v1.4h', 'vector16'),
    ('fcvtzs s0, s1, #3', 'scalar-shift'), ('fcvtzu d0, d1, #9', 'scalar-shift'),
    ('fcvtzs v0.4s, v1.4s, #3', 'vector-shift'), ('fcvtzu v0.2d, v1.2d, #9', 'vector-shift'), ('fcvtzs v0.8h, v1.8h, #2', 'vector-shift'),
]
NEGATIVE = ['fmov w0, s1', 'fmov x0, d1', 'fmov x0, v1.d[1]', 'fmov s0, w1', 'scvtf s0, w1', 'ucvtf d0, x1', 'scvtf s0, s1', 'scvtf v0.4s, v1.4s',
            'ucvtf v0.2d, v1.2d', 'scvtf s0, s1, #3', 'fcvt d0, s1', 'fcvt s0, d1', 'fcvtl v0.2d, v1.2s', 'fcvtn v0.2s, v1.2d', 'fcvtxn s0, d1', 'frintn s0, s1',
            'frintz v0.4s, v1.4s', 'fadd s0, s1, s2', 'fmul v0.4s, v1.4s, v2.4s', 'add w0, w1, w2', 'ldar w0, [x1]', 'stlr w0, [x1]', 'msr fpcr, x0',
            'mrs x0, fpcr', 'fmov v0.4h, #1.0', 'fmov v0.8h, #2.0', 'fcmp s0, s1', 'fabs v0.4s, v1.4s', 'frecpe s0, s1', 'sqxtn v0.4h, v1.4s', 'addp v0.4s, v1.4s, v2.4s']


def judge(path, unit=None):
    u = unit or os.path.basename(path)[:-2]   # recipe layout: obj/<unit>.o
    return G.judge_object('t/' + u, path, u.endswith(('.S', '.s')), u)['f2i:t/' + u]


def sections(path):
    with open(path, 'rb') as fh:
        return {s.name: s.data() for s in ELFFile(fh).iter_sections()}


def relocs(path, secname):
    with open(path, 'rb') as fh:
        elf = ELFFile(fh)
        s = elf.get_section_by_name('.rela' + secname)
        if s is None:
            return []
        st = elf.get_section(s['sh_link'])
        return [(r['r_offset'], r['r_info_type'], st.get_symbol(r['r_info_sym']).name, r['r_addend']) for r in s.iter_relocations()]


def symbols(path):
    """[(name, value, size, type, bind)] of every symbol but the section symbols (their index follows the section list) and the mapping symbols of the asm-range section"""
    with open(path, 'rb') as fh:
        elf = ELFFile(fh)
        mine = {i for i, sec in enumerate(elf.iter_sections()) if sec.name == '.fnvnx.asmrange'}
        st = elf.get_section_by_name('.symtab')
        return sorted((x.name, x['st_value'], x['st_size'], x['st_info']['type'], x['st_info']['bind']) for x in st.iter_symbols()
                      if x['st_info']['type'] != 'STT_SECTION' and x['st_shndx'] not in mine)


def build_cxx(sources, **kw):
    kw.setdefault('flags', CXXFLAGS)
    res = realobj.build(sources, **kw)
    if res['status'] != 'green':
        raise RuntimeError('toy root does not build: %s' % (res.get('failure'),))
    return {os.path.splitext(o['unit'])[0]: o['object'] for o in res['objects']}   # the unit's stem: cbare.c -> 'cbare'


class Table(unittest.TestCase):
    """fp_to_int selects by the encoding fields. The words come from the pinned assembler (the recipe's .S stage), one instruction per line of one unit."""
    @classmethod
    def setUpClass(cls):
        lines = [t for t, _ in POSITIVE] + NEGATIVE
        res = realobj.build({'t.S': ASMARCH + '.text\n' + ''.join('  %s\n' % t for t in lines)})
        if res['status'] != 'green':
            raise RuntimeError('assembler unit does not build: %s' % (res.get('failure'),))
        data = sections(res['objects'][0]['object'])['.text']
        cls.words = [int.from_bytes(data[i:i + 4], 'little') for i in range(0, len(data), 4)]
        cls.lines = lines

    def test_one_word_per_line(self):
        self.assertEqual(len(self.words), len(self.lines))

    def test_every_conversion_group_is_selected_with_its_mnemonic(self):
        for (text, grp), w in zip(POSITIVE, self.words):
            self.assertEqual(G.fp_to_int(w), text.split()[0], '%s (%s, 0x%08x)' % (text, grp, w))

    def test_every_group_of_the_table_is_covered(self):
        self.assertEqual({g for _, g in POSITIVE}, {'int', 'fixed', 'scalar', 'scalar16', 'vector', 'vector16', 'scalar-shift', 'vector-shift'})

    def test_other_floating_point_instructions_are_not_selected(self):
        for text, w in zip(NEGATIVE, self.words[len(POSITIVE):]):
            self.assertIsNone(G.fp_to_int(w), '%s (0x%08x)' % (text, w))


class Real(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rt = sorted(RT + '/' + f for f in os.listdir(os.path.join(REAL, RT)) if f.endswith(('.h', '.json')))
        enter = open(os.path.join(REAL, RT, 'enter.cc')).read()
        mem = ('#include "fnvl.h"\n'
               'extern "C" u32 rd(u32 a){ GM<u32> m = {a}; return m; }\n'
               'extern "C" void wr(u32 a, u32 v){ GM<u32> m = {a}; m = v; }\n')
        objs = build_cxx({'enter': enter, 'mem': mem}, files=rt, include=[RT])
        cls.enter, cls.mem = objs['enter'], objs['mem']

    def report(self, o):
        with open(o[:-2] + '.f2i.json') as fh:
            return json.load(fh)

    def test_enter_fpcr_asm_decodes_clean(self):
        r = G.scan_asmranges(self.enter)
        self.assertEqual(r['findings'], [])
        self.assertEqual(r['fcvt'], 0)
        self.assertGreaterEqual(r['entries'], 3)          # fp_set: one asm, fp_park: two; plus the ordering asm the order plugin makes

    def test_fnvl_ldar_asm_decodes_clean(self):
        r = G.scan_asmranges(self.mem)
        self.assertEqual(r['findings'], [])
        self.assertGreaterEqual(r['entries'], 2)          # the ldar of rd and the stlr of wr
        words = sections(self.mem)['.text']
        self.assertTrue(any(int.from_bytes(words[i:i + 4], 'little') & 0xbfe08000 == 0x88c08000 for i in range(0, len(words), 4)), 'an ldar is in the object')

    def test_accounting_is_complete(self):
        for o in (self.enter, self.mem):
            N, M, D, U = G.asm_totals(self.report(o)['asm'], G.scan_asmranges(o)['entries'])
            self.assertEqual(U, 0)
            self.assertEqual(M + self.report(o)['asm']['toplevel'], G.scan_asmranges(o)['entries'])
            self.assertGreaterEqual(N, 1)

    def test_every_asm_insn_has_a_range_over_its_own_bytes(self):
        # the section holds exactly the marked insns, each range is non-empty and inside .text
        r = G.scan_asmranges(self.mem)
        self.assertEqual(r['entries'], sum(v[3] for v in self.report(self.mem)['asm']['functions'].values()))

    def test_enter_top_level_asms_all_lie_in_ranges(self):
        r = G.scan_asmranges(self.enter)
        self.assertEqual(r['toplevel'], self.report(self.enter)['asm']['toplevel'])
        self.assertGreater(r['toplevel'], 0)
        self.assertEqual(r['findings'], [])
        self.assertEqual(judge(self.enter), [])


class Negative(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = {
            # no float or vector operand, no FP clobber: nothing in the source text says it converts
            'bare': 'extern "C" int f(void){ int r; __asm__ volatile("fcvtzs %w0, s0" : "=r"(r)); return r; }\n',
            # reached only through an operand modifier: the FP register is named by %s1 of a "w" operand that is an integer in the C types
            'modif': 'extern "C" int g(unsigned b){ int r; __asm__ volatile("fcvtzs %w0, %s1" : "=r"(r) : "w"(b)); return r; }\n',
            'clean': 'extern "C" unsigned h(unsigned a){ unsigned r; __asm__ volatile("add %w0, %w1, #1" : "=r"(r) : "r"(a)); return r; }\n',
            'top': '__asm__(".text\\n.globl topf\\n.type topf,%function\\ntopf:\\n ret\\n.size topf,.-topf\\n");\n',
            'top2': '__asm__(".text\\n.globl t1\\n.type t1,%function\\nt1:\\n add w0, w0, #1\\n ret\\n.size t1,.-t1\\n");\n'
                    '__asm__(".text\\n.globl t2\\n.type t2,%function\\nt2:\\n nop\\n fcvtzs w0, s0\\n ret\\n.size t2,.-t2\\n");\n',
            'bad': 'extern "C" void u(void){ __asm__ volatile(".inst 0xffffffff"); }\n',
            'cbare.c': 'int cf(void){ int r; __asm__ volatile("fcvtzu %w0, s2" : "=r"(r)); return r; }\n',
            'none': 'extern "C" int n(int a){ return a + 1; }\n',
        }
        cls.o = build_cxx(src)

    def test_plugin_alone_does_not_see_the_bare_asm_but_3c_does(self):
        rep = json.load(open(self.o['bare'][:-2] + '.f2i.json'))
        self.assertEqual(rep['findings'], [])                  # the GIMPLE classification finds nothing in either unit
        for n, name in (('bare', 'fcvtzs'), ('modif', 'fcvtzs')):
            f = judge(self.o[n])
            self.assertTrue(any('asm range' in x and name in x for x in f), (n, f))

    def test_a_c_unit_is_covered_the_same_way(self):
        f = judge(self.o['cbare'])
        self.assertTrue(any('asm range' in x and 'fcvtzu' in x for x in f), f)

    def test_modifier_case_is_found(self):
        rep = json.load(open(self.o['modif'][:-2] + '.f2i.json'))
        self.assertEqual(rep['findings'], [])
        self.assertEqual(G.scan_asmranges(self.o['modif'])['fcvt'], 1)

    def test_clean_asm_has_no_finding(self):
        self.assertEqual(judge(self.o['clean']), [])
        self.assertEqual(G.scan_asmranges(self.o['clean'])['entries'], 1)

    def test_no_asm_gives_an_empty_section_not_a_missing_one(self):
        r = G.scan_asmranges(self.o['none'])
        self.assertEqual((r['entries'], r['findings']), (0, []))
        self.assertIn('.fnvnx.asmrange', sections(self.o['none']))

    def test_missing_section_is_a_finding(self):
        raw = bytearray(open(self.o['clean'], 'rb').read())
        raw = bytes(raw).replace(b'.fnvnx.asmrange', b'.fnvnx.asmrangX')
        d = os.path.dirname(self.o['clean'])
        p = os.path.join(d, 'mut_missing.o')
        open(p, 'wb').write(raw)
        open(os.path.join(d, 'mut_missing.f2i.json'), 'w').write(open(self.o['clean'][:-2] + '.f2i.json').read())
        self.assertTrue(any('no section .fnvnx.asmrange' in x for x in judge(p, 'clean.cc')))

    def test_undecodable_word_is_a_finding(self):
        f = judge(self.o['bad'])
        self.assertTrue(any('undecodable word' in x for x in f), f)

    def test_mutated_bytes_of_a_clean_range_are_decoded(self):
        # the range of `add w0, w1, #1`: overwrite its word with fcvtzs w0, s0 (0x1e380000); the gate reads the product's own bytes
        o = self.o['clean']
        d = os.path.dirname(o)
        raw = bytearray(open(o, 'rb').read())
        with open(o, 'rb') as fh:
            tsec = ELFFile(fh).get_section_by_name('.text')
            off = tsec['sh_offset'] + relocs(o, '.fnvnx.asmrange')[0][3]      # the first range begins here: the add
        self.assertEqual(int.from_bytes(raw[off:off + 4], 'little') >> 24, 0x11)       # add (immediate), 32-bit
        raw[off:off + 4] = (0x1e380000).to_bytes(4, 'little')
        p = os.path.join(d, 'mut_bytes.o')
        open(p, 'wb').write(bytes(raw))
        open(os.path.join(d, 'mut_bytes.f2i.json'), 'w').write(open(o[:-2] + '.f2i.json').read())
        self.assertTrue(any('asm range' in x and 'fcvtzs' in x for x in judge(p, 'clean.cc')), judge(p, 'clean.cc'))

    def test_a_clean_top_level_asm_has_a_range_and_no_finding(self):
        f = judge(self.o['top'])
        self.assertEqual(f, [])
        r = G.scan_asmranges(self.o['top'])
        self.assertEqual((r['entries'], r['toplevel']), (1, 1))

    def test_a_conversion_inside_a_top_level_asm_is_a_finding(self):
        f = judge(self.o['top2'])
        self.assertTrue(any('<toplevel>' in x and 'fcvtzs' in x for x in f), f)
        self.assertEqual(G.scan_asmranges(self.o['top2'])['toplevel'], 2)

    def test_toplevel_counter_must_equal_the_toplevel_ranges(self):
        # the report says 2 top-level asm statements, the section holds 1 range of <toplevel>: a finding
        import json
        o = self.o['top']
        d = os.path.dirname(o)
        rep = json.load(open(o[:-2] + '.f2i.json'))
        rep['asm']['toplevel'] = 2
        p = os.path.join(d, 'mut_tl.o')
        open(p, 'wb').write(open(o, 'rb').read())
        json.dump(rep, open(os.path.join(d, 'mut_tl.f2i.json'), 'w'))
        self.assertTrue(any('top-level asm statement' in x for x in judge(p, 'top.cc')))

    def test_unrecorded_asm_arithmetic(self):
        a = {'functions': {'f': [1, 1, 1, 1, 0], 'g': [0, 2, 3, 1, 1]}, 'toplevel': 0}
        self.assertEqual(G.asm_totals(a, 2), (3, 2, 2, 1))     # N=1+2, M=1+1, D=(0+max(3-1,0)... see asm_totals, U=1+|entries-M|
        self.assertEqual(G.asm_totals(a, 1)[3], 2)


class Equal(unittest.TestCase):
    """The marker pass changes no byte: the same units built with the product plugin and with the same plugin minus the pass (the recipe builds both) are
    byte-equal in every section except the asm-range pair, relocations included."""
    SRC = {
        'a': ('__asm__(".text\\n.globl tl\\n.type tl,%function\\ntl:\\n ret\\n.size tl,.-tl\\n");\n'
              '__asm__(".pushsection .rodata.x\\n.word 5\\n.popsection\\n");\n'
              'extern "C" unsigned h(unsigned a){ unsigned r; __asm__ volatile("add %w0, %w1, #1" : "=r"(r) : "r"(a)); return r; }\n'
              'extern "C" int k(int a, int b){ if (a) { __asm__ volatile("nop"); return b; } __asm__ volatile("nop\\nnop"); return a * b; }\n'
              'extern "C" __attribute__((noreturn)) void z(void){ for (;;) __asm__ volatile("wfe"); }\n'
              'extern "C" void m(int *p, int n){ for (int i = 0; i < n; i++) { __asm__ volatile("" ::: "memory"); p[i] += 1; } }\n'),
    }

    def test_text_and_relocations_are_equal_without_the_pass(self):
        full = open(os.path.join(REAL, 'ports/fnv-nx/toolchain/ftoi/fnvnx_ftoi.cc')).read()
        needle = 'register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &pm);'
        self.assertEqual(full.count(needle), 1)
        a = build_cxx(self.SRC)['a']
        tl = 'register_callback(info->base_name, PLUGIN_ALL_IPA_PASSES_START, count_toplevel, NULL);'
        self.assertEqual(full.count(tl), 1)
        b = build_cxx(self.SRC, plugin_text={'fnvnx_ftoi': full.replace(needle, '').replace(tl, '')})['a']   # without the markers and without the top-level rewrite
        sa, sb = sections(a), sections(b)
        skip = ('.fnvnx.asmrange', '.rela.fnvnx.asmrange', '.symtab', '.strtab', '.shstrtab')   # the symbol tables are compared symbol by symbol below
        self.assertEqual(sorted(k for k in sa if k not in skip), sorted(k for k in sb if k not in skip))
        for k in sa:
            if k not in skip:
                self.assertEqual(sa[k], sb[k], k)
        for k in ('.text', '.eh_frame'):
            self.assertEqual(relocs(a, k), relocs(b, k), k)
        self.assertEqual(symbols(a), symbols(b))
        self.assertGreater(len(sections(a)['.fnvnx.asmrange']), 0)
        self.assertEqual(len(sections(b)['.fnvnx.asmrange']), 0)


if __name__ == '__main__':
    unittest.main()
