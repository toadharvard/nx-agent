"""Dispatcher tables for rt/rt.c: the sorted table (reference, binary search) and a minimal-cost perfect hash (hash-and-displace).
   idx = ((a ^ g[(a*K1)>>(32-NBITS)]) * K2) >> (32-TBITS); the slot holds the key, empty slots the key 0xffffffff.
   build(keys) -> dict(k1, k2, nbits, tbits, g, slots) ; slots[i] = key or None."""
import math, random
M = 0xffffffff


def build(keys, seed=1, load=0.4):
    keys = sorted(set(keys))
    n = len(keys)
    tbits = max(3, math.ceil(math.log2(n / load)))
    nbits = max(1, math.ceil(math.log2(max(n, 2) / 4)))
    for att in range(200):
        r = random.Random(seed * 1000 + att)
        k1, k2 = r.getrandbits(32) | 1, r.getrandbits(32) | 1
        bk = {}
        for a in keys:
            bk.setdefault(((a * k1) & M) >> (32 - nbits), []).append(a)
        slots = [None] * (1 << tbits)
        g = [0] * (1 << nbits)
        ok = True
        for b, ks in sorted(bk.items(), key=lambda t: -len(t[1])):
            for d in range(65536):
                idx = [(((a ^ d) * k2) & M) >> (32 - tbits) for a in ks]
                if len(set(idx)) == len(idx) and all(slots[i] is None for i in idx):
                    g[b] = d
                    for a, i in zip(ks, idx):
                        slots[i] = a
                    break
            else:
                ok = False
                break
        if ok:
            return dict(k1=k1, k2=k2, nbits=nbits, tbits=tbits, g=g, slots=slots, attempts=att + 1)
    raise RuntimeError('no perfect hash found')


def lookup(h, a):
    i = (((a ^ h['g'][((a * h['k1']) & M) >> (32 - h['nbits'])]) * h['k2']) & M) >> (32 - h['tbits'])
    return i if h['slots'][i] == a else None


def table_c(syms, phf=None):
    """syms: {address: symbol}. -> C text of the entry table (and the displacement table for the hash)"""
    out = ['typedef unsigned int u32; typedef unsigned short u16; struct fnvl_ent { u32 a; void (*f)(void); };']
    out += ['extern void %s(void);' % s for s in sorted(syms.values())]
    if phf is None:
        ks = sorted(syms)
        out.append('const struct fnvl_ent fnvl_tab[] = {%s};' % ','.join('{0x%x,%s}' % (a, syms[a]) for a in ks))
        out.append('const u32 fnvl_ntab = %d;' % len(ks))
    else:
        out.append('static void nop(void) {}')
        out.append('const struct fnvl_ent fnvl_tab[] = {%s};' % ','.join('{0x%x,%s}' % (a, syms[a]) if a is not None else '{0xffffffff,nop}' for a in phf['slots']))
        out.append('const u32 fnvl_ntab = %d;' % len(phf['slots']))
        out.append('const u16 fnvl_g[] = {%s};' % ','.join(map(str, phf['g'])))
    return '\n'.join(out) + '\n'


def table_asm(syms, phf):
    """position-independent dispatcher table for rt.c -DFNVL_PHF_REL (tools/liftall.py): AArch64 assembly, section .rodata.fnvl_disp.
    slot i = {u32 key, s32 L_sym - fnvl_disp_table} (empty slot: a key that does not hash to it), then fnvl_g[] (u16). No absolute code address appears."""
    out = ['.section .rodata.fnvl_disp,"a"', '.balign 8', '.globl fnvl_disp_table', 'fnvl_disp_table:']
    def idx(a): return (((a ^ phf['g'][((a * phf['k1']) & M) >> (32 - phf['nbits'])]) * phf['k2']) & M) >> (32 - phf['tbits'])
    def empty(i):       # the key of an empty slot must not be an address that hashes to this slot, or a lookup of that address would hit it
        return next(k for k in range(M, M - 64, -1) if idx(k) != i)
    out += [' .long 0x%x, %s - fnvl_disp_table' % (a, syms[a]) if a is not None else ' .long 0x%x, 0' % empty(i) for i, a in enumerate(phf['slots'])]
    out += ['.globl fnvl_g', '.balign 2', 'fnvl_g:'] + [' .short %d' % g for g in phf['g']]
    return '\n'.join(out) + '\n'
