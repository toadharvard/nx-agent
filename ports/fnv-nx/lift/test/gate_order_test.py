#!/usr/bin/env python3
"""Self-test of tools/gate_order.py on hand-written AArch64 ILP32 assembly (no game data).   python3 test/gate_order_test.py
Each case is one function body; the gate must give the stated colour to each of its three results."""
import importlib.util, json, os, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); GATE = os.path.join(os.path.dirname(HERE), 'tools', 'gate_order.py')
AS = ['aarch64-linux-gnu-gcc', '-march=armv8.1-a', '-mabi=ilp32', '-c', '-x', 'assembler']
RT = '.text\n.globl fnvl_dispatch\n.type fnvl_dispatch,%function\nfnvl_dispatch: ret\n.size fnvl_dispatch,.-fnvl_dispatch\n.data\n.globl fnvnx_lift_cpu_bytes\n.type fnvnx_lift_cpu_bytes,%object\n.size fnvnx_lift_cpu_bytes,4\nfnvnx_lift_cpu_bytes: .word 536\n'
FN = '.text\n.globl f\n.type f,%function\nf:\n{}\n.size f,.-f\n.section .rodata\ntab: .word 1,2,3,4\n.data\n.balign 4\nwtab: .word 1,2,3,4\n.byte 0\nwodd: .word 5\n.lcomm bsym,4\n.section .rodata.c,"a"\nctab: .word f,f,f,f\n.section .rodata.u,"a"\nutab: .word f,5,f,f\n.data\n.balign 4\nwctab: .word f,f,f,f\n'
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
    'image_store':          ('adrp x0,tab\n add x0,x0,:lo12:tab\n str w1,[x0]\n ret', R, G, G),       # a store is a guest access: no dmb ishst
    'image_table':          ('adrp x0,tab\n add x0,x0,:lo12:tab\n and w1,w1,#3\n ldr w0,[x0,x1,lsl #2]\n ret', G, G, G),
    'image_table_too_far':  ('adrp x0,tab\n add x0,x0,:lo12:tab\n and w1,w1,#7\n ldr w0,[x0,x1,lsl #2]\n ret', R, R, G),
    'image_table_unbound':  ('adrp x0,tab\n add x0,x0,:lo12:tab\n ldr w0,[x0,x1,lsl #2]\n ret', R, R, G),
    'image_ro_lo12':        ('adrp x0,tab\n ldr w0,[x0,:lo12:tab]\n ret', G, G, G),
    'image_ro_const_past_end': ('adrp x0,tab\n add x0,x0,:lo12:tab\n ldr w0,[x0,#16]\n ret', R, R, G),
    'image_w_plain_load':   ('adrp x0,wtab\n add x0,x0,:lo12:wtab\n ldr w0,[x0]\n ret', R, G, G),
    'image_w_load_dmb':     ('adrp x0,wtab\n add x0,x0,:lo12:wtab\n ldr w0,[x0]\n dmb ishld\n ret', G, G, G),
    'image_w_lo12_load':    ('adrp x0,wtab\n ldr w0,[x0,:lo12:wtab]\n ret', R, G, G),
    'image_w_ordered_load': ('adrp x0,wtab\n add x0,x0,:lo12:wtab\n ldar w0,[x0]\n ret', G, G, G),
    'image_w_ordered_store': ('adrp x0,wtab\n add x0,x0,:lo12:wtab\n stlr w1,[x0]\n ret', G, G, G),
    'image_w_ordered_misaligned': ('adrp x0,wodd\n add x0,x0,:lo12:wodd\n ldar w0,[x0]\n ret', G, G, R),
    'image_w_ordered_byte': ('adrp x0,wodd\n add x0,x0,:lo12:wodd\n ldarb w0,[x0]\n ret', G, G, G),
    'image_w_table':        ('adrp x0,wtab\n add x0,x0,:lo12:wtab\n and w1,w1,#3\n ldr w0,[x0,x1,lsl #2]\n ret', R, R, G),
    'image_bss_load':       ('adrp x0,bsym\n add x0,x0,:lo12:bsym\n ldr w0,[x0]\n ret', R, G, G),
    'image_bss_ordered':    ('adrp x0,bsym\n add x0,x0,:lo12:bsym\n ldaxr w0,[x0]\n ret', G, G, G),
    'image_unknown_target': ('adrp x0,tab\n add x0,x0,#8\n ldr w0,[x0]\n ret', R, G, G),
    'got_load':             ('adrp x0,:got:ext\n ldr w0,[x0,:got_lo12:ext]\n ldarb w1,[x0]\n ret', G, G, G),
    'join_cpu_guest':       ('mov x1,x27\n cbz w0,1f\n mov w1,w19\n1: ldr w0,[x1]\n ret', R, R, G),
    'lse':                  ('casal w0,w1,[x19]\n ret', R, G, G),
    'sys':                  ('dc zva,x19\n ret', R, G, G),
    'exclusive_pair':       ('tst w19,#3\n b.ne 2f\n1: ldaxr w0,[x19]\n stlxr w2,w1,[x19]\n cbnz w2,1b\n dmb ish\n2: ret', G, G, G),
}

