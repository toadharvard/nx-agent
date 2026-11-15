#!/usr/bin/env python3
"""MMX / SSE integer forms (proof/forms_simd.py) against an emulated x86 (unicorn): each encoding is run on random states, once with a register source and once
with a memory source, and every xmm / mm register and the 16 bytes of memory are compared with the Form's z3 post-state.   test_forms_simd.py  (exit 0 = all pass)"""
import os, random, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import capstone as CS, z3
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32
from unicorn.x86_const import *
from proof import forms
from fnvlift import dsl
md = CS.Cs(CS.CS_ARCH_X86, CS.CS_MODE_32); md.detail = True
OPS2 = {'pand': 'db', 'pandn': 'df', 'por': 'eb', 'pxor': 'ef', 'paddb': 'fc', 'paddw': 'fd', 'paddd': 'fe', 'paddq': 'd4', 'psubb': 'f8', 'psubw': 'f9', 'psubd': 'fa', 'psubq': 'fb',
        'paddsb': 'ec', 'paddsw': 'ed', 'paddusb': 'dc', 'paddusw': 'dd', 'psubsb': 'e8', 'psubsw': 'e9', 'psubusb': 'd8', 'psubusw': 'd9',
        'pcmpeqb': '74', 'pcmpeqw': '75', 'pcmpeqd': '76', 'pcmpgtb': '64', 'pcmpgtw': '65', 'pcmpgtd': '66', 'pminub': 'da', 'pmaxub': 'de', 'pminsw': 'ea', 'pmaxsw': 'ee',
        'pavgb': 'e0', 'pavgw': 'e3', 'pmullw': 'd5', 'pmulhw': 'e5', 'pmulhuw': 'e4', 'pmuludq': 'f4', 'pmaddwd': 'f5', 'psadbw': 'f6',
        'punpcklbw': '60', 'punpcklwd': '61', 'punpckldq': '62', 'punpckhbw': '68', 'punpckhwd': '69', 'punpckhdq': '6a', 'packsswb': '63', 'packssdw': '6b', 'packuswb': '67',
        'psllw': 'f1', 'pslld': 'f2', 'psllq': 'f3', 'psrlw': 'd1', 'psrld': 'd2', 'psrlq': 'd3', 'psraw': 'e1', 'psrad': 'e2'}
SSE_ONLY = {'paddq128': None}
def case(hexs, sse, mem):                     # (name, bytes)
    pass
def run_emu(code, st, mem, sse):
    mu = Uc(UC_ARCH_X86, UC_MODE_32); mu.mem_map(0x400000, 0x1000); mu.mem_map(0x10000, 0x1000)
    mu.mem_write(0x400000, code); mu.mem_write(0x10000, mem); mu.reg_write(UC_X86_REG_EBX, 0x10000); mu.reg_write(UC_X86_REG_EAX, st['eax']); mu.reg_write(UC_X86_REG_ECX, st['ecx'])
    for i in range(8):
        if sse: mu.reg_write(UC_X86_REG_XMM0 + i, st['v%d' % i])
        else: mu.reg_write(UC_X86_REG_FP0 + i, (st['v%d' % i] & (2 ** 64 - 1), 0xFFFF))      # unicorn has no MM registers: mmN is the significand of physical register N (TOP = 0)
    mu.emu_start(0x400000, 0x400000 + len(code))
    out = {}
    for i in range(8):
        out['v%d' % i] = (lambda v: v if isinstance(v, int) else int.from_bytes(v, 'little'))(mu.reg_read(UC_X86_REG_XMM0 + i)) if sse else mu.reg_read(UC_X86_REG_FP0 + i)[0]
    out['mem'] = bytes(mu.mem_read(0x10000, 32)); out['eax'] = mu.reg_read(UC_X86_REG_EAX)
    return out
def run_form(code, st, mem, sse):
    ins = next(md.disasm(code, 0x400000)); F = forms.build(ins, 0x400000); P = F.post()
    env = {'eax': (st['eax'], 32), 'ecx': (st['ecx'], 32), 'ebx': (0x10000, 32)}
    sub = [(z3.BitVec(k, w), z3.BitVecVal(v, w)) for k, (v, w) in env.items()]
    for i in range(8):
        if sse: sub.append((z3.BitVec('xmm%d' % i, 128), z3.BitVecVal(st['v%d' % i], 128)))
        else: sub.append((z3.BitVec('fr%d' % i, 80), z3.BitVecVal(0xFFFF << 64 | st['v%d' % i] & (2 ** 64 - 1), 80)))
    ev = lambda e: z3.simplify(z3.substitute(e, *sub)).as_long()
    m = z3.simplify(z3.substitute(P['mem'], *sub))
    out = {'eax': ev(P['regs']['eax'])}
    for i in range(8): out['v%d' % i] = ev(P['xmm'][i]) if sse else ev(P['fr'][i]) & (2 ** 64 - 1)
    mb = bytearray(mem[:32])
    for k in range(32):
        # memory read of the post array is over its address, constants for the initial memory come from the array's default; query by select
        pass
    return out, P, ins, F
