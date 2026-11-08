#!/usr/bin/env python3
"""tests/s13/test_d14.py - the difffuzz-14 scoped harness changes on hand-written code (no decomp content).

  dtor     an MSVC deleting destructor target runs with flags = 1 only, its return value is not compared
  noreturn a call of _CxxThrowException (oracle) / a noreturn native callee ends the run on that machine
  math     CRT log10 / pow / _CIpow run in Python on the oracle, the native libm names and CRT code stubs likewise:
           log10(0) = -inf on both (Unicorn's fyl2x gave 0.0)
  vslot    a native call of the link's fnvnx_vslot_trap is named in the error and classified native-undefined
  keys     fnkeys.CHANGES select functions by static facts; fuzzcache rechecks matching old records once

Needs unicorn, capstone, aarch64-linux-gnu-as/ld.
"""
import json
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13d14'))

import a64image   # noqa: E402
import conv       # noqa: E402
import fnkeys     # noqa: E402
import fuzzcache  # noqa: E402
import libcalls   # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def build_image(body, d, defsyms=()):
    s = os.path.join(d, 'f.s')
    open(s, 'w').write('\t.text\n\t.globl __fnv_00401000\n\t.type __fnv_00401000, %function\n__fnv_00401000:\n'
                       + body)
    o, elf, ld = os.path.join(d, 'f.o'), os.path.join(d, 'f.elf'), os.path.join(d, 'layout.ld')
    open(ld, 'w').write(a64image.LAYOUT % a64image.A64_BASE)
    subprocess.run(['aarch64-linux-gnu-as', '-mabi=ilp32', s, '-o', o], check=True)
    cmd = ['aarch64-linux-gnu-ld', '-m', 'aarch64linux32', '-static', '-e', '0', '-T', ld, '-o', elf, o]
    for n, a in defsyms:
        cmd += ['--defsym', '%s=0x%x' % (n, a)]
    subprocess.run(cmd, check=True, capture_output=True)
    return a64image.Image(elf, {}, {})


def call_rel(at, target):
    return b'\xe8' + struct.pack('<i', target - (at + 5))


def stub(x):
    return ('__fnv_%08x' % x, a64image.CODE_STUB + x)


def run(cv, oracle, body, case, defsyms=()):
    with tempfile.TemporaryDirectory() as d:
        img = build_image(body, d, defsyms)
        pair = runner.Pair(runner.Target(cv, 0x00401000, {0x00401000: oracle}, img, {}), {})
        return pair.run(case)


def dbl(x):
    return struct.pack('<d', x)


def test_math():
    cv = conv.Conv('?f@@YANN@Z', 'test')                    # double __cdecl f(double)
    # fld qword [esp+4]; sub esp,8; fstp qword [esp]; call log10; add esp,8; ret
    head = bytes.fromhex('dd442404' '83ec08' 'dd1c24')
    ora = head + call_rel(0x00401000 + len(head), 0x00ecbc80) + bytes.fromhex('83c408' 'c3')
    arm = '\tb __fnv_00ecbc80\n'
    for x in (0.0, 1000.0, -1.0, 0.5):
        rx, ra, d = run(cv, ora, arm, world.Case(seed=1, args={'p0': dbl(x)}), [stub(0x00ecbc80)])
        check(d is None and not rx.calls and not ra.calls, 'log10(%r): builtin on both machines, equal (%s)' % (
            x, (rx.ret or b'').hex()))
    rx, _, _ = run(cv, ora, arm, world.Case(seed=1, args={'p0': dbl(0.0)}), [stub(0x00ecbc80)])
    check(rx.ret is not None and struct.unpack('<d', rx.ret[:8])[0] == float('-inf'), 'oracle log10(0) = -inf')
    cv2 = conv.Conv('?f@@YANNN@Z', 'test')                  # double __cdecl f(double x, double y): pow via _CIpow
    ora2 = bytes.fromhex('dd442404' 'dd44240c') + call_rel(0x00401008, 0x00ec9b00) + b'\xc3'
    for x, y in ((2.0, 10.0), (0.0, -1.0), (-8.0, 1.0 / 3), (9.0, 0.5)):
        rx, ra, d = run(cv2, ora2, '\tb pow\n', world.Case(seed=2, args={'p0': dbl(x), 'p1': dbl(y)}),
                        [('pow', a64image.CODE_STUB + 0x00ec9ac0)])
        check(d is None and not rx.calls, '_CIpow(%r, %r) on the oracle = native pow (%s)' % (
            x, y, (rx.ret or b'').hex()))
    check(libcalls.crt_math('powf', (2.0, 3.0)) == 8.0 and libcalls.crt_math('floorf', (-0.5,)) == -1.0 and
          str(libcalls.crt_math('ceil', (-0.5,))) == '-0.0', 'float variants and signed zero')


