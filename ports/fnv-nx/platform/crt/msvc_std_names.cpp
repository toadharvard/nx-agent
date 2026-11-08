/* msvc_std_names.cpp - MSVC C++ library (libcpmt) entry points the units reference by a C++ name the link does not
 * bind by address. Each name is the symbol the units call (an asm label: libstdc++'s own std classes must not be
 * redeclared); the bodies are S4b's implementations on the MSVC object layouts (cpplib.cpp, helpers.cpp).
 *
 *   std::ios_base::FUN_00ec3205(ios_base *)         0x00ec3205 = ios_base::_Ios_base_dtor (crt_symbols.tsv)
 *   std::char_traits<char>::_Copy_s / _Move_s       VC9's checked copies: memcpy_s / memmove_s, return dst
 *   std::_String_base::_Xlen/_Xran and the decomp's  length_error("string too long") /
 *   StringBase / StringErrorBase spellings           out_of_range("invalid string position")
 *   std::exception::exception(), (const char *const &)  MSVC std::exception (vptr, _m_what, _m_doFree)
 *   std::bad_alloc::bad_alloc(const char *)         MSVC bad_alloc, catchable as std::bad_alloc
 *   std::_Lockit(int) / ~_Lockit, _Locinfo::_Locinfo_ctor(_Locinfo *, const string &) / _Locinfo_dtor,
 *   std::bad_cast(const char *), locale::facet::_Incref / facet_Register, locale::id::_Id_cnt
 *                                                   the names R2's s2d/list/97deacd38aec traps under 'runtime'
 * All weak. Constructors return the object, as MSVC's do (callers that expect void ignore x0). */
#include "fnvnx_msvcrt.h"

#include <string.h>

#define WEAK __attribute__((weak))
#define AS(sym) __asm__(sym)

extern "C" {
/* ios_base */
WEAK void fnvnx_n_ios_base_dtor(void *ios) AS("_ZNSt8ios_base12FUN_00ec3205EPS_");
void fnvnx_n_ios_base_dtor(void *ios) { fnvnx_ios_base_dtor(ios); }

/* char_traits<char>::_Copy_s(dst, dst_size, src, count) / _Move_s: unsigned int is `j` on ILP32 */
WEAK char *fnvnx_n_copy_s(char *d, unsigned n, const char *s, unsigned count) AS("_ZNSt11char_traitsIcE7_Copy_sEPcjPKcj");
char *fnvnx_n_copy_s(char *d, unsigned n, const char *s, unsigned count)
{
    memcpy_s(d, n, s, count);
    return d;
}
WEAK char *fnvnx_n_move_s(char *d, unsigned n, const char *s, unsigned count) AS("_ZNSt11char_traitsIcE7_Move_sEPcjPKcj");
char *fnvnx_n_move_s(char *d, unsigned n, const char *s, unsigned count)
{
    memmove_s(d, n, s, count);
    return d;
}

/* _Xlen / _Xran under every spelling the units use */
#define XLEN(id, sym)                                                                                        \
    WEAK void id(void) AS(sym) __attribute__((noreturn));                                                    \
    void id(void) { fnvnx_xlength_error(); }
#define XRAN(id, sym)                                                                                        \
    WEAK void id(void) AS(sym) __attribute__((noreturn));                                                    \
    void id(void) { fnvnx_xout_of_range(); }
XLEN(fnvnx_n_xlen1, "_ZNSt12_String_base5_XlenEv")
XLEN(fnvnx_n_xlen2, "_ZNSt15StringErrorBase5_XlenEv")
XLEN(fnvnx_n_xlen3, "_ZNSt10StringBase5_XlenEv")
XRAN(fnvnx_n_xran1, "_ZNSt12_String_base5_XranEv")
XRAN(fnvnx_n_xran2, "_ZNSt15StringErrorBase5_XranEv")
XRAN(fnvnx_n_xran3, "_ZNSt10StringBase5_XranEv")
#undef XLEN
#undef XRAN

/* std::exception (C1 complete / C2 base object constructors are the same for a class without virtual bases) */
WEAK void *fnvnx_n_exc_c2(void *self) AS("_ZNSt9exceptionC2Ev");
void *fnvnx_n_exc_c2(void *self) { return fnvnx_exception_ctor(self); }
WEAK void *fnvnx_n_exc_c2s(void *self, const char *const *what) AS("_ZNSt9exceptionC2ERKPKc");
void *fnvnx_n_exc_c2s(void *self, const char *const *what) { return fnvnx_exception_ctor_str(self, what); }

/* std::bad_alloc(const char *) */
WEAK void *fnvnx_n_bad_alloc_c1(void *self, const char *msg) AS("_ZNSt9bad_allocC1EPKc");
void *fnvnx_n_bad_alloc_c1(void *self, const char *msg) { return fnvnx_bad_alloc_ctor(self, msg); }

/* R2 s2d/list/97deacd38aec 'runtime' traps: the decomp's spellings of libcpmt entry points S4 implements */
WEAK void *fnvnx_n_lockit_c1(void *self, int kind) AS("_ZNSt7_LockitC1Ei");
void *fnvnx_n_lockit_c1(void *self, int kind) { fnvnx_lockit_ctor(self, kind); return self; }
WEAK void fnvnx_n_lockit_d1(void *self) AS("_ZNSt7_LockitD1Ev");
void fnvnx_n_lockit_d1(void *self) { fnvnx_lockit_dtor(self); }
/* static _Locinfo_ctor(_Locinfo *, const rstd::basic_string<char> &): the decomp's MSVC string by reference */
WEAK void fnvnx_n_locinfo_ctor(void *self, const void *name) AS(
    "_ZNSt8_Locinfo13_Locinfo_ctorEPS_RKN4rstd12basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEE");
void fnvnx_n_locinfo_ctor(void *self, const void *name) { fnvnx_locinfo_ctor(self, name); }
WEAK void fnvnx_n_locinfo_dtor(void *self) AS("_ZNSt8_Locinfo13_Locinfo_dtorEPS_");
void fnvnx_n_locinfo_dtor(void *self) { fnvnx_locinfo_dtor(self); }
WEAK void *fnvnx_n_bad_cast_c1(void *self, const char *msg) AS("_ZNSt8bad_castC1EPKc");
void *fnvnx_n_bad_cast_c1(void *self, const char *msg) { return fnvnx_bad_cast_ctor(self, msg); }
WEAK void fnvnx_n_facet_incref(void *f) AS("_ZNSt6locale5facet7_IncrefEv");
void fnvnx_n_facet_incref(void *f) { fnvnx_facet_incref(f); }
WEAK void fnvnx_n_facet_register(void *f) AS("_ZNSt6locale5facet14facet_RegisterEPS0_");
void fnvnx_n_facet_register(void *f) { fnvnx_facet_register(f); }
/* locale::id::_Id_cnt: the facet id counter (VC9: static int, starts at 0) */
WEAK int fnvnx_n_id_cnt AS("_ZNSt6locale2id7_Id_cntE") = 0;
}
