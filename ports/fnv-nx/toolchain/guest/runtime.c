/* fnv-nx ILP32 guest runtime: self-relocation, TLS, newlib glue, entry from the LP64 host.
 * Compiled with -mabi=ilp32. See docs/TOOLCHAIN.md.
 *
 * Startup order (called on the host's main thread, see crt0.s):
 *   1. clear .bss (the host zero-fills it too)
 *   2. only for a PIE build (_DYNAMIC present): apply our own relocations (R_AARCH64_P32_RELATIVE, RELA and
 *      RELR); no absolute pointer may be read before this point. The normal build is a fixed-address
 *      static executable mapped at its link address, with nothing to relocate.
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
    R_AARCH64_NONE = 0, R_AARCH64_P32_RELATIVE = 183,
};

/* Guest entry errors (returned to the host instead of a main() result). */
#define FNVNX_GUEST_ERR_RELOC   0xF0000001u
#define FNVNX_GUEST_ERR_API     0xF0000002u

extern char __fnvnx_guest_start[] HIDDEN;
extern Elf32_Dyn _DYNAMIC[] __attribute__((weak, visibility("hidden")));
extern char __bss_start__[] HIDDEN, __bss_end__[] HIDDEN;
extern char __tdata_lma[] HIDDEN, __tdata_lma_end[] HIDDEN;
extern char __tls_start[] HIDDEN, __tls_tcb[] HIDDEN;
extern char __end__[] HIDDEN;

const FnvnxHostApi *fnvnx_host;
extern char *fake_heap_start, *fake_heap_end;

int main(int argc, char **argv);
void __libc_init_array(void);

static EARLY void early_zero(char *p, char *end) {
    while (p < end) *(volatile char *)p++ = 0;
}

static EARLY int self_relocate(uintptr_t base, const Elf32_Dyn *dyn) {
    uintptr_t rela = 0, relr = 0;
    uint32_t relasz = 0, relaent = sizeof(Elf32_Rela), relrsz = 0;
    for (; dyn->d_tag != DT_NULL; dyn++) {
        switch (dyn->d_tag) {
            case DT_RELA:    rela = base + dyn->d_val; break;
            case DT_RELASZ:  relasz = dyn->d_val; break;
            case DT_RELAENT: relaent = dyn->d_val; break;
            case DT_RELR:    relr = base + dyn->d_val; break;
            case DT_RELRSZ:  relrsz = dyn->d_val; break;
        }
    }
    if (relaent != sizeof(Elf32_Rela)) return -1;
    for (uint32_t i = 0; i < relasz / sizeof(Elf32_Rela); i++) {
        const Elf32_Rela *r = (const Elf32_Rela *)rela + i;
        switch (r->r_info & 0xFF) {
            case R_AARCH64_NONE: break;
            case R_AARCH64_P32_RELATIVE:
                if (r->r_info >> 8) return -1; /* symbol-relative: not produced by a static PIE */
                *(uint32_t *)(base + r->r_offset) = (uint32_t)(base + r->r_addend);
                break;
            default: return -1;
        }
    }
    /* RELR (-z pack-relative-relocs), 32-bit entries: even = address, odd = bitmap of the next 31 words. */
    uint32_t *where = 0;
    for (uint32_t i = 0; i < relrsz / 4; i++) {
        uint32_t e = ((const uint32_t *)relr)[i];
        if (!(e & 1)) {
            where = (uint32_t *)(base + e);
            *where++ += (uint32_t)base;
        } else {
            for (uint32_t bits = e >> 1, k = 0; bits; bits >>= 1, k++)
                if (bits & 1) where[k] += (uint32_t)base;
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
    const Elf32_Dyn *volatile dyn = _DYNAMIC;
    if (dyn && self_relocate(base, dyn) != 0) return FNVNX_GUEST_ERR_RELOC;

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
    extern void fnvnx_guest_fs_init(void);
    fnvnx_guest_fs_init();

    __libc_init_array();
    static char arg0[] = "fnvnx-guest";
    static char *argv[] = { arg0, NULL };
    extern int fnvnx_guest_main(int, char **) __attribute__((weak));
    int rc = fnvnx_guest_main ? fnvnx_guest_main(1, argv) : main(1, argv);
    fflush(stdout);

    __asm__ volatile("msr tpidr_el0, %0" :: "r"(saved_tp) : "memory");
    return (uint32_t)rc;
}
