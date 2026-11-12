/* heap.c - MSVC heap extensions (_msize, _aligned_*) and small runtime entry points (_errno, _purecall,
 * atexit/doexit, clock). */
#include "fnvnx_msvcrt.h"

#include <errno.h>
#include <malloc.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "../os/os.h"
#include "fnvnx_clock.h"




/* aligned blocks: [ ...padding... | header | user data ], header just below the user pointer */
struct ahdr { void *base; size_t size; size_t align; size_t offset; };






/* ---- runtime */
int *_errno(void) { return &errno; }







/* rand/srand with MSVC's generator: per-thread state (seed 1), x = x * 214013 + 2531011, RAND_MAX 0x7FFF.
 * The engine scales rand() by 32767, so the libc generator (RAND_MAX 2^31-1 on newlib/glibc) cannot be used. */
static __thread uint32_t g_holdrand = 1;
void fnvnx_srand(unsigned int seed) { g_holdrand = seed; }
int fnvnx_rand(void)
{
    g_holdrand = g_holdrand * 214013u + 2531011u;
    return (int)((g_holdrand >> 16) & 0x7FFF);
}

