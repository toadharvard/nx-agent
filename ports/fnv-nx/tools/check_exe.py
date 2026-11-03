#!/usr/bin/env python3
"""check_exe.py - is this FalloutNV.exe the build the decomp (and therefore fnv-nx) is made for?

    python3 tools/check_exe.py <FalloutNV.exe> [<fnv_decomp checkout>/build/image_check.json]

Runs locally on your own copy; nothing is uploaded and nothing from the exe is printed except hashes and section
facts. Checks:
  * PE32 headers, image base, section table (names, virtual ranges, sizes);
  * the decomp's code fingerprints (image_check.json: address, size, SHA-256) - all match means the same build;
  * whether .text looks encrypted (SteamStub adds a `.bind` section; encrypted code has ~8 bits/byte entropy);
  * whether the data sections fnv-nx needs (.rdata/.data) look plain (entropy, RTTI names present).
Exit code 0 = same build as the decomp; 1 = different build or unreadable.
"""
import hashlib
import json
import math
import os
import struct
import sys
from collections import Counter

def entropy(b):
    if not b: return 0.0
    n = len(b)
    return -sum(c / n * math.log2(c / n) for c in Counter(b).values())

def main():
    exe = sys.argv[1]
    probes_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..', 'ext', 'fnv_decomp', 'build', 'image_check.json')
    data = open(exe, 'rb').read()
    print('file: %s  size: %d  sha256: %s' % (os.path.basename(exe), len(data), hashlib.sha256(data).hexdigest()))
    if data[:2] != b'MZ':
        print('not a PE file'); return 1
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    if data[pe:pe + 4] != b'PE\0\0':
        print('no PE signature'); return 1
    machine, nsec, _, _, _, optsize, _ = struct.unpack_from('<HHIIIHH', data, pe + 4)
    opt = pe + 24
    magic = struct.unpack_from('<H', data, opt)[0]
    image_base = struct.unpack_from('<I', data, opt + 28)[0]
    print('machine: %#x  optional header magic: %#x  image base: %#x' % (machine, magic, image_base))
    secs = []
    for i in range(nsec):
        o = opt + optsize + 40 * i
        name = data[o:o + 8].rstrip(b'\0').decode('latin-1')
        vsize, va, rsize, rptr = struct.unpack_from('<IIII', data, o + 8)
        secs.append((name, image_base + va, vsize, rptr, rsize))
    print('\nsection   virtual range            raw size   entropy')
    for name, va, vsize, rptr, rsize in secs:
        print('%-8s  %#010x-%#010x  %9d   %.2f' % (name, va, va + vsize, rsize, entropy(data[rptr:rptr + rsize])))

    def read_va(addr, size):
        for name, va, vsize, rptr, rsize in secs:
            if va <= addr and addr + size <= va + max(vsize, rsize):
                off = rptr + (addr - va)
                return data[off:off + size] if addr - va + size <= rsize else None
        return None

    names = {s[0] for s in secs}
    if '.bind' in names:
        print('\n.bind section present: SteamStub-wrapped exe, code is encrypted on disk.')
    rdata = next((s for s in secs if s[0] == '.rdata'), None)
    if rdata:
        blob = data[rdata[3]:rdata[3] + rdata[4]]
        print('.rdata RTTI type names (".?AV"): %d -> %s' % (blob.count(b'.?AV'),
              'data sections look plain' if blob.count(b'.?AV') > 100 else 'data sections do NOT look plain'))

    try:
        spec = json.load(open(probes_path))
    except OSError:
        print('\nno image_check.json at %s; pass its path as the second argument' % probes_path); return 1
    ok = 0
    for address, size, digest in spec['probes']:
        b = read_va(int(address, 16), size)
        ok += b is not None and hashlib.sha256(b).hexdigest() == digest
    total = len(spec['probes'])
    print('\ncode fingerprints of %s: %d / %d match' % (spec.get('release', '?'), ok, total))
    if ok == total:
        print('=> same build as the decomp.'); return 0
    if '.bind' in names and ok == 0:
        print('=> probably the right build but encrypted code; fnv-nx only needs the data sections, see above.')
    else:
        print('=> different build (or encrypted code); fnv-nx would need an address remap for this exe.')
    return 1

if __name__ == '__main__':
    sys.exit(main())
