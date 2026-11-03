// Synthetic generated unit B (tests/s10): one object and one plain initializer function (the shape of the
// decomp's InitializeRuntime* families), which the address map lists as kind F.
#include "demo.h"

// @fnv 0x00f40020
// @fnv 0x00fc8020
Logger g_b1(2);

// @fnv 0x00f40040
void InitializeRuntimeDemo1()
{
    g_runtime_value = g_a2.a + 41; // needs g_a2 (0x00f40030, unit A) constructed
}

// What the link step adds for kind F entries (docs/LINK.md, __fnv_<addr> alias of the definition).
extern "C" void __fnv_00f40040() __attribute__((alias("_Z22InitializeRuntimeDemo1v")));
