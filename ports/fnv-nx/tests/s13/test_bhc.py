#!/usr/bin/env python3
"""tests/s13/test_bhc.py - the bh_C round (farm baseline 588863, Boot + Hot rows left unclassified), on hand-written code
(no decomp content).

  fclose     libcalls.EQUIV: the platform's lock-free close (`_fclose_nolock` in the decomp's source) stands for the exe's
             _fclose 0x00ec9907 at both of BSFile's call sites (0x00aff443, 0x00affd3c)
  d0         vtmodel.deleting_d0: a synthesized vtable that binds the D1 slot to the exe deleting destructor's D1 stub
             and leaves the D0 slot to the compiler's weak D0: that D0 stands for the same exe function
  popecx     runner._learn_pop: `call [..]; pop ecx; ret` closes a frame (a stdcall callee is possible), it is no cdecl
             cleanup of one argument (0x00aa13e0)
  vslotent   runner.vslot_entered: the oracle ran the exe's slot function as code (no call), the native entered another
             function of its unit through the same slot of its vtable copy (0x00b004e0, call-count)
  family     libcalls.FAMILY: MemoryManager::Deallocate (0x00aa4060) is the family of the native operator delete
             (0x00af67d0)
  derefcopy  r5design.deref_after_call follows `mov esi, eax` and the callee-saved copy across a later call (0x00885560)
  unplanted  classify.native_unplanted_vcall: the native calls a null word of an unplanted table where the oracle calls a
             wild word of the same garbage, one slot lower (0x0078a3e0): native-vcall-unplanted, no null-read
  denormal   x86emu X87_CLAMP 'f32den': a float product in the denormal range keeps all its bits on the x87 stack; the
             native rounds it to the 2**-149 grid (0x00f92520); the float store is classified by the x87 probes

Needs unicorn, capstone, aarch64-linux-gnu-as/ld.
"""
import os
import struct
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13bhc'))

import conv       # noqa: E402
import engine     # noqa: E402
import libcalls   # noqa: E402
import r5design   # noqa: E402
import runner     # noqa: E402
import vtmodel    # noqa: E402
import world      # noqa: E402
from test_d14 import build_image, stub  # noqa: E402
from test_float import run_hint, f32  # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def test_fclose():
    fake = types.SimpleNamespace(_refs=lambda src: {}, _import_names=lambda: {}, providers=lambda: {})
    image = types.SimpleNamespace(stubs={0x097f0000: 'fnvnx_iob_fclose_nolock', 0x097f0010: 'fnvnx_unknown_name'})
    got = engine.UnitCache.stub_addrmap(fake, image, 'x.cpp')
    check(got.get(0x097f0000) == 0x00ec9907, 'fnvnx_iob_fclose_nolock stands for 0x00ec9907 (%s)' % got)
    check(0x097f0010 not in got, 'an unknown runtime name stays unnamed')


def test_d0():
    got = vtmodel.deleting_d0(['__fnv_00afc680_D1', '_ZN1AD0Ev', '__fnv_00be0be0'])
    check(got == {'_ZN1AD0Ev': 0x00afc680}, 'D0 after the exe deleting destructor\'s D1 stub: %s' % got)
    check(vtmodel.deleting_d0(['__fnv_00afc680', '_ZN1AD0Ev']) == {}, 'no D1 stub before it: not paired')
    check(vtmodel.deleting_d0(['__fnv_00afc680_D1', '_ZN1A3fooEv']) == {}, 'only a D0 symbol is paired')
    check(vtmodel.deleting_d0([0, 0x1000]) == {}, 'numbers are no names')


ORACLE_POP = bytes.fromhex('56'                  # push esi
                           '8b742408'            # mov esi, [esp+8]
                           '51'                  # push ecx
                           '56' '56' 'ff16'      # push esi; push esi; call [esi]     (stdcall, pops 8)
                           '59'                  # pop ecx
                           '5e'                  # pop esi
                           'c20400')             # ret 4
NATIVE_POP = ('\tstp x29, x30, [sp, #-32]!\n\tstr x19, [sp, #16]\n\tmov w19, w0\n'
              '\tldr w8, [x19]\n\tmov w1, w19\n\tblr x8\n'
              '\tldr x19, [sp, #16]\n\tldp x29, x30, [sp], #32\n\tret\n')


