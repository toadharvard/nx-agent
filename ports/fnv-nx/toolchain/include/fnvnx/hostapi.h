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
#define FNVNX_HOSTAPI_VERSION 3u

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

    /* ---- version 2: threads, address wait/wake, time, files ---- */
    uint64_t thread_create;        /* (entry, arg, stack_size, prio, cpu) -> host thread id (!= 0) or 0.
                                      The new thread calls the guest function entry(arg) (u64 arg) and ends
                                      when it returns. prio: Win32 scale -15..15; cpu: -1 default placement,
                                      0..63 that core only, FNVNX_THREAD_ROLE(r) the placement role r
                                      (toolchain/host/placement.h). Not started. */
    uint64_t thread_start;         /* (id) -> 0 or Result */
    uint64_t thread_join;          /* (id) -> 0 when the thread has ended */
    uint64_t thread_free;          /* (id) -> 0; after join, or detaches a running thread */
    uint64_t thread_exit;          /* () -> does not return; ends the calling host-created thread */
    uint64_t thread_yield;         /* () -> 0 */
    uint64_t thread_set_priority;  /* (id or 0 = self, prio) -> 0 or Result */
    uint64_t thread_set_cpu;       /* (id or 0 = self, spec) -> 0 or Result. spec 0..63: that core only;
                                      FNVNX_CPU_SPEC(ideal, mask): ideal core (FNVNX_CPU_KEEP: keep) and
                                      affinity mask (0: keep), the ideal core joins the mask */
    uint64_t wait_on_address;      /* (addr, expected u32, timeout_ns) -> 0 woken, 1 timed out, 2 value != expected
                                      (svcWaitForAddress WaitIfEqual; addr 4-aligned) */
    uint64_t wake_address;         /* (addr, count; count = UINT64_MAX: all) -> 0 (svcSignalToAddress) */
    uint64_t wall_time_us;         /* () -> microseconds since 1970 UTC */
    uint64_t tz_offset_minutes;    /* () -> local = UTC + offset (int64 as u64) */
    uint64_t random64;             /* () -> random u64 */
    uint64_t memory_info;          /* (FnvnxMemInfo *) -> 0 */
    uint64_t cpu_count;            /* () -> cores the game may use */
    uint64_t fs_open;              /* (path, OS_O_* flags) -> fd >= 0, or -errno (int64 as u64) */
    uint64_t fs_read;              /* (fd, buf, n) -> bytes or -errno */
    uint64_t fs_write;             /* (fd, buf, n) -> bytes or -errno */
    uint64_t fs_pread;             /* (fd, buf, n, off) -> bytes or -errno */
    uint64_t fs_pwrite;            /* (fd, buf, n, off) -> bytes or -errno */
    uint64_t fs_seek;              /* (fd, off, whence) -> new position or -errno */
    uint64_t fs_truncate;          /* (fd, size) -> 0 or -errno */
    uint64_t fs_sync;              /* (fd) -> 0 or -errno */
    uint64_t fs_fstat;             /* (fd, FnvnxStat *) -> 0 or -errno */
    uint64_t fs_close;             /* (fd) -> 0 or -errno */
    uint64_t fs_stat;              /* (path, FnvnxStat *) -> 0 or -errno */
    uint64_t fs_mkdir;             /* (path) -> 0 or -errno */
    uint64_t fs_rmdir;             /* (path) -> 0 or -errno */
    uint64_t fs_remove;            /* (path) -> 0 or -errno */
    uint64_t fs_rename;            /* (from, to) -> 0 or -errno */
    uint64_t fs_dir_open;          /* (path) -> dir id (!= 0) or 0 */
    uint64_t fs_dir_next;          /* (dir, buf, cap) -> name length (without NUL), 0 at end; skips . and .. */
    uint64_t fs_dir_close;         /* (dir) -> 0 */

    /* ---- version 3: audio out and input (platform/audio, platform/input via toolchain/guest/platform_glue) */
    uint64_t audio_open;           /* (rate = 48000, period frames) -> 0 or negative; stereo PCM16 */
    uint64_t audio_submit;         /* (pcm, frames = period) -> 0; blocks until the device takes it */
    uint64_t audio_close;          /* () -> 0 */
    uint64_t pad_read;             /* (index 0..3, fnvnx_pad_raw *) -> 1 connected, 0 not */
    uint64_t pad_vibrate;          /* (index, float bits low, float bits high) -> 0; amplitudes 0..1 */
    uint64_t input_poll;           /* (FnvnxInputEvent *, cap) -> events since the last poll */
    uint64_t map_fixed;            /* (addr, size, src) -> 0 or Result: makes [addr, addr+size) RW memory at exactly
                                      addr by svcMapMemory from src, a page-aligned guest heap block of the same
                                      size (it becomes inaccessible); addr in the small map, page aligned */
    uint64_t ext_call;             /* (module, fn, const uint64_t *args, nargs) -> handler result, or
                                      FNVNX_EXT_NO_MODULE. Generic extension point: host-side modules (e.g. the
                                      D3D9 proxy of render/nine) register with fnvnx_host_register_ext() */
} FnvnxHostApi;