FAILS = []; N = 0
def rnd(sse):
    r = random.Random(1); 
    while True:
        yield {'eax': r.getrandbits(32), 'ecx': r.getrandbits(32), **{'v%d' % i: (r.choice([0, r.getrandbits(128), 2 ** 128 - 1, 0x8000000000000000800000008000, 0x7fff7fff7fff7fff7fff7fff7fff7fff])) for i in range(8)}}, bytes(r.getrandbits(8) for _ in range(32))
def check(name, code, sse, gen, n=40):
    global N
    for _ in range(n):
        st, mem = next(gen); N += 1
        ef = run_emu(code, st, mem, sse)
        ins = next(md.disasm(code, 0x400000)); F = forms.build(ins, 0x400000); P = F.post()
        sub = [(z3.BitVec(k, w), z3.BitVecVal(v, w)) for k, (v, w) in {'eax': (st['eax'], 32), 'ecx': (st['ecx'], 32), 'ebx': (0x10000, 32)}.items()]
        for i in range(8):
            if sse: sub.append((z3.BitVec('xmm%d' % i, 128), z3.BitVecVal(st['v%d' % i], 128)))
            else: sub.append((z3.BitVec('fr%d' % i, 80), z3.BitVecVal((0xFFFF << 64) | (st['v%d' % i] & (2 ** 64 - 1)), 80)))
        M0 = dsl.mem_array(); mm = M0
        for k, b in enumerate(mem): mm = z3.Store(mm, z3.BitVecVal(0x10000 + k, 32), z3.BitVecVal(b, 8))
        sub.append((M0, mm))
        ev = lambda e: z3.simplify(z3.substitute(e, *sub)).as_long()
        if ev(P['fault']) != 0:
            FAILS.append((name, 'fault', hex(ev(P['fault'])))); return
        got = {('v%d' % i): (ev(P['xmm'][i]) if sse else ev(P['fr'][i]) & (2 ** 64 - 1)) for i in range(8)}
        got['eax'] = ev(P['regs']['eax'])
        gm = bytes(ev(z3.Select(P['mem'], z3.BitVecVal(0x10000 + k, 32))) for k in range(32))
        bad = [k for k in got if got[k] != ef[k]] + (['mem'] if gm != ef['mem'] else [])
        if bad: FAILS.append((name, code.hex(), bad, {k: hex(got[k]) for k in bad if k != 'mem'}, {k: hex(ef[k]) for k in bad if k != 'mem'})); return
