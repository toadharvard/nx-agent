"""Deterministic pseudo-random test inputs: SplitMix64 (Steele, Lea, Flood 2014), a pure function of the seed.
Rng(seed) is the generator every proof test uses where it needs "random-looking" inputs: the sequence is fixed by the seed alone (no clock, no OS entropy)."""
M64 = (1 << 64) - 1


class Rng:
    def __init__(self, seed):
        self.state = seed & M64

    def next64(self):
        self.state = (self.state + 0x9E3779B97F4A7C15) & M64
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & M64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & M64
        return z ^ (z >> 31)

    def getrandbits(self, k):
        """a uniform integer in [0, 2^k): the k low bits of as many 64-bit outputs as needed"""
        v, n = 0, 0
        while n < k:
            v |= self.next64() << n
            n += 64
        return v & ((1 << k) - 1)

    def choice(self, seq):
        """an element of the non-empty sequence (index = 64 random bits modulo len: the bias is below 2^-32 for the sizes used)"""
        return seq[self.getrandbits(64) % len(seq)]
