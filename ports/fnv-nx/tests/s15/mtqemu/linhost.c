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
 * placement.h is applied with sched_setaffinity (Switch cores 0-3 -> host CPUs 0-3). docs/PERF.md section 6.6.
 *
 * The game guest (fnvnx.elf of the link, tests/s15/mtqemu/boot_game.sh) runs too:
 *
 *   qemu-aarch64 -R 0xC0000000 linhost [--root DIR] [--expect TEXT] [--skip-faulting-init] [--heap MiB] fnvnx.elf
 *
 *   - the ELF is loaded at its link address, and the crt0 header words elf2nso writes into the NSO (+16 link base,
 *     +20 size, +24 relocatable) are filled in memory, so the guest's self-relocation runs with a zero slide;
 *   - a guest heap (heap_base/heap_size, default 64 MiB like tests/s5/emu_guest.py), map_fixed (mremap of the heap
 *     block, as svcMapMemory leaves the source inaccessible), the fs_* slots under --root as sdmc:/ (without
 *     --root they return -ENOSYS), wall time, audio/input stubs and ext_call (platform CPU boost: on);
 *   - --skip-faulting-init: emu_guest.py's guard of the initializer runners. Each blr of __libc_init_array and call
 *     (found in .symtab) is patched into a bl to a
 *     trampoline that records the callee-saved registers; a SIGSEGV/SIGBUS/SIGILL/SIGTRAP/SIGFPE below it resumes
 *     after the call with 0, as the emulator does. A fault with no guard prints pc, lr and the frame chain;
 *   - --expect: the process ends (exit 0) once the guest's console or debug output contains TEXT, and says after
 *     how long; a guest that returns first exits 3;
 *   - --profile FILE [--profile-hz N] [--frame SYM]...: the per-thread CPU profiler (tools/perf/frames.py, D25 #12).
 *     Every thread (main and each clone) gets a timer on its own CPU-time clock (timer_create
 *     CLOCK_THREAD_CPUTIME_ID, SIGEV_THREAD_ID, SIGPROF, N Hz, default 1000): each tick records the interrupted pc.
 *     Each --frame function's entry is patched into a branch to a trampoline that records a frame mark and runs
 *     the displaced instruction (it must not be PC-relative). Records, one text line each, buffered per thread:
 *       p <hz> <t0 ns>          header        t <tid> <slot> <mask>    a thread starts
 *       s <tid> <t ns> <pc>     a CPU sample  f <tid> <t ns> <frame#>  a frame mark (entry of a --frame function)
 *     t is CLOCK_MONOTONIC; a sample stands for 1/hz s of that thread's CPU time (emulated: relative, not device
 *     time). Buffers are written when full, when a thread ends and at every exit of linhost;
 *   - --feed FILE: scripted keyboard input (S18's scenarios, tools/perf/linfeed.py makes
 *     FILE from a fnvnx-scenario-plan/1). The steps run on CLOCK_MONOTONIC and are checked at every input_poll,
 *     sleep_ns, thread_yield and wall_time_us call, as tools/scenario/emu_scenario.py checks them on the emulator's
 *     clock: press (key events, the ups half a gap later; input_poll hands them out as FNVNX_EV_KEY), wait, until
 *     (a hook milestone: the feed's "hook 0xADDR NAME" function was entered; a file under --root, case-insensitive, at any depth; or only
 *     the timeout, for a milestone linhost cannot see) and mark. Every step is logged as "linhost: scenario ...";
 *     after the last one linhost prints "linhost: scenario complete", writes the profile and exits 0;
 *   - --progress-timeout S: the hang detector (D25 #7 / #1 on long farm runs). Progress is a guest code page sampled for
 *     the first time (the per-thread CPU timers of --profile, at --profile-hz, run for this even without --profile), a
 *     hook milestone reached, or a --feed step done. After S seconds without any, linhost prints
 *     "linhost: hang: no progress for S s (last: <what> at <ms> ms)", one "linhost: hang thread <tid> pc .. lr .. frames .."
 *     line per thread (each thread interrupted by SIGURG walks its own frame chain), writes the profile and exits 4;
 *   - --calls FILE: the call watcher (D25 #10, tools/report/scripterr.py; FILE from tools/perf/callwatch.py resolve):
 *     "call 0xADDR NAME [label=xN | label=*xN+OFF]..." lines; every entry of such a function prints
 *     "linhost: call NAME caller 0x<lr> label 0x<value> ..." (argument registers, or the 32-bit word they point to;
 *     values only, never strings; the first 5000 calls), and every exit path a "linhost: calls NAME n <count>" line. */
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
enum { SYS_mkdirat = 34, SYS_unlinkat = 35, SYS_ftruncate = 46, SYS_openat = 56, SYS_close = 57,
       SYS_getdents64 = 61, SYS_lseek = 62, SYS_read = 63, SYS_write = 64, SYS_pread64 = 67, SYS_pwrite64 = 68,
       SYS_newfstatat = 79, SYS_fstat = 80, SYS_fsync = 82, SYS_exit = 93, SYS_exit_group = 94, SYS_futex = 98,
       SYS_nanosleep = 101, SYS_clock_gettime = 113, SYS_sched_setaffinity = 122, SYS_sched_yield = 124,
       SYS_rt_sigaction = 134, SYS_gettid = 178, SYS_munmap = 215, SYS_mremap = 216, SYS_mmap = 222,
       SYS_renameat2 = 276 };
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
static void prof_flush_all(void);
static void feed_tick(void);
static void die(const char *m) { puts_(2, "linhost: "); puts_(2, m); puts_(2, "\n"); prof_flush_all(); sc(SYS_exit_group, 127, 0, 0, 0, 0, 0); }
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

static void prof_thread_begin(int slot, uint32_t mask);
static void prof_thread_end(void);
static void prof_flush_all(void);

static void thread_main(void *p) {
    HThread *h = p;
    set_affinity(0, h->mask);
    prof_thread_begin((int)(h - g_t), h->mask);
    ((u64 (*)(u64))h->entry)(h->arg);
    prof_thread_end();
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
static u64 h_thread_yield(void) { feed_tick(); sc(SYS_sched_yield, 0, 0, 0, 0, 0, 0); return 0; }
static u64 h_thread_set_priority(u64 id, u64 prio) { (void)id; (void)prio; return 0; }   /* CFS: advisory */
static u64 h_thread_set_cpu(u64 id, u64 spec) {
    if ((spec >> 40) & 0x7FFFFFull) return 0;   /* -1 and other negative values: nothing to do (host_v2.inc) */
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
    feed_tick();
    struct ts t = { (long)(timeout_ns / 1000000000ull), (long)(timeout_ns % 1000000000ull) };
    long r = sc(SYS_futex, (long)addr, FUTEX_WAIT_PRIVATE, (long)(uint32_t)expected,
                timeout_ns == UINT64_MAX ? 0 : (long)&t, 0, 0);
    return r == -ETIMEDOUT ? 1 : r == -EAGAIN ? 2 : 0;
}
static u64 h_wake_address(u64 addr, u64 count) {
    sc(SYS_futex, (long)addr, FUTEX_WAKE_PRIVATE, count > 0x7fffffff ? 0x7fffffff : (long)count, 0, 0, 0);
    return 0;
}

/* ---- --expect: end the run when the guest's output contains a text */
static const char *g_expect;
static size_t g_expect_len;
static char g_win[512];
static size_t g_win_len;
static int g_out_lock;
static u64 g_t0, g_t_entry;
static volatile u64 g_skipped;

static void put_dec(int fd, u64 v) {
    char b[24];
    int i = 23;
    b[i] = 0;
    do b[--i] = (char)('0' + v % 10); while ((v /= 10) && i);
    puts_(fd, b + i);
}
static void expect_feed(const char *s, u64 n) {
    if (!g_expect) return;
    while (__atomic_exchange_n(&g_out_lock, 1, __ATOMIC_ACQUIRE)) sc(SYS_sched_yield, 0, 0, 0, 0, 0, 0);
    for (u64 k = 0; k < n; k++) {
        if (g_win_len == sizeof g_win) {   /* keep the last g_expect_len - 1 bytes */
            memcpy(g_win, g_win + g_win_len - (g_expect_len - 1), g_expect_len - 1);
            g_win_len = g_expect_len - 1;
        }
        g_win[g_win_len++] = s[k];
        if (g_win_len >= g_expect_len) {
            const char *t = g_win + g_win_len - g_expect_len;
            size_t i = 0;
            while (i < g_expect_len && t[i] == g_expect[i]) i++;
            if (i == g_expect_len) {
                u64 t1 = now_ns();
                puts_(2, "\nlinhost: reached \""); puts_(2, g_expect); puts_(2, "\" after ");
                put_dec(2, (t1 - g_t0) / 1000000); puts_(2, " ms (guest entry after ");
                put_dec(2, (t1 - g_t_entry) / 1000000); puts_(2, " ms), initializers skipped after a fault: ");
                put_dec(2, g_skipped); puts_(2, "\n");
                prof_flush_all();
                sc(SYS_exit_group, 0, 0, 0, 0, 0, 0);
            }
        }
    }
    __atomic_store_n(&g_out_lock, 0, __ATOMIC_RELEASE);
}

/* ---- console, time, misc */
static u64 h_output_debug_string(u64 s, u64 n) {
    sc(SYS_write, 2, (long)s, (long)n, 0, 0, 0);
    expect_feed((const char *)(uintptr_t)s, n);
    return 0;
}
static u64 h_console_write(u64 s, u64 n) {
    u64 r = (u64)sc(SYS_write, 1, (long)s, (long)n, 0, 0, 0);
    expect_feed((const char *)(uintptr_t)s, n);
    return r;
}
static void feed_incomplete(void);
static u64 h_exit(u64 code) { feed_incomplete(); prof_flush_all(); sc(SYS_exit_group, (long)code, 0, 0, 0, 0, 0); return 0; }
static u64 h_sleep_ns(u64 ns) { feed_tick(); struct ts t = { (long)(ns / 1000000000ull), (long)(ns % 1000000000ull) }; sc(SYS_nanosleep, (long)&t, 0, 0, 0, 0, 0); return 0; }
static u64 h_zero(void) { return 0; }
static u64 h_one(void) { return 1; }
static u64 h_tick(void) { return now_ns(); }
static u64 h_tick_freq(void) { return 1000000000ull; }
static u64 h_cpu_count(void) { return 3; }
static u64 h_random(void) { static u64 x = 0x9E3779B97F4A7C15ull; u64 v = __atomic_load_n(&x, __ATOMIC_RELAXED); v ^= v << 13; v ^= v >> 7; v ^= v << 17; __atomic_store_n(&x, v, __ATOMIC_RELAXED); return v; }
static u64 h_meminfo(u64 p) { FnvnxMemInfo *m = (FnvnxMemInfo *)(uintptr_t)p; m->total_phys = 4ull << 30; m->avail_phys = 3ull << 30; return 0; }
static u64 h_enosys(void) { return (u64)-ENOSYS; }
static u64 h_wall_time_us(void) { feed_tick(); struct ts t; sc(SYS_clock_gettime, 0 /* REALTIME */, (long)&t, 0, 0, 0, 0); return (u64)t.s * 1000000ull + (u64)t.ns / 1000; }
static u64 h_audio_open(u64 rate, u64 period) { (void)period; return rate == 48000 ? 0 : (u64)-1; }
static u64 h_audio_submit(u64 pcm, u64 frames) { (void)pcm; return h_sleep_ns(frames * 1000000000ull / 48000); }
static u64 h_ext_call(u64 module, u64 fn, u64 args, u64 nargs) {
    (void)args; (void)nargs;
    if (module == FNVNX_EXT_PLATFORM && fn == FNVNX_PLAT_CPU_BOOST) return 0;   /* boost on (nothing to do here) */
    return FNVNX_EXT_NO_MODULE;
}

/* ---- memory: the guest heap and map_fixed */
#define PROT_RW 3
#define MAP_PRIVATE_ANON 0x22
#define MAP_FIXED 0x10
#define MAP_FIXED_NOREPLACE 0x100000
static u64 map_at(u64 addr, u64 size, int prot) {   /* exactly at addr, never over an existing mapping; 0 or -1 */
    long m = sc(SYS_mmap, (long)addr, (long)size, prot, MAP_PRIVATE_ANON | MAP_FIXED_NOREPLACE, -1, 0);
    if (m == (long)addr) return 0;
    if (m > 0) sc(SYS_munmap, m, (long)size, 0, 0, 0, 0);   /* a kernel that took it as a hint */
    return (u64)-1;
}
/* the original image's data window, which the Switch host claims at startup (startup/host/reserve_nx.c, S10):
 * map_fixed inside it needs no source block (host_v3.inc) */
#define WINDOW_LO 0x00400000ull
#define WINDOW_HI 0x02100000ull
static u64 h_map_fixed(u64 addr, u64 size, u64 src) {
    if ((addr | size) & 0xFFF || !size) return 1;
    if (addr >= WINDOW_LO && addr + size <= WINDOW_HI) {   /* zeroed RW memory, nothing to map from */
        if (map_at(addr, size, PROT_RW)) memset((void *)(uintptr_t)addr, 0, size);   /* mapped before: zero it */
        return 0;
    }
    if (!src || src & 0xFFF) return 1;
    if (map_at(addr, size, PROT_RW)) return 2;   /* occupied */
    /* svcMapMemory: addr gets src's pages, src becomes inaccessible */
    long m = sc(SYS_mremap, (long)src, (long)size, (long)size, 3 /* MAYMOVE | FIXED */, (long)addr, 0);
    if (m != (long)addr) memcpy((void *)(uintptr_t)addr, (const void *)(uintptr_t)src, size);   /* no mremap: a copy */
    return 0;
}

/* ---- files: sdmc:/ is --root */
static const char *g_root;
#define AT_FDCWD -100
#define O_DIRECTORY 040000
#define O_CLOEXEC 02000000
static int fs_path(char *out, size_t cap, u64 gp) {
    const char *p = (const char *)(uintptr_t)gp;
    if (p[0] == 's' && p[1] == 'd' && p[2] == 'm' && p[3] == 'c' && p[4] == ':') p += 5;
    while (*p == '/') p++;
    size_t n = slen(g_root), m = slen(p);
    if (n + 1 + m + 1 > cap) return -36;   /* ENAMETOOLONG */
    memcpy(out, g_root, n);
    out[n] = '/';
    memcpy(out + n + 1, p, m + 1);
    return 0;
}
#define PATH(buf, gp) char buf[1024]; { int e_ = fs_path(buf, sizeof buf, gp); if (e_) return (u64)(long)e_; }
static u64 h_fs_open(u64 gp, u64 flags) {
    PATH(path, gp);
    long fl = (flags & 3) == 3 ? 2 : (flags & 2) ? 1 : 0;
    if (flags & FNVNX_O_CREATE) fl |= 0100;
    if (flags & FNVNX_O_EXCL) fl |= 0200;
    if (flags & FNVNX_O_TRUNC) fl |= 01000;
    if (flags & FNVNX_O_APPEND) fl |= 02000;
    return (u64)sc(SYS_openat, AT_FDCWD, (long)path, fl | O_CLOEXEC, 0666, 0, 0);
}
static u64 h_fs_read(u64 fd, u64 b, u64 n) { return (u64)sc(SYS_read, (long)fd, (long)b, (long)n, 0, 0, 0); }
static u64 h_fs_write(u64 fd, u64 b, u64 n) { return (u64)sc(SYS_write, (long)fd, (long)b, (long)n, 0, 0, 0); }
static u64 h_fs_pread(u64 fd, u64 b, u64 n, u64 off) { return (u64)sc(SYS_pread64, (long)fd, (long)b, (long)n, (long)off, 0, 0); }
static u64 h_fs_pwrite(u64 fd, u64 b, u64 n, u64 off) { return (u64)sc(SYS_pwrite64, (long)fd, (long)b, (long)n, (long)off, 0, 0); }
static u64 h_fs_seek(u64 fd, u64 off, u64 whence) { return (u64)sc(SYS_lseek, (long)fd, (long)off, (long)whence, 0, 0, 0); }
static u64 h_fs_truncate(u64 fd, u64 size) { return (u64)sc(SYS_ftruncate, (long)fd, (long)size, 0, 0, 0, 0); }
static u64 h_fs_sync(u64 fd) { return (u64)sc(SYS_fsync, (long)fd, 0, 0, 0, 0, 0); }
static u64 h_fs_close(u64 fd) { return (u64)sc(SYS_close, (long)fd, 0, 0, 0, 0, 0); }
typedef struct LStat {   /* struct stat of aarch64 Linux (asm-generic) */
    u64 dev, ino; uint32_t mode, nlink, uid, gid; u64 rdev, pad1; int64_t size; int32_t blksize, pad2;
    int64_t blocks, atime, atime_ns, mtime, mtime_ns, ctime, ctime_ns; uint32_t unused[2];
} LStat;
_Static_assert(sizeof(LStat) == 128, "struct stat");
static u64 put_stat(long r, const LStat *st, u64 out) {
    if (r < 0) return (u64)r;
    FnvnxStat *f = (FnvnxStat *)(uintptr_t)out;
    f->is_dir = (st->mode & 0170000) == 0040000;
    f->read_only = !(st->mode & 0200);
    f->size = (u64)st->size;
    f->mtime_us = st->mtime * 1000000 + st->mtime_ns / 1000;
    f->atime_us = st->atime * 1000000 + st->atime_ns / 1000;
    f->ctime_us = st->ctime * 1000000 + st->ctime_ns / 1000;
    return 0;
}
static u64 h_fs_fstat(u64 fd, u64 out) { LStat st; return put_stat(sc(SYS_fstat, (long)fd, (long)&st, 0, 0, 0, 0), &st, out); }
static u64 h_fs_stat(u64 gp, u64 out) {
    PATH(path, gp);
    LStat st;
    return put_stat(sc(SYS_newfstatat, AT_FDCWD, (long)path, (long)&st, 0, 0, 0), &st, out);
}
static u64 h_fs_mkdir(u64 gp) { PATH(path, gp); return (u64)sc(SYS_mkdirat, AT_FDCWD, (long)path, 0777, 0, 0, 0); }
static u64 h_fs_rmdir(u64 gp) { PATH(path, gp); return (u64)sc(SYS_unlinkat, AT_FDCWD, (long)path, 0x200 /* AT_REMOVEDIR */, 0, 0, 0); }
static u64 h_fs_remove(u64 gp) { PATH(path, gp); return (u64)sc(SYS_unlinkat, AT_FDCWD, (long)path, 0, 0, 0, 0); }
static u64 h_fs_rename(u64 ga, u64 gb) {
    PATH(a, ga);
    PATH(b, gb);
    return (u64)sc(SYS_renameat2, AT_FDCWD, (long)a, AT_FDCWD, (long)b, 0, 0);
}
#define MAXD 16
static struct HDir { int used, fd, pos, len; char buf[4096]; } g_dirs[MAXD];
static u64 h_fs_dir_open(u64 gp) {
    PATH(path, gp);
    long fd = sc(SYS_openat, AT_FDCWD, (long)path, O_DIRECTORY | O_CLOEXEC, 0, 0, 0);
    if (fd < 0) return 0;
    for (int i = 0; i < MAXD; i++)
        if (!__atomic_exchange_n(&g_dirs[i].used, 1, __ATOMIC_ACQ_REL)) {
            g_dirs[i].fd = (int)fd; g_dirs[i].pos = g_dirs[i].len = 0;
            return (u64)i + 1;
        }
    sc(SYS_close, fd, 0, 0, 0, 0, 0);
    return 0;
}
static u64 h_fs_dir_next(u64 id, u64 buf, u64 cap) {
    if (!id || id > MAXD || !cap) return 0;
    struct HDir *d = &g_dirs[id - 1];
    for (;;) {
        if (d->pos >= d->len) {
            long n = sc(SYS_getdents64, d->fd, (long)d->buf, sizeof d->buf, 0, 0, 0);
            if (n <= 0) return 0;
            d->len = (int)n; d->pos = 0;
        }
        const char *e = d->buf + d->pos;
        d->pos += *(const uint16_t *)(e + 16);
        const char *name = e + 19;
        if (name[0] == '.' && (!name[1] || (name[1] == '.' && !name[2]))) continue;
        size_t n = slen(name);
        if (n > cap - 1) n = cap - 1;
        memcpy((char *)(uintptr_t)buf, name, n);
        ((char *)(uintptr_t)buf)[n] = 0;
        return n;
    }
}
static u64 h_fs_dir_close(u64 id) {
    if (!id || id > MAXD) return 0;
    sc(SYS_close, g_dirs[id - 1].fd, 0, 0, 0, 0, 0);
    __atomic_store_n(&g_dirs[id - 1].used, 0, __ATOMIC_RELEASE);
    return 0;
}

static FnvnxHostApi g_api;

/* ---- --skip-faulting-init: guarded calls of the initializer runners (tests/s5/emu_guest.py install_guards)
 * A patched site  blr xN  becomes  bl T  with T: mov x16, xN; ldr x17, =guard_enter; br x17. guard_enter saves a
 * GuardRec on the guest stack (argument registers, the callee-saved x19-x30 and d8-d15), links it into the calling
 * thread's guard list, calls x16 and unlinks it. A fault below it makes the signal handler unlink it and resume at
 * guard_resume with sp = the record: the callee-saved registers come back and the call returns 0. */
typedef struct GuardRec {
    u64 a[8], target, x8, cs[12], d[8];
    struct GuardRec *prev;
    u64 pad;
} GuardRec;
_Static_assert(sizeof(GuardRec) == 256, "GuardRec is guard_enter's frame");
#define MAXG (MAXT + 8)
static struct { int tid; GuardRec *top; } g_guard[MAXG];
static u64 g_trap_vslot, g_trap_special;

static int gettid_(void) { return (int)sc(SYS_gettid, 0, 0, 0, 0, 0, 0); }
static int guard_slot(int tid, int claim) {
    for (int i = 0; i < MAXG; i++)
        if (__atomic_load_n(&g_guard[i].tid, __ATOMIC_ACQUIRE) == tid) return i;
    if (!claim) return -1;
    for (int i = 0; i < MAXG; i++) {
        int z = 0;
        if (__atomic_compare_exchange_n(&g_guard[i].tid, &z, tid, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) return i;
    }
    die("guard table full");
    return -1;
}
__attribute__((used)) void linhost_guard_push(GuardRec *r) {
    int i = guard_slot(gettid_(), 1);
    r->prev = g_guard[i].top;
    g_guard[i].top = r;
}
__attribute__((used)) void linhost_guard_pop(GuardRec *r) {
    int i = guard_slot(gettid_(), 0);
    if (i >= 0) g_guard[i].top = r->prev;
}
void linhost_guard_enter(void);
void linhost_guard_resume(void);
__asm__(".text\n.global linhost_guard_enter\nlinhost_guard_enter:\n"
        "  sub sp, sp, #256\n"
        "  stp x0, x1, [sp, #0]\n  stp x2, x3, [sp, #16]\n  stp x4, x5, [sp, #32]\n  stp x6, x7, [sp, #48]\n"
        "  stp x16, x8, [sp, #64]\n"
        "  stp x19, x20, [sp, #80]\n  stp x21, x22, [sp, #96]\n  stp x23, x24, [sp, #112]\n"
        "  stp x25, x26, [sp, #128]\n  stp x27, x28, [sp, #144]\n  stp x29, x30, [sp, #160]\n"
        "  stp d8, d9, [sp, #176]\n  stp d10, d11, [sp, #192]\n  stp d12, d13, [sp, #208]\n  stp d14, d15, [sp, #224]\n"
        "  mov x0, sp\n  bl linhost_guard_push\n"
        "  ldp x0, x1, [sp, #0]\n  ldp x2, x3, [sp, #16]\n  ldp x4, x5, [sp, #32]\n  ldp x6, x7, [sp, #48]\n"
        "  ldp x16, x8, [sp, #64]\n  ldp x29, x30, [sp, #160]\n"
        "  blr x16\n"
        "  stp x0, x1, [sp, #0]\n"
        "  mov x0, sp\n  bl linhost_guard_pop\n"
        "  ldp x0, x1, [sp, #0]\n"
        "  b 1f\n"
        ".global linhost_guard_resume\nlinhost_guard_resume:\n"
        "  mov x0, #0\n  mov x1, #0\n"
        "1:\n"
        "  ldp x19, x20, [sp, #80]\n  ldp x21, x22, [sp, #96]\n  ldp x23, x24, [sp, #112]\n"
        "  ldp x25, x26, [sp, #128]\n  ldp x27, x28, [sp, #144]\n  ldp x29, x30, [sp, #160]\n"
        "  ldp d8, d9, [sp, #176]\n  ldp d10, d11, [sp, #192]\n  ldp d12, d13, [sp, #208]\n  ldp d14, d15, [sp, #224]\n"
        "  add sp, sp, #256\n  ret\n");
void linhost_sigreturn(void);
__asm__(".text\n.global linhost_sigreturn\nlinhost_sigreturn:\n  mov x8, #139\n  svc #0\n");

static int readable(u64 a) { return a >= 0x10000 && a < 0xC0000000ull; }   /* no probing: only the range check */

/* ---- --profile: per-thread CPU samples and frame marks (see the header) */
#define PROF_SLOTS (MAXT + 1)   /* clone slots 0..63, the main thread last */
#define PROF_BUF 4096
typedef struct { volatile int tid; volatile int busy; int n, timer; char b[PROF_BUF]; } ProfBuf;
static ProfBuf g_prof[PROF_SLOTS];
static long g_prof_fd = -1;
static u64 g_prof_hz = 1000;
static volatile uint32_t g_frames;
static int g_prof_lock;
/* --progress-timeout: the last progress and what it was */
static u64 g_progress_ns, g_progress_s;
static const char *volatile g_progress_what = "start";
static uint8_t g_pages[1 << 17];   /* 1 bit per 4 KiB guest code page: 4 GiB */
static void progress(const char *what) { __atomic_store_n(&g_progress_ns, now_ns(), __ATOMIC_RELAXED); g_progress_what = what; }
static void prof_write(const char *b, int n) {   /* one writer at a time: lines stay whole */
    while (__atomic_exchange_n(&g_prof_lock, 1, __ATOMIC_ACQUIRE)) sc(SYS_sched_yield, 0, 0, 0, 0, 0, 0);
    for (int o = 0; o < n;) { long r = sc(SYS_write, g_prof_fd, (long)(b + o), n - o, 0, 0, 0); if (r <= 0) break; o += (int)r; }
    __atomic_store_n(&g_prof_lock, 0, __ATOMIC_RELEASE);
}
static ProfBuf *prof_mine(int tid) {
    for (int i = 0; i < PROF_SLOTS; i++) if (g_prof[i].tid == tid) return &g_prof[i];
    return 0;
}
static void prof_hex(ProfBuf *p, u64 v) {
    char t[16]; int n = 0;
    do t[n++] = "0123456789abcdef"[v & 15]; while (v >>= 4);
    while (n) p->b[p->n++] = t[--n];
}
static void prof_dec(ProfBuf *p, u64 v) {
    char t[20]; int n = 0;
    do t[n++] = (char)('0' + v % 10); while (v /= 10);
    while (n) p->b[p->n++] = t[--n];
}
static void prof_flush(ProfBuf *p) { if (p->n) { prof_write(p->b, p->n); p->n = 0; } }
/* one record: c tid t x (x in hex for 's', decimal otherwise) */
static void prof_rec(ProfBuf *p, char c, u64 t, u64 x, int hex) {
    if (p->n > PROF_BUF - 64) prof_flush(p);
    p->b[p->n++] = c; p->b[p->n++] = ' ';
    prof_dec(p, (u64)p->tid); p->b[p->n++] = ' ';
    prof_dec(p, t); p->b[p->n++] = ' ';
    if (hex) { p->b[p->n++] = '0'; p->b[p->n++] = 'x'; prof_hex(p, x); } else prof_dec(p, x);
    p->b[p->n++] = '\n';
}
static volatile uint32_t g_prof_dropped;
static void on_prof(int sig, void *info, void *ucv) {
    (void)sig;
    if (g_progress_s) {   /* a code page sampled for the first time is progress */
        u64 pc = *(u64 *)((unsigned char *)ucv + 440);
        if (pc < (1ull << 32)) {
            uint32_t pg = (uint32_t)(pc >> 12);
            uint8_t bit = (uint8_t)(1u << (pg & 7));
            if (!(__atomic_fetch_or(&g_pages[pg >> 3], bit, __ATOMIC_RELAXED) & bit)) progress("new code");
        }
    }
    if (g_prof_fd < 0) return;
    ProfBuf *p = prof_mine(gettid_());
    int over = *(int *)((unsigned char *)info + 20);   /* siginfo _timer.si_overrun: expirations merged into this one */
    if (over < 0 || over > 100000) over = 0;
    if (!p || p->busy) { __atomic_add_fetch(&g_prof_dropped, 1u + (uint32_t)over, __ATOMIC_RELAXED); return; }
    p->busy = 1;
    prof_rec(p, 's', now_ns(), *(u64 *)((unsigned char *)ucv + 440), 1);
    if (over) {   /* "s tid t pc w": this sample stands for 1 + overrun ticks */
        p->n--;
        p->b[p->n++] = ' ';
        prof_dec(p, 1u + (u64)over);
        p->b[p->n++] = '\n';
    }
    p->busy = 0;
}
/* --feed's hooks (milestones) and --frame share the trampolines (patch_site) */
#define HOOK_MAX 128
static const char *g_hook_name[HOOK_MAX];
static volatile int g_hook_hit[HOOK_MAX];
static u64 g_hook_at[HOOK_MAX];
static int g_hook_frame[HOOK_MAX];   /* the same function is a --frame too: one site does both */
/* --calls: a hook that logs its arguments; specs: register, deref (read the u32 at reg + off), label */
typedef struct { const char *label; uint8_t reg, deref; uint32_t off; } CallSpec;
static uint8_t g_hook_call[HOOK_MAX], g_hook_nspec[HOOK_MAX];
static CallSpec g_hook_spec[HOOK_MAX][4];
static volatile u64 g_hook_count[HOOK_MAX];
static volatile u64 g_call_lines;
static void hook_mark(uint32_t id);
__attribute__((used)) void linhost_hook_hit(uint32_t id, const u64 *regs);
static void linhost_prof_frame(void) {
    if (g_prof_fd < 0) return;
    ProfBuf *p = prof_mine(gettid_());
    uint32_t k = __atomic_add_fetch(&g_frames, 1, __ATOMIC_RELAXED);
    if (!p) return;
    p->busy = 1;
    prof_rec(p, 'f', now_ns(), k, 0);
    p->busy = 0;
}
static int readable(u64 a);
/* regs: the stub's save area: x0..x7 at [0..7], x8, the continuation, x29, x30 (the caller's return address) */
static void call_log(uint32_t id, const u64 *regs) {
    __atomic_add_fetch(&g_hook_count[id], 1, __ATOMIC_RELAXED);
    if (__atomic_add_fetch(&g_call_lines, 1, __ATOMIC_RELAXED) > 5000) return;
    char line[400];
    int o = 0;
    #define CAPP(s) do { const char *s_ = (s); while (*s_ && o < (int)sizeof line - 2) line[o++] = *s_++; } while (0)
    #define CHEX(v) do { u64 v_ = (v); char h_[19]; int n_ = 0; char t_[16]; do t_[n_++] = "0123456789abcdef"[v_ & 15]; while (v_ >>= 4); h_[0] = '0'; h_[1] = 'x'; int k_ = 2; while (n_) h_[k_++] = t_[--n_]; h_[k_] = 0; CAPP(h_); } while (0)
    CAPP("linhost: call "); CAPP(g_hook_name[id]); CAPP(" caller "); CHEX(regs[11]);
    for (int k = 0; k < g_hook_nspec[id]; k++) {
        const CallSpec *c = &g_hook_spec[id][k];
        u64 v = c->reg <= 8 ? regs[c->reg] : 0;
        CAPP(" "); CAPP(c->label); CAPP(" ");
        if (c->deref) {
            u64 a = (v & 0xFFFFFFFFull) + c->off;
            if (readable(a) && !(a & 3)) { CHEX(*(volatile uint32_t *)(uintptr_t)a); } else CAPP("unreadable");
        } else CHEX(v & 0xFFFFFFFFull);
    }
    line[o++] = '\n';
    sc(SYS_write, 2, (long)line, o, 0, 0, 0);
    #undef CAPP
    #undef CHEX
}
__attribute__((used)) void linhost_hook_hit(uint32_t id, const u64 *regs) {
    if (id == 0xFFFFu) { linhost_prof_frame(); return; }
    if (id < HOOK_MAX && g_hook_frame[id]) linhost_prof_frame();
    if (id < HOOK_MAX && g_hook_call[id]) { call_log(id, regs); return; }
    hook_mark(id);
    feed_tick();   /* a milestone may end the plan: at once, not at the next host call */
}
static void prof_thread_begin(int slot, uint32_t mask) {
    if (g_prof_fd < 0 && !g_progress_s) return;
    int tid = gettid_();
    ProfBuf *p = 0;
    for (int i = 0; i < PROF_SLOTS && !p; i++)
        if (__atomic_compare_exchange_n(&g_prof[i].tid, &(int){0}, tid, 0, __ATOMIC_ACQ_REL, __ATOMIC_RELAXED)) p = &g_prof[i];
    if (!p) return;
    p->n = 0;
    if (g_prof_fd >= 0) {
        p->busy = 1;
        prof_rec(p, 't', (u64)slot, mask, 0);   /* t <tid> <slot> <mask> */
        prof_flush(p);
        p->busy = 0;
    }
    /* struct sigevent: value, signo, notify, tid (SIGEV_THREAD_ID = 4) */
    struct { u64 value; int signo, notify, tid; int pad[11]; } ev = { 0, 27 /* SIGPROF */, 4, tid, {0} };
    int timer = 0;
    long r = sc(107 /* timer_create */, 3 /* CLOCK_THREAD_CPUTIME_ID */, (long)&ev, (long)&timer, 0, 0, 0);
    /* qemu-user keeps 32 timers: a thread's is deleted at its end (prof_thread_end), so 32 may run at once */
    if (r < 0) { p->timer = -1; puts_(2, "linhost: --profile: timer_create failed "); put_dec(2, (u64)-r); puts_(2, "\n"); return; }
    p->timer = timer;
    u64 ns = 1000000000ull / g_prof_hz;
    struct ts it[2] = { { (long)(ns / 1000000000ull), (long)(ns % 1000000000ull) },
                        { (long)(ns / 1000000000ull), (long)(ns % 1000000000ull) } };
    sc(110 /* timer_settime */, timer, 0, (long)it, 0, 0, 0);
}
static void prof_thread_end(void) {
    if (g_prof_fd < 0 && !g_progress_s) return;
    ProfBuf *p = prof_mine(gettid_());
    if (!p) return;
    if (p->timer >= 0) sc(111 /* timer_delete */, p->timer, 0, 0, 0, 0, 0);
    p->busy = 1;
    if (g_prof_fd >= 0) prof_flush(p);
    p->busy = 0;
    __atomic_store_n(&p->tid, 0, __ATOMIC_RELEASE);   /* the slot is free (an ended thread's timer never fires) */
}
static volatile int g_calls_said;
static void calls_summary(void) {
    if (__atomic_exchange_n(&g_calls_said, 1, __ATOMIC_ACQ_REL)) return;
    for (int i = 0; i < HOOK_MAX; i++)
        if (g_hook_call[i]) {
            puts_(2, "linhost: calls "); puts_(2, g_hook_name[i]); puts_(2, " n ");
            put_dec(2, g_hook_count[i]); puts_(2, "\n");
        }
}
static void prof_flush_all(void) {
    calls_summary();
    if (g_prof_fd < 0) return;
    for (int i = 0; i < PROF_SLOTS; i++) if (g_prof[i].tid > 0) prof_flush(&g_prof[i]);
}
static void prof_open(const char *path) {
    g_prof_fd = sc(SYS_openat, AT_FDCWD, (long)path, 0x1 | 0x40 | 0x200 | 0x400 /* WRONLY CREAT TRUNC APPEND */, 0644, 0, 0);
    if (g_prof_fd < 0) die("--profile: cannot create the file");
    char h[64]; ProfBuf tmp; tmp.n = 0; tmp.tid = 0;
    tmp.b[tmp.n++] = 'p'; tmp.b[tmp.n++] = ' '; prof_dec(&tmp, g_prof_hz); tmp.b[tmp.n++] = ' ';
    prof_dec(&tmp, now_ns()); tmp.b[tmp.n++] = '\n';
    memcpy(h, tmp.b, (size_t)tmp.n);
    prof_write(h, tmp.n);
    struct { u64 handler, flags, restorer, mask; } sa = {
        (u64)(uintptr_t)on_prof, 4 /* SA_SIGINFO */ | 0x10000000 /* SA_RESTART */ | 0x04000000 /* SA_RESTORER */,
        (u64)(uintptr_t)linhost_sigreturn, 0 };
    sc(SYS_rt_sigaction, 27, (long)&sa, 0, 8, 0, 0);
}
/* the frame hook: saves what is live at a function entry (x0-x8, q0-q7, x29/x30, the continuation in x16), records
 * the mark, restores, continues at the displaced instruction (x16) */
void linhost_frame_stub(void);
__asm__(".text\n.global linhost_frame_stub\nlinhost_frame_stub:\n"
        "  sub sp, sp, #224\n"
        "  stp x0, x1, [sp]\n  stp x2, x3, [sp, #16]\n  stp x4, x5, [sp, #32]\n  stp x6, x7, [sp, #48]\n"
        "  stp x8, x16, [sp, #64]\n  stp x29, x30, [sp, #80]\n"
        "  stp q0, q1, [sp, #96]\n  stp q2, q3, [sp, #128]\n  stp q4, q5, [sp, #160]\n  stp q6, q7, [sp, #192]\n"
        "  mov w0, w15\n  mov x1, sp\n  bl linhost_hook_hit\n"
        "  ldp q0, q1, [sp, #96]\n  ldp q2, q3, [sp, #128]\n  ldp q4, q5, [sp, #160]\n  ldp q6, q7, [sp, #192]\n"
        "  ldp x0, x1, [sp]\n  ldp x2, x3, [sp, #16]\n  ldp x4, x5, [sp, #32]\n  ldp x6, x7, [sp, #48]\n"
        "  ldp x8, x16, [sp, #64]\n  ldp x29, x30, [sp, #80]\n"
        "  add sp, sp, #224\n  br x16\n");

/* ---- --collect-traps (S5's collect-all boot): a virtual function defined nowhere (fnvnx_vslot_trap) or a
 * constructor/destructor defined nowhere (fnvnx_special_trap) returns to its caller instead of stopping the boot:
 * 0, or this (x0 as it came) for a constructor/destructor. Each distinct (kind, caller, text) is printed once as
 *   linhost: collect<TAB>vslot|special<TAB>caller<TAB>text
 * so one boot lists every such trap up to the first real fault (tests/s5/collect_traps.py groups them). */
static int g_collect;
static u64 g_collect_seen[8192];
static u64 g_collect_n;
static int collect_trap(u64 *x, u64 *pc) {
    int special = *pc == g_trap_special;
    u64 text = readable(x[1]) ? x[1] : 0, key = (x[30] * 0x9E3779B97F4A7C15ull) ^ text ^ (u64)special;
    key |= 1;
    for (u64 h = key % 8192, k = 0; k < 8192; k++, h = (h + 1) % 8192) {
        u64 cur = __atomic_load_n(&g_collect_seen[h], __ATOMIC_ACQUIRE);
        if (cur == key) break;
        if (!cur && __atomic_compare_exchange_n(&g_collect_seen[h], &cur, key, 0, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
            u64 n = __atomic_add_fetch(&g_collect_n, 1, __ATOMIC_RELAXED);
            puts_(2, "linhost: collect\t"); puts_(2, special ? "special" : "vslot"); puts_(2, "\t");
            put_hex(2, x[30]); puts_(2, "\t");
            puts_(2, text ? (const char *)(uintptr_t)text : "?"); puts_(2, "\t#"); put_dec(2, n); puts_(2, "\n");
            break;
        }
        if (cur == key) break;
    }
    if (!special) x[0] = 0;
    *pc = x[30];
    return 1;
}
static void on_fault(int sig, void *info, void *ucv) {
    (void)info;
    unsigned char *uc = ucv;            /* struct ucontext: uc_mcontext (struct sigcontext) at 176 */
    u64 fault = *(u64 *)(uc + 176), *x = (u64 *)(uc + 184), *sp = (u64 *)(uc + 432), *pc = (u64 *)(uc + 440);
    int i = guard_slot(gettid_(), 0);
    GuardRec *r = i >= 0 ? g_guard[i].top : 0;
    int trap = *pc == g_trap_vslot || *pc == g_trap_special;
    if (trap && g_collect && sig == 5 && collect_trap(x, pc)) return;
    if (r) {
        g_guard[i].top = r->prev;
        u64 n = __atomic_add_fetch(&g_skipped, 1, __ATOMIC_RELAXED);
        if (n <= 30) {
            puts_(2, "linhost: skipped initializer "); put_hex(2, r->target); puts_(2, ": signal "); put_dec(2, (u64)sig);
            puts_(2, " at pc "); put_hex(2, *pc);
            if (trap && readable(x[1])) { puts_(2, ": "); puts_(2, (const char *)(uintptr_t)x[1]); }
            puts_(2, "\n");
        }
        *sp = (u64)(uintptr_t)r;
        *pc = (u64)(uintptr_t)linhost_guard_resume;
        return;
    }
    puts_(2, "\nlinhost: fault: signal "); put_dec(2, (u64)sig); puts_(2, " thread "); put_dec(2, (u64)gettid_());
    puts_(2, " pc "); put_hex(2, *pc); puts_(2, " lr "); put_hex(2, x[30]); puts_(2, " addr "); put_hex(2, fault);
    puts_(2, " sp "); put_hex(2, *sp);
    if (trap && readable(x[1])) { puts_(2, "\nlinhost: trap: "); puts_(2, (const char *)(uintptr_t)x[1]); }
    puts_(2, "\nlinhost: frames");
    u64 fp = x[29];
    for (int k = 0; k < 16 && readable(fp) && !(fp & 7); k++) {
        u64 *f = (u64 *)(uintptr_t)fp;
        puts_(2, " "); put_hex(2, f[1]);
        if (f[0] <= fp) break;
        fp = f[0];
    }
    puts_(2, "\nlinhost: initializers skipped after a fault: "); put_dec(2, g_skipped); puts_(2, "\n");
    prof_flush_all();
    sc(SYS_exit_group, 128 + sig, 0, 0, 0, 0, 0);
}
static void install_fault_handler(void) {
    struct { u64 handler, flags, restorer, mask; } sa = {
        (u64)(uintptr_t)on_fault, 4 /* SA_SIGINFO */ | 0x04000000 /* SA_RESTORER */, (u64)(uintptr_t)linhost_sigreturn, 0 };
    static const int sigs[] = { 4 /* ILL */, 5 /* TRAP */, 7 /* BUS */, 8 /* FPE */, 11 /* SEGV */ };
    for (unsigned k = 0; k < sizeof sigs / sizeof sigs[0]; k++)
        sc(SYS_rt_sigaction, sigs[k], (long)&sa, 0, 8, 0, 0);
}

/* ---- ELF32 loader */
typedef struct { unsigned char id[16]; uint16_t type, machine; uint32_t version, entry, phoff, shoff, flags;
                 uint16_t ehsize, phentsize, phnum, shentsize, shnum, shstrndx; } E32;
typedef struct { uint32_t type, offset, vaddr, paddr, filesz, memsz, flags, align; } P32;
typedef struct { uint32_t name, type, flags, addr, offset, size, link, info, addralign, entsize; } S32;
typedef struct { uint32_t name, value, size; unsigned char info, other; uint16_t shndx; } Y32;

static u64 g_lo = ~0ull, g_hi;   /* the guest's mapped range */

static u64 load(const char *path, long *fdp) {
    long fd = sc(SYS_openat, AT_FDCWD, (long)path, 0, 0, 0, 0);
    if (fd < 0) die("cannot open the guest");
    E32 e;
    if (sc(SYS_pread64, fd, (long)&e, sizeof e, 0, 0, 0) != sizeof e || e.id[4] != 1 || e.machine != 183)
        die("not an ELF32 AArch64 guest");
    int dynamic = 0;
    for (unsigned i = 0; i < e.phnum; i++) {
        P32 p;
        sc(SYS_pread64, fd, (long)&p, sizeof p, e.phoff + i * e.phentsize, 0, 0);
        if (p.type == 2) dynamic = 1;
        if (p.type != 1 || !p.memsz) continue;
        u64 lo = p.vaddr & ~0xFFFull, hi = ((u64)p.vaddr + p.memsz + 0xFFF) & ~0xFFFull;
        long m = sc(SYS_mmap, (long)lo, (long)(hi - lo), 7, 0x32 /* PRIVATE|ANON|FIXED: the range is ours (linked below it) */, -1, 0);
        if (m != (long)lo) die("cannot map a guest segment at its address");
        for (u64 done = 0; done < p.filesz;) {   /* a read may stop short of a large segment */
            long r = sc(SYS_pread64, fd, (long)(p.vaddr + done), (long)(p.filesz - done), (long)(p.offset + done), 0, 0);
            if (r <= 0) die("short read");
            done += (u64)r;
        }
        if (lo < g_lo) g_lo = lo;
        if (hi > g_hi) g_hi = hi;
    }
    /* an fnv-nx game module (crt0.s): fill the header words elf2nso.py writes into the NSO */
    uint32_t *hdr = (uint32_t *)(uintptr_t)e.entry;
    if (e.entry == g_lo && hdr[2] == FNVNX_GUEST_MAGIC) {
        hdr[4] = (uint32_t)g_lo;
        hdr[5] = (uint32_t)(g_hi - g_lo);
        hdr[6] = dynamic ? 1u : 0u;   /* loaded at the link base: the self-relocation's slide is 0 */
        puts_(2, "linhost: fnv-nx guest module "); put_hex(2, g_lo); puts_(2, ".."); put_hex(2, g_hi); puts_(2, "\n");
    }
    *fdp = fd;
    return e.entry;
}

static void *anon(u64 size) {
    long m = sc(SYS_mmap, 0, (long)size, PROT_RW, MAP_PRIVATE_ANON, -1, 0);
    if (m < 0) die("out of memory");
    return (void *)m;
}
static int streq(const char *a, const char *b) { while (*a && *a == *b) a++, b++; return *a == *b; }

/* patch every blr of the initializer runners (by name, .symtab) into a guarded call */
static void install_guards(long fd) {
    static const char *const runners[] = { "__libc_init_array", "call" };
    E32 e;
    sc(SYS_pread64, fd, (long)&e, sizeof e, 0, 0, 0);
    S32 *sh = anon((u64)e.shnum * sizeof(S32));
    sc(SYS_pread64, fd, (long)sh, (long)e.shnum * sizeof(S32), e.shoff, 0, 0);
    S32 *symtab = 0;
    for (unsigned i = 0; i < e.shnum; i++) if (sh[i].type == 2) symtab = &sh[i];
    if (!symtab) die("--skip-faulting-init: the guest has no .symtab");
    S32 *strtab = &sh[symtab->link];
    Y32 *sym = anon(symtab->size);
    char *str = anon(strtab->size + 1);
    if (sc(SYS_pread64, fd, (long)sym, symtab->size, symtab->offset, 0, 0) != (long)symtab->size ||
        sc(SYS_pread64, fd, (long)str, strtab->size, strtab->offset, 0, 0) != (long)strtab->size)
        die("--skip-faulting-init: cannot read .symtab");
    /* the trampolines: one page right above the guest (bl reaches +-128 MiB) */
    u64 tramp = (g_hi + 0xFFFF) & ~0xFFFFull;
    if (map_at(tramp, 0x10000, 7)) die("--skip-faulting-init: no room for the trampolines");
    uint32_t *t = (uint32_t *)(uintptr_t)tramp;
    unsigned sites = 0, nsym = symtab->size / sizeof(Y32);
    for (unsigned k = 0; k < nsym; k++) {
        const char *name = str + sym[k].name;
        if ((sym[k].info & 0xF) != 2 /* STT_FUNC */ || !sym[k].shndx) continue;
        if (streq(name, "fnvnx_vslot_trap")) g_trap_vslot = sym[k].value;
        if (streq(name, "fnvnx_special_trap")) g_trap_special = sym[k].value;
        int hit = 0;
        for (unsigned r = 0; r < sizeof runners / sizeof runners[0]; r++) hit |= streq(name, runners[r]);
        if (!hit) continue;
        u64 a = sym[k].value & ~3ull, end = a + (sym[k].size ? sym[k].size : 0x400);
        if (end > a + 0x2000) end = a + 0x2000;
        for (; a < end && a + 4 <= g_hi; a += 4) {
            uint32_t *w = (uint32_t *)(uintptr_t)a;
            if ((*w & 0xFFFFFC1Fu) != 0xD63F0000u) continue;   /* blr xN */
            if (sites == 0x10000 / 24) die("--skip-faulting-init: too many sites");
            uint32_t n = (*w >> 5) & 31;
            uint32_t *tk = t + sites * 6;   /* 24 bytes, the literal 8-aligned */
            tk[0] = 0xAA0003F0u | (n << 16);   /* mov x16, xN */
            tk[1] = 0x58000071u;               /* ldr x17, tk+16 */
            tk[2] = 0xD61F0220u;               /* br x17 */
            tk[3] = 0xD503201Fu;               /* nop */
            *(u64 *)(tk + 4) = (u64)(uintptr_t)linhost_guard_enter;
            long d = ((long)(uintptr_t)tk - (long)a) / 4;
            if (d >= (1 << 25) || d < -(1 << 25)) die("--skip-faulting-init: trampoline out of bl range");
            *w = 0x94000000u | ((uint32_t)d & 0x3FFFFFFu);   /* bl tk */
            sites++;
        }
    }
    sc(SYS_munmap, (long)sym, symtab->size, 0, 0, 0, 0);
    sc(SYS_munmap, (long)str, strtab->size + 1, 0, 0, 0, 0);
    sc(SYS_munmap, (long)sh, (long)e.shnum * sizeof(S32), 0, 0, 0, 0);
    puts_(2, "linhost: guarding "); put_dec(2, sites); puts_(2, " indirect call sites of the initializer runners\n");
}

/* --frame: patch each named function's first instruction into a branch to a per-site trampoline (one page above the
 * guards' page): ldr x16, =continuation; ldr x17, =linhost_frame_stub; br x17; <displaced>; b <function + 4> */
static void sync_icache(u64 a, u64 n) {   /* no libgcc: dc cvau / ic ivau per 64-byte line */
    for (u64 x = a & ~63ull; x < a + n; x += 64) __asm__ volatile("dc cvau, %0" :: "r"(x) : "memory");
    __asm__ volatile("dsb ish" ::: "memory");
    for (u64 x = a & ~63ull; x < a + n; x += 64) __asm__ volatile("ic ivau, %0" :: "r"(x) : "memory");
    __asm__ volatile("dsb ish\n isb" ::: "memory");
}
static int pcrel(uint32_t w) {
    return (w & 0x1F000000u) == 0x10000000u || (w & 0x7C000000u) == 0x14000000u || (w & 0xFF000010u) == 0x54000000u ||
           (w & 0x7E000000u) == 0x34000000u || (w & 0x7E000000u) == 0x36000000u || (w & 0x3B000000u) == 0x18000000u;
}
/* one hook site, 48 bytes: movz w15, #id; ldr x16, =continuation; ldr x17, =stub; br x17; <displaced>;
 * b <function + 4>; nop nop; the literals at +32 and +40. id: -1 a frame mark, else a --hook milestone */
static int g_nhooks;
static int patch_site(uint32_t *w, uint32_t *t, int *sites, int id) {
    if (*sites == 0x10000 / 48) die("--frame/--hook: too many functions");
    uint32_t *tk = t + *sites * 12;
    uint32_t sid = id < 0 ? 0xFFFFu : (uint32_t)id;
    tk[0] = 0x52800000u | (sid << 5) | 15;   /* movz w15, #sid */
    tk[1] = 0x580000F0u;                     /* ldr x16, tk+32 (from tk+4: 28) */
    tk[2] = 0x58000111u;                     /* ldr x17, tk+40 (from tk+8: 32) */
    tk[3] = 0xD61F0220u;                     /* br x17 */
    tk[4] = *w;                              /* the displaced instruction */
    long back = ((long)(uintptr_t)(w + 1) - (long)(uintptr_t)(tk + 5)) / 4;
    long go = ((long)(uintptr_t)tk - (long)(uintptr_t)w) / 4;
    if (back >= (1 << 25) || back < -(1 << 25) || go >= (1 << 25) || go < -(1 << 25)) die("--frame/--hook: out of b range");
    tk[5] = 0x14000000u | ((uint32_t)back & 0x3FFFFFFu);   /* b function + 4 */
    tk[6] = tk[7] = 0xD503201Fu;
    *(u64 *)(tk + 8) = (u64)(uintptr_t)(tk + 4);
    *(u64 *)(tk + 10) = (u64)(uintptr_t)linhost_frame_stub;
    *w = 0x14000000u | ((uint32_t)go & 0x3FFFFFFu);          /* b tk */
    sync_icache((u64)(uintptr_t)w, 4);
    (*sites)++;
    return 1;
}
static uint32_t *g_hook_page;
static int g_hook_sites;
static void install_hooks(const u64 *addrs, int n) {   /* --hook 0xADDR NAME: by address (linfeed.py resolves them) */
    for (int i = 0; i < n; i++) {
        uint32_t *w = (uint32_t *)(uintptr_t)(addrs[i] & ~3ull);
        if (addrs[i] < g_lo || addrs[i] + 4 > g_hi || pcrel(*w)) {
            puts_(2, "linhost: --hook: "); puts_(2, g_hook_name[i]); puts_(2, " at "); put_hex(2, addrs[i]);
            puts_(2, ": not patched (outside the guest or PC-relative)\n");
            continue;
        }
        g_hook_at[i] = addrs[i] & ~3ull;
        patch_site(w, g_hook_page, &g_hook_sites, i);
    }
    sync_icache((u64)(uintptr_t)g_hook_page, (u64)g_hook_sites * 48);
}
static void install_frames(long fd, const char *const *names, int nnames) {
    E32 e;
    sc(SYS_pread64, fd, (long)&e, sizeof e, 0, 0, 0);
    S32 *sh = anon((u64)e.shnum * sizeof(S32));
    sc(SYS_pread64, fd, (long)sh, (long)e.shnum * sizeof(S32), e.shoff, 0, 0);
    S32 *symtab = 0;
    for (unsigned i = 0; i < e.shnum; i++) if (sh[i].type == 2) symtab = &sh[i];
    if (!symtab) die("--frame: the guest has no .symtab");
    S32 *strtab = &sh[symtab->link];
    Y32 *sym = anon(symtab->size);
    char *str = anon(strtab->size + 1);
    sc(SYS_pread64, fd, (long)sym, symtab->size, symtab->offset, 0, 0);
    sc(SYS_pread64, fd, (long)str, strtab->size, strtab->offset, 0, 0);
    uint32_t *t = g_hook_page;
    int sites = g_hook_sites, found = 0;
    for (unsigned k = 0, nsym = symtab->size / sizeof(Y32); k < nsym; k++) {
        if ((sym[k].info & 0xF) != 2 || !sym[k].shndx) continue;
        int hit = 0;
        for (int r = 0; r < nnames; r++) hit |= streq(str + sym[k].name, names[r]);
        if (!hit) continue;
        uint32_t *w = (uint32_t *)(uintptr_t)(sym[k].value & ~3u);
        int shared = 0;
        for (int i = 0; i < g_nhooks; i++)
            if (g_hook_at[i] == (u64)(uintptr_t)w) { g_hook_frame[i] = 1; shared = 1; }
        if (shared) { puts_(2, "linhost: --frame: "); puts_(2, str + sym[k].name); puts_(2, " shares the feed's hook\n");
                      found++; continue; }
        if (pcrel(*w)) { puts_(2, "linhost: --frame: "); puts_(2, str + sym[k].name);
                         puts_(2, " starts with a PC-relative instruction: not patched\n"); continue; }
        if (!patch_site(w, t, &sites, -1)) continue;
        puts_(2, "linhost: --frame: "); puts_(2, str + sym[k].name); puts_(2, " at "); put_hex(2, sym[k].value); puts_(2, "\n");
    }
    sync_icache((u64)(uintptr_t)t, (u64)sites * 48);
    if (sites == g_hook_sites && !found) puts_(2, "linhost: --frame: no named function found: no frame marks\n");
    g_hook_sites = sites;
    sc(SYS_munmap, (long)sym, symtab->size, 0, 0, 0, 0);
    sc(SYS_munmap, (long)str, strtab->size + 1, 0, 0, 0, 0);
    sc(SYS_munmap, (long)sh, (long)e.shnum * sizeof(S32), 0, 0, 0, 0);
}

/* ---- --feed: the scenario steps (format: tools/perf/linfeed.py) */
#define FEED_MAX 4096
#define FEED_KEYS 8
typedef struct { char op; int ref; u64 ns; const char *str; int nk; uint16_t usage[FEED_KEYS]; uint8_t down[FEED_KEYS]; } FeedStep;
static FeedStep *g_feed;
static int g_nfeed, g_fk = -1;
static u64 g_ft_step, g_ft0;
static int g_feed_lock, g_feed_done, g_feed_seen = -1;
static struct { u64 due; uint16_t usage; uint8_t down; } g_fq[512];
static unsigned g_fq_head, g_fq_tail;
static u64 g_feed_sent;
static u64 h_ext_hex(const char *s) {
    u64 v = 0;
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) s += 2;
    for (;; s++) {
        int d = *s >= '0' && *s <= '9' ? *s - '0' : *s >= 'a' && *s <= 'f' ? *s - 'a' + 10 : *s >= 'A' && *s <= 'F' ? *s - 'A' + 10 : -1;
        if (d < 0) return v;
        v = v * 16 + (u64)d;
    }
}
static u64 feed_dec(const char *s) { u64 v = 0; while (*s >= '0' && *s <= '9') v = v * 10 + (u64)(*s++ - '0'); return v; }
static char *feed_tok(char **p) {   /* next space-separated token; the rest of the line with **p == 0 */
    while (**p == ' ') (*p)++;
    char *t = *p;
    while (**p && **p != ' ') (*p)++;
    if (**p) *(*p)++ = 0;
    return t;
}
static void hook_mark(uint32_t id) {
    if (id < HOOK_MAX && !__atomic_exchange_n(&g_hook_hit[id], 1, __ATOMIC_ACQ_REL)) progress("milestone");
}
static void feed_log(const char *what, const char *a, u64 n) {
    puts_(2, "linhost: scenario "); puts_(2, what);
    if (a) { puts_(2, " "); puts_(2, a); }
    puts_(2, " t "); put_dec(2, (now_ns() - g_ft0) / 1000000ull); puts_(2, " ms");
    if (n) { puts_(2, " "); put_dec(2, n); }
    puts_(2, "\n");
}
static void feed_load(const char *path, u64 *hook_addr) {
    long fd = sc(SYS_openat, AT_FDCWD, (long)path, 0, 0, 0, 0);
    if (fd < 0) die("--feed: cannot open the file");
    u64 cap = 1 << 20;
    char *b = anon(cap + 1);
    long n = 0, r;
    while (n < (long)cap && (r = sc(SYS_read, fd, (long)(b + n), (long)cap - n, 0, 0, 0)) > 0) n += r;
    sc(SYS_close, fd, 0, 0, 0, 0, 0);
    b[n] = 0;
    g_feed = anon(sizeof(FeedStep) * FEED_MAX);
    int line = 0;
    for (char *p = b; *p;) {
        char *e = p;
        while (*e && *e != '\n') e++;
        int more = *e != 0;
        *e = 0;
        char *q = p;
        p = more ? e + 1 : e;
        if (line++ == 0) { if (!streq(q, "fnvnx-linfeed/1")) die("--feed: not a fnvnx-linfeed/1 file"); continue; }
        char *op = feed_tok(&q);
        if (!*op || *op == '#') continue;
        if (streq(op, "hook")) {
            char *a = feed_tok(&q);
            if (g_nhooks == HOOK_MAX) die("--feed: too many hooks");
            hook_addr[g_nhooks] = h_ext_hex(a);
            g_hook_name[g_nhooks++] = q;
            continue;
        }
        if (g_nfeed == FEED_MAX) die("--feed: too many steps");
        FeedStep *st = &g_feed[g_nfeed++];
        memset(st, 0, sizeof *st);
        st->ref = -1;
        if (streq(op, "press")) {
            st->op = 'p';
            st->ns = feed_dec(feed_tok(&q));
            for (char *k; *(k = feed_tok(&q)) && st->nk < FEED_KEYS;) {
                char *c = k;
                while (*c && *c != ':') c++;
                st->usage[st->nk] = (uint16_t)feed_dec(k);
                st->down[st->nk++] = *c == ':' && c[1] == '1';
            }
            st->str = "";
        } else if (streq(op, "wait")) {
            st->op = 'w';
            st->ns = feed_dec(feed_tok(&q));
        } else if (streq(op, "until-hook") || streq(op, "until-file") || streq(op, "until-none")) {
            st->op = op[6] == 'h' ? 'h' : op[6] == 'f' ? 'f' : 'n';
            st->ns = feed_dec(feed_tok(&q));
            st->str = q;
            if (st->op == 'h') {
                for (int i = 0; i < g_nhooks; i++) if (streq(g_hook_name[i], q)) st->ref = i;
                if (st->ref < 0) st->op = 'n';   /* a milestone with no hook: only its timeout */
            }
        } else if (streq(op, "mark")) {
            st->op = 'm';
            st->str = q;
        } else {
            g_nfeed--;
        }
    }
    puts_(2, "linhost: --feed: "); put_dec(2, (u64)g_nfeed); puts_(2, " steps, "); put_dec(2, (u64)g_nhooks);
    puts_(2, " hooks\n");
}
/* a file under the SD root at any depth whose last components match rel, case-insensitively */
static int ci_eq(const char *a, const char *b) {
    for (; *a && *b; a++, b++) {
        char x = *a >= 'A' && *a <= 'Z' ? *a + 32 : *a, y = *b >= 'A' && *b <= 'Z' ? *b + 32 : *b;
        if (x != y) return 0;
    }
    return *a == *b;
}
static int file_walk(const char *dir, const char *const *want, int nwant, const char **stack, int depth) {
    long fd = sc(SYS_openat, AT_FDCWD, (long)dir, O_DIRECTORY, 0, 0, 0);
    if (fd < 0) return 0;
    static char bufs[8][4096];
    char *buf = bufs[depth & 7];
    int found = 0;
    for (long n; !found && (n = sc(SYS_getdents64, fd, (long)buf, 4096, 0, 0, 0)) > 0;)
        for (long o = 0; o < n && !found;) {
            uint16_t rl = *(uint16_t *)(buf + o + 16);
            unsigned char ty = *(unsigned char *)(buf + o + 18);
            const char *nm = buf + o + 19;
            o += rl;
            if (nm[0] == '.' && (!nm[1] || (nm[1] == '.' && !nm[2]))) continue;
            stack[depth] = nm;
            if (ty != 4) {   /* not a directory: compare the last components */
                if (depth + 1 >= nwant) {
                    int ok = 1;
                    for (int k = 0; k < nwant && ok; k++) ok = ci_eq(stack[depth + 1 - nwant + k], want[k]);
                    found = ok;
                }
            } else if (depth < 7) {
                char sub[1024];
                size_t a = slen(dir), b = slen(nm);
                if (a + b + 2 < sizeof sub) {
                    memcpy(sub, dir, a); sub[a] = '/'; memcpy(sub + a + 1, nm, b + 1);
                    found = file_walk(sub, want, nwant, stack, depth + 1);
                }
            }
        }
    sc(SYS_close, fd, 0, 0, 0, 0, 0);
    return found;
}
static int feed_has_file(const char *rel) {
    if (!g_root) return 0;
    char copy[512];
    size_t n = slen(rel);
    if (n >= sizeof copy) return 0;
    memcpy(copy, rel, n + 1);
    const char *want[16];
    int nw = 0;
    for (char *p = copy; *p && nw < 16;) {
        while (*p == '/' || *p == '\\') *p++ = 0;
        if (!*p) break;
        want[nw++] = p;
        while (*p && *p != '/' && *p != '\\') p++;
    }
    const char *stack[8];
    return nw && file_walk(g_root, want, nw, stack, 0);
}
static void feed_tick(void) {
    if (!g_feed || g_feed_done) return;
    if (__atomic_exchange_n(&g_feed_lock, 1, __ATOMIC_ACQUIRE)) return;   /* another thread is at it */
    u64 now = now_ns();
    if (g_fk < 0) { g_fk = 0; g_ft_step = g_ft0 = now; }
    while (g_fk < g_nfeed) {
        FeedStep *st = &g_feed[g_fk];
        if (now < g_ft_step) break;
        if (st->op == 'p') {
            for (int i = 0; i < st->nk; i++) {
                unsigned slot = g_fq_tail % 512;
                if (g_fq_tail - g_fq_head >= 512) break;
                g_fq[slot].due = st->down[i] ? now : now + st->ns / 2;
                g_fq[slot].usage = st->usage[i];
                g_fq[slot].down = st->down[i];
                g_fq_tail++;
            }
            g_feed_sent++;
            g_fk++;
            g_ft_step = now + st->ns;
            if (st->ns) break;
            continue;
        }
        if (st->op == 'w') {
            u64 due = g_ft_step + st->ns;
            if (now < due) break;
            g_fk++; g_ft_step = due;
            continue;
        }
        if (st->op == 'h' || st->op == 'f' || st->op == 'n') {
            int ok = 0;
            if (st->op == 'h')   /* any hook of that name (a milestone can have several ways) */
                for (int i = 0; i < g_nhooks && !ok; i++)
                    ok = __atomic_load_n(&g_hook_hit[i], __ATOMIC_ACQUIRE) && streq(g_hook_name[i], st->str);
            else if (st->op == 'f')
                ok = feed_has_file(st->str);
            if (ok) feed_log("until ok", st->str, 0);
            else if (now >= g_ft_step + st->ns)
                feed_log(st->op == 'n' ? "until unsupported (timeout)" : "until TIMEOUT", st->str, st->ns / 1000000000ull);
            else break;
            g_fk++; g_ft_step = now;
            continue;
        }
        if (st->op == 'm') { feed_log("mark", st->str, 0); g_fk++; continue; }
        g_fk++;
    }
    if (g_fk != g_feed_seen) { g_feed_seen = g_fk; progress("scenario step"); }
    int end = g_fk >= g_nfeed && g_fq_head == g_fq_tail;
    __atomic_store_n(&g_feed_lock, 0, __ATOMIC_RELEASE);
    if (end && !__atomic_exchange_n(&g_feed_done, 1, __ATOMIC_ACQ_REL)) {
        feed_log("complete", 0, g_feed_sent);
        prof_flush_all();
        sc(SYS_exit_group, 0, 0, 0, 0, 0, 0);
    }
}
static void feed_incomplete(void) {   /* the guest ends before the plan: say where */
    if (!g_feed || g_feed_done || g_fk >= g_nfeed) return;
    feed_log("incomplete at step", 0, (u64)(g_fk < 0 ? 0 : g_fk) + 1);
}
/* input_poll(FnvnxInputEvent *buf, cap): the due key events */
static u64 h_input_poll(u64 buf, u64 cap) {
    feed_tick();
    if (!g_feed || !buf || !cap) return 0;
    if (__atomic_exchange_n(&g_feed_lock, 1, __ATOMIC_ACQUIRE)) return 0;
    u64 now = now_ns(), n = 0;
    uint32_t *ev = (uint32_t *)(uintptr_t)buf;
    while (n < cap && n < 64 && g_fq_head != g_fq_tail && g_fq[g_fq_head % 512].due <= now) {
        unsigned s2 = g_fq_head % 512;
        uint32_t *e = ev + n * 6;
        e[0] = FNVNX_EV_KEY; e[1] = g_fq[s2].usage; e[2] = g_fq[s2].down; e[3] = 0; e[4] = 0; e[5] = 0;
        g_fq_head++;
        n++;
    }
    __atomic_store_n(&g_feed_lock, 0, __ATOMIC_RELEASE);
    return n;
}

/* ---- --calls FILE ("call 0xADDR NAME label=xN|label=*xN+OFF ...", tools/perf/callwatch.py resolve) */
static void calls_load(const char *path, u64 *hook_addr) {
    long fd = sc(SYS_openat, AT_FDCWD, (long)path, 0, 0, 0, 0);
    if (fd < 0) die("--calls: cannot open the file");
    u64 cap = 1 << 18;
    char *b = anon(cap + 1);
    long n = 0, r;
    while (n < (long)cap && (r = sc(SYS_read, fd, (long)(b + n), (long)cap - n, 0, 0, 0)) > 0) n += r;
    sc(SYS_close, fd, 0, 0, 0, 0, 0);
    b[n] = 0;
    int calls = 0;
    for (char *p = b; *p;) {
        char *e = p;
        while (*e && *e != '\n') e++;
        int more = *e != 0;
        *e = 0;
        char *q = p;
        p = more ? e + 1 : e;
        char *op = feed_tok(&q);
        if (!streq(op, "call")) continue;
        if (g_nhooks == HOOK_MAX) die("--calls: too many hooks");
        int id = g_nhooks++;
        hook_addr[id] = h_ext_hex(feed_tok(&q));
        g_hook_name[id] = feed_tok(&q);
        g_hook_call[id] = 1;
        for (char *t; *(t = feed_tok(&q)) && g_hook_nspec[id] < 4;) {
            char *eq = t;
            while (*eq && *eq != '=') eq++;
            if (!*eq) continue;
            *eq++ = 0;
            CallSpec *c = &g_hook_spec[id][g_hook_nspec[id]];
            c->label = t;
            c->deref = *eq == '*';
            if (c->deref) eq++;
            if (*eq != 'x') continue;
            c->reg = (uint8_t)feed_dec(eq + 1);
            while (*eq && *eq != '+') eq++;
            c->off = *eq == '+' ? (uint32_t)h_ext_hex(eq + 1) : 0;
            if (c->reg <= 8) g_hook_nspec[id]++;
        }
        calls++;
    }
    puts_(2, "linhost: --calls: "); put_dec(2, (u64)calls); puts_(2, " functions watched\n");
}

/* ---- --progress-timeout: the monitor thread and the per-thread dump */
static int g_main_tid;
static volatile int g_dump_ack;
static void on_dump(int sig, void *info, void *ucv) {   /* SIGURG: this thread's stack */
    (void)sig; (void)info;
    unsigned char *uc = ucv;
    u64 *x = (u64 *)(uc + 184), pc = *(u64 *)(uc + 440);
    char line[600];
    int o = 0;
    #define LAPP(s) do { const char *s_ = (s); while (*s_ && o < (int)sizeof line - 2) line[o++] = *s_++; } while (0)
    #define LHEX(v) do { char h_[19]; u64 v_ = (v); h_[0] = '0'; h_[1] = 'x'; for (int i_ = 0; i_ < 16; i_++) h_[2 + i_] = "0123456789abcdef"[(v_ >> (60 - 4 * i_)) & 15]; h_[18] = 0; LAPP(h_); } while (0)
    char tid[16]; int t = gettid_(), n = 0; char r[12];
    do r[n++] = (char)('0' + t % 10); while (t /= 10);
    int k = 0; while (n) tid[k++] = r[--n]; tid[k] = 0;
    LAPP("linhost: hang thread "); LAPP(tid); LAPP(" pc "); LHEX(pc); LAPP(" lr "); LHEX(x[30]); LAPP(" frames");
    u64 fp = x[29];
    for (int i = 0; i < 16 && readable(fp) && !(fp & 7); i++) {
        u64 *f = (u64 *)(uintptr_t)fp;
        LAPP(" "); LHEX(f[1]);
        if (f[0] <= fp) break;
        fp = f[0];
    }
    line[o++] = '\n';
    sc(SYS_write, 2, (long)line, o, 0, 0, 0);
    #undef LAPP
    #undef LHEX
    __atomic_add_fetch(&g_dump_ack, 1, __ATOMIC_RELEASE);
}
static void dump_one(long pid, int tid) {
    int before = __atomic_load_n(&g_dump_ack, __ATOMIC_ACQUIRE);
    if (sc(131 /* tgkill */, pid, tid, 23 /* SIGURG */, 0, 0, 0) < 0) return;
    for (int i = 0; i < 40 && __atomic_load_n(&g_dump_ack, __ATOMIC_ACQUIRE) == before; i++) {
        struct ts t = { 0, 10000000 };
        sc(SYS_nanosleep, (long)&t, 0, 0, 0, 0, 0);
    }
}
static unsigned char g_mon_stack[64 * 1024] __attribute__((aligned(16)));
static void monitor_main(void *arg) {
    (void)arg;
    for (;;) {
        struct ts t = { 1, 0 };
        sc(SYS_nanosleep, (long)&t, 0, 0, 0, 0, 0);
        u64 now = now_ns(), last = __atomic_load_n(&g_progress_ns, __ATOMIC_RELAXED);
        if (now - last < g_progress_s * 1000000000ull) continue;
        puts_(2, "\nlinhost: hang: no progress for "); put_dec(2, g_progress_s); puts_(2, " s (last: ");
        puts_(2, g_progress_what); puts_(2, " at "); put_dec(2, (last - g_t0) / 1000000ull); puts_(2, " ms)\n");
        long pid = sc(172 /* getpid */, 0, 0, 0, 0, 0, 0);
        dump_one(pid, g_main_tid);
        for (int i = 0; i < MAXT; i++)
            if (g_t[i].started && g_t[i].ktid && __atomic_load_n(&g_t[i].tid, __ATOMIC_ACQUIRE) != 0)
                dump_one(pid, g_t[i].ktid);
        feed_incomplete();
        prof_flush_all();
        sc(SYS_exit_group, 4, 0, 0, 0, 0, 0);
    }
}
static void start_monitor(void) {
    struct { u64 handler, flags, restorer, mask; } sa = {
        (u64)(uintptr_t)on_dump, 4 /* SA_SIGINFO */ | 0x10000000 /* SA_RESTART */ | 0x04000000 /* SA_RESTORER */,
        (u64)(uintptr_t)linhost_sigreturn, 0 };
    sc(SYS_rt_sigaction, 23, (long)&sa, 0, 8, 0, 0);
    if (g_prof_fd < 0) {   /* the CPU timers' handler, without a profile file */
        struct { u64 handler, flags, restorer, mask; } sp = {
            (u64)(uintptr_t)on_prof, 4 | 0x10000000 | 0x04000000, (u64)(uintptr_t)linhost_sigreturn, 0 };
        sc(SYS_rt_sigaction, 27, (long)&sp, 0, 8, 0, 0);
    }
    progress("start");
    unsigned long fl = 0x100 | 0x200 | 0x400 | 0x800 | 0x10000 | 0x40000;   /* VM FS FILES SIGHAND THREAD SYSVSEM */
    if (linhost_clone(fl, g_mon_stack + sizeof g_mon_stack, 0, 0, 0, monitor_main, 0) < 0)
        die("--progress-timeout: no monitor thread");
}

static u64 parse_u(const char *s) { u64 v = 0; while (*s >= '0' && *s <= '9') v = v * 10 + (u64)(*s++ - '0'); return v; }

__attribute__((used)) long hmain(long *sp);
__attribute__((used)) long hmain(long *sp) {
    long argc = sp[0];
    char **argv = (char **)(sp + 1);
    g_t0 = now_ns();
    const char *guest = 0;
    int skip_init = 0;
    u64 heap_mib = 64;
    const char *prof_path = 0, *frames[16], *feed_path = 0, *calls_path = 0;
    int nframes = 0;
    for (long i = 1; i < argc; i++) {
        if (streq(argv[i], "--root") && i + 1 < argc) g_root = argv[++i];
        else if (streq(argv[i], "--expect") && i + 1 < argc) g_expect = argv[++i];
        else if (streq(argv[i], "--heap") && i + 1 < argc) heap_mib = parse_u(argv[++i]);
        else if (streq(argv[i], "--skip-faulting-init")) skip_init = 1;
        else if (streq(argv[i], "--collect-traps")) g_collect = 1;
        else if (streq(argv[i], "--profile") && i + 1 < argc) prof_path = argv[++i];
        else if (streq(argv[i], "--profile-hz") && i + 1 < argc) g_prof_hz = parse_u(argv[++i]);
        else if (streq(argv[i], "--frame") && i + 1 < argc && nframes < 16) frames[nframes++] = argv[++i];
        else if (streq(argv[i], "--feed") && i + 1 < argc) feed_path = argv[++i];
        else if (streq(argv[i], "--progress-timeout") && i + 1 < argc) g_progress_s = parse_u(argv[++i]);
        else if (streq(argv[i], "--calls") && i + 1 < argc) calls_path = argv[++i];
        else if (!guest && argv[i][0] != '-') guest = argv[i];
        else die("usage: linhost [--root DIR] [--expect TEXT] [--skip-faulting-init] [--collect-traps] [--heap MiB] [--profile FILE [--profile-hz N] [--frame SYM]...] [--feed FILE] [--progress-timeout S] [--calls FILE] guest.elf");
    }
    if (!guest) die("usage: linhost [--root DIR] [--expect TEXT] [--skip-faulting-init] [--collect-traps] [--heap MiB] [--profile FILE [--profile-hz N] [--frame SYM]...] [--feed FILE] [--progress-timeout S] [--calls FILE] guest.elf");
    if (g_expect) {
        g_expect_len = slen(g_expect);
        if (!g_expect_len || g_expect_len >= sizeof g_win / 2) die("--expect: 1..255 bytes");
    }
    if ((uintptr_t)&g_api >> 32 || (uintptr_t)hmain >> 32) die("host is not below 4 GiB");
    g_api.magic = FNVNX_HOSTAPI_MAGIC; g_api.version = FNVNX_HOSTAPI_VERSION; g_api.size = sizeof g_api;
#define F(slot, fn) g_api.slot = (u64)(uintptr_t)(fn)
    F(output_debug_string, h_output_debug_string); F(sleep_ns, h_sleep_ns); F(exit, h_exit);
    F(console_write, h_console_write); F(console_flush, h_zero); F(main_loop, h_one); F(buttons_down, h_zero);
    F(system_tick, h_tick); F(system_tick_freq, h_tick_freq);
    F(thread_create, h_thread_create); F(thread_start, h_thread_start); F(thread_join, h_thread_join);
    F(thread_free, h_thread_free); F(thread_exit, h_thread_exit); F(thread_yield, h_thread_yield);
    F(thread_set_priority, h_thread_set_priority); F(thread_set_cpu, h_thread_set_cpu);
    F(wait_on_address, h_wait_on_address); F(wake_address, h_wake_address);
    F(wall_time_us, h_wall_time_us); F(tz_offset_minutes, h_zero); F(random64, h_random); F(memory_info, h_meminfo);
    F(cpu_count, h_cpu_count);
    u64 *slot = &g_api.fs_open;
    for (; slot <= &g_api.ext_call; slot++) *slot = (u64)(uintptr_t)h_enosys;
    if (g_root) {
        F(fs_open, h_fs_open); F(fs_read, h_fs_read); F(fs_write, h_fs_write); F(fs_pread, h_fs_pread);
        F(fs_pwrite, h_fs_pwrite); F(fs_seek, h_fs_seek); F(fs_truncate, h_fs_truncate); F(fs_sync, h_fs_sync);
        F(fs_fstat, h_fs_fstat); F(fs_close, h_fs_close); F(fs_stat, h_fs_stat); F(fs_mkdir, h_fs_mkdir);
        F(fs_rmdir, h_fs_rmdir); F(fs_remove, h_fs_remove); F(fs_rename, h_fs_rename); F(fs_dir_open, h_fs_dir_open);
        F(fs_dir_next, h_fs_dir_next); F(fs_dir_close, h_fs_dir_close);
    }
    F(audio_open, h_audio_open); F(audio_submit, h_audio_submit); F(audio_close, h_zero); F(pad_read, h_zero);
    F(pad_vibrate, h_zero); F(input_poll, h_input_poll); F(map_fixed, h_map_fixed); F(ext_call, h_ext_call);
    long fd;
    u64 entry = load(guest, &fd);
    g_api.guest_base = g_lo;
    g_api.guest_size = g_hi - g_lo;
    if (heap_mib) {   /* the guest heap: the first free 64 MiB-aligned range above the guest (and its trampolines) */
        u64 size = heap_mib << 20, base = (g_hi + 0x10000 + 0x3FFFFFFull) & ~0x3FFFFFFull;
        for (; base + size <= 0xC0000000ull; base += 0x4000000ull)
            if (!map_at(base, size, PROT_RW)) break;
        if (base + size > 0xC0000000ull) die("no room for the guest heap");
        g_api.heap_base = base;
        g_api.heap_size = size;
        puts_(2, "linhost: guest heap "); put_hex(2, base); puts_(2, " ("); put_dec(2, heap_mib); puts_(2, " MiB)\n");
    }
    if (g_collect && !skip_init) die("--collect-traps needs --skip-faulting-init (the trap symbols are found there)");
    if (skip_init) install_guards(fd);
    if (nframes || feed_path || calls_path) {   /* the hook trampolines: one page above the guards' page */
        u64 page = ((g_hi + 0xFFFF) & ~0xFFFFull) + 0x10000;
        if (map_at(page, 0x10000, 7)) die("--frame/--feed: no room for the trampolines");
        g_hook_page = (uint32_t *)(uintptr_t)page;
    }
    if (feed_path || calls_path) {
        static u64 hook_addr[HOOK_MAX];
        if (feed_path) feed_load(feed_path, hook_addr);
        if (calls_path) calls_load(calls_path, hook_addr);
        install_hooks(hook_addr, g_nhooks);
    }
    if (nframes) install_frames(fd, frames, nframes);
    if (prof_path) {
        if (!g_prof_hz || g_prof_hz > 100000) die("--profile-hz: 1..100000");
        prof_open(prof_path);
    }
    sc(SYS_close, fd, 0, 0, 0, 0, 0);
    install_fault_handler();
    set_affinity(0, 0x1);   /* the main thread: placement row "main", core 0 */
    g_t_entry = now_ns();
    g_main_tid = gettid_();
    if (g_progress_s) start_monitor();
    prof_thread_begin(-1, 0x1);   /* the main thread: slot -1 */
    u64 r = ((u64 (*)(u64))(uintptr_t)entry)((u64)(uintptr_t)&g_api);
    prof_flush_all();
    if (g_expect) {
        puts_(2, "\nlinhost: the guest returned "); put_hex(2, r); puts_(2, " before \""); puts_(2, g_expect);
        puts_(2, "\"\n");
        sc(SYS_exit_group, 3, 0, 0, 0, 0, 0);
    }
    feed_incomplete();
    sc(SYS_exit_group, (long)r, 0, 0, 0, 0, 0);
    return 0;
}

__asm__(".text\n.global _start\n_start:\n  mov x0, sp\n  bl hmain\n  b .\n");
