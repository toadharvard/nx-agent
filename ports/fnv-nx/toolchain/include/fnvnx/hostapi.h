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
    uint64_t sleep_ns;             /* (ns) -> 0; ns above INT64_MAX sleeps INT64_MAX ns */
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
                                      (toolchain/host/placement.h). Not started. The id is a table handle
                                      ((generation << 16) | slot, host_ids.inc), never an address; at most 1024
                                      live thread and directory ids together: 0 when the table is full. */
    uint64_t thread_start;         /* (id) -> 0 or Result; an id that names no live thread -> FNVNX_HE_NOTFOUND; a thread that is already
                                      started (also a THREAD_SELF record) -> FNVNX_HE_BADINPUT, nothing changed */
    uint64_t thread_join;          /* (id) -> 0 when the thread has ended; unknown id -> FNVNX_HE_NOTFOUND; a thread
                                      that was never started, or a THREAD_SELF record -> FNVNX_HE_BADINPUT (it would never end) */
    uint64_t thread_free;          /* (id) -> 0; after join, or detaches a running thread (a THREAD_SELF record: the record goes, the thread is not touched). The id dies at once: a second
                                      free, or any later use of it -> FNVNX_HE_NOTFOUND */
    uint64_t thread_exit;          /* () -> does not return; ends the calling host-created thread (a thread that thread_create did not
                                      make, the main thread included, must not call it: not detected) */
    uint64_t thread_yield;         /* () -> 0 */
    uint64_t thread_set_priority;  /* (id or 0 = self, prio) -> 0 or Result; unknown id -> FNVNX_HE_NOTFOUND */
    uint64_t thread_set_cpu;       /* (id or 0 = self, spec) -> 0 or Result; unknown id -> FNVNX_HE_NOTFOUND;
                                      a negative spec (bits 40..62 set) -> 0, nothing changed. spec 0..63: that core only;
                                      FNVNX_CPU_SPEC(ideal, mask): ideal core (FNVNX_CPU_KEEP: keep) and
                                      affinity mask (0: keep), the ideal core joins the mask */
    uint64_t wait_on_address;      /* (addr, expected u32, timeout_ns) -> 0 woken, 1 timed out, 2 value != expected
                                      (svcWaitForAddress WaitIfEqual; addr 4-aligned); timeout_ns above INT64_MAX waits
                                      forever; any other kernel result unchanged (0xCC01 addr not 4-aligned, 0xD401 addr
                                      not readable guest memory) */
    uint64_t wake_address;         /* (addr, count; count > 0x7FFFFFFF: all) -> 0 or the kernel's result of
                                      svcSignalToAddress (0xCC01 addr not 4-aligned, 0xD401 not guest memory) */
    uint64_t wall_time_us;         /* () -> microseconds since 1970 UTC */
    uint64_t tz_offset_minutes;    /* () -> off_s / 60 (C division) of TZ_AT at the moment of the call (int64 as u64); 0 when TZ_AT
                                      does not answer 0 for that instant. The platform no longer uses this slot (it calls TZ_AT). */
    uint64_t random64;             /* () -> random u64 */
    uint64_t memory_info;          /* (FnvnxMemInfo *) -> 0 (avail_phys = 0 when used > total) */
    uint64_t cpu_count;            /* () -> cores the game may use: 3 (application cores 0-2), 4 with the platform config core3=1 */
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
    uint64_t fs_dir_open;          /* (path) -> dir id (!= 0, a table handle like thread ids) or 0: not a directory, no such path or the
                                      id table is full */
    uint64_t fs_dir_next;          /* (dir, buf, cap) -> name length (without NUL), 0 at end; skips . and ..; a name longer than
                                      cap - 1 is cut to cap - 1 bytes; cap 0 or an id that names no open directory -> 0 (nothing
                                      consumed) */
    uint64_t fs_dir_close;         /* (dir) -> 0; the id dies; an id that names no open directory: 0, nothing done */

    /* ---- version 3: audio out and input (platform/audio, platform/input via platform/guest/platform_glue) */
    uint64_t audio_open;           /* (rate = 48000, period frames 1..8192) -> 0 or negative (int64 as u64); stereo PCM16.
                                      -1 rate != 48000 or period 0 or > 8192; -2 audren init failed; -3 driver create failed;
                                      -4 no memory for the pool; -5 renderer start failed. Already open: 0, arguments ignored */
    uint64_t audio_submit;         /* (pcm, frames = period) -> 0; blocks until the device takes it; -1 not open or frames !=
                                      the open period (nothing read) */
    uint64_t audio_close;          /* () -> 0; not open: 0, nothing done */
    uint64_t pad_read;             /* (index 0..3, fnvnx_pad_raw *) -> 1 connected, 0 not; index > 3 -> 0, nothing written */
    uint64_t pad_vibrate;          /* (index, float bits low, float bits high) -> 0; amplitudes 0..1; index > 3 -> 0, nothing done */
    uint64_t input_poll;           /* (FnvnxInputEvent *, cap) -> events written (<= cap; cap 0: none) since the last poll */
    uint64_t map_fixed;            /* (addr, size, src) -> 0 or Result: makes [addr, addr+size) read-write memory at exactly
                                      addr from src, a page-aligned guest heap block of the same size (it becomes
                                      inaccessible until UNMAP_FIXED). The mapping is reprotectable (VM_PROTECT). Checks in
                                      this order: size 0, addr+size wraps, addr or size not 4 KiB aligned -> FNVNX_HE_BADINPUT
                                      (0x1759); [addr, addr+size) inside a window the host claimed at start (the original
                                      image range 0x00400000..0x00FDF000, the data window 0x00FDF000..0x02100000): the range
                                      becomes read-write and zeroed, src is not looked at; src 0 or not aligned ->
                                      FNVNX_HE_BADINPUT; 512 map_fixed ranges live -> FNVNX_HE_NOMEM (0x0559); a page of the range
                                      is mapped (svcQueryMemory type != unmapped, or the range crosses into a mapped page) ->
                                      FNVNX_HE_ALREADY (0x0F59), nothing mapped; else the kernel's result unchanged (0xDA01 addr
                                      outside the alias-code region, 0xD401 src is not ordinary heap). */
    uint64_t ext_call;             /* (module, fn, const uint64_t *args, nargs) -> handler result. Generic extension point:
                                      host-side modules (e.g. the D3D9 proxy of render/nine) register with
                                      fnvnx_host_register_ext(). module >= 64 or not registered -> FNVNX_EXT_NO_MODULE; fn
                                      above UINT32_MAX -> FNVNX_EXT_NO_SERVICE (the handler is not called); nargs above
                                      UINT32_MAX is passed as UINT32_MAX. The results of module 11 are the table below. */
} FnvnxHostApi;

