/* test_crt.cpp - MSVC CRT semantics in platform/crt. */
#include "s4_test.h"

#include <errno.h>
#include <math.h>
#include <stdlib.h>

#include <stdexcept>
#include <typeinfo>

#include "fnvnx_msvcrt.h"

TEST(crt_compare_and_case)
{
    CHECK_EQ(_stricmp("Data\\Meshes", "DATA\\meshes"), 0);
    CHECK(_stricmp("a", "B") < 0);
    CHECK(_stricmp("_", "a") < 0); /* MSVC folds to lower case: '_' (0x5F) < 'a' */
    CHECK_EQ(_strnicmp("FalloutNV.esm", "falloutnv.ESP", 11), 0);
    CHECK(_strnicmp("FalloutNV.esm", "falloutnv.ESP", 13) != 0);
    CHECK_EQ(_mbsicmp((const unsigned char *)"Abc", (const unsigned char *)"aBC"), 0);
    char s[] = "MiXeD";
    CHECK_STR(_strlwr(s), "mixed");
    CHECK_STR(_strupr(s), "MIXED");
    char *d = _strdup("dup");
    CHECK_STR(d, "dup");
    free(d);
}

TEST(crt_snprintf_semantics)
{
    char b[8];
    memset(b, 'Z', sizeof b);
    CHECK_EQ(_snprintf(b, 4, "%d", 123456), -1); /* truncated: -1, no terminator */
    CHECK(!memcmp(b, "1234Z", 5));
    memset(b, 'Z', sizeof b);
    CHECK_EQ(_snprintf(b, 4, "%s", "abcd"), 4); /* exactly full: count, no terminator */
    CHECK(!memcmp(b, "abcdZ", 5));
    CHECK_EQ(_snprintf(b, 8, "%s", "ab"), 2);
    CHECK_STR(b, "ab");
    char big[64];
    CHECK_EQ(_snprintf(big, sizeof big, "%I64d|%I64u|%I64x|%5.2f", -5ll, 7ull, 255ull, 1.5), 13);
    CHECK_STR(big, "-5|7|ff| 1.50");
    CHECK_EQ(sprintf_s(b, sizeof b, "%s", "toolongvalue"), -1);
    CHECK_STR(b, "");
    CHECK_EQ(sprintf_s(b, sizeof b, "%d", 42), 2);
    CHECK_STR(b, "42");
    CHECK_EQ(_snprintf_s(b, sizeof b, _TRUNCATE, "%s", "toolongvalue"), -1);
    CHECK_STR(b, "toolong");
    CHECK_EQ(_scprintf("%d-%s", 10, "xy"), 5);
}

TEST(crt_secure_copies)
{
    char b[6];
    CHECK_EQ(strcpy_s(b, sizeof b, "hello"), 0);
    CHECK_STR(b, "hello");
    CHECK_EQ(strcpy_s(b, sizeof b, "toolong"), FNVNX_ERANGE);
    CHECK_STR(b, "");
    strcpy_s(b, sizeof b, "ab");
    CHECK_EQ(strcat_s(b, sizeof b, "cd"), 0);
    CHECK_STR(b, "abcd");
    CHECK_EQ(strcat_s(b, sizeof b, "ef"), FNVNX_ERANGE);
    CHECK_STR(b, "");
    CHECK_EQ(strncpy_s(b, sizeof b, "abcdefgh", 3), 0);
    CHECK_STR(b, "abc");
    CHECK_EQ(strncpy_s(b, sizeof b, "abcdefgh", _TRUNCATE), FNVNX_STRUNCATE);
    CHECK_STR(b, "abcde");
    char m[4];
    CHECK_EQ(memcpy_s(m, sizeof m, "xyz", 4), 0);
    CHECK_STR(m, "xyz");
    CHECK_EQ(memcpy_s(m, 2, "xyz", 4), FNVNX_ERANGE);
    char t[] = "a, b,,c";
    char *ctx = NULL;
    CHECK_STR(strtok_s(t, ", ", &ctx), "a");
    CHECK_STR(strtok_s(NULL, ", ", &ctx), "b");
    CHECK_STR(strtok_s(NULL, ", ", &ctx), "c");
    CHECK(strtok_s(NULL, ", ", &ctx) == NULL);
    unsigned short w[] = { 'F', 'N', 'V', 0x263A, 0 };
    size_t conv = 0;
    char mb[8];
    CHECK_EQ(wcstombs_s(&conv, mb, sizeof mb, w, _TRUNCATE), 0);
    CHECK_STR(mb, "FNV?");
    CHECK_EQ(conv, 5);
}

