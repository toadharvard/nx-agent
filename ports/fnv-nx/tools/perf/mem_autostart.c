/* mem_autostart.c - linked into a memory-lane guest (tools/threads/variant.sh MEM=1): the recorder
 * (platform/win32/memtrack.cpp) on before the game's own initializers. MEM_PERIOD_MS: the timeline step (1000). */
void fnvnx_mem_autostart(unsigned period_ms);
#ifndef MEM_PERIOD_MS
#define MEM_PERIOD_MS 1000
#endif
__attribute__((constructor(101))) static void fnvnx_mem_autostart_ctor(void) { fnvnx_mem_autostart(MEM_PERIOD_MS); }
