#!/usr/bin/env python3
"""tests/s13/test_difffuzz.py - self-test of tools/difffuzz on hand-written code (no decomp content).

Pure parts (libcalls semantics, x87 extended conversion, case round trip, float verdicts, rule-attribution body
cutting, Itanium class names) and an end-to-end run: `int f(char c) { return c < 0; }` as hand-assembled MSVC-style
x86 (the oracle) against two hand-written AArch64 ILP32 versions, a correct one (sxtb) and one with the unsigned-char
bug (uxtb). The fuzzer must find no divergence for the first and a minimised `return` divergence for the second.

Needs unicorn, capstone, pyelftools, llvm-undname, aarch64-linux-gnu-as/ld.
"""
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, '..', '..', 'tools', 'difffuzz')
sys.path.insert(0, TOOLS)
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13test'))

import a64image  # noqa: E402
import attrib    # noqa: E402
import conv      # noqa: E402
import engine    # noqa: E402
import libcalls  # noqa: E402
import runner    # noqa: E402
import world     # noqa: E402
import x86emu    # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


class FakeMem:
    def __init__(self):
        self.m = bytearray(0x10000)

    def read(self, a, n):
        return bytes(self.m[a:a + n])

    def write(self, a, b):
        self.m[a:a + len(b)] = b

    def cstr(self, a, limit=4096):
        e = self.m.index(0, a)
        return bytes(self.m[a:e])


def test_pure():
    m = FakeMem()
    m.write(0x100, b'Hello\0'); m.write(0x200, b'hello\0')
    check(libcalls.run('strlen', m, [0x100]) == 5, 'strlen')
    check(libcalls.run('stricmp', m, [0x100, 0x200]) == 0, 'stricmp equal ignoring case')
    check(libcalls.run('strcmp', m, [0x100, 0x200]) == 0xffffffff, 'strcmp H < h')
    libcalls.run('memcpy', m, [0x300, 0x100, 6])
    check(m.cstr(0x300) == b'Hello', 'memcpy')
    check(libcalls.run('strchr', m, [0x100, ord('l')]) == 0x102, 'strchr')
    ext = struct.pack('<Q', 0xc000000000000000) + struct.pack('<H', 16383)   # 1.5
    check(x86emu.ext_to_double(ext) == 1.5, 'x87 80-bit -> double')
    c = world.Case(seed=7, args={'p0': b'\x80'}, mem={0x03000000: b'\x01\x02'}, rets=[b'\0' * 16], bg='rand')
    c2 = world.Case.from_json(c.to_json())
    check((c2.seed, c2.args, c2.mem, c2.bg) == (c.seed, c.args, c.mem, c.bg), 'case json round trip')
    inf = float('inf')
    cm = libcalls.crt_math
    check(cm('log', (0.0,)) == -inf and cm('log10', (-0.0,)) == -inf and cm('log10', (1000.0,)) == 3.0,
          'crt_math: log/log10 of 0 is -inf (C99), log10(1000) = 3')
    check(cm('log', (-1.0,)) != cm('log', (-1.0,)), 'crt_math: log of a negative is NaN')
    check(cm('pow', (0.0, -1.0)) == inf and cm('pow', (-0.0, -3.0)) == -inf and cm('pow', (10.0, 400.0)) == inf
          and cm('pow', (-10.0, 401.0)) == -inf, 'crt_math: pow poles and overflow keep C99 signs')
    check(cm('exp', (1000.0,)) == inf and cm('exp', (-1000.0,)) == 0.0, 'crt_math: exp overflow / underflow')
    check(str(cm('ceil', (-0.5,))) == '-0.0' and cm('floor', (-0.5,)) == -1.0 and cm('floor', (inf,)) == inf,
          'crt_math: floor/ceil keep signed zero and infinities')
    check(cm('atan2', (1.0, 0.0)) == cm('atan', (inf,)) and cm('powf', (2.0, 3.0)) == 8.0,
          'crt_math: atan2, float variants')
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_32
    xu = Uc(UC_ARCH_X86, UC_MODE_32)
    xu.mem_map(0x1000, 0x1000)

    def use(code, fret='int'):
        xu.mem_write(0x1000, code + bytes(16))
        return x86emu.x87_result_use(xu, 0x1000, fret)
    check(use(b'\xd9\x5d\xfc') == 'f32' and use(b'\xdd\x5d\xf8') == 'f64', 'x87 use: fstp m32 / m64')
    check(use(b'\x83\xc4\x08\xd9\x5d\xfc') == 'f32', 'x87 use: after the cdecl cleanup')
    check(use(b'\x8b\xe5\x5d\xc3', 'f32') == 'f32' and use(b'\x8b\xe5\x5d\xc3') == 'int',
          'x87 use: a ret returns the function\'s own result kind')
    check(use(b'\x89\x45\xfc') == 'int' and use(b'\xd9\x45\xfc') == 'int', 'x87 use: mov / fld m32 do not use ST0')
    v = runner.float_verdict(4, struct.pack('<f', 1.0), struct.pack('<f', 1.0000001))
    check(v['tolerant'] and v['ulp'] == 1, 'float verdict: 1 ulp is tolerant')
    src = '// @fnv 0x00401000\nint f(int a) { if (a) { return 1; } return "}"[0]; }\n// @fnv 0x00401010\nint g() {}\n'
    b = attrib.bodies(src)
    check(sorted(b) == [0x401000, 0x401010] and b[0x401000].rstrip().endswith('}'), 'attrib body cutting')
    check(engine.itanium_class('N3Foo3BarE') == 'Foo::Bar' and engine.itanium_class('12FUN_00404920') ==
          'FUN_00404920', 'itanium class names')
    check(world.mappable(0x100, 'x86') is False and world.mappable(0x08001000, 'a64') is False and
          world.mappable(0x00fdf000, 'a64'), 'world mapping policy')
    import vtmodel
    check(vtmodel.td_class('.?AVBar@Foo@@') == 'Foo::Bar' and vtmodel.td_class('.?AV?$T@H@@') is None,
          'RTTI type descriptor names')
    check(vtmodel.native_slots([0, None, 'a', 'b', 'c', -8, None, 'd'], 8) == 3 and
          vtmodel.native_slots([0, None, 'a', 'b'], 8) == 2, 'native vtable length up to the next vtable of the group')
    c = world.Case(seed=3, vptrs={0x03000000: 0x0101144c})
    c2 = world.Case.from_json(c.to_json())
    check(c2.vptrs == c.vptrs and c.copy().vptrs == c.vptrs, 'planted vptrs survive json and copy')
    w = world.World(c, {}, {0x0101144c: b'\x10\x20\x40\x00'})
    w.a64_vptrs = {0x03000000: 0x0bc00008}
    check(world.u32(w.page(0x03000000)) == 0x0101144c and world.u32(w.page_a64(0x03000000)) == 0x0bc00008 and
          w.page_a64(0x01011000) == w.page(0x01011000), 'x86 sees the image vftable, AArch64 the address point')
    cmp = runner.compare
    import types
    mk = lambda ts: types.SimpleNamespace(error=None, calls=[types.SimpleNamespace(target=t) for t in ts], ret=None,
                                         writes={}, saved_ok=True)
    cv0 = conv.Conv('?f@@YAXXZ', 'test')
    d = cmp(cv0, mk([0x00401230]), mk([0x00405670]), w, vcalls=({0x00405670}, set()))
    check(d and d.get('vcall') == 'unplanted', 'a differing call to no direct target and no planted slot: unplanted')
    d = cmp(cv0, mk([0x00401230]), mk([0x00405670]), w, vcalls=(set(), {0x00401230}))
    check(d and d.get('vcall') == 'planted', 'a differing call through a planted slot is kept as such')
    al = [world.alloc_result(k) for k in range(3)]
    check(all(world.HEAP <= a < world.HEAP_END for a in al) and len(set(al)) == 3 and
          not {a & ~0xfff for a in al} & {world.ref_result(k) & ~0xfff for k in range(0x200)},
          'allocator results: distinct heap blocks, apart from reference results')


