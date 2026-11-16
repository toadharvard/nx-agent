#!/usr/bin/env python3
"""udcensus: the CPUID-UD census over the code universe of every image (the same universe as proof/factsites.py: L3a's facts graph, one decoder).

    python3 -I udcensus.py --exe FalloutNV.exe --modules modules.tsv --dir DIR [--dir DIR ...] --facts FACTS_DIR --cpumodel cpuid_model.json --sdmrows SDMROWS.json --out CENSUS.tsv

Per image it counts, over EVERY instruction of the universe:
  total          the instructions
  ud:<GROUP>     the instructions whose capstone feature group has a clear CPUID bit on the declared CPU (cpufeat.missing): the lifter lowers each to #UD
  gap:<class>    the instructions the capstone group rule does NOT lower, whose behaviour on the declared CPU it therefore decides wrongly or not at all:
    sdm_absent   no capstone feature group is missing, and the SDM opcode row (sdmfeat.match_form / needs_of over SDMROWS) names a CPUID flag the declared CPU lacks
                 (the open-ended class: fisttp, MMX-form pshufb and every 0F38/0F3A form without a group, xgetbv, prefetchw, ... as the SDM rows give them, not a list)
    f3_bsf_bsr   F3 0F BC / F3 0F BD: tzcnt / lzcnt to capstone (BMI / LZCNT groups), BSF / BSR to a CPU without that flag (SDM: "executes as BSF/BSR")
    no_group     the instruction has no capstone group at all, is not a branch / privileged / mode instruction, and decodes in map 0F38 / 0F3A (an encoding without a group)
Every row lists the sites (address) of its class, so a class with sites is a worklist and a class with none is an assertion over the universe."""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools')); sys.path.insert(0, HERE)
import imageset  # noqa: E402
from fnvlift import inputs  # noqa: E402
from proof import cpufeat, sdmfeat, shape  # noqa: E402
import udcert as U  # noqa: E402
from factsites import Mapped, sha  # noqa: E402


def classify(i, raw, idx, table, cpu, nopflags, miss):
    """-> the gap classes of one decoded instruction (a list; empty when the capstone group rule covers it)"""
    out = []
    p = 0
    while p < len(raw) and raw[p] in (0x66, 0xf2, 0xf3, 0x2e, 0x36, 0x3e, 0x26, 0x64, 0x65, 0x67, 0xf0):
        p += 1
    if not miss:
        cand, kind, mp = sdmfeat.match_form(i, idx)
        if cand:
            st, needs, val, ea, bad = sdmfeat.needs_of(cand, table, cpu, nopflags)
            if val == 'absent':
                out.append('sdm_absent')
        if raw[:p].count(0xf3) and raw[p:p + 2] in (b'\x0f\xbc', b'\x0f\xbd'):
            out.append('f3_bsf_bsr')
        if raw[p:p + 2] in (b'\x0f\x38', b'\x0f\x3a') and not [g for g in i.groups]:
            out.append('no_group')
    elif raw[:p].count(0xf3) and raw[p:p + 2] in (b'\x0f\xbc', b'\x0f\xbd'):
        out.append('f3_bsf_bsr')
    return out


def census(pe_path, facts, load_base, idx, table, cpu, nopflags):
    base, ranges = U.load_ranges(os.path.join(facts, 'ranges.tsv'))
    uni = U.load_universe(os.path.join(facts, 'universe.tsv'))
    if load_base is not None and base != load_base:
        raise SystemExit('%s: the facts are at base %08x, the module table assigns %08x' % (pe_path, base, load_base))
    g = U.Graph(Mapped(pe_path, load_base), ranges, uni, lambda i: False)
    md = shape.decoder(); res = {'total': []}
    for a in sorted(g.ins):
        raw = g.pe.read(a, 15)
        i = next(iter(md.disasm(raw, a, 1)))
        raw = bytes(raw[:i.size])
        res['total'].append(a)
        miss = cpufeat.missing(i)
        for m in miss:
            res.setdefault('ud:' + m, []).append(a)
        for c in classify(i, raw, idx, table, cpu, nopflags, miss):
            res.setdefault('gap:' + c, []).append(a)
    return res


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe', required=True); ap.add_argument('--modules', required=True); ap.add_argument('--dir', action='append', required=True)
    ap.add_argument('--facts', required=True); ap.add_argument('--cpumodel', required=True); ap.add_argument('--sdmrows', required=True); ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    inputs.configure(cpumodel=a.cpumodel)
    allrows, table, nopflags = sdmfeat.load_rows(a.sdmrows); idx = sdmfeat.index_rows(allrows); cpu = cpufeat._cpu()
    ims = imageset.images(a.exe, a.modules, a.dir); ab = imageset.assigned(a.modules)
    with open(a.out, 'w', newline='\n') as fh:
        fh.write('# fnvnx-udcensus/1 images=%d modules_sha256=%s cpumodel_sha256=%s\n# image\tclass\tcount\tsites\n' % (len(ims), sha(a.modules), sha(a.cpumodel)))
        for name, path, sub in ims:
            fd = a.facts if sub is None else os.path.join(a.facts, sub)
            r = census(path, fd, ab.get(name), idx, table, cpu, nopflags)
            for k in sorted(r):
                fh.write('%s\t%s\t%d\t%s\n' % (name, k, len(r[k]), ','.join('%08x' % x for x in r[k]) if k != 'total' else ''))
            sys.stdout.write('%s: %d instructions, %s\n' % (name, len(r['total']), ' '.join('%s=%d' % (k, len(v)) for k, v in sorted(r.items()) if k != 'total') or 'no #UD and no gap'))
            sys.stdout.flush()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
