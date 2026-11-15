"""SDM spec of the string instructions with the REP / REPE / REPNE prefixes (OWNER 10: the part that the pinned model does not cover: defect rep-prefix-ignored).
Every SDM quote is checked by sdm_cite_check.py against stringspec_pages.tsv; the result of check() is proved | FAILED(dsl) | uncertified: <why>.

The REP loop.  SDM 2B:P1853
  'WHILE CountReg ≠ 0'
  'Service pending interrupts (if any);'
  'Execute associated string instruction;'
SDM 2B:P1853  'CountReg := (CountReg – 1);'
SDM 2B:P1853  'Use ECX for CountReg;'
SDM 2B:P1853  'the EIP register points to the string instruction'
The SDM says that between two iterations the loop is an architectural state (interrupts are serviced, EIP points to the string instruction, ECX is the value after the last
iteration).  The spec is the state after ONE pass of the loop body, with EIP = the instruction while the loop goes on and the next instruction when it exits: the DSL Form
has this meaning (one iteration per step), and the whole repetition is that step iterated, as the SDM loop is.
REPE, SDM 2B:P1855:   'IF ZF = 0 THEN exit WHILE loop;'   (after the decrement of CountReg, and the WHILE condition CountReg ≠ 0 stops the loop at 0)
REPNE, SDM 2B:P1857:  'IF CountReg = 0 THEN exit WHILE loop; FI;'   'IF ZF = 1 THEN exit WHILE loop; FI;'
The ZF tests are those of CMPS and SCAS ('None by the prefixes' flags): the REPE / REPNE prefixes are defined only for these two; MOVS, STOS and LODS take REP, whose loop has no ZF test.

The string instructions (the step in bytes: by operand size, DF = 0 increments, DF = 1 decrements):
  MOVS, SDM 2B:P1403:  'if the DF flag is 1, the (E)SI and (E)DI registers are decremented.'   'incremented or decremented by 1 for byte operations, by 2 for word operations, or by 4 for doubleword operations.'
  MOVS, SDM 2B:P1402:  'If the DF flag is 0, the (E)SI and (E)DI register are'
  STOS, SDM 2B:P1967:  'THEN IF DF = 0 THEN (E)DI := (E)DI + 2;'   'ELSE (E)DI := (E)DI – 1;'   (DEST := AL / AX / EAX, the store of the accumulator at ES:EDI)
  LODS, SDM 2A:P1272:  'THEN AL := SRC; (* Byte load *)'   'ELSE IF AX := SRC; (* Word load *)'   'ELSE IF EAX := SRC; (* Doubleword load *)'   'ELSE (E)SI := (E)SI – 1;'
  CMPS, SDM 2A:P886:   'temp := SRC1 - SRC2;'   'SetStatusFlags(temp);'
  CMPS, SDM 2A:P887:   'The CF, OF, SF, ZF, AF, and PF flags are set according to the temporary result of the comparison.'
  SCAS, SDM 2B:P1906:  'temp := AL − SRC;'   'temp := AX − SRC;'   'temp := EAX – SRC;'
The status flags of a subtraction, SDM 1:P82:
  'Set if an arithmetic operation generates a carry or a borrow out of the most-significant bit of the result; cleared otherwise.'   CF: a <u b
  'Set if the least-significant byte of the result contains an even number of 1 bits; cleared otherwise.'                            PF
  'Set if an arithmetic operation generates a carry or a borrow out of bit 3 of the result; cleared otherwise.'                      AF: low nibble of a <u low nibble of b
SDM 1:P82  'Set if the result is zero; cleared otherwise.'     ZF
SDM 1:P82  'Set equal to the most-significant bit of the result, which is the sign bit of a signed integer.'                                  SF
SDM 1:P82  'Set if the integer result is too large a positive number or too small a negative number (excluding the sign-bit) to fit in the destination operand; cleared otherwise.'  OF: the signed result a - b does not fit in w bits
The address size is 32 bits (ESI, EDI, ECX), the segments are flat: a 0x67 prefix or a segment override is outside this spec (uncertified: the reason names it).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import z3  # noqa: E402
from fnvlift import dsl  # noqa: E402
from proof import shape, symform  # noqa: E402

OTHER_PREFIX = (0x67, 0x2e, 0x36, 0x3e, 0x26, 0x64, 0x65, 0xf0)
OPS = ('movs', 'stos', 'lods', 'cmps', 'scas')


def decode(key):
    ins = next(iter(shape.decoder().disasm(shape.rep_bytes(key), shape.ADDR)))
    t = ins.mnemonic.split()
    rep = t[0] if len(t) > 1 else None
    m = t[-1]
    if m[:4] not in OPS or len(m) != 5 or m[4] not in 'bwd' or rep not in (None, 'rep', 'repe', 'repne', 'repz', 'repnz'): return None
    return rep, m[:4], {'b': 8, 'w': 16, 'd': 32}[m[4]], ins.size


def V(n, w): return dsl.to_smt(dsl.V(n, w))


def load(a, w):
    m = dsl.mem_array(); bs = [z3.Select(m, a + i) for i in range(w // 8)]
    return z3.Concat(*reversed(bs)) if len(bs) > 1 else bs[0]


def spec(rep, op, w, size):
    """the SDM state after one pass of the loop body: {reg: term}, {flag: 1-bit term}, the store (addr, value) or None, eip"""
    esi, edi, ecx, eax, eip = V('esi', 32), V('edi', 32), V('ecx', 32), V('eax', 32), V('eip', 32)
    one = lambda b: z3.If(b, z3.BitVecVal(1, 1), z3.BitVecVal(0, 1))
    act = ecx != 0 if rep else z3.BoolVal(True)                  # WHILE CountReg != 0
    d = z3.If(V('df', 1) == 1, z3.BitVecVal(-(w // 8), 32), z3.BitVecVal(w // 8, 32))
    acc = z3.Extract(w - 1, 0, eax)
    regs, flags, store = {}, {}, None
    g = lambda new, old: z3.If(act, new, old)
    src, dst = load(esi, w), load(edi, w)
    if op in ('movs', 'stos'): store = (edi, g(src if op == 'movs' else acc, dst))
    if op in ('movs', 'lods', 'cmps'): regs['esi'] = g(esi + d, esi)
    if op in ('movs', 'stos', 'cmps', 'scas'): regs['edi'] = g(edi + d, edi)
    if op == 'lods': regs['eax'] = g(z3.Concat(z3.Extract(31, w, eax), src) if w < 32 else src, eax)
    zf = None
    if op in ('cmps', 'scas'):
        a, b = (src, dst) if op == 'cmps' else (acc, dst); r = a - b
        sub = {'cf': one(z3.ULT(a, b)), 'pf': one(z3.Extract(0, 0, r ^ z3.LShR(r, 1) ^ z3.LShR(r, 2) ^ z3.LShR(r, 3) ^ z3.LShR(r, 4) ^ z3.LShR(r, 5) ^ z3.LShR(r, 6) ^ z3.LShR(r, 7)) == 0),
               'af': one(z3.ULT(z3.Extract(3, 0, a), z3.Extract(3, 0, b))), 'zf': one(r == 0), 'sf': one(z3.Extract(w - 1, w - 1, r) == 1),
               'of': one(z3.SignExt(1, a) - z3.SignExt(1, b) != z3.SignExt(1, r))}
        for f, v in sub.items(): flags[f] = z3.If(act, v, V(f, 1))
        zf = sub['zf']
    nxt = eip + size
    if rep:
        regs['ecx'] = g(ecx - 1, ecx); more = (ecx - 1) != 0
        if op in ('cmps', 'scas'): more = z3.And(more, zf == 1 if rep in ('repe', 'repz') else zf == 0)
        e = z3.If(z3.And(act, more), eip, nxt)
    else: e = nxt
    return regs, flags, store, e


def check(key):
    pre = bytes.fromhex(key.split('/')[0])
    if any(b in OTHER_PREFIX for b in pre): return 'uncertified', 'uncertified: ' + 'addr-seg pfx'
    dc = decode(key)
    if dc is None: return 'not-covered', 'no string'
    rep, op, w, size = dc
    if rep in ('repe', 'repz', 'repne', 'repnz') and op not in ('cmps', 'scas'): return 'uncertified', 'uncertified: ' + 'rep-zf only'
    try: f = symform.symbuild(key).form
    except Exception as e: return 'not-covered', 'no Form (%s)' % type(e).__name__
    sr, sf, ss, se = spec(rep, op, w, size)
    if f.faults: return 'FAILED(dsl)', 'faults'
    pr = {r[0]: dsl.to_smt(r[1]) for r in f.regs}; pf = {r[0]: dsl.to_smt(r[1]) for r in f.flags}
    s = z3.Solver()
    diff = []
    for name in set(pr) | set(sr) | {'esi', 'edi', 'ecx', 'eax'}:
        a, b = pr.get(name, V(name, 32)), sr.get(name, V(name, 32))
        s.push(); s.add(a != b)
        if s.check() != z3.unsat: diff.append(name)
        s.pop()
    for name in set(pf) | set(sf):
        a, b = pf.get(name, V(name, 1)), sf.get(name, V(name, 1))
        s.push(); s.add(a != b)
        if s.check() != z3.unsat: diff.append(name)
        s.pop()
    if (ss is None) != (not f.stores): diff.append('stores')
    elif ss is not None:
        if len(f.stores) != 1: diff.append('nstores')
        else:
            fa, fv = dsl.to_smt(f.stores[0][0]), dsl.to_smt(f.stores[0][1])
            s.push(); s.add(z3.Or(fa != ss[0], fv != ss[1]))
            if s.check() != z3.unsat: diff.append('store')
            s.pop()
    s.push(); s.add(dsl.to_smt(f.eip) != se)
    if s.check() != z3.unsat: diff.append('eip')
    s.pop()
    return ('proved', 'one pass %s %s' % (rep or 'plain', op)) if not diff else ('FAILED(dsl)', 'differs: ' + ','.join(sorted(diff)))


def main(argv):
    keys = [ln.split()[0] for ln in open(argv[0]) if ln.strip() and ln[0] != '#'] if argv else []
    bad = 0
    for k in keys:
        r, n = check(k); bad += r.startswith('FAILED')
        print('%s\t%s\t%s' % (k, r, n))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