/* ext_call results that belong to no service (all three are -255, -254, -253 as int64; no service answers one of them otherwise):
 *   NO_MODULE  the module number is >= 64 or no host module is registered under it
 *   NO_SERVICE the module is registered but has no service of that number (module 11: fn 0, 27..29, 32, 33 and 37 and up)
 *   BAD_NARGS  module 11: fewer arguments than the service takes (the column "n" of the table below), or nargs > 0 with a null array */
#define FNVNX_EXT_NO_MODULE 0xFFFFFFFFFFFFFF01ull
#define FNVNX_EXT_NO_SERVICE 0xFFFFFFFFFFFFFF02ull
#define FNVNX_EXT_BAD_NARGS 0xFFFFFFFFFFFFFF03ull
/* Result values the host returns, as numbers (Horizon result = module | description << 9). libnx module 345: BadInput is
 * LibnxError 11, AlreadyInitialized 7, NotFound 9, OutOfMemory 2 (switch/result.h); kernel module 1: InvalidSize 101, InvalidAddress 102,
 * InvalidCurrentMemory 106, InvalidNewMemoryPermission 108. */
#define FNVNX_HE_NOMEM    0x0559u   /* LibnxError_OutOfMemory: a host table is full, or a block could not be allocated */
#define FNVNX_HE_ALREADY  0x0F59u   /* LibnxError_AlreadyInitialized: map_fixed of a range that is already mapped */
#define FNVNX_HE_NOTFOUND 0x1359u   /* LibnxError_NotFound: an id that names nothing; the own-process handle is missing */
#define FNVNX_HE_BADINPUT 0x1759u   /* LibnxError_BadInput: an argument the service cannot take (see the service) */
#define FNVNX_HE_SIZE     0xCA01u   /* kernel InvalidSize: size 0 or not a 4 KiB multiple */
#define FNVNX_HE_ADDR     0xCC01u   /* kernel InvalidAddress: address not 4 KiB aligned */
#define FNVNX_HE_RANGE    0xD401u   /* kernel InvalidCurrentMemory: a page of the range is not memory this service works on */
#define FNVNX_HE_PERM     0xD801u   /* kernel InvalidNewMemoryPermission */
/* ext_call module of the host's platform services (toolchain/host/host.c; render/nine is 9, render/native 10) */
#define FNVNX_EXT_PLATFORM 11u
#define FNVNX_EXT_NINE 9u    /* module of the D3D9 proxy (render/nine); its services belong to the render lead */
#define FNVNX_EXT_DK 12u     /* machine model of the deko3d API (dk*): served by the emulator machine (verify/bootdk.py) in host-test ELFs only; in the product dk* is the
                                real deko3d, so this module is no service of the platform and has no FNVNX_PLAT_* number: rule "used = declared" is about the services of module 11 */
#define FNVNX_EXT_GPU 10u    /* module of the native renderer (render/native); its services belong to the render lead */
#define FNVNX_PLAT_CPU_BOOST 1u   /* (mode: 0 normal, 1 fast load; any non-zero = fast) -> 0 set (or already in that mode), 1 off by the
                                     platform config (boost_loading=0), else the Horizon result of appletSetCpuBoostMode (the
                                     mode stays as it was) */
#define FNVNX_PLAT_THREAD_ACTIVITY 2u   /* (host thread id, paused: 0 resume, non-zero pause) -> 0, or the Horizon result of
                                     svcSetThreadActivity; id 0 (the calling thread cannot pause itself) -> FNVNX_HE_BADINPUT;
                                     an id that names no live thread_create thread or THREAD_SELF record -> FNVNX_HE_NOTFOUND;
                                     a thread not started yet or already finished -> FNVNX_HE_BADINPUT, nothing done. A pause
                                     returns only when the thread is actually stopped (svcGetThreadContext3 succeeds); a resume
                                     returns at once. */
#define FNVNX_PLAT_MESSAGE 3u           /* (caption, text: guest C strings, 0 reads as "") -> 0 after the player closed the dialog,
                                     or the applet's Horizon result */
