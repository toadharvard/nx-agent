#!/usr/bin/env python3
"""Self-test of gates/gate_flags.py (flag liveness, item 21) and of proof/flagagree.py on hand-assembled x86-32 bytes (no game data).   python3 -I tests/test_gate_flags.py
Each case is one body at 0x1000; the gate must give the stated result: None = green, else the (flag, origin, class) rows of the red reads.
Origins: E = as at the entry of the body, C = as after a call, U = left undefined by an instruction."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), 'proof'))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
import gate_flags as g

A = 0x1000


class Img:
    def __init__(self, code): self.code = code
    def read(self, lo, hi): return self.code[lo - A: hi - A]


def red(hexs, ranges=None):
    g.MEMO.clear()
    code = bytes.fromhex(hexs.replace(' ', ''))
    r = ranges or [(A, A + len(code))]
    n, contrib, rows, reads, bad = g.analyse(Img(code), A, r, g.ALL_E, (), True)
    return [(f, o, c) for _, f, o, c, _ in rows], reads, bad


CASES = {
    # name: (bytes, expected red rows)
    'xor_jz':             ('31c0 7400 c3', []),
    'cmp_jz':             ('39c0 7400 c3', []),
    'jz_at_entry':        ('7400 c3', [('zf', 1, 'jcc')]),
    'call_kills':         ('39c0 e800000000 7400 c3', [('zf', 2, 'jcc')]),
    'call_then_cmp':      ('e800000000 39c0 7400 c3', []),
    'and_af_undefined':   ('21d8 9c c3', [('af', 4, 'pushf')]),                       # and: AF undefined; pushfd reads it
    'cmp_af_defined':     ('39c0 9c c3', []),
    'mul_zf_undefined':   ('f7e1 7400 c3', [('zf', 4, 'jcc')]),
    'mul_cf_defined':     ('f7e1 7200 c3', []),
    'imul_zf_undefined':  ('0fafc1 7400 c3', [('zf', 4, 'jcc')]),
    'imul_cf_defined':    ('0fafc1 7200 c3', []),
    'shl_imm_defined':    ('c1e002 7400 c3', []),
    'shl_imm_af_undef':   ('c1e002 9c c3', [('af', 4, 'pushf'), ('of', 4, 'pushf')]),
    'shl_imm0_nothing':   ('c1e000 7400 c3', [('zf', 1, 'jcc')]),                       # a count of 0 writes no flag: ZF is still as at the entry
    'shl_cl_keeps_entry': ('d3e0 7400 c3', [('zf', 1, 'jcc')]),                         # by cl: the write is conditional, an undefined flag stays so
    'shl_cl_after_cmp':   ('39c0 d3e0 7400 c3', []),
    'shl_cl_af_undef':    ('39c0 d3e0 9c c3', [('cf', 4, 'pushf'), ('af', 4, 'pushf'), ('of', 4, 'pushf')]),                    # conditional undefined adds U to a defined flag
    'join_call_path':     ('39c0 7403 e800000000 7400 c3', [('zf', 2, 'jcc')]),         # one path crosses a call
    'join_both_define':   ('85c0 7405 39c0 eb00 7400 c3', []),
    'loop_back_edge':     ('31c0 39c0 7402 ebfa c3', []),
    'pushfd_defined':     ('39c0 9c c3', []),
    'pushfd_after_and':   ('21d8 9c c3', [('af', 4, 'pushf')]),
    'pushfd_at_entry':    ('9c c3', [(f, 1, 'pushf') for f in ('cf', 'pf', 'af', 'zf', 'sf', 'of')]),
    'lahf_after_cmp':     ('39c0 9f c3', []),
    'lahf_at_entry':      ('9f c3', [(f, 1, 'pushf') for f in ('cf', 'pf', 'af', 'zf', 'sf')]),
    'pf_pop_and_clean':   ('21d8 9c 58 2500002000 c3', []),                             # and: AF undefined; the copy is masked down to bit 21 before ret
    'pf_pop_ret':         ('21d8 9c 58 c3', [('af', 4, 'pushf')]),                       # a masked register leaves in eax
    'pf_pop_test_jz':     ('21d8 9c 58 a910000000 7400 c3', [('af', 4, 'pushf')]),       # test eax,0x10 (AF) then jz: the masked bit decides a branch
    'pf_pop_test_other':  ('21d8 9c 58 a900002000 7400 31c0 c3', []),                    # test eax,0x200000: the masked bit is not observed
    'pf_pop_store':       ('21d8 9c 58 a300200000 31c0 c3', [('af', 4, 'pushf')]),       # mov [0x2000],eax stores the masked value
    'pf_pop_mov_xor_and': ('21d8 9c 58 89c1 3500002000 31c8 2500002000 31c9 c3', []),        # cpuid id-bit idiom
    'pf_popf_pushf_pop':  ('21d8 9c 9d 9c 58 2500002000 31c0 c3', []),                   # push, popf, push again, pop, mask: the mask moves through popf
    'pf_popf_jz':         ('21d8 9c 9d 7400 c3', []),                                    # popf of a masked word masks AF only; jz reads ZF
    'cmc_at_entry':       ('f5 c3', [('cf', 1, 'other')]),
    'cmc_after_clc':      ('f8 f5 c3', []),
    'adc_at_entry':       ('11c0 c3', [('cf', 1, 'carry')]),
    'adc_after_add':      ('01c0 11c0 c3', []),
    'setcc_at_entry':     ('0f94c0 c3', [('zf', 1, 'setcc')]),
    'cmov_at_entry':      ('0f44c1 c3', [('zf', 1, 'cmov')]),
    'rep_cmps_keeps':     ('f3a6 7400 c3', [('zf', 1, 'jcc')]),                         # a zero count writes nothing
    'rep_cmps_after_cmp': ('39c0 f3a6 7400 c3', []),
    'cmps_plain_defines': ('a6 7400 c3', []),
    'fucomip_no_form':    ('39c0 dfe9 7400 c3', [('-', 0, 'no-form')]),                 # x87 compares have no Form yet (item 23)
    'sahf_defines':       ('9e 7600 c3', []),
    'test_mem_defines':   ('845e00 7400 c3', []),                                       # the Form of test m8, r8 (capstone lists no flags for it)
    'test_mem_af_undef':  ('845e00 9c c3', [('af', 4, 'pushf')]),
    'rol_of_imm1_defined': ('f8 d1c0 7000 c3', []),                                     # the SDM defines OF of a one-bit rotate
    'rol_cl_of_union':    ('39c0 d3c0 7000 c3', [('of', 4, 'jcc')]),                    # by cl: in-state, defined or undefined
    'shr_imm1_of':        ('39c0 d1e8 7000 c3', []),
    'shld_imm2_of_undef': ('39c0 0fa4c802 7000 c3', [('of', 4, 'jcc')]),
    'imul_sf_undefined':  ('0fafc1 7800 c3', [('sf', 4, 'jcc')]),                       # the Form (SDM), not the decoder's table
    'jmp_out_is_exit':    ('39c0 e900000000', []),
    'jmp_out_unreached': ('e900100000 7400', []),                                      # the bytes after a jmp are reached by no path from the entry
    'bsf_zf_defined':     ('0fbcc1 7400 c3', []),
    'bsf_cf_undefined':   ('0fbcc1 7200 c3', [('cf', 4, 'jcc')]),
    'div_all_undefined':  ('f7f1 7400 c3', [('zf', 4, 'jcc')]),
    'inc_keeps_cf':       ('40 7200 c3', [('cf', 1, 'jcc')]),                           # inc does not write CF
    'inc_zf_defined':     ('40 7400 c3', []),
    'rol_of_undefined':   ('f8 c1c003 7000 c3', [('of', 4, 'jcc')]),
    'daa_no_form':        ('01c0 27 c3', [('-', 0, 'no-form')]),                        # daa has no Form yet (item 23)
}


class T(unittest.TestCase):
    def test_cases(self):
        bad = []
        for name, (hx, exp) in sorted(CASES.items()):
            rows, reads, undecodable = red(hx)
            if sorted(rows) != sorted(exp) or undecodable: bad.append((name, rows, exp, undecodable))
        self.assertEqual(bad, [])

    def test_universe_counts_reads(self):
        rows, reads, _ = red('39c0 7400 7200 c3')           # cmp; jz; jb: two reads, both defined
        self.assertEqual((rows, reads), ([], 2))
        rows, reads, _ = red('7400 c3')
        self.assertEqual((len(rows), reads), (1, 1))

    def test_undecodable_range_reported(self):
        _, _, bad = red('39c0 0f', [(A, A + 3)])
        self.assertEqual(bad, 1)

    def test_class_of_reader(self):
        self.assertEqual([c for _, _, c in red('7400 c3')[0]], ['jcc'])

    def test_interprocedural(self):
        """entries B (0x1100) .. reached by a direct transfer take the caller's state; an entry with an unknown predecessor starts undefined"""
        def solve(parts, uni):
            code, rows = bytearray(0x400), {}
            for a, hx in parts.items():
                b = bytes.fromhex(hx.replace(' ', ''))
                code[a - A:a - A + len(b)] = b
                rows[a] = [(a, a + len(b))]
            g.MEMO.clear()
            inst, res = g.solve(Img(bytes(code)), A, rows, uni, 1)
            return inst, sorted((e, [(f, o, c) for _, f, o, c, _ in r]) for e, n, ct, r, rd, b in res if r)
        T = ('tail', ['-'])
        # jmp to B after cmp: B's jz reads a defined flag
        inst, red = solve({A: '39c0 e9f9000000', A + 0x100: '7400 c3'}, {A: ('export', ['-']), A + 0x100: T})
        self.assertEqual(red, [])
        # the same without the cmp: the entry state is E through the jmp
        inst, red = solve({A: 'e9fb000000', A + 0x100: '7400 c3'}, {A: ('export', ['-']), A + 0x100: T})
        self.assertEqual(red, [(A + 0x100, [('zf', 1, 'jcc')])])
        # a call passes the state before the call; the callee's reads are defined
        inst, red = solve({A: '39c0 e8f9000000 c3', A + 0x100: '7400 c3'}, {A: ('export', ['-']), A + 0x100: ('call', ['-'])})
        self.assertEqual(red, [])
        # an entry whose address is taken (flag ptr) starts undefined even when a direct caller defines the flags
        inst, red = solve({A: '39c0 e8f9000000 c3', A + 0x100: '7400 c3'}, {A: ('export', ['-']), A + 0x100: ('call', ['ptr'])})
        self.assertEqual(red, [(A + 0x100, [('zf', 1, 'jcc')])])
        # two callers: one defines, one does not: the join is undefined
        inst, red = solve({A: '39c0 e8f9000000 c3', A + 0x10: 'e8eb000000 c3', A + 0x100: '7400 c3'},
                          {A: ('export', ['-']), A + 0x10: ('export', ['-']), A + 0x100: ('call', ['-'])})
        self.assertEqual(red, [(A + 0x100, [('zf', 1, 'jcc')])])
        # an entry with no direct predecessor is an unknown predecessor itself
        inst, red = solve({A: '7400 c3'}, {A: ('call', ['-'])})
        self.assertEqual(red, [(A, [('zf', 1, 'jcc')])])


class Agree(unittest.TestCase):
    def test_agreement_rows(self):
        import flagagree
        reps = {'and': bytes.fromhex('21d8'), 'cmp': bytes.fromhex('39c0'), 'imul': bytes.fromhex('0fafc1'), 'shl0': bytes.fromhex('c1e000'),
                'rol1': bytes.fromhex('d1c0'), 'test_mem': bytes.fromhex('845e00')}
        n, bad = flagagree.agreement(reps)
        self.assertEqual(sorted(bad), [('imul', 'sf', 'undef', 'def'), ('rol', 'of', 'def', 'undef'), ('test', 'af', 'undef', 'none')] +
                         [('test', f, 'def', 'none') for f in ('cf', 'of', 'pf', 'sf', 'zf')])             # capstone lists no flags for test m8, r8
        self.assertEqual(n, 5)             # shl by a count of 0 is not compared


if __name__ == '__main__':
    unittest.main()
