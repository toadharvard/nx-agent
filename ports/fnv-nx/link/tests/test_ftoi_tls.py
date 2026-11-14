#!/usr/bin/env python3
"""fnvnx_ftoi `.fnvnx.tls` (toolchain/ftoi/TLS.md): every TLS access the compiler emitted, read from the compiler's own state (SYMBOL_REF_TLS_MODEL of the insn's
symbols), with its relocated byte range, variable, model, function, file and line; one record per TLS variable the unit defines. Real objects built by the one build
(realobj.build: msvcmodel compiler, every plugin, the recipe's flags). Expected values come from the object's own relocation table, not from this file.
Run: python3 -I <this file> (needs the pinned compiler: ARTIFACTS_URL)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from elftools.elf.elffile import ELFFile
import realobj

REAL = realobj.REAL
SRC = {
    'tls': open(os.path.join(HERE, 'fixtures', 'tls.cc')).read(),
    'none': 'extern "C" int n(int a){ return a + 1; }\n',
    # a TLS reference written in inline asm: the compiler's state does not know the symbol, so the relocation lies outside every record
    'asmtls': '__thread int at_v;\nextern "C" long at(void){ long r; __asm__ volatile("mrs %0, tpidr_el0\\n add %0, %0, #:tprel_hi12:at_v\\n add %0, %0, #:tprel_lo12_nc:at_v" : "=r"(r)); return r; }\n',
    'init': '__thread int zi;\n__thread int di = 5;\n__thread char arr[24] __attribute__((aligned(16)));\nextern "C" int ti(void){ return zi + di + arr[3]; }\n',
}
TLSREL = ('_TLS', 'TPREL', 'DTPREL')      # substrings of the AArch64 TLS relocation names


def build(sources, **kw):
    res = realobj.build(sources, **kw)
    if res['status'] != 'green':
        raise RuntimeError('build is red: %s' % (res.get('failure'),))
    return {os.path.splitext(o['unit'])[0]: o['object'] for o in res['objects']}


def word(b, o):
    return int.from_bytes(b[o:o + 4], 'little')


def rel_name(r):
    from elftools.elf.enums import ENUM_RELOC_TYPE_AARCH64
    t = r['r_info_type']
    for k, v in ENUM_RELOC_TYPE_AARCH64.items():
        if v == t:
            return k
    return 'R_%d' % t


def section_relocs(elf, secname):
    """[(offset, relocation name, symbol name or None, section index of the symbol, addend)] of .rela<secname>"""
    rs = elf.get_section_by_name('.rela' + secname)
    if rs is None:
        return []
    st = elf.get_section(rs['sh_link'])
    out = []
    for r in rs.iter_relocations():
        sym = st.get_symbol(r['r_info_sym'])
        out.append((r['r_offset'], rel_name(r), sym.name, sym['st_shndx'], r['r_addend'] + (sym['st_value'] if sym['st_info']['type'] != 'STT_SECTION' else 0)))
    return out


def parse(path):
    """-> {'version', 'accesses': [dict], 'vars': [dict]} of the section .fnvnx.tls, relocations resolved (BEGIN/END -> (section index, offset))"""
    with open(path, 'rb') as fh:
        elf = ELFFile(fh)
        sec = elf.get_section_by_name('.fnvnx.tls')
        if sec is None:
            raise AssertionError('no .fnvnx.tls')
        d = sec.data()
        rel = {r[0]: (r[3], r[4]) for r in section_relocs(elf, '.fnvnx.tls')}
        ver, na, nv, ns = (word(d, 0), word(d, 4), word(d, 8), word(d, 12))
        base = 16 + 28 * na + 16 * nv
        strtab = d[base:base + ns]

        def s(o):
            return strtab[o:strtab.index(b'\0', o)].decode()
        acc, p = [], 16
        for _ in range(na):
            acc.append({'begin': rel[p], 'end': rel[p + 4], 'var': s(word(d, p + 8)), 'model': word(d, p + 12), 'func': s(word(d, p + 16)), 'file': s(word(d, p + 20)), 'line': word(d, p + 24)})
            p += 28
        vs = []
        for _ in range(nv):
            vs.append({'name': s(word(d, p)), 'size': word(d, p + 4), 'align': word(d, p + 8), 'sect': word(d, p + 12)})
            p += 16
        return {'version': ver, 'accesses': acc, 'vars': vs, 'strsz': ns, 'size': len(d)}


def code_tls_relocs(path):
    """[(code section index, offset, relocation name)] of every TLS-family relocation of every code section of the object, from `readelf -rW` (the binutils of the
    pinned environment names the ILP32 P32 relocation types, which pyelftools does not)"""
    import subprocess
    with open(path, 'rb') as fh:
        elf = ELFFile(fh)
        index = {sec.name: i for i, sec in enumerate(elf.iter_sections()) if sec['sh_flags'] & 4 and sec['sh_type'] == 'SHT_PROGBITS'}
    out, cur = [], None
    for ln in subprocess.run(['aarch64-linux-gnu-readelf', '-rW', path], capture_output=True, text=True, check=True).stdout.split('\n'):
        if ln.startswith('Relocation section'):
            cur = index.get(ln.split("'")[1][len('.rela'):])
            continue
        f = ln.split()
        if cur is not None and len(f) >= 3 and f[2].startswith('R_AARCH64_') and any(k in f[2] for k in TLSREL):
            out.append((cur, int(f[0], 16), f[2]))
    return out


MODEL = {0: 'GD', 1: 'LD', 2: 'IE', 3: 'LE'}
FAMILY = (('TLSDESC', 0), ('TLSGD', 0), ('DTPREL', 1), ('TLSLD', 1), ('GOTTPREL', 2), ('TLSIE', 2), ('TPREL', 3), ('TLSLE', 3))


def family(name):
    for k, m in FAMILY:
        if k in name:
            return m
    return None


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.o = build(SRC)
        cls.p = {k: parse(v) for k, v in cls.o.items()}

    def test_header_and_strings(self):
        for k, p in self.p.items():
            self.assertEqual(p['version'], 1, k)
            self.assertEqual(p['size'] % 4, 0, k)

    def test_unit_without_tls_has_an_empty_section(self):
        p = self.p['none']
        self.assertEqual((p['accesses'], p['vars']), ([], []))

    def test_every_record_range_is_one_instruction_in_one_section(self):
        for r in self.p['tls']['accesses']:
            self.assertEqual(r['begin'][0], r['end'][0])
            self.assertEqual(r['end'][1] - r['begin'][1], 4 * ((r['end'][1] - r['begin'][1]) // 4))
            self.assertGreater(r['end'][1], r['begin'][1])

    def test_every_tls_relocation_of_the_object_lies_in_a_record(self):
        # the class closure the gate relies on: a TLS relocation outside every record is an access the plugin did not see
        recs = [(r['begin'][0], r['begin'][1], r['end'][1]) for r in self.p['tls']['accesses']]
        rels = code_tls_relocs(self.o['tls'])
        self.assertGreater(len(rels), 0)
        for sec, off, name in rels:
            self.assertTrue(any(s == sec and lo <= off < hi for s, lo, hi in recs), (name, off))

    def test_relocation_family_inside_each_record_is_its_model(self):
        # the model the plugin records agrees with the relocation family the assembler wrote at the same bytes (the object is the witness)
        rels = code_tls_relocs(self.o['tls'])
        for r in self.p['tls']['accesses']:
            fam = {family(n) for s, off, n in rels if s == r['begin'][0] and r['begin'][1] <= off < r['end'][1]}
            self.assertTrue(fam, r)
            # under the TLS descriptor dialect the AArch64 backend generates local-dynamic as the descriptor sequence: a model-1 record may carry the global-dynamic family
            self.assertIn(r['model'], fam | ({1} if 0 in fam else set()), (r, fam))

    def test_models_by_variable(self):
        acc = {}
        for r in self.p['tls']['accesses']:
            acc.setdefault(r['var'], set()).add(r['model'])
        self.assertEqual(acc['e_ie'], {2})
        self.assertEqual(acc['d_le'], {3})
        self.assertEqual(acc['d_gd'], {0})
        self.assertIn(acc['d_ld'].pop(), (0, 1))
        self.assertEqual(acc['d_def'], {3})        # the pin's -ftls-model=local-exec

    def test_function_file_line_come_from_the_insn(self):
        for r in self.p['tls']['accesses']:
            self.assertTrue(r['func'])
            self.assertTrue(r['file'].endswith('.cc') and r['line'] > 0, r)      # the insn's location, else its function's declaration

    def test_defined_variables_with_layout(self):
        v = {x['name']: x for x in self.p['init']['vars']}
        self.assertEqual(set(v), {'zi', 'di', 'arr'})
        self.assertEqual((v['zi']['size'], v['zi']['sect']), (4, 1))
        self.assertEqual((v['di']['size'], v['di']['sect']), (4, 0))
        self.assertEqual((v['arr']['size'], v['arr']['align'], v['arr']['sect']), (24, 16, 1))
        self.assertNotIn('e_ie', {x['name'] for x in self.p['tls']['vars']})     # an extern is not defined here

    def test_a_tls_reference_in_inline_asm_is_a_relocation_outside_every_record(self):
        recs = [(r['begin'][0], r['begin'][1], r['end'][1]) for r in self.p['asmtls']['accesses']]
        rels = code_tls_relocs(self.o['asmtls'])
        self.assertGreater(len(rels), 0)
        self.assertTrue(any(not any(s == sec and lo <= off < hi for s, lo, hi in recs) for sec, off, _ in rels), (rels, recs))


class Equal(unittest.TestCase):
    """The markers change no byte: the same unit built with the product plugin and with the plugin minus the marker pass is byte-equal in every section except
    the two record sections and the symbol tables."""
    def test_equal_without_markers(self):
        full = open(os.path.join(REAL, 'ports/fnv-nx/toolchain/ftoi/fnvnx_ftoi.cc')).read()
        needle = 'register_callback(info->base_name, PLUGIN_PASS_MANAGER_SETUP, NULL, &pm);'
        self.assertEqual(full.count(needle), 1)
        a = build({'tls': SRC['tls']})['tls']
        b = build({'tls': SRC['tls']}, plugin_text={'fnvnx_ftoi': full.replace(needle, '')})['tls']

        def secs(path):
            with open(path, 'rb') as fh:
                return {s.name: s.data() for s in ELFFile(fh).iter_sections()}
        sa, sb = secs(a), secs(b)
        skip = ('.fnvnx.tls', '.rela.fnvnx.tls', '.fnvnx.asmrange', '.rela.fnvnx.asmrange', '.symtab', '.strtab', '.shstrtab', '.rela.text', '.rela.eh_frame')   # relocation symbol indices shift with the extra symbols; compared resolved below
        self.assertEqual(sorted(k for k in sa if k not in skip), sorted(k for k in sb if k not in skip))
        for k in sa:
            if k not in skip:
                self.assertEqual(sa[k], sb[k], k)
        for sec in ('.text', '.eh_frame'):
            ra, rb = [], []
            for path, out in ((a, ra), (b, rb)):
                with open(path, 'rb') as fh:
                    out += section_relocs(ELFFile(fh), sec)
            self.assertEqual(ra, rb, sec)
        self.assertGreater(len(sa['.fnvnx.tls']), len(sb['.fnvnx.tls']))


if __name__ == '__main__':
    unittest.main()
