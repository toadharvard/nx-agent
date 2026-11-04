/* linhost.c - a Linux stand-in for the Switch host module (toolchain/host/host.c): loads an ILP32 guest ELF and runs
 * it with the FnvnxHostApi threads, address wait/wake, time and console slots, on real host threads.
 *
 * Built LP64 for aarch64 Linux, static, freestanding (no libc: raw syscalls), linked low so every host address
 * fits the guest's 32-bit pointers. Run it on an aarch64 machine or under qemu-user, which maps each guest thread
 * onto a host thread, so the guest's threads run in parallel on the host's cores:
 *
 *   qemu-aarch64 -R 0xC0000000 linhost guest.elf
 *
 * No libc means no TLS of our own: TPIDR_EL0 belongs to the guest, as on the Switch (hostapi.h). Threads are
 * clone()d with CLONE_CHILD_CLEARTID for join; wait/wake are futexes; the placement table of toolchain/host/
 * placement.h is applied with sched_setaffinity (Switch cores 0-3 -> host CPUs 0-3). Slots the prototype does not
 * need (files, audio, input) return -ENOSYS. docs/PERF.md section 6.6. */
#include <stdint.h>
#include <stddef.h>

#include "fnvnx/hostapi.h"
#include "placement.h"

typedef uint64_t u64;

/* ---- raw syscalls (aarch64 Linux) */
static long sc(long n, long a, long b, long c, long d, long e, long f) {
    register long x8 __asm__("x8") = n, x0 __asm__("x0") = a, x1 __asm__("x1") = b, x2 __asm__("x2") = c,
                  x3 __asm__("x3") = d, x4 __asm__("x4") = e, x5 __asm__("x5") = f;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2), "r"(x3), "r"(x4), "r"(x5) : "memory");
    return x0;
}
enum { SYS_openat = 56, SYS_close = 57, SYS_read = 63, SYS_write = 64, SYS_pread64 = 67, SYS_exit = 93,
       SYS_exit_group = 94, SYS_futex = 98, SYS_nanosleep = 101, SYS_clock_gettime = 113,
       SYS_sched_setaffinity = 122, SYS_sched_yield = 124, SYS_gettid = 178, SYS_mmap = 222 };
#define FUTEX_WAIT 0
#define FUTEX_WAIT_PRIVATE 128
#define FUTEX_WAKE_PRIVATE 129
#define ETIMEDOUT 110
#define EAGAIN 11
#define ENOSYS 38

static size_t slen(const char *s) { size_t n = 0; while (s[n]) n++; return n; }
static void puts_(int fd, const char *s) { sc(SYS_write, fd, (long)s, (long)slen(s), 0, 0, 0); }
static void put_hex(int fd, u64 v) {
    char b[19] = "0x";
    for (int i = 0; i < 16; i++) b[2 + i] = "0123456789abcdef"[(v >> (60 - 4 * i)) & 15];
    b[18] = 0;
    puts_(fd, b);
}
static void die(const char *m) { puts_(2, "linhost: "); puts_(2, m); puts_(2, "\n"); sc(SYS_exit_group, 127, 0, 0, 0, 0, 0); }
void *memset(void *d, int c, size_t n) { unsigned char *p = d; while (n--) *p++ = (unsigned char)c; return d; }
void *memcpy(void *d, const void *s, size_t n) { unsigned char *p = d; const unsigned char *q = s; while (n--) *p++ = *q++; return d; }

/* ---- time */
struct ts { long s, ns; };
static u64 now_ns(void) { struct ts t; sc(SYS_clock_gettime, 1 /* MONOTONIC */, (long)&t, 0, 0, 0, 0); return (u64)t.s * 1000000000ull + (u64)t.ns; }

/* ---- threads: records and stacks in .bss (low addresses, no allocator) */
#define MAXT 64
#define STACK (1u << 20)
typedef struct HThread {
    u64 entry, arg;
    volatile int tid;          /* -1 from start, CLONE_CHILD_CLEARTID: 0 once the thread has exited */
    int ktid;                  /* the kernel's thread id (clone's result) */
    int used, started, ideal;
    uint32_t mask;
    uint32_t state;            /* HT_* as in toolchain/host/host_v2.inc */
    int zombie;                /* detached and finished: the slot is free once tid reads 0 */
} HThread;
#define HT_FINISHED 2u
#define HT_DETACHED 4u
static HThread g_t[MAXT];
static unsigned char g_stacks[MAXT][STACK] __attribute__((aligned(16)));
static unsigned g_seq;