def test_popecx():
    with tempfile.TemporaryDirectory() as d:
        cv = conv.Conv('?f@@YGXPAPAX@Z', 'test')
        p = runner.Pair(runner.Target(cv, 0x00401000, {0x00401000: ORACLE_POP}, build_image(NATIVE_POP, d), {}), {})
        c = world.Case(seed=9, args={}, mem={world.BUF: struct.pack('<I', 0x41414140)})
        rx, ra, div = p.run(c)
        check(not (rx.error or '').startswith('fault:') and rx.esp_ok,
              'the run is clean and balanced (%s, esp_ok %s)' % (rx.error, rx.esp_ok))
        check(sorted(p.pops.values()) == [8], 'learned pop 8: %s' % p.pops)
        check(div is None, 'then both machines agree (%s)' % (div and div['kind']))


def test_vslot_entered():
    exe_slots = [0x00400000 + 16 * k for k in range(8)] + [0x00afc060, 0x00aff2a0]
    native = [0x0a000000 + 16 * k for k in range(9)] + [0x0a000100]       # native slot 9 = exe slot 8
    ent = {0x0a000100: 0x00aff300, 0x0a000200: 0x00b004e0}
    vt_mem = {0x0bc00000: struct.pack('<2I', 0, 0) + struct.pack('<%dI' % len(native), *native)}
    tgt = types.SimpleNamespace(image=types.SimpleNamespace(entries=ent), orig_addr=0x00b004e0,
                                vt_pins={0x010a464c: struct.pack('<%dI' % len(exe_slots), *exe_slots)},
                                vt_ap={0x010a464c: 0x0bc00008}, vt_mem=vt_mem)
    me = types.SimpleNamespace(target=tgt)
    case = world.Case(seed=1, args={}, mem={})
    case.vptrs = {0x03000000: 0x010a464c}
    # native slot 9 holds ent[0x0a000100] = 0x00aff300 (entered); exe slot 8 (0x00afc060) ran as code on the oracle
    got = runner.Pair.vslot_entered(me, case, types.SimpleNamespace(blocks={0x00afc060}),
                                    types.SimpleNamespace(blocks={0x0a000100, 0x0a000200}))
    check(got is not None and got['native_slot'] == 9 and got['exe_slot'] == 8 and got['native'] == '00aff300' and
          got['exe'] == '00afc060', 'the native vtable holds another function than the exe at the slot: %s' % got)
    got = runner.Pair.vslot_entered(me, case, types.SimpleNamespace(blocks=set()),
                                    types.SimpleNamespace(blocks={0x0a000100, 0x0a000200}))
    check(got is None, 'the oracle did not run the exe slot: no verdict')
    got = runner.Pair.vslot_entered(me, case, types.SimpleNamespace(blocks={0x00afc060}),
                                    types.SimpleNamespace(blocks={0x0a000200}))
    check(got is None, 'the native did not enter a slot function: no verdict')


def test_family():
    check(libcalls.FAMILY.get(0x00aa4060) == libcalls.FAMILY.get(0x00401030) == 'delete',
          'MemoryManager::Deallocate is a delete')
    # int-free function: the oracle calls MemoryManager::Deallocate(p) (thiscall, this = g_memoryManager), the native
    # operator delete: the same family, so the calls pair (before: call-target 0x00aa4060 against 0x00401030)
    oracle = bytes.fromhex('b938621f01'          # mov ecx, 0x011f6238
                           'ff742404'            # push dword [esp+4]
                           'e8') + struct.pack('<i', 0x00aa4060 - (0x00401000 + 11)) + bytes.fromhex('c3')
    native = '\tstp x29, x30, [sp, #-16]!\n\tmov x29, sp\n\tbl __fnv_00401030\n\tldp x29, x30, [sp], #16\n\tret\n'
    with tempfile.TemporaryDirectory() as d:
        cv = conv.Conv('?f@@YAXPAX@Z', 'test')
        img = build_image(native, d, [stub(0x00401030)])
        t = runner.Target(cv, 0x00401000, {0x00401000: oracle}, img, {})
        p = runner.Pair(t, {})
        c = world.Case(seed=2, args={'p0': (world.BUF).to_bytes(4, 'little')}, mem={world.BUF: bytes(16)})
        rx, ra, div = p.run(c)
        check(div is None, 'the oracle\'s Deallocate and the native delete pair (%s)' % (div and div.get('kind')))