ORACLE = bytes.fromhex('0fbe442404' 'c1e81f' 'c3')     # movsx eax, byte [esp+4]; shr eax, 31; ret
ARM_OK = '\tsxtb w0, w0\n\tlsr w0, w0, #31\n\tret\n'
ARM_BUG = '\tand w0, w0, #0xff\n\tlsr w0, w0, #31\n\tret\n'


def build_image(body, d):
    s = os.path.join(d, 'f.s')
    open(s, 'w').write('\t.text\n\t.globl __fnv_00401000\n\t.type __fnv_00401000, %function\n__fnv_00401000:\n'
                       + body)
    o, elf, ld = os.path.join(d, 'f.o'), os.path.join(d, 'f.elf'), os.path.join(d, 'layout.ld')
    open(ld, 'w').write(a64image.LAYOUT % a64image.A64_BASE)
    subprocess.run(['aarch64-linux-gnu-as', '-mabi=ilp32', s, '-o', o], check=True)
    subprocess.run(['aarch64-linux-gnu-ld', '-m', 'aarch64linux32', '-static', '-e', '0', '-T', ld, '-o', elf, o],
                   check=True, capture_output=True)
    return a64image.Image(elf, {}, {})


def fuzz(pair, n=300):
    import random
    r = random.Random(1)
    for i in range(n):
        case = world.Case(seed=i, args={'p0': bytes([r.getrandbits(8)])})
        _, _, d = pair.run(case)
        if d:
            return case, d
    return None, None


def test_end_to_end():
    cv = conv.Conv('?f@@YAHD@Z', 'test')      # int __cdecl f(char)
    check([v.cls for v in cv.vals] == ['I8'] and cv.x86_ret == 'eax', 'convention of int f(char)')
    with tempfile.TemporaryDirectory() as d:
        for body, want in ((ARM_OK, False), (ARM_BUG, True)):
            img = build_image(body, d)
            t = runner.Target(cv, 0x00401000, {0x00401000: ORACLE}, img, {})
            pair = runner.Pair(t, {})
            case, div = fuzz(pair)
            if want:
                check(div is not None and div['kind'] == 'return' and case.args['p0'][0] >= 0x80,
                      'planted unsigned-char bug found (input %s)' % (case.args['p0'].hex() if case else None))
            else:
                check(div is None, 'correct native version: no divergence in 300 runs')


if __name__ == '__main__':
    test_pure()
    test_end_to_end()
    print('%d failures' % len(FAIL))
    sys.exit(1 if FAIL else 0)
