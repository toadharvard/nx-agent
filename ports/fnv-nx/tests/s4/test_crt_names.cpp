/* test_crt_names.cpp - platform/crt/msvc_names.c: the CRT spellings the game units reference by name. */
#include "s4_test.h"

#include <stdint.h>
#include <time.h>

/* the shim maps the intrinsics to builtins as macros; the units that trap declare them as functions */
#undef _byteswap_ulong
#undef _byteswap_ushort
#undef _byteswap_uint64

extern "C" {
unsigned long _byteswap_ulong(unsigned long);
unsigned short _byteswap_ushort(unsigned short);
unsigned long long _byteswap_uint64(unsigned long long);
void *_memcpy(void *, const void *, size_t);
void *__memcpy(void *, const void *, size_t);
int __mbsnicmp(const unsigned char *, const unsigned char *, size_t);
long long __time64(long long *);
void _ReadWriteBarrier(void);
int __isoc23_sscanf(const char *, const char *, ...);
long __isoc23_strtol(const char *, char **, int);
void FreeBlockImp(void *);
}

TEST(crt_msvc_names)
{
    CHECK_EQ(_byteswap_ulong(0x11223344ul) & 0xffffffffu, 0x44332211u);
    CHECK_EQ(_byteswap_ushort(0xa1b2), 0xb2a1);
    CHECK(_byteswap_uint64(0x0102030405060708ull) == 0x0807060504030201ull);
    char d[8] = {0}, e[8] = {0};
    CHECK(_memcpy(d, "abcdef", 7) == d);
    CHECK_STR(d, "abcdef");
    CHECK(__memcpy(e, "xyz", 4) == e);
    CHECK_STR(e, "xyz");
    CHECK_EQ(__mbsnicmp((const unsigned char *)"ABCx", (const unsigned char *)"abcy", 3), 0);
    CHECK_EQ(__mbsnicmp((const unsigned char *)"abd", (const unsigned char *)"ABC", 3), 1);
    CHECK_EQ(__mbsnicmp((const unsigned char *)"Ab", (const unsigned char *)"AC", 2), -1);
    long long t = 0, r = __time64(&t);
    CHECK(r == t && r > 1600000000ll);   /* wall clock, seconds since 1970 */
    _ReadWriteBarrier();
    int a = 0, b = 0;
    char w[16] = {0};
    CHECK_EQ(__isoc23_sscanf("12 0x1f word", "%d %x %15s", &a, &b, w), 3);
    CHECK_EQ(a, 12);
    CHECK_EQ(b, 31);
    CHECK_STR(w, "word");
    char *end = NULL;
    CHECK_EQ(__isoc23_strtol("-42z", &end, 10), -42);
    CHECK(end && *end == 'z');
    CHECK_EQ(__isoc23_strtol("0x10", NULL, 0), 16);
    FreeBlockImp(NULL);   /* no game heap linked: a no-op, as on NULL */
}