/* VirtualAlloc's address space (toolchain/host/host_vm.inc). The pages the guest owns there (committed extents) and the
 * map_fixed ranges are REPROTECTABLE memory (VM_PROTECT below). Ranges are widened outward to 4 KiB pages.
 *   4 VM_RESERVE (hint or 0, size)  -> the reservation's address (64 KiB aligned) or 0. 0: size 0 or above 4 GiB, no free slot
 *                                      (256), a hint that is not free (a 64 KiB-rounded hint range must be unmapped, outside
 *                                      0x00400000..0x02100000, below 4 GiB and not inside another reservation: it is never moved),
 *                                      or no free range. size is rounded up to 64 KiB.
 *   5 VM_COMMIT (address, size)     -> 0 or a result. Maps zeroed read-write memory under every page of the range that is not
 *                                      committed yet; already committed pages are left as they are (contents and protection).
 *                                      size 0, addr+size wraps, or the range not inside ONE reservation -> FNVNX_HE_BADINPUT; a
 *                                      page of a section view or of a map_fixed range -> FNVNX_HE_RANGE, nothing done; no memory
 *                                      -> FNVNX_HE_NOMEM; else the kernel's result.
 *   6 VM_DECOMMIT (address, size)   -> 0 or a result. An extent wholly inside the range is unmapped (any protection); a partly
 *                                      covered one is zeroed and read-write and stays mapped. Pages of the range that are not
 *                                      committed are ignored; size 0 -> 0, nothing done. addr+size wraps, or a page of a section
 *                                      view or a map_fixed range -> FNVNX_HE_RANGE, nothing done.
 *   7 VM_RELEASE (address)          -> 0 or a result. address must be exactly a reservation's base, else FNVNX_HE_BADINPUT. Unmaps
 *                                      everything committed in it (DECOMMIT of the whole reservation: its results, e.g. a view
 *                                      in it -> FNVNX_HE_RANGE, and the reservation stays) and frees the address range. */
#define FNVNX_PLAT_VM_RESERVE 4u
#define FNVNX_PLAT_VM_COMMIT 5u
#define FNVNX_PLAT_VM_DECOMMIT 6u
#define FNVNX_PLAT_VM_RELEASE 7u
/* 8 VM_PROTECT (address, size, prot) -> 0 or a result. prot: 0 no access, 1 read, 3 read-write (bit 0 readable, bit 1 writable;
 * write-only 2 does not exist on the console, the kernel has no such permission); never executable.
 * Works on whole 4 KiB pages the guest owns: committed extents of a VM reservation, map_fixed ranges, and the windows the host
 * claimed at start (the original image range 0x00400000..0x00FDF000 and the data window 0x00FDF000..0x02100000). Every
 * permission R, RW and none is expressible on all of them: the host builds each of these pages in the kernel state AliasCodeData
 * (a code-memory alias) because the kernel reprotects exactly the states Normal, CodeData and AliasCodeData and refuses Stack
 * (what svcMapMemory gives) and SharedCode (section views); the proofs with file:line are in host_vm.inc K1..K5. A range may
 * span pages of different protections and of different extents, ranges and windows, but every page must be guest-owned.
 * Checks in this order: address not 4 KiB aligned -> FNVNX_HE_ADDR; size 0 or not a 4 KiB multiple -> FNVNX_HE_SIZE; address+size
 * wraps -> FNVNX_HE_RANGE; prot is not 0, 1 or 3 (X, write-only, anything else) -> FNVNX_HE_PERM; a page that is not guest-owned (the guest
 * module, host memory, a section view, committed nowhere, unmapped) -> FNVNX_HE_RANGE, nothing changed; then the kernel's results
 * unchanged. The change is applied run by run; if the kernel refuses a run (its block slab is exhausted) the runs before it keep
 * the new protection. UNMAP_FIXED and DECOMMIT work on pages of any protection, and a page that stays mapped after a partial
 * DECOMMIT is read-write: nothing needs restoring first (the loader may still do it). The loader sets the PE section protection
 * and the stack guard pages with this call; the platform's VirtualProtect uses the same call. */
#define FNVNX_PLAT_VM_PROTECT 8u
#define FNVNX_VM_PROT_R 1u
#define FNVNX_VM_PROT_W 2u
/* 9 DISPLAY_DOCKED () -> 1 docked (1080p output), 0 handheld (720p): appletGetOperationMode() == AppletOperationMode_Console.
 * Takes no argument: nargs 0 is the call; nargs > 0 with a null argument array -> FNVNX_EXT_BAD_NARGS; otherwise the arguments are ignored. */
#define FNVNX_PLAT_DISPLAY_DOCKED 9u
/* ACCESS_FAULT_HANDLER {handler, stack_top} -> 0. Registers, for the CALLING thread, the guest function
 * `uint64_t handler(FnvnxFaultFrame *frame)` and the top of a separate stack it runs on (16-byte aligned, guest memory).
 * On a data or instruction access fault of that thread the host fills a frame at stack_top - sizeof(frame), switches to
 * that stack and calls handler with faults of the same kind masked (a fault inside the handler ends the process).
 * Return 0: the host loads every register from the frame (the handler may have changed any of them, pc included) and
 * resumes. Return non-zero: the process ends with that value as exit code. A thread without a handler: the fault ends
 * the process. handler == 0 unregisters.
 * Registration results: stack_top not 16-byte aligned, below sizeof(FnvnxFaultFrame) or >= 4 GiB -> 0xCC01 (nothing
 * registered). The frame's `pstate` carries only the condition flags NZCV (bits 31..28); every other bit reads as 0 and is
 * ignored when the host resumes. A fault taken while the HOST dereferences a bad guest pointer passed to a host slot is not
 * delivered to the guest: the process ends.
 * Served by the console host: toolchain/host/host_fault.inc (the delivery rule and its proof), row 10 of host_platform.inc. */
