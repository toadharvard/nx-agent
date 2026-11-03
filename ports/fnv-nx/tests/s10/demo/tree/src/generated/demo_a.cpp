// Synthetic generated unit A (tests/s10): file-scope objects whose dynamic initializers interleave, in
// original address order, with those of unit B.
#include "demo.h"

// @fnv 0x00f40010
// @fnv 0x00fc8010
Logger g_a1(1);
// @fnv 0x00f40030
Plain g_a2;
// @fnv 0x00f40050
// @fnv 0x00fc8030
Dep g_a3(g_b1);
