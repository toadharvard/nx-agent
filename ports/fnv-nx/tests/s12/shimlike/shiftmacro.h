// shiftmacro.h - stands in for shim / sse2neon code in tests/s12 cases.cpp: run.py passes this directory as
// fnvcheck --exclude, so neither the macro (spelled here, expanded in cases.cpp) nor the inline function may
// produce a row.
#define FNVS_SHL(x, n) ((x) << (n))
static inline unsigned fnvs_rotl(unsigned x, int n) { return (x << n) | (x >> (32 - n)); }
