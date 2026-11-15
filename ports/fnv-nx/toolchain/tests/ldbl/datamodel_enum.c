/* datamodel_enum.c - DATAMODEL row S14 on the C front end (c/c-decl.cc finish_enum): an enumeration without a fixed type is int whatever the enumerators; an
   enumerator written outside int is converted to int, the 32-bit pattern kept. Compiled by test_ldbl.py through the recipe (a failing case is its message). */
#define SAME(a, b) __builtin_types_compatible_p(a, b)
enum C0 { C0a, C0b };
enum C1 { C1a = 0xffff0000 };
enum C2 { C2a = 0x80000000 };
enum C3 { C3a = (1u << 31) | 1u, C3b = ~0u, C3c = 0x7fffffff + 1u };
_Static_assert(sizeof(enum C0) == 4 && SAME(__typeof__(C0a), int), "S14c0");
_Static_assert(sizeof(enum C1) == 4 && C1a == -65536 && C1a < 0 && (enum C1)-1 < 0 && SAME(enum C1, int), "S14c1");
_Static_assert(sizeof(enum C2) == 4 && C2a == (-2147483647 - 1) && C2a < 0, "S14c2");
_Static_assert(sizeof(enum C3) == 4 && C3a == -2147483647 && C3b == -1 && C3c == (-2147483647 - 1), "S14c3");
int dm_enum_c_anchor;
