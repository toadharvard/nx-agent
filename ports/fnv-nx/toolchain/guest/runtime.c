/* fnv-nx ILP32 guest runtime: self-relocation, TLS, newlib glue, entry from the LP64 host.
 * Compiled with -mabi=ilp32. See docs/TOOLCHAIN.md.
 *
 * Startup order (called on the host's main thread, see crt0.s):
 *   1. clear .bss (the host zero-fills it too)
 *   2. relocatable build (header +24 bit 0, guest_pie.specs): apply our own R_AARCH64_P32_RELATIVE relocations
 *      (RELA and RELR) for the slide between the link base (+16) and where the host mapped us; no absolute
 *      pointer may be read before this point. A fixed build (guest.specs) has nothing to relocate.
 *   3. validate the host API table
 *   4. main-thread TLS: copy .tdata, TPIDR_EL0 = TCB
 *   5. newlib: heap, stdout/stderr -> host console, exit/sleep/clock hooks
 *   6. __libc_init_array, main(), return its result to the host
 */
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
#include <time.h>
#include <sys/iosupport.h>

#include "fnvnx/hostapi.h"

#define HIDDEN __attribute__((visibility("hidden")))
#define EARLY  __attribute__((optimize("no-tree-loop-distribute-patterns"), noinline))

typedef struct { int32_t d_tag; uint32_t d_val; } Elf32_Dyn;
typedef struct { uint32_t r_offset; uint32_t r_info; int32_t r_addend; } Elf32_Rela;

enum {
    DT_NULL = 0, DT_RELA = 7, DT_RELASZ = 8, DT_RELAENT = 9, DT_RELRSZ = 35, DT_RELR = 36, DT_RELRENT = 37,
    R_AARCH64_P32_RELATIVE = 183,
};

/* Guest entry errors (returned to the host instead of a main() result). */
#define FNVNX_GUEST_ERR_RELOC   0xF0000001u
#define FNVNX_GUEST_ERR_API     0xF0000002u

extern char __fnvnx_guest_start[] HIDDEN;
extern char __bss_start__[] HIDDEN, __bss_end__[] HIDDEN;
extern char __tdata_lma[] HIDDEN, __tdata_lma_end[] HIDDEN;
extern char __tls_start[] HIDDEN, __tls_tcb[] HIDDEN;
extern char __end__[] HIDDEN;

const FnvnxHostApi *fnvnx_host;
extern char *fake_heap_start, *fake_heap_end;

int main(int argc, char **argv);
void fnvnx_guest_fs_init(void);
void __libc_init_array(void);

static EARLY void early_zero(char *p, char *end) {
    while (p < end) *(volatile char *)p++ = 0;
}

/* Relocatable guest (ET_DYN linked at link_base, header +16): R_AARCH64_P32_RELATIVE in RELA and RELR form.
 * Every entry is an image address: the link stage (link/ld.py, link/gate_abs.py) admits no absolute symbol, so no entry holds an absolute
 * value, and the slide is added to every entry, whatever its link-time value (a pointer one past the end of the image, an addend
 * below the link base, a value outside [link_base, link_base + size)) modulo 2^32. */
static EARLY int self_relocate(uintptr_t base, uint32_t link_base, const Elf32_Dyn *dyn) {
    uint32_t slide = (uint32_t)base - link_base;
    uintptr_t rela = 0, relr = 0;
    uint32_t relasz = 0, relaent = sizeof(Elf32_Rela), relrsz = 0;
    for (; dyn->d_tag != DT_NULL; dyn++) {
        switch (dyn->d_tag) {
            case DT_RELA:    rela = dyn->d_val + slide; break;
            case DT_RELASZ:  relasz = dyn->d_val; break;
            case DT_RELAENT: relaent = dyn->d_val; break;
            case DT_RELR:    relr = dyn->d_val + slide; break;
            case DT_RELRSZ:  relrsz = dyn->d_val; break;
        }
    }
    if (relaent != sizeof(Elf32_Rela)) return -1;
    for (uint32_t i = 0; i < relasz / sizeof(Elf32_Rela); i++) {
        const Elf32_Rela *r = (const Elf32_Rela *)rela + i;
        switch (r->r_info & 0xFF) {
            case R_AARCH64_P32_RELATIVE:
                if (r->r_info >> 8) return -1; /* symbol-relative: not produced by a static PIE */
                *(uint32_t *)(uintptr_t)(r->r_offset + slide) = (uint32_t)r->r_addend + slide;
                break;
            default: return -1;
        }
    }
    /* RELR (-z pack-relative-relocs), 32-bit entries: even = address, odd = bitmap of the next 31 words. */
    uint32_t *where = 0;
    for (uint32_t i = 0; i < relrsz / 4; i++) {
        uint32_t e = ((const uint32_t *)relr)[i];
        if (!(e & 1)) {
            where = (uint32_t *)(uintptr_t)(e + slide);
            *where += slide;
            where++;
        } else {
            for (uint32_t bits = e >> 1, k = 0; bits; bits >>= 1, k++)
                if (bits & 1) where[k] += slide;
            where += 31;
        }
    }
    return 0;
}

