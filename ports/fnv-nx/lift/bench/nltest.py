#!/usr/bin/env python3
"""Unit test of the control-transfer rule on hand-assembled functions (a synthetic image at 0x401000): the paths no real chain of bench/chain.py
takes (a callee returning to another address, a return through two frames, a tail jump through a register, an indirect call, a tail jump).
    nltest.py [--work DIR]      prints one line per case; exit status 1 on any difference to the x86 oracle."""
import argparse, os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.environ.get('FNVL_LIFTER', os.path.dirname(HERE))   # FNVL_LIFTER: a copy of fnvlift/ + rt/ (sanity runs)
sys.path.insert(0, HERE); sys.path.insert(0, LIFT)
import chain, chrun, fnvlift as L

BASE = 0x401000


def asm(parts, at):
    """parts: bytes or ('call'|'jmp', target) -> bytes placed at address `at`"""
    out = b''
    for p in parts:
        if isinstance(p, bytes): out += p
        else: out += (b'\xe8' if p[0] == 'call' else b'\xe9') + struct.pack('<i', p[1] - (at + len(out) + 5))
    return out


class Fake:
    """just enough of fnvlift.Image for lift() and the test world"""
    def __init__(s, code): s.d = code; s.secs = [('.text', BASE, len(code), 0, len(code))]; s.rset = set(); s.taken = []; s.base = BASE
    def bytes(s, a, n): return s.d[a - BASE:a - BASE + n]
    def u32(s, a): return struct.unpack_from('<I', s.d, a - BASE)[0]


MOV = lambda r, v: bytes([0xb8 + r]) + struct.pack('<I', v)
F = {n: BASE + 0x40 * i for i, n in enumerate(('c1', 'c1b', 'c2', 'c2b', 'c2c', 'c3', 'c3b', 'c3c', 'c4', 'c4b', 'c5', 'c5b', 'c6'))}
# each case: a root and callees; (name, parts, owns)
SRC = {
    # callee adds 5 to its return address: returns to the instruction after the `mov eax,0x111` (a label the universe knows: owns)
    'c1': ([MOV(0, 1), ('call', F['c1b']), MOV(0, 0x111), b'\x83\xc0\x02', b'\xc3'], [BASE + 0x40 * 0 + 15]),
    'c1b': ([b'\x83\x04\x24\x05', b'\xc3'], []),
    # return through two frames: c2c discards its return address, so its ret returns to the caller of c2b (c2b sees c->ra != NXT, esp > E: leaves)
    'c2': ([MOV(0, 1), ('call', F['c2b']), MOV(0, 2), b'\xc3'], []),
    'c2b': ([('call', F['c2c']), MOV(0, 3), b'\xc3'], []),
    'c2c': ([b'\x83\xc4\x04', b'\xc3'], []),
    # c3: root calls c3b which tail-jumps to c3c (direct jmp, esp == E), c3c returns to c3
    'c3': ([('call', F['c3b']), b'\x40', b'\xc3'], []),
    'c3b': ([MOV(2, 9), ('jmp', F['c3c'])], []),
    'c3c': ([MOV(0, 7), b'\xc3'], []),
    # c4: indirect jmp through a register to an entry (nonlocal loop: not an own label -> dispatcher), then an indirect call
    'c4': ([MOV(2, F['c4b']), b'\xff\xe2'], []),
    'c4b': ([MOV(2, F['c3c']), b'\xff\xd2', b'\x40', b'\xc3'], []),
    # c5: the callee returns to the sentinel (the root's own return address), c6 returns with ret 8
    'c5': ([MOV(0, 5), ('call', F['c5b']), MOV(0, 6), b'\xc3'], []),
    'c5b': ([b'\x83\xc4\x04', b'\xc3'], []),
    'c6': ([b'\x6a\x01', ('call', F['c3c']), b'\x83\xc4\x04', b'\xc2\x08\x00'], []),
}
ROOTS = ['c1', 'c2', 'c3', 'c4', 'c5', 'c6']


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--work', default='/home/user/l3work/chain/nltest'); a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True); a.lifter = LIFT; os.makedirs(a.work, exist_ok=True); code = bytearray(0x40 * len(F) + 0x40); texts, ents = {}, {}
    for n, (parts, owns) in SRC.items():
        b = asm(parts, F[n]); code[F[n] - BASE:F[n] - BASE + len(b)] = b; ents[n] = (F[n], len(b), owns)
    img = Fake(bytes(code)); img.entries = set(F.values()); chrun.T.G['img'] = img
    for n, (ad, sz, owns) in ents.items(): texts[ad] = L.lift(img, ad, sz, owns=owns)[0]
    flags = [f for f in chrun.T.FLAGS if f != '-DFNVL_FLAGS_IO'] + ['-DFNVL_TRACE', '-I', LIFT + '/rt']
    o, err = chain.comp_chunk((L.prelude(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'rt')) + '\n'.join(texts.values()), flags, a.work))
    if not o: sys.exit('compile: ' + err)
    im = chrun.Image(chain.link(a, [o], {ad: 'L_%08x' % ad for ad in texts}, flags)); bad = 0
    for r in ROOTS:
        res = chrun.run_chain(im, F[r], set(F.values()), 3)
        ok = all(v == 'equal' for v, *_ in res); bad += not ok
        print('%s %s %s' % (r, 'equal' if ok else 'DIFF', ' | '.join('%s %s' % (v, d) for v, d, *_ in res if v != 'equal')[:200]))
    sys.exit(1 if bad else 0)


if __name__ == '__main__': main()
