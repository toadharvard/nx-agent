#!/usr/bin/env python3
"""udcert on synthetic images: the certificate is the pruned reachability of the roots, the excluded edges come from the declared cpuid answer.
    test_udcert.py     (exit 0 = all pass)
Cases (declared answer: 0x80000001 EDX bit 31 = 0 (3DNow cleared), leaf 1 EDX bit 25 = 1 (SSE), max basic leaf 4):
  guard     mov eax,0x80000001; cpuid; test edx,0x80000000; jz over; pi2fd; over: ret            -> the pi2fd is CERT (the fall-through of jz is excluded)
  unguarded mov eax,0x80000001; cpuid; test edx,0x00100000; jz over; pi2fd; over: ret            -> NX (bit 20) is declared 1: jz is never taken, the pi2fd is reachable: U-ROOT
  noleaf    mov eax,[0x1000]; cpuid; test edx,0x80000000; jz over; pi2fd; over: ret              -> the leaf is unknown: nothing is decided: U-ROOT
  flagword  mov eax,1; cpuid; mov [0x2000],edx; ... (the answer laundered through memory)        -> not decided: U-ROOT
  root      the 3DNow code is a root of its own (a relocated pointer)                          -> U-ROOT
  setne     pushal; mov eax,1; cpuid; test edx,0x2000000; popal; setne al; test al,al; jz over; pi2fd; over: ret -> SSE is declared 1: setne is 1, the jz is never taken: U-ROOT"""
import os, struct, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import udcert as U

LIFT = os.path.dirname(os.path.dirname(HERE))
MODEL = os.path.join(LIFT, '..', 'verify', 'cpuid_model.json')
LIB = os.path.join(LIFT, 'fnvlift')
BASE = 0x10000


class FakePe:
    def __init__(s, code): s.code = code
    def read(s, a, n): return s.code[a - BASE: a - BASE + n] if BASE <= a < BASE + len(s.code) else b''


def mov_eax(v): return b'\xb8' + struct.pack('<I', v)
CPUID = b'\x0f\xa2'
PI2FD = b'\x0f\x0f\xc1\x0d'
RET = b'\xc3'


def run(code, extra_roots=()):
    model = U.load_model(MODEL, LIB)
    from fnvlift import f_cpufeat
    rg = {BASE: [(BASE, BASE + len(code))]}
    uni = {BASE: ('entry', '')}
    for r in extra_roots:
        rg[BASE + r] = [(BASE + r, BASE + len(code))]; uni[BASE + r] = ('ptr', 'candidate')
    pe = FakePe(code)
    g = U.Graph(pe, rg, uni, lambda i: bool(f_cpufeat.missing(i)))
    S, inn, dec = U.analyse(g, model)
    cut = {}
    for a, (t, ta) in dec.items():
        i = g.dec(a); n = a + i.size; tg = i.operands[0].imm & U.M32
        cut[(a, n if t else tg)] = 1
    org = U.origin_reach(g, frozenset(cut))
    return g, dec, {a: ('U-ROOT' if a in org else 'CERT') for a in sorted(g.gated)}


def jz(rel): return b'\x74' + bytes([rel])


def main():
    fails = []

    def check(name, cond):
        print('%-10s %s' % (name, 'ok' if cond else 'FAIL'))
        if not cond: fails.append(name)
    t3 = b'\xf7\xc2' + struct.pack('<I', 0x80000000)
    g, dec, st = run(mov_eax(0x80000001) + CPUID + t3 + jz(len(PI2FD)) + PI2FD + RET)
    check('guard', len(g.gated) == 1 and set(st.values()) == {'CERT'} and len(dec) == 1)
    tn = b'\xf7\xc2' + struct.pack('<I', 0x00100000)
    g, dec, st = run(mov_eax(0x80000001) + CPUID + tn + jz(len(PI2FD)) + PI2FD + RET)
    check('unguarded', set(st.values()) == {'U-ROOT'})
    g, dec, st = run(b'\xa1' + struct.pack('<I', 0x1000) + CPUID + t3 + jz(len(PI2FD)) + PI2FD + RET)
    check('noleaf', set(st.values()) == {'U-ROOT'} and not dec)
    g, dec, st = run(mov_eax(0x80000001) + CPUID + b'\x89\x15' + struct.pack('<I', 0x2000) + b'\xf7\xc2' + struct.pack('<I', 0x80000000) + b'\x8b\x15' + struct.pack('<I', 0x2000) + t3 + jz(len(PI2FD)) + PI2FD + RET)
    check('flagword', set(st.values()) == {'U-ROOT'})
    body = mov_eax(0x80000001) + CPUID + t3 + jz(len(PI2FD)) + PI2FD + RET
    g, dec, st = run(body, extra_roots=(len(body) - len(PI2FD) - 1,))
    check('root', set(st.values()) == {'U-ROOT'})
    ts = b'\xf7\xc2' + struct.pack('<I', 0x2000000)
    g, dec, st = run(b'\x60' + mov_eax(1) + CPUID + ts + b'\x61\x0f\x95\xc0\x84\xc0' + jz(len(PI2FD)) + PI2FD + RET)
    check('setne', set(st.values()) == {'U-ROOT'})
    # decisive bits: bit 31 only (the other known bits do not matter for `test edx, 0x80000000`)
    g, dec, st = run(mov_eax(0x80000001) + CPUID + t3 + jz(len(PI2FD)) + PI2FD + RET)
    model = U.load_model(MODEL, LIB)
    S, inn, d = U.analyse(g, model)
    (jcc, (taken, taint)), = d.items()
    pb = U.probe_bits(g, model, S, jcc, taint)
    check('bits', list(pb.values()) == [0x80000000] and taken == 1)
    print('PASS' if not fails else 'FAIL %s' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