/* long clone3ish(flags, stack, ptid, tls, ctid, fn, arg): the child calls fn(arg) on the new stack, then exit(0) */
long linhost_clone(unsigned long flags, void *stack, volatile int *ptid, void *tls, volatile int *ctid,
                   void (*fn)(void *), void *arg);
__asm__(".text\n.global linhost_clone\nlinhost_clone:\n"
        "  stp x5, x6, [x1, #-16]!\n"
        "  mov x8, #220\n  svc #0\n"
        "  cbnz x0, 1f\n"
        "  ldp x5, x6, [sp], #16\n  mov x0, x6\n  blr x5\n"
        "  mov x0, #0\n  mov x8, #93\n  svc #0\n"
        "1: ret\n");

static void set_affinity(long tid, uint32_t mask) {
    unsigned long m = mask & 0xF;   /* Switch cores 0-3 -> host CPUs 0-3 */
    sc(SYS_sched_setaffinity, tid, sizeof m, (long)&m, 0, 0, 0);
}

static void thread_main(void *p) {
    HThread *h = p;
    set_affinity(0, h->mask);
    ((u64 (*)(u64))h->entry)(h->arg);
    /* freed before its end (CloseHandle of a running thread, or its own last reference): the slot and its stack
     * are reused only after the kernel cleared tid at exit (h_thread_create) */
    if (__atomic_fetch_or(&h->state, HT_FINISHED, __ATOMIC_ACQ_REL) & HT_DETACHED)
        __atomic_store_n(&h->zombie, 1, __ATOMIC_RELEASE);
}

static u64 h_thread_create(u64 entry, u64 arg, u64 stack, u64 prio, u64 cpu) {
    (void)stack;
    for (int i = 0; i < MAXT; i++)   /* reap: detached threads that have exited */
        if (__atomic_load_n(&g_t[i].zombie, __ATOMIC_ACQUIRE) && !__atomic_load_n(&g_t[i].tid, __ATOMIC_ACQUIRE)) {
            g_t[i].zombie = 0;
            __atomic_store_n(&g_t[i].used, 0, __ATOMIC_RELEASE);
        }
    for (int i = 0; i < MAXT; i++)
        if (!__atomic_exchange_n(&g_t[i].used, 1, __ATOMIC_ACQ_REL)) {
            HThread *h = &g_t[i];
            h->entry = entry; h->arg = arg; h->started = 0; h->tid = 0; h->state = 0; h->zombie = 0;
            int hprio;
            int role = fnvnx_place(cpu, __atomic_fetch_add(&g_seq, 1, __ATOMIC_RELAXED), (int64_t)prio, &h->ideal,
                                   &h->mask, &hprio);
            puts_(2, "linhost: thread "); put_hex(2, (u64)i); puts_(2, " role ");
            puts_(2, role >= 0 ? fnvnx_placement[role].role : "explicit"); puts_(2, " mask "); put_hex(2, h->mask);
            puts_(2, "\n");
            return (u64)(uintptr_t)h;
        }
    return 0;
}
static u64 h_thread_start(u64 id) {
    HThread *h = (HThread *)(uintptr_t)id;
    unsigned long fl = 0x100 | 0x200 | 0x400 | 0x800 | 0x10000 | 0x40000 | 0x200000;
    /* VM FS FILES SIGHAND THREAD SYSVSEM CHILD_CLEARTID. tid is set before the clone, not by PARENT_SETTID, so no
     * late store can follow the kernel's clear of a thread that ended at once */
    h->tid = -1;
    h->started = 1;   /* before it runs: its own thread_free must see it */
    long r = linhost_clone(fl, g_stacks[h - g_t] + STACK, 0, 0, &h->tid, thread_main, h);
    if (r < 0) { h->started = 0; h->tid = 0; return (u64)-r; }
    h->ktid = (int)r;
    return 0;
}
static u64 h_thread_join(u64 id) {
    HThread *h = (HThread *)(uintptr_t)id;
    if (!h->started) return 1;
    /* not FUTEX_WAIT_PRIVATE: the kernel's CLONE_CHILD_CLEARTID wake is a shared-key FUTEX_WAKE (kernel/fork.c
     * mm_release; qemu-user's exit does the same), which never reaches a private-key waiter: a join that slept
     * before the thread ended slept forever (the mtqemu hang of J-0125) */
    for (int t; (t = h->tid) != 0;)
        sc(SYS_futex, (long)&h->tid, FUTEX_WAIT, t, 0, 0, 0);
    return 0;
}
static u64 h_thread_free(u64 id) {   /* after join, or a detach: also of a running thread, also by itself */
    HThread *h = (HThread *)(uintptr_t)id;
    uint32_t old = __atomic_fetch_or(&h->state, HT_DETACHED, __ATOMIC_ACQ_REL);
    if (h->started && !(old & HT_FINISHED))
        return 0;   /* still running (a join here could be a self-join): thread_main marks it a zombie */
    if (h->started) h_thread_join(id);   /* finished: wait for the kernel's exit before the stack is reused */
    __atomic_store_n(&h->used, 0, __ATOMIC_RELEASE);
    return 0;
}
static u64 h_thread_exit(void) { sc(SYS_exit, 0, 0, 0, 0, 0, 0); return 0; }
static u64 h_thread_yield(void) { sc(SYS_sched_yield, 0, 0, 0, 0, 0, 0); return 0; }
static u64 h_thread_set_priority(u64 id, u64 prio) { (void)id; (void)prio; return 0; }   /* CFS: advisory */
static u64 h_thread_set_cpu(u64 id, u64 spec) {
    HThread *h = (HThread *)(uintptr_t)id;
    int ideal = h ? h->ideal : 0;
    uint32_t mask = h ? h->mask : 0xF;
    fnvnx_cpu_spec(spec, ideal, mask, &ideal, &mask);
    if (h) { h->ideal = ideal; h->mask = mask; }
    if (!h || h->started) set_affinity(h ? h->ktid : 0, mask);
    return 0;
}

