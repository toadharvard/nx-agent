// V1 driver for nx::SortPackets (render/native/front/front.cpp, S6n 1b0665e2): adversarial key sets against
// std::stable_sort, and (T1) two threads sorting their own frames at once (the renderer's two-thread recording plan).
// The function itself is compiled from front.cpp as it stands (test_sort_radix.py extracts it).
#include "nx/front.h"
#include <algorithm>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <thread>
#include <vector>

static int bad = 0;

static bool check(const char* name, std::vector<uint64_t> ks) {
    uint32_t n = (uint32_t)ks.size();
    std::vector<nx::Packet> pk(n ? n : 1);
    std::vector<uint32_t> order(n + 1), tmp(n + 1), ref(n + 1);
    std::vector<uint64_t> keys(n + 1), ktmp(n + 1);
    for (uint32_t i = 0; i < n; i++) { pk[i].key = ks[i]; ref[i] = i; }
    nx::SortPackets(pk.data(), n, order.data(), tmp.data(), keys.data(), ktmp.data());
    std::stable_sort(ref.begin(), ref.begin() + n, [&](uint32_t a, uint32_t b) { return pk[a].key < pk[b].key; });
    bool ok = !memcmp(order.data(), ref.data(), n * 4);
    for (uint32_t i = 0; ok && i < n; i++) ok = keys[i] == pk[order[i]].key;
    printf("%s: %s (n=%u)\n", name, ok ? "ok" : "DEFECT: differs from std::stable_sort", n);
    bad += !ok;
    return ok;
}

int main(int argc, char** argv) {
    const bool threads = argc > 1 && !strcmp(argv[1], "t1"), seq = argc > 1 && !strcmp(argv[1], "seq");
    uint64_t rng = 99;
    auto rnd = [&]() { rng = rng * 6364136223846793005ull + 1442695040888963407ull; return rng ^ (rng >> 29); };
    if (!threads && !seq) {
    check("S1 empty", {});
    check("S2 one key", {0xFFFFFFFFFFFFFFFFull});
    check("S3 all equal", std::vector<uint64_t>(5000, 0x0123456789abcdefull));
    { std::vector<uint64_t> v; for (int i = 0; i < 5000; i++) v.push_back((uint64_t)(rnd() & 1) << 63); check("S4 bit 63 only", v); }
    { std::vector<uint64_t> v; for (int i = 0; i < 5000; i++) v.push_back((uint64_t)(rnd() % 3) << 56 | (rnd() & 0xff)); check("S5 pass byte + low byte", v); }
    { std::vector<uint64_t> v; for (int i = 0; i < 70000; i++) v.push_back(rnd()); check("S6 70,000 random (> 16-bit counts)", v); }
    { std::vector<uint64_t> v; for (int i = 0; i < 4096; i++) v.push_back(~(uint64_t)i); check("S7 descending", v); }
    { std::vector<uint64_t> v; for (int i = 0; i < 4096; i++) v.push_back((uint64_t)i << 32); check("S8 ascending, upper word", v); }
    { std::vector<uint64_t> v; for (int i = 0; i < 4096; i++) v.push_back(i & 1 ? 0xFF00000000000000ull : 0x00000000000000FFull); check("S9 two keys, disjoint bytes", v); }
    return bad ? 1 : 0;
    }
    // T1: two threads (t1), or the same two sorts one after the other (seq: the control), each with its own frame's buffers, at once (the hist table is a function-level static)
    {
        const uint32_t n = 200000;
        std::vector<nx::Packet> a(n), b(n);
        for (uint32_t i = 0; i < n; i++) { a[i].key = rnd(); b[i].key = rnd() >> 8; }
        std::vector<uint32_t> oa(n), ta(n), ob(n), tb(n);
        std::vector<uint64_t> ka(n), kta(n), kb(n), ktb(n);
        int tbad = 0;
        for (int round = 0; round < 20 && !tbad; round++) {
            auto sa = [&] { nx::SortPackets(a.data(), n, oa.data(), ta.data(), ka.data(), kta.data()); };
            auto sb = [&] { nx::SortPackets(b.data(), n, ob.data(), tb.data(), kb.data(), ktb.data()); };
            if (seq) { sa(); sb(); }
            else { std::thread t1(sa), t2(sb); t1.join(); t2.join(); }
            for (uint32_t i = 1; i < n; i++) {
                if (ka[i - 1] > ka[i] || kb[i - 1] > kb[i] || oa[i] >= n || ob[i] >= n) { tbad = round + 1; break; }
            }
        }
        if (tbad) printf("%s: unsorted output in round %d\n", seq ? "T0" : "T1", tbad);
        else printf("%s: ok - 20 rounds\n", seq ? "T0 (one after the other)" : "T1 (two threads at once)");
    return tbad ? 1 : 0;
    }
}
