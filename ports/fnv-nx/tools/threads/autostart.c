/* autostart.c - linked into a report lane's guest (tools/threads/variant.sh, through toolchain/game/build.sh's
 * EXTRA_OBJS): turns the wait-graph watchdog (platform/win32/waitgraph.cpp) on before the game's own initializers.
 * WG_LIMIT_MS: the long-wait limit (default 10000). */
void fnvnx_waitgraph_autostart(unsigned limit_ms);
#ifndef WG_LIMIT_MS
#define WG_LIMIT_MS 10000
#endif
__attribute__((constructor(101))) static void fnvnx_threads_autostart(void) { fnvnx_waitgraph_autostart(WG_LIMIT_MS); }
