/* test_msvc_std_names.cpp - platform/crt/msvc_std_names.cpp: the libcpmt names the units call. */
#include "s4_test.h"
#include "fnvnx_msvcrt.h"

#include <stdexcept>

extern "C" {
char *t_copy_s(char *, unsigned, const char *, unsigned) __asm__("_ZNSt11char_traitsIcE7_Copy_sEPcjPKcj");
char *t_move_s(char *, unsigned, const char *, unsigned) __asm__("_ZNSt11char_traitsIcE7_Move_sEPcjPKcj");
void t_xlen1(void) __asm__("_ZNSt12_String_base5_XlenEv");
void t_xlen2(void) __asm__("_ZNSt15StringErrorBase5_XlenEv");
void t_xlen3(void) __asm__("_ZNSt10StringBase5_XlenEv");
void t_xran1(void) __asm__("_ZNSt12_String_base5_XranEv");
void t_xran2(void) __asm__("_ZNSt15StringErrorBase5_XranEv");
void t_xran3(void) __asm__("_ZNSt10StringBase5_XranEv");
void *t_exc(void *) __asm__("_ZNSt9exceptionC2Ev");
void *t_exc_s(void *, const char *const *) __asm__("_ZNSt9exceptionC2ERKPKc");
void *t_bad_alloc(void *, const char *) __asm__("_ZNSt9bad_allocC1EPKc");
}

template <class E> static int throws(void (*f)(void), const char *what)
{
    try {
        f();
    } catch (const E &e) {
        return !strcmp(e.what(), what);
    } catch (...) {
        return 0;
    }
    return 0;
}

TEST(msvc_std_names)
{
    char b[8] = {0};
    CHECK(t_copy_s(b, sizeof b, "abcd", 5) == b);
    CHECK_STR(b, "abcd");
    CHECK(t_move_s(b + 1, 7, b, 5) == b + 1);
    CHECK_STR(b, "aabcd");
    CHECK(throws<std::length_error>(t_xlen1, "string too long"));
    CHECK(throws<std::length_error>(t_xlen2, "string too long"));
    CHECK(throws<std::length_error>(t_xlen3, "string too long"));
    CHECK(throws<std::out_of_range>(t_xran1, "invalid string position"));
    CHECK(throws<std::out_of_range>(t_xran2, "invalid string position"));
    CHECK(throws<std::out_of_range>(t_xran3, "invalid string position"));
    alignas(8) unsigned char e[32], f[32], g[32];
    const char *msg = "boom";
    CHECK(t_exc(e) == e);
    CHECK(t_exc_s(f, &msg) == f);
    CHECK_STR(fnvnx_exception_what(f), "boom");
    fnvnx_exception_dtor(f);
    fnvnx_exception_dtor(e);
    CHECK(t_bad_alloc(g, "out of memory") == g);
    CHECK_STR(fnvnx_exception_what(g), "out of memory");
    CHECK(dynamic_cast<std::bad_alloc *>((std::exception *)(void *)g) != NULL);
    fnvnx_exception_dtor(g);
}
