#!/usr/bin/env python3
"""tests/s13/test_d19.py - difffuzz-19 (full refresh) on hand-written code (no decomp content).

  untyped  a call nothing describes (wild / unplanted) gets a canned result whose low byte is 0/1 on both machines:
           MSVC tests al, GCC bit 0 (S1-fuzzA: 17 records)
  thunk    the native entry of an override of a secondary base's virtual is the link's __fnvnx_msvc_<addr>
  order    a call through a vtpad slot is vptr-unpaired even when the oracle's call is unplanted
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13d19'))

import arm64emu   # noqa: E402
import classify   # noqa: E402
import conv       # noqa: E402
import fnkeys     # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402
from test_d14 import build_image  # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


# bool f(void **p): return (*p)() != 0 as a byte test: MSVC `test al, al`, GCC `tbz`/and bit 0
ORACLE = bytes.fromhex('8b442404' 'ff10' '84c0' '0f95c0' '0fb6c0' 'c3')   # mov eax,[esp+4]; call [eax]; test al,al;
ARM = ('\tstp x29, x30, [sp, #-16]!\n\tldr w8, [x0]\n\tblr x8\n\tand w0, w0, #1\n'   # setne al; movzx eax, al; ret
       '\tldp x29, x30, [sp], #16\n\tret\n')


def main():
    check(world.canned_kind((None, None)) == 'untyped', 'no conv: untyped')
    cv = conv.Conv('?f@@YA_NPAPAX@Z', 'test')
    with tempfile.TemporaryDirectory() as d:
        t = runner.Target(cv, 0x00401000, {0x00401000: ORACLE}, build_image(ARM, d), {})
        p = runner.Pair(t, {})
        bad = []
        for seed in range(40):
            c = world.Case(seed=seed, mem={world.BUF: struct.pack('<I', 0x41414140)}, rets=[bytes([2 + seed % 3]) +
                                                                                          b'\0' * 7])
            _, _, div = p.run(c)
            if div:
                bad.append((seed, div['kind']))
        check(not bad, 'untyped canned results 2/3/4: the low byte is 0/1 on both machines (%s)' % bad[:3])
        img = build_image('\tret\n\t.globl __fnvnx_msvc_00401000\n__fnvnx_msvc_00401000:\n\tsub w0, w0, #24\n'
                          '\tb __fnv_00401000\n', d)
        e = arm64emu.Arm64Engine.entry(type('E', (), {'image': img})(), 0x00401000)
        check(e == img.syms['__fnvnx_msvc_00401000'], 'secondary-base override: entered at __fnvnx_msvc_<addr>')
    div = dict(kind='call-target', x86='41414140', arm='name:__s13_vtpad_01000000_9', vcall='unplanted', index=0)
    h = classify.hint(None, None, None, div, dict(code=b'\xc3', address=0x00401000), 0)
    check(h.get('cause') == 'vptr-unpaired', 'a vtpad slot is vptr-unpaired before oracle-vcall (%s)' % h)
    check(fnkeys.HARNESS == 'difffuzz-19', 'HARNESS names the full refresh')
    # a custom-convention function whose case does not depend on garbage gets its real cause, not x86-custom-cc
    cc_or = bytes.fromhex('8b00' 'c3')                  # mov eax, [eax]; ret  (reads eax: a custom convention)
    cvc = conv.Conv('?f@@YAHPAH@Z', 'test')
    regs = classify.entry_register_reads(dict(code=cc_or, address=0x00401000), cvc)
    if regs:
        cvc.with_registers(regs)
    with tempfile.TemporaryDirectory() as d:
        tc = runner.Target(cvc, 0x00401000, {0x00401000: cc_or}, build_image('\tldr w0, [x0]\n\tadd w0, w0, #1\n'
                                                                              '\tret\n', d), {})
        pc = runner.Pair(tc, {})
        c = world.Case(seed=2, mem={world.BUF: b'\x05\0\0\0'})
        _, _, dv = pc.run(c)
        h = classify.hint(pc, tc, c, dv, dict(code=cc_or, address=0x00401000), 200000) if dv else {}
        check(bool(regs) and dv is not None and h.get('cause') not in (None, 'x86-custom-cc'),
              'custom convention, garbage-independent case: a real cause (%s, regs %s)' % (h.get('cause'), regs))
    check(world.mappable(0x09900000, 'a64') and not world.mappable(0x0a000000, 'a64') and
          world.mappable(0x0e030000, 'x86') and not world.mappable(0x0e001000, 'x86') and
          any(lo <= 0x0e030000 < hi for lo, hi in world.WILD_X86), 'private-range holes: background and wild')
    fake = type('E', (), {'image': type('I', (), {'stubs': {0x09000000: 'a', 0x09000010: 'b'}, 'end': 0x0a123456})(),
                          '_holes': None})()
    hs = arm64emu.Arm64Engine.holes(fake)
    check(any(lo <= 0x0a200000 < hi for lo, hi in hs) and not any(lo <= 0x0a100000 < hi for lo, hi in hs) and
          any(lo <= 0x09001000 < hi for lo, hi in hs) and not any(lo <= 0x09000010 < hi for lo, hi in hs),
          'per image: after the image end and above the last named stub, not inside them')
    rt = world.returns_this
    check(rt((conv.Conv('??0Foo@@QAE@XZ', 't'), None)) and rt((conv.Conv('?f@Foo@@QAEAAV1@ABV1@@Z', 't'),)) and
          rt((None, conv.Conv('?g@Foo@@QAEPAV1@XZ', 't'))) and
          not rt((conv.Conv('?h@Foo@@QAEPAVBar@@XZ', 't'),)) and not rt((conv.Conv('?f@@YAPAXXZ', 't'),)),
          'returns this: ctors, own-class pointer/reference results (not another class, not free functions)')
    print('%d failures' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
