#!/usr/bin/env python3
# reads: ports/fnv-nx/contract ports/fnv-nx/hostabi/product/build.json ports/fnv-nx/lib/sha256/build.json ports/fnv-nx/link
# reads: ports/fnv-nx/loader/build.json ports/fnv-nx/platform/build.json ports/fnv-nx/render/d3d9/build.json ports/fnv-nx/render/gl4/build.json
# reads: ports/fnv-nx/render/nxgpu/build.json ports/fnv-nx/render/pair/build.json ports/fnv-nx/render/shaders/translator/build.json
# reads: ports/fnv-nx/render/smscan/build.json ports/fnv-nx/rt/guest/build.json ports/fnv-nx/rt/guest_main/build.json ports/fnv-nx/shim/build.json
# reads: ports/fnv-nx/toolchain/addr/fnvnx_addr.cc ports/fnv-nx/toolchain/dispatch/fnvnx_dispatch.cc ports/fnv-nx/toolchain/ftoi/f2i_builtins.inc
# reads: ports/fnv-nx/toolchain/ftoi/fnvnx_ftoi.cc ports/fnv-nx/toolchain/layout/crossing_terms.inc ports/fnv-nx/toolchain/layout/decltype_spell.inc
# reads: ports/fnv-nx/toolchain/layout/fnvnx_layout.cc ports/fnv-nx/toolchain/msvcmodel/DATAMODEL.json ports/fnv-nx/toolchain/names/fnvnx_names.cc
# reads: ports/fnv-nx/toolchain/order/atomic_order.inc ports/fnv-nx/toolchain/order/fnvnx_frame.h ports/fnv-nx/toolchain/order/fnvnx_order.cc
# reads: ports/fnv-nx/toolchain/pi/fnvnx_pi.cc ports/fnv-nx/toolchain/sdd/fnvnx_sdd.cc ports/fnv-nx/verify
"""Test of the FOLD SET rule of toolchain/order/fnvnx_order.cc: a builtin of GCC's fold set (gimple-fold.cc gimple_fold_builtin) or any
non-atomic builtin that reads/writes through a pointer calls its library function, so GCC cannot fold it into plain loads/stores.
Fixture fixtures/order_fold.cc, built by the recipe with the full plugin set (realobj): copy16, zero8, cmp4 and len hold no plain
ldr/str/ldp/stp of data and call memcpy/memset/memcmp/strlen.
Run: python3 -I <this file> from the checkout root."""
import json
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