/* ---- newlib glue ----------------------------------------------------------------------------------- */

static ssize_t con_write(struct _reent *r, void *fd, const char *ptr, size_t len) {
    (void)r; (void)fd;
    return (ssize_t)fnvnx_hcall2(fnvnx_host->console_write, FNVNX_ADDR(ptr), len);
}

static const devoptab_t dotab_hostcon = {
    .name = "con", .structSize = 0, .write_r = con_write, .dirStateSize = 0,
};

void __syscall_exit(int rc) {
    fnvnx_hcall1(fnvnx_host->exit, (uint64_t)(int64_t)rc);
    for (;;) {}
}

int __syscall_nanosleep(const struct timespec *req, struct timespec *rem) {
    if (!req || req->tv_nsec < 0 || req->tv_nsec >= 1000000000) { errno = EINVAL; return -1; }
    fnvnx_hcall1(fnvnx_host->sleep_ns, (uint64_t)req->tv_sec * 1000000000ull + (uint64_t)req->tv_nsec);
    if (rem) { rem->tv_sec = 0; rem->tv_nsec = 0; }
    return 0;
}

int __syscall_clock_gettime(clockid_t clock_id, struct timespec *tp) {
    if (!tp) { errno = EINVAL; return -1; }
    if (clock_id == CLOCK_REALTIME) {
        int64_t us = (int64_t)fnvnx_hcall0(fnvnx_host->wall_time_us);
        tp->tv_sec = (time_t)(us / 1000000);
        tp->tv_nsec = (long)(us % 1000000) * 1000;
        return 0;
    }
    if (clock_id != CLOCK_MONOTONIC) { errno = EINVAL; return -1; }
    uint64_t t = fnvnx_hcall0(fnvnx_host->system_tick), f = fnvnx_hcall0(fnvnx_host->system_tick_freq);
    tp->tv_sec = (time_t)(t / f);
    tp->tv_nsec = (long)((t % f) * 1000000000ull / f);
    return 0;
}

/* newlib's register_fini (an init_array entry of __call_atexit.o) registers __libc_fini_array with atexit when the address of
 * __libc_fini is not null: the program's .fini_array (the destructors of its static objects) then runs at exit().  The guest
 * wants exactly that, so the marker is defined. */
const char __libc_fini = 1;

/* The C++ ABI's pure virtual call handler (libsupc++ pure.cc: write the message, std::terminate).  GCC references it weakly from
 * every object with a pure virtual slot, and a weak reference pulls no archive member, so it is defined here: the message goes to
 * the host console and the process ends through the host's exit. */
void __cxa_pure_virtual(void) {
    static const char msg[] = "pure virtual method called\n";
    fnvnx_hcall2(fnvnx_host->console_write, FNVNX_ADDR(msg), sizeof msg - 1);
    fnvnx_hcall1(fnvnx_host->exit, 0xFB);
    for (;;) {}   /* the host's exit does not return */
}

/* clock_getres: the resolution of a clock is the step of the host counter it is read from.  CLOCK_REALTIME comes from wall_time_us
 * (microseconds: 1000 ns).  CLOCK_MONOTONIC comes from system_tick at system_tick_freq Hz: one tick lasts 1e9 / f ns, reported
 * rounded up to whole nanoseconds, ceil(1e9 / f) = (1e9 + f - 1) / f, exact in 64-bit integers for every f in [1, 2^63 - 1e9]. */