/* ---- address wait/wake */
static u64 h_wait_on_address(u64 addr, u64 expected, u64 timeout_ns) {
    struct ts t = { (long)(timeout_ns / 1000000000ull), (long)(timeout_ns % 1000000000ull) };
    long r = sc(SYS_futex, (long)addr, FUTEX_WAIT_PRIVATE, (long)(uint32_t)expected,
                timeout_ns == UINT64_MAX ? 0 : (long)&t, 0, 0);
    return r == -ETIMEDOUT ? 1 : r == -EAGAIN ? 2 : 0;
}
static u64 h_wake_address(u64 addr, u64 count) {
    sc(SYS_futex, (long)addr, FUTEX_WAKE_PRIVATE, count > 0x7fffffff ? 0x7fffffff : (long)count, 0, 0, 0);
    return 0;
}

/* ---- the rest */
static u64 h_output_debug_string(u64 s, u64 n) { sc(SYS_write, 2, (long)s, (long)n, 0, 0, 0); return 0; }
static u64 h_console_write(u64 s, u64 n) { return (u64)sc(SYS_write, 1, (long)s, (long)n, 0, 0, 0); }
static u64 h_exit(u64 code) { sc(SYS_exit_group, (long)code, 0, 0, 0, 0, 0); return 0; }
static u64 h_sleep_ns(u64 ns) { struct ts t = { (long)(ns / 1000000000ull), (long)(ns % 1000000000ull) }; sc(SYS_nanosleep, (long)&t, 0, 0, 0, 0, 0); return 0; }
static u64 h_zero(void) { return 0; }
static u64 h_tick(void) { return now_ns(); }
static u64 h_tick_freq(void) { return 1000000000ull; }
static u64 h_cpu_count(void) { return 3; }
static u64 h_random(void) { static u64 x = 0x9E3779B97F4A7C15ull; u64 v = __atomic_load_n(&x, __ATOMIC_RELAXED); v ^= v << 13; v ^= v >> 7; v ^= v << 17; __atomic_store_n(&x, v, __ATOMIC_RELAXED); return v; }
static u64 h_meminfo(u64 p) { FnvnxMemInfo *m = (FnvnxMemInfo *)(uintptr_t)p; m->total_phys = 4ull << 30; m->avail_phys = 3ull << 30; return 0; }
static u64 h_enosys(void) { return (u64)-ENOSYS; }

