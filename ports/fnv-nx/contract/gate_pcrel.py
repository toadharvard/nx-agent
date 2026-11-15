#!/usr/bin/env python3
"""gate_pcrel - the guest is position independent everywhere (S7 of the loader rules; part of the L1 verdict).

    gate_pcrel.py --elf <linked guest elf> --objects <dir of the objects that were linked> --work <dir>
                  --readelf <aarch64 readelf> --nm <aarch64 nm>

A PC-relative relocation (adrp/adr/literal load/branch) whose symbol is absolute in the linked ELF is right
only at the link base: a relocated guest moves the PC and not the target.  An image address must be reached
through the GOT (relocated by value) or as an immediate.  contract/pcrel_abs.py finds such sites.
Gate: 0 sites.  A "pinned" guest (one that may only run at its link base) is not a state the clean branch holds.
Exit status 1 otherwise.  Counts only on stdout.
--work: a directory for the response file and the site list (nothing is read from TMPDIR or the process id).
Gate file `pcrel` ($GATE_OUT/pcrel.json, gatefile.py): universe = the PC-relative relocations examined (second summary line
of the tool), certified = examined - offending sites, uncertified = `object:section+offset:symbol` of the first 50 (sorted).
"""
import argparse, glob, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gatefile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, 'pcrel_abs.py')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--elf', required=True)
    ap.add_argument('--objects', required=True)
    ap.add_argument('--work', required=True, help='directory for the response file and the site list (created)')
    ap.add_argument('--readelf', required=True, help='path of the aarch64 readelf (no PATH lookup)')
    ap.add_argument('--nm', required=True, help='path of the aarch64 nm (no PATH lookup)')
    a = ap.parse_args()
    objs = sorted(glob.glob(os.path.join(a.objects, '*.o')))
    os.makedirs(a.work, exist_ok=True)
    rsp = os.path.join(a.work, 'pcrel.rsp')
    sites = os.path.join(a.work, 'pcrel_sites.tsv')
    with open(rsp, 'w', newline='\n') as fh:
        fh.write('\n'.join(objs))
    out = subprocess.run([sys.executable, TOOL, a.elf, a.readelf, a.nm, '@' + rsp, '--out', sites], capture_output=True, text=True)
    m = re.search(r'pc-relative references to absolute symbols: (\d+)', out.stdout)
    e = re.search(r'^pc-relative relocations examined: (\d+)$', out.stdout, re.M)
    if out.returncode or not m or not e:
        sys.exit('pcrel_abs.py failed: ' + (out.stderr or out.stdout)[-300:])
    first = out.stdout.strip().splitlines()[0]
    print(first)
    n, examined = int(m[1]), int(e[1])
    ids = []
    with open(sites) as fh:
        for ln in fh:
            p = ln.rstrip('\n').split('\t')
            if p[0][:1] != '#' and len(p) >= 5:
                ids.append('%s:%s+%s:%s' % (os.path.basename(p[0]), p[1], p[2], p[4]))
    gatefile.write('pcrel', examined, examined - n, ids, '%s; examined %d' % (first, examined))
    sys.exit(1 if n else 0)


if __name__ == '__main__':
    main()
