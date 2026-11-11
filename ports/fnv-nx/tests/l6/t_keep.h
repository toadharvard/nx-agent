// t_keep.h: the l6 test drivers create devices / objects through the public classes and the product API and do not release every one of them before exit.
// A driver keeps the root of what it created in this table until the process ends, so LeakSanitizer (NX_SAN, san.py) reports only memory that NO live pointer reaches:
// an allocation the product itself lost.  (An object the test holds is reachable; everything it owns is reachable through it.)
#pragma once
#include <stdlib.h>
static inline void t_keep(const void* p) {
    static const void** tab; static size_t n, cap;
    if (n == cap) { cap = cap ? cap * 2 : 256; tab = (const void**)realloc((void*)tab, cap * sizeof *tab); if (!tab) abort(); }
    tab[n++] = p;
}
template <class T> static inline T* t_new() { T* p = new T; t_keep(p); return p; }