TEST(crt_number_conversion)
{
    char b[40];
    CHECK_STR(_itoa(-123, b, 10), "-123");
    CHECK_STR(_itoa(-1, b, 16), "ffffffff"); /* two's complement outside radix 10 */
    CHECK_STR(_ultoa(255, b, 2), "11111111");
    CHECK_STR(_i64toa(-9000000000ll, b, 10), "-9000000000");
    CHECK_STR(_ui64toa(0xFFFFFFFFFFFFFFFFull, b, 16), "ffffffffffffffff");
    CHECK_EQ(_itoa_s(12345, b, 5, 10), FNVNX_ERANGE);
    CHECK_EQ(_itoa_s(1234, b, 5, 10), 0);
    CHECK_STR(b, "1234");
    CHECK_EQ(_atoi64("-9000000000"), -9000000000ll);
    CHECK_STR(_gcvt(1.0, 4, b), "1.");
    CHECK_STR(_gcvt(0.5, 4, b), "0.5");
    CHECK_STR(_gcvt(1e10, 3, b), "1e+010");
    CHECK_EQ(fnvnx_ftol2(-2.9), -2);
    CHECK_EQ(fnvnx_ftol2(3.99), 3);
    CHECK_EQ((unsigned long long)fnvnx_ftol2(NAN), 0x8000000000000000ull);
    CHECK_EQ((unsigned long long)fnvnx_ftol2(1e30), 0x8000000000000000ull);
}

TEST(crt_split_make_path)
{
    char drv[4], dir[64], fn[32], ext[16];
    _splitpath("C:\\FalloutNV\\Data\\FalloutNV.esm", drv, dir, fn, ext);
    CHECK_STR(drv, "C:");
    CHECK_STR(dir, "\\FalloutNV\\Data\\");
    CHECK_STR(fn, "FalloutNV");
    CHECK_STR(ext, ".esm");
    _splitpath("Meshes/arch.v2/rock", drv, dir, fn, ext);
    CHECK_STR(drv, "");
    CHECK_STR(dir, "Meshes/arch.v2/");
    CHECK_STR(fn, "rock");
    CHECK_STR(ext, "");
    CHECK_EQ(_splitpath_s("C:\\a\\b.txt", drv, sizeof drv, dir, sizeof dir, fn, sizeof fn, ext, 2), FNVNX_ERANGE);
    CHECK_EQ(_splitpath_s("C:\\a\\b.txt", NULL, 0, dir, sizeof dir, NULL, 0, NULL, 0), 0);
    CHECK_STR(dir, "\\a\\");
    char out[64];
    _makepath(out, "C", "\\Saves", "quick", "fos");
    CHECK_STR(out, "C:\\Saves\\quick.fos");
    CHECK_EQ(_makepath_s(out, 8, "C", "\\Saves", "quick", "fos"), FNVNX_ERANGE);
    CHECK_EQ(_makepath_s(out, sizeof out, NULL, "dir\\", "f", ".e"), 0);
    CHECK_STR(out, "dir\\f.e");
}

TEST(crt_msvc_rand_and_time)
{
    fnvnx_srand(1);
    /* the first values of MSVC's rand() after srand(1) */
    CHECK_EQ(fnvnx_rand(), 41);
    CHECK_EQ(fnvnx_rand(), 18467);
    CHECK_EQ(fnvnx_rand(), 6334);
    for (int i = 0; i < 1000; i++)
        CHECK(fnvnx_rand() <= FNVNX_MSVC_RAND_MAX);
    long long t = 0;
    CHECK(_time64(&t) > 1700000000ll && t > 1700000000ll);
    unsigned c0 = fnvnx_clock();
    Sleep(20);
    CHECK(fnvnx_clock() - c0 >= 15);
}

TEST(crt_aligned_heap)
{
    for (size_t a = 1; a <= 4096; a <<= 1) {
        void *p = _aligned_malloc(100, a);
        CHECK(p != NULL);
        CHECK(((uintptr_t)p & (a - 1)) == 0);
        CHECK_EQ(_aligned_msize(p, a, 0), 100);
        memset(p, 0xAB, 100);
        p = _aligned_realloc(p, 300, a);
        CHECK(((uintptr_t)p & (a - 1)) == 0);
        CHECK(((unsigned char *)p)[99] == 0xAB);
        _aligned_free(p);
    }
    CHECK(_aligned_malloc(10, 3) == NULL); /* not a power of two */
    void *q = _aligned_offset_malloc(64, 16, 8);
    CHECK((((uintptr_t)q + 8) & 15) == 0);
    _aligned_free(q);
    void *m = malloc(37);
    CHECK(_msize(m) >= 37);
    free(m);
    int *e = _errno();
    *e = 0;
    CHECK(e == &errno);
}

