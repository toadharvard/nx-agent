#!/usr/bin/env python3
"""c3_ecx.py - V1-link claim C3: the `ecx-not-passed` verdict flags exactly the pairs whose definition reads an ecx
parameter the reference does not pass; a missing edx (edx-dummy bridge) is never a parameter the definition reads.

    c3_ecx.py <link out dir> <prepared units.json> <pristine units.json> <MSVC object dir> <work dir>

For every reference bound to a decomp definition (refs.tsv code classes), V1's own declaration model (v1pairs.regs_of,
from the MSVC oracle) gives the registers each side passes / takes; the definition's MSVC 2008 object (the pristine
decomp, u<index>.obj) gives whether its code uses the incoming ecx / edx (coff_x86.entry_reads). Buckets:

  FN-read   ecx missing by declaration, the definition's code reads/passes ecx, not flagged x86-inconsistent
  FN-dead   ecx missing, not flagged, the code never uses ecx (harmless: no need to flag)
  TP        flagged ecx-not-passed / this-not-passed, ecx missing, the code uses it
  FP-dead   flagged ecx-not-passed, but the code never uses ecx (x86 ecx does not matter)
  FP-decl   flagged ecx-not-passed, but by V1's declaration model the reference passes ecx
  EDX-read  edx missing (edx-dummy), the definition's code reads/passes the incoming edx
Writes <work>/c3_pairs.tsv (local) and prints counts and the unique pairs of each bucket except TP.
"""
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bridge_harness as bh  # noqa: E402
import coff_x86              # noqa: E402
import v1pairs               # noqa: E402


