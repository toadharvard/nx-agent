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
#include "fnvnx/sockwire.h"

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
/* protect (address, size, OS_VM_* bits of platform/os/os.h) -> 0, or nonzero when the range is not inside one
 * reservation or the permission cannot be applied */
#define FNVNX_PLAT_VM_PROTECT 8u
/* The console's operation mode: () -> 1 docked (AppletOperationMode_Console), 0 handheld. The host always
 * implements it (toolchain/host/host.c platform_ext); a host without it is a contract defect, not a state. */
#define FNVNX_PLAT_DISPLAY_DOCKED 9u
/* 10..15 are left free for the VM family. */

/* ---- BSD sockets (FNVNX_PLAT_SOCK_*, services 16..33; guest: toolchain/guest/sock_guest.c, host:
 * toolchain/host/host_sock.inc; the wire constants and structs: fnvnx/sockwire.h). The rule: EVERY BSD-socket
 * operation of the guest is one of these services; the guest's C library has no network device of its own.
 *  - Arguments are the uint64 words of ext_call's args array; nargs must be exactly the count shown, else the
 *    result is -FNVNX_WE_INVAL. A pointer argument is a guest address zero-extended to 64 bits (one shared address
 *    space, as for fs_read); 0 is the null pointer where "or 0" is said.
 *  - Result: an int64 in a uint64, value >= 0, or -errno with errno a WIRE number (sockwire.h FNVNX_WIRE_ERRNO_LIST,
 *    the Linux numbers). The guest translates wire errno into newlib's and the host translates its own into the wire
 *    ones, both by name, so nothing assumes that two systems' errno numbers agree. FNVNX_EXT_NO_MODULE is what a host
 *    without these services returns for every one of them.
 *  - A socket handle h is the host's descriptor number; the guest keeps it inside its own descriptor table (a newlib
 *    device "soc"), so close/read/write/dup/fcntl on a socket descriptor reach these services.
 *  - Blocking: a blocking call (connect, accept, sendto, recvfrom, poll with a timeout, the resolution of
 *    GAI_OPEN) blocks only the calling guest thread: ext_call runs on the caller's own thread, as audio_submit and
 *    the file services do. A new socket is blocking until FNVNX_PLAT_SOCK_SETFL says otherwise. There are no signals:
 *    EINTR does not occur and sends never raise SIGPIPE (the host passes MSG_NOSIGNAL where the OS has it). */
#define FNVNX_PLAT_SOCK_SOCKET 16u      /* (domain WAF_*, type WSOCK_*, protocol IANA) -> h */
#define FNVNX_PLAT_SOCK_CLOSE 17u       /* (h) -> 0; the handle is gone whatever the result */
#define FNVNX_PLAT_SOCK_BIND 18u        /* (h, const FnvnxWireSockaddr *) -> 0 */
#define FNVNX_PLAT_SOCK_LISTEN 19u      /* (h, backlog) -> 0 */
#define FNVNX_PLAT_SOCK_ACCEPT 20u      /* (h, FnvnxWireSockaddr *peer or 0) -> new h (blocking mode); blocks until a
                                           connection arrives, -EAGAIN when non-blocking and none is pending */
#define FNVNX_PLAT_SOCK_CONNECT 21u     /* (h, const FnvnxWireSockaddr *) -> 0; non-blocking: -FNVNX_WE_INPROGRESS */
#define FNVNX_PLAT_SOCK_SENDTO 22u      /* (h, buf, len, FNVNX_WIRE_MSG_LIST flags, const FnvnxWireSockaddr *to or 0)
                                           -> bytes sent; len > 0x7FFFFFFF is -FNVNX_WE_INVAL */
#define FNVNX_PLAT_SOCK_RECVFROM 23u    /* (h, buf, len, flags, FnvnxWireSockaddr *from or 0) -> bytes received (0 =
                                           orderly shutdown of a stream); *from is written on every success (family
                                           WAF_UNSPEC when the socket has no source address) */
#define FNVNX_PLAT_SOCK_SETSOCKOPT 24u  /* (h, level, option, const void *val, vallen) -> 0; level FNVNX_WLVL_SOCKET or
                                           an IANA protocol number; option and value shape: the FNVNX_WIRE_*_OPT_LISTs */
#define FNVNX_PLAT_SOCK_GETSOCKOPT 25u  /* (h, level, option, void *val, uint32_t *vallen; in: capacity, out: size of
                                           the option's value) -> 0; at most the capacity is written */
#define FNVNX_PLAT_SOCK_POLL 26u        /* (FnvnxWirePollfd *fds, nfds, timeout_ms int32, -1 = forever) -> number of
                                           entries with revents != 0; fd < 0 entries are ignored, an fd that is not an
                                           open socket gets FNVNX_WPOLLNVAL */
#define FNVNX_PLAT_SOCK_SHUTDOWN 27u    /* (h, how 0 read / 1 write / 2 both) -> 0 */
#define FNVNX_PLAT_SOCK_GETSOCKNAME 28u /* (h, FnvnxWireSockaddr *out) -> 0 */
#define FNVNX_PLAT_SOCK_GETPEERNAME 29u /* (h, FnvnxWireSockaddr *out) -> 0 */
#define FNVNX_PLAT_SOCK_GETFL 30u       /* (h) -> FNVNX_WO_RDWR | (non-blocking ? FNVNX_WO_NONBLOCK : 0) */
#define FNVNX_PLAT_SOCK_SETFL 31u       /* (h, flags) -> 0; only FNVNX_WO_NONBLOCK of flags is applied (POSIX F_SETFL) */
/* getaddrinfo is two services: its result list lives in host memory, which a 32-bit guest cannot address. GAI_OPEN
 * resolves once and keeps the result under a ticket; GAI_FETCH copies it into a guest buffer. */
#define FNVNX_PLAT_SOCK_GAI_OPEN 32u    /* (node or 0, service or 0: guest C strings, const FnvnxWireAddrinfo *hints or 0,
                                           int32_t *sys_errno or 0) -> ticket > 0, or -FNVNX_WEAI_x (sockwire.h; for
                                           FNVNX_WEAI_SYSTEM the wire errno is stored to *sys_errno) */
#define FNVNX_PLAT_SOCK_GAI_FETCH 33u   /* (ticket, buf or 0, cap) -> total bytes of the result: a sequence of
                                           FnvnxWireAddrinfo records each followed by its canonical name bytes, padded
                                           to 8. cap < total: nothing is written, the ticket stays. cap >= total: the
                                           records are written and the ticket is freed (buf 0: nothing
                                           is written and the ticket is freed: how a result that cannot be taken
                                           is released). -FNVNX_WE_INVAL for a ticket
                                           that is not open. */
#define FNVNX_PLAT_SOCK_FIRST 16u
#define FNVNX_PLAT_SOCK_LAST 33u

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

#if defined(__ILP32__) || defined(FNVNX_HOST_CALLS)
/* (FNVNX_HOST_CALLS: the same helpers in an LP64 test process, tests/s4/sockwire.)
 * Guest-side helpers: call an LP64 host function through its slot. */
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