def test_derefcopy():
    base = 0x1000
    # call edx; mov esi, eax; lea eax,[esp+4]; push eax; call edx; fld dword [esi+8]; ret
    code = bytes.fromhex('ffd2' '89c6' '8d442404' '50' 'ffd2' 'd94608' 'c3')
    first, second = base + 2, base + 2 + 2 + 4 + 1 + 2
    check(r5design.deref_after_call(code, base, first), 'a load through the copy esi, across the next call')
    check(r5design.deref_after_call(code, base, first, 8), 'at the displacement the fault shows')
    check(not r5design.deref_after_call(code, base, first, 12), 'not at another displacement')
    check(not r5design.deref_after_call(code, base, second), 'the second call\'s result is not loaded: eax is gone')
    plain = bytes.fromhex('ffd2' 'd86008' 'c3')                       # call edx; fsub dword [eax+8]
    check(r5design.deref_after_call(plain, base, base + 2, 8), 'the direct load through eax still counts')
    lost = bytes.fromhex('ffd2' '89c6' 'be00000000' 'd94608' 'c3')   # mov esi, eax; mov esi, 0; fld [esi+8]
    check(not r5design.deref_after_call(lost, base, base + 2), 'a copy overwritten before the load does not count')
    used = bytes.fromhex('ffd2' '89c6' '85f6' 'd94608' 'c3')          # test esi, esi between copy and load
    check(not r5design.deref_after_call(used, base, base + 2), 'a test of the copy (a null check) does not count')


# void f(Obj* p): p->vptr[8]() on the oracle (call [edx + 0x20]), p->vptr[9]() natively (the Itanium destructor pair): the
# table is case memory with no vptr planting, a wild word at +0x20, 0 at +0x24
UNPL_X86 = bytes.fromhex('8b442404' '8b10' '89c1' 'ff5220' 'c3')       # mov eax,[esp+4]; mov edx,[eax]; mov ecx,eax; call [edx+0x20]; ret
UNPL_A64 = '\tstp x29, x30, [sp, #-16]!\n\tmov x29, sp\n\tldr w1, [x0]\n\tldr w1, [x1, #0x24]\n\tblr x1\n\tldp x29, x30, [sp], #16\n\tret\n'


def test_unplanted():
    mem = bytearray(0x80)
    mem[0:4] = struct.pack('<I', world.BUF + 0x40)
    mem[0x60:0x64] = struct.pack('<I', world.BUF + 0x500)
    div, h = run_hint('?f@@YAXPAX@Z', UNPL_X86, UNPL_A64, {'p0': (world.BUF).to_bytes(4, 'little')}, {world.BUF: bytes(mem)})
    check(div is not None and div['kind'] == 'error' and div.get('arm') == 'fault:x:00000000' and div.get('x86') is None,
          'the native fetches from 0, the oracle calls on (%s)' % (div,))
    check(h.get('cause') == 'native-vcall-unplanted', 'native-vcall-unplanted (%s)' % h.get('cause'))
    # an oracle slot that holds an exe function (a vftable of an object the exe built) is a real slot, not garbage
    mem2 = bytearray(mem)
    mem2[0x60:0x64] = struct.pack('<I', 0x00500000)
    div, h = run_hint('?f@@YAXPAX@Z', UNPL_X86, UNPL_A64, {'p0': (world.BUF).to_bytes(4, 'little')}, {world.BUF: bytes(mem2)})
    check(div is not None and h.get('cause') != 'native-vcall-unplanted', 'an exe function in the slot: not this cause (%s)'
          % h.get('cause'))
    # the oracle stored a vftable of the image in this run (a constructor it ran): its objects have real vptrs, the
    # native's table lacks the slot - a defect, not a garbage table (0x00551180)
    import classify
    saved = classify.image_vftables
    classify.image_vftables = lambda: {0x01000000}
    try:
        stor = UNPL_X86.replace(bytes.fromhex('8b10'), bytes.fromhex('c7407000000001' '8b10'))
        div, h = run_hint('?f@@YAXPAX@Z', stor, UNPL_A64, {'p0': (world.BUF).to_bytes(4, 'little')}, {world.BUF: bytes(mem)})
        check(div is not None and h.get('cause') != 'native-vcall-unplanted',
              'the oracle built an object with a vftable: not this cause (%s)' % h.get('cause'))
        div, h = run_hint('?f@@YAXPAX@Z', UNPL_X86, UNPL_A64, {'p0': (world.BUF).to_bytes(4, 'little')}, {world.BUF: bytes(mem)})
        check(h.get('cause') == 'native-vcall-unplanted', 'without that store it is (%s)' % h.get('cause'))
    finally:
        classify.image_vftables = saved
    # the same fault where the oracle makes no call at that point stays a null read
    div, h = run_hint('?f@@YAXPAX@Z', UNPL_X86.replace(bytes.fromhex('ff5220'), bytes.fromhex('909090')), UNPL_A64,
                      {'p0': (world.BUF).to_bytes(4, 'little')}, {world.BUF: bytes(mem)})
    check(div is not None and h.get('cause') != 'native-vcall-unplanted', 'no oracle call: not this cause (%s)' % h.get('cause'))


