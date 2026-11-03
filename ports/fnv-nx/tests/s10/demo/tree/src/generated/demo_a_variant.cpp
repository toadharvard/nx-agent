// Synthetic flag-variant unit (tests/s10): owns one function, and carries a copy of unit A's declarations
// as the decomp's *_variant.cpp units do. The copy must not construct g_a1 a second time.
#include "demo.h"

// @fnv 0x00500000
int FUN_00500000()
{
    return 5;
}

// @fnv 0x00f40010
// @fnv 0x00fc8010
Logger g_a1(1);
