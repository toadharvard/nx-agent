/* encptr.cpp - kernel32 EncodePointer / DecodePointer.
 *
 * The documented contract is only DecodePointer(EncodePointer(p)) == p for every p within one process; the encoded
 * value is opaque and EncodePointer(NULL) need not be NULL. The Windows scheme is kept: XOR with a per-process
 * cookie, then a rotate right by the cookie's low bits over the word (32 bits in the game: rotate by cookie & 31;
 * the LP64 test host rotates its 64-bit word by cookie & 63, the same rule at the host's word width).
 *
 * The cookie is a per-process value fixed at first use: the first caller draws it from the platform's random source
 * (os_random32, seeded by the monotonic clock and the process id) and publishes it with one atomic compare-exchange,
 * so every thread sees the same value. Predictability of the cookie is NOT part of the contract (the environment states
 * no entropy requirement); it is never zero, so the encoding is never the identity. */
#include "w32.h"

static uintptr_t g_cookie;

static uintptr_t cookie(void)
{
    uintptr_t c = __atomic_load_n(&g_cookie, __ATOMIC_ACQUIRE);
    if (c)
        return c;
    uintptr_t fresh;
    do {
        fresh = (uintptr_t)os_random32();
        if (sizeof(uintptr_t) > 4)
            fresh |= (uintptr_t)os_random32() << (sizeof(uintptr_t) > 4 ? 32 : 0);
    } while (!fresh);
    uintptr_t expect = 0;
    if (__atomic_compare_exchange_n(&g_cookie, &expect, fresh, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE))
        return fresh;
    return expect;   /* another thread published first */
}

static const unsigned W = (unsigned)(sizeof(uintptr_t) * 8);

static uintptr_t ror(uintptr_t v, unsigned r)
{
    r %= W;
    return r ? (v >> r) | (v << (W - r)) : v;
}

extern "C" LPVOID EncodePointer(LPVOID p)
{
    uintptr_t c = cookie();
    return (LPVOID)ror((uintptr_t)p ^ c, (unsigned)(c % W));
}

extern "C" LPVOID DecodePointer(LPVOID p)
{
    uintptr_t c = cookie();
    return (LPVOID)(ror((uintptr_t)p, (unsigned)(W - c % W)) ^ c);
}
