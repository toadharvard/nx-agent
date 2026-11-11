#!/usr/bin/env python3
"""x87 dynamic stack: the cases the QEMU oracle of instest.py cannot judge (it does not model stack faults): push on a full stack, use of an empty slot.
The masked x87 answer is the real indefinite plus IE+SF in the status word; checked on single lifted instructions (dynamic variant, Unicorn AArch64)."""
import os, struct, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); RT = os.path.join(os.path.dirname(HERE), 'rt'); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import instest as T, fnvlift as L
EXE = os.environ.get('FNVNX_EXE', '/home/user/game/FalloutNV.exe'); WORK = '/home/user/l3work/x87dyn'
IND = (0xC000000000000000, 0xFFFF, 6)
def find(img, funcs, pred):
    for a, s in funcs:
        for ins in L.core.explore(img, a, a + s, img.bytes(a, s))[0].values():
            if pred(ins): return ins
def main():
    os.makedirs(WORK, exist_ok=True); img = L.Image(EXE); T.G.update(L=L, img=img, work=WORK)
    funcs = sorted((int(x.split('\t')[0], 16), int(x.split('\t')[1])) for x in open(os.environ.get('FNVNX_FUNCS', '/home/user/l3facts2/funcs.tsv')) if x[0] != '#')
    T.runtime_objects(WORK, os.path.dirname(HERE))      # rt.o fmath.o rcptab.o x87.o hostticks.o cpumodel.o: the one build of the runtime objects every test link needs (instest.py)
    mem = lambda b0, g: lambda i: i.opcode[0] == b0 and i.modrm < 0xc0 and i.modrm >> 3 & 7 == g
    # (name, instruction, depth, TOP, expected TOP after, expected ST0 carrier or None, expected 8 stored bytes or None)
    cases = [('fld m32, full stack', mem(0xd9, 0), 8, 0, 7, IND, None), ('fstp m64, empty stack', mem(0xdd, 3), 0, 0, 1, None, 0xFFF8000000000000),
             ('fadd st0,st1, one slot', lambda i: bytes(i.bytes) == b'\xd8\xc1', 1, 3, 3, IND, None), ('fld st(0), empty', lambda i: bytes(i.bytes) == b'\xd9\xc0', 0, 0, 7, IND, None)]
    bad = 0
    for name, pred, d, top, etop, est0, estore in cases:
        ins = find(img, funcs, pred); raw = bytes(ins.bytes)
        E = dict(addr=ins.address, raw=raw, ins=ins, base=ins.mnemonic, rep=False, x87=True, mmx=False, sse=False)
        elf, _ = T.build(1, [(ins.address, L.lift(img, ins.address, len(raw), strict=False, x87='dyn')[0])], WORK); arm = T.Arm(elf)
        st = T.gen_state(E, 0, 0); st['fcw'] = 0x27f; st['ftop'] = top; st['valid'] = sum(1 << ((top + k) & 7) for k in range(d))
        st['fx'] = [(1, 0x3ff0000000000000, 0) if (p - top) & 7 < d else (0, 0, 0) for p in range(8)]
        c = arm.run(E, st); fex = struct.unpack('<I', bytes(arm.uc.mem_read(T.CPU + 252, 4)))[0]; fp = c['fp']; got0 = fp['st'][fp['top']][::-1][:3]
        got0 = (fp['st'][fp['top']][0], fp['st'][fp['top']][1], fp['st'][fp['top']][2])
        ok = fex & 0x41 == 0x41 and fp['top'] == etop and (est0 is None or T.img80(got0[2], got0[0], got0[1]) == T.img80(est0[2], est0[0], est0[1]))      # compared as 80-bit images: F64 -NaN is the real indefinite
        if estore is not None: ok = ok and int.from_bytes(bytes(c['mem'][a] for a in sorted(c['mem'])[:8]), 'little') == estore
        print('%-26s %s  fex=%x top=%d st0=%s' % (name, 'ok' if ok else 'FAIL', fex, fp['top'], tuple(hex(x) for x in got0))); bad += not ok
    sys.exit(1 if bad else 0)
if __name__ == '__main__': main()
