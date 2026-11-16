#!/usr/bin/env python3
"""cpufeat_diff: the old capstone-group gate against the SDM-row gate, per site and in global numbers.

    python3 -I cpufeat_diff.py --sdmrows SDMROWS.json --cpumodel CPUID_MODEL.json --old-module OLD_f_cpufeat.py --old-cpufeat CPUFEAT.tsv --udsail UDSAIL.tsv [--undefined BAD.tsv] SITES.tsv [OUT.tsv]

SITES.tsv: packuniverse.py's sites file (`module:address<TAB>shape key<TAB>family`), every image. OLD_f_cpufeat.py: the retired fnvlift/f_cpufeat.py (git show <commit>:...), loaded under another name; it reads its feature table
from --old-cpufeat. The new gate is fnvlift/f_cpufeat.py over --sdmrows. A shape's answer is computed once and applied to every site of the shape (the gate reads the decoded form only).
Output rows: `site  shape  old  new  verdict` for every site where the two differ, and for every site the new gate cannot decide (`unsupported`: no SDM row and no opcode-map entry, or ambiguous rows).
--udsail: the lifter's `udsail` input (a header-only file for the first pass). --undefined BAD.tsv: a baddecode.tsv (`image<TAB>0<TAB>8 bytes`) of every decoded shape that has no SDM row and no Sail #UD verdict yet,
to be run through proof/udsail.py together with the images' baddecode.tsv files (the Sail rule b decides them; a completing path is a form that needs a Form).
The global numbers (all sites of all images): sites, cleared by the old gate, cleared by the new gate, sites with a different answer, unsupported sites."""
import collections
import importlib.util
import os
import sys

LIFT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LIFT)


def old_gate(path, cpufeat):
    from fnvlift import inputs
    spec = importlib.util.spec_from_file_location('fnvlift.f_cpufeat_old', path)
    real = inputs.get
    inputs.get = lambda n: cpufeat if n == 'cpufeat' else real(n)
    try:
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    finally:
        inputs.get = real
    return m


def main(argv):
    a = list(argv)

    def opt(n):
        if n not in a: raise SystemExit('missing %s' % n)
        i = a.index(n); v = a[i + 1]; del a[i:i + 2]; return v
    rows, old, cf, ud, cm = opt('--sdmrows'), opt('--old-module'), opt('--old-cpufeat'), opt('--udsail'), opt('--cpumodel')
    undef = opt('--undefined') if '--undefined' in a else None
    if len(a) not in (1, 2): raise SystemExit(__doc__)
    from fnvlift import inputs
    from proof import sdmfeat, shape
    inputs.configure(sdmrows=rows, cpumodel=cm, udsail=ud)
    from fnvlift import f_cpufeat as new
    O = old_gate(old, cf)
    md = shape.decoder(); by = collections.OrderedDict(); n = collections.Counter(); fam = {}
    sites = sdmfeat.read_sites(a[0])
    for ad, k, f in sites: n[k] += 1
    ans = {}; nodec = []
    for k in n:
        ins = sdmfeat.decode(shape.rep_bytes(k), md)
        o = ','.join(O.missing(ins)) or '-'
        try: nw = ','.join(new.missing(ins)) or '-'
        except Exception as e:
            nw = 'unsupported: %s' % str(e)[:80]
            if 'no SDM row' in str(e): nodec.append(k)
        ans[k] = (o, nw)
    if undef:
        with open(undef, 'w', newline='\n') as fh:
            fh.write('# fnvnx-undefined-forms/1: decoded shapes with no SDM row and no Sail verdict (image, address, 8 bytes)\n')
            for k in sorted(nodec): fh.write('forms\t0\t%s\n' % (shape.rep_bytes(k) + bytes(8))[:8].hex())
    cnt = collections.Counter(); out = open(a[1], 'w', newline='\n') if len(a) == 2 else None
    if out: out.write('# fnvnx-cpufeat-diff/1 sdmrows=%s\n# site\tshape\told\tnew\tverdict\n' % os.path.basename(rows))
    for ad, k, f in sites:
        o, nw = ans[k]; cnt['sites'] += 1
        cnt['old cleared'] += o != '-'; cnt['new cleared'] += nw not in ('-',) and not nw.startswith('unsupported')
        if nw.startswith('unsupported'): cnt['unsupported'] += 1; v = 'unsupported'
        elif (o != '-') != (nw != '-'): cnt['different'] += 1; v = 'old-only' if o != '-' else 'new-only'
        else: continue
        if out: out.write('%s\t%s\t%s\t%s\t%s\n' % (ad, k, o, nw, v))
    sys.stdout.write('shapes %d %s\n' % (len(n), dict(cnt)))
    return 1 if cnt['different'] or cnt['unsupported'] else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
