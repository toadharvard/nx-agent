/* d9wrap_port.h: the only platform-dependent piece of the D3D9 journaling wrapper (RD1). The generated code and d9stream.cpp
 * are the same source on Windows/Wine (mingw-w64 i686, SDK d3d9.h) and in the guest (shim d3d9.h): both headers define
 * STDMETHODCALLTYPE and WINAPI (SDK: __stdcall; shim: empty, one calling convention). Locking is a spin lock on gcc atomics
 * (no OS object, usable from any thread); thread-local storage is __thread. */
#ifndef D9WRAP_PORT_H
#define D9WRAP_PORT_H
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#if !defined(_WIN32) && !defined(WINAPI)
#define WINAPI   /* shim: one calling convention (SDK build: windef.h gives __stdcall) */
#endif
#include <d3d9.h>

#if defined(__i386__) || defined(__x86_64__)
#define D9W_PAUSE() __builtin_ia32_pause()
#elif defined(__aarch64__)
#define D9W_PAUSE() __asm__ volatile("yield")
#else
#define D9W_PAUSE() ((void)0)
#endif
static_assert(sizeof(void *) == 4, "D9S1 is ILP32 (i686 / AArch64-ILP32): pointer words in buffers are 4 bytes");
#define D9W_TLS __thread
typedef volatile int D9Lock;
static inline void d9port_lock(D9Lock *l) { while (__atomic_exchange_n(l, 1, __ATOMIC_ACQUIRE)) while (__atomic_load_n(l, __ATOMIC_RELAXED)) D9W_PAUSE(); }
static inline void d9port_unlock(D9Lock *l) { __atomic_store_n(l, 0, __ATOMIC_RELEASE); }

/* Pixel read-back of a GDI DC for IDirect3DSurface9::GetDC/ReleaseDC (spec lock shape "dc"): fills dst with n bytes, the rows of the
 * surface (fmt, w x h) packed, read from the DC. Supplied by the integrator of each side (GDI GetDIBits on Windows, the guest's GDI
 * layer in the guest, a test model in tests/l6/wrap); there is no portable default. */
void d9port_dc_pixels(void *hdc, uint32_t fmt, uint32_t w, uint32_t h, void *dst, size_t n);
#endif