def table():
    t = []
    for sse in (False, True):
        pre = bytes([0x66]) if sse else b''
        for name, op in OPS2.items():
            for modrm in (0xc8, 0x0b): t.append((name + ('/r' if modrm == 0xc8 else '/m'), pre + bytes([0x0f, int(op, 16), modrm]), sse))
        for nm, op, ext in (('psllw', 0x71, 6), ('pslld', 0x72, 6), ('psllq', 0x73, 6), ('psrlw', 0x71, 2), ('psrld', 0x72, 2), ('psrlq', 0x73, 2), ('psraw', 0x71, 4), ('psrad', 0x72, 4)):
            for im in (0, 1, 3, 15, 16, 31, 32, 63, 64, 255): t.append((nm + ' imm%d' % im, pre + bytes([0x0f, op, 0xc0 | (ext << 3) | 1, im]), sse))
        for im in (0x1b, 0xe4, 0x00, 0x55): t.append(('pshuf%d' % im, (pre + bytes([0x0f, 0x70, 0xc9, im])) if sse else bytes([0x0f, 0x70, 0xc8, im]), sse))
        t += [('pextrw', pre + bytes([0x0f, 0xc5, 0xc1, 3 if not sse else 6]), sse), ('pinsrw r', pre + bytes([0x0f, 0xc4, 0xc8, 2]), sse), ('pinsrw m', pre + bytes([0x0f, 0xc4, 0x0b, 1]), sse),
              ('pmovmskb', pre + bytes([0x0f, 0xd7, 0xc1]), sse),
              ('movd r<-', pre + bytes([0x0f, 0x6e, 0xc8]), sse), ('movd m<-', pre + bytes([0x0f, 0x6e, 0x0b]), sse), ('movd ->r', pre + bytes([0x0f, 0x7e, 0xc8]), sse), ('movd ->m', pre + bytes([0x0f, 0x7e, 0x0b]), sse),
              ('movq r,r', pre + bytes([0x0f, 0x6f, 0xc8]) if not sse else bytes([0xf3, 0x0f, 0x7e, 0xc8]), sse), ('movq m->', bytes([0xf3, 0x0f, 0x7e, 0x0b]) if sse else bytes([0x0f, 0x6f, 0x0b]), sse),
              ('movq ->m', (bytes([0x66, 0x0f, 0xd6, 0x0b]) if sse else bytes([0x0f, 0x7f, 0x0b])), sse)]
    t += [('pslldq', bytes([0x66, 0x0f, 0x73, 0xf9, 5]), True), ('psrldq', bytes([0x66, 0x0f, 0x73, 0xd9, 7]), True), ('psrldq16', bytes([0x66, 0x0f, 0x73, 0xd9, 16]), True),
          ('movdqa r,r', bytes([0x66, 0x0f, 0x6f, 0xc8]), True), ('movdqu m->', bytes([0xf3, 0x0f, 0x6f, 0x0b]), True), ('movdqu ->m', bytes([0xf3, 0x0f, 0x7f, 0x0b]), True),
          ('movdqa m->', bytes([0x66, 0x0f, 0x6f, 0x0b]), True), ('movaps r', bytes([0x0f, 0x28, 0xc8]), True), ('movups m->', bytes([0x0f, 0x10, 0x0b]), True), ('movups ->m', bytes([0x0f, 0x11, 0x0b]), True),
          ('andps', bytes([0x0f, 0x54, 0xc8]), True), ('xorps m', bytes([0x0f, 0x57, 0x0b]), True), ('orpd', bytes([0x66, 0x0f, 0x56, 0xc8]), True), ('andnps', bytes([0x0f, 0x55, 0xc8]), True),
          ('movhlps', bytes([0x0f, 0x12, 0xc8]), True), ('movlhps', bytes([0x0f, 0x16, 0xc8]), True), ('movlps ->m', bytes([0x0f, 0x13, 0x0b]), True), ('movlps m->', bytes([0x0f, 0x12, 0x0b]), True),
          ('movhps ->m', bytes([0x0f, 0x17, 0x0b]), True), ('movhps m->', bytes([0x0f, 0x16, 0x0b]), True), ('movss r', bytes([0xf3, 0x0f, 0x10, 0xc8]), True), ('movss m->', bytes([0xf3, 0x0f, 0x10, 0x0b]), True),
          ('movss ->m', bytes([0xf3, 0x0f, 0x11, 0x0b]), True), ('movsd r', bytes([0xf2, 0x0f, 0x10, 0xc8]), True), ('movsd m->', bytes([0xf2, 0x0f, 0x10, 0x0b]), True), ('movsd ->m', bytes([0xf2, 0x0f, 0x11, 0x0b]), True),
          ('shufps', bytes([0x0f, 0xc6, 0xc8, 0x1b]), True), ('shufpd', bytes([0x66, 0x0f, 0xc6, 0xc8, 0x01]), True), ('pshufd m', bytes([0x66, 0x0f, 0x70, 0x0b, 0x4e]), True),
          ('pshuflw', bytes([0xf2, 0x0f, 0x70, 0xc8, 0x1b]), True), ('pshufhw', bytes([0xf3, 0x0f, 0x70, 0xc8, 0x1b]), True),
          ('unpcklps', bytes([0x0f, 0x14, 0xc8]), True), ('unpckhps', bytes([0x0f, 0x15, 0xc8]), True), ('unpcklpd', bytes([0x66, 0x0f, 0x14, 0xc8]), True), ('unpckhpd', bytes([0x66, 0x0f, 0x15, 0xc8]), True),
          ('punpcklqdq', bytes([0x66, 0x0f, 0x6c, 0xc8]), True), ('punpckhqdq', bytes([0x66, 0x0f, 0x6d, 0xc8]), True), ('movmskps', bytes([0x0f, 0x50, 0xc1]), True), ('movmskpd', bytes([0x66, 0x0f, 0x50, 0xc1]), True)]
    return t
def main():
    gen = rnd(True)
    for name, code, sse in table(): check(name, code, sse, gen, 24)
    print('cases', N, 'fails', len(FAILS))
    for f in FAILS: print('FAIL', f)
    return 1 if FAILS else 0
if __name__ == '__main__': sys.exit(main())
