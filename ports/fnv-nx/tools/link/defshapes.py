#!/usr/bin/env python3
"""defshapes.py - sub-classify the `code-defmissing` addresses that synthesized vtable objects (`<unit>#vtables`)
reference, by the shape of the exe code at the address (W15, S2-bodies).

`code-defmissing` says only that units.json places a function at the address and no native body exists. The exe says
what it is. Shapes (one per address, first match):

    thunk>native         MSVC adjustor thunk (`sub ecx, n; jmp`, exevft.thunk) whose chain ends at a native body:
                         fnvlink.adjustor_thunks gives `__fnv_<a>` a stub (this -= n, then the body)
    thunk>sdd-global     thunk into an exe ??_G that destroys through a native callee and frees with the global
                         operator delete (`push esi; call delete; add esp, 4`): D26 machinery applies
    thunk>sdd-sized      the same into a ??_G that frees through the Havok sized allocator (`call router; ...
                         call [vtbl+8](this, size)`): no global delete, no D26 stand-in
    sdd-global/-sized    the ??_G itself (scalar deleting destructor: `push esi; mov esi, ecx; call D; test
                         byte [esp+8], 1; je`), D native
    sdd-nocallee         a ??_G-shaped body whose destructor call has no native body
    function             a frame-prologue function with padding before it and no thunk shape: a real function the
                         decomp has no body for
    other                anything else (stays a trap)

    defshapes.py refs.tsv addrmap.tsv FalloutNV.exe [out.tsv]

Reads the exe, never copies it; prints names, addresses and counts only.
"""
import collections
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import exevft  # noqa: E402

NATIVE_KINDS = ('F', 'G', 'C', 'V', 'X')


def _bytes(path, addr, n):
    pe, data = exevft._text(path)
    try:
        off = pe.rva_to_off(addr - pe.image_base, n)
    except Exception:
        return None
    return None if off is None else data[off:off + n]


def _rel(b, i, at):
    return (at + i + 5 + struct.unpack_from('<i', b, i + 1)[0]) & 0xffffffff


# push esi; mov esi, ecx; call D; test byte [esp+8], 1; je +n
_SDD_HEAD = bytes.fromhex('568bf1e8')
_SDD_TEST = bytes.fromhex('f644240801')
# the Havok sized free: call router; movzx edx, word [esi+4]; mov ecx, [eax+0x10]; mov eax, [ecx]; mov eax, [eax+8];
# push edx; push esi; call eax; (then mov eax, esi; pop esi; ret 4)
_SIZED = bytes.fromhex('0fb756048b48108b018b40085256ffd0')


def sdd(path, addr):
    """(callee D, 'global' | 'sized' | 'other') of an exe scalar deleting destructor at addr, else None."""
    b = _bytes(path, addr, 48)
    if b is None or b[:4] != _SDD_HEAD or b[8:13] != _SDD_TEST or b[13] != 0x74:
        return None
    d = _rel(b, 3, addr)
    body = b[15:]       # after `je rel8` (the taken branch skips the free)
    if body[:1] == b'\x56' and body[1:2] == b'\xe8' and body[6:9] == b'\x83\xc4\x04':   # push esi; call; add esp, 4
        return d, 'global'
    if body[:1] == b'\xe8' and body[5:5 + len(_SIZED)] == _SIZED:
        return d, 'sized'
    return d, 'other'


def classify(path, addr, native):
    """-> (shape, detail)"""
    n, t, hops = 0, addr, 0
    chain = []
    while hops < 3:
        th = exevft.thunk(path, t)
        if th is None:
            break
        n, t, hops = n + th[0], th[1], hops + 1
        chain.append(t)
        if t in native:
            return 'thunk>native', (n, t)
    s = sdd(path, t)
    if s is not None:
        d, how = s
        tag = 'sdd-' + how if d in native else 'sdd-nocallee'
        return (tag if hops == 0 else 'thunk>' + tag, (n, t, d))
    if hops:
        return 'other', (n, t)
    b = _bytes(path, addr - 1, 49)
    if b is not None and b[0] in (0xcc, 0xc3) and b[1:2] == b'\x55' and b[2:4] == b'\x8b\xec':
        return 'function', None
    return 'other', None


def read_tsv(refs, addrmap):
    native, want = set(), collections.defaultdict(set)
    for line in open(addrmap, encoding='utf-8', errors='replace'):
        f = line.rstrip('\n').split('\t')
        if f[0][:1] != '#' and len(f) > 3 and f[1] in NATIVE_KINDS and f[2]:
            native.add(int(f[0], 16))
    for line in open(refs, encoding='utf-8', errors='replace'):
        f = line.rstrip('\n').split('\t')
        if len(f) > 5 and f[2] == 'code-defmissing' and f[0].endswith('#vtables'):
            want[int(f[4], 16)].add(f[1])
    return native, want


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    native, want = read_tsv(argv[0], argv[1])
    rows = {a: classify(argv[2], a, native) for a in sorted(want)}
    cnt = collections.Counter(s for s, _ in rows.values())
    out = open(argv[3], 'w') if len(argv) > 3 else sys.stdout
    out.write('#address\tshape\tn\ttarget\tdestructor\treferences\n')
    for a, (s, det) in rows.items():
        n = '' if det is None else '%d' % det[0] if len(det) > 1 else ''
        tgt = '' if det is None or len(det) < 2 else '%08x' % det[1]
        d = '%08x' % det[2] if det is not None and len(det) > 2 else ''
        out.write('%08x\t%s\t%s\t%s\t%s\t%d\n' % (a, s, n, tgt, d, len(want[a])))
    for s, k in cnt.most_common():
        sys.stderr.write('%-18s %d\n' % (s, k))
    sys.stderr.write('%-18s %d\n' % ('total', sum(cnt.values())))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