#define FNVNX_PLAT_ACCESS_FAULT_HANDLER 10u
typedef struct FnvnxFaultFrame {
    uint32_t kind;        /* 0 read, 1 write, 2 execute */
    uint32_t reason;      /* 0 unmapped, 1 protection, 2 alignment */
    uint64_t addr;        /* faulting data address (kind 0, 1) or pc (kind 2) */
    uint64_t pc, sp, pstate;
    uint64_t x[31];       /* x0..x30 */
    uint32_t fpcr, fpsr;
    uint64_t v[32][2];    /* v0..v31, low then high 64 bits */
} __attribute__((aligned(16))) FnvnxFaultFrame;
typedef char fnvnx_fault_frame_is_816_bytes[sizeof(FnvnxFaultFrame) == 816 ? 1 : -1];   /* both sides of the host boundary */
/* Memory sections: SHARED views of the same pages (Win32 CreateFileMapping over the paging file + MapViewOfFile
 * FILE_MAP_WRITE). Implemented by toolchain/host/host_section.inc; the proofs from the Mesosphere sources (commit
 * 4e3f37038d9e82dde7054897213ab32b2ecf651f) are in that file's header. All four take their arguments as a uint64 array
 * like the VM services. A result with the top bit set is an error: -(code) as int64, code from the table below;
 * a non-negative result is the value (id, address, 0).
 *   11 SECTION_CREATE (size)                 -> id (1..0x7FFFFFFF) | error. A zeroed, page-aligned backing of `size` bytes
 *                                               (4 KiB multiple, 0 < size <= 2 GiB) that is never given an address.
 *   12 SECTION_MAP (id, addr, offset, size)  -> the view's address | error. A READ-WRITE view of [offset, offset+size)
 *                                               of the section (offset, size multiples of 4 KiB, inside the section).
 *                                               addr != 0: the view is placed at exactly addr, which must lie inside a
 *                                               VM reservation (VM_RESERVE) and be kernel-free there (nothing committed,
 *                                               no other view); addr == 0: the host takes a fresh range of its own (not a VM reservation; 64 KiB
 *                                               aligned) of its own for the view and returns it. Views of one section may
 *                                               overlap, and see each other's writes at once (same physical pages).
 *   13 SECTION_UNMAP (addr, size)            -> 0 | error. Exactly a view (same addr and size as SECTION_MAP made it).
 *                                               The other views and the data stay. A host-reserved range is released.
 *   14 SECTION_FREE (id)                     -> 0 | error. The id dies at once (MAP with it fails with 0xE401); the pages
 *                                               live until the last view is unmapped (the kernel needs the backing mapped
 *                                               to unmap a view), then they are released. Deferred, never refused.
 * 15 is UNMAP_FIXED below. Error codes (the Horizon result values the kernel uses for the same condition, kernel results of a
 * failing svc are passed through unchanged):
 *   0xCA01 size is 0, not a 4 KiB multiple or too large   0xCC01 addr or offset not 4 KiB aligned
 *   0xD401 range outside the section; addr range not free in the kernel; not exactly a view
 *   0xDA01 addr range not inside a VM reservation          0xE401 unknown id (never created, or freed)
 *   0xD001 out of address space or memory                  0xCE01 table full (128 sections, 512 views)
 * Order of checks of SECTION_MAP (host_section.inc): size (0, not a 4 KiB multiple, or > 2 GiB) -> 0xCA01; then alignment of
 * addr and offset -> 0xCC01; then the id -> 0xE401; then offset + size inside the section -> 0xD401; then the view table
 * -> 0xCE01; then the address (0xDA01 / the kernel's result). The size check therefore comes before the range check.
 * Interplay with the VM services 4..8 (a view is memory the VM service did not commit): VM_DECOMMIT of a range containing
 * a page of a view -> 0xD401, nothing done (it is the first check: host_vm_decommit has no reservation check). VM_COMMIT:
 * a range that is not inside one VM reservation (or size 0) is refused FIRST with FNVNX_HE_BADINPUT (0x1759); only
 * a range inside a reservation is then checked for view pages -> 0xD401, nothing done. map_fixed (slot above) of a range
 * with any view page, whether the view was placed by the host (addr == 0) or inside a reservation, is refused the same way
 * as any occupied range: svcQueryMemory reports a type other than Unmapped (a view is MemType_ProcessMem) and the host
 * returns FNVNX_HE_ALREADY (0x0F59), nothing is mapped; VM_RELEASE of a reservation that still holds a view ->
 * 0xD401 (its decommit is refused); VM_PROTECT of a view page -> 0xD401 (SharedCode has no CanReprotect, see host_section.inc).
 * NOT provided, by the mechanism: a view is always read-write (the kernel fixes the alias permission to UserReadWrite),
 * so there is no read-only view of a section and no copy-on-write view; no view with a permission different from
 * another view of the same section; no view of a file or of a section that is not a SECTION_CREATE object. */
#define FNVNX_PLAT_SECTION_CREATE 11u
#define FNVNX_PLAT_SECTION_MAP 12u
#define FNVNX_PLAT_SECTION_UNMAP 13u
#define FNVNX_PLAT_SECTION_FREE 14u
#define FNVNX_SEC_E_SIZE 0xCA01u
#define FNVNX_SEC_E_ALIGN 0xCC01u
#define FNVNX_SEC_E_RANGE 0xD401u
#define FNVNX_SEC_E_REGION 0xDA01u
#define FNVNX_SEC_E_ID 0xE401u
#define FNVNX_SEC_E_NOMEM 0xD001u
#define FNVNX_SEC_E_FULL 0xCE01u
#define FNVNX_SEC_E_NOSERVICE 0xFFu   /* the host answers FNVNX_EXT_NO_MODULE (= -255): an older host without sections */
#define FNVNX_SEC_MAX_SIZE 0x80000000ull
/* 15 UNMAP_FIXED (addr, size, src) -> 0 or an error, the undo of the host slot map_fixed. (addr, size, src) must be exactly
 * the arguments of one live, successful map_fixed call (src: the page-aligned guest heap block that call was given). Effect: the
 * range stops being mapped; the source heap block becomes ordinary accessible heap again, with the contents the guest had left in
 * the mapped range, and the guest frees it. The range is unmapped, so a later map_fixed of it, or any VM service reservation,
 * can use it again. The pages may have ANY protection (VM_PROTECT R, RW or none): nothing needs restoring first. A range inside
 * the window the host claimed at startup (where map_fixed only zeroes, see map_fixed) is made read-write and zeroed again and
 * stays the host's window; src is not looked at there. Results, in this order: addr or src not 4 KiB aligned -> FNVNX_HE_ADDR
 * (0xCC01); size 0 or not a 4 KiB multiple -> FNVNX_HE_SIZE (0xCA01); addr+size wraps -> FNVNX_HE_RANGE (0xD401); not a window range
 * and no live map_fixed with exactly these arguments (also a second unmap of the same call) -> FNVNX_HE_RANGE, nothing changed;
 * else the kernel's result. */
