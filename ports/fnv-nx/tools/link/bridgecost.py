#!/usr/bin/env python3
"""bridgecost.py - the ABI bridges of a link run, rebuilt offline, with their size per shape (stream S2c-abi, the
bridge-cost item: S15-simd's overhead audit found bridges dominating the worst codegen ratios).

    bridgecost.py <link out dir> <units.json> [--tree <prepared tree>] [--top N] [--plans <out.json>]

For every distinct bridge (address, reference declaration) of <out>/abi.tsv that resolve would emit, the verdict
is decided again with the current abi / abi2 and the link's layouts, and the bridge is emitted with the generic
emitter (abi2.emit, abi.emit_bridge) and with abi2.emit_lean where that applies. Prints the bridge count and the
static instruction count by shape (classes), before (generic) and after (lean). --plans writes the abi2 plans in a
JSON form test_bridgefuzz.py replays under qemu.

Reads decomp-derived files; prints counts only (scratch otherwise).
"""
import argparse
import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import abi     # noqa: E402
import abi2    # noqa: E402
import layout  # noqa: E402
import sigs    # noqa: E402


def insns(code):
    """Instructions of an emitted bridge (labels, directives and comments are not)."""
    n = 0
    for line in (code or '').splitlines():
        t = re.sub(r'^\s*\d+:\s*', '', line).strip()
        if t and not t.startswith(('.', '//')) and not t.endswith(':'):
            n += 1
    return n


def bridges(out, units_json, tree=None):
    """Yield (address, ref MSVC, row, verdict) for every bridge resolve would emit from abi.tsv."""
    units = json.load(open(units_json))['units']
    def_unit = {}
    for u in units:
        for f in u['functions']:
            def_unit.setdefault(int(f['address'], 16), u['source'])
    lay = {k: v for k, v in layout.load(os.path.join(out, 'layouts.tsv')).items() if v is not None}
    seen = set()
    for line in open(os.path.join(out, 'abi.tsv'), encoding='utf-8', errors='surrogateescape'):
        if line.startswith('#'):
            continue
        f = line.rstrip('\n').split('\t')
        if len(f) < 11 or f[5] not in ('bridge', 'x86-inconsistent'):
            continue
        key = (f[2], f[3])
        if key in seen:
            continue
        rs, ds = sigs.parse_msvc(f[3], f[9] or None), sigs.parse_msvc(f[4], f[10] or None)
        if rs.kind != 'func' or ds.kind != 'func':
            continue
        src, du = f[0].split('#')[0], def_unit.get(int(f[2], 16))
        v = abi.compare(rs, ds)
        if v.kind != 'same' and du is not None:
            p2 = abi2.compare(rs, ds, lambda t: lay.get((src, t)), lambda t: lay.get((du, t)))
            if p2 is not None and (v.kind != 'bridge' or p2.kind != 'bridge') and \
                    not (v.kind == 'bridge' and 'narrow-extend' in v.classes):
                v2 = abi.Verdict()
                v2.kind, v2.classes, v2.notes, v2.plan = p2.kind, p2.classes, p2.notes, p2
                v = v2
        # resolve: an x86-inconsistent pop-mismatch / ecx-not-passed pair still gets its moves
        ekind = v.kind
        if v.kind == 'x86-inconsistent' and (('ecx-not-passed' in v.classes and (v.moves or v.plan is not None)) or (
                'pop-mismatch' in v.classes and (v.moves or (v.plan is not None and v.plan.moves)) and not {
                    'this-not-passed', 'size-differs', 'cast-conflict', 'result-pointer-not-passed'} & set(v.classes))):
            ekind = 'bridge'
        if ekind != 'bridge':
            continue
        seen.add(key)
        yield int(f[2], 16), f[3], f, v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out')
    ap.add_argument('units')
    ap.add_argument('--top', type=int, default=15)
    ap.add_argument('--plans')
    a = ap.parse_args()
    by = collections.defaultdict(lambda: [0, 0, 0, 0])   # shape -> [bridges, generic insns, lean insns, lean used]
    tot = [0, 0, 0, 0]
    plans = []
    for addr, ref, f, v in bridges(a.out, a.units):
        name = abi.bridge_name(addr, ref)
        if v.plan is not None:
            gen = abi2.emit(name, 'T', v.plan, '')
            lean = abi2.emit_lean(name, 'T', v.plan, '') if hasattr(abi2, 'emit_lean') else None
            plans.append({'name': name, 'ref': ref, 'def': f[4], 'refd': f[9], 'defd': f[10], 'src': f[0]})
        else:
            gen = abi.emit_bridge(name, 'T', v, '')
            lean = None
        g = insns(gen)
        l_ = insns(lean) if lean else g
        shape = ','.join(sorted(set(re.sub(r'\([^)]*\)', '', c) for c in v.classes))) or '-'
        for t in (by[shape], tot):
            t[0] += 1; t[1] += g; t[2] += l_; t[3] += lean is not None
    print('bridges %d: instructions generic %d, lean %d (lean emitter used for %d)' % tuple(tot))
    for shape, t in sorted(by.items(), key=lambda kv: -kv[1][1])[:a.top]:
        print('  %5d bridges  %6d -> %6d insns  (%4.1f -> %4.1f per bridge)  %s' % (
            t[0], t[1], t[2], t[1] / t[0], t[2] / t[0], shape))
    if a.plans:
        json.dump(plans, open(a.plans, 'w'))


if __name__ == '__main__':
    main()
