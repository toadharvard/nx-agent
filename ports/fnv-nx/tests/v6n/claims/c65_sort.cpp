// V6n claim check C65 (radix sort orders by key and keeps submission order for equal keys): nx::SortPackets vs
// std::stable_sort on random packet sets biased to duplicate keys, constant digits and the extreme keys; plus
// nx::SortKey field packing against an independent bit layout (pass 56-63, layer 52-55, state 36-51, tex 20-35,
// depth 4-19). Exit 1 on the first difference.
#include <cstdio>
#include <cstdint>
#include <vector>
#include <algorithm>
#include <random>
#include "nx/front.h"
int main() {
    std::mt19937_64 rng(7);
    long cases = 0;
    for (int it = 0; it < 4000; it++) {
        uint32_t n = 1 + rng() % (it < 2000 ? 64 : 3000);
        std::vector<nx::Packet> p(n);
        int mode = it % 5;
        for (uint32_t i = 0; i < n; i++) {
            uint64_t k;
            switch (mode) {
            case 0: k = rng(); break;
            case 1: k = rng() % 4; break;                                   // many duplicates
            case 2: k = (rng() % 3) << 48 | (rng() % 3) << 16; break;       // two digits used, others constant
            case 3: k = rng() % 2 ? ~0ull : 0ull; break;                    // extremes
            default: k = nx::SortKey(rng() % 4, rng() % 3, rng() % 5, rng() % 7, rng() % 9); break;
            }
            p[i].key = k;
        }
        std::vector<uint32_t> order(n), tmp(n); std::vector<uint64_t> keys(n), ktmp(n);
        nx::SortPackets(p.data(), n, order.data(), tmp.data(), keys.data(), ktmp.data());
        std::vector<uint32_t> ref(n);
        for (uint32_t i = 0; i < n; i++) ref[i] = i;
        std::stable_sort(ref.begin(), ref.end(), [&](uint32_t a, uint32_t b) { return p[a].key < p[b].key; });
        if (order != ref) { printf("C65 FAIL: case %d (mode %d, n %u) differs from stable_sort\n", it, mode, n); return 1; }
        cases++;
    }
    for (int it = 0; it < 100000; it++) {
        uint8_t pa = rng(), la = rng(); uint16_t st = rng(), tx = rng(), dp = rng();
        uint64_t e = (uint64_t)pa << 56 | (uint64_t)(la & 15) << 52 | (uint64_t)st << 36 | (uint64_t)tx << 20 | (uint64_t)dp << 4;
        if (nx::SortKey(pa, la, st, tx, dp) != e) { printf("C65 FAIL: SortKey packing\n"); return 1; }
    }
    printf("C65 sort: %ld random packet sets stable-sorted identically, SortKey packing ok: PASS\n", cases);
    return 0;
}
