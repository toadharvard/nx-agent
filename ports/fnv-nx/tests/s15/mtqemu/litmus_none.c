/* litmus_none.c - stand-ins when the fnvnx_order plugin does not build here (no gcc plugin headers): the
 * litmus cases report "skip" (mt_test.cpp checks litmus_available). */
int litmus_available = 0;
int litmus_main_order(void) { return 0; }
int litmus_main_plain(void) { return 0; }
