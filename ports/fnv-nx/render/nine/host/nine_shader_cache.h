/* fnv-nx: startup hook for the nvc0 precompiled shader cache (patch 0005, render/nine/precompile/README.md).
 * The host module calls nx_nine_shader_cache_init() once at startup, before the first Nine device
 * creates shaders. */
#ifndef NINE_SHADER_CACHE_H
#define NINE_SHADER_CACHE_H

#ifdef __cplusplus
extern "C" {
#endif

#define NX_NINE_SHADER_CACHE_PATH "sdmc:/switch/fnv/shaders.nvc0cache"   /* inside FNVNX_SD_DIR (fnvnx/sdlayout.h) */

/* path NULL: $NVC0_SHADER_CACHE if set, else NX_NINE_SHADER_CACHE_PATH. Read-write: shipped entries
 * hit, misses are compiled and appended. Creates the parent directory if missing.
 * Returns 0, or -1 if the directory could not be created (the cache then stays off). */
int nx_nine_shader_cache_init(const char *path);

/* counters since init (for the log / overlay) */
void nx_nine_shader_cache_stats(unsigned *entries, unsigned *hits, unsigned *misses, unsigned *stores);

#ifdef __cplusplus
}
#endif
#endif
