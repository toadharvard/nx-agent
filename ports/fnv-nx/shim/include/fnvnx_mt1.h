/* fnvnx_mt1.h - the gate of patch class MT1 (docs/PATCHES.md "Patch class MT1" and "Gated classes", docs/PERF.md
 * 6.8): gated Switch behaviour changes that make the engine's multithreaded culling (bMTRendering:Display) work on
 * the PC code. Force-included through fnvnx_compat.h (the Switch build only); the one definition of FNVNX_MT1_ON
 * (tests/s1b/test_mt1.py checks that).
 *
 * FNVNX_MT1_ON: bMTRendering is set. fnvnx_mt1_on() (toolchain/game/boot.cpp) reads the INI setting object itself
 * (0x011c710c, its bool at +4 after the vtable), not the engine's copy DAT_011f94b4: WinMain builds the accumulators
 * (FUN_0086c160) before the renderer init copies the setting (FUN_004da670). The declaration is weak: in an image
 * that does not define it (fuzz harness, unit tests) its address is null and every MT1 hunk stays off. With
 * bMTRendering=0 every MT1 site behaves as the PC code. */
#ifndef FNVNX_MT1_H
#define FNVNX_MT1_H

#ifdef __cplusplus
extern "C"
#endif
int fnvnx_mt1_on(void) __attribute__((weak));
#define FNVNX_MT1_ON (fnvnx_mt1_on != 0 && fnvnx_mt1_on() != 0)

/* The batch-renderer count of the main scene's accumulator: 2 with MT culling, else 1 (the PC value). */
#define FNVNX_MT1_INLINE static inline __attribute__((always_inline))   /* a macro: prepare.py's name scan reads lines */
FNVNX_MT1_INLINE unsigned int fnvnx_mt1_batches(void)
{
    return FNVNX_MT1_ON ? 2u : 1u;
}

#endif
