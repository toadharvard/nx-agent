/* The embedder of the libnxgl4.so test: provides the two symbols the library leaves undefined. Loaded with
 * RTLD_GLOBAL from python before the library. */
#include <stdint.h>
static uint32_t g_count, g_last = 0xFFFFFFFFu;
void* nxgl4_guest_ptr(uint64_t addr, uint64_t size) { (void)size; return (void*)(uintptr_t)addr; }
void nxgl4_on_present(uint32_t frame) { g_count++; g_last = frame; }
uint32_t preload_count(void) { return g_count; }
uint32_t preload_last(void) { return g_last; }
