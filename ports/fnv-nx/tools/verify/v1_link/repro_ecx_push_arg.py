#!/usr/bin/env python3
"""repro_ecx_push_arg.py - V1-link defect reproducer (C3, round 4): the definition register-read analysis that
`fnvlink.py resolve --msvc-code` uses (vslots.Code.entry_use) calls an incoming ecx that is pushed as a callee's
stack argument 'push' (a reserved stack word), so the pair is judged ecx-unused: no longer listed x86-inconsistent,
bridged or bound as if the definition never read it. The farm link link/9a0243c7eabe has this for 0x00e89ad0
(`__fastcall f(int)`: push ecx; call g(int)) and 0x00a5d450 (a thiscall member pushing `this` as an argument).

    repro_ecx_push_arg.py <work dir>       # exit 1 while the defect is present

Hand-made i386 COFF object (no decomp content, machine code written here), read by the code under test through a
units.json of three functions:
  f   void __fastcall f(int)            push ecx / call g / pop ecx / ret          ecx is g's argument: used
  m   void __thiscall T::m(unsigned, void *)  mov eax,[esp+4] / push eax / push ecx / mov ecx,[esp+0x10] /
                                        mov ecx,[ecx] / call V::Create(unsigned, unsigned) / ret 8   this is pushed
  r   void __thiscall T::r(void)        push ecx / push 1 / call h(int) / add esp,4 / pop ecx / ret
                                        (MSVC's reserve: the pushed word is not h's argument): unused - control
V1's own analysis (coff_x86 with argument counting) is printed beside it.
"""
import json
import os
import struct
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'link'))
import coff_x86  # noqa: E402

FUNCS = [
    # (decorated name, code bytes, [(offset of the rel32 field, target symbol)])
    ('?f@@YIXH@Z', bytes.fromhex('51' 'e800000000' '59' 'c3'), [(2, '?g@@YAXH@Z')]),
    ('?m@T@@QAEXIPAX@Z', bytes.fromhex('8b442404' '50' '51' '8b4c2410' '8b09' 'e800000000' 'c20800'),
     [(13, '?Create@V@@QAEXII@Z')]),
    ('?r@T@@QAEXXZ', bytes.fromhex('51' '6a01' 'e800000000' '83c404' '59' 'c3'), [(4, '?h@@YAXH@Z')]),
]


def coff(path):
    text, rels, syms, strtab = b'', [], [], b''
    starts = []
    for name, code, rr in FUNCS:
        starts.append(len(text))
        for off, tgt in rr:
            rels.append((len(text) + off, tgt))
        text += code
    names = [n for n, _, _ in FUNCS] + sorted({t for _, t in rels})
    index = {}
    sym_bytes = b''
    for k, n in enumerate(names):
        if len(n) <= 8:
            nm = n.encode().ljust(8, b'\0')
        else:
            nm = b'\0\0\0\0' + struct.pack('<I', 4 + len(strtab))
            strtab += n.encode() + b'\0'
        defined = k < len(FUNCS)
        sym_bytes += nm + struct.pack('<IhHBB', starts[k] if defined else 0, 1 if defined else 0, 0x20, 2, 0)
        index[n] = k
    rel_bytes = b''.join(struct.pack('<IIH', va, index[t], 0x14) for va, t in rels)
    hdr = 20
    sec = 40
    raw = hdr + sec
    relptr = raw + len(text)
    symptr = relptr + len(rel_bytes)
    out = struct.pack('<HHIIIHH', 0x14c, 1, 0, symptr, len(names), 0, 0)
    out += b'.text\0\0\0' + struct.pack('<IIIIIIHHI', 0, 0, len(text), raw, relptr, 0, len(rels), 0, 0x60500020)
    out += text + rel_bytes + sym_bytes + struct.pack('<I', 4 + len(strtab)) + strtab
    open(path, 'wb').write(out)
    return starts


def main():
    work = sys.argv[1]
    os.makedirs(work, exist_ok=True)
    starts = coff(os.path.join(work, 'u0.obj'))
    units = [{'source': 'src/v1.cpp', 'functions': [
        {'address': '0x%08x' % (0x00c30000 + k * 0x10), 'symbol': n, 'size': len(c)}
        for k, (n, c, _) in enumerate(FUNCS)], 'symbols': {}}]
    json.dump({'units': units}, open(os.path.join(work, 'units.json'), 'w'))
    import vslots
    code = vslots.Code(types.SimpleNamespace(units=units), work)
    takes = {'?g@@YAXH@Z': set(), '?h@@YAXH@Z': set(), '?Create@V@@QAEXII@Z': {'ecx'}}
    words = {'?g@@YAXH@Z': 1, '?h@@YAXH@Z': 1, '?Create@V@@QAEXII@Z': 2}
    c = coff_x86.Coff(os.path.join(work, 'u0.obj'))
    want = {'?f@@YIXH@Z': 'used', '?m@T@@QAEXIPAX@Z': 'used', '?r@T@@QAEXXZ': 'unused'}
    bad = 0
    for k, (n, _, _) in enumerate(FUNCS):
        tool = code.entry_use(0x00c30000 + k * 0x10, lambda s, slot=False: takes.get(s))
        f = c.func(n)
        mine = coff_x86.entry_reads(f[0], f[1], lambda s: takes.get(s), c.labels(n), lambda s: words.get(s))
        tool_used = tool is not None and tool['ecx'] in ('read', 'pass')
        ok = tool_used == (want[n] == 'used')
        bad += 0 if ok else 1
        print('%-6s %-22s ecx %-6s  code under test: %-5s  V1: %s' % ('ok' if ok else 'DEFECT', n, want[n],
                                                                    tool and tool['ecx'], mine['ecx']))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
