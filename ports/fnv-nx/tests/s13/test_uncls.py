#!/usr/bin/env python3
"""tests/s13/test_uncls.py - the records F1b left `unclassified`, and the call-flow residue (D24, difffuzz-20), on
hand-written code (no decomp content).

  garbage   classify.hint's garbage probe compares the argument a call-args divergence names (the raw bytes each
            machine passed), not only the run's result / calls / stores: a byte of an uninitialised local, the
            out-parameter of an intercepted callee the harness does not model, reached the compare as a divergence
            whatever the garbage was (0x005b6cd0, 0x00805920, 0x006ab8e0, 0x006fe830, 0x00866a90, 0x00d41930).
            A random-background case is probed too, with the background pinned (world.BG_SEED) and only for the
            value the divergence is about
  pops      runner._learn_pop tries the pushes right before a call first: three call sites over branches put the
            true counts beyond the 27-run bound in the descending order alone (0x00e68870)
  layout    readset.layout_shift: the machines read the same fields at offsets that differ by one constant: a
            member the native places elsewhere is layout-offset (0x00e3cb10, every member read at +0x10)
  x87int    classify.hint: an integer result the rerun with the x87 control word at 24 bits reproduces
            is x87-precision; int_float_close is only recorded (0x00603fc0)
  zerostub  runner.zero_stubs: the native entered a function whose whole body is a placeholder returning 0 (a local
            static, invisible to the relocations): native-zero-stub, for returns, stores and arguments alike
            (0x00d57b20, 0x0065f820)
  params    classify.undeclared_stack_reads: the exe function reads stack words past the parameters its declaration
            has: param-count (0x00d32b40)

Needs unicorn, capstone, aarch64-linux-gnu-as/ld.
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13uncls'))

import classify   # noqa: E402
import conv       # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402
from test_d14 import build_image, call_rel, stub  # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


class Info:
    """callee_info for one intercepted callee of a declared convention: cdecl, int result."""

    def __init__(self, cv):
        self.cv = cv

    def __call__(self, addr):
        return (0, 'int')

    def conv(self, addr):
        return self.cv

    caller_conv = conv


CALLEE = 0x00402000
# void f(): g(*(bool*)&local)  -- a local byte nothing wrote, passed to an intercepted `void g(bool)`
UNINIT = (b'\x55' b'\x89\xe5' b'\x83\xec\x08'
          b'\x0f\xb6\x45\xfc'                      # movzx eax, byte [ebp-4]
          b'\x50')                                  # push eax
UNINIT += call_rel(0x00401000 + len(UNINIT), CALLEE) + b'\x83\xc4\x04' b'\x89\xec' b'\x5d' b'\xc3'
# the native passes the false it wrote
UNINIT_NATIVE = ('\tstp x29, x30, [sp, #-16]!\n\tmov w0, #0\n\tbl __fnv_00402000\n\tldp x29, x30, [sp], #16\n\tret\n')


def probe(bg, seed=7):
    with tempfile.TemporaryDirectory() as d:
        cv = conv.Conv('?f@@YAXXZ', 'test')
        t = runner.Target(cv, 0x00401000, {0x00401000: UNINIT}, build_image(UNINIT_NATIVE, d, [stub(CALLEE)]), {},
                          Info(conv.Conv('?g@@YAX_N@Z', 'test')))
        p = runner.Pair(t, {})
        c = world.Case(seed=seed, bg=bg)
        _, _, div = p.run(c)
        fn = dict(code=UNINIT, address=0x00401000)
        return div, (classify.hint(p, t, c, div, fn, 200000) if div else {})


def test_garbage():
    for bg in ('zero', 'rand'):
        div, h = probe(bg)
        check(div is not None and div['kind'] == 'call-args' and div['arg'] == 'p0',
              'bg %s: the oracle passes a stack byte, the native passes false: call-args p0 (%s)' % (bg, div))
        check(h.get('cause') == 'uninit-x86',
              'bg %s: the oracle\'s argument moves with the stack fill: uninit-x86 (%s)' % (bg, h))
    # a byte the oracle wrote itself does not move with the garbage: the divergence stays
    wrote = UNINIT.replace(b'\x0f\xb6\x45\xfc', b'\xb0\x05' b'\x90\x90')      # mov al, 5; nop; nop
    wrote = wrote.replace(b'\x50', b'\x0f\xb6\xc0' b'\x50', 1)               # movzx eax, al; push eax
    wrote = wrote[:wrote.index(b'\xe8')] + call_rel(0x00401000 + wrote.index(b'\xe8'), CALLEE) + \
        wrote[wrote.index(b'\xe8') + 5:]
    with tempfile.TemporaryDirectory() as d:
        t = runner.Target(conv.Conv('?f@@YAXXZ', 'test'), 0x00401000, {0x00401000: wrote},
                          build_image(UNINIT_NATIVE, d, [stub(CALLEE)]), {}, Info(conv.Conv('?g@@YAX_N@Z', 'test')))
        p = runner.Pair(t, {})
        c = world.Case(seed=7)
        _, _, div = p.run(c)
        h = classify.hint(p, t, c, div, dict(code=wrote, address=0x00401000), 200000) if div else {}
        check(div is not None and h.get('cause') != 'uninit-x86',
              'an argument the oracle computed: not uninit-x86 (%s, %s)' % (div and div['kind'], h.get('cause')))


# void __stdcall f(void **p): p[0](p), p[1](p), p[2](p, p, p) as stdcall calls through pointers the case fills; each
# call after a conditional branch, three words pushed (and removed) at the start: the walk back over the branches
# counts five pushes before the first call, the call itself passes one
POPS_ORACLE = bytes.fromhex(
    '56'                # push esi
    '8b742408'          # mov esi, [esp+8]
    '56' '56' '56'      # push esi x3 (stay until `add esp, 12`)
    '85f6' '7400'       # test esi, esi; je +0
    '56' 'ff16'         # push esi; call [esi]        (pops 4)
    '85c0' '7400'       # test eax, eax; je +0
    '56' 'ff5604'       # push esi; call [esi+4]      (pops 4)
    '85c0' '7400'       # test eax, eax; je +0
    '56' '56' '56' 'ff5608'     # push esi x3; call [esi+8]   (pops 12)
    '83c40c'            # add esp, 12
    '5e'                # pop esi
    'c20400')           # ret 4
POPS_NATIVE = ('\tstp x29, x30, [sp, #-32]!\n\tstr x19, [sp, #16]\n\tmov w19, w0\n\tldr w8, [x19]\n\tmov w0, w19\n'
               '\tblr x8\n\tldr w8, [x19, #4]\n\tmov w0, w19\n\tblr x8\n\tldr w8, [x19, #8]\n\tmov w0, w19\n\tblr x8\n'
               '\tldr x19, [sp, #16]\n\tldp x29, x30, [sp], #32\n\tret\n')


def test_pops():
    with tempfile.TemporaryDirectory() as d:
        cv = conv.Conv('?f@@YGXPAPAX@Z', 'test')
        p = runner.Pair(runner.Target(cv, 0x00401000, {0x00401000: POPS_ORACLE}, build_image(POPS_NATIVE, d), {}), {})
        c = world.Case(seed=9, mem={world.BUF: struct.pack('<III', 0x41414140, 0x52525250, 0x63636360)})
        rx, ra, div = p.run(c)
        learned = sorted(p.pops.values())
        check(rx.esp_ok and learned == [4, 4, 12] and div is None,
              'learned 4, 4 and 12 and then the machines agree (%s, div %s)' % (learned, div and div['kind']))
        rets = [i.address + i.size for i in classify.CS.disasm(POPS_ORACLE, 0x00401000) if i.mnemonic == 'call']
        far = [p._pushes_before(r) for r in rets]
        try:
            near = [p._pushes_before(r, strict=True) for r in rets]
        except TypeError:
            near = None
        check(near == [1, 1, 3] and far[0] > 3,
              'pushes before the three calls: %s without crossing a branch, %s over them' % (near, far))


def run_pair(name, oracle, body, case, defsyms=()):
    with tempfile.TemporaryDirectory() as d:
        cv = conv.Conv(name, 'test')
        t = runner.Target(cv, 0x00401000, {0x00401000: oracle}, build_image(body, d, defsyms), {})
        p = runner.Pair(t, {})
        rx, ra, div = p.run(case)
        fn = dict(code=oracle, address=0x00401000)
        return t, p, div, (classify.hint(p, t, case, div, fn, 200000) if div else {}), fn


def test_layout():
    # int f(int *p): p[3] + p[5] + p[6]; the native reads the members 16 bytes further
    oracle = bytes.fromhex('8b442404' '8b480c' '034814' '034818' '89c8' 'c3')
    mem = bytes((i * 37 + 11) & 0xff for i in range(0x100))
    ok = ('\tldr w1, [x0, #0x1c]\n\tldr w2, [x0, #0x24]\n\tldr w3, [x0, #0x28]\n\tadd w0, w1, w2\n'
          '\tadd w0, w0, w3\n\tret\n')
    t, p, div, h, fn = run_pair('?f@@YAHPAH@Z', oracle, ok, world.Case(seed=3, mem={world.BUF: mem}))
    check(div is not None and div['kind'] == 'return' and h.get('cause') == 'layout-offset' and h.get('shift') == 16,
          'every field read 16 bytes further: layout-offset (%s)' % h)
    # fields read at two different offsets are not a consistent shift
    one = ok.replace('#0x24', '#0x30').replace('#0x28', '#0x38')
    t, p, div, h, fn = run_pair('?f@@YAHPAH@Z', oracle, one, world.Case(seed=3, mem={world.BUF: mem}))
    check(div is not None and h.get('cause') != 'layout-offset',
          'fields elsewhere by two different shifts: not layout-offset (%s)' % h)


def test_x87int():
    check(classify.int_float_close('b78fe928', 'c08fe928'), 'two ints 9 apart near 2^29: within 4 float ulps')
    check(not classify.int_float_close('00000040', '000000e0') and not classify.int_float_close('01000000', '02000000')
          and not classify.int_float_close('ffffff7f', '00000080'), 'small, far apart, or opposite signs: not')
    # int f(float x): (int)(x * x), rounded; the x87 product keeps 53 bits, the native's single does not
    oracle = bytes.fromhex('d9442404' 'd8c8' '83ec04' 'db1c24' '8b0424' '83c404' 'c3')
    nat = '\tfmul s0, s0, s0\n\tfcvtas w0, s0\n\tret\n'
    x = struct.pack('<f', 33333.3)
    t, p, div, h, fn = run_pair('?f@@YAHM@Z', oracle, nat, world.Case(seed=1, args={'p0': x}))
    check(div is not None and div['kind'] == 'return' and h.get('cause') == 'x87-precision',
          'x87 product converted to int, single in the native: x87-precision (%s, %s)' % (div and div['kind'], h))


def test_zerostub():
    # int f(): the oracle returns 5; the native calls a local placeholder `mov w0, #0; ret`
    oracle = bytes.fromhex('b805000000' 'c3')
    nat = ('\tstp x29, x30, [sp, #-16]!\n\tbl ph\n\tldp x29, x30, [sp], #16\n\tret\n'
           '\t.type ph, %function\nph:\n\tmov w0, #0\n\tret\n\t.size ph, 8\n')
    t, p, div, h, fn = run_pair('?f@@YAHXZ', oracle, nat, world.Case(seed=1))
    check(div is not None and div.get('arm_zero_stub') == ['ph'], 'the run names the placeholder it entered (%s)' % (
        div and div.get('arm_zero_stub')))
    import causes
    check(div is not None and causes._rule_unclassified(div, dict(case={}, source='test'), div['kind'])
          == 'native-zero-stub', 'native-zero-stub for that return')
    clean = dict(div)
    clean.pop('arm_zero_stub', None)
    check(causes._rule_unclassified(clean, dict(case={}, source='test'), clean['kind']) != 'native-zero-stub',
          'not without it')


def test_params():
    # int f(int a): reads [esp+4], [esp+8] and [esp+0xc]; the declaration has one parameter
    oracle = bytes.fromhex('8b442404' '03442408' '0344240c' 'c3')
    fn = dict(code=oracle, address=0x00401000)
    cv = conv.Conv('?f@@YAHH@Z', 'test')
    got = classify.undeclared_stack_reads(fn, cv)
    check(got == [4, 8], 'words 1 and 2 past the declared one: %s' % got)
    check(classify.undeclared_stack_reads(fn, conv.Conv('?f@@YAHHHH@Z', 'test')) == [], 'declared: nothing')
    # frame pointer function: [ebp+8] is the first parameter, [ebp+0xc] and [ebp+0x10] the next two
    fr = bytes.fromhex('55' '89e5' '8b4508' '03450c' '034510' '5d' 'c3')
    got = classify.undeclared_stack_reads(dict(code=fr, address=0x00401000), cv)
    check(got == [4, 8], 'ebp frame: the same words (%s)' % got)
    # esp lowered by 0x10: [esp+0x14] is the declared parameter (esp + 4 at entry), [esp+0x18] the next word
    loc = bytes.fromhex('83ec10' '8b442414' '8b4c2418' '83c410' 'c3')
    got = classify.undeclared_stack_reads(dict(code=loc, address=0x00401000), cv)
    check(got == [4], '[esp+0x14] is the declared word, [esp+0x18] the one past it, after sub esp, 0x10 (%s)' % got)


def test_partial():
    # int f(int *p): mov eax, [esp+4]; mov eax, [eax]; mov al, [eax+0x72-0x72]... simplified: load the pointer, then
    # mov al, [eax] -- only al is defined, the upper bytes are the pointer's; the native returns the zero-extended byte
    oracle = bytes.fromhex('8b442404' '8b00' '8a00' 'c3')
    nat = '\tldr w0, [x0]\n\tldrb w0, [x0]\n\tret\n'
    mem = struct.pack('<I', world.BUF + 0x40) + bytes(0x3c) + b'\x07'
    t, p, div, h, fn = run_pair('?f@@YAHPAH@Z', oracle, nat, world.Case(seed=2, mem={world.BUF: mem}))
    check(div is not None and div['kind'] == 'return' and h.get('cause') == 'partial-return',
          'the oracle returns in al, the upper bytes are the pointer: partial-return (%s, %s)' % (div, h))
    wide = bytes.fromhex('8b442404' '8b00' '0fb600' 'c3')            # movzx eax, byte [eax]: a full write
    t, p, div, h, fn = run_pair('?f@@YAHPAH@Z', wide, '\tldr w0, [x0]\n\tldrb w0, [x0]\n\tadd w0, w0, #1\n\tret\n',
                                world.Case(seed=2, mem={world.BUF: mem}))
    check(div is not None and h.get('cause') != 'partial-return', 'a full write of eax: not partial-return (%s)' % h)


if __name__ == '__main__':
    test_partial()
    test_garbage()
    test_pops()
    test_layout()
    test_x87int()
    test_zerostub()
    test_params()
    print('%d failures' % len(FAIL))
    sys.exit(1 if FAIL else 0)