#define FNVNX_EXT_NO_MODULE 0xFFFFFFFFFFFFFF01ull
/* ext_call module of the host's platform services (toolchain/host/host.c; render/nine is 9, render/native 10) */
#define FNVNX_EXT_PLATFORM 11u
#define FNVNX_PLAT_CPU_BOOST 1u   /* (mode: 0 normal, 1 fast load) -> 0 set, 1 off by the platform config (boost_loading) */
#define FNVNX_PLAT_THREAD_ACTIVITY 2u   /* (host thread id != 0, paused 0/1) -> 0, or the Horizon result of svcSetThreadActivity */
#define FNVNX_PLAT_MESSAGE 3u           /* (caption, text: guest strings) -> 0 after the player closed the dialog, or the applet's result */
/* VirtualAlloc's address space (toolchain/host/host_vm.inc): reserve (hint or 0, size) -> address or 0; commit /
 * decommit (address, size) -> 0 or a Horizon result; release (address) -> 0 or a result */
#define FNVNX_PLAT_VM_RESERVE 4u
#define FNVNX_PLAT_VM_COMMIT 5u
#define FNVNX_PLAT_VM_DECOMMIT 6u
#define FNVNX_PLAT_VM_RELEASE 7u
/* VM_PROTECT (address, size, prot) -> 0 or a result. prot: bit 0 readable, bit 1 writable, 0 = no access; never
 * executable. Works on whole pages the guest owns: map_fixed memory and committed pages of a VM reservation.
 * DECOMMIT returns a page to zeroed read-write whatever VM_PROTECT set before. Results: 0xD401 a page of the range
 * is not the guest's (the guest module, host pages, unmapped), 0xD801 prot has other bits, 0xCA01 size is 0 or not
 * page aligned, 0xCC01 address not page aligned. The loader sets the PE section protection and the stack guard
 * pages with it; the platform's VirtualProtect uses the same call. */
#define FNVNX_PLAT_VM_PROTECT 8u
#define FNVNX_VM_PROT_R 1u
#define FNVNX_VM_PROT_W 2u
/* DISPLAY_DOCKED () -> 1 docked (1080p output), 0 handheld (720p) */
#define FNVNX_PLAT_DISPLAY_DOCKED 9u
/* 16 and above: guest sockets (platform/, table published by the platform lead) */

/* Thread placement (docs/PERF.md 6.3): roles for thread_create's cpu argument; the host's table
 * (toolchain/host/placement.h) gives each an ideal core, an affinity mask and a priority. */
enum {
    FNVNX_ROLE_DEFAULT = 0,       /* game threads without a role of their own (Havok, ...) */
    FNVNX_ROLE_MAIN = 1,          /* the main thread */
    FNVNX_ROLE_AI_LINEAR_1 = 2,   /* AI Linear Task Thread (1) */
    FNVNX_ROLE_AI_LINEAR_2 = 3,   /* AI Linear Task Thread 2 */
    FNVNX_ROLE_RENDER_WORKER = 4, /* the renderer's worker (Gallium Nine's CSMT thread, a host pthread) */
    FNVNX_ROLE_IO = 5,            /* IO and background loading (the Serialized IO Thread, ...) */
    FNVNX_ROLE_AUDIO = 6,         /* audio (the platform's mixer pump, the game's LibAudioUpdate) */
    FNVNX_ROLE_TASKLET = 7,       /* tasklet workers */
    FNVNX_ROLE_LOADING = 8,       /* the loading screen (LoadingMenuThread): CPU boost while it runs */
    FNVNX_ROLE_COUNT
};
#define FNVNX_THREAD_ROLE(r) (0x100u | (unsigned)(r))
#define FNVNX_CPU_KEEP 0xFFu
#define FNVNX_CPU_SPEC(ideal, mask) ((1ull << 63) | ((uint64_t)(uint32_t)(mask) << 8) | ((uint64_t)(ideal) & 0xFFu))
/* Host side (LP64) only: register a handler for one module id (< 64); returns 0, or -1 if taken/out of range. */
typedef uint64_t (*fnvnx_ext_handler)(uint32_t fn, const uint64_t *args, uint32_t nargs);
int fnvnx_host_register_ext(uint32_t module, fnvnx_ext_handler handler);

