/* fnv-nx host <-> guest ABI (stream S5, docs/TOOLCHAIN.md "The libnx boundary").
 *
 * One process, two modules, one 32-bit address space:
 *   host  = LP64 module (libnx, later the GPU driver), the NSO "main";
 *   guest = ILP32 module (the game), guest.nso, mapped by the host at its fixed link address.
 * Both are AArch64 code and share AAPCS64 (same registers, same stack, same callee-saved set). They differ only
 * in the data model, so everything that crosses the boundary uses fixed-width integers:
 *   - every argument and result of a host call is uint64_t; addresses travel zero-extended as uint64_t, never
 *     as pointers, long or size_t (their width and upper-bit rules differ between ILP32 and LP64);
 *   - shared structs contain only uint8_t..uint64_t fields with explicit padding, so their layout is identical
 *     in both data models (checked by the static asserts below in both builds);
 *   - no struct-by-value, no varargs, no C++ exceptions or longjmp across the boundary;
 *   - the guest owns TPIDR_EL0 (its TLS); the host (libnx) owns TPIDRRO_EL0's TLS area. Neither touches the
 *     other's.
 * This header compiles in both data models.
 */
#ifndef FNVNX_HOSTAPI_H
#define FNVNX_HOSTAPI_H

#include <stdint.h>

#define FNVNX_HOSTAPI_MAGIC   0x48584e46u /* "FNXH" */
#define FNVNX_GUEST_MAGIC     0x47584e46u /* "FNXG", at guest text + 8 */
#define FNVNX_HOSTAPI_VERSION 1u

/* Function slots: host addresses of LP64 functions with the signature given in the comment
 * (all parameters uint64_t, result uint64_t). */
typedef struct FnvnxHostApi {
    uint32_t magic;                /* FNVNX_HOSTAPI_MAGIC */
    uint32_t version;              /* FNVNX_HOSTAPI_VERSION */
    uint32_t size;                 /* sizeof(FnvnxHostApi) as the host built it */
    uint32_t flags;                /* reserved, 0 */

    uint64_t guest_base;           /* link (= load) address of the guest module (its .text) */
    uint64_t guest_size;           /* bytes mapped for the guest module, bss included */
    uint64_t heap_base;            /* memory block given to the guest heap (newlib sbrk), 0 if none */
    uint64_t heap_size;

    uint64_t output_debug_string;  /* (str, len) -> Result of svcOutputDebugString */
    uint64_t sleep_ns;             /* (ns) -> 0 */
    uint64_t exit;                 /* (code) -> does not return; ends the process through libnx */
    uint64_t console_write;        /* (buf, len) -> bytes written to the host text console */
    uint64_t console_flush;        /* () -> 0; presents the console framebuffer */
    uint64_t main_loop;            /* () -> 1 while the applet should keep running (appletMainLoop) */
    uint64_t buttons_down;         /* () -> HidNpadButton bits newly pressed on the default pad */
    uint64_t system_tick;          /* () -> armGetSystemTick() */
    uint64_t system_tick_freq;     /* () -> armGetSystemTickFreq() */
} FnvnxHostApi;

#if defined(__cplusplus)
static_assert(sizeof(FnvnxHostApi) == 0x78, "FnvnxHostApi layout");
#else
_Static_assert(sizeof(FnvnxHostApi) == 0x78, "FnvnxHostApi layout");
#endif

/* Guest entry: the host calls the guest module base as   uint64_t entry(uint64_t api)   where api is the
 * address of a FnvnxHostApi that stays valid for the life of the process. The result is the guest's exit
 * code. The first words of the guest module are:
 *   +0 branch to the entry, +4 offset of MOD0 from the module base, +8 FNVNX_GUEST_MAGIC, +12 ABI version. */

#if defined(__ILP32__)
/* Guest-side helpers: call an LP64 host function through its slot. */
#define FNVNX_ADDR(p) ((uint64_t)(uintptr_t)(p))
typedef uint64_t (*fnvnx_hfn0)(void);
typedef uint64_t (*fnvnx_hfn1)(uint64_t);
typedef uint64_t (*fnvnx_hfn2)(uint64_t, uint64_t);
typedef uint64_t (*fnvnx_hfn3)(uint64_t, uint64_t, uint64_t);
static inline uint64_t fnvnx_hcall0(uint64_t fn) { return ((fnvnx_hfn0)(uintptr_t)fn)(); }
static inline uint64_t fnvnx_hcall1(uint64_t fn, uint64_t a) { return ((fnvnx_hfn1)(uintptr_t)fn)(a); }
static inline uint64_t fnvnx_hcall2(uint64_t fn, uint64_t a, uint64_t b) { return ((fnvnx_hfn2)(uintptr_t)fn)(a, b); }
static inline uint64_t fnvnx_hcall3(uint64_t fn, uint64_t a, uint64_t b, uint64_t c) {
    return ((fnvnx_hfn3)(uintptr_t)fn)(a, b, c);
}

/* Set by the guest runtime before main() runs. */
extern const FnvnxHostApi *fnvnx_host;
#endif

#endif /* FNVNX_HOSTAPI_H */