#define FNVNX_PLAT_UNMAP_FIXED 15u
/* TOTALITY TABLE BEGIN
 * The guest <-> host boundary is TOTAL: every service has one specified result for every input and one host implementation. Columns:
 * service, n = least nargs (fewer -> FNVNX_EXT_BAD_NARGS; extra arguments are ignored), results (numbers: FNVNX_HE_* above), host
 * implementation (toolchain/host/), unit tests (names `test:` in tests/s4/backends/<name>_test.c, run under qemu-aarch64 against the kernel double
 * kdouble.h; hostapi_total.py fails when a declared service has no row in host_platform.inc or a test name here is not in any test).
 * Guest pointers (strings, buffers, FnvnxStat, ...) must be readable/writable guest memory: a host access that faults ends the process.
 * An id (thread, directory) is used by one thread at a time while it is freed (the guest's side of the contract).
 *
 * ext_call (slot): module >= 64 or not registered -> NO_MODULE (-255); fn > UINT32_MAX -> NO_SERVICE (-254); nargs > UINT32_MAX counts as
 *   UINT32_MAX; host_ext.inc:h_ext_call, fnvnx_host_register_ext (a null handler or a taken module: -1)      test:total_ext test:test_ext_and_map
 * module 11, platform_ext (host_platform.inc, one row per service); fn 0, 27..29, 32, 33 and fn >= 37 -> NO_SERVICE (-254):
 *   test:total_unknown test:total_nargs test:total_declared
 *  1 CPU_BOOST          n=1  0 set / already that mode | 1 boost_loading=0 | the Horizon result of appletSetCpuBoostMode, mode unchanged
 *                            host_platform.inc:pf_cpu_boost,set_cpu_boost                                    test:cpu_boost
 *  2 THREAD_ACTIVITY    n=2  0 | the kernel's result | 0x1759 id 0, thread not started or finished | 0x1359 id names no live thread
 *                                                                                                              test:thread_activity test:thread_ids
 *                            host_platform.inc:pf_thread_activity, host_thread_activity.inc:host_thread_activity
 *  3 MESSAGE            n=2  0 after the dialog | the applet's Horizon result (null string = "")
 *                            host_message.inc:host_show_message                                              test:message
 *  4 VM_RESERVE         n=2  address | 0 (size 0 / > 4 GiB, no slot, hint not free, no range)
 *                            host_vm.inc:host_vm_reserve                                                      test:vm_reserve
 *  5 VM_COMMIT          n=2  0 | 0x1759 (size 0, wraps, not inside one reservation) | 0xD401 (view or map_fixed page) | 0x0559 no memory | kernel
 *                            host_vm.inc:host_vm_commit                                                       test:vm_commit
 *  6 VM_DECOMMIT        n=2  0 (size 0: nothing done) | 0xD401 (wraps, view or map_fixed page) | kernel
 *                            host_vm.inc:host_vm_decommit                                                     test:vm_decommit
 *  7 VM_RELEASE         n=1  0 | 0x1759 (not exactly a reservation's base) | decommit's results
 *                            host_vm.inc:host_vm_release                                                      test:vm_release
 *  8 VM_PROTECT         n=3  0 | 0xCC01 | 0xCA01 | 0xD401 (wraps, page not guest-owned) | 0xD801 (prot not 0, 1, 3) | kernel (partial: see above)
 *                            host_vm.inc:host_vm_protect, host_win_adopt                                      test:protect_args test:protect_commit
 *                            test:protect_mapfixed test:protect_window test:protect_not_owned test:protect_partial_failure
 *  9 DISPLAY_DOCKED     n=0  1 docked | 0 handheld
 *                            host_platform.inc:pf_display_docked                                              test:display_docked
 * 10 ACCESS_FAULT_HANDLER n=2  0 | 0xCC01 (stack_top not 16-aligned, < sizeof(FnvnxFaultFrame) or >= 4 GiB; handler == 0 unregisters and skips the check)
 *                            host_fault.inc:host_fault_register, fnvnx_fault_deliver (decode + delivery rule), fnvnx_fault_entry (asm) test:fault_register
 *                            test:fault_deliver test:fault_resume test:fault_masked test:fault_die test:fault_logic
 * 11 SECTION_CREATE     n=1  id | -(0xCA01 | 0xCE01 | 0xD001 | kernel | 0x1359 no own-process handle)
 * 12 SECTION_MAP        n=4  address | -(0xCA01 | 0xCC01 | 0xE401 | 0xD401 | 0xCE01 | 0xDA01 | 0xD001 | kernel), the order of checks is in the text above
 * 13 SECTION_UNMAP      n=2  0 | -(0xD401 not exactly a view | kernel)
 * 14 SECTION_FREE       n=1  0 | -(0xE401 unknown id | kernel)
 *                            host_section.inc:host_section_create,_map,_unmap,_free                           test:host_section_test test:via_dispatch_section
 * 15 UNMAP_FIXED        n=3  0 | 0xCC01 | 0xCA01 | 0xD401 (wraps, not exactly one live map_fixed) | kernel
 *                            host_vm.inc:h_unmap_fixed                                                        test:unmapfixed_args test:via_dispatch_unmap_fixed
 * 16..26 30 31 SOCK_SOCKET SOCK_CLOSE SOCK_BIND SOCK_LISTEN SOCK_ACCEPT SOCK_CONNECT SOCK_SENDTO SOCK_RECVFROM SOCK_SETSOCKOPT SOCK_GETSOCKOPT
 *         SOCK_POLL SOCK_GETFL SOCK_SETFL   n = the count each service states (SOCK_SOCKET 3, CLOSE 1, BIND 2, LISTEN 2, ACCEPT 2, CONNECT 2, SENDTO 5, RECVFROM 5,
 *                            SETSOCKOPT 5, GETSOCKOPT 5, POLL 3, GETFL 1, SETFL 2)
 *                            value >= 0 | -(wire errno, sockwire.h). ORDER (host_platform.inc:platform_ext): a fn in 16..31 goes to host_sock_call BEFORE
 *                            the generic nargs / null-array check, so for sockets "n" is the EXACT count: nargs != n (more or fewer, null array or not)
 *                            -> -FNVNX_WE_INVAL; nargs == n with a null array -> FNVNX_EXT_BAD_NARGS; no socket service reads a null array
 *                            host_sock.inc:host_sock_call (the platform lead's, verbatim)                      test:via_dispatch_sock test:total_declared
 * 34 TZ_AT              n=2  0 | -14 (out null) | -75 (instant outside the time service's range)
 *                            host_platform.inc:pf_tz_at, host_v2.inc:host_tz_at (the platform lead's service)  test:tz_at
 * 35 THREAD_SELF        n=0  thread id | 0 (no memory, id table full)
 *                            host_platform.inc:pf_thread_self                                                 test:thread_self
 * 36 TEXT_ENTRY         n=5  1 text entered | 0 cancelled | -FNVNX_HE_BADINPUT (max_len 0 or > 500, out or n null) | -(the applet's Horizon result)
 *                            host_textentry.inc:host_text_entry                                               test:text_entry
 * VM services and the sections through the dispatch (argument mapping): test:via_dispatch_vm.
 *
 * FnvnxHostApi slots: the results are stated at each slot above. Host function (file) and tests; "-" = a direct libnx / newlib call
 * whose answer is that call's (Result or -errno) with no host logic of its own:
 *   output_debug_string h_output_debug_string (host.c) -; sleep_ns h_sleep_ns (host.c) -; exit h_exit (host.c) -; console_write,
 *   console_flush, main_loop, buttons_down, system_tick, system_tick_freq (host.c) -;
 *   thread_create, thread_start, thread_join, thread_free, thread_set_priority, thread_set_cpu (host_v2.inc, ids host_ids.inc)  test:thread_slots test:thread_ids;
 *   thread_exit, thread_yield (host_v2.inc) -;
 *   wait_on_address, wake_address (host_v2.inc)  test:wait_wake;
 *   wall_time_us, random64, memory_info (host_v2.inc) -; tz_offset_minutes (host_v2.inc)  test:tz_at; cpu_count (host_v2.inc)  test:thread_slots;
 *   fs_open, fs_read, fs_write, fs_pread, fs_pwrite, fs_seek, fs_truncate, fs_sync, fs_fstat, fs_close, fs_stat, fs_mkdir, fs_rmdir, fs_remove,
 *   fs_rename (host_v2.inc)  test:fs_errors; fs_dir_open, fs_dir_next, fs_dir_close (host_v2.inc)  test:dir_slots;
 *   audio_open, audio_submit, audio_close, pad_read, pad_vibrate, input_poll (host_v3.inc)  test:test_audio test:test_pads test:test_events;
 *   map_fixed (host_vm.inc:h_map_fixed)  test:mapfixed_args test:protect_mapfixed test:protect_window;  ext_call (host_ext.inc)  test:total_ext.
 * TOTALITY TABLE END */
