#!/usr/bin/env python3
"""tests/s13/test_eqrest.py - EQ's farm-baseline residue (difffuzz-20, hand-built records, no decomp content).

  static-this  a pointer argument that is the address of a function-local static: the oracle passes the static's image
               address, the native its own .bss copy. local-static, not call-args / split-global (0x00aff100)
  jumptab      classify.entry_register_reads follows the cases of a switch jump table inside the function: a register
               a case body reads before writing it (edx pushed behind `jmp [eax*4 + table]`) is a custom register
               (0x00e03170)
  leaf-regs    an intercepted call to an exe leaf that writes neither ecx nor edx leaves them as the caller held them (MSVC
               keeps a value in edx over `call 0x00d3ab60`, which writes only eax and cl); a callee that writes edx
               still clobbers it (0x00d3abc0)
  guard-skip   a call-count where the oracle's static guard is set (no calls) and the native's own guard runs the
               initializer: local-static (0x00aff100)
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='eqrest'))

sys.path.insert(0, HERE)
import classify   # noqa: E402
import conv       # noqa: E402
import retsrc     # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402
from test_d14 import build_image, call_rel, stub  # noqa: E402
from test_uncls import Info  # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


class T:
    local_statics = set(range(0x011f81e8, 0x011f81e8 + 16)) | {0x011f824c}
    conv = conv.Conv('?f@@YAXXZ', 'test')
    source = None


def cause(d, fn):
    try:
        return classify.hint(None, T(), world.Case(seed=1, bg='rand'), d, fn, 0).get('cause')
    except AttributeError:      # falls through to the probes, which need a pair: not local-static
        return 'probe'


def test_jumptab():
    code = bytes.fromhex('48'                   # dec eax          (the case index, in eax)
                         'ff248510104000'       # jmp dword ptr [eax*4 + 0x401010]
                         'c3' + '90' * 7 +      # pad to 0x401010
                         '18104000' '18104000'  # the table: two cases, both at 0x401018
                         '52' 'c3')             # push edx; ret
    cv = conv.Conv('?f@@YAXH@Z', 'test')
    got = classify.entry_register_reads(dict(code=code, address=0x00401000), cv)
    check('edx' in got, 'a register read in a jump-table case is a custom register: %s' % got)


def test_guard():
    fn = dict(code=bytes.fromhex('a14c821f01' 'c3'), address=0x00aff100)      # mov eax, [0x011f824c]; ret
    d = dict(kind='call-count', x86=0, arm=2, arm_local_static=True, arm_private_reads=1,
             arm_private_syms=['_ZGVZN3Foo3barEvE1s'])
    check(cause(d, fn) == 'local-static', 'guard skipped by the oracle, run by the native: local-static (%s)' % cause(d, fn))
    check(cause(dict(d, arm_private_syms=['_ZZN3Foo3barEvE1s']), fn) != 'local-static', 'no guard among the reads: stays')
    check(cause(dict(d, x86=1), fn) != 'local-static', 'the oracle called too: stays')
    fn2 = dict(code=b'\xc3', address=0x00aff100)
    check(cause(d, fn2) != 'local-static', 'the exe code touches no local static: stays')


class FakePE:
    def __init__(self, blobs):
        img = bytearray(b'\xcc' * 0x3000)
        for a, b in blobs.items():
            img[a - 0x00401000:a - 0x00401000 + len(b)] = b
        self.code = bytes(img)

    def section_of(self, va):
        return (0x00401000, len(self.code), 0x20000000, self.code) if 0x00401000 <= va < 0x00404000 else None


def test_leafregs():
    CALLEE = 0x00402000
    # void f(int *p): int *q = p; g(p); *q = 7        (q is kept in edx over the call)
    code = bytes.fromhex('8b542404' '52') + call_rel(0x00401000 + 5, CALLEE) + bytes.fromhex('83c404' 'c70207000000' 'c3')
    nat = ('\tstp x29, x30, [sp, #-32]!\n\tstr x19, [sp, #16]\n\tmov w19, w0\n\tbl __fnv_00402000\n\tmov w1, #7\n'
           '\tstr w1, [x19]\n\tldr x19, [sp, #16]\n\tldp x29, x30, [sp], #32\n\tret\n')
    case = world.Case(seed=3, args={'p0': struct.pack('<I', world.BUF + 0x40)})
    for name, leaf, want in (('a leaf that writes eax only', bytes.fromhex('8b442404' 'c700' '00000000' 'c3'), True),
                             ('a leaf that writes edx', bytes.fromhex('8b442404' '33d2' 'c3'), False)):
        retsrc._PE[:] = [FakePE({CALLEE: leaf})]
        retsrc._PRES.clear() if hasattr(retsrc, '_PRES') else None
        with tempfile.TemporaryDirectory() as d:
            cv = conv.Conv('?f@@YAXPAH@Z', 'test')
            t = runner.Target(cv, 0x00401000, {0x00401000: code}, build_image(nat, d, [stub(CALLEE)]), {},
                              Info(conv.Conv('?g@@YAXPAH@Z', 'test')))
            rx, ra, div = runner.Pair(t, {}).run(case)
        check((div is None) == want, '%s: %s (%s)' % (name, 'edx kept, the machines agree' if want else 'edx clobbered, a '
                                                      'difference stays', div and div['kind']))
    retsrc._PE[:] = [None]


def main():
    test_leafregs()
    test_jumptab()
    test_guard()
    fn = dict(code=b'\xc3', address=0x00401000)
    d = dict(kind='call-args', arg='this', cls='P', x86='e8811f01', arm='58121b0a', index=0, arm_local_static=True)
    h = cause(d, fn)
    check(h == 'local-static', 'this = a local static: local-static (%s)' % h)
    d2 = dict(d, x86='00901f01')
    h = cause(d2, fn)
    check(h != 'local-static', 'a pointer that is no local static stays (%s)' % h)
    d3 = dict(d, arm_local_static=False)
    h = cause(d3, fn)
    check(h != 'local-static', 'no private-static evidence on the native: stays (%s)' % h)
    print('FAIL' if FAIL else 'PASS')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
