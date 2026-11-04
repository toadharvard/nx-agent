#!/usr/bin/env python3
"""setting_names.py - name the game's INI/game settings by address, from the player's FalloutNV.exe (stream S15-perf).

    setting_names.py <FalloutNV.exe> <decomp or prepared tree> [--grep REGEX] [--addr 0x011c3ea4,...]

The decomp constructs every setting object in src/generated/*initializers*.cpp as
`GlobalObject_<ctor> g_<object address>(g_<object address>_arg0, <default>)` (or `Setting_... g_<addr>(s_<addr>, ...)`):
the name is image data the decomp does not carry (units.json binds `g_<addr>_arg0` to its .rdata address). With the
exe, this tool reads that string (directly, or through the `const char * const` pointer the address holds) and prints
one row per setting: object address, constructor class, default from the source, name. docs/PERF.md uses it to
confirm the threading settings whose names the audit could only infer (`--grep 'Thread|HWThread|Multibound'`).

Untested on the real exe (not in the dev container); the synthetic S14 image has no string contents. Output is
derived from the player's exe and the decomp: local only, never committed.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'loader', 'tools'))
import fnvnx_pe  # noqa: E402

DECL = re.compile(r'^\s*(GlobalObject_[0-9a-f]{8}|Setting_\w+)\s+g_([0-9a-f]{8})\((\w+)(?:,\s*([^)]*))?\);')


def printable(b):
    return b and all(32 <= c < 127 for c in b)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('exe')
    ap.add_argument('tree')
    ap.add_argument('--grep')
    ap.add_argument('--addr', help='comma-separated object addresses to report')
    a = ap.parse_args()
    img = fnvnx_pe.Image(fnvnx_pe.PE(open(a.exe, 'rb').read()))
    raw = json.load(open(os.path.join(a.tree, 'build', 'units.json')))
    # every decorated data name -> address (the symbol tables live next to the units)
    sym = {}

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, str) and k.startswith('?') and re.match(r'^0x[0-9a-f]+$', v):
                    m = re.match(r'^\?(\w+)@@', k)
                    if m:
                        sym.setdefault(m.group(1), int(v, 16))
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(raw)
    want = {int(x, 16) for x in a.addr.split(',')} if a.addr else None
    gen = os.path.join(a.tree, 'src', 'generated')
    rows = []
    for fn in sorted(os.listdir(gen)):
        if 'initializers' not in fn:
            continue
        for line in open(os.path.join(gen, fn), errors='replace'):
            m = DECL.match(line)
            if not m:
                continue
            cls, obj, arg, dflt = m.group(1), int(m.group(2), 16), m.group(3), (m.group(4) or '').strip()
            if want and obj not in want:
                continue
            at = sym.get(arg)
            if at is None:
                ms = re.match(r'^s_([0-9a-f]{8})$', arg)
                at = int(ms.group(1), 16) if ms else None
            name = '?'
            if at is not None:
                s = img.string(at, 128)
                if s and printable(s[0]):
                    name = s[0].decode()
                else:
                    p = img.read32(at)
                    s = img.string(p, 128) if p else None
                    if s and printable(s[0]):
                        name = s[0].decode()
            rows.append((obj, cls, dflt, name))
    rx = re.compile(a.grep, re.I) if a.grep else None
    for obj, cls, dflt, name in sorted(set(rows)):
        if rx and not rx.search(name):
            continue
        print('%#010x\t%s\t%s\t%s' % (obj, cls, dflt, name))


if __name__ == '__main__':
    main()
