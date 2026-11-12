"""Dispatcher table for rt/rt.c: a minimal perfect hash (hash-and-displace).
   idx = ((a ^ g[(a*K1)>>(32-NBITS)]) * K2) >> (32-TBITS); the slot holds the key, empty slots the key 0xffffffff.
   build(keys) -> dict(k1, k2, nbits, tbits, g, slots) ; slots[i] = key or None."""
import math
M = 0xffffffff


class MT19937:
    """The Mersenne Twister of CPython's random.Random(int seed), bit for bit (init_by_array of the 32-bit words of the seed, genrand_uint32): the build must not import
    `random` (puregate bans it), and the published tables stay byte-identical. getrandbits(k <= 32) = next32() >> (32 - k)."""
    def __init__(self, seed):
        key = []; n = abs(seed)
        while True:
            key.append(n & M); n >>= 32
            if not n: break
        mt = [0] * 624; mt[0] = 19650218
        for i in range(1, 624): mt[i] = (1812433253 * (mt[i - 1] ^ (mt[i - 1] >> 30)) + i) & M
        i, j = 1, 0
        for _ in range(max(624, len(key))):
            mt[i] = ((mt[i] ^ ((mt[i - 1] ^ (mt[i - 1] >> 30)) * 1664525)) + key[j] + j) & M
            i += 1; j += 1
            if i >= 624: mt[0] = mt[623]; i = 1
            if j >= len(key): j = 0
        for _ in range(623):
            mt[i] = ((mt[i] ^ ((mt[i - 1] ^ (mt[i - 1] >> 30)) * 1566083941)) - i) & M
            i += 1
            if i >= 624: mt[0] = mt[623]; i = 1
        mt[0] = 0x80000000
        self.mt, self.i = mt, 624
    def next32(self):
        mt = self.mt
        if self.i >= 624:
            for k in range(624):
                y = (mt[k] & 0x80000000) | (mt[(k + 1) % 624] & 0x7fffffff)
                mt[k] = mt[(k + 397) % 624] ^ (y >> 1) ^ (0x9908b0df if y & 1 else 0)
            self.i = 0
        y = mt[self.i]; self.i += 1
        y ^= y >> 11; y ^= (y << 7) & 0x9d2c5680; y ^= (y << 15) & 0xefc60000; y ^= y >> 18
        return y & M
    def getrandbits32(self): return self.next32()


def build(keys, seed=1, load=0.4):
    keys = sorted(set(keys))
    n = len(keys)
    tbits = max(3, math.ceil(math.log2(n / load)))
    nbits = max(1, math.ceil(math.log2(max(n, 2) / 4)))
    for att in range(200):
        r = MT19937(seed * 1000 + att)
        k1, k2 = r.getrandbits32() | 1, r.getrandbits32() | 1
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


def table_asm(syms, phf):
    """position-independent dispatcher table for rt.c (tools/liftgen.py): AArch64 assembly, section .rodata.fnvl_disp.
    slot i = {u32 key, s32 L_sym - fnvl_disp_table} (empty slot: a key that does not hash to it), then fnvl_g[] (u16). No absolute code address appears."""
    out = ['.section .rodata.fnvl_disp,"a"', '.balign 8', '.globl fnvl_disp_table', 'fnvl_disp_table:']
    def idx(a): return (((a ^ phf['g'][((a * phf['k1']) & M) >> (32 - phf['nbits'])]) * phf['k2']) & M) >> (32 - phf['tbits'])
    def empty(i):       # the key of an empty slot must not be an address that hashes to this slot, or a lookup of that address would hit it
        return next(k for k in range(M, M - 64, -1) if idx(k) != i)
    out += [' .long 0x%x, %s - fnvl_disp_table' % (a, syms[a]) if a is not None else ' .long 0x%x, 0' % empty(i) for i, a in enumerate(phf['slots'])]
    out += ['.size fnvl_disp_table, . - fnvl_disp_table', '.globl fnvl_g', '.balign 2', 'fnvl_g:'] + [' .short %d' % g for g in phf['g']] + ['.size fnvl_g, . - fnvl_g']
    # the hash parameters live with the table (one source): rt.c reads them from here
    out += ['.balign 4', '.globl fnvl_phf_params', 'fnvl_phf_params:', ' .long %d, %d, %d, %d' % (phf['k1'], phf['k2'], phf['nbits'], phf['tbits']), '.size fnvl_phf_params, . - fnvl_phf_params']
    return '\n'.join(out) + '\n'