/* ---- BSD sockets (FNVNX_PLAT_SOCK_*, services 16..26, 30, 31; guest: platform/os/sock_guest.c, host:
 * toolchain/host/host_sock.inc; the wire constants and structs: fnvnx/sockwire.h). The rule: EVERY BSD-socket
 * operation of the guest is one of these services; the guest's C library has no network device of its own.
 *  - Arguments are the uint64 words of ext_call's args array; nargs must be exactly the count shown, else the
 *    result is -FNVNX_WE_INVAL (and with the exact count but a null args array, FNVNX_EXT_BAD_NARGS). A pointer argument is a guest address zero-extended to 64 bits (one shared address
 *    space, as for fs_read); 0 is the null pointer where "or 0" is said.
 *  - Result: an int64 in a uint64, value >= 0, or -errno with errno a WIRE number (sockwire.h FNVNX_WIRE_ERRNO_LIST,
 *    the Linux numbers). The guest translates wire errno into newlib's and the host translates its own into the wire
 *    ones, both by name, so nothing assumes that two systems' errno numbers agree (a host errno that is not in the list is -EIO).
 *    Where a service below says "the platform's answer", the host passes the call to the console's bsd service (libnx) with the
 *    constants translated by name and returns its outcome: 0 / a count / a descriptor, or -(that errno mapped through the list),
 *    i.e. any value of FNVNX_WIRE_ERRNO_LIST the bsd service can produce; the host adds only the answers the service names. FNVNX_EXT_NO_MODULE is what a host
 *    without these services returns for every one of them.
 *  - A socket handle h is the descriptor number that the bsd service's socket() / accept() returned, unchanged: the host has no numbering
 *    of its own and makes no claim about which number is next (no "lowest free"); a number can be returned again by the bsd service
 *    after its close. The host keeps the set of descriptors it created; every service with a handle argument answers -EBADF for a
 *    number outside the set (checked before anything else of that service, after the count check), so the guest cannot name another
 *    descriptor of the host. The guest keeps it inside its own descriptor table (a newlib
 *    device "soc"), so close/read/write/dup/fcntl on a socket descriptor reach these services.
 *  - Blocking: a blocking call (connect, accept, sendto, recvfrom, poll with a timeout) blocks only the calling guest thread: ext_call runs on the caller's own thread, as audio_submit and
 *    the file services do. A new socket is blocking until FNVNX_PLAT_SOCK_SETFL says otherwise. There are no signals:
 *    EINTR does not occur and sends never raise SIGPIPE (the host passes MSG_NOSIGNAL where the OS has it). */
