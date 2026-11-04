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

extern "C" {
long long __emul(int, int);
unsigned long long __emulu(unsigned, unsigned);
void __movsd(unsigned long *, const unsigned long *, size_t);
void __movsb(unsigned char *, const unsigned char *, size_t);
void *xmemcpy(void *, const void *, size_t);
void __debugbreak(void);
}

TEST(crt_msvc_intrinsics_out_of_line)
{
    CHECK(__emul(0x66666667, -25) == (long long)0x66666667 * -25);
    CHECK((int)(__emul(0x66666667, 1234) >> 34) == 123);   /* the division by 10 the Havok splitter does */
    CHECK(__emulu(0xffffffffu, 2u) == 0x1fffffffeull);
    uint32_t w[6] = {1, 2, 3, 4, 5, 6};
    __movsd((unsigned long *)(void *)(w + 1), (const unsigned long *)(void *)w, 4);   /* overlapping, forward */
    CHECK(w[1] == 1 && w[2] == 1 && w[3] == 1 && w[4] == 1 && w[5] == 6);
    unsigned char b[5] = {'a', 'b', 'c', 'd', 'e'};
    __movsb(b, b + 1, 3);
    CHECK(!memcmp(b, "bcdde", 5));
    char x[4] = {0};
    CHECK(xmemcpy(x, "hi!", 4) == x);
    CHECK_STR(x, "hi!");
    __debugbreak();   /* logged, then goes on */
}

extern "C" {
void *FUN_00401460(void *, const void *, unsigned);
int FUN_00406d50(char *, unsigned, const char *);
}

TEST(crt_exe_forwarders)
{
    char b[16] = "abcdef";
    CHECK(FUN_00401460(b + 2, b, 4) == b + 2);   /* CopyMemoryBlock: overlap-safe */
    CHECK_STR(b, "ababcd");
    char c[8] = "ab";
    CHECK_EQ(FUN_00406d50(c, sizeof c, "cde"), 0);   /* AppendPreview = strcat_s */
    CHECK_STR(c, "abcde");
    CHECK(FUN_00406d50(c, 6, "xyz") != 0);           /* too long: an error (and an empty string, as strcat_s) */
}

extern "C" void __fnv_00401050(void *, unsigned, int, void (*)(void *));
int t_appendpreview_v(char *, unsigned, const char *) __asm__("_Z12FUN_00406d50z");
static int g_ctor_calls;
static void t_ctor(void *p) { *(int *)p = 0x55 + g_ctor_calls++; }

TEST(crt_vector_ctor_and_variadic_appendpreview)
{
    int a[5] = {0, 0, 0, 0, 0};
    g_ctor_calls = 0;
    __fnv_00401050(a, sizeof(int), 4, t_ctor);
    CHECK(g_ctor_calls == 4 && a[0] == 0x55 && a[3] == 0x58 && a[4] == 0);
    __fnv_00401050(a, sizeof(int), 0, t_ctor);
    CHECK_EQ(g_ctor_calls, 4);
    char b[8] = "ab";
    CHECK_EQ(t_appendpreview_v(b, sizeof b, "cd"), 0);
    CHECK_STR(b, "abcd");
}