/* input_poll events */
#define FNVNX_EV_KEY          1    /* code = USB HID usage, a = down */
#define FNVNX_EV_MOUSE_MOVE   2    /* a = dx, b = dy, c = wheel */
#define FNVNX_EV_MOUSE_BUTTON 3    /* code = button 0.., a = down */
#define FNVNX_EV_TOUCH        4    /* code = finger id, a = x, b = y (one per finger of the current frame) */
#define FNVNX_EV_TOUCH_END    5    /* code = finger count: end of a touch frame */
typedef struct FnvnxInputEvent {
    uint32_t type, code;
    int32_t a, b, c;
    uint32_t reserved;
} FnvnxInputEvent;

/* fs_open flags and fs_seek whence (same values as platform/os/os.h OS_O_* / OS_SEEK_*). Errors are negated
 * errno values as in os.h (OS_ENOENT 2, ...). */
#define FNVNX_O_READ   0x01
#define FNVNX_O_WRITE  0x02
#define FNVNX_O_CREATE 0x04
#define FNVNX_O_EXCL   0x08
#define FNVNX_O_TRUNC  0x10
#define FNVNX_O_APPEND 0x20

/* Shared structs (identical layout in LP64 and ILP32). */
typedef struct FnvnxStat {
    uint32_t is_dir, read_only;
    uint64_t size;
    int64_t mtime_us, atime_us, ctime_us;
} FnvnxStat;

typedef struct FnvnxMemInfo {
    uint64_t total_phys, avail_phys;
} FnvnxMemInfo;

#if defined(__cplusplus)
static_assert(sizeof(FnvnxHostApi) == 0x1C0, "FnvnxHostApi layout");
static_assert(sizeof(FnvnxStat) == 40 && sizeof(FnvnxMemInfo) == 16 && sizeof(FnvnxInputEvent) == 24,
               "shared struct layout");
#else
_Static_assert(sizeof(FnvnxHostApi) == 0x1C0, "FnvnxHostApi layout");
_Static_assert(sizeof(FnvnxStat) == 40 && sizeof(FnvnxMemInfo) == 16 && sizeof(FnvnxInputEvent) == 24,
               "shared struct layout");
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
typedef uint64_t (*fnvnx_hfn4)(uint64_t, uint64_t, uint64_t, uint64_t);
typedef uint64_t (*fnvnx_hfn5)(uint64_t, uint64_t, uint64_t, uint64_t, uint64_t);
static inline uint64_t fnvnx_hcall0(uint64_t fn) { return ((fnvnx_hfn0)(uintptr_t)fn)(); }
static inline uint64_t fnvnx_hcall1(uint64_t fn, uint64_t a) { return ((fnvnx_hfn1)(uintptr_t)fn)(a); }
static inline uint64_t fnvnx_hcall2(uint64_t fn, uint64_t a, uint64_t b) { return ((fnvnx_hfn2)(uintptr_t)fn)(a, b); }
static inline uint64_t fnvnx_hcall3(uint64_t fn, uint64_t a, uint64_t b, uint64_t c) {
    return ((fnvnx_hfn3)(uintptr_t)fn)(a, b, c);
}
static inline uint64_t fnvnx_hcall4(uint64_t fn, uint64_t a, uint64_t b, uint64_t c, uint64_t d) {
    return ((fnvnx_hfn4)(uintptr_t)fn)(a, b, c, d);
}
static inline uint64_t fnvnx_hcall5(uint64_t fn, uint64_t a, uint64_t b, uint64_t c, uint64_t d, uint64_t e) {
    return ((fnvnx_hfn5)(uintptr_t)fn)(a, b, c, d, e);
}

/* Set by the guest runtime before main() runs. */
extern const FnvnxHostApi *fnvnx_host;
#endif

#endif /* FNVNX_HOSTAPI_H */
