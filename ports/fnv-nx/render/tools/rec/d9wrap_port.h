/* d9wrap_port.h (Windows/Wine build of the recorder): replaces render/tools/replay/d9wrap_port.h in the build directory; identical except the
 * lock, which is a CRITICAL_SECTION (initialised on first use: the runtime's locks are zero-initialised statics) and Sleep-free. */
#ifndef D9WRAP_PORT_H
#define D9WRAP_PORT_H
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <d3d9.h>
static_assert(sizeof(void *) == 4, "D9S1 is ILP32 (i686 / AArch64-ILP32): pointer words in buffers are 4 bytes");
#define D9W_TLS __thread
struct D9Lock { volatile LONG st; CRITICAL_SECTION cs; };   /* st: 0 new, 1 initialising, 2 ready */
static inline void d9port_lock(D9Lock *l) {
    if (l->st != 2) {
        if (InterlockedCompareExchange(&l->st, 1, 0) == 0) { InitializeCriticalSection(&l->cs); InterlockedExchange(&l->st, 2); }
        else while (l->st != 2) SwitchToThread();
    }
    EnterCriticalSection(&l->cs);
}
static inline void d9port_unlock(D9Lock *l) { LeaveCriticalSection(&l->cs); }
void d9port_dc_pixels(void *hdc, uint32_t fmt, uint32_t w, uint32_t h, void *dst, size_t n);   /* rec_dc.cpp */
#endif
