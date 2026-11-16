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
    if not n:       # an empty key set (a table with nothing to find): every slot empty, any lookup misses
        return dict(k1=1, k2=0x9E3779B1, nbits=1, tbits=3, g=[0, 0], slots=[None] * 8, attempts=1)
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


# The entry type of ENTRY1, the line of platform/include/fnvl_host.h (the declaration there is the contract; tests/test_dispgen.py checks that this text is a line of it). The table unit
# states it itself so that it compiles in a root whose include path holds no platform header (the lifted pack).
ENTRY_TYPEDEF = 'typedef void (*__attribute__((fnvnx_entry)) fnvnx_entry_fn)(void);'


def layout(syms, phf):
    """the arrays of the typed table, ONE derivation for the emitter and the rows file: (keys, pairs, entries)
    keys     the addresses in key order (the order of <prefix>_disp_entries)
    pairs    [(key, index + 1)] per slot of <prefix>_disp_table, in slot order; an empty slot is (a key that does not hash to it, 0)
    entries  [(addr, symbol)] per element of <prefix>_disp_entries"""
    def idx(a): return (((a ^ phf['g'][((a * phf['k1']) & M) >> (32 - phf['nbits'])]) * phf['k2']) & M) >> (32 - phf['tbits'])
    def empty(i):       # the key of an empty slot must not be an address that hashes to this slot, or a lookup of that address would hit it
        return next(k for k in range(M, M - 64, -1) if idx(k) != i)
    keys = sorted(syms)
    pos = {a: j + 1 for j, a in enumerate(keys)}
    return keys, [(a, pos[a]) if a is not None else (empty(i), 0) for i, a in enumerate(phf['slots'])], [(a, syms[a]) for a in keys]


def table_c(syms, phf, prefix='fnvl'):
    """the dispatcher table as typed C++ data (a unit of the root that owns the table; tools/dispgen.py writes it). DISPATCH1 / ENTRY1: an entry is never an integer.
    <prefix>_disp_table  u32 slots {key, index + 1 into <prefix>_disp_entries} (an empty slot: a key that does not hash to it, index 0)
    <prefix>_disp_entries  fnvnx_entry_fn[], the address of each function of the list, in key order (the address of a function is a source of an entry value)
    <prefix>_g (u16) and <prefix>_phf_params {K1, K2, NBITS, TBITS} as before; sections .rodata.<prefix>_disp (the table, the entries, g, params)."""
    T, E = prefix + '_disp_table', prefix + '_disp_entries'
    keys, pairs, entries = layout(syms, phf)
    sec = '__attribute__((section(".rodata.%s_disp")))' % prefix
    out = ['/* GENERATED by tools/dispgen.py (tools/phf.py table_c): the typed dispatcher table %s; do not edit. */' % prefix, ENTRY_TYPEDEF, 'extern "C" {']
    out += ['void %s(void);' % syms[a] for a in keys]
    out += ['extern const unsigned %s[%d] %s = {' % (T, 2 * len(phf['slots']), sec)]
    out += ['  0x%xu, %du,' % p for p in pairs] + ['};']
    out += ['extern const fnvnx_entry_fn %s[%d] %s = {' % (E, max(len(keys), 1), sec)]
    out += ['  &%s,' % sy for a, sy in entries] + (['  0,'] if not keys else []) + ['};']
    out += ['extern const unsigned short %s_g[%d] %s = {' % (prefix, len(phf['g']), sec), '  ' + ', '.join(str(g) for g in phf['g']), '};']
    out += ['extern const unsigned %s_phf_params[4] %s = { %d, %d, %d, %d };' % (prefix, sec, phf['k1'], phf['k2'], phf['nbits'], phf['tbits']), '}']
    return '\n'.join(out) + '\n'
