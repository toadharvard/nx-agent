#!/usr/bin/env python3
"""Data fingerprints for the data image: bytes the decomp says are at given addresses of .rdata/.data.

usage: mkdataprobes.py build/units.json OUT.txt [--max N]

Two sources, both spelled out by MSVC decorated names in build/units.json:
  * string literals  ??_C@_0<len>@<hash>@<chars>@  -> the leading characters of the string at that address
    (MSVC encodes at most 32 characters: letters, digits, _ and $ as themselves, ?0..?9 for , / \\ : . space
    newline tab ' -, ?$XY for any byte; anything else ends the probe);
  * RTTI type descriptors  ??_R0<type>@8  -> the name ".<type>" at address + 8.
They verify the data sections without the code (the Steam exe's .text is encrypted on disk): an image whose
probes match at >= 90 % is FNV 1.4.0.525 data at the original addresses. Output lines: "0x<va> <hex bytes>".
Derived from the decomp: write it under build/, never commit it.
"""
import argparse
import json

SPECIAL = {str(i): c for i, c in enumerate(b",/\\:. \n\t'-")}
SAFE = set(b'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$')


def decode_literal(name):
    """bytes the literal starts with, or None. Only narrow strings (_0)."""
    if not name.startswith('??_C@_0'):
        return None
    rest = name[7:]                          # length (one digit, or hex letters and @), hash@, characters
    if not rest:
        return None
    if rest[0].isdigit():
        rest = rest[1:]
    elif '@' in rest:
        rest = rest.split('@', 1)[1]
    else:
        return None
    if '@' not in rest:
        return None
    enc = rest.split('@', 1)[1]
    out, i = bytearray(), 0
    while i < len(enc):
        c = enc[i]
        if c == '@':
            break
        if c != '?':
            if ord(c) not in SAFE:
                break
            out.append(ord(c))
            i += 1
        elif i + 1 < len(enc) and enc[i + 1] in SPECIAL:
            out.append(SPECIAL[enc[i + 1]])
            i += 2
        elif i + 3 < len(enc) and enc[i + 1] == '$' and 'A' <= enc[i + 2] <= 'P' and 'A' <= enc[i + 3] <= 'P':
            out.append((ord(enc[i + 2]) - 65) * 16 + ord(enc[i + 3]) - 65)
            i += 4
        else:
            break
    return bytes(out)


def probes(units):
    seen = {}
    for u in units['units']:
        for name, v in u.get('symbols', {}).items():
            va = int(v, 16)
            if va < 0x00fdf000:
                continue
            if name.startswith('??_C@_0'):
                b = decode_literal(name)
                if b and len(b) >= 4:
                    seen.setdefault(va, b)
            elif name.startswith('??_R0') and name.endswith('@8'):
                seen.setdefault(va + 8, ('.' + name[5:-2]).encode('latin-1'))
    return sorted(seen.items())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('units')
    ap.add_argument('out')
    ap.add_argument('--max', type=int, default=0)
    a = ap.parse_args()
    with open(a.units) as f:
        p = probes(json.load(f))
    if a.max:
        p = p[::max(1, len(p) // a.max)][:a.max]
    with open(a.out, 'w') as f:
        for va, b in p:
            f.write('0x%08x %s\n' % (va, b.hex()))
    print('data probes: %d' % len(p))


if __name__ == '__main__':
    main()
