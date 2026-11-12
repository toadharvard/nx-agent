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
    # the product-program proof (invariance.py) for what the taint walk cannot show unobserved: both runs reach identical observable states
    'pf_prove_idiom':     ('21d8 53 9c 58 89c3 3500002000 50 9d 9c 58 53 9d 39d8 7407 b801000000 31db 31c0 5b c3', []),    # cpuid ID-bit probe shape: the bit cancels in cmp, ebx is restored
    'pf_prove_ret_eax':   ('21d8 53 9c 58 89c3 3500002000 50 9d 9c 58 53 9d 39d8 7402 31db 5b c3', [('af', 4, 'pushf')]),   # eax still holds the bit at the ret
    'pf_prove_dead_slot': ('21d8 9c 83c404 c3', []),                                                                  # the pushed word lies below ESP again: dead
    'pf_prove_shift':     ('21d8 9c 58 c1e804 83e001 c3', [('af', 4, 'pushf')]),                                        # shr moves the bit to bit 0: observed
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
}


def uncovered_encoding():
    """DERIVED from the real Form set, never picked: the first decodable instruction, in the order of the opcode space (one-byte opcodes, then 0f xx, each with
    ModRM and immediates of zero bytes), whose shape has no certified Form (gate_flags.effect is None). None when the Form set has no gap."""
    for pre in (b'', b'\x0f'):
        for op in range(256):
            code = pre + bytes([op]) + bytes(8)
            for i in g._md.disasm(code, A):
                if g.effect(i) is None and i.mnemonic and i.id not in (g.X.X86_INS_RET,) and i.size <= len(pre) + 1 + 7: return bytes(i.bytes)
                break
    return None


UNCOVERED = uncovered_encoding()
if UNCOVERED is not None:
    CASES['no_form_row'] = ('01c0 %s c3' % UNCOVERED.hex(), [('-', 0, 'no-form')])                            # an instruction with no Form is a red row, never assumed
    CASES['pf_prove_no_form'] = ('21d8 9c 58 89c1 %s c3' % UNCOVERED.hex(), [('af', 4, 'pushf'), ('-', 0, 'no-form')])   # the proof stops at it: no Form, red


