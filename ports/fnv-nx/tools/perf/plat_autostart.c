/* plat_autostart.c - linked into a platform-lane guest (tools/threads/variant.sh PLAT=1): the recorder
 * (platform/win32/platstat.cpp) on before the game's own initializers. */
void fnvnx_plat_autostart(void);
__attribute__((constructor(101))) static void fnvnx_plat_autostart_ctor(void) { fnvnx_plat_autostart(); }