def test_noreturn():
    cv = conv.Conv('?f@@YAHH@Z', 'test')                    # int __cdecl f(int)
    # push 0; push 0; call _CxxThrowException; (then garbage: a wrong eax and a ret the run must not reach)
    ora = bytes.fromhex('6a00' '6a00') + call_rel(0x00401004, 0x00ec7894) + bytes.fromhex('b801000000' 'c3')
    arm_stub = '\tstp x29, x30, [sp, #-16]!\n\tbl __fnv_00ec7894\n\tudf #0\n'
    rx, ra, d = run(cv, ora, arm_stub, world.Case(seed=3, args={'p0': b'\x05\0\0\0'}), [stub(0x00ec7894)])
    check(rx.noreturn == '_CxxThrowException' and ra.noreturn == '_CxxThrowException' and rx.error is None and
          ra.error is None and d is None, 'a throw ends the run on both machines (no exc:1 after the call)')
    # the native returns instead of throwing: a divergence
    rx, ra, d = run(cv, ora, '\tmov w0, #1\n\tret\n', world.Case(seed=3, args={'p0': b'\x05\0\0\0'}))
    check(d is not None and d['kind'] in ('call-count', 'return'), 'a throw only on the oracle diverges (%s)' % (
        d and d['kind']))
    check(libcalls.noreturn_a64('_ZSt20__throw_length_errorPKc') and libcalls.noreturn_a64('__cxa_rethrow') and
          not libcalls.noreturn_a64('free'), 'native noreturn names')
    check(libcalls.A64.get('__cxa_allocate_exception') == 'cxa_alloc_exc' and
          not any(lo <= libcalls.A64_EXC_OBJ < hi for lo, hi in world.COMPARED), 'exception objects: private')


def test_dtor():
    cv = conv.Conv('??_GFoo@@UAEPAXI@Z', 'test')            # void *__thiscall Foo::`scalar deleting destructor'(uint)
    c = world.Case(seed=4, args={'this': b'\0' * 4, 'p0': b'\0\0\0\0'})
    runner.deleting_flags(cv, c)
    check(c.args.get('p0') == b'\x01\0\0\0', '??_G flags: 0 -> 1 (the call D0 stands for)')
    cv2 = conv.Conv('?f@@YAHH@Z', 'test')
    c2 = world.Case(seed=4, args={'p0': b'\0\0\0\0'})
    runner.deleting_flags(cv2, c2)
    check(c2.args['p0'] == b'\0\0\0\0', 'other targets keep their arguments')
    # ??_G returns this (oracle: mov eax, ecx; ret 4), D0 returns nothing: not compared
    rx, ra, d = run(cv, bytes.fromhex('8bc1' 'c20400'), '\tret\n', world.Case(seed=4, args={'p0': b'\0' * 4}))
    check(d is None, 'deleting destructor: the return value is not compared')


