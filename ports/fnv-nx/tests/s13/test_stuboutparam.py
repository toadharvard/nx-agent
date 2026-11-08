#!/usr/bin/env python3
"""tests/s13/test_stuboutparam.py - difffuzz-20 stub-outparam: a native temporary that a stubbed callee should have
filled and the native then copies back is not an uninitialised read of the exe's code. MSVC passes its destination to
the callee; GCC passes a stack temporary and copies it. The stubbed callee writes nothing, so the copy moves the
planted stack garbage into the destination (uninit-arm). The probe zeroes the never-stored words of buffers the native
passes to an intercepted call and runs both seeds again; if the native is then stable and equal, the cause is
stub-outparam (artifact). A native that reads a local no callee gets a pointer to stays uninit-arm."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13stubout'))

import arm64emu   # noqa: E402
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


MEM = 0x03000000
G = 0x00402000
# void f(int *p): push p; call G; add esp, 4; ret          (G is stubbed and writes nothing)
ORACLE = bytes.fromhex('8b442404' '50') + call_rel(0x00401005, G) + bytes.fromhex('83c404' 'c3')
# the native: int tmp; G(&tmp); *p = tmp;
COPY_BACK = ('\tstp x29, x30, [sp, #-32]!\n\tmov x29, sp\n\tstr x19, [sp, #16]\n\tmov x19, x0\n\tadd x0, sp, #24\n'
             '\tbl __fnv_%08x\n\tldr w1, [sp, #24]\n\tstr w1, [x19]\n\tldr x19, [sp, #16]\n\tldp x29, x30, [sp], #32\n'
             '\tret\n' % G)
# the native: int tmp; G(p); *p = *(int *)(sp + 24)   (reads a local nobody got a pointer to)
LOCAL = ('\tstp x29, x30, [sp, #-32]!\n\tmov x29, sp\n\tstr x19, [sp, #16]\n\tmov x19, x0\n'
         '\tbl __fnv_%08x\n\tldr w1, [sp, #24]\n\tstr w1, [x19]\n\tldr x19, [sp, #16]\n\tldp x29, x30, [sp], #32\n'
         '\tret\n' % G)


def hint(body):
    cv = conv.Conv('?f@@YAXPAH@Z', 'test')
    fn = dict(code=ORACLE, address=0x00401000)
    with tempfile.TemporaryDirectory() as d:
        t = runner.Target(cv, 0x00401000, {0x00401000: ORACLE}, build_image(body, d, [stub(G)]), {})
        p = runner.Pair(t, {})
        c = world.Case(seed=5, args={'p0': MEM.to_bytes(4, 'little')}, mem={MEM: bytes(4)})
        rx, ra, div = p.run(c)
        return div, (classify.hint(p, t, c, div, fn, 200000) if div else {})


def main():
    div, h = hint(COPY_BACK)
    check(div is not None and h.get('cause') == 'stub-outparam',
          'a temporary passed to a stubbed callee and copied back: stub-outparam (%s)' % h)
    div, h = hint(LOCAL)
    check(div is not None and h.get('cause') == 'uninit-arm',
          'a local no callee was given a pointer to: still uninit-arm (%s)' % h)
    print('%d failures' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