static FnvnxHostApi g_api;

/* ---- ELF32 loader */
typedef struct { unsigned char id[16]; uint16_t type, machine; uint32_t version, entry, phoff, shoff, flags;
                 uint16_t ehsize, phentsize, phnum, shentsize, shnum, shstrndx; } E32;
typedef struct { uint32_t type, offset, vaddr, paddr, filesz, memsz, flags, align; } P32;

static u64 load(const char *path) {
    long fd = sc(SYS_openat, -100, (long)path, 0, 0, 0, 0);
    if (fd < 0) die("cannot open the guest");
    E32 e;
    if (sc(SYS_pread64, fd, (long)&e, sizeof e, 0, 0, 0) != sizeof e || e.id[4] != 1 || e.machine != 183)
        die("not an ELF32 AArch64 guest");
    for (unsigned i = 0; i < e.phnum; i++) {
        P32 p;
        sc(SYS_pread64, fd, (long)&p, sizeof p, e.phoff + i * e.phentsize, 0, 0);
        if (p.type != 1 || !p.memsz) continue;
        u64 lo = p.vaddr & ~0xFFFull, hi = ((u64)p.vaddr + p.memsz + 0xFFF) & ~0xFFFull;
        long m = sc(SYS_mmap, (long)lo, (long)(hi - lo), 7, 0x32 /* PRIVATE|ANON|FIXED: the range is ours (linked below it) */, -1, 0);
        if (m != (long)lo) die("cannot map a guest segment at its address");
        if (sc(SYS_pread64, fd, (long)p.vaddr, p.filesz, p.offset, 0, 0) != (long)p.filesz) die("short read");
    }
    sc(SYS_close, fd, 0, 0, 0, 0, 0);
    return e.entry;
}

__attribute__((used)) long hmain(long *sp);
__attribute__((used)) long hmain(long *sp) {
    long argc = sp[0];
    char **argv = (char **)(sp + 1);
    if (argc < 2) die("usage: linhost guest.elf");
    if ((uintptr_t)&g_api >> 32 || (uintptr_t)hmain >> 32) die("host is not below 4 GiB");
    g_api.magic = FNVNX_HOSTAPI_MAGIC; g_api.version = FNVNX_HOSTAPI_VERSION; g_api.size = sizeof g_api;
#define F(slot, fn) g_api.slot = (u64)(uintptr_t)(fn)
    F(output_debug_string, h_output_debug_string); F(sleep_ns, h_sleep_ns); F(exit, h_exit);
    F(console_write, h_console_write); F(console_flush, h_zero); F(main_loop, h_zero); F(buttons_down, h_zero);
    F(system_tick, h_tick); F(system_tick_freq, h_tick_freq);
    F(thread_create, h_thread_create); F(thread_start, h_thread_start); F(thread_join, h_thread_join);
    F(thread_free, h_thread_free); F(thread_exit, h_thread_exit); F(thread_yield, h_thread_yield);
    F(thread_set_priority, h_thread_set_priority); F(thread_set_cpu, h_thread_set_cpu);
    F(wait_on_address, h_wait_on_address); F(wake_address, h_wake_address);
    F(wall_time_us, h_zero); F(tz_offset_minutes, h_zero); F(random64, h_random); F(memory_info, h_meminfo);
    F(cpu_count, h_cpu_count);
    u64 *slot = &g_api.fs_open;
    for (; slot <= &g_api.ext_call; slot++) *slot = (u64)(uintptr_t)h_enosys;
    u64 entry = load(argv[1]);
    set_affinity(0, 0x1);   /* the main thread: placement row "main", core 0 */
    u64 r = ((u64 (*)(u64))(uintptr_t)entry)((u64)(uintptr_t)&g_api);
    sc(SYS_exit_group, (long)r, 0, 0, 0, 0, 0);
    return 0;
}

__asm__(".text\n.global _start\n_start:\n  mov x0, sp\n  bl hmain\n  b .\n");
