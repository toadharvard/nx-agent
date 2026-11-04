#!/usr/bin/env python3
"""check_native.py - the facts of the native build that S15-simd overrides copy, checked against a link's rewritten
objects (docs/PERF.md 11.6).

    python3 -I tests/s15/simd/check_native.py <link dir>      # <link>/fixed/*.o (farm link/<c12>); skips without it

An override that calls a virtual function names its native vtable slot, which the decomp's own object computes from
the class at compile time. If a later change moves the slot (vftables from the exe, a new virtual), the override
must change with it. Each row: unit object, function alias, a pattern its disassembly must contain. Prints ok/FAIL
per row; exit 1 on a failure. Reads the private link, prints names and offsets only.
"""
import os
import re
import subprocess
import sys

OBJDUMP = os.environ.get('OBJDUMP', 'aarch64-linux-gnu-objdump')

# (override, unit object, function alias, regex over its disassembly, what it means)
FACTS = [
    ('cull_worker_00a694e0.c', 'src_gamebryo2.2_corelibs_nimain_nicullingprocess.cpp.o', '__fnv_00a694e0',
     r'ldr\s+w(\d+), \[x\d+, #212\]\s*\n[^\n]*blr\s+x\1\b', 'the culling callback is vtable slot 53 (+212)'),
]


def body(obj, alias):
    """disassembly of the function at `alias` (objdump labels it with one of its names, not necessarily this one)"""
    nm = subprocess.run([OBJDUMP.replace('objdump', 'nm'), '-S', obj], capture_output=True, text=True).stdout
    m = re.search(r'^([0-9a-f]+) (?:[0-9a-f]+ )?T %s$' % re.escape(alias), nm, re.M)
    if not m:
        return None
    sized = re.search(r'^%s ([0-9a-f]+) T ' % m.group(1), nm, re.M)   # the alias has no size: a name at its address
    if not sized:
        return None
    lo = int(m.group(1), 16)
    out = subprocess.run([OBJDUMP, '-d', '--no-show-raw-insn', '--start-address=%d' % lo,
                          '--stop-address=%d' % (lo + int(sized.group(1), 16)), obj], capture_output=True, text=True)
    return out.stdout


def main():
    if len(sys.argv) < 2 or not os.path.isdir(os.path.join(sys.argv[1], 'fixed')):
        print('skip: no link directory given')
        return 0
    fail = 0
    for ovr, obj, alias, rx, what in FACTS:
        path = os.path.join(sys.argv[1], 'fixed', obj)
        b = body(path, alias) if os.path.exists(path) else None
        ok = b is not None and re.search(rx, b) is not None
        print('%s %s: %s' % ('ok  ' if ok else 'FAIL', ovr, what))
        fail += not ok
    print('%d failed' % fail)
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