class T(unittest.TestCase):
    def test_form_set_gap(self):
        """the no-Form cases above exist only while the Form set has a gap; when it has none, no decodable encoding is uncovered"""
        self.assertEqual(UNCOVERED is None, all(g.effect(i) is not None for pre in (b'', b'\\x0f') for op in range(256) for i in list(g._md.disasm(pre + bytes([op]) + bytes(8), A))[:1] if i.mnemonic))

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

    def test_entry_consumer(self):
        import flagcause
        uni = {0x10: ('ptr', ['candidate']), 0x20: ('ptr', ['candidate']), 0x30: ('ptr', ['candidate', 'call'])}
        jt = frozenset([0x10])
        self.assertEqual([flagcause.consumer(e, uni, jt) for e in (0x10, 0x20, 0x30, 0x40)], ['jtab', 'unknown-consumer', 'call', 'unknown-consumer'])
        self.assertEqual(flagcause.consumer(0x20, uni, frozenset()), 'unknown-consumer')

    def test_summary_splits_reads_from_no_form_rows(self):
        rd = [(0x1010, 'zf', 1, 'jcc', 'je'), (0x1020, '-', 0, 'no-form', 'fld'), (0x1030, '-', 0, 'no-form', 'fld')]     # (site, flag, origin, class, mnemonic)
        s = g.summary([A], {A: 1}, [(A, 9, [], rd, 5, 0)])[0]
        self.assertEqual((s['red_flag_reads'], s['no_form_rows'], s['flag_reads'], s['uncovered_by_mnemonic']), (1, 2, 5, {'fld': 2}))
        with self.assertRaises(AssertionError):                  # a red read count above the reads is impossible by construction
            g.summary([A], {A: 1}, [(A, 9, [], rd, 0, 0)])

    def test_decode_depends_on_the_range_only(self):
        code = bytes.fromhex('b8c3c3c3c3 7400 c3'.replace(' ', ''))                        # mov eax,imm32 (c3 bytes) ; jz ; ret
        fresh = []
        for lo in (A, A + 1):
            g.MEMO.clear(); fresh.append(g.decode(Img(code), lo, A + len(code)))
        g.MEMO.clear()
        both = [g.decode(Img(code), lo, A + len(code)) for lo in (A, A + 1)]               # the second range overlaps the first with another alignment
        self.assertEqual(fresh, both)
        self.assertNotEqual(both[0][0][1], both[1][0][1])

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
        # a callee reached with every flag defined is still judged, and still passes its own states on: B (state 0 from A) multiplies (ZF undefined) and calls C
        inst, red = solve({A: '39c0 e8f9000000 c3', A + 0x100: 'f7e1 e8f9000000 c3', A + 0x200: '7400 c3'},
                          {A: ('export', ['-']), A + 0x100: ('call', ['-']), A + 0x200: ('call', ['-'])})
        self.assertEqual(red, [(A + 0x200, [('zf', 4, 'jcc')])])
        inst, red = solve({A: '39c0 e8f9000000 c3', A + 0x100: 'e8fb000000 7400 c3'}, {A: ('export', ['-']), A + 0x100: ('call', ['-'])})
        self.assertEqual(red, [(A + 0x100, [('zf', 2, 'jcc')])])
        # the call transfer is the callee's summary: a callee that defines every flag (cmp) before its ret gives the caller defined flags
        X = {A: ('export', ['-']), A + 0x100: ('call', ['-'])}
        inst, red = solve({A: 'e8fb000000 7400 c3', A + 0x100: '39c0 c3'}, X)
        self.assertEqual(red, [])
        # a callee that leaves the flags alone passes the caller's state through: the cmp before the call is still the definition
        inst, red = solve({A: '39c0 e8f9000000 7400 c3', A + 0x100: '90 c3'}, X)
        self.assertEqual(red, [])
        # the same pass-through with no definition before the call: the caller's own entry state (E) comes out of the call
        inst, red = solve({A: 'e8fb000000 7400 c3', A + 0x100: '90 c3'}, X)
        self.assertEqual(red, [(A, [('zf', 1, 'jcc')])])
        # a callee with one path that defines and one that does not: the join keeps the caller's state on the second path
        inst, red = solve({A: '39c0 e8f9000000 7400 c3', A + 0x100: '85c9 7402 39c0 c3'}, X)
        self.assertEqual(red, [])
        # a callee that leaves ZF undefined (mul) hands that to the caller; the callee's own ret reads nothing
        inst, red = solve({A: '39c0 e8f9000000 7400 c3', A + 0x100: 'f7e1 c3'}, X)
        self.assertEqual(red, [(A, [('zf', 4, 'jcc')])])
        # an indirect call is the calling convention: every flag undefined (C)
        inst, red = solve({A: '39c0 ffd0 7400 c3'}, X)
        self.assertEqual(red, [(A, [('zf', 2, 'jcc')])])
        # a tail jmp takes the tail entry's summary: A jmp B, B = cmp; ret, and a caller C calls A
        inst, red = solve({A: 'e9fb000000', A + 0x100: '39c0 c3', A + 0x200: 'e8fbfdffff 7400 c3'},
                          {A: ('call', ['-']), A + 0x100: ('tail', ['-']), A + 0x200: ('export', ['-'])})
        self.assertEqual(red, [])
        # recursion is the least fixpoint: the callee calls itself and defines the flags on its base path
        inst, red = solve({A: 'e8fb000000 7400 c3', A + 0x100: '85c9 7405 e8f6ffffff 39c0 c3'}, X)
        self.assertEqual(red, [])
        # a callee that never returns (jmp $) is no exit: its call's fall-through keeps the calling convention (C), judged and not dropped
        inst, red = solve({A: '39c0 e8f9000000 7400 c3', A + 0x100: 'ebfe'}, X)
        self.assertEqual(red, [(A, [('zf', 2, 'jcc')])])
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