# rule (b): every br/blr is legal only as the dispatcher's return value (copies), a word of a bounded read-only image table of relocated code addresses, or a function GOT entry
CASES.update({
    'br_table':            ('adrp x1,ctab\n add x1,x1,:lo12:ctab\n and w0,w0,#3\n ldr w1,[x1,x0,lsl #2]\n br x1', G, G, G),
    'blr_table':           ('stp x29,x30,[sp,#-16]!\n adrp x1,ctab\n add x1,x1,:lo12:ctab\n and w0,w0,#3\n ldr w1,[x1,x0,lsl #2]\n blr x1\n ldp x29,x30,[sp],#16\n ret', G, G, G),
    'br_table_const':      ('adrp x1,ctab\n add x1,x1,:lo12:ctab\n ldr w1,[x1,#8]\n br x1', G, G, G),
    'br_table_lo12':       ('adrp x1,ctab\n ldr w1,[x1,:lo12:ctab]\n br x1', G, G, G),
    'br_table_too_long':   ('adrp x1,ctab\n add x1,x1,:lo12:ctab\n and w0,w0,#7\n ldr w1,[x1,x0,lsl #2]\n br x1', R, R, G),
    'br_table_unbound':    ('adrp x1,ctab\n add x1,x1,:lo12:ctab\n ldr w1,[x1,x0,lsl #2]\n br x1', R, R, G),
    'br_table_unrelocated': ('adrp x1,utab\n add x1,x1,:lo12:utab\n and w0,w0,#3\n ldr w1,[x1,x0,lsl #2]\n br x1', R, G, G),
    'br_table_unrelocated_const': ('adrp x1,utab\n add x1,x1,:lo12:utab\n ldr w1,[x1,#4]\n br x1', R, G, G),
    'br_table_unrelocated_byte': ('adrp x1,ctab\n add x1,x1,:lo12:ctab\n and w0,w0,#3\n ldrb w1,[x1,x0]\n br x1', R, G, G),
    'br_writable_table':   ('adrp x1,wctab\n add x1,x1,:lo12:wctab\n ldar w1,[x1]\n br x1', R, G, G),
    'br_from_guest':       ('ldarb w1,[x19]\n br x1', R, G, G),
    'blr_from_guest':      ('stp x29,x30,[sp,#-16]!\n ldarb w1,[x19]\n blr x1\n ldp x29,x30,[sp],#16\n ret', R, G, G),
    'br_from_cpu':         ('ldr w1,[x27,#16]\n br x1', R, G, G),
    'br_from_frame':       ('sub sp,sp,#16\n str x0,[sp]\n ldr x1,[sp]\n add sp,sp,#16\n br x1', R, G, G),
    'br_derived':          ('ldarb w1,[x19]\n add x2,x1,#4\n br x2', R, G, G),
    'br_one_path':         ('cbz w0,1f\n ldarb w1,[x19]\n1: br x1', R, G, G),
    'br_mixed_table_guest': ('cbz w0,1f\n adrp x1,ctab\n add x1,x1,:lo12:ctab\n ldr w1,[x1,#4]\n b 2f\n1: ldarb w1,[x19]\n2: br x1', R, G, G),
    'br_argument':         ('br x0', R, G, G),
    'br_call_result':      ('stp x29,x30,[sp,#-16]!\n bl g\n ldp x29,x30,[sp],#16\n br x0', R, G, G),
    'br_overwritten':      ('ldarb w1,[x19]\n mov x1,x0\n br x1', R, G, G),
    'br_dispatch':         ('stp x29,x30,[sp,#-16]!\n bl fnvl_dispatch\n blr x0\n ldp x29,x30,[sp],#16\n ret', G, G, G),
    'br_dispatch_tail':    ('stp x29,x30,[sp,#-16]!\n bl fnvl_dispatch\n ldp x29,x30,[sp],#16\n br x0', G, G, G),
    'br_dispatch_copy':    ('stp x29,x30,[sp,#-16]!\n bl fnvl_dispatch\n mov x1,x0\n blr x1\n ldp x29,x30,[sp],#16\n ret', G, G, G),
    'br_dispatch_copy_w':  ('stp x29,x30,[sp,#-16]!\n bl fnvl_dispatch\n mov w1,w0\n blr x1\n ldp x29,x30,[sp],#16\n ret', G, G, G),
    'br_dispatch_modified': ('stp x29,x30,[sp,#-16]!\n bl fnvl_dispatch\n add x1,x0,#4\n blr x1\n ldp x29,x30,[sp],#16\n ret', R, G, G),
    'br_dispatch_overwritten': ('stp x29,x30,[sp,#-16]!\n bl fnvl_dispatch\n mov x0,x1\n blr x0\n ldp x29,x30,[sp],#16\n ret', R, G, G),
    'br_dispatch_one_path': ('stp x29,x30,[sp,#-16]!\n cbz w1,1f\n bl fnvl_dispatch\n1: blr x0\n ldp x29,x30,[sp],#16\n ret', R, G, G),
    'br_dispatch_clobbered_by_call': ('stp x29,x30,[sp,#-16]!\n bl fnvl_dispatch\n bl g\n blr x0\n ldp x29,x30,[sp],#16\n ret', R, G, G),
    'br_other_call':       ('stp x29,x30,[sp,#-16]!\n bl g\n blr x0\n ldp x29,x30,[sp],#16\n ret', R, G, G),
    'br_got_function':     ('stp x29,x30,[sp,#-16]!\n adrp x1,:got:f\n ldr w1,[x1,:got_lo12:f]\n blr x1\n ldp x29,x30,[sp],#16\n ret', G, G, G),
    'br_got_object':       ('stp x29,x30,[sp,#-16]!\n adrp x1,:got:tab\n ldr w1,[x1,:got_lo12:tab]\n blr x1\n ldp x29,x30,[sp],#16\n ret', R, G, G),
    'br_got_undefined':    ('stp x29,x30,[sp,#-16]!\n adrp x1,:got:ext\n ldr w1,[x1,:got_lo12:ext]\n blr x1\n ldp x29,x30,[sp],#16\n ret', R, G, G),
    'br_got_function_modified': ('stp x29,x30,[sp,#-16]!\n adrp x1,:got:f\n ldr w1,[x1,:got_lo12:f]\n add x1,x1,#4\n blr x1\n ldp x29,x30,[sp],#16\n ret', R, G, G),
})
# the CPU class through a W-form copy: x27 is a 32-bit pointer zero-extended (ILP32 object, ASM-007; struct cpu lies below 2^32, ASM-201)
CASES.update({
    'cpu_wform_copy':      ('mov w1,w27\n ldr w0,[x1,#16]\n ret', G, G, G),
    'cpu_wform_add':       ('mov w1,w27\n ubfiz w2,w0,#4,#3\n add w2,w2,w1\n str wzr,[x2,#340]\n ret', G, G, G),
    'cpu_wform_add_swapped': ('mov w1,w27\n ubfiz w2,w0,#4,#3\n add w2,w1,w2\n str wzr,[x2,#340]\n ret', G, G, G),
    'cpu_wform_add_past_end': ('mov w1,w27\n ubfiz w2,w0,#4,#3\n add w2,w2,w1\n str wzr,[x2,#424]\n ret', R, R, G),
    'cpu_wform_add_unbound': ('mov w1,w27\n add w2,w0,w1\n str wzr,[x2]\n ret', R, G, G),
    'cpu_wform_copy_past_end': ('mov w1,w27\n str wzr,[x1,#536]\n ret', R, R, G),
})
CASES.update({     # rule (a): no write of x19-x27 but a guest-register write proven below 2^32; decided from the encoding's destination fields
    'wr_ldp_x_guest':      ('ldp x19,x20,[sp]\n ret', R, G, G),
    'wr_ldp_w_guest':      ('ldp w19,w20,[sp]\n ret', G, G, G),
    'wr_ldp_x27':          ('ldp x26,x27,[sp]\n ret', R, G, G),
    'wr_ldr_w27':          ('ldr w27,[sp]\n ret', R, G, G),
    'wr_ldr_x_guest':      ('ldr x19,[sp]\n ret', R, G, G),
    'wr_ldr_w_guest':      ('ldr w19,[sp]\n ret', G, G, G),
    'wr_ldrsw_guest':      ('ldrsw x19,[sp]\n ret', R, G, G),
    'wr_ldr_literal_x27':  ('ldr x27,tab\n ret', R, G, G),
    'wr_writeback_x27':    ('ldr w0,[x27],#4\n ret', R, R, G),
    'wr_mrs_guest':        ('mrs x19,tpidr_el0\n ret', R, G, G),
    'wr_mrs_x27':          ('mrs x27,tpidr_el0\n ret', R, G, G),
    'wr_mrs_scratch':      ('mrs x0,tpidr_el0\n ret', G, G, G),
    'wr_stxr_status_w19':  ('tst w20,#3\n b.ne 1f\n stlxr w19,w1,[x20]\n1: ret', G, G, G),
    'wr_stxr_status_x27':  ('tst w20,#3\n b.ne 1f\n stlxr w27,w1,[x20]\n1: ret', R, G, G),
    'wr_fcvt_x_guest':     ('fcvtzs x19,d0\n ret', R, G, G),
    'wr_fcvt_w_guest':     ('fcvtzs w19,s0\n ret', G, G, G),
    'wr_fmov_x_guest':     ('fmov x19,d0\n ret', R, G, G),
    'wr_scvtf_vector':     ('scvtf d19,x0\n scvtf d27,x0\n ret', G, G, G),
    'wr_umov_w_guest':     ('umov w19,v0.s[1]\n ret', G, G, G),
    'wr_smov_x_guest':     ('smov x19,v0.b[1]\n ret', R, G, G),
    'wr_vector_load_pair': ('ldp q19,q20,[sp]\n ldr d27,[sp]\n ret', G, G, G),
    'wr_cmp_guest':        ('cmp x19,#1\n ccmp x27,#1,#0,eq\n ret', G, G, G),
    'wr_and_x_bounded':    ('and x19,x0,#0xffff\n ret', G, G, G),
    'wr_and_x_unbounded':  ('and x19,x0,x1\n ret', R, G, G),
    'wr_movz_x27':         ('movz x27,#1\n ret', R, G, G),
    'wr_csel_x_guest':     ('csel x19,x0,x1,eq\n ret', R, G, G),
    'wr_csel_w_guest':     ('csel w19,w0,w1,eq\n ret', G, G, G),
})
CORPUS = [
    'add {x}, x0, #1',
    'sub {w}, w0, w1',
    'movz {x}, #1',
    'movk {x}, #1, lsl #16',
    'and {x}, x0, #0xff',
    'ubfx {x}, x0, #1, #3',
    'adrp {x}, f',
    'adr {x}, f',
    'mul {x}, x0, x1',
    'madd {x}, x0, x1, x2',
    'udiv {w}, w0, w1',
    'csel {x}, x0, x1, eq',
    'adc {x}, x0, x1',
    'rbit {x}, x0',
    'clz {w}, w0',
    'extr {x}, x0, x1, #3',
    'ccmp {x}, #1, #0, eq',
    'ccmn {w}, w1, #4, ne',
    'cmp {x}, #1',
    'tst {x}, #1',
    'cmn {x}, x1',
    'mov {x}, x0',
    'mvn {w}, w0',
    'smull {x}, w0, w1',
    'ldr {x}, [sp]',
    'ldr {w}, [sp, #4]',
    'ldr {x}, [sp, x1]',
    'ldr {x}, [sp], #8',
    'ldr {x}, [sp, #8]!',
    'ldur {w}, [sp, #-1]',
    'ldrb {w}, [x0]',
    'ldrsb {x}, [x0]',
    'ldrsw {x}, [x0]',
    'ldrsh {w}, [x0, #2]',
    'str {x}, [sp]',
    'str {w}, [sp], #4',
    'str {x}, [sp, #-8]!',
    'strb {w}, [x0, x1]',
    'stur {x}, [sp, #1]',
    'ldtr {w}, [x0]',
    'sttr {w}, [x0]',
    'ldr {x}, f',
    'ldrsw {x}, f',
    'prfm pldl1keep, [{x}]',
    'prfm pstl1keep, f',
    'ldp {x}, x1, [sp]',
    'ldp x1, {x}, [sp, #16]',
    'ldp {w}, w1, [sp], #8',
    'ldp x1, x2, [{x}], #16',
    'stp {x}, x1, [sp, #-16]!',
    'stp x1, x2, [{x}, #16]',
    'ldpsw {x}, x1, [sp]',
    'ldnp {x}, x1, [sp]',
    'stnp {x}, x1, [sp]',
    'ldxr {x}, [x0]',
    'ldaxr {w}, [x0]',
    'ldar {x}, [x0]',
    'ldaxp {x}, x1, [x0]',
    'ldaxp x1, {x}, [x0]',
    'stxr {w}, x1, [x0]',
    'stlxr w1, {x}, [x0]',
    'stlxp {w}, x1, x2, [x0]',
    'stlr {x}, [x0]',
    'stlr w1, [{x}]',
    'casal {w}, w1, [x0]',
    'casal w1, {w}, [x0]',
    'ldaddal {w}, w1, [x0]',
    'ldaddal w1, {w}, [x0]',
    'swpal {w}, {w}, [x0]',
    'stadd {w}, [x0]',
    'ldapr {x}, [x0]',
    'ldapur {x}, [x0]',
    'stlur {x}, [x0]',
    'ldr {w}, [x0, #4]!',
    'ldrb {w}, [x0], #1',
    'ld1 {{v0.4s}}, [{x}]',
    'ld1 {{v0.4s}}, [{x}], #16',
    'ld1 {{v0.4s}}, [x0], {x}',
    'st1 {{v0.s}}[1], [{x}], #4',
    'ld1r {{v0.4s}}, [{x}], #4',
    'ldr q{n}, [sp]',
    'ldr d{n}, [{x}], #8',
    'ldp q{n}, q1, [sp]',
    'stp d{n}, d1, [{x}, #16]!',
    'str s{n}, [x0, {x}]',
    'ldr s{n}, f',
    'mrs {x}, tpidr_el0',
    'msr tpidr_el0, {x}',
    'mrs {x}, nzcv',
    'bl f',
    'blr {x}',
    'br {x}',
    'ret {x}',
    'cbz {x}, f',
    'tbz {x}, #1, f',
    'b.eq f',
    'fcvtzs {w}, s{n}',
    'fcvtzs {x}, d{n}',
    'fcvtzu {x}, d{n}, #3',
    'fcvtas {w}, s{n}',
    'fmov {x}, d{n}',
    'fmov {w}, s{n}',
    'fmov {x}, v{n}.d[1]',
    'fjcvtzs {w}, d{n}',
    'scvtf d{n}, {x}',
    'ucvtf s{n}, {w}',
    'scvtf d{n}, {x}, #3',
    'fmov d{n}, {x}',
    'fmov s{n}, {w}',
    'fmov v{n}.d[1], {x}',
    'umov {w}, v{n}.s[1]',
    'umov {x}, v{n}.d[1]',
    'smov {x}, v{n}.b[1]',
    'smov {w}, v{n}.h[1]',
    'ins v{n}.s[1], {w}',
    'dup v{n}.4s, {w}',
    'fadd d{n}, d0, d1',
    'fcmp d{n}, d0',
    'add v{n}.4s, v0.4s, v1.4s',
    'fcsel d{n}, d0, d1, eq',
    'sxtw {x}, w0',
    'uxtb {w}, w0',
    'neg {x}, x0',
    'crc32w {w}, w0, w1',
]
LSE_FP_UNREPORTED = ('cas', 'swp', 'ldadd', 'fjcvtzs')       # capstone reports no register write for these; the encoding's fields do

