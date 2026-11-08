#!/usr/bin/env python3
"""tests/s13/test_litstore.py - literal-store (D24, difffuzz-20), on hand-made write maps (no decomp content).

A function stores the address of a string literal into the object it builds. The oracle's literal sits in the pinned
pool (0x02f0xxxx), the native unit's copy in its private image (0x0a0xxxxx): the two words differ by design. The
runner compares the pointees (runner.literal_store_words):

  same      equal C strings: nothing reported
  empty     an empty string falls back to the first 8 raw bytes (a __real@ constant): equal -> nothing, different -> kept
  wrong     different strings: the divergence stays, a hard mem-write
  field     an equal literal word beside a differing integer field: the field is reported, not the literal word
  notptr    a word in the pool range on one side only (the other not an image address) stays a divergence
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.environ.get('FNVNX_DFDIR') or os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13litstore'))

import arm64emu   # noqa: E402
import conv       # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402
import x86emu     # noqa: E402

FAIL = []
B = 0x03000000
PX, PA = 0x02f00100, 0x0a081000          # the oracle's pool literal, the native's copy


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def word(addr, v):
    return {addr + i: b for i, b in enumerate(struct.pack('<I', v))}


def res(*ws):
    a, b = x86emu.Result(), arm64emu.Result()
    a.ret = b.ret = None
    a.writes, b.writes = {}, {}
    for (x, y) in ws:
        a.writes.update(x)
        b.writes.update(y)
    return a, b


def mem(table):
    def rd(a, n):
        for base, blob in table.items():
            if base <= a < base + len(blob):
                return (blob[a - base:] + b'\xaa' * 512)[:n]
        raise ValueError('unmapped')
    return rd


def run(sx, sa, extra=()):
    cv = conv.Conv('?f@Foo@@QAEXXZ', 'test')
    w = world.World(world.Case(seed=1), {}, {})
    rx, ra = res((word(B, PX), word(B, PA)), *extra)
    rd = (mem({PX: sx}), mem({PA: sa}))
    return runner.compare(cv, rx, ra, w, readers=rd)


def main():
    d = run(b'Data\\Meshes\\a.nif\0', b'Data\\Meshes\\a.nif\0')
    check(d is None, 'equal strings: no divergence (%s)' % d)
    d = run(b'Data\\Meshes\\a.nif\0', b'Data\\Meshes\\b.nif\0')
    check(d is not None and d['kind'] == 'mem-write' and not d.get('soft') and d['addr'] == '%08x' % (B + 1),
          'different strings: the store stays a hard divergence (%s)' % (d and d.get('kind')))
    d = run(b'\0' + struct.pack('<f', 1.0) + b'\0\0\0', b'\0' + struct.pack('<f', 1.0) + b'\0\0\0')
    check(d is None, 'empty string, equal raw constant: none (%s)' % d)
    d = run(b'\0' + struct.pack('<f', 1.0) + b'\0\0\0', b'\0' + struct.pack('<f', 2.0) + b'\0\0\0')
    check(d is not None and d['kind'] == 'mem-write', 'empty string, different raw constant: kept (%s)' % d)
    d = run(b'abc\0', b'abc\0', extra=((word(B + 4, 5), word(B + 4, 6)),))
    check(d is not None and d['addr'] == '%08x' % (B + 4) and d['ndiff'] == 1,
          'an equal literal beside a differing field: the field is reported (%s)' % (d and d.get('addr')))
    cv = conv.Conv('?f@Foo@@QAEXXZ', 'test')
    w = world.World(world.Case(seed=1), {}, {})
    rx, ra = res((word(B, PX), word(B, 0x03000040)))
    d = runner.compare(cv, rx, ra, w, readers=(mem({PX: b'abc\0'}), mem({0x03000040: b'abc\0'})))
    check(d is not None and d['kind'] == 'mem-write', 'a native word outside its image is not a literal copy')
    d = runner.compare(cv, *res((word(B, PX), word(B, PA))), w)
    check(d is not None and d['kind'] == 'mem-write', 'no readers: compared as before')
    print('FAIL: %d' % len(FAIL) if FAIL else 'all ok')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