def test_vslot():
    cv = conv.Conv('?f@@YAHH@Z', 'test')
    body = ('\tadr x1, .Ltext\n\tb fnvnx_vslot_trap\n\t.globl fnvnx_vslot_trap\n'
            '\t.type fnvnx_vslot_trap, %function\nfnvnx_vslot_trap:\n\tbrk #0xf5\n'
            '\t.section .rodata\n.Ltext:\n\t.asciz "virtual function defined nowhere: _ZN1A1fEv (slot 3 of _ZTV1A)"\n')
    rx, ra, d = run(cv, bytes.fromhex('31c0c3'), body, world.Case(seed=5, args={'p0': b'\0' * 4}))
    check(ra.error == 'trap:fnvnx_vslot_trap:virtual function defined nowhere: _ZN1A1fEv (slot 3 of _ZTV1A)',
          'vslot trap named: %s' % ra.error)
    import classify
    h = classify.hint(None, None, None, dict(kind='error', x86=None, arm=ra.error),
                      dict(code=b'\xc3', address=0x00401000), 0)
    check(h.get('cause') == 'native-undefined' and '_ZN1A1fEv' in h.get('trap', ''),
          'vslot trap classified native-undefined')


def test_keys():
    F = fnkeys.Facts
    tags = fnkeys.change_tags
    check(tags(F('??_GFoo@@UAEPAXI@Z')) == ['d14-dtor'], 'deleting destructor selected')
    check(tags(F('?f@@YAXXZ', ocalls={0x00ec7894})) == ['d14-noreturn'], 'oracle throw selected')
    check(tags(F('?f@@YAXXZ', nrefs={'__cxa_throw_bad_array_new_length'})) == ['d14-noreturn'], 'GCC new[] check')
    check(tags(F('?f@@YAXXZ', ocalls={0x00ec9b00})) == ['d14-math'] and
          tags(F('?f@@YAXXZ', nrefs={'powf'})) == ['d14-math'], 'math selected (_CIpow, powf)')
    check(tags(F('?f@@YAXXZ', ocalls={0x00401000}, nrefs={'memcpy', 0x00aa1460})) == [], 'others keep their keys')
    cat = [dict(address='00401000', source='a', calls=['00401100'], tails=[]),
           dict(address='00401100', source='a', calls=['00ec9ac0'], tails=[]),
           dict(address='00401200', source='b', calls=['00401100'], tails=[])]
    ocl = fnkeys.oracle_closure(cat)
    check(0x00ec9ac0 in ocl['00401000'] and 0x00ec9ac0 not in ocl['00401200'],
          'oracle calls through same-unit callees only')
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, 'div'))
        rows = {'00401000': dict(address='00401000', status='ok', key='k1'),
                '00401100': dict(address='00401100', status='ok', key='k2', changes=[c.tag for c in fnkeys.CHANGES]),
                # (d27-eqbase rechecks every hard record a row has not seen it for: this one has, so only the
                # d14-vslot recheck of 00401000 is under test)
                '00401200': dict(address='00401200', status='ok', key='k3', changes=['d27-eqbase'])}
        fuzzcache.save_rows(d, rows)
        for a in rows:
            json.dump(dict(divergence=dict(kind='error', x86=None, arm='exc:7@0a045d54')),
                      open(os.path.join(d, 'div', a + '-0.json'), 'w'))
        json.dump(dict(divergence=dict(kind='return')), open(os.path.join(d, 'div', '00401200-0.json'), 'w'))
        due = fuzzcache.recheck_due(d)
        check(due == {'00401000'}, 'recheck: an old exc:7 record once (not after the change, not other records): %s'
              % sorted(due))
        tj, kj, out, cnt = (os.path.join(d, x) for x in ('t.jsonl', 'k.json', 'todo.jsonl', 'c.json'))
        open(tj, 'w').write(''.join(json.dumps(dict(address=a)) + '\n' for a in rows))
        json.dump({'00401000': 'k1', '00401100': 'k2', '00401200': 'k3'}, open(kj, 'w'))
        fuzzcache.cmd_todo(d, tj, kj, out, cnt)
        c = json.load(open(cnt))
        check(c['recheck'] == 1 and c['up_to_date'] == 2, 'todo counts the recheck: %s' % c)


if __name__ == '__main__':
    test_math()
    test_noreturn()
    test_dtor()
    test_vslot()
    test_keys()
    print('%d failures' % len(FAIL))
    sys.exit(1 if FAIL else 0)
