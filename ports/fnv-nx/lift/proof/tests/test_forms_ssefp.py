#!/usr/bin/env python3
"""SSE FP arithmetic forms (proof/forms_ssefp.py) against an emulated x86 (unicorn): add sub mul div sqrt, ss/sd/ps/pd, register and memory source, MXCSR rounding modes, on operands whose
results are outside the tiny range (tininess is checked by fpc_host.py against the z3 model; the emulator's softfloat is not the reference for it).  Compared: every xmm register (unicorn's softfloat is the value reference; it neither updates the sticky MXCSR flags nor
raises #XM, so the flags and faults are checked by directed SDM cases below and by fpc_host.py against the z3 model).   test_forms_ssefp.py  (exit 0 = all pass)"""
import os, random, struct, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import capstone as CS, z3
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UcError
from unicorn.x86_const import *
from proof import forms
from fnvlift import dsl
md = CS.Cs(CS.CS_ARCH_X86, CS.CS_MODE_32); md.detail = True
OP = {'add': 0x58, 'mul': 0x59, 'sub': 0x5c, 'div': 0x5e, 'sqrt': 0x51, 'min': 0x5d, 'max': 0x5f}
PFX = {'ss': b'\xf3', 'sd': b'\xf2', 'ps': b'', 'pd': b'\x66'}
def rnd_operand(r, w):
    if r.random() < 0.15: return r.choice([0, 1 << (w - 1), 0x7f800000 if w == 32 else 0x7ff0000000000000, 0xff800000 if w == 32 else 0xfff0000000000000, 0x7fc00000 if w == 32 else 0x7ff8000000000000, 0x3f800000 if w == 32 else 0x3ff0000000000000])
    fb = 23 if w == 32 else 52; eb = 8 if w == 32 else 11; bias = (1 << (eb - 1)) - 1
    e = r.randrange(max(1, bias - 40), min((1 << eb) - 2, bias + 40))      # normal operands near 1: results stay normal (no tiny / overflow region)
    return r.getrandbits(1) << (w - 1) | e << fb | r.getrandbits(fb)
def run_emu(code, v, mem, mx):
    mu = Uc(UC_ARCH_X86, UC_MODE_32); mu.mem_map(0x400000, 0x1000); mu.mem_map(0x10000, 0x1000); mu.mem_write(0x400000, code); mu.mem_write(0x10000, mem); mu.reg_write(UC_X86_REG_EBX, 0x10000)
    for i in range(8): mu.reg_write(UC_X86_REG_XMM0 + i, v[i])
    mu.reg_write(UC_X86_REG_MXCSR, mx)
    try: mu.emu_start(0x400000, 0x400000 + len(code)); fault = False
    except UcError: fault = True
    g = lambda x: x if isinstance(x, int) else int.from_bytes(x, 'little')
    return [g(mu.reg_read(UC_X86_REG_XMM0 + i)) for i in range(8)], g(mu.reg_read(UC_X86_REG_MXCSR)), fault