int __syscall_clock_getres(clockid_t clock_id, struct timespec *res) {
    if (clock_id != CLOCK_REALTIME && clock_id != CLOCK_MONOTONIC) { errno = EINVAL; return -1; }
    if (res) {
        res->tv_sec = 0;
        if (clock_id == CLOCK_REALTIME) res->tv_nsec = 1000;
        else {
            uint64_t f = fnvnx_hcall0(fnvnx_host->system_tick_freq);
            res->tv_nsec = (long)((1000000000ull + f - 1) / f);
        }
    }
    return 0;
}

/* clock_settime: the guest is an unprivileged process of the console (POSIX: EPERM, "the requesting process does not have the appropriate
 * privilege to set the specified clock"); CLOCK_MONOTONIC cannot be set by any process (EINVAL), the same answer as for an unknown clock. */
int __syscall_clock_settime(clockid_t clock_id, const struct timespec *tp) {
    if (!tp || tp->tv_nsec < 0 || tp->tv_nsec >= 1000000000 || (clock_id != CLOCK_REALTIME)) { errno = EINVAL; return -1; }
    errno = EPERM;
    return -1;
}

static int api_ok(const FnvnxHostApi *a, uintptr_t base) {
    if (a->magic != FNVNX_HOSTAPI_MAGIC || a->version != FNVNX_HOSTAPI_VERSION || a->size < sizeof(*a)) return 0;
    if (a->guest_base != base) return 0;
    const uint64_t *slot = &a->guest_base, *end = (const uint64_t *)(a + 1);
    for (; slot < end; slot++)
        if (*slot >> 32) return 0; /* a 32-bit address space keeps every address below 4 GiB */
    return 1;
}

/* Called from crt0 with the host API address in x0 (zero-extended by the host). */
uint64_t __fnvnx_guest_entry(uint64_t api) {
    uintptr_t base = (uintptr_t)__fnvnx_guest_start;
    early_zero(__bss_start__, __bss_end__);
    /* crt0 header (filled by elf2nso); .dynamic is found through MOD0's PC-relative offset, not through the
     * _DYNAMIC symbol, whose GOT entry would itself need relocating first. */
    const uint32_t *hdr = (const uint32_t *)base;
    if (hdr[6] & 1) {
        const char *mod0 = (const char *)base + hdr[1];
        const Elf32_Dyn *dyn = (const Elf32_Dyn *)(mod0 + ((const int32_t *)mod0)[1]);
        if (self_relocate(base, hdr[4], dyn) != 0) return FNVNX_GUEST_ERR_RELOC;
    }

    if (api >> 32 || !api_ok((const FnvnxHostApi *)(uintptr_t)api, base)) return FNVNX_GUEST_ERR_API;
    fnvnx_host = (const FnvnxHostApi *)(uintptr_t)api;

    memcpy(__tls_start, __tdata_lma, (size_t)(__tdata_lma_end - __tdata_lma));
    uint64_t saved_tp;
    __asm__ volatile("mrs %0, tpidr_el0" : "=r"(saved_tp));
    __asm__ volatile("msr tpidr_el0, %0" :: "r"((uint64_t)(uintptr_t)__tls_tcb) : "memory");

    if (fnvnx_host->heap_size) {
        fake_heap_start = (char *)(uintptr_t)fnvnx_host->heap_base;
        fake_heap_end = fake_heap_start + (uintptr_t)fnvnx_host->heap_size;
    } else {
        fake_heap_start = fake_heap_end = __end__; /* no heap: malloc fails cleanly */
    }
    devoptab_list[STD_OUT] = &dotab_hostcon;
    devoptab_list[STD_ERR] = &dotab_hostcon;
    setvbuf(stdout, NULL, _IONBF, 0);
    fnvnx_guest_fs_init();   /* fs.c */

    __libc_init_array();
    static char arg0[] = "fnvnx-guest";
    static char *argv[] = { arg0, NULL };
    int rc = main(1, argv);
    fflush(stdout);

    __asm__ volatile("msr tpidr_el0, %0" :: "r"(saved_tp) : "memory");
    return (uint32_t)rc;
}
