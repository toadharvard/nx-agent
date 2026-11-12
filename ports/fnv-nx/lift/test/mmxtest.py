#!/usr/bin/env python3
"""MMX aliasing x87 (task C4a / B3): multi-instruction sequences x87 -> MMX -> x87 on the lifted code (Unicorn AArch64, the harness of instest.py) against the expectation of the SDM.
    mmxtest.py [--n 24]
SDM Vol 1 9.2 / 12.2: MM0-MM7 are the significands of the PHYSICAL x87 registers R0-R7; an MMX instruction (not EMMS) sets TOP = 0 and the whole tag word to valid; a write of MMi sets the
exponent|sign field of Ri to all ones; EMMS sets all tags empty (contents unchanged); a register popped by an x87 instruction keeps its bits (MMX reads them).
The expected values are computed here from the SDM with the exact 80-bit images of instest.py (f2x80), not by the oracle: Unicorn (QEMU) keeps the old exponent on an MMX write and cannot judge the
exponent field, so the single-instruction rows of instest.py compare significands, TOP and tags against the oracle and the exponent against the SDM (mm_written). Initial memory is the harness's
deterministic page generator; stores are read back as `changed bytes or the original byte`."""
import os, sys, struct
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import instest as T
import instest_extra as XE
import fnvlift
WORK = '/home/user/l3work/mmxtest'
SRC, DST, TMP = 0x00200000, 0x00200100, 0x00200200            # esi, edi, ebx
IND = (0xC000000000000000, 0xFFFF)


def mrm(reg, rm):
    return 0xc0 | reg << 3 | rm


# name, bytes, initial TOP, expected function(q(addr)->u64 initial qword at addr, orig(addr)->byte) -> {addr: bytes}
def cases():
    def q(st, a):
        return int.from_bytes(bytes(T.origbyte(st, a + k) for k in range(8)), 'little')
    fld_s = bytes([0xdd, 0x06])                         # fld qword [esi]
    movq_ld = lambda k: bytes([0x0f, 0x6f, 0x06 | k << 3])                       # movq mmk, [esi]
    movq_ld_d = lambda k: bytes([0x0f, 0x6f, 0x07 | k << 3])                    # movq mmk, [edi]
    movq_st = lambda k: bytes([0x0f, 0x7f, 0x07 | k << 3])                       # movq [edi], mmk
    fstp_t = lambda r: bytes([0xdb, 0x38 | r])                                  # fstp tbyte [reg]  (r: 7 = edi, 3 = ebx)
    out = []
    m80 = lambda st, a: T.f2x80(T.QN(q(st, a), 64))
    # A: x87 write, MMX read: fld pushes into R7 (TOP 0 -> 7); mm7 is its 80-bit significand
    out.append(('fld m64; movq [edi],mm7', fld_s + movq_st(7), 0, lambda st: {DST: struct.pack('<Q', m80(st, SRC)[0])}))
    # B: MMX write, x87 read: the register reads back (significand, 0xFFFF)
    out.append(('movq mm0,[esi]; fstp tbyte [edi]', movq_ld(0) + fstp_t(7), 0, lambda st: {DST: struct.pack('<QH', q(st, SRC), 0xffff)}))
    # C: x87 push (R7), MMX write of mm0 sets only R0's exponent; ST(0) is R0 after TOP := 0; mm7 still shows the pushed value
    out.append(('fld m64; movq mm0,[edi]; fstp tbyte [ebx]; movq [edi+16],mm7', fld_s + movq_ld_d(0) + fstp_t(3) + bytes([0x0f, 0x7f, 0x7f, 0x10]), 0,
                lambda st: {TMP: struct.pack('<QH', q(st, DST), 0xffff), DST + 16: struct.pack('<Q', m80(st, SRC)[0])}))
    # D: EMMS empties the tags: fld1 then pushes without a stack fault (TOP stayed 0, R7 empty) and reads back 1.0
    out.append(('movq mm3,[esi]; emms; fld1; fstp tbyte [edi]', movq_ld(3) + bytes([0x0f, 0x77, 0xd9, 0xe8]) + fstp_t(7), 0,
                lambda st: {DST: struct.pack('<QH', 1 << 63, 0x3fff)}))
    # E: without EMMS all tags are valid: fld1 overflows the stack (masked): the real indefinite
    out.append(('movq mm3,[esi]; fld1; fstp tbyte [edi]', movq_ld(3) + bytes([0xd9, 0xe8]) + fstp_t(7), 0, lambda st: {DST: struct.pack('<QH', IND[0], IND[1])}))
    # F: a register popped by x87 keeps its bits: fld; fstp; MMX reads the stale significand
    out.append(('fld m64; fstp m64 [ebx]; movq [edi],mm7', fld_s + bytes([0xdd, 0x1b]) + movq_st(7), 0, lambda st: {DST: struct.pack('<Q', m80(st, SRC)[0])}))
    # G: MMX data survives EMMS (contents unchanged); the next MMX instruction revalidates the tags and reads them back
    out.append(('movq mm5,[esi]; emms; movq [edi],mm5', movq_ld(5) + bytes([0x0f, 0x77]) + movq_st(5), 0, lambda st: {DST: struct.pack('<Q', q(st, SRC))}))
    return out


def run(cases, work=WORK, tag=1000):
    """lift every sequence as one function, run it on the lifted AArch64 code from a deterministic state (esi, edi, ebx = SRC, DST, TMP; an optional 5th tuple element edits the state) and compare the
    bytes it stores with the SDM expectation; -> number of failures"""
    os.makedirs(work, exist_ok=True)
    img = XE.FImage('/home/user/game/FalloutNV.exe')
    from fnvlift import fpfacts
    fpfacts.ASSUME.update(('ASM-204', 'ASM-205'))
    T.G.update(L=fnvlift, img=img, work=work)
    T.runtime_objects(work, os.path.dirname(HERE))
    bad = 0
    for k, case in enumerate(cases):
        name, raw, top, expect = case[:4]
        a = 0x7d000000 + k * 0x100; XE.fake[a] = raw
        ins = next(fnvlift.core.md.disasm(raw, a))
        E = dict(addr=a, raw=raw, ins=ins, base='seq', rep=False, x87=True, mmx=False, sse=False)
        txt = fnvlift.lift(img, a, len(raw), strict=False)[0]
        elf, _ = T.build(tag + k, [(a, txt)], work); arm = T.Arm(elf)
        st = T.gen_state(E, 0, 0); st['wk'] = (1234 + k, 0); st['ftop'] = top; st['valid'] = 0; st['fx'] = [(0, 0, 0)] * 8; st['fcw'] = 0x27f; st['fc'] = 0
        r = st['regs']; r[6], r[7], r[3] = SRC, DST, TMP
        if len(case) > 4: case[4](st)
        T.imgpage = XE.imgpage
        c = arm.run(E, st)
        if 'fault' in c: print('%-60s FAIL %s' % (name, c['fault'])); bad += 1; continue
        ok = True
        for ad, want in sorted(expect(st).items()):
            got = bytes(c['mem'].get(ad + i, T.origbyte(st, ad + i)) for i in range(len(want)))
            if got != want: ok = False; print('   %s: at %x got %s want %s' % (name, ad, got.hex(), want.hex()))
        print('%-60s %s' % (name, 'ok' if ok else 'FAIL')); bad += not ok
    return bad


def main():
    sys.exit(1 if run(cases()) else 0)


if __name__ == '__main__':
    main()
