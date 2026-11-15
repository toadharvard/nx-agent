#!/usr/bin/env python3
"""Test of the FOLD SET rule of toolchain/order/fnvnx_order.cc: a builtin of GCC's fold set (gimple-fold.cc gimple_fold_builtin) or any
non-atomic builtin that reads/writes through a pointer calls its library function, so GCC cannot fold it into plain loads/stores.
Fixture fixtures/order_fold.cc, built by the recipe with the full plugin set (realobj): copy16, zero8, cmp4 and len hold no plain
ldr/str/ldp/stp of data and call memcpy/memset/memcmp/strlen.
Run: python3 -I <this file> from the checkout root."""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'contract'))
import realobj  # noqa: E402
import test_sdd_order as T  # noqa: E402

fails = []


def main():
    obj = realobj.objects(['order_fold'])['order_fold']
    fns = T.functions(obj)
    want = {'_Z6copy16P1PPKS_': 'memcpy', '_Z5zero8Pc': 'memset', '_Z4cmp4PKcS0_': 'memcmp', '_Z3lenPKc': 'strlen'}
    for fn, callee in want.items():
        ins = fns.get(fn)
        if ins is None:
            fails.append('%s: missing (have %s)' % (fn, sorted(fns)))
            continue
        mem = [m for o, m, a in ins if re.match(r'(ldr|str|ldp|stp|ldur|stur|ldrb|strb)', m) and not re.search(r'\[(sp|x29)\b', a)]   # frame spills are not data
        calls = [a for o, m, a in ins if m in ('bl', 'b') and callee in a]
        # the GOT/PLT call carries the symbol in its relocation or operand text
        if mem:
            fails.append('%s: plain access %s' % (fn, mem))
        if not calls:
            fails.append('%s: no call to %s: %s' % (fn, callee, ins))
    sf = [i for k, v in fns.items() if k.startswith('_Z6slot_fv') for i in v]
    if not sf:
        fails.append('slot_f missing')
    elif [m for o, m, a in sf if m in ('ldar', 'stlr') or m.startswith('dmb') or m == 'bl']:
        fails.append('slot_f: a non-escaping builtin on locals must stay unordered and inline: %s' % sf)
    pc = [i for k, v in fns.items() if k.startswith('_Z7pcalleri') or k.startswith('_Z7pcallerj') for i in v]
    plain = [(m, a) for o, m, a in pc if re.match(r'(str|stp|ldr|ldp)', m) and not re.search(r'\[(sp|x29)\b', a)]
    stl = [m for o, m, a in pc if m == 'stlr' or m.startswith('dmb')]   # an ordered or release-fenced store
    if not pc or plain or len(stl) < 2:
        fails.append('pcaller: the inlined parameter store must be ordered: plain=%s stlr=%d' % (plain, len(stl)))
    # AGGREGATE COPIES: unit-wise, every shared unit ordered; ATOMICS: raised to seq_cst (ldaxr/stlxr)
    def mn(prefix):
        return [m for k, v in fns.items() if k.startswith(prefix) for o, m, a in v]
    for pre, nl in (('_Z6copy12', 3), ('_Z7copybig', 100)):
        ms = mn(pre)
        if ms.count('ldar') != nl or ms.count('stlr') != nl or [m for m in ms if re.match(r'(ldr|str|ldp|stp)', m)]:
            fails.append('%s: want %d ldar + %d stlr and no plain access: %s' % (pre, nl, nl, {m: ms.count(m) for m in set(ms)}))
    if mn('_Z4retgv').count('ldar') != 3 or mn('_Z6passesPK3T12').count('ldar') != 3:
        fails.append('retg/passes: the shared source of a by-value copy is read with 3 ldar')
    for pre in ('_Z3rmwPi', '_Z3casPiS_'):
        ms = mn(pre)
        if 'ldaxr' not in ms or 'stlxr' not in ms or 'ldxr' in ms or 'stxr' in ms:
            fails.append('%s: atomics must be seq_cst (ldaxr/stlxr): %s' % (pre, ms))
    # types no single ldar/stlr covers: vector, complex, long double, packed (each unit ordered)
    ov = T.functions(realobj.objects(['order_vec'])['order_vec'])
    for sym, want in (('_Z5vcopyPDv4_fPKS_', {'ldar': 2, 'stlr': 2}), ('_Z6vstorePDv4_fS_', {'stlr': 2}), ('_Z4cplxPKCf', {'ldar': 2}),
                      ('_Z2ldPKe', {'ldar': 2}), ('_Z3ldsPee', {'stlr': 2}), ('_Z6packedPK2Pk', {'ldarb': 4}), ('_Z6pstoreP2Pki', {'stlrb': 4})):
        ms = [m for o, m, a in ov[sym]]
        got = {k: ms.count(k) for k in want}
        if got != want:
            fails.append('%s: want %s, got %s (%s)' % (sym, want, got, sorted(set(ms))))
    # READONLY RECORD: .fnvnx.ro names the table with its byte ranges (the whole 32 bytes for the indexed read)
    import subprocess
    sec = subprocess.run(['objdump', '-s', '-j', '.fnvnx.ro', obj], capture_output=True, text=True)
    txt = sec.stdout if sec.returncode == 0 else ''
    flat = ''.join(re.findall(r'^ [0-9a-f]+ ((?:[0-9a-f]{2,8} ?)+)', txt, re.M)).replace(' ', '')
    rec = bytes.fromhex(flat).decode('latin1') if flat else ''
    if '_ZL3tbl\t0:32\n' not in rec:
        fails.append('.fnvnx.ro lacks the tbl ranges: %r' % rec)
    if fails:
        print('FAIL', *fails, sep='\n  ')
        return 1
    print('ok: builtins on shared operands are library calls')
    return 0


if __name__ == '__main__':
    sys.exit(main())