# rule (c): an object whose source is assembler preserves x19-x30 and sp on every path to every exit.  name: (body, order colour with --asm)
# (x19-x26 are saved and restored in W-form: an X-form write of a guest register is red by rule (a))
ASMCASES = {
    'asm_leaf':           ('mov x0,#1\n ret', G),
    'asm_frame':          ('stp x29,x30,[sp,#-16]!\n mov x29,sp\n bl g\n ldp x29,x30,[sp],#16\n ret', G),
    'asm_saved_guest':    ('sub sp,sp,#16\n str w19,[sp,#8]\n mov w19,w0\n ldr w19,[sp,#8]\n add sp,sp,#16\n ret', G),
    'asm_saved_pair':     ('stp w19,w20,[sp,#-16]!\n mov w19,w0\n mov w20,w1\n ldp w19,w20,[sp],#16\n ret', G),
    'asm_saved_x28':      ('str x28,[sp,#-16]!\n mov x28,x0\n ldr x28,[sp],#16\n ret', G),
    'asm_frame_pointer':  ('stp x29,x30,[sp,#-32]!\n mov x29,sp\n str x28,[x29,#16]\n mov x28,x0\n ldr x28,[x29,#16]\n mov sp,x29\n ldp x29,x30,[sp],#32\n ret', G),
    'asm_one_path_saved': ('cbz x0,1f\n str x28,[sp,#-16]!\n mov x28,x0\n ldr x28,[sp],#16\n1: ret', G),
    'asm_tail_call':      ('b g', G),
    'asm_xform_pair':     ('stp x19,x20,[sp,#-16]!\n mov w19,w0\n mov w20,w1\n ldp x19,x20,[sp],#16\n ret', G),
    'asm_xform_single':   ('str x19,[sp,#-16]!\n mov w19,w0\n ldr x19,[sp],#16\n ret', G),
    'asm_xform_x27_pair': ('stp x26,x27,[sp,#-16]!\n ldp x26,x27,[sp],#16\n ret', R),
    'asm_xform_then_base': ('stp x19,x20,[sp,#-16]!\n ldp x19,x20,[sp],#16\n ldarb w0,[x19]\n ret', G),
    'asm_xform_one_path': ('stp x19,x20,[sp,#-16]!\n cbz w0,1f\n ldp x19,x20,[sp],#16\n ret\n1: add sp,sp,#16\n ret', G),
    'asm_xform_wrong_slot': ('stp x19,x20,[sp,#-16]!\n ldp x20,x19,[sp],#16\n ret', R),
    'asm_xform_unsaved':  ('ldr x19,[sp]\n ret', R),
    'asm_xform_write':    ('mov x19,x0\n ret', R),
    'asm_xform_overwritten_slot': ('str x19,[sp,#-16]!\n str x0,[sp]\n ldr x19,[sp],#16\n ret', R),
    'asm_xform_one_path_bad': ('str x19,[sp,#-16]!\n cbz w0,1f\n ldr x19,[sp],#16\n ret\n1: ldr x19,[sp,#8]\n add sp,sp,#16\n ret', R),
    'asm_clobber':        ('mov w19,w0\n ret', R),
    'asm_clobber_x28':    ('mov x28,x0\n ret', R),
    'asm_clobber_x30':    ('bl g\n ret', R),
    'asm_sp_not_restored': ('sub sp,sp,#16\n ret', R),
    'asm_wrong_slot':     ('str x28,[sp,#-16]!\n mov x28,x0\n ldr x28,[sp,#8]\n add sp,sp,#16\n ret', R),
    'asm_slot_overwritten': ('str x28,[sp,#-16]!\n str x0,[sp]\n mov x28,x0\n ldr x28,[sp],#16\n ret', R),
    'asm_slot_half_overwritten': ('str x28,[sp,#-16]!\n strb w0,[sp,#3]\n mov x28,x0\n ldr x28,[sp],#16\n ret', R),
    'asm_one_path_clobber': ('cbz x0,1f\n mov x28,x0\n1: ret', R),
    'asm_swapped':        ('stp x28,x29,[sp,#-16]!\n ldp x29,x28,[sp],#16\n ret', R),
    'asm_width_mismatch': ('str x28,[sp,#-16]!\n mov x28,x0\n ldr w28,[sp],#16\n ret', R),
    'asm_escape_then_call': ('stp x29,x30,[sp,#-32]!\n str x28,[sp,#16]\n add x0,sp,#16\n bl g\n ldr x28,[sp,#16]\n ldp x29,x30,[sp],#32\n ret', R),
    'asm_tail_call_clobber': ('mov x28,x0\n b g', R),
    'asm_fall_into_next': ('mov x28,x0', R),
}


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.TemporaryDirectory(); cls.root = cls.d.name
        cls.rt = cls.asm('rt', RT)
    @classmethod
    def tearDownClass(cls): cls.d.cleanup()
    @classmethod
    def asm(cls, name, text, march=None):
        s, o = os.path.join(cls.root, name + '.S'), os.path.join(cls.root, name + '.o')
        open(s, 'w').write(text); subprocess.run([a if not (march and a.startswith('-march')) else '-march=' + march for a in AS] + [s, '-o', o], check=True); return o
    def gate(self, objs, asm=()):
        out = tempfile.mkdtemp(dir=self.root)
        r = subprocess.run([sys.executable, '-I', GATE, self.rt] + objs + [a for o in asm for a in ('--asm', o)], env=dict(os.environ, GATE_OUT=out, JOBS='1'), capture_output=True, text=True)
        res = {g: json.load(open(os.path.join(out, g + '.json'))) for g in ('order', 'addrwrap', 'align')}
        for g, j in res.items(): self.assertEqual((j['format'], j['name']), ('fnvnx-gate/1', g))
        self.assertEqual(r.returncode == 0, all(j['status'] == G for j in res.values()), r.stderr); return res
    def test_cases(self):
        for name, (body, *want) in sorted(CASES.items()):
            with self.subTest(name):
                res = self.gate([self.asm(name, FN.format(body))])
                self.assertEqual([res[g]['status'] for g in ('order', 'addrwrap', 'align')], want, json.dumps(res, indent=1))
    def test_asm_cases(self):
        for name, (body, want) in sorted(ASMCASES.items()):
            with self.subTest(name):
                o = self.asm(name, FN.format(body))
                res = self.gate([o], asm=[o])
                self.assertEqual(res['order']['status'], want, json.dumps(res['order'], indent=1))
    def test_asm_rule_needs_the_flag(self):
        o = self.asm('noflag', FN.format(ASMCASES['asm_clobber'][0]))
        self.assertEqual(self.gate([o])['order']['status'], G)                     # a compiler-output object is not held to rule (c)
        self.assertEqual(self.gate([o], asm=[o])['order']['status'], R)
        self.assertEqual(self.gate([self.asm('ok', FN.format(ASMCASES['asm_frame'][0]))], asm=[])['order']['certified'], 0)
        x = self.asm('xf', FN.format(ASMCASES['asm_xform_pair'][0]))
        self.assertEqual(self.gate([x])['order']['status'], R)                     # compiler objects keep rule (a): an X-form reload of x19-x26 is red
        self.assertEqual(self.gate([x], asm=[x])['order']['status'], G)
    def test_asm_code_outside_func(self):
        o = self.asm('outside', '.text\n.globl f\n.type f,%function\nf: ret\n.size f,.-f\nh: mov x28,x0\n ret\n')
        res = self.gate([o], asm=[o])['order']
        self.assertEqual(res['status'], R); self.assertIn('code outside every STT_FUNC', json.dumps(res))
        self.assertEqual(self.gate([o])['order']['status'], G)
    def test_asm_rows(self):
        o = self.asm('arow', FN.format('cbz x0,1f\n mov x28,x0\n1: ret'))
        spec = importlib.util.spec_from_file_location('gate_order', GATE); go = importlib.util.module_from_spec(spec); sys.modules['gate_order'] = go; spec.loader.exec_module(go)
        os.environ['JOBS'] = '1'; rw = go.rows(self.rt, [o], asm=[o])['order']
        self.assertEqual([(r[0], r[1], r[2].split(':')[0]) for r in rw], [('arow', 'f+0x8', 'not preserved at the exit')])
        self.assertIn('x28', rw[0][2])
    def test_counts(self):
        res = self.gate([self.asm('c1', FN.format(CASES['ordered_aligned'][0])), self.asm('c2', FN.format(CASES['plain_store'][0]))])
        self.assertEqual((res['order']['universe'], res['order']['certified'], len(res['order']['uncertified'])), (2, 1, 1))
        self.assertEqual((res['align']['universe'], res['align']['certified']), (1, 1))
    def test_rows(self):
        spec = importlib.util.spec_from_file_location('gate_order', GATE); go = importlib.util.module_from_spec(spec); sys.modules['gate_order'] = go; spec.loader.exec_module(go)
        o = self.asm('rw', FN.format('ldarb w0,[x19]\n str w0,[x20]\n ret'))
        os.environ['JOBS'] = '1'; rw = go.rows(self.rt, [o])
        self.assertEqual(sorted(rw), ['addrwrap', 'align', 'order'])
        self.assertEqual([(r[0], r[1]) for r in rw['order']], [('rw', 'f+0x0'), ('rw', 'f+0x4')])
        self.assertEqual([bool(r[2]) for r in rw['order']], [False, True])         # the plain store has no dmb ishst
        self.assertEqual([r[2] for r in rw['addrwrap']], ['', ''])
        self.assertEqual(rw['align'], [])
        self.assertEqual(rw['order'], sorted(rw['order']))
        self.assertEqual(go.verdict('order', rw['order'], {'instructions': 3})[0], False)
        self.assertEqual(go.verdict('align', rw['align'], {'instructions': 3})[0], True)
    def test_encoding_destinations(self):
        # gprwrites (the encoding's destination fields) against capstone's operand view, over every form with each of x19..x27
        import capstone
        spec = importlib.util.spec_from_file_location('gate_order', GATE); go = importlib.util.module_from_spec(spec); sys.modules['gate_order'] = go; spec.loader.exec_module(go)
        lines = [' ' + f.format(x='x%d' % r, w='w%d' % r, n=r) for r in range(19, 28) for f in CORPUS]
        o = self.asm('corpus', '.text\nf:\n' + '\n'.join(lines) + '\n.section .rodata\ntab: .word 1\n', march='armv8.5-a+lse+crc+rcpc+fp16')
        md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM); md.detail = True
        code = go.elf(o)[0][1]; self.assertEqual(len(code), 4 * len(lines))
        for k, ln in enumerate(lines):
            raw = code[4 * k:4 * k + 4]; ci = next(md.disasm(raw, 0)); cap = set()
            for x in ci.regs_access()[1]:
                g = go.regno(ci.reg_name(x))
                if g and 19 <= g[0] <= 27 and ci.mnemonic not in ('tst', 'cmp', 'cmn', 'ccmp', 'ccmn'): cap.add(g[0])
            enc = {q for q in go.gprwrites(int.from_bytes(raw, 'little')) if 19 <= q <= 27}
            if ci.mnemonic.startswith(LSE_FP_UNREPORTED): self.assertTrue(enc >= cap, ln)
            elif ci.mnemonic == 'stlur': self.assertEqual(enc, set(), ln)
            else: self.assertEqual(enc, cap, ln)
    def test_global_counts_reported(self):
        out = tempfile.mkdtemp(dir=self.root); o = self.asm('cnt', FN.format('ldr w0,[x27,#16]\n str w0,[x27,#532]\n ldr w0,[x27,#536]\n mov x27,x0\n ret'))
        subprocess.run([sys.executable, '-I', GATE, self.rt, o], env=dict(os.environ, GATE_OUT=out, JOBS='1'), capture_output=True)
        c = json.load(open(os.path.join(out, 'order.json')))['counts']
        self.assertEqual((c['x27_writes'], c['cpu_outside_struct'], c['cpu_accesses_inside_struct']), (1, 1, 2))
        o2 = self.asm('cnt0', FN.format('ldr w0,[x27,#16]\n ret')); out = tempfile.mkdtemp(dir=self.root)
        subprocess.run([sys.executable, '-I', GATE, self.rt, o2], env=dict(os.environ, GATE_OUT=out, JOBS='1'), capture_output=True)
        c = json.load(open(os.path.join(out, 'order.json')))['counts']
        self.assertEqual((c['x27_writes'], c['cpu_outside_struct']), (0, 0))
    def test_runtime_object_is_not_judged(self):      # the runtime object only gives sizeof(struct cpu); only the objects named as lifted are judged
        bad = self.asm('judged', FN.format('mov x27,x0\n ret'))
        self.assertEqual(self.gate([bad])['order']['status'], R)
        self.assertEqual(self.gate([])['order']['status'], R)           # nothing judged: red (no universe)
        rt_bad = self.asm('rt2', RT + '.text\n.globl h\n.type h,%function\nh: mov x27,x0\n ret\n.size h,.-h\n')
        out = tempfile.mkdtemp(dir=self.root); ok = self.asm('okobj', FN.format('ret'))
        r = subprocess.run([sys.executable, '-I', GATE, rt_bad, ok], env=dict(os.environ, GATE_OUT=out, JOBS='1'), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
    def test_dispatcher_must_be_defined_by_the_runtime_object(self):
        rt0 = self.asm('rtnodisp', RT.replace('fnvl_dispatch', 'fnvl_other'))
        r = subprocess.run([sys.executable, '-I', GATE, rt0, self.asm('x1', FN.format('ret'))], env=dict(os.environ, GATE_OUT=tempfile.mkdtemp(dir=self.root), JOBS='1'), capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0); self.assertIn('fnvl_dispatch', r.stderr)
    def test_no_object_red(self):
        self.assertEqual([j['status'] for j in self.gate([]).values()], [R, R, R])
    def test_data_in_text_skipped_and_garbage_red(self):
        self.assertEqual(self.gate([self.asm('d1', FN.format('ldarb w0,[x19]\n ret\n .word 0xffffffff'))])['order']['status'], G)   # $d range
        self.assertEqual(self.gate([self.asm('d2', FN.format('ldarb w0,[x19]\n ret\n .inst 0xffffffff'))])['order']['status'], R)   # $x range


if __name__ == '__main__':
    unittest.main()
