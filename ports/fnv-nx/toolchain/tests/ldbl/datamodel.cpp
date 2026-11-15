// datamodel.cpp - one case per row of toolchain/msvcmodel/DATAMODEL.json (the exe's 32-bit Visual C++ 2008 data model), compiled by test_ldbl.py through the
// recipe. A static_assert message is the row id; test_ldbl.py checks that every row of the table has its case here and every case has a row. Rows of status
// `open` pin what the compiler gives today (a known mismatch, detected per record by the R7 layout gate), so a change of it fails here until the table is updated.
#include <stddef.h>
#define DM(id, ...) static_assert((__VA_ARGS__), id)
template <class A, class B> struct Same { static const bool v = false; };
template <class A> struct Same<A, A> { static const bool v = true; };

DM("S01", (char)-1 < 0 && !Same<char, signed char>::v && !Same<char, unsigned char>::v);   // /J off: char signed (and a type of its own)
DM("S02", sizeof(wchar_t) == 2 && (wchar_t)-1 > 0 && !Same<wchar_t, unsigned short>::v);   // /Zc:wchar_t: native, 2 bytes, unsigned
DM("S03", sizeof(bool) == 1 && alignof(bool) == 1 && sizeof(char) == 1);
DM("S04", sizeof(short) == 2 && alignof(short) == 2);
DM("S05", sizeof(int) == 4 && alignof(int) == 4 && sizeof(long) == 4 && alignof(long) == 4 && !Same<int, long>::v);
DM("S06", sizeof(long long) == 8 && alignof(long long) == 8);
DM("S07", sizeof(float) == 4 && alignof(float) == 4);
DM("S08", sizeof(double) == 8 && alignof(double) == 8);
DM("S09", sizeof(long double) == 8 && alignof(long double) == 8 && !Same<long double, double>::v && __LDBL_MANT_DIG__ == 53);
DM("S10", sizeof(void *) == 4 && alignof(void *) == 4 && sizeof(void (*)()) == 4);
struct Fwd;
DM("S11", sizeof(int Fwd::*) == 4 && alignof(int Fwd::*) == 4);
struct Single { void f(); int m; };
struct M1 { virtual void a(); int x; };
struct M2 { virtual void b(); int y; };
struct Multi : M1, M2 {};
DM("S12", sizeof(void (Single::*)()) == 8 && sizeof(void (Multi::*)()) == 8);   // OPEN: MSVC single inheritance is 4 (multiple 8)
struct SD { char c; double d; };
struct SQ { char c; long long q; };
struct SL { char c; long double d; };
struct SP { char c; void *p; };
struct SW { char c; wchar_t w; };
struct SS { char c; short s; };
struct SA { char c; SD sd; };
DM("S13", sizeof(SD) == 16 && offsetof(SD, d) == 8 && sizeof(SQ) == 16 && offsetof(SQ, q) == 8 && sizeof(SL) == 16 && offsetof(SL, d) == 8
          && sizeof(SP) == 8 && offsetof(SP, p) == 4 && sizeof(SW) == 4 && offsetof(SW, w) == 2 && sizeof(SS) == 4 && offsetof(SS, s) == 2
          && alignof(SA) == 8 && sizeof(SA) == 24 && offsetof(SA, sd) == 8);
