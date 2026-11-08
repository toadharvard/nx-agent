"""_vlit_vftables.py - the vftable table of prepare rule VLIT1's exe form (VLIT2, tools/rules/s1_compile.py).

    python3 tools/rules/_vlit_vftables.py --exe FalloutNV.exe -o <dir>/vlit.json

For every vftable of the player's exe that RTTI finds (tools/link/exevft.scan): its slot 0 and the COL offset (the
base subobject the vftable serves). VLIT2 maps a 1-3 character string literal stored as a vptr (the decompiler
printed the address's bytes as text: TESObjectARMA's constructor 0x00511f60 stores `" rH"` = 0x00487220 at
this + 0x68) to the one vftable whose slot 0 is that address at that offset (0x0102a28c, COL offset 0x68).

The output is exe-derived. It goes to the private fnv-nx-build branch recon/vlit_vftables, which tools/farm/recon.sh
puts into FNVNX_RECON, never into nx-agent. Format: {"format": "fnvnx-vlit/1", "exe_sha256": <sha>, "slot0":
{"<slot0 hex>": [["<vftable hex>", <COL offset>], ...]}, "class": {"<vftable hex>": <RTTI TypeDescriptor>}}.
Two vftables can share slot 0 and offset (TESObjectARMA's and TESObjectARMO's at +0x68); VLIT2 then takes the one
whose class is the class of the other vftables the same function stores.
"""
import argparse
import collections
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'link'))
import exevft  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe', required=True)
    ap.add_argument('-o', '--out', required=True)
    a = ap.parse_args()
    vft = exevft.scan(a.exe)
    by = collections.defaultdict(list)
    cls = {}
    for v, x in sorted(vft.items()):
        cls['%08x' % v] = x.td
        if x.slots and exevft.TEXT[0] <= x.slots[0] < exevft.TEXT[1]:
            by['%08x' % x.slots[0]].append(['%08x' % v, x.off])
    sha = hashlib.sha256(open(a.exe, 'rb').read()).hexdigest()
    json.dump({'format': 'fnvnx-vlit/1', 'exe_sha256': sha, 'slot0': by, 'class': cls}, open(a.out, 'w'), indent=0, sort_keys=True)
    print('vftables %d, distinct slot 0 %d' % (sum(len(v) for v in by.values()), len(by)))


if __name__ == '__main__':
    main()
