#!/usr/bin/env python3
"""gate_pcrel - the guest is position independent everywhere (S7 of the loader rules; part of the L1 verdict).

    gate_pcrel.py --elf <linked guest elf> --objects <dir of the objects that were linked>

A PC-relative relocation (adrp/adr/literal load/branch) whose symbol is absolute in the linked ELF is right
only at the link base: a relocated guest moves the PC and not the target.  An image address must be reached
through the GOT (relocated by value) or as an immediate.  toolchain/game/pcrel_abs.py finds such sites.
Gate: 0 sites.  A "pinned" guest (one that may only run at its link base) is not a state the clean branch holds.
Exit status 1 otherwise.  Counts only.
"""
import argparse, glob, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, '..', 'toolchain', 'game', 'pcrel_abs.py')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--elf', required=True)
    ap.add_argument('--objects', required=True)
    ap.add_argument('--readelf', default='aarch64-linux-gnu-readelf')
    ap.add_argument('--nm', default='aarch64-linux-gnu-nm')
    a = ap.parse_args()
    objs = sorted(glob.glob(os.path.join(a.objects, '*.o')))
    rsp = os.path.join(os.environ.get('TMPDIR', '/tmp'), 'gate_pcrel.%d.rsp' % os.getpid())
    with open(rsp, 'w') as fh:
        fh.write('\n'.join(objs))
    try:
        out = subprocess.run([sys.executable, TOOL, a.elf, a.readelf, a.nm, '@' + rsp], capture_output=True, text=True)
    finally:
        os.unlink(rsp)
    m = re.search(r'pc-relative references to absolute symbols: (\d+)', out.stdout)
    if out.returncode or not m:
        sys.exit('pcrel_abs.py failed: ' + (out.stderr or out.stdout)[-300:])
    print(out.stdout.strip().splitlines()[-1])
    sys.exit(1 if int(m.group(1)) else 0)


if __name__ == '__main__':
    main()
