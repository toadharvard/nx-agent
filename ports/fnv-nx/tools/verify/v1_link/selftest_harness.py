#!/usr/bin/env python3
"""selftest_harness.py - V1-link round 2: re-validate the salvaged harness (J-0041) before it judges anything.

    selftest_harness.py <work dir> [oracle disassembly: objdump -d -r -M intel of oracle/x86conv.obj]

1. qemu_ilp32: an ILP32 program runs with its stack below 4 GB; a failing program's status comes back.
2. bridge_harness detects wrong bridges: for hand-made pairs a correct hand-written bridge passes and a wrong one
   (missing move, wrong extension, swapped registers) fails; a direct call of an x86-consistent pair passes.
3. the x86 model of bridge_harness agrees with the MSVC 2008 oracle disassembly (hidden result pointer placement for
   thiscall / stdcall / cdecl members and fastcall, POD results in edx:eax, fastcall register assignment, narrow
   argument pushes), when the disassembly is given.
4. elf32 + c5_pic find a variable reached through a section anchor after a local symbol was renamed to an address
   symbol, and do not flag the GOT path of a renamed global.
Exit status 0 only when every check passes. Hand-made inputs only (no decomp content).
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bridge_harness as bh  # noqa: E402
import qemu_ilp32            # noqa: E402

FAILS = []


def check(cond, what):
    print('%-6s %s' % ('ok' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


def t_qemu(work):
    d = os.path.join(work, 'q')
    os.makedirs(d, exist_ok=True)
    src = os.path.join(d, 't.c')
    open(src, 'w').write('void v1_puts(const char*); int g = 5; int main(void) { v1_puts("ilp32 ok\\n"); int x;'
                         ' return ((unsigned long)&x >> 32) == 0 && sizeof(long) == 4 && g == 5 ? 0 : 1; }\n')
    code, out = qemu_ilp32.run(qemu_ilp32.build([src], os.path.join(d, 't')))
    check(code == 0 and 'ilp32 ok' in out, 'qemu ILP32 program runs, stack below 4 GB, long is 4 bytes')
    src2 = os.path.join(d, 'f.c')
    open(src2, 'w').write('int main(void) { return 3; }\n')
    code, out = qemu_ilp32.run(qemu_ilp32.build([src2], os.path.join(d, 'f')))
    check(code == 3, 'qemu: exit status of a failing program comes back (%d)' % code)


BRIDGES = [
    # (id, ref, def, bridge asm with 'B' as its name and 'T' as target, expected 'pass' / 'fail')
    ('regshift-correct', '?n@T@@QAEHH@Z', '?n@@YGHH@Z', 'B:\n    mov w0, w1\n    b T\n', 'pass'),
    ('regshift-missing-move', '?n@T@@QAEHH@Z', '?n@@YGHH@Z', 'B:\n    b T\n', 'fail'),
    ('edxdummy-correct', '?m@T@@QAEHHH@Z', '?m@@YIHPAUT@@HHH@Z',
     'B:\n    mov w3, w2\n    mov w2, w1\n    b T\n', 'pass'),
    ('edxdummy-wrong-order', '?m@T@@QAEHHH@Z', '?m@@YIHPAUT@@HHH@Z',
     'B:\n    mov w2, w1\n    mov w3, w2\n    b T\n', 'fail'),
    ('intfloat-correct', '?q@T@@QAEXH@Z', '?q@T@@QAEXM@Z', 'B:\n    fmov s0, w1\n    b T\n', 'pass'),
    ('intfloat-missing', '?q@T@@QAEXH@Z', '?q@T@@QAEXM@Z', 'B:\n    b T\n', 'fail'),
    ('swap-x0-x1', '?r@@YAXHH@Z', '?r@@YAXHH@Z', 'B:\n    mov w16, w0\n    mov w0, w1\n    mov w1, w16\n    b T\n',
     'fail'),
    ('direct-same', '?r@@YAXHH@Z', '?r@@YAXHH@Z', None, 'pass'),
    ('float-result-as-int-missing', '?u@@YAHXZ', '?u@@YAMXZ', 'B:\n    b T\n', 'pass'),   # x86 junk: not checked
]


def t_bridges(work):
    names = sorted({c[1] for c in BRIDGES} | {c[2] for c in BRIDGES})
    dem = bh.undname(names)
    for cid, ref, dfn, asm, want in BRIDGES:
        if asm is not None:
            asm = '    .globl v1brg\n' + asm.replace('B:', 'v1brg:')
            asm = asm.replace('b T', 'b __fnv_00c0ffee')
        st, notes = bh.run_case(ref, dfn, {}, {}, asm, os.path.join(work, 'b_' + cid), seed=3, dem=dem)
        got = 'pass' if st == 'pass' else 'fail'
        check(got == want, 'bridge_harness %-28s -> %s (%s)' % (cid, st[:70], want))
    # narrow arguments: x86 upper bytes are the caller's (oracle); the harness checks only the bytes passed
    dem = bh.undname(['?c@@YAXD@Z', '?c@@YAXH@Z'])
    st, notes = bh.run_case('?c@@YAXD@Z', '?c@@YAXH@Z', {}, {}, '    .globl v1brg\nv1brg:\n    b __fnv_00c0ffee\n',
                            os.path.join(work, 'b_narrow'), seed=3, dem=dem, edge=1)
    check(st == 'pass', 'bridge_harness narrow char -> int without extension: low byte only (%s)' % st)


ORACLE_FACTS = [
    # (function in the oracle, regex over its disassembly, meaning)
    ('?call_m_p8@@YAXPAUT@@@Z', r'mov\s+ecx,DWORD PTR \[esp\+0x4\].*lea\s+eax,\[esp\].*push\s+eax.*call',
     'thiscall member returning a POD 8-byte struct: this in ecx, hidden pointer pushed'),
    ('?call_s_p8@@YAXPAUT@@@Z', r'push\s+0x1111.*push\s+eax.*push\s+ecx.*call',
     'stdcall member: [this][hidden][args]'),
    ('?call_c_p8@@YAXPAUT@@@Z', r'push\s+0x2222.*push\s+eax.*push\s+ecx.*call',
     'cdecl member: [this][hidden][args]'),
    ('?call_st_p8@@YAXXZ', r'push\s+0x3333.*call.*add\s+eax,edx', 'static member POD 8-byte result in edx:eax'),
    ('?call_free_p8@@YAXXZ', r'call.*add\s+eax,edx', 'free function POD 8-byte result in edx:eax'),
    ('?call_free_c8@@YAXXZ', r'push\s+eax.*call', 'free function, 8-byte class with user ctor: hidden pointer'),
    ('?call_free_c4@@YAXXZ', r'push\s+eax.*call', 'free function, 4-byte class with user ctor: hidden pointer'),
    ('?call_fc_p12@@YAXXZ', r'mov\s+edx,0x55.*push\s+eax.*lea\s+ecx,\[edx-0x11\].*call',
     'fastcall aggregate result: ecx/edx keep the arguments, hidden pointer is a stack word'),
    ('?call_fcf_p12@@YAXXZ', r'fstp\s+DWORD PTR \[esp\].*push\s+eax.*mov\s+ecx,0x45.*call',
     'fastcall (float, int): the float on the stack, the int in ecx'),
    ('?call_fc_i64@@YAXXZ', r'push\s+0x1.*mov\s+edx,0x77.*push\s+0x23456789.*lea\s+ecx,\[edx-0x11\]',
     'fastcall (__int64, int, int): the 64-bit value on the stack, ecx/edx the ints'),
    ('?call_narrow@@YAXH@Z', r'movzx\s+ecx,al\s+push\s+ecx', 'bool argument pushed zero-extended'),
    ('?call_narrow@@YAXH@Z', r'push\s+ebx\s+call.*take_char',
     'char argument (char)x pushed as the untruncated int (upper bytes are the caller\'s)'),
    ('?call_narrow@@YAXH@Z', r'inc\s+dl\s+movzx\s+eax,dl\s+push\s+eax',
     'signed char argument computed in a byte register pushed ZERO-extended (movzx)'),
    ('?call_gf_as_double@@YANXZ', r'jmp.*gf@@YAMXZ', 'double function returning a float callee\'s st(0) unchanged'),
]


def t_oracle(dis_path):
    text = open(dis_path).read()
    funcs = {}
    for m in re.finditer(r'^[0-9a-f]+ <([^>]+)>:\n(.*?)(?=^\S|\Z)', text, re.M | re.S):
        funcs.setdefault(m.group(1), m.group(2))
    for fn, rx, meaning in ORACLE_FACTS:
        body = funcs.get(fn, '')
        flat = ' '.join(l.split('\t', 1)[-1].strip() for l in body.split('\n'))
        check(re.search(rx, flat) is not None, 'oracle: ' + meaning)
    # the bridge harness's x86 model gives the same placements
    dem = bh.undname(['?m_p8@T@@QAE?AUP8@@XZ', '?s_p8@T@@QAG?AUP8@@H@Z', '?fc_p12@@YI?AUP12@@HH@Z',
                      '?fcf_p12@@YI?AUP12@@MH@Z', '?st_p8@T@@SA?AUP8@@H@Z', '?free_c4@@YA?AUC4@@XZ'])
    lay = {'P8': bh.Lay(8, 4, 1, 'G1', True), 'P12': bh.Lay(12, 4, 1, 'G2', True), 'C4': bh.Lay(4, 4, 1, 'G1', False)}

    def model(n):
        s = bh.Sig(dem[n])
        return bh.x86_args(s, [bh.ty_of(t, lay) for t in s.params], bh.ty_of(s.ret, lay))
    check(model('?m_p8@T@@QAE?AUP8@@XZ') == ([('this', 'ecx', 4), ('sret', ('stk', 0), 4)], True),
          'model: thiscall member hidden pointer at [esp+4]')
    check(model('?s_p8@T@@QAG?AUP8@@H@Z')[0] == [('this', ('stk', 0), 4), ('sret', ('stk', 4), 4),
                                                 ('p0', ('stk', 8), 4)], 'model: stdcall member [this][hidden][p0]')
    check(model('?fc_p12@@YI?AUP12@@HH@Z')[0] == [('sret', ('stk', 0), 4), ('p0', 'ecx', 4), ('p1', 'edx', 4)],
          'model: fastcall hidden pointer first stack word')
    check(model('?fcf_p12@@YI?AUP12@@MH@Z')[0] == [('sret', ('stk', 0), 4), ('p0', ('stk', 4), 4), ('p1', 'ecx', 4)],
          'model: fastcall float on stack, int in ecx')
    check(model('?st_p8@T@@SA?AUP8@@H@Z')[1] is False, 'model: static member POD 8-byte result in registers')
    check(model('?free_c4@@YA?AUC4@@XZ')[1] is True, 'model: free function non-POD 4-byte result hidden')


C5_SRC = r'''
static int s_a[4];
static int s_b;
int g_c;
int f(int i) { return s_a[i] + s_b + g_c; }
void set(int v) { s_b = v; s_a[1] = v; g_c = v; }
'''


def t_c5(work):
    import c5_pic
    d = os.path.join(work, 'c5')
    os.makedirs(d, exist_ok=True)
    src = os.path.join(d, 'a.c')
    open(src, 'w').write(C5_SRC)
    o = os.path.join(d, 'a.o')
    subprocess.run([qemu_ilp32.CC, '-mabi=ilp32', '-O2', '-fPIC', '-c', src, '-o', o], check=True)
    o2 = os.path.join(d, 'b.o')
    subprocess.run([qemu_ilp32.OBJCOPY, '--redefine-sym', 's_b=__fnv_01234560', '--redefine-sym',
                    'g_c=__fnv_01234570', '--weaken-symbol', '__fnv_01234570', o, o2], check=True)
    rows, nrel, nanch = c5_pic.scan_object(o2)
    kinds = {r[0] for r in rows if r[5] == '__fnv_01234560'}
    check(bool(kinds & {'anchor', 'direct'}), 'c5: a renamed static reached through its section is found (%s)' % kinds)
    check(not any(r[5] == '__fnv_01234570' and r[0] in ('anchor', 'direct') for r in rows),
          'c5: a renamed global reached through the GOT is not flagged')
    src2 = os.path.join(d, 'p.c')
    open(src2, 'w').write('extern int __fnv_01234580; int g(void) { return __fnv_01234580; }\n')
    o3 = os.path.join(d, 'p.o')
    subprocess.run([qemu_ilp32.CC, '-mabi=ilp32', '-O2', '-fno-pic', '-c', src2, '-o', o3], check=True)
    rows, _, _ = c5_pic.scan_object(o3)
    check(any(r[0] == 'pcrel-image' for r in rows), 'c5: a PC-relative reference to an image symbol is found')
    o4 = os.path.join(d, 'p_pic.o')
    subprocess.run([qemu_ilp32.CC, '-mabi=ilp32', '-O2', '-fPIC', '-c', src2, '-o', o4], check=True)
    rows, _, _ = c5_pic.scan_object(o4)
    check(not any(r[0] == 'pcrel-image' for r in rows), 'c5: the same reference with -fPIC goes through the GOT')


def main():
    work = sys.argv[1]
    os.makedirs(work, exist_ok=True)
    t_qemu(work)
    t_bridges(work)
    if len(sys.argv) > 2:
        t_oracle(sys.argv[2])
    t_c5(work)
    print('selftest: %d failed' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