#define FNVNX_PLAT_SOCK_SOCKET 16u      /* (domain WAF_*, type WSOCK_*, protocol IANA) -> h; domain not in FNVNX_WIRE_AF_LIST (or not an int32)
                                           -> -EAFNOSUPPORT, checked first; type not in FNVNX_WIRE_SOCKTYPE_LIST (no SOCK_NONBLOCK/CLOEXEC bits; or not an int32) ->
                                           -EINVAL; else the platform's socket(): WAF_UNSPEC and WSOCK_RAW are in the lists and are passed on, their
                                           outcome (a handle or any wire errno the bsd service gives, e.g. -EPROTONOSUPPORT, -EACCES) is the platform's;
                                           registry out of memory -> -ENOMEM (the new descriptor is closed) */
#define FNVNX_PLAT_SOCK_CLOSE 17u       /* (h) -> 0; the handle is gone whatever the result; a handle that is not open -> -EBADF */
#define FNVNX_PLAT_SOCK_BIND 18u        /* (h, const FnvnxWireSockaddr *) -> 0; null address -> -EFAULT; family not in
                                           WAF_UNSPEC/INET/INET6 -> -EAFNOSUPPORT; else the platform's bind(): the host checks no port or address,
                                           so port 1..1023, 0 or any other value is the bsd service's answer (0 or a wire errno) */
#define FNVNX_PLAT_SOCK_LISTEN 19u      /* (h, backlog) -> 0 */
#define FNVNX_PLAT_SOCK_ACCEPT 20u      /* (h, FnvnxWireSockaddr *peer or 0) -> new h (blocking mode); blocks until a
                                           connection arrives, -EAGAIN when non-blocking and none is pending */
#define FNVNX_PLAT_SOCK_CONNECT 21u     /* (h, const FnvnxWireSockaddr *) -> 0; null address -> -EFAULT; family not in WAF_UNSPEC/INET/INET6 ->
                                           -EAFNOSUPPORT; else the platform's connect(), also for a local / loopback address (no host check): 0 or a wire
                                           errno of the bsd service; a non-blocking socket: -FNVNX_WE_INPROGRESS as the bsd service gives it; an
                                           interrupted connect (EINTR) is -FNVNX_WE_INPROGRESS too */
#define FNVNX_PLAT_SOCK_SENDTO 22u      /* (h, buf, len, FNVNX_WIRE_MSG_LIST flags, const FnvnxWireSockaddr *to or 0)
                                           -> bytes sent; len > 0x7FFFFFFF is -FNVNX_WE_INVAL */
#define FNVNX_PLAT_SOCK_RECVFROM 23u    /* (h, buf, len, flags, FnvnxWireSockaddr *from or 0) -> bytes received (0 =
                                           orderly shutdown of a stream); *from is written on every success (family
                                           WAF_UNSPEC when the socket has no source address) */
#define FNVNX_PLAT_SOCK_SETSOCKOPT 24u  /* (h, level, option, const void *val, vallen) -> 0; level FNVNX_WLVL_SOCKET or
                                           an IANA protocol number; option and value shape: the FNVNX_WIRE_*_OPT_LISTs */
#define FNVNX_PLAT_SOCK_GETSOCKOPT 25u  /* (h, level, option, void *val, uint32_t *vallen; in: capacity, out: size of
                                           the option's value) -> 0. Checks in this order: handle (-EBADF); level or option not in the
                                           FNVNX_WIRE_*_OPT_LISTs (or above UINT32_MAX) -> -ENOPROTOOPT; null val -> -EFAULT (whatever the
                                           capacity, so capacity 0 with a null val is -EFAULT too); null vallen -> -EFAULT; then the platform's
                                           getsockopt() (its wire errno on failure, nothing written). On success the value's wire size
                                           (INT, TYPE, ERRNO: 4; LINGER: 8; TIMEVAL: 16) is stored in *vallen ALWAYS, and min(capacity, size)
                                           bytes of the value are written to val: a capacity smaller than the size gives a truncated value, *vallen = the
                                           full size (so *vallen > the capacity tells the truncation), result 0; a capacity 0 with a non-null val writes
                                           nothing and gives *vallen = the size, result 0 */
#define FNVNX_PLAT_SOCK_POLL 26u        /* (FnvnxWirePollfd *fds, nfds, timeout_ms int32, -1 = forever) -> number of
                                           entries with revents != 0. Checks: nfds > 0x1000000 -> -EINVAL; nfds > 0 with a null array ->
                                           -EFAULT; nfds > 32 and no memory -> -ENOMEM. nfds 0: no array access, waits the timeout, returns 0.
                                           An element with a negative fd is ignored (revents 0); an element whose fd is not an open
                                           host-created socket gets revents FNVNX_WPOLLNVAL and counts, and when there is any such element the call does not wait
                                           (timeout 0: it reports what is ready now). Event bits outside FNVNX_WIRE_POLL_LIST are dropped
                                           (not an error). Otherwise the platform's poll(): open sockets with no traffic give revents 0 for them
                                           and, after the timeout (forever for -1), the result 0; a failure of poll() is -(its wire errno) */