def ld_accesses():
    """ordered 64-bit accesses of one long double: its size is the declared data model's (toolchain/msvcmodel/DATAMODEL.json, row S09: `long double: N bytes`),
    one access per 8 bytes (the widest single ldar/stlr); the expectation is not a constant of this test"""
    with open(os.path.join(HERE, '..', '..', 'toolchain', 'msvcmodel', 'DATAMODEL.json')) as f:
        rows = json.load(f)
    rows = rows if isinstance(rows, list) else rows['rows']
    size = int(re.match(r'long double: (\d+) bytes', next(r for r in rows if r['id'] == 'S09')['property']).group(1))
    return -(-size // 8)


def main():
    obj = realobj.objects(['order_fold'])['order_fold']
    fns = T.functions(obj)
    # LIBRARY CALLS ARE PLAIN CALLS (the runtime orders its own accesses). A builtin GCC folds before the order pass is ordinary GIMPLE and
    # is ordered as any access (copy16, zero8, cmp4: unit accesses, no call); one that stays a call (strlen) is a plain call to its library
    # function: no fence around it.
    # (a plain access to the compiler's own frame temporary is no shared access: the gate certifies it against the frame record)
    for fn in ('_Z6copy16P1PPKS_', '_Z5zero8Pc', '_Z4cmp4PKcS0_', '_Z3lenPKc'):
        ins = fns.get(fn)
        if ins is None:
            fails.append('%s: missing (have %s)' % (fn, sorted(fns)))
        elif [m for o, m, a in ins if m.startswith('dmb')]:
            fails.append('%s: a fence around a call or access: %s' % (fn, ins))
    calls = lambda fn: [a for o, m, a in fns[fn] if m in ('bl', 'b')]   # noqa: E731
    ms = lambda fn: [m for o, m, a in fns[fn]]                          # noqa: E731
    if not calls('_Z3lenPKc'):
        fails.append('len: strlen on a shared pointer is a plain call: %s' % fns['_Z3lenPKc'])
    if not calls('_Z5zero8Pc') and not [m for m in ms('_Z5zero8Pc') if m.startswith('stlr')]:
        fails.append('zero8: a call, or ordered stores: %s' % ms('_Z5zero8Pc'))
    if ms('_Z6copy16P1PPKS_').count('ldarb') != 16 or ms('_Z6copy16P1PPKS_').count('stlrb') != 16:
        fails.append('copy16: the folded copy is 16 ordered byte loads and 16 ordered byte stores: %s' % ms('_Z6copy16P1PPKS_'))
    if ms('_Z4cmp4PKcS0_').count('ldar') != 2:
        fails.append('cmp4: the folded compare is two ordered loads: %s' % ms('_Z4cmp4PKcS0_'))
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
    # AGGREGATE COPIES: unit-wise (a loop beyond 16 units: copybig is the loop body plus the last unit), every shared unit ordered; ATOMICS: raised to seq_cst (ldaxr/stlxr)
    def mn(prefix):
        return [m for k, v in fns.items() if k.startswith(prefix) for o, m, a in v]
    for pre, nl in (('_Z6copy12', 3), ('_Z7copybig', 2)):
        ms = mn(pre)
        if ms.count('ldar') != nl or ms.count('stlr') != nl or (pre == '_Z7copybig' and not [m for m in ms if m.startswith('b.')]):
            fails.append('%s: want %d ldar + %d stlr and no plain access: %s' % (pre, nl, nl, {m: ms.count(m) for m in set(ms)}))
    if mn('_Z7ld_privPK3Bigi').count('ldar') != 2 or mn('_Z7ld_privPK3Bigi').count('stlr'):
        fails.append('ld_priv: a shared aggregate into an unshared local is ordered loads (loop body + last unit) and no ordered store: %s' % mn('_Z7ld_privPK3Bigi'))
    if mn('_Z4retgv').count('ldar') != 3 or mn('_Z6passesPK3T12').count('ldar') != 3:
        fails.append('retg/passes: the shared source of a by-value copy is read with 3 ldar')
    for pre in ('_Z3rmwPi', '_Z3casPiS_'):
        ms = mn(pre)
        if 'ldaxr' not in ms or 'stlxr' not in ms or 'ldxr' in ms or 'stxr' in ms:
            fails.append('%s: atomics must be seq_cst (ldaxr/stlxr): %s' % (pre, ms))
    # BIT-FIELDS: an access of the allocation unit (one ldar; a store is the unit's ldar + stlr), and no fence
    for pre, want in (('_Z6bf_getPK2BF', {'ldar': 1}), ('_Z7bf_getsPK2BF', {'ldar': 1}), ('_Z6bf_setP2BFj', {'ldar': 1, 'stlr': 1}),
                      ('_Z7bf_copyP2BFPKS_', {'ldar': 2, 'stlr': 1})):
        m_ = mn(pre)
        got = {k: m_.count(k) for k in want}
        if got != want or [m for m in m_ if m.startswith('dmb')]:
            fails.append('%s: want %s and no fence, got %s' % (pre, want, {m: m_.count(m) for m in set(m_)}))
    # CAS EXPECTED: a shared `expected` is bounced through a private copy (ordered load in, ordered store on failure); an unshared one is not
    cs = mn('_Z6cas_shPiS_')
    if cs.count('ldar') != 1 or cs.count('stlr') != 1:
        fails.append('cas_sh: expected must be one ordered load and one ordered store (on failure): %s' % cs)
    cl = mn('_Z7cas_locPi')
    if [m for m in cl if m in ('ldar', 'stlr')]:
        fails.append('cas_loc: an unshared expected stays unordered: %s' % cl)
    # types no single ldar/stlr covers: vector, complex, packed (each unit ordered)
    ov = T.functions(realobj.objects(['order_vec'])['order_vec'])
    for sym, want in (('_Z5vcopyPDv4_fPKS_', {'ldar': 2, 'stlr': 2}), ('_Z6vstorePDv4_fS_', {'stlr': 2}), ('_Z4cplxPKCf', {'ldar': 2}),
                      ('_Z2ldPKe', {'ldar': ld_accesses()}), ('_Z3ldsPee', {'stlr': ld_accesses()}), ('_Z6packedPK2Pk', {'ldarb': 4}), ('_Z6pstoreP2Pki', {'stlrb': 4})):
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
    # NO-MIX: no recorded frame range of a function overlaps another one (a private temporary keeps its own slot, never the slot of a shared object)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(['aarch64-linux-gnu-objcopy', '--dump-section', '.fnvnx.frame=' + td + '/fr', obj, td + '/o'], check=True)
        frtxt = open(td + '/fr', 'rb').read().decode('latin1')
    for sym in ('_Z6nomix2iPK2Gd', '_Z6nomix3iPK2GdS1_'):
        row = next((l for l in frtxt.split('\n') if l.startswith(sym + '\t')), None)
        if row is None:
            fails.append('no frame row for %s' % sym)
            continue
        rg = sorted((int(x.split(':')[0]), int(x.split(':')[1])) for x in row.split('\t')[1].split() if x[0] in '-0123456789')
        if any(rg[i][0] + rg[i][1] > rg[i + 1][0] for i in range(len(rg) - 1)):
            fails.append('%s: frame ranges overlap (a slot is shared): %s' % (sym, row))
    if fails:
        print('FAIL', *fails, sep='\n  ')
        return 1
    print('ok: builtins on shared operands are library calls')
    return 0


if __name__ == '__main__':
    sys.exit(main())
