#!/usr/bin/env python3
"""tests/s13/test_canned.py - canned-this / arg-stack-vs-object (D24, difffuzz-20), on hand-written code.

  retsrc    retsrc.ret_source reads what every `ret` of an exe callee leaves in eax: its first stack argument (ebp
            frame, esp frame), `this`, or nothing it can name (a call result, paths that disagree)
  input     retsrc.returned_input maps that to the callee's pointer parameter label (only for a callee whose
            definition returns a pointer)
  aggptr    runner.compare_args: the address of a by-value aggregate parameter is the oracle's stack slot and the
            native's hidden pointer to its copy: equal at the same offset, a different object or offset is not
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13canned'))

import conv       # noqa: E402
import retsrc     # noqa: E402
import runner     # noqa: E402

FAIL = []
BASE = 0x00401000


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


class FakePE:
    def __init__(self, code):
        self.code = code

    def section_of(self, va):
        return (BASE, len(self.code), 0x20000000, self.code) if BASE <= va < BASE + len(self.code) else None


def src(code):
    retsrc._PE[:] = [FakePE(bytes(code) + b'\xcc' * 32)]
    retsrc._CACHE.clear()
    return retsrc.ret_source(BASE)


def main():
    h = bytes.fromhex
    got = src(h('55' '8bec' '8b4508' '5d' 'c20400'))          # push ebp; mov ebp,esp; mov eax,[ebp+8]; pop ebp; ret 4
    check(got == ('arg', 0), 'ebp frame, [ebp+8]: first stack argument (%s)' % (got,))
    got = src(h('55' '8bec' '8b450c' '5d' 'c20800'))
    check(got == ('arg', 1), 'ebp frame, [ebp+12]: second (%s)' % (got,))
    got = src(h('8b442404' 'c20400'))                          # mov eax,[esp+4]; ret 4
    check(got == ('arg', 0), 'esp frame, [esp+4] (%s)' % (got,))
    got = src(h('51' '8b442408' '59' 'c20400'))                # push ecx; mov eax,[esp+8]; pop ecx; ret 4
    check(got == ('arg', 0), 'esp frame after a push (%s)' % (got,))
    got = src(h('8bc1' 'c3'))                                   # mov eax,ecx; ret
    check(got == 'this', 'mov eax, ecx: this (%s)' % (got,))
    got = src(h('51' '59' '8bc1' 'c3'))                         # ecx clobbered by pop before the copy: not this
    check(got is None, 'ecx rewritten before the copy: unknown (%s)' % (got,))
    got = src(h('85c9' '7406' '8b442404' 'c20400' '33c0' 'c20400'))   # test ecx,ecx; jz L; mov eax,[esp+4]; ret 4; L: xor eax,eax; ret 4
    check(got is None, 'paths that disagree: unknown (%s)' % (got,))
    got = src(h('e8000000 00' .replace(' ', '')) + h('c3'))     # call +0; ret
    check(got is None, 'a call result: unknown (%s)' % (got,))
    got = src(h('8b4104' 'c3'))                                 # mov eax,[ecx+4]; ret
    check(got is None, 'a field: unknown (%s)' % (got,))
    got = src(h('8b442404' '85c0' '7503' '33c0' 'c3' 'c20400'))   # mov eax,[esp+4]; test; jnz; xor eax,eax; ret ...
    check(got is None, 'a path that zeroes eax: unknown (%s)' % (got,))

    cv = conv.Conv('?Assign@Foo@@QAEPAUBar@@PAU2@@Z', 'test')   # Bar *Foo::Assign(Bar *)
    retsrc._PE[:] = [FakePE(h('55' '8bec' '8b4508' '5d' 'c20400') + b'\xcc' * 32)]
    retsrc._CACHE.clear()
    check(retsrc.returned_input(cv, BASE) == 'p0', 'returned_input names the pointer parameter (%s)'
          % retsrc.returned_input(cv, BASE))
    cv2 = conv.Conv('?Assign@Foo@@QAEHPAUBar@@@Z', 'test')      # int result: no pointer to hand back
    check(retsrc.returned_input(cv2, BASE) is None, 'an int result: none')
    retsrc._PE[:] = [FakePE(h('8bc1' 'c3') + b'\xcc' * 32)]
    retsrc._CACHE.clear()
    check(retsrc.returned_input(cv, BASE) == 'this', 'returned_input: this')
    retsrc._PE[:] = [None]
    retsrc._CACHE.clear()
    check(retsrc.returned_input(cv, BASE) is None, 'without the exe: none')

    # compare_args: by-value aggregate parameter addresses
    cvc = conv.Conv('?f@Foo@@QAEXPAUBar@@@Z', 'test')

    class C:
        def __init__(self, args):
            self.args, self.regs, self.pushed = args, {}, None
    sl, buf = 0x06efff00, 0x03001000

    def cmp(px, pa, agg):
        cx = C([0x03000000, 0, px])
        ca = type('A', (), dict(args=[0x03000000, pa] + [0] * 6, fargs=[0] * 8, x8=0, stack=b'', target=0))()
        return runner.compare_args(cvc, cx, ca, agg_ptrs=agg)
    d = cmp(sl, buf, None)
    check(d is not None and d['arg'] == 'p0', 'without the aggregate slots: the stack/buffer pair is a difference')
    d = cmp(sl, buf, [(sl, buf, 8)])
    check(d is None, 'the aggregate\'s slot and its hidden pointer: equal (%s)' % d)
    d = cmp(sl + 4, buf + 4, [(sl, buf, 8)])
    check(d is None, 'the same offset inside it: equal')
    d = cmp(sl + 4, buf, [(sl, buf, 8)])
    check(d is not None, 'another offset on the native side: a difference')
    d = cmp(sl, buf + 0x1000, [(sl, buf, 8)])
    check(d is not None, 'another object natively: a difference')
    # nrvo-elision: the hidden result buffer against a local of the frame (either side) is soft, anything else not
    rb = 0x03001000

    def cmp2(px, pa):
        cx = C([0x03000000, 0, px])
        ca = type('A', (), dict(args=[0x03000000, pa] + [0] * 6, fargs=[0] * 8, x8=0, stack=b'', target=0))()
        return runner.compare_args(cvc, cx, ca, result_buf=rb)
    d = cmp2(rb, sl)
    check(d is not None and d.get('soft') and d.get('nrvo_elided'), 'result buffer vs a local (oracle NRVO): soft (%s)' % d)
    d = cmp2(sl, rb)
    check(d is not None and d.get('soft') and d.get('nrvo_elided'), 'a local vs the result buffer (native NRVO): soft')
    d = cmp2(0x03002000, sl)
    check(d is not None and not d.get('soft'), 'another buffer vs a local: a difference')
    d = cmp2(rb, 0x03003000)
    check(d is not None and not d.get('soft'), 'the result buffer vs another buffer: a difference')
    import classify
    check(classify.hint(None, None, None, dict(kind='call-args', soft=True, nrvo_elided=True, arg='this', x86='00', arm='00'),
                        dict(code=b'\xc3', address=0x00401000), 0).get('cause') == 'nrvo-elision', 'classify: nrvo-elision')
    print('FAIL: %d' % len(FAIL) if FAIL else 'all ok')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