def main():
    out_dir, units_json, pristine_json, objdir, work = sys.argv[1:6]
    os.makedirs(work, exist_ok=True)
    cache = os.path.join(work, 'undname.cache')
    prs = v1pairs.pairs(out_dir, units_json)
    pu = json.load(open(pristine_json))['units']
    pdef = collections.defaultdict(list)
    for i, u in enumerate(pu):
        for fn in u['functions']:
            pdef[int(fn['address'], 16)].append((i, fn['symbol']))
    names = set()
    for p in prs:
        names.add(p.ref)
        names.update(p.defs)
    dem = v1pairs.undname_cached(names, cache)
    abi_def = {}
    ab = v1pairs.load_abi(out_dir)
    for k, c in ab.items():
        abi_def[k] = c[4]

    def regs(name):
        if name and not name.startswith('?'):
            return v1pairs.regs_of_cname(name)
        t = dem.get(name)
        if not t:
            return None
        try:
            return v1pairs.regs_of(t)
        except bh.Skip:
            return None

    objs = {}
    conv_cache = {}

    def conv_of(sym):
        if sym in conv_cache:
            return conv_cache[sym]
        r = None
        if sym.startswith('?'):
            t = v1pairs.undname_cached([sym], cache).get(sym)
            try:
                cc, rg = v1pairs.regs_of(t) if t else (None, None)
                r = None if cc is None or 'unknown' in rg else {k for k in rg if k in ('ecx', 'edx')}
            except bh.Skip:
                r = set()                              # data symbol
        else:
            m = re.match(r'^@\w+@(\d+)$', sym)
            if m:
                n = int(m.group(1)) // 4
                r = {'ecx', 'edx'} if n >= 2 else ({'ecx'} if n == 1 else set())
            else:
                r = set()
        conv_cache[sym] = r
        return r

    use_cache = {}

    def code_use(addr):
        """Strongest use of the incoming ecx/edx over the pristine definitions at addr."""
        if addr in use_cache:
            return use_cache[addr]
        best = {'ecx': None, 'edx': None}
        found = 0
        for idx, sym in pdef.get(addr, []):
            p = os.path.join(objdir, 'u%d.obj' % idx)
            if not os.path.exists(p):
                continue
            if idx not in objs:
                try:
                    objs[idx] = coff_x86.Coff(p)
                except Exception:
                    objs[idx] = None
            o = objs[idx]
            if o is None:
                continue
            f = o.func(sym)
            if f is None:
                continue
            found += 1
            u = coff_x86.entry_reads(f[0], f[1], conv_of)
            for k in best:
                if coff_x86.RANK[u[k]] > coff_x86.RANK[best[k]]:
                    best[k] = u[k]
        res = (best if found else None)
        use_cache[addr] = res
        return res

    rows = []
    buckets = collections.Counter()
    uniq = collections.defaultdict(set)
    flagged_total = set()
    for p in prs:
        flagged = p.verdict == 'x86-inconsistent' and ('ecx-not-passed' in p.classes or 'this-not-passed' in p.classes)
        if p.verdict == 'x86-inconsistent' and 'ecx-not-passed' in p.classes:
            flagged_total.add((p.ref, p.addr))
        rr = regs(p.ref)
        dname = abi_def.get((p.obj, p.undef)) or (p.defs[0] if len(p.defs) == 1 else None)
        dnames = [dname] if dname else p.defs
        drs = [regs(d) for d in dnames]
        if rr is None or not drs or any(d is None for d in drs):
            buckets['undecided-decl'] += 1
            continue
        ref_regs = rr[1]
        def_ecx = any('ecx' in d[1] for d in drs)
        def_edx = any('edx' in d[1] for d in drs)
        ecx_missing = def_ecx and 'ecx' not in ref_regs and 'unknown' not in ref_regs
        edx_missing = def_edx and 'edx' not in ref_regs and 'unknown' not in ref_regs
        if not (ecx_missing or edx_missing or flagged):
            continue
        use = code_use(p.addr)
        cu = (use or {}).get('ecx')
        du = (use or {}).get('edx')
        b = []
        if ecx_missing:
            if use is None:
                b.append('ecx-missing-nocode' + ('-flagged' if flagged else ''))
            elif flagged:
                b.append('TP' if cu in ('read', 'pass') else ('FP-dead' if 'ecx-not-passed' in p.classes
                                                              else 'TP-this-dead'))
            elif p.verdict == 'x86-inconsistent':
                b.append('flagged-other' if cu in ('read', 'pass') else 'flagged-other-dead')
            else:
                b.append('FN-read' if cu in ('read', 'pass') else ('FN-push' if cu == 'push' else 'FN-dead'))
        elif flagged:
            b.append('FP-decl')
        if edx_missing:
            if use is None:
                b.append('edx-missing-nocode')
            elif du in ('read', 'pass'):
                b.append('EDX-' + du)
            else:
                b.append('edx-dead' if du is None else 'edx-push')
        if p.cls == 'code-pending':
            b = [x + '(pending)' for x in b]      # definition not compiled: resolve compares once it is
        elif not (p.ref.startswith('?') and all(d.startswith('?') for d in dnames)):
            b = [x + '(C-name)' if x.startswith(('FN', 'EDX')) else x for x in b]   # resolve compares no C name
        for x in b:
            buckets[x] += 1
            uniq[x].add((p.ref, '|'.join(dnames), p.addr, p.verdict, p.classes, cu, du))
        rows.append((p.obj, p.undef, '%08x' % p.addr, ','.join(b), p.cls + ':' + p.verdict, p.classes, str(cu), str(du), p.ref,
                     '|'.join(dnames), dem.get(p.ref, ''), ' | '.join(dem.get(d, '') for d in dnames)))
    with open(os.path.join(work, 'c3_pairs.tsv'), 'w') as f:
        f.write('#object\tundefined\taddress\tbuckets\tverdict\tclasses\tecx_use\tedx_use\tref\tdef\tref_dem\tdef_dem\n')
        for r in rows:
            f.write('\t'.join(r) + '\n')
    print('pairs (references bound to a decomp definition):', len(prs))
    print('tool ecx-not-passed unique (reference, address):', len(flagged_total))
    for k, v in sorted(buckets.items()):
        print('  %-22s refs %6d  unique pairs %5d' % (k, v, len(uniq[k])))
    for k in sorted(uniq):
        if k in ('TP',) or k.startswith('edx-dead') or k.startswith('edx-push') or k.endswith('(pending)'):
            continue
        print('== %s' % k)
        for ref, d, a, v, c, cu, du in sorted(uniq[k], key=lambda x: x[2])[:40]:
            print('  %08x %-16s %-40s ecx=%s edx=%s\n      ref %s\n      def %s' % (a, v, c[:40], cu, du,
                                                                         dem.get(ref) or ref,
                                                                         ' | '.join(dem.get(x) or x for x in d.split('|'))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
