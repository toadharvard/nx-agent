#!/usr/bin/env python3
"""c2_shapes.py - V1-link: find the real (reference, definition) pairs of a link run that have the shapes of the
adversarial reproducers (adversarial_abi.py), with V1's own signature parsing:

  C  float result read as double or double as float (x87 st(0) converts exactly on x86; on AArch64 s0 vs d0 differ)
  B  __fastcall with an aggregate result through a hidden pointer on one side, an explicit pointer on the other
  A  free function returning a 1/2/4/8-byte aggregate on one side, an explicit result pointer on the other (MSVC returns
     a non-POD in a hidden buffer, a POD in edx:eax: the bridge choice depends on C++03 POD-ness)

    c2_shapes.py <link out dir> <units.json> <work dir>
"""
import collections
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bridge_harness as bh  # noqa: E402
import v1pairs               # noqa: E402


def lay_of(out_dir):
    lay = {}
    if not os.path.exists(os.path.join(out_dir, 'layouts.tsv')):
        return lay
    for line in open(os.path.join(out_dir, 'layouts.tsv')):
        c = line.rstrip('\n').split('\t')
        if len(c) >= 7 and not line.startswith('#') and c[2] != '-1':
            lay[(c[0], c[1])] = int(c[2])
    return lay


def agg_name(t):
    t = ' '.join(re.sub(r'\b(const|volatile)\b', '', t).split())
    if re.search(r'[*&]', t):
        return None                       # a pointer or reference to an aggregate is a scalar
    m = re.match(r'^(class|struct|union) (.*)$', t)
    return m.group(2) if m else None


def main():
    out_dir, units_json, work = sys.argv[1:4]
    os.makedirs(work, exist_ok=True)
    prs = [p for p in v1pairs.pairs(out_dir, units_json) if p.cls in ('code', 'code-bridge', 'code-manual')]
    dem = v1pairs.undname_cached({n for p in prs for n in [p.ref] + p.defs}, os.path.join(work, 'undname.cache'))
    abi = v1pairs.load_abi(out_dir)
    lay = lay_of(out_dir)
    found = collections.defaultdict(dict)
    for p in prs:
        a = abi.get((p.obj, p.undef))
        dname = a[4] if a else (p.defs[0] if len(p.defs) == 1 else None)
        if not dname or not p.ref.startswith('?') or not dname.startswith('?'):
            continue
        try:
            rs, ds = bh.Sig(dem.get(p.ref) or ''), bh.Sig(dem.get(dname) or '')
        except bh.Skip:
            continue
        key = (p.ref, dname)
        rr, dr = rs.ret.replace(' const', ''), ds.ret.replace(' const', '')
        fl = {'float', 'double', 'long double'}
        if rr in fl and dr in fl and (rr == 'float') != (dr == 'float'):
            found['C float/double result'][key] = (p, rs, ds)
        # E: the callee pops its arguments (stdcall / thiscall / fastcall) and the reference pushes another number
        # of stack bytes: x86 returns with esp moved (the pair cannot have worked as declared)
        if ds.cc in ('stdcall', 'thiscall', 'fastcall'):
            try:
                def stack_bytes(s):
                    lay = {}
                    tys = [bh.ty_of(t, lay) for t in s.params]
                    locs, _ = bh.x86_args(s, tys, bh.ty_of(s.ret, lay))   # aggregates (no layout here): Skip
                    return sum((sz + 3) // 4 * 4 for lab, loc, sz in locs if isinstance(loc, tuple))
                if stack_bytes(rs) != stack_bytes(ds):
                    found['E callee-pop byte count differs'][key] = (p, rs, ds)
            except bh.Skip:
                pass
        # D: a member returns a 1/2/4/8-byte aggregate (MSVC: hidden buffer, always for members) and the other side
        # is a free function returning an aggregate of the same size in edx:eax (trivial: as abi2 assumes)
        for x, y in ((rs, ds), (ds, rs)):
            ax, ay = agg_name(x.ret), agg_name(y.ret)
            if ax and ay and x.member and not y.member and y.cc != 'thiscall':
                sx = lay.get((p.obj, ax)) or next((v for (u, t), v in lay.items() if t == ax), None)
                sy = next((v for (u, t), v in lay.items() if t == ay), None)
                if sx in (1, 2, 4, 8) and sy == sx:
                    found['D member hidden result vs free small aggregate in registers'][key] = (p, rs, ds)
        for x, y in ((rs, ds), (ds, rs)):
            ag = agg_name(x.ret)
            if ag and x.cc == 'fastcall' and p.cls == 'code-bridge':
                size = lay.get((p.obj, ag)) or next((v for (u, t), v in lay.items() if t == ag), None)
                if size is None or size > 8:     # MSVC: hidden pointer (first stack word for fastcall)
                    found['B bridged pair with a __fastcall aggregate result'][key] = (p, rs, ds)
            if ag and not x.member and len(y.params) >= len(x.params) + 1 and y.params and \
                    any(q.endswith('*') for q in y.params):
                size = lay.get((p.obj, ag)) or next((v for (u, t), v in lay.items() if t == ag), None)
                if size in (1, 2, 4, 8):
                    found['A free small aggregate result vs explicit pointer'][key] = (p, rs, ds)
    # run the direct bindings of a few float/double pairs under qemu ILP32 (no bridge: the tool judged them same)
    direct = [(p, rs, ds) for p, rs, ds in found['C float/double result'].values() if p.verdict == 'same']
    runs = collections.Counter()
    for p, rs, ds in sorted(direct, key=lambda x: x[0].addr)[:8]:
        dname = (abi.get((p.obj, p.undef)) or [None] * 5)[4] or p.defs[0]
        try:
            st, notes = bh.run_case(p.ref, dname, {}, {}, None, os.path.join(work, 'C_%08x' % p.addr), seed=2,
                                    dem={p.ref: rs.text, dname: ds.text})
        except bh.Skip as e:
            st = 'skip ' + str(e)
        runs[st.split(' ')[0]] += 1
        print('C direct %08x: %s | %s -> %s' % (p.addr, st[:90], rs.ret, ds.ret))
    print('C direct runs:', dict(runs))
    for k in sorted(found):
        v = found[k]
        print('== %s: %d unique pairs, %d references' % (k, len(v), sum(1 for p in prs if (p.ref, (abi.get(
            (p.obj, p.undef)) or [None] * 5)[4] or (p.defs[0] if len(p.defs) == 1 else None)) in v)))
        print('   tool verdicts (unique pairs):', dict(collections.Counter('%s/%s' % (p.cls, p.verdict)
                                                                     for p, _, _ in v.values())))
        for (r, d), (p, rs, ds) in sorted(v.items(), key=lambda x: x[1][0].addr)[:12]:
            print('  %08x %-11s %-12s %s\n      ref %s\n      def %s' % (p.addr, p.cls, p.verdict, p.classes[:40],
                                                                     rs.text, ds.text))
    return 0


if __name__ == '__main__':
    sys.exit(main())