TEST(crt_controlfp_rounding)
{
    unsigned int cw = _controlfp(0, 0);
    CHECK_EQ(cw & _MCW_RC, _RC_NEAR);
    volatile double h = 2.5, m = -2.5;
    _controlfp(_RC_CHOP, _MCW_RC);
    CHECK_EQ(_controlfp(0, 0) & _MCW_RC, _RC_CHOP);
    CHECK_EQ(rint(h), 2.0);
    CHECK_EQ(rint(m), -2.0);
    _controlfp(_RC_UP, _MCW_RC);
    CHECK_EQ(rint(h), 3.0);
    _controlfp(_RC_DOWN, _MCW_RC);
    CHECK_EQ(rint(h), 2.0);
    CHECK_EQ(rint(m), -3.0);
    _controlfp(_RC_NEAR, _MCW_RC);
    CHECK_EQ(rint(h), 2.0); /* ties to even */
    /* precision control is accepted and remembered, but has no effect */
    _controlfp(_PC_24, _MCW_PC);
    CHECK_EQ(_controlfp(0, 0) & _MCW_PC, _PC_24);
    volatile double third = 1.0 / 3.0;
    CHECK(third != (double)(float)third);
    _controlfp(_PC_53, _MCW_PC);
    unsigned int cur = 0;
    CHECK_EQ(_controlfp_s(&cur, 0, 0x40000000u), FNVNX_EINVAL);
    CHECK_EQ(_control87(0, 0) & _MCW_RC, _RC_NEAR);
    CHECK(_finite(1.0) && !_finite(INFINITY) && _isnan(NAN));
}

/* helpers called by name from the decomp */
static int g_built, g_destroyed;
static void ctor(void *p) { *(int *)p = ++g_built; if (g_built == 4) throw 7; }
static void dtor(void *p) { g_destroyed = g_destroyed * 10 + *(int *)p; }

TEST(crt_eh_vector_iterators)
{
    int arr[5] = { 0 };
    g_built = 10;
    fnvnx_eh_vector_ctor(arr, sizeof(int), 5, [](void *p) { *(int *)p = 1; }, dtor);
    CHECK(arr[0] == 1 && arr[4] == 1);
    for (int i = 0; i < 5; i++)
        arr[i] = i + 1;
    g_destroyed = 0;
    fnvnx_eh_vector_dtor(arr, sizeof(int), 5, dtor);
    CHECK_EQ(g_destroyed, 54321); /* last to first */
    g_built = 0;
    g_destroyed = 0;
    bool caught = false;
    try {
        fnvnx_eh_vector_ctor(arr, sizeof(int), 5, ctor, dtor);
    } catch (int v) {
        caught = v == 7;
    }
    CHECK(caught);
    CHECK_EQ(g_destroyed, 321); /* the three built objects, in reverse */
}

struct Base { virtual ~Base() {} int b = 1; };
struct Other { virtual ~Other() {} int o = 2; };
struct Derived : Base, Other { int d = 3; };
static const char TD_BASE[1] = { 0 }, TD_OTHER[1] = { 0 }, TD_DERIVED[1] = { 0 }; /* stand-ins for ??_R0 data */

TEST(crt_rtdynamiccast_bridge)
{
    fnvnx_rtti_register(TD_BASE, &typeid(Base));
    fnvnx_rtti_register(TD_OTHER, &typeid(Other));
    fnvnx_rtti_register(TD_DERIVED, &typeid(Derived));
    Derived d;
    Base *pb = &d;
    void *r = fnvnx_rt_dynamic_cast(pb, 0, (void *)TD_BASE, (void *)TD_DERIVED, 0);
    CHECK(r == &d);
    r = fnvnx_rt_dynamic_cast(pb, 0, (void *)TD_BASE, (void *)TD_OTHER, 0); /* cross cast */
    CHECK(r == static_cast<Other *>(&d));
    Base plain;
    CHECK(fnvnx_rt_dynamic_cast(&plain, 0, (void *)TD_BASE, (void *)TD_DERIVED, 0) == NULL);
    CHECK(fnvnx_rt_dynamic_cast(NULL, 0, (void *)TD_BASE, (void *)TD_DERIVED, 0) == NULL);
    bool threw = false;
    try {
        fnvnx_rt_dynamic_cast(&plain, 0, (void *)TD_BASE, (void *)TD_DERIVED, 1);
    } catch (const std::bad_cast &) {
        threw = true;
    }
    CHECK(threw);
}

TEST(crt_cxx_throw_and_lockit)
{
    int obj = 5;
    bool got = false;
    try {
        fnvnx_cxx_throw_exception(&obj, NULL);
    } catch (const fnvnx_msvc_exception &e) {
        got = e.object == &obj;
    }
    CHECK(got);
    bool rethrown = false;
    try {
        try {
            throw 3;
        } catch (...) {
            fnvnx_cxx_throw_exception(NULL, NULL); /* "throw;" */
        }
    } catch (int v) {
        rethrown = v == 3;
    }
    CHECK(rethrown);
    int lk1, lk2;
    fnvnx_lockit_ctor(&lk1, 2);
    fnvnx_lockit_ctor(&lk2, 2); /* recursive */
    CHECK_EQ(lk1, 2);
    fnvnx_lockit_dtor(&lk2);
    fnvnx_lockit_dtor(&lk1);
    bool le = false;
    try {
        fnvnx_xlength_error();
    } catch (const std::length_error &) {
        le = true;
    }
    CHECK(le);
}
