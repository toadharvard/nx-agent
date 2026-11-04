/* fnvnx_mt1.h - the gate of patch class MT1 (docs/PATCHES.md "Patch class MT1", docs/PERF.md 6.8): gated Switch
 * behaviour changes that make the engine's multithreaded culling (bMTRendering:Display) work on the PC code.
 *
 * fnvnx_mt1_batches() is the batch-renderer count of the main scene's accumulator: 2 when bMTRendering is set,
 * else 1, the PC value. It reads the INI setting object itself (0x011c710c, its bool at +4 after the vtable), not
 * the engine's copy DAT_011f94b4: WinMain builds the accumulators (FUN_0086c160) before the renderer init that
 * copies the setting (FUN_004da670). With bMTRendering=0 every MT1 site behaves as the PC code. */
#ifndef FNVNX_MT1_H
#define FNVNX_MT1_H

extern const volatile unsigned char fnvnx_mt1_setting_bMTRendering[8] __asm__("DAT_011c710c");
#define FNVNX_MT1_INLINE static inline __attribute__((always_inline))   /* a macro: prepare.py's name scan reads lines */
FNVNX_MT1_INLINE unsigned int fnvnx_mt1_batches(void)
{
    return fnvnx_mt1_setting_bMTRendering[4] ? 2u : 1u;
}

#endif