enum E0 { E0a, E0b };
enum E1 { E1a = -1, E1b };
DM("S14", Same<__underlying_type(E0), int>::v && Same<__underlying_type(E1), int>::v && sizeof(E0) == 4 && Same<decltype(+E0a), int>::v && (E0)-1 < 0);
enum E2 : unsigned char { E2a };
DM("S15", sizeof(E2) == 1 && Same<__underlying_type(E2), unsigned char>::v);   // a fixed underlying type is as declared
struct B1 { char a : 3; int b : 3; };
struct B2 { int a : 3; char b : 3; };
struct B3 { char c; int b : 3; };
struct B4 { bool a : 1; int b : 1; };
struct B5 { unsigned a : 3; int b : 3; };
struct B6 { char a : 3; char b : 3; short c : 3; short d : 3; };
DM("S16", sizeof(B1) == 8 && sizeof(B2) == 8 && sizeof(B3) == 8 && sizeof(B4) == 8 && sizeof(B5) == 4 && sizeof(B6) == 4 && alignof(B1) == 4);   // a unit is shared only by the same integral type size
struct B7 { int a : 3; int : 0; int b : 3; };
struct B8 { char a : 1; short : 0; char b : 1; };
DM("S17", sizeof(B7) == 8 && sizeof(B8) == 4);   // an unnamed zero-width field aligns the next one to the boundary of its base type
struct B9 { char a : 7; char b : 7; };
struct B10 { short a : 9; short b : 9; };
struct B11 { int a : 20; int b : 20; };
struct B12 { unsigned first : 9; unsigned second : 7; unsigned may_straddle : 30; unsigned last : 18; };   // the MS page "C Bit Fields" example: sizeof 12
DM("S18", sizeof(B9) == 2 && sizeof(B10) == 4 && sizeof(B11) == 8 && sizeof(B12) == 12);   // a bit-field never straddles its unit
struct BO { unsigned a : 4, b : 5, c : 7, d : 16; };   // the MS page "C Bit Fields" example (a = 2, b = 31, c = 0): the bits are allocated from the least significant bit up, 0x01F2
constexpr unsigned bo() { BO x{}; x.a = 2; x.b = 31; x.c = 0; x.d = 0; return __builtin_bit_cast(unsigned, x); }
DM("S19", sizeof(BO) == 4 && bo() == 0x1F2u);
struct BS { int a : 3; };
constexpr bool bs() { BS s{}; s.a = -1; return s.a < 0; }
DM("S20", bs());   // an int bit-field is signed
struct Em {};
DM("S21", sizeof(Em) == 1 && alignof(Em) == 1);
struct D1 : Em { int x; };
struct D2 : Em { Em e; int x; };
struct D2b : Em { char c; };
DM("S22", sizeof(D1) == 4 && sizeof(D2) == 8 && sizeof(D2b) == 1);   // the first empty base is overlapped
struct E5 {};
struct E6 {};
struct D3 : E5, E6 { int x; };
// the cases of cpp/empty-bases.md (pinned cpp-docs): Derived3 is 2 bytes, Derived4 8, Struct2 1 (its empty base sits at offset 1, past Struct1)
struct Empty1d {};
struct Empty2d : Empty1d {};
struct Empty3d {};
struct Derived2d : Empty2d { char c; };
struct Derived3d : Empty2d, Empty3d { char c; };
struct Derived4d : Empty2d, Empty3d { int i; };
struct Struct1d { char c; };
struct Struct2d : Struct1d, Empty1d {};
DM("S23", sizeof(D3) == 8 && sizeof(Derived2d) == 1 && sizeof(Derived3d) == 2 && sizeof(Derived4d) == 8 && sizeof(Struct2d) == 1);
struct V { virtual void f(); };
struct NV { int a; };
struct D6 : NV { virtual void f(); int y; };
struct D5 : V { int x; };
DM("S24", sizeof(V) == 4 && alignof(V) == 4 && sizeof(D5) == 8 && offsetof(D5, x) == 4 && sizeof(D6) == 12 && offsetof(D6, y) == 8);
struct VB { int v; };
struct VD1 : virtual VB { int a; };
DM("S25", sizeof(VD1) == 12);   // OPEN: MSVC keeps a vbptr (4) + a + the shared base: 12; Itanium vptr (4) + a + base = 12. Equal size, different layout (the layout gate checks it)
// va_arg: the promoted types and their sizes; the representation of va_list is the AAPCS64 one (OPEN)
typedef __builtin_va_list VL;
double va_k(int n, ...) { VL ap; __builtin_va_start(ap, n); double s = 0; s += __builtin_va_arg(ap, int); s += __builtin_va_arg(ap, long long); s += __builtin_va_arg(ap, double); s += __builtin_va_arg(ap, long double); s += (unsigned)__builtin_va_arg(ap, void *); __builtin_va_end(ap); return s; }
DM("S26", sizeof(VL) != sizeof(char *));   // OPEN: MSVC's va_list is `char *`

// S27, S28: the types of the language's own typedefs (cl: size_t unsigned int, ptrdiff_t a 4-byte signed integer, int in its mangling)
DM("S27", __is_same(__SIZE_TYPE__, unsigned int));
DM("S28", __is_same(__PTRDIFF_TYPE__, int));
// S30: a class with a virtual function holds one 4-byte vptr, then its members; a vtable entry is a 4-byte pointer
struct VT { virtual void f(); int a; };
DM("S30", sizeof(VT) == 8 && sizeof(void (*)()) == 4);

// S31, S32: wint_t is unsigned short and sig_atomic_t is int in the 32-bit Windows program
DM("S31", __is_same(__WINT_TYPE__, unsigned short));
DM("S32", __is_same(__SIG_ATOMIC_TYPE__, int));
// S33: every fixed-width alias of the native side is a fundamental type with an S-row; the exact ones have their one identity
#define FUND(T) (__is_same(T, signed char) || __is_same(T, unsigned char) || __is_same(T, short) || __is_same(T, unsigned short) || __is_same(T, int) || __is_same(T, unsigned int) || __is_same(T, long long) || __is_same(T, unsigned long long))
DM("S33", __is_same(__INT8_TYPE__, signed char) && __is_same(__INT16_TYPE__, short) && __is_same(__INT32_TYPE__, int) && __is_same(__INT64_TYPE__, long long)
        && __is_same(__UINT8_TYPE__, unsigned char) && __is_same(__UINT16_TYPE__, unsigned short) && __is_same(__UINT32_TYPE__, unsigned int) && __is_same(__UINT64_TYPE__, unsigned long long)
        && __is_same(__INTMAX_TYPE__, long long) && __is_same(__UINTMAX_TYPE__, unsigned long long) && __is_same(__INTPTR_TYPE__, int) && __is_same(__UINTPTR_TYPE__, unsigned int)
        && FUND(__INT_LEAST8_TYPE__) && FUND(__INT_LEAST16_TYPE__) && FUND(__INT_LEAST32_TYPE__) && FUND(__INT_LEAST64_TYPE__)
        && FUND(__UINT_LEAST8_TYPE__) && FUND(__UINT_LEAST16_TYPE__) && FUND(__UINT_LEAST32_TYPE__) && FUND(__UINT_LEAST64_TYPE__)
        && FUND(__INT_FAST8_TYPE__) && FUND(__INT_FAST16_TYPE__) && FUND(__INT_FAST32_TYPE__) && FUND(__INT_FAST64_TYPE__)
        && FUND(__UINT_FAST8_TYPE__) && FUND(__UINT_FAST16_TYPE__) && FUND(__UINT_FAST32_TYPE__) && FUND(__UINT_FAST64_TYPE__));
