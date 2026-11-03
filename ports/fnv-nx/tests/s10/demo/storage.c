/* Storage of the demo objects (tests/s10). In the game these live in the data image at their original
 * addresses (D3) and the link step binds the names to them; here plain zeroed bytes stand in. */
#include <stdalign.h>

alignas(8) unsigned char g_a1[16], g_a2[16], g_a3[16], g_b1[16];
int g_runtime_value;