FAILS = []; N = 0
def cmp_and_comi(r):
    """CMPccPS/SS/PD/SD (imm8 predicate 0-7, capstone names it in the mnemonic) and COMISS/UCOMISS/COMISD/UCOMISD (ZF PF CF compared with the emulator's EFLAGS) on special-heavy operands; values only (no #XM, no sticky flags in unicorn)."""
    global N
    pool = lambda w: [0, 1 << (w - 1), 1, (1 << (w - 1)) | 1, 0x7f800000 if w == 32 else 0x7ff0000000000000, 0xff800000 if w == 32 else 0xfff0000000000000, 0x7fc00000 if w == 32 else 0x7ff8000000000000, 0x3f800000 if w == 32 else 0x3ff0000000000000, 0xbf800000 if w == 32 else 0xbff0000000000000, 0x40000000 if w == 32 else 0x4000000000000000]
    cases = []
    for sfx, w, packed in (('ss', 32, False), ('sd', 64, False), ('ps', 32, True), ('pd', 64, True)):
        for pred in range(8):
            for modrm in (0xc8, 0x0b): cases.append(('cmp', sfx, w, packed, PFX[sfx] + bytes([0x0f, 0xc2, modrm, pred])))
    for sfx, w in (('ss', 32), ('sd', 64)):
        for opc in (0x2e, 0x2f):
            for modrm in (0xc8, 0x0b): cases.append(('comi', sfx, w, False, PFX[sfx if sfx == 'sd' else 'ps'].replace(b'\xf2', b'\x66') + bytes([0x0f, opc, modrm])))
    for kind, sfx, w, packed, code in cases:
        ins = next(md.disasm(code, 0x400000)); F = forms.build(ins, 0x400000); P = F.post()
        for _ in range(40):
            N += 1
            lane = lambda: sum(r.choice(pool(w) + [rnd_operand(r, w)]) << (w * k) for k in range(128 // w))
            v = [lane() for _ in range(8)]; mem = lane().to_bytes(16, 'little') + bytes(16)
            mx = 0x1f80 | r.randrange(4) << 13 | (r.random() < 0.3) << 6
            mu = Uc(UC_ARCH_X86, UC_MODE_32); mu.mem_map(0x400000, 0x1000); mu.mem_map(0x10000, 0x1000); mu.mem_write(0x400000, code); mu.mem_write(0x10000, mem); mu.reg_write(UC_X86_REG_EBX, 0x10000)
            for i in range(8): mu.reg_write(UC_X86_REG_XMM0 + i, v[i])
            mu.reg_write(UC_X86_REG_MXCSR, mx); mu.reg_write(UC_X86_REG_EFLAGS, 0x202)
            mu.emu_start(0x400000, 0x400000 + len(code))
            g = lambda x: x if isinstance(x, int) else int.from_bytes(x, 'little')
            ev = [g(mu.reg_read(UC_X86_REG_XMM0 + i)) for i in range(8)]; ef = mu.reg_read(UC_X86_REG_EFLAGS)
            mm = dsl.mem_array(); mmv = mm
            for k, b in enumerate(mem): mmv = z3.Store(mmv, z3.BitVecVal(0x10000 + k, 32), z3.BitVecVal(b, 8))
            sub = [(z3.BitVec('ebx', 32), z3.BitVecVal(0x10000, 32)), (z3.BitVec('mxcsr', 32), z3.BitVecVal(mx, 32)), (mm, mmv)] + [(z3.BitVec('xmm%d' % i, 128), z3.BitVecVal(v[i], 128)) for i in range(8)] + [(z3.BitVec(f, 1), z3.BitVecVal(0, 1)) for f in ('cf', 'pf', 'af', 'zf', 'sf', 'of')]
            ev_ = lambda e: z3.simplify(z3.substitute(e, *sub)).as_long()
            if ev_(P['fault']) != 0: continue
            if kind == 'cmp':
                got = [ev_(P['xmm'][i]) for i in range(8)]
                if got != ev: FAILS.append((ins.mnemonic, [hex(x) for x in got[:2]], [hex(x) for x in ev[:2]], [hex(x) for x in v[:2]], mem[:16].hex())); break
            else:
                fl = P['flags']; gf = {f: ev_(fl[f]) for f in ('cf', 'pf', 'af', 'zf', 'sf', 'of')}
                ex = {'cf': ef & 1, 'pf': ef >> 2 & 1, 'af': ef >> 4 & 1, 'zf': ef >> 6 & 1, 'sf': ef >> 7 & 1, 'of': ef >> 11 & 1}
                if gf != ex: FAILS.append((ins.mnemonic, gf, ex, [hex(x) for x in v[:2]], mem[:16].hex())); break
def main():
    global N
    r = random.Random(11)
    for op, opc in OP.items():
        for sfx, w, packed in (('ss', 32, False), ('sd', 64, False), ('ps', 32, True), ('pd', 64, True)):
            lanes = 128 // w if packed else 1
            for form, modrm in (('r', 0xc8), ('m', 0x0b)):
                code = PFX[sfx] + bytes([0x0f, opc, modrm]); ins = next(md.disasm(code, 0x400000)); F = forms.build(ins, 0x400000); P = F.post()
                for _ in range(60):
                    N += 1
                    v = [sum(rnd_operand(r, w) << (w * k) for k in range(128 // w)) for _ in range(8)]
                    if op == 'sqrt': v = [x & ~sum(1 << (w - 1 + w * k) for k in range(128 // w)) for x in v]      # keep sqrt operands non-negative mostly; negative ones come from the special pool
                    mem = b''.join(x.to_bytes(16, 'little') for x in (sum(rnd_operand(r, w) << (w * k) for k in range(128 // w)), 0))
                    rc = r.randrange(4); um = r.random() < 0.8; mx = 0x1f80 & ~(3 << 13) | rc << 13
                    if not um: mx &= ~(0x3f << 7) | (r.getrandbits(6) << 7)       # some exception masks clear: unmasked exceptions
                    mx = (mx & ~(0x3f << 7)) | ((0x3f << 7) if um else (r.getrandbits(6) << 7))
                    ev, emx, efault = run_emu(code, v, mem, mx)
                    mm = dsl.mem_array(); mmv = mm
                    for k, b in enumerate(mem): mmv = z3.Store(mmv, z3.BitVecVal(0x10000 + k, 32), z3.BitVecVal(b, 8))
                    sub = [(z3.BitVec('ebx', 32), z3.BitVecVal(0x10000, 32)), (z3.BitVec('mxcsr', 32), z3.BitVecVal(mx, 32)), (mm, mmv)] + [(z3.BitVec('xmm%d' % i, 128), z3.BitVecVal(v[i], 128)) for i in range(8)]
                    ev_ = lambda e: z3.simplify(z3.substitute(e, *sub)).as_long()
                    if ev_(P['fault']) != 0: continue          # an unmasked exception: the emulator has no #XM; the Form faults, nothing is compared
                    got = [ev_(P['xmm'][i]) for i in range(8)]; gmx = ev_(P['mxcsr'])
                    if got != ev: FAILS.append((op + sfx, form, [hex(x) for x in got[:2]], [hex(x) for x in ev[:2]], hex(gmx), hex(emx), hex(mx), [hex(x) for x in v[:2]], mem.hex())); break
    cmp_and_comi(r)
    # directed SDM cases (flags and faults): divss xmm1, xmm0 with 1.0 / 0.0
    code = b'\xf3\x0f\x5e\xc8'; ins = next(md.disasm(code, 0x400000)); P = forms.build(ins, 0x400000).post()
    def at(mx, a, b):
        sub = [(z3.BitVec('mxcsr', 32), z3.BitVecVal(mx, 32)), (z3.BitVec('xmm1', 128), z3.BitVecVal(a, 128)), (z3.BitVec('xmm0', 128), z3.BitVecVal(b, 128))]
        e = lambda x: z3.simplify(z3.substitute(x, *sub)).as_long(); return e(P['fault']), e(P['mxcsr']), e(P['xmm'][1])
    FZ = {n: __import__('fnvlift.faults', fromlist=['x']).BYNAME[n]['code'] for n in ('FLT_ZE', 'FLT_PE', 'FLT_IE')}
    one, zero = 0x3f800000, 0
    f, m, x = at(0x1f80, one, zero); ok = f == 0 and m == 0x1f84 and x == 0x7f800000
    FAILS.extend([] if ok else [('divss 1/0 masked', f, hex(m), hex(x))])
    f, m, x = at(0x1f80 & ~(1 << 9), one, zero); FAILS.extend([] if f == FZ['FLT_ZE'] else [('divss 1/0 unmasked ZM', f)])
    f, m, x = at(0x1f80, 0x3f800000, 0x40400000); ok = f == 0 and m == 0x1fa0 and x == 0x3eaaaaab      # 1/3: PE, rounded to nearest even
    FAILS.extend([] if ok else [('divss 1/3', f, hex(m), hex(x))])
    f, m, x = at(0x1f80 & ~(1 << 12), 0x3f800000, 0x40400000); FAILS.extend([] if f == FZ['FLT_PE'] else [('divss 1/3 unmasked PM', f)])
    f, m, x = at(0x1f80 & ~(1 << 7), 0x7fa00000, one); FAILS.extend([] if f == FZ['FLT_IE'] else [('divss SNaN unmasked IM', f)])
    print('cases', N, 'fails', len(FAILS))
    for f in FAILS: print('FAIL', f)
    return 1 if FAILS else 0
if __name__ == '__main__': sys.exit(main())