# void f(float a, float b, float y, float* out): *out = (float)((8.0 / (double)(a * b)) * y); the x87 code keeps a * b
# unrounded (a denormal float natively), and 8 / (a * b) is beyond the float range there too
DEN_X86 = bytes.fromhex('d9e8' 'd8c0' 'd8c0' 'd8c0'          # fld1; fadd st0, st0 x3   (8.0)
                        'd9442404' 'd84c2408' 'def9'           # fld [a]; fmul [b]; fdivp st1, st0
                        'd84c240c'                             # fmul [y]
                        '8b442410' 'd918' 'c3')                # mov eax, [out]; fstp dword [eax]; ret
DEN_A64 = ('\tfmul s0, s0, s1\n\tfcvt d0, s0\n\tfmov d3, #8.0\n\tfdiv d3, d3, d0\n\tfcvt d2, s2\n\tfmul d3, d3, d2\n'
           '\tfcvt s0, d3\n\tstr s0, [x0]\n\tret\n')


def test_denormal():
    a, b = struct.unpack('<f', bytes.fromhex('9fcab804'))[0], struct.unpack('<f', bytes.fromhex('ab71f5b4'))[0]
    y = struct.unpack('<f', bytes.fromhex('00008203'))[0]
    mem = {world.BUF: bytes(16)}
    args = {'p0': bytes.fromhex(f32(a)), 'p1': bytes.fromhex(f32(b)), 'p2': bytes.fromhex(f32(y)),
            'p3': (world.BUF).to_bytes(4, 'little')}
    div, h = run_hint('?f@@YAXMMMPAM@Z', DEN_X86, DEN_A64, args, mem)
    check(div is not None and div['kind'] == 'mem-write' and div.get('float_store'),
          'the stored floats differ beyond the tolerance (%s)' % (div and sorted(div)))
    check(h.get('cause') == 'x87-range' and h.get('x87_range', {}).get('range') == 'f32den',
          'x87-range with the denormal rounding only (%s / %s)' % (h.get('cause'), h.get('x87_range')))
    # the native computes something else (8.5): no denormal rounding makes the machines agree
    div, h = run_hint('?f@@YAXMMMPAM@Z', DEN_X86, DEN_A64.replace('#8.0', '#8.5'), args, mem)
    check(div is not None and h.get('cause') != 'x87-range', 'another native value stays unexplained (%s)' % h.get('cause'))
    check(world.X87_CW == 0x027f and world.X87_CLAMP is None, 'the control word and the clamp are restored')


if __name__ == '__main__':
    test_fclose()
    test_d0()
    test_popecx()
    test_vslot_entered()
    test_family()
    test_derefcopy()
    test_unplanted()
    test_denormal()
    print('%d failures' % len(FAIL))
    sys.exit(1 if FAIL else 0)