#define FNVNX_PLAT_SOCK_GETFL 30u       /* (h) -> FNVNX_WO_RDWR | (non-blocking ? FNVNX_WO_NONBLOCK : 0); of the flags only NONBLOCK is returned */
#define FNVNX_PLAT_SOCK_SETFL 31u       /* (h, flags) -> 0; only FNVNX_WO_NONBLOCK of flags is applied (POSIX F_SETFL); any other bit of
                                           flags is IGNORED, not an error (no -EINVAL); the result is 0, or the wire errno of the platform's fcntl() */
/* Declared machine (CLAUDE.md, owner decision 2026-11-11: no network connection). Under the L4 emulator the stage host is that
 * machine: connect/sendto to a non-local address answer -ENETUNREACH (WSAENETUNREACH 10051). On the console the answer is whatever
 * the bsd service gives. Name resolution, shutdown and getsockname/getpeername are not services: the platform never calls them
 * (rule "used = declared", gate hostapi); the numbers 27, 28, 29, 32 and 33 stay unassigned and answer FNVNX_EXT_NO_SERVICE. */
/* TZ_AT (utc_seconds, FnvnxTzAt *) -> 0 | -errno (wire number, int64 as u64). The zone of the console's time service
 * (timeToCalendarTimeWithMyRule) at one instant; platform/os/os_tzrule.inc derives the zone's rule (os_tz_rule) from it.
 * Out null -> -EFAULT; utc_seconds outside the host time service's range -> -EOVERFLOW (the host answers it for any refusal of the
 * time service: range is its only input-dependent one); else 0 and *out is written. abbr = the first 7 bytes of the zone's abbreviation
 * and a NUL (an empty abbreviation is the empty string). */
#define FNVNX_PLAT_TZ_AT 34u
/* THREAD_SELF () -> a host thread id (!= 0) for the CALLING thread, which the host did not create (the main thread), or 0 (no memory or
 * the id table full). The id is only for FNVNX_PLAT_THREAD_ACTIVITY (the world registry, platform/os/os_world.c; also thread_set_priority
 * and thread_set_cpu). Call once per such thread. thread_start of it -> FNVNX_HE_BADINPUT (it is started), thread_join -> FNVNX_HE_BADINPUT;
 * thread_free ends the id and the record, never the thread. */
#define FNVNX_PLAT_THREAD_SELF 35u
/* TEXT_ENTRY (max_len, init, init_n, out, n) -> int64: the system software keyboard (libnx swkbd), modal; blocks only the calling guest thread
 * (any thread may call; one keyboard exists, so concurrent callers are served one after the other).
 *   max_len  the most UTF-16 code units of the text, 1..FNVNX_TEXT_ENTRY_MAX (500). 0 or above 500 -> -FNVNX_HE_BADINPUT, nothing shown.
 *   init     const uint16_t * (guest address), init_n units: the text the keyboard starts with (cursor at its end). init 0: no text (init_n is
 *            ignored). Only the first max_len units are used (a cut through a surrogate pair drops the pair). A lone surrogate is shown as U+FFFD.
 *   out      uint16_t *, room for max_len + 1 units; n  uint32_t *. Either null -> -FNVNX_HE_BADINPUT, nothing shown.
 * Results: 1  text entered: out[0..*n) are the UTF-16 units of the text, out[*n] = 0, *n <= max_len (the text may contain any unit the
 *             applet returns, newline included only if the applet gives it; an empty text is *n = 0). The applet returns UTF-8, converted exactly
 *             by the UTF-8 definition (a byte outside a well-formed sequence is U+FFFD, one per byte; characters above U+FFFF are two units).
 *             swkbd counts characters, the contract counts units: a text of more than max_len units (only possible with characters above
 *             U+FFFF) is not returned, the keyboard opens again with that text, until the text fits or the player cancels.
 *          0  the player cancelled (libnx: swkbdShow's LibnxError_LibAppletBadExit, i.e. the applet's exit reason is not Normal); out and *n untouched.
 *          -r any other failure: r is the Horizon result code (libnx swkbdCreate / swkbdShow) as a positive number, the result is -r as int64;
 *             out and *n untouched. -FNVNX_HE_BADINPUT is the one the host produces itself (the table above).
 * The guest-side check of a name dialog (accept / reject at index) is the guest's loop: call, run the check on out[0..*n), on rejection call again
 * with init = that text. Host-side tests: the swkbd calls are stubbed in tests/s4/backends/host_total_test.c.
 * Under the L4 emulator (no human): the scenario supplies the text. With a scenario text the emulator answers 1 and
 * that text (a text longer than max_len is a scenario error and stops the run); without one it answers 0
 * (cancelled, out and *n untouched). Argument checks are the same as on the console. */
#define FNVNX_PLAT_TEXT_ENTRY 36u
#define FNVNX_TEXT_ENTRY_MAX 500u
typedef struct FnvnxTzAt {
    int32_t off_s;      /* local = UTC + off_s */
    uint32_t dst;       /* 1: daylight time at that instant */
    char abbr[8];       /* the zone's abbreviation then, NUL-terminated */
} FnvnxTzAt;
#define FNVNX_PLAT_SOCK_FIRST 16u
#define FNVNX_PLAT_SOCK_LAST 31u

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
