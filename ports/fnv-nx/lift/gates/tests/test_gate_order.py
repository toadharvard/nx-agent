#!/usr/bin/env python3
"""Self-test of tools/gate_order.py on hand-written AArch64 ILP32 assembly (no game data).   python3 -I tests/test_gate_order.py
Each case is one function body; the gate must give the stated colour to each of its three results."""
import json, os, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); GATE = os.path.join(os.path.dirname(HERE), 'gate_order.py')
AS = ['aarch64-linux-gnu-gcc-13', '-march=armv8.1-a', '-mabi=ilp32', '-c', '-x', 'assembler']
RT = '.data\n.globl fnvnx_lift_cpu_bytes\n.type fnvnx_lift_cpu_bytes,%object\n.size fnvnx_lift_cpu_bytes,4\nfnvnx_lift_cpu_bytes: .word 536\n'
FN = '.text\n.globl f\n.type f,%function\nf:\n{}\n.size f,.-f\n.section .rodata\ntab: .word 1,2,3,4\n'
G, R = 'green', 'red'
# name: (body, order, addrwrap, align)
CASES = {
    'ordered_aligned':      ('tst w19,#3\n b.ne 1f\n ldar w0,[x19]\n1: ret', G, G, G),
    'ordered_store':        ('tst w19,#3\n b.ne 1f\n stlr w0,[x19]\n1: ret', G, G, G),
    'ordered_byte':         ('ldarb w0,[x19]\n stlrb w0,[x20]\n ret', G, G, G),
    'ordered_no_test':      ('ldar w0,[x19]\n ret', G, G, R),
    'ordered_wrong_mask':   ('tst w19,#1\n b.ne 1f\n ldar w0,[x19]\n1: ret', G, G, R),
    'ordered_wrong_edge':   ('tst w19,#3\n b.eq 1f\n ldar w0,[x19]\n1: ret', G, G, R),
    'ordered_base_changed': ('tst w19,#3\n b.ne 1f\n add w19,w19,#1\n ldar w0,[x19]\n1: ret', G, G, R),
    'half_tbz':             ('tbnz w19,#0,1f\n ldarh w0,[x19]\n1: ret', G, G, G),
    'plain_load_dmb':       ('ldr w0,[x19]\n dmb ishld\n ret', G, G, G),
    'plain_store_dmb':      ('dmb ishst\n str w0,[x19]\n ret', G, G, G),
    'plain_load':           ('ldr w0,[x19]\n ret', R, G, G),
    'plain_store':          ('str w0,[x19]\n ret', R, G, G),
    'load_dmb_wrong_side':  ('dmb ishld\n ldr w0,[x19]\n ret', R, G, G),
    'store_dmb_wrong_side': ('str w0,[x19]\n dmb ishst\n ret', R, G, G),
    'store_dmb_wrong_kind': ('dmb ishld\n str w0,[x19]\n ret', R, G, G),
    'dmb_next_block':       ('ldr w0,[x19]\n cbz w1,1f\n1: dmb ishld\n ret', R, G, G),
    'dmb_shared_by_two':    ('dmb ishst\n str w0,[x19]\n str w0,[x20]\n ret', R, G, G),
    'guest_disp':           ('ldarb w0,[x19]\n ldrb w0,[x19,#4]\n dmb ishld\n ret', G, R, G),
    'guest_index':          ('ldrb w0,[x19,x20]\n dmb ishld\n ret', G, R, G),
    'guest_writeback':      ('ldrb w0,[x1],#1\n dmb ishld\n ret', G, R, G),
    'x_form_address':       ('add x1,x19,x20\n ldarb w0,[x1]\n ret', G, R, G),
    'w_form_address':       ('add w1,w19,w20\n ldarb w0,[x1]\n ret', G, G, G),
    'argument_register':    ('ldarb w0,[x0]\n ret', G, R, G),
    'after_call':           ('stp x29,x30,[sp,#-16]!\n mov w1,w19\n bl g\n ldarb w0,[x1]\n ldp x29,x30,[sp],#16\n ret', G, R, G),
    'guest_reg_x_write':    ('add x19,x19,x20\n ret', R, G, G),
    'guest_reg_x_bounded':  ('and x19,x0,#0xffff\n add x19,x19,#4\n ldarb w0,[x19]\n ret', G, G, G),
    'cpu_field':            ('ldr w0,[x27,#16]\n str w0,[x27,#532]\n ret', G, G, G),
    'cpu_past_end':         ('ldr w0,[x27,#536]\n ret', R, R, G),
    'cpu_straddles_end':    ('add x1,x27,#532\n ldr x0,[x1]\n ret', R, R, G),
    'cpu_index':            ('ldr w0,[x27,x1]\n ret', R, R, G),
    'cpu_bounded_offset':   ('and w1,w0,#0xff\n add x1,x27,x1\n ldrb w0,[x1]\n ret', G, G, G),
    'cpu_unbounded_offset': ('add x1,x27,x0\n ldrb w0,[x1]\n dmb ishld\n ret', G, R, G),
    'x27_written':          ('mov x27,x0\n ret', R, G, G),
    'frame':                ('sub sp,sp,#32\n str x0,[sp,#8]\n add x1,sp,#16\n str w0,[x1,#4]\n ldr x0,[sp,#8]\n add sp,sp,#32\n ret', G, G, G),
    'frame_index':          ('sub sp,sp,#32\n ldr w0,[sp,x1]\n add sp,sp,#32\n ret', R, R, G),
    'sp_from_guest':        ('mov sp,x0\n ret', R, G, G),
    'spill_reload':         ('sub sp,sp,#16\n add x1,x27,#8\n str x1,[sp,#8]\n mov x1,#0\n ldr x1,[sp,#8]\n ldr w0,[x1]\n add sp,sp,#16\n ret', G, G, G),
    'spill_overwritten':    ('sub sp,sp,#16\n add x1,x27,#8\n str x1,[sp,#8]\n str x0,[sp,#8]\n ldr x1,[sp,#8]\n ldr w0,[x1]\n add sp,sp,#16\n ret', R, R, G),
    'spill_across_call':    ('stp x29,x30,[sp,#-32]!\n add x1,x27,#8\n str x1,[sp,#24]\n bl g\n ldr x1,[sp,#24]\n ldr w0,[x1]\n ldp x29,x30,[sp],#32\n ret', G, G, G),
    'spill_escaped_call':   ('stp x29,x30,[sp,#-32]!\n add x1,x27,#8\n str x1,[sp,#24]\n add x0,sp,#16\n bl g\n ldr x1,[sp,#24]\n ldr w0,[x1]\n ldp x29,x30,[sp],#32\n ret', R, R, G),
    'image_load':           ('adrp x0,tab\n add x0,x0,:lo12:tab\n ldr w0,[x0,#4]\n ret', G, G, G),
    'image_store':          ('adrp x0,tab\n add x0,x0,:lo12:tab\n str w1,[x0]\n ret', R, R, G),
    'image_table':          ('adrp x0,tab\n add x0,x0,:lo12:tab\n and w1,w1,#3\n ldr w0,[x0,x1,lsl #2]\n ret', G, G, G),
    'image_table_too_far':  ('adrp x0,tab\n add x0,x0,:lo12:tab\n and w1,w1,#7\n ldr w0,[x0,x1,lsl #2]\n ret', R, R, G),
    'image_table_unbound':  ('adrp x0,tab\n add x0,x0,:lo12:tab\n ldr w0,[x0,x1,lsl #2]\n ret', R, R, G),
    'join_cpu_guest':       ('mov x1,x27\n cbz w0,1f\n mov w1,w19\n1: ldr w0,[x1]\n ret', R, R, G),
    'lse':                  ('casal w0,w1,[x19]\n ret', R, G, G),
    'sys':                  ('dc zva,x19\n ret', R, G, G),
    'exclusive_pair':       ('tst w19,#3\n b.ne 2f\n1: ldaxr w0,[x19]\n stlxr w2,w1,[x19]\n cbnz w2,1b\n dmb ish\n2: ret', G, G, G),
}


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.TemporaryDirectory(); cls.root = cls.d.name
        cls.rt = cls.asm('rt', RT)
    @classmethod
    def tearDownClass(cls): cls.d.cleanup()
    @classmethod
    def asm(cls, name, text):
        s, o = os.path.join(cls.root, name + '.S'), os.path.join(cls.root, name + '.o')
        open(s, 'w').write(text); subprocess.run(AS + [s, '-o', o], check=True); return o
    def gate(self, objs):
        out = tempfile.mkdtemp(dir=self.root)
        r = subprocess.run([sys.executable, '-I', GATE, self.rt] + objs, env=dict(os.environ, GATE_OUT=out, JOBS='1'), capture_output=True, text=True)
        res = {g: json.load(open(os.path.join(out, g + '.json'))) for g in ('order', 'addrwrap', 'align')}
        for g, j in res.items(): self.assertEqual((j['format'], j['name']), ('fnvnx-gate/1', g))
        self.assertEqual(r.returncode == 0, all(j['status'] == G for j in res.values()), r.stderr); return res
    def test_cases(self):
        for name, (body, *want) in sorted(CASES.items()):
            with self.subTest(name):
                res = self.gate([self.asm(name, FN.format(body))])
                self.assertEqual([res[g]['status'] for g in ('order', 'addrwrap', 'align')], want, json.dumps(res, indent=1))
    def test_counts(self):
        res = self.gate([self.asm('c1', FN.format(CASES['ordered_aligned'][0])), self.asm('c2', FN.format(CASES['plain_store'][0]))])
        self.assertEqual((res['order']['universe'], res['order']['certified'], len(res['order']['uncertified'])), (2, 1, 1))
        self.assertEqual((res['align']['universe'], res['align']['certified']), (1, 1))
    def test_no_object_red(self):
        self.assertEqual([j['status'] for j in self.gate([]).values()], [R, R, R])
    def test_data_in_text_skipped_and_garbage_red(self):
        self.assertEqual(self.gate([self.asm('d1', FN.format('ldarb w0,[x19]\n ret\n .word 0xffffffff'))])['order']['status'], G)   # $d range
        self.assertEqual(self.gate([self.asm('d2', FN.format('ldarb w0,[x19]\n ret\n .inst 0xffffffff'))])['order']['status'], R)   # $x range


if __name__ == '__main__':
    unittest.main()
