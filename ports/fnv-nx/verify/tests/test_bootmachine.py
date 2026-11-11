#!/usr/bin/env python3
"""Tests of verify/bootmachine.py + boothost.py (the boot stage machine). Plain script: exit 0 on pass, one line per case.
Builds small ILP32 PIE guests with aarch64-linux-gnu-gcc (-mabi=ilp32) from the fixtures in tests/guest/ (crt0.s with the
FNXG header, guest.ld rebased to 0 with its two base asserts already removed, hostapi.h); the runtime part is the test's own
freestanding entry (the real runtime.c needs devkitA64's newlib: sys/iosupport.h). The guest relocates itself, as runtime.c
does. Scratch: tempfile.mkdtemp, removed at exit."""
import atexit
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
GUEST_DIR = os.path.join(HERE, "guest")
sys.path.insert(0, VERIFY)
import bootlayout  # noqa: E402
import boothost  # noqa: E402
import bootmachine  # noqa: E402

TMP = tempfile.mkdtemp(prefix="bm-")
atexit.register(shutil.rmtree, TMP, ignore_errors=True)
FAILED, N = [], [0]
CC = "aarch64-linux-gnu-gcc"
CFLAGS = ["-mabi=ilp32", "-march=armv8-a", "-O2", "-fPIE", "-ffreestanding", "-fno-builtin", "-fno-stack-protector", "-fvisibility=hidden",
          "-mno-outline-atomics", "-fno-asynchronous-unwind-tables", "-fno-tree-loop-distribute-patterns", "-Wall",
          "-I" + GUEST_DIR]
LDFLAGS = ["-m", "aarch64elf32", "-pie", "--no-dynamic-linker", "-z", "text", "-z", "now", "-z", "pack-relative-relocs",
           "--gc-sections", "-z", "max-page-size=0x1000", "--defsym=FNVNX_GUEST_BASE=0",
           "--require-defined=__fnvnx_guest_start"]

GUEST = r'''
#include <stdint.h>
#include "hostapi.h"
const FnvnxHostApi *fnvnx_host;
#define H (fnvnx_host)
typedef struct { int32_t d_tag; uint32_t d_val; } Dyn;
typedef struct { uint32_t o, i; int32_t a; } Rela;
extern char __fnvnx_guest_start[] __attribute__((visibility("hidden")));   /* no GOT before relocation */
static const char *const MSG[] = {"hello from the guest\n", "second line\n"};   /* needs RELATIVE relocations */
static volatile uint32_t flag;
static uint64_t H2(uint64_t f, uint64_t a, uint64_t b) { return fnvnx_hcall2(f, a, b); }
static uint32_t slen(const char *s) { uint32_t n = 0; while (s[n]) n++; return n; }
static void say(const char *s) { H2(H->console_write, FNVNX_ADDR(s), slen(s)); }
static uint32_t mv(uint32_t v, uint32_t slide, uint32_t size) { return v < size ? v + slide : v; }
static void relocate(uint32_t base) {
    const uint32_t *hdr = (const uint32_t *)(uintptr_t)base;
    const char *mod0 = (const char *)(uintptr_t)base + hdr[1];
    const Dyn *d = (const Dyn *)(mod0 + ((const int32_t *)mod0)[1]);
    uint32_t rela = 0, relasz = 0, relr = 0, relrsz = 0, size = hdr[5];
    for (; d->d_tag; d++) {
        if (d->d_tag == 7) rela = d->d_val + base; else if (d->d_tag == 8) relasz = d->d_val;
        else if (d->d_tag == 36) relr = d->d_val + base; else if (d->d_tag == 35) relrsz = d->d_val;
    }
    for (uint32_t i = 0; i < relasz / 12; i++) {
        const Rela *r = (const Rela *)(uintptr_t)rela + i;
        if ((r->i & 0xFF) == 183) *(uint32_t *)(uintptr_t)(r->o + base) = mv((uint32_t)r->a, base, size);
    }
    uint32_t *w = 0;
    for (uint32_t i = 0; i < relrsz / 4; i++) {
        uint32_t e = ((const uint32_t *)(uintptr_t)relr)[i];
        if (!(e & 1)) { w = (uint32_t *)(uintptr_t)(e + base); *w += base; w++; }
        else { for (uint32_t b = e >> 1, k = 0; b; b >>= 1, k++) if (b & 1) w[k] += base; w += 31; }
    }
}
__attribute__((noinline)) uint32_t marker(uint32_t x) { __asm__ volatile("" ::: "memory"); return x + 1; }
static uint64_t worker(uint64_t arg) {
    H2(H->sleep_ns, 5000000, 0);
    flag = (uint32_t)arg;
    H2(H->wake_address, FNVNX_ADDR(&flag), 1);
    return 0;
}
static void num(const char *what, uint64_t v) {
    char b[64]; uint32_t n = 0, k = 0; char t[24];
    while (what[k]) b[n++] = what[k++];
    b[n++] = '=';
    k = 0; do { t[k++] = "0123456789abcdef"[v & 15]; v >>= 4; } while (v);
    while (k) b[n++] = t[--k];
    b[n++] = '\n'; b[n] = 0;
    say(b);
}
static uint64_t plat(uint32_t fn, uint64_t a, uint64_t b, uint64_t c, uint32_t n) {
    uint64_t v[3] = {a, b, c};
    return fnvnx_hcall4(H->ext_call, 11, fn, FNVNX_ADDR(v), n);
}
/* ACCESS_FAULT_HANDLER tests (MODE 12..22) */
#define PG(h) ((h)->heap_base + 0x40000)
#define STK(h) ((h)->heap_base + 0x51000)
static volatile uint64_t gk, gr, ga, gpc, gsp, gps, gv0, gv1, gcnt;
static void note(FnvnxFaultFrame *f) { gk = f->kind; gr = f->reason; ga = f->addr; gpc = f->pc; gsp = (uint64_t)(uintptr_t)__builtin_frame_address(0);   /* the handler's own stack */ gps = f->pstate; gv0 = f->v[8][0]; gv1 = f->v[8][1]; gcnt++; }
static uint64_t fh_fix(FnvnxFaultFrame *f) {   /* make the page RW again and retry the store */
    note(f);
    if (f->kind == 1 && f->reason == 1 && f->addr == PG(fnvnx_host)) plat(8, PG(fnvnx_host), 0x1000, 3, 3);
    return 0;
}
static uint64_t fh_skip(FnvnxFaultFrame *f) {   /* continue after the faulting instruction with a marker in x5 */
    note(f);
    f->pc += 4; f->x[5] = 0xABCD;
    return 0;
}
static uint64_t fh_five(FnvnxFaultFrame *f) { note(f); return 5; }
static uint64_t fh_nested(FnvnxFaultFrame *f) { note(f); return *(volatile uint32_t *)0x30000000; }
static uint64_t fh_regs(FnvnxFaultFrame *f) {
    note(f);
    __asm__ volatile("movi v8.2d, #0\n cmp xzr, xzr" ::: "v8", "cc");   /* dirty what the frame must restore */
    plat(8, PG(fnvnx_host), 0x1000, 3, 3);
    return 0;
}
static uint64_t fault_thread(uint64_t arg) { *(volatile uint32_t *)(uintptr_t)PG(fnvnx_host) = (uint32_t)arg; return 0; }
/* SECTION tests (MODE 30..34) */
static uint64_t plat4(uint32_t fn, uint64_t a, uint64_t b, uint64_t c, uint64_t d) {
    uint64_t v[4] = {a, b, c, d};
    return fnvnx_hcall4(H->ext_call, 11, fn, FNVNX_ADDR(v), 4);
}
#define SC(size) plat(FNVNX_PLAT_SECTION_CREATE, size, 0, 0, 1)
#define SM(id, addr, off, size) plat4(FNVNX_PLAT_SECTION_MAP, id, addr, off, size)
#define SU(addr, size) plat(FNVNX_PLAT_SECTION_UNMAP, addr, size, 0, 2)
#define SF(id) plat(FNVNX_PLAT_SECTION_FREE, id, 0, 0, 1)
#define W32(a) (*(volatile uint32_t *)(uintptr_t)(a))
static volatile uint64_t g_v1, g_v2, sflag;
static uint64_t sec_worker(uint64_t arg) {
    if (arg == 1) W32(g_v2 + 4) = W32(g_v2) + 1;   /* reads what main wrote through the other view */
    else W32(g_v2 + 8) = 0x77;                     /* writes for main to read through its view */
    sflag = arg;
    H2(H->wake_address, FNVNX_ADDR(&sflag), 1);
    return 0;
}
/* boot-stage CLI test (MODE 6): three functions called in order; results 0, 1, 0 */
__attribute__((noinline, used)) uint32_t fnvboot_a(uint32_t x) { __asm__ volatile("" ::: "memory"); return x - x; }
__attribute__((noinline, used)) uint32_t fnvboot_b(uint32_t x) { __asm__ volatile("" ::: "memory"); return (x | 1) & 1; }
__attribute__((noinline, used)) uint32_t fnvboot_c(uint32_t x) { __asm__ volatile("" ::: "memory"); return x ^ x; }
uint64_t __fnvnx_guest_entry(uint64_t api) {
    relocate((uint32_t)(uintptr_t)__fnvnx_guest_start);
    fnvnx_host = (const FnvnxHostApi *)(uintptr_t)api;
    say(MSG[0]); say(MSG[1]);
#if MODE == 6
    uint32_t acc = fnvboot_a(3);
    acc += fnvboot_b(acc);
    say("fnvnx-start: identity ok\n");
    acc += fnvboot_c(acc);
    fnvnx_hcall1(H->exit, acc - 1);
#elif MODE == 1
    uint64_t a3[3] = {0x500000, 0x1000, 1};   /* a 3-argument call to module 9: not registered */
    fnvnx_hcall4(H->ext_call, 9, 1, FNVNX_ADDR(a3), 3);
#elif MODE == 2
    fnvnx_hcall3(H->wait_on_address, FNVNX_ADDR(&flag), 0, ~0ull);   /* nobody wakes it, no deadline */
#elif MODE >= 7 && MODE <= 11
    uint64_t A = plat(4, 0, 0x8000000, 0, 2);
    volatile uint32_t *pa = (volatile uint32_t *)(uintptr_t)A;
    num("resA", A);
#if MODE == 7
    num("resB", plat(4, 0, 0x10000, 0, 2));
    num("commit", plat(5, A, 0x2000, 0, 2));
    pa[0] = 0x11; pa[0x400] = 0x22;
    num("rb", pa[0] + pa[0x400]);
    num("decommit", plat(6, A, 0x2000, 0, 2));
    num("recommit", plat(5, A, 0x2000, 0, 2));
    num("zero", pa[0] | pa[0x400]);
    pa[0] = 0x33;
    num("recommit-keeps", plat(5, A, 0x1000, 0, 2) + pa[0]);
    num("commit-outside", plat(5, 0x30000000, 0x1000, 0, 2));
    num("commit-past-end", plat(5, A + 0x8000000 - 0x1000, 0x2000, 0, 2));
    num("commit-size0", plat(5, A, 0, 0, 2));
    num("protect-uncommitted", plat(8, A + 0x10000, 0x1000, 1, 3));
    num("protect-committed", plat(8, A, 0x1000, 1, 3));
    num("ro-read", pa[0]);
    num("protect-rw", plat(8, A, 0x1000, 3, 3));
    pa[0] = 0x44;
    num("rw-write", pa[0]);
    num("protect-x", plat(8, A, 0x1000, 4, 3));
    plat(8, A + 0x1000, 0x1000, 0, 3);
    num("decommit-protected", plat(6, A, 0x2000, 0, 2));
    num("recommit-protected", plat(5, A, 0x2000, 0, 2));
    pa[0x400] = 0x55;
    num("zero-rw-after-protect", pa[0] | (pa[0x400] - 0x55));
    plat(5, A + 0x10000, 0x2000, 0, 2);
    pa[0x4000] = 0x66; pa[0x4400] = 0x77;
    num("partial-decommit", plat(6, A + 0x10000, 0x1000, 0, 2));
    num("partial-zero-writable", pa[0x4000] | (pa[0x4400] - 0x77));
    pa[0x4000] = 0x88;
    num("partial-write", pa[0x4000]);
    num("mapfixed-over-resv", fnvnx_hcall3(H->map_fixed, A, 0x1000, H->heap_base + 0x20000));
    num("release", plat(7, A, 0, 0, 1));
    num("release-again", plat(7, A, 0, 0, 1));
    num("release-inner", plat(7, A + 0x10000, 0, 0, 1));
    num("resA2", plat(4, 0, 0x8000000, 0, 2));
    num("hint-overlap", plat(4, A + 0x10000, 0x10000, 0, 2));
    num("hint-exe", plat(4, 0x500000, 0x10000, 0, 2));
    num("hint-module", plat(4, 0x1420000, 0x10000, 0, 2));
    num("hint-gap", plat(4, 0x3000000, 0x10000, 0, 2));
    num("hint-above-4g", plat(4, 0xFFFF0000u, 0x20000, 0, 2));
    num("hint-in-image", plat(4, H->guest_base, 0x10000, 0, 2));
    num("hint-size0", plat(4, 0x20000000, 0, 0, 2));
    num("hint-ok", plat(4, 0x20001234, 0x10001, 0, 2));
    num("hint-ok-overlap", plat(4, 0x20010000, 0x10000, 0, 2));
    num("tact-nothread", plat(2, 99, 1, 0, 2));
    num("tact-tid0", plat(2, 0, 1, 0, 2));
    uint64_t id = fnvnx_hcall5(H->thread_create, FNVNX_ADDR(worker), 77, 0x10000, 0, ~0ull);
    num("tact-new", plat(2, id, 1, 0, 2));
    fnvnx_hcall1(H->thread_start, id);
    num("tact-pause", plat(2, id, 1, 0, 2));
    fnvnx_hcall1(H->sleep_ns, 2000000);
    num("paused-flag", flag);
    num("tact-resume", plat(2, id, 0, 0, 2));
    while (flag == 0) fnvnx_hcall3(H->wait_on_address, FNVNX_ADDR(&flag), 0, ~0ull);
    num("resumed-flag", flag);
    fnvnx_hcall1(H->thread_join, id);
    num("boost", plat(1, 1, 0, 0, 1));
    num("message", plat(3, FNVNX_ADDR("cap"), FNVNX_ADDR("body"), 0, 2));
#elif MODE == 8
    plat(5, A, 0x1000, 0, 2);
    pa[0] = 1;
    num("committed-ok", pa[0]);
    num("touch-uncommitted", pa[0x400]);   /* reserved, not committed: the run must end here */
    say("after the fault\n");
#elif MODE == 9
    plat(5, A, 0x1000, 0, 2);
    plat(8, A, 0x1000, 1, 3);
    pa[0] = 1;   /* write to a read-only committed page */
    say("after the fault\n");
#elif MODE == 10
    plat(5, A, 0x1000, 0, 2);
    pa[0] = 1;
    plat(6, A, 0x1000, 0, 2);
    num("touch-decommitted", pa[0]);
    say("after the fault\n");
#else
    plat(9, 0, 0, 0, 0);   /* a fn of module 11 the header does not define */
#endif
#elif MODE == 30
    uint64_t R = plat(4, 0, 0x10000, 0, 2);
    num("resv", R != 0);
    num("c0", SC(0)); num("c-unal", SC(0x800)); num("c-big", SC(0x80001000ull));
    uint64_t s = SC(0x4000);
    num("id", s);
    uint64_t A = SM(s, R, 0, 0x2000);
    num("A", A == R);
    uint64_t B = SM(s, 0, 0x1000, 0x2000);
    num("B-own", B && !(B & 0xFFFF) && (B < R || B >= R + 0x10000));
    uint64_t C = SM(s, R + 0x4000, 0, 0x4000);
    num("C", C == R + 0x4000);
    num("zero", W32(A) | W32(A + 0x1FFC) | W32(B) | W32(C + 0x3FFC));
    W32(A) = 0x11; W32(A + 0x1000) = 0x22;      /* section page 0, page 1 */
    num("share1", W32(B));                       /* B page 0 = section page 1 */
    W32(B + 4) = 0x33; num("share2", W32(A + 0x1004));
    W32(B + 0x1000) = 0x44;                      /* section page 2: only B and C reach it */
    num("share3", W32(C + 0x2000));
    num("C-sees", W32(C) == 0x11 && W32(C + 0x1000) == 0x22 && W32(C + 0x1004) == 0x33);
    num("m-size0", SM(s, 0, 0, 0)); num("m-size-unal", SM(s, 0, 0, 0x800)); num("m-size-over", SM(s, 0, 0, 0x80001000ull));
    num("m-addr-unal", SM(s, R + 0x8001, 0, 0x1000)); num("m-off-unal", SM(s, 0, 0x800, 0x1000));
    num("m-id", SM(999, 0, 0, 0x1000)); num("m-id0", SM(0, 0, 0, 0x1000));
    num("m-range", SM(s, 0, 0x3000, 0x2000)); num("m-range-wrap", SM(s, 0, 0xFFFFFFFFFFFFF000ull, 0x2000));
    num("h-outside", SM(s, 0x30000000, 0, 0x1000)); num("h-keys", SM(s, 0xf0000, 0, 0x1000));
    num("h-image", SM(s, H->guest_base, 0, 0x1000)); num("h-heap", SM(s, H->heap_base, 0, 0x1000));
    num("h-straddle", SM(s, R + 0xF000, 0, 0x2000));
    num("h-over-view", SM(s, R + 0x1000, 0, 0x1000));
    num("commit-gap", plat(5, R + 0x8000, 0x1000, 0, 2));
    num("h-over-committed", SM(s, R + 0x8000, 0, 0x1000));
    num("vm-commit", plat(5, A, 0x1000, 0, 2)); num("vm-commit-wide", plat(5, R, 0x10000, 0, 2));
    num("vm-commit-B", plat(5, B, 0x1000, 0, 2));
    num("vm-decommit", plat(6, A, 0x1000, 0, 2)); num("vm-protect", plat(8, A, 0x1000, 1, 3));
    num("vm-protect-B", plat(8, B, 0x1000, 1, 3)); num("vm-release", plat(7, R, 0, 0, 1));
    num("view-intact", W32(A) == 0x11 && W32(C + 0x1000) == 0x22);
    num("mf-B", fnvnx_hcall3(H->map_fixed, B, 0x1000, H->heap_base + 0x20000) != 0);
    num("mf-A", fnvnx_hcall3(H->map_fixed, A, 0x1000, H->heap_base + 0x20000) != 0);
    uint64_t r2 = plat(4, 0, 0x1000, 0, 2);
    num("resv2-clear", r2 && (r2 + 0x10000 <= B || r2 >= B + 0x2000));
    num("resv-hint-B", plat(4, B, 0x10000, 0, 2));
    num("u-wrong-size", SU(A, 0x1000)); num("u-wrong-addr", SU(A + 0x1000, 0x1000)); num("u-nonview", SU(0x30000000, 0x1000));
    num("free", SF(s)); num("map-after-free", SM(s, 0, 0, 0x1000)); num("free-twice", SF(s)); num("free-unknown", SF(555));
    num("alive-A", W32(A) == 0x11);
    W32(B) = 0x55; num("alive-B", W32(A + 0x1000));
    num("u-A", SU(A, 0x2000)); num("u-B-still", W32(B)); num("u-C", SU(C, 0x4000));
    num("u-B", SU(B, 0x2000)); num("u-B-twice", SU(B, 0x2000));
    num("release-R", plat(7, R, 0, 0, 1));
    uint64_t s2 = SC(0x1000), B2 = SM(s2, 0, 0, 0x1000);
    num("B2-lowest", B2 == R); num("s2-id", s2);
#elif MODE == 31
    uint64_t s = SC(0x2000), A = SM(s, 0, 0, 0x2000), B = SM(s, 0, 0, 0x2000);
    W32(A) = 7; num("A-addr", A); num("B-addr", B);
    num("unmap-A", SU(A, 0x2000));
    num("B-works", W32(B));
    W32(B + 4) = 9; num("B-writes", W32(B + 4));
    num("touch-A", W32(A));   /* the unmapped view: the run must end here */
    say("after the fault\n");
#elif MODE == 32
    uint64_t s = SC(0x2000);
    g_v1 = SM(s, 0, 0, 0x2000); g_v2 = SM(s, 0, 0, 0x2000);
    W32(g_v1) = 0xA5A5;
    for (uint64_t k = 1; k <= 2; k++) {
        uint64_t id = fnvnx_hcall5(H->thread_create, FNVNX_ADDR(sec_worker), k, 0x10000, 0, ~0ull);
        fnvnx_hcall1(H->thread_start, id);
        while (sflag != k) fnvnx_hcall3(H->wait_on_address, FNVNX_ADDR(&sflag), k - 1, ~0ull);
        fnvnx_hcall1(H->thread_join, id);
    }
    num("thread-read", W32(g_v1 + 4)); num("thread-write", W32(g_v1 + 8));
#elif MODE == 33
    uint64_t s = SC(0x4000), v = SM(s, 0, 0, 0x4000), top = v + 0x4000;
    num("reg", plat(10, FNVNX_ADDR(fh_skip), top, 0, 2));
    uint64_t out, val = 9;
    __asm__ volatile("mov x5, #0\n str %w1, [%2]\n mov %0, x5" : "=r"(out) : "r"(val), "r"(0x30000000ull) : "x5", "memory");
    num("marker", out); num("kind", gk); num("reason", gr); num("addr", ga);
    num("frame-in-view", W32(top - 816) == 1 && W32(top - 808) == 0x30000000);
    num("same-pages", W32(top - 816) == W32(SM(s, 0, 0x3000, 0x1000) + 0x1000 - 816));
#elif MODE == 34
    uint64_t first = SC(0x1000), n = 1, last = first, r;
    for (; n < 128 && (r = SC(0x1000)) < 0x80000000ull; n++) last = r;
    num("sections-ok", n); num("last-id", last);
    num("c-full", SC(0x1000));
    uint64_t v = SM(first, 0, 0, 0x1000);
    num("free-first-deferred", SF(first));
    num("c-full-deferred", SC(0x1000));
    num("unmap-last-view", SU(v, 0x1000));
    uint64_t id129 = SC(0x1000);
    num("id129", id129);
    num("free-last", SF(last)); num("id130", SC(0x1000));
    uint64_t cnt = 0, asc = 1, prev = 0, a;
    for (; cnt < 512 && (a = SM(id129, 0, 0, 0x1000)) < 0x100000000ull; cnt++) { if (a <= prev || (a & 0xFFFF)) asc = 0; prev = a; }
    num("views-ok", cnt); num("views-ascending", asc);
    num("v-full", SM(id129, 0, 0, 0x1000));
    num("unmap-one", SU(prev, 0x1000)); num("v-after", SM(id129, 0, 0, 0x1000) == prev);
#elif MODE == 35
    fnvnx_hcall1(H->exit, 0x5200002A);   /* fnvnx_reach_violation(42) */
#elif MODE == 36
    fnvnx_hcall1(H->exit, 0x5100002A);   /* an ordinary non-zero code */
#elif MODE >= 12
    volatile uint32_t *pg = (volatile uint32_t *)(uintptr_t)PG(H);
    plat(8, PG(H), 0x1000, 3, 3);
    pg[0] = 1;
#if MODE == 12 || MODE == 20
    plat(8, PG(H), 0x1000, 1, 3);
#if MODE == 12
    num("reg", plat(10, FNVNX_ADDR(fh_fix), STK(H), 0, 2));
    pg[0] = 0x77;
    num("val", pg[0]); num("kind", gk); num("reason", gr); num("addr-ok", ga == PG(H)); num("count", gcnt);
    num("sp-in-stack", gsp < PG(H) + 0x20000 && gsp > PG(H));
#else
    plat(8, PG(H), 0x1000, 1, 3);
    uint64_t lo = 0x1122334455667788ull, hi = 0x99aabbccddeeff00ull, olo, ohi, nz;
    plat(10, FNVNX_ADDR(fh_regs), STK(H), 0, 2);
    __asm__ volatile("fmov d8, %[lo]\n ins v8.d[1], %[hi]\n mov x9, #5\n cmp x9, #7\n str w9, [%[p]]\n"
                     "mrs %[nz], nzcv\n fmov %[olo], d8\n mov %[ohi], v8.d[1]\n"
                     : [olo] "=&r"(olo), [ohi] "=&r"(ohi), [nz] "=&r"(nz)
                     : [lo] "r"(lo), [hi] "r"(hi), [p] "r"(pg) : "x9", "v8", "cc", "memory");
    num("v8lo", olo == lo); num("v8hi", ohi == hi); num("nzcv", nz >> 28);
    num("frame-pstate", gps >> 28); num("frame-v8", gv0 == lo && gv1 == hi); num("store", pg[0]);
#endif
#elif MODE == 13
    plat(8, PG(H), 0x1000, 1, 3);
    plat(10, FNVNX_ADDR(fh_skip), STK(H), 0, 2);
    uint64_t out, v = 9;
    __asm__ volatile("mov x5, #0\n str %w1, [%2]\n mov %0, x5" : "=r"(out) : "r"(v), "r"(pg) : "x5", "memory");
    num("marker", out); num("ins", *(volatile uint32_t *)(uintptr_t)gpc >> 22); num("still-ro", pg[0]);
#elif MODE == 14
    plat(10, FNVNX_ADDR(fh_skip), STK(H), 0, 2);
    uint64_t out;
    __asm__ volatile("mov x5, #0\n ldr w6, [%1]\n mov %0, x5" : "=r"(out) : "r"(0x30000000ull) : "x5", "x6", "memory");
    num("marker", out); num("kind", gk); num("reason", gr); num("addr", ga);
#elif MODE == 15
    plat(10, FNVNX_ADDR(fh_five), STK(H), 0, 2);
    plat(8, PG(H), 0x1000, 1, 3);
    pg[0] = 2;
    say("after the fault\n");
#elif MODE == 16
    plat(10, FNVNX_ADDR(fh_nested), STK(H), 0, 2);
    plat(8, PG(H), 0x1000, 1, 3);
    pg[0] = 2;
    say("after the fault\n");
#elif MODE == 17 || MODE == 18
    plat(8, PG(H), 0x1000, 1, 3);
#if MODE == 18
    num("reg", plat(10, FNVNX_ADDR(fh_fix), STK(H), 0, 2));
    num("unreg", plat(10, 0, 0, 0, 2));
#endif
    pg[0] = 2;
    say("after the fault\n");
#elif MODE == 19
    plat(8, PG(H), 0x1000, 1, 3);
    plat(10, FNVNX_ADDR(fh_fix), STK(H), 0, 2);   /* main's handler; the fault below is thread 2's */
    uint64_t id = fnvnx_hcall5(H->thread_create, FNVNX_ADDR(fault_thread), 3, 0x10000, 0, ~0ull);
    fnvnx_hcall1(H->thread_start, id);
    fnvnx_hcall1(H->thread_join, id);
    say("after the fault\n");
#elif MODE == 21
    plat(8, PG(H), 0x1000, 1, 3);
    num("misaligned", plat(10, FNVNX_ADDR(fh_fix), STK(H) + 8, 0, 2));
    plat(10, FNVNX_ADDR(fh_fix), STK(H), 0, 2);
    plat(8, STK(H) - 0x1000, 0x1000, 1, 3);   /* the frame page is read-only */
    pg[0] = 2;
    say("after the fault\n");
#endif
#else
    num("marker", marker(41));
    volatile uint32_t *hp = (volatile uint32_t *)(uintptr_t)H->heap_base;
    hp[0] = 0xabcd0001; hp[0x3FFF] = 0xabcd0002;
    num("map", fnvnx_hcall3(H->map_fixed, 0x400000, 0x10000, H->heap_base));
    volatile uint32_t *p = (volatile uint32_t *)0x400000;
    num("carried", p[0] ^ p[0x3FFF]);
    num("zeroed-rest", p[1] | p[0x3FFE]);
#if MODE == 4
    num("touch-src", hp[0]);   /* the given-away block: the run must end here */
    say("after the fault\n");
#endif
    p[0] = 0x1234; p[0x3FFF] = 0x5678;
    num("readback", p[0] + p[0x3FFF]);
    num("overlap", fnvnx_hcall3(H->map_fixed, 0x400000, 0x1000, H->heap_base + 0x20000));
    num("keys", fnvnx_hcall3(H->map_fixed, 0xf0000, 0x1000, H->heap_base + 0x20000));
    num("src-unaligned", fnvnx_hcall3(H->map_fixed, 0x500000, 0x1000, H->heap_base + 0x20001));
    num("src-given", fnvnx_hcall3(H->map_fixed, 0x500000, 0x1000, H->heap_base));
    num("src-outside", fnvnx_hcall3(H->map_fixed, 0x500000, 0x1000, 0x10000));
    num("src-past-end", fnvnx_hcall3(H->map_fixed, 0x500000, 0x2000, H->heap_base + H->heap_size - 0x1000));
    uint64_t a[3] = {0x400000, 0x10000, 1};
    num("protect", fnvnx_hcall4(H->ext_call, 11, 8, FNVNX_ADDR(a), 3));
    uint64_t u[3] = {0x500000, 0x1000, 1};
    num("protect-unmapped", fnvnx_hcall4(H->ext_call, 11, 8, FNVNX_ADDR(u), 3));
    uint64_t im[3] = {H->guest_base, 0x1000, 0};
    num("protect-image", fnvnx_hcall4(H->ext_call, 11, 8, FNVNX_ADDR(im), 3));
    uint64_t hg[3] = {H->heap_base, 0x1000, 0};
    num("protect-given-src", fnvnx_hcall4(H->ext_call, 11, 8, FNVNX_ADDR(hg), 3));
    uint64_t hp0[3] = {H->heap_base + 0x30000, 0x1000, 0}, hp1[3] = {H->heap_base + 0x30000, 0x1000, 3};
    num("protect-heap-none", fnvnx_hcall4(H->ext_call, 11, 8, FNVNX_ADDR(hp0), 3));
#if MODE == 5
    num("touch-guard", *(volatile uint32_t *)(uintptr_t)(H->heap_base + 0x30000));
    say("after the fault\n");
#endif
    num("protect-heap-rw", fnvnx_hcall4(H->ext_call, 11, 8, FNVNX_ADDR(hp1), 3));
    *(volatile uint32_t *)(uintptr_t)(H->heap_base + 0x30000) = 5;
    num("heap-rw-works", *(volatile uint32_t *)(uintptr_t)(H->heap_base + 0x30000));
    uint64_t hu[3] = {H->heap_base + 0x30000, 0x800, 3};
    num("protect-bad-size", fnvnx_hcall4(H->ext_call, 11, 8, FNVNX_ADDR(hu), 3));
    hu[0]++; hu[1] = 0x1000;
    num("protect-bad-addr", fnvnx_hcall4(H->ext_call, 11, 8, FNVNX_ADDR(hu), 3));
#if MODE == 3
    p[0] = 9;   /* a write to the read-only page: the run must end here */
    say("after the fault\n");
#endif
    uint64_t w[3] = {0x400000, 0x10000, 3};
    num("unprotect", fnvnx_hcall4(H->ext_call, 11, 8, FNVNX_ADDR(w), 3));
    p[0] = 9;
    uint64_t t1 = fnvnx_hcall0(H->system_tick), t2 = fnvnx_hcall0(H->system_tick);
    num("tickdiff", t2 - t1);
    uint64_t id = fnvnx_hcall5(H->thread_create, FNVNX_ADDR(worker), 77, 0x10000, 0, ~0ull);
    num("thread", id);
    fnvnx_hcall1(H->thread_start, id);
    uint64_t r = 0;
    while (flag == 0) r = fnvnx_hcall3(H->wait_on_address, FNVNX_ADDR(&flag), 0, ~0ull);
    num("woken", r); num("flag", flag);
    fnvnx_hcall1(H->thread_join, id);
    uint64_t fd = fnvnx_hcall2(H->fs_open, FNVNX_ADDR("sdmc:/Data.BIN"), FNVNX_O_READ);
    char buf[9]; buf[8] = 0;
    uint64_t n = fnvnx_hcall4(H->fs_pread, fd, FNVNX_ADDR(buf), 8, 2);
    num("pread", n); say(buf); say("\n");
    fnvnx_hcall1(H->fs_close, fd);
    num("rand-calls-equal", fnvnx_hcall0(H->random64) != fnvnx_hcall0(H->random64));
#endif
    fnvnx_hcall1(H->exit, 7);
    return 0;
}
'''


def case(name, cond, extra=""):
    N[0] += 1
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:400]))
    if not cond:
        FAILED.append(name)


def sh(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(" ".join(cmd) + "\n" + r.stdout + r.stderr)
    return r.stdout


def build(mode):
    """crt0.s + test C (GUEST with -DMODE) -> (ELF bytes, ELF path); importable: test_boot.py uses it"""
    src, ld = os.path.join(TMP, "g.c"), os.path.join(TMP, "guest.ld")
    if not os.path.exists(ld):
        shutil.copy(os.path.join(GUEST_DIR, "guest.ld"), ld)
        open(src, "w").write(GUEST)
        sh([CC] + CFLAGS[:3] + ["-c", os.path.join(GUEST_DIR, "crt0.s"), "-o", os.path.join(TMP, "crt0.o")])
    # crt0 header: the +20 size and +24 flags are filled as elf2nso does
    obj, elf = os.path.join(TMP, "g%d.o" % mode), os.path.join(TMP, "g%d.elf" % mode)
    sh([CC] + CFLAGS + ["-DMODE=%d" % mode, "-g", "-c", src, "-o", obj])
    sh(["aarch64-linux-gnu-ld"] + LDFLAGS + ["-T", ld, os.path.join(TMP, "crt0.o"), obj, "-o", elf])
    data = bytearray(open(elf, "rb").read())
    phoff, phnum = struct.unpack_from("<I", data, 28)[0], struct.unpack_from("<H", data, 44)[0]
    loads = [struct.unpack_from("<8I", data, phoff + 32 * i) for i in range(phnum)]
    loads = [p for p in loads if p[0] == 1]
    lo = min(p[3] for p in loads) // 4096 * 4096
    hi = (max(p[3] + p[5] for p in loads) + 4095) // 4096 * 4096
    first_x = next(p for p in loads if p[6] & 1)
    struct.pack_into("<III", data, first_x[1] + 16, lo, hi - lo, 1)
    return bytes(data), elf


def symbol(elf, name):
    for line in sh(["aarch64-linux-gnu-nm", elf]).splitlines():
        a, _t, n = line.split()
        if n == name:
            return int(a, 16)
    raise KeyError(name)


def make_plan(elf_bytes):
    rows = [("keys", 0xf0000, 0x200000), ("exe", 0x400000, 0x1409000), ("mod1", 0x1410000, 0x1500000)]
    plan = bootlayout.plan(bootlayout.parse_elf(elf_bytes), rows, 1 << 20)
    # the free range for map_fixed: the exe image window; keys stays never-mapped
    return plan


def run(elf_bytes, root, watch=None, **kw):
    svc = boothost.Services(root)
    m = bootmachine.Machine(elf_bytes, make_plan(elf_bytes), svc, **kw)
    lines, tids = [], []
    m.on_console(lines.append)
    m.on_thread(tids.append)
    if watch:
        watch(m)
    return m.run(), lines, tids, svc


def main():
    t0 = time.time()
    root = os.path.join(TMP, "sd")
    os.makedirs(root)
    open(os.path.join(root, "data.bin"), "wb").write(b"01ABCDEFGH23")
    built = {m: build(m) for m in (0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, *range(12, 22), *range(30, 37))}   # MODE 6 (boot CLI guest) is built by test_boot.py
    elf0, path0 = built[0]
    plan0 = make_plan(elf0)
    base = plan0["image"]["base"]
    hits = {"pc": [], "ret": [], "store": []}
    marker = base + symbol(path0, "marker")
    flag = base + symbol(path0, "flag")

    def watchers(m):
        m.watch_pc(marker, lambda tid, pc: hits["pc"].append((tid, pc)))
        m.watch_ret(marker, lambda tid, x0: hits["ret"].append((tid, x0)))
        m.watch_store(flag, lambda tid, a, s, v: hits["store"].append((tid, a, s, v)))

    r1, lines1, tids1, svc1 = run(elf0, root, watch=watchers)
    text = "\n".join(lines1)
    case("exit code 7, reason exit", r1["reason"] == "exit" and r1["exit_code"] == 7, r1)
    case("console: self-relocated string pointers", lines1[:2] == ["hello from the guest", "second line"], lines1[:3])
    for k, v in (("marker", "2a"), ("map", "0"), ("readback", "68ac"), ("overlap", "d401"), ("keys", "cc01"),
                 ("carried", str(hex(0xabcd0001 ^ 0xabcd0002))[2:]), ("zeroed-rest", "0"), ("src-unaligned", "cc01"),
                 ("src-given", "d401"), ("src-outside", "cc01"), ("src-past-end", "cc01"),
                 ("protect", "0"), ("protect-unmapped", "d401"), ("protect-image", "d401"),
                 ("protect-given-src", "d401"), ("protect-heap-none", "0"), ("protect-heap-rw", "0"),
                 ("heap-rw-works", "5"), ("protect-bad-size", "ca01"), ("protect-bad-addr", "cc01"), ("unprotect", "0"), ("tickdiff", "3e8"),
                 ("thread", "2"), ("woken", "0"), ("flag", "4d"), ("pread", "8"), ("rand-calls-equal", "1")):
        case("guest saw %s=%s" % (k, v), (k + "=" + v) in lines1, text)
    case("pread through fs_* (case-folded path)", "ABCDEFGH" in lines1, text)
    case("two threads, a context switch", r1["threads"] == 2 and r1["switches"] >= 2, r1)
    case("refusals recorded", [e["kind"] for e in r1["events"]] == ["map_fixed-refused"] * 6
                                                                      + ["vm_protect-refused"] * 5, r1["events"])
    case("watch_pc fired once, main thread", hits["pc"] == [(1, marker)], hits["pc"])
    case("watch_ret fired once with x0=42", hits["ret"] == [(1, 42)], hits["ret"])
    case("watch_store saw the worker's store", any(h[0] == 2 and h[3] == 77 for h in hits["store"]), hits["store"])
    r2, lines2, tids2, _ = run(elf0, root)
    case("deterministic: identical run() dicts", r1 == r2, (r1, r2))
    case("deterministic: identical on_thread sequences", tids1 == tids2 and len(tids1) > 3, (tids1, tids2))
    case("virtual time moved by sleep (5 ms)", r1["virtual_ns"] >= 5_000_000, r1["virtual_ns"])

    rf, linesf, _, _ = run(*[built[3][0], root])
    f = rf.get("fault", {})
    case("write to a read-only page ends the run: fault", rf["reason"] == "fault" and rf["exit_code"] is None, rf)
    case("fault record", f.get("kind") == "write-protected" and f.get("addr") == 0x400000 and f.get("tid") == 1
        and f.get("size") == 4 and set(f) == {"kind", "addr", "size", "pc", "lr", "tid"}, f)
    case("nothing runs after the fault", "after the fault" not in linesf, linesf)

    rf4, lines4 = run(built[4][0], root)[:2]
    case("touching the given-away src block ends the run: fault", rf4["reason"] == "fault"
        and rf4["fault"]["kind"] == "read-unmapped" and rf4["fault"]["addr"] == plan0["regions"]["heap"]["lo"], rf4)
    rg, linesg = run(built[5][0], root)[:2]
    case("protected-none heap page: read faults at that address", rg["reason"] == "fault"
        and rg["fault"]["kind"] == "read-protected" and rg["fault"]["addr"] == plan0["regions"]["heap"]["lo"] + 0x30000
        and "after the fault" not in linesg, rg)
    case("image-page protect refused and recorded", any(e["kind"] == "vm_protect-refused" and e["addr"] == base
        and e["result"] == 0xD401 for e in r1["events"]), r1["events"])
    case("nothing runs after the src fault", "after the fault" not in lines4 and "map=0" in lines4, lines4)
    case("run() carries the whole console text", r1["console"] == "\n".join(lines1) + "\n", r1["console"][:80])

    # instruction bound: one block hook, deterministic stop, reuse of the Icount total
    def limited(limit, with_ic):
        svc = boothost.Services(root)
        m = bootmachine.Machine(elf0, plan0, svc, limit_instructions=limit)
        ic = m.attach_icount(None, base) if with_ic else None
        return m.run(), ic, m.instr

    full, icf, ninstr = limited(10**9, True)
    case("limit not reached: normal exit, run() equals the unbounded run",
         full["reason"] == "exit" and full == r1, full)
    case("instr total equals Icount total", ninstr == icf.report()["totals"]["all"]["total"] > 1000, ninstr)
    lim = ninstr // 2
    la, ica, na = limited(lim, True)
    lb, _, nb = limited(lim, False)
    case("limit: reason limit, same stop point, with and without icount", la["reason"] == lb["reason"] == "limit"
        and la == lb and na == nb >= lim and na - lim < 64 and ica.report()["totals"]["all"]["total"] == na, (la, na, nb))
    try:
        bootmachine.Machine(elf0, plan0, boothost.Services(root), limit_instructions=0)
        ok = False
    except bootmachine.MachineError:
        ok = True
    case("limit_instructions <= 0 is a construction error", ok)

    re_, _, _, _ = run(built[1][0], root)
    case("unregistered ext module ends the run", re_["reason"] == "ext-module-missing"
        and re_["ext"] == {"module": 9, "fn": 1}, re_)
    rd, _, _, _ = run(built[2][0], root)
    case("no ready thread, no deadline: deadlock with a wait report", rd["reason"] == "deadlock"
        and rd["waits"][0]["tid"] == 1 and rd["waits"][0]["wait"][0] == "addr", rd)
    rl, _, _, _ = run(elf0, root, limit_host_calls=3)
    case("limit_host_calls ends with reason limit", rl["reason"] == "limit" and rl["host_calls"] == 3, rl)

    n_slots = sum(1 for n in bootmachine.SLOTS if callable(getattr(boothost.Services, "h_" + n, None)))
    case("50 slots, all resolved at construction", len(bootmachine.SLOTS) == 50 == n_slots
        and len(bootmachine.Machine(elf0, plan0, boothost.Services(root)).handlers) == 50)

    class NoPad(boothost.Services):
        h_pad_read = None

    try:
        bootmachine.Machine(elf0, plan0, NoPad(root))
        ok, msg = False, "no error"
    except bootmachine.MachineError as e:
        ok, msg = "h_pad_read" in str(e), str(e)
    case("a services object missing one slot raises at construction", ok, msg)

    # platform service (module 11): the VM service and thread activity
    heap_lo, image_hi = plan0["regions"]["heap"]["lo"], plan0["regions"]["image"]["hi"]
    rv, linesv, _, svcv = run(built[7][0], root)
    vals = {l.split("=")[0]: int(l.split("=")[1], 16) for l in linesv if "=" in l and l.split("=")[1].isalnum()}
    A, B, A2 = vals.get("resA", 0), vals.get("resB", 0), vals.get("resA2", 0)
    textv = "\n".join(linesv)
    case("vm: run exits 7", rv["reason"] == "exit" and rv["exit_code"] == 7, (rv, textv))
    def lowest(size, taken):
        """reference: the lowest 64 KiB multiple >= 0x10000 below 4 GiB clear of the plan's regions, free ranges and taken"""
        blocked = [(r["lo"], r["hi"]) for r in plan0["regions"].values()] + [(f["lo"], f["hi"]) for f in plan0["free"]] + taken
        c = 0x10000
        while c + size <= 1 << 32:
            if not any(c < b and a < c + size for a, b in blocked):
                return c
            c += 0x10000
    expA = lowest(0x8000000, [])
    expB = lowest(0x10000, [(expA, expA + 0x8000000)])
    case("vm: hintless 128 MiB reserve is the lowest fitting 64 KiB multiple by the rule (plan with two image ranges)",
         A == expA and A % 0x10000 == 0 and A >= 0x10000, (hex(A), hex(expA)))
    case("vm: the second reserve (64 KiB) does not overlap the first and is the lowest fitting address",
         B == expB and not (B < A + 0x8000000 and A < B + 0x10000), (hex(A), hex(B), hex(expB)))
    case("vm: commit, write, decommit, recommit gives zeros", all(vals.get(k) == v for k, v in (
        ("commit", 0), ("rb", 0x33), ("decommit", 0), ("recommit", 0), ("zero", 0))), textv)
    case("vm: recommit of a committed page keeps its content", vals.get("recommit-keeps") == 0x33, textv)
    case("vm: commit outside, past the end of, and of size 0 in a reservation fail",
         vals["commit-outside"] == 0xCC01 and vals["commit-past-end"] == 0xCC01 and vals["commit-size0"] == 0xCA01, textv)
    case("vm: VM_PROTECT on an uncommitted page: nonzero", vals.get("protect-uncommitted") == 0xD401, textv)
    case("vm: VM_PROTECT on a committed page works, read-only reads, rw writes", vals.get("protect-committed") == 0
         and vals.get("ro-read") == 0x33 and vals.get("protect-rw") == 0 and vals.get("rw-write") == 0x44, textv)
    case("vm: VM_PROTECT with an X bit is refused", vals.get("protect-x") == 0xD801, textv)
    case("vm: recommit after decommit is RW and zero whatever the protection was",
         vals.get("recommit-protected") == 0 and vals.get("zero-rw-after-protect") == 0, textv)
    case("vm: decommit of part of an extent zeroes it and it stays accessible (host_vm.inc)",
         vals.get("partial-decommit") == 0 and vals.get("partial-zero-writable") == 0 and vals.get("partial-write") == 0x88, textv)
    case("vm: map_fixed over a reservation refused", vals.get("mapfixed-over-resv") == 0xD401, textv)
    case("vm: release frees the whole reservation; a non-base address is refused",
         vals.get("release") == 0 and vals.get("release-again") == 0xCC01 and vals.get("release-inner") == 0xCC01, textv)
    case("vm: reserve after release gives the same address", A2 == A, (hex(A), hex(A2)))
    case("vm: hints: overlap, inside the exe or a module range, above 4 GiB, image, size 0 fail; a hint in a gap or free address is exact (64 KiB rounded)",
         all(vals.get(k) == 0 for k in ("hint-overlap", "hint-exe", "hint-module", "hint-above-4g", "hint-in-image", "hint-size0",
                                        "hint-ok-overlap")) and vals.get("hint-ok") == 0x20000000
         and vals.get("hint-gap") == 0x3000000, textv)
    kinds = [e["kind"] for e in rv["events"]]
    case("vm: every refusal is an event with its result", kinds == [
        "vm_commit-refused"] * 3 + ["vm_protect-refused"] * 2 + ["map_fixed-refused"] + ["vm_release-refused"] * 2
        + ["vm_reserve-refused"] * 7 + ["thread_activity-refused"] * 3, kinds)
    case("platform: thread_activity: unknown / tid 0 -> 0xE401, new thread -> 0xFA01, pause holds the worker, resume runs it",
         vals.get("tact-nothread") == 0xE401 and vals.get("tact-tid0") == 0xE401 and vals.get("tact-new") == 0xFA01
         and vals.get("tact-pause") == 0 and vals.get("paused-flag") == 0 and vals.get("tact-resume") == 0
         and vals.get("resumed-flag") == 0x4d, textv)
    case("platform: cpu_boost and message are the services'", svcv.boosts == [1] and svcv.messages == [("cap", "body")]
         and vals.get("boost") == 0 and vals.get("message") == 0, (svcv.boosts, svcv.messages))
    rv2 = run(built[7][0], root)[0]
    case("vm: two identical runs give identical run() dicts", rv == rv2, (rv, rv2))
    rf8, lines8 = run(built[8][0], root)[:2]
    case("vm: access to a reserved, uncommitted page: fault record", rf8["reason"] == "fault"
         and rf8["fault"]["kind"] == "read-unmapped" and rf8["fault"]["addr"] == int(lines8[2].split("=")[1], 16) + 0x1000
         and "committed-ok=1" in lines8 and "after the fault" not in lines8, (rf8, lines8))
    rf9, lines9 = run(built[9][0], root)[:2]
    case("vm: write to a VM_PROTECT read-only committed page: fault", rf9["reason"] == "fault"
         and rf9["fault"]["kind"] == "write-protected" and "after the fault" not in lines9, (rf9, lines9))
    rf10, lines10 = run(built[10][0], root)[:2]
    case("vm: access to a wholly decommitted extent: fault", rf10["reason"] == "fault" and rf10["fault"]["kind"] == "read-unmapped"
         and "after the fault" not in lines10, (rf10, lines10))
    rf11 = run(built[11][0], root)[0]
    case("platform: a fn of module 11 the header does not define ends the run as ext-module-missing",
         rf11["reason"] == "ext-module-missing" and rf11["ext"] == {"module": 11, "fn": 9}, rf11)

    class NoMsg(boothost.Services):
        plat_message = None

    try:
        bootmachine.Machine(elf0, plan0, NoMsg(root))
        ok, msg = False, "no error"
    except bootmachine.MachineError as e:
        ok, msg = "message" in str(e), str(e)
    case("a header-defined platform fn nobody implements raises at construction", ok, msg)
    case("every header fn 1..8, 10 and 11..14 of module 11 resolves", sorted(bootmachine.Machine(elf0, plan0, boothost.Services(root)).plat) == [*range(1, 9), 10, 11, 12, 13, 14])

    class NoSec(boothost.Services):
        plat_section_map = None

    try:
        bootmachine.Machine(elf0, plan0, NoSec(root))   # services may override a fn; None means "not there": the machine's own serves it
        ok = True
    except bootmachine.MachineError:
        ok = False
    case("section fns are served by the machine itself", ok)
    del bootmachine.PLATFORM_FNS[14]
    try:
        bootmachine.Machine(elf0, plan0, boothost.Services(root))
        ok, msg = True, ""
    except bootmachine.MachineError as e:
        ok, msg = False, str(e)
    case("construction check is over PLATFORM_FNS (fn 14 removed: still builds, 13 others resolve)", ok, msg)
    bootmachine.PLATFORM_FNS[14] = ("section_free", 1)
    saved = bootmachine.Machine._plat_section_free
    del bootmachine.Machine._plat_section_free
    try:
        bootmachine.Machine(elf0, plan0, boothost.Services(root))
        ok, msg = False, "no error"
    except bootmachine.MachineError as e:
        ok, msg = "section_free" in str(e), str(e)
    bootmachine.Machine._plat_section_free = saved
    case("totality: a missing section fn raises at construction", ok, msg)
    # ACCESS_FAULT_HANDLER (module 11 fn 10)
    def kv(lines):
        return {l.split("=")[0]: l.split("=")[1] for l in lines if "=" in l}

    ra, la = run(built[12][0], root)[:2]
    k = kv(la)
    case("fault handler: write to a read-only page, handler restores RW, the store is re-executed",
         ra["reason"] == "exit" and ra["exit_code"] == 7 and k.get("reg") == "0" and k.get("val") == "77" and k.get("kind") == "1"
         and k.get("reason") == "1" and k.get("addr-ok") == "1" and k.get("count") == "1" and k.get("sp-in-stack") == "1"
         and len(ra["events"]) == 1 and ra["events"][0]["kind"] == "fault-handler" and ra["events"][0]["reason"] == 1, (ra, la))
    rb, lb = run(built[13][0], root)[:2]
    k = kv(lb)
    case("fault handler: handler advances pc and sets x5; execution continues after the faulting instruction",
         rb["reason"] == "exit" and k.get("marker") == "abcd" and k.get("ins") == "2e4" and k.get("still-ro") == "1", (rb, lb))
    rc, lc = run(built[14][0], root)[:2]
    k = kv(lc)
    case("fault handler: read of an unmapped address: kind 0, reason 0", rc["reason"] == "exit" and k.get("kind") == "0"
         and k.get("reason") == "0" and k.get("addr") == "30000000" and k.get("marker") == "abcd", (rc, lc))
    rd, ld = run(built[15][0], root)[:2]
    case("fault handler: non-zero return ends the run with that exit code", rd["reason"] == "exit" and rd["exit_code"] == 5
         and "after the fault" not in ld, (rd, ld))
    re_, le = run(built[16][0], root)[:2]
    case("fault handler: a fault inside the handler ends the run (in_handler)", re_["reason"] == "fault"
         and re_["fault"].get("in_handler") is True and re_["fault"]["kind"] == "read-unmapped"
         and re_["fault"]["addr"] == 0x30000000 and "after the fault" not in le, (re_, le))
    rf, lf = run(built[17][0], root)[:2]
    case("fault handler: no handler registered: the fault ends the run as before", rf["reason"] == "fault"
         and rf["fault"]["kind"] == "write-protected" and "in_handler" not in rf["fault"] and rf["events"] == []
         and "after the fault" not in lf, (rf, lf))
    rg, lg = run(built[19][0], root)[:2]
    case("fault handler: a handler of thread 1 does not serve thread 2's fault", rg["reason"] == "fault"
         and rg["fault"]["tid"] == 2 and rg["fault"]["kind"] == "write-protected" and rg["events"] == [], (rg, lg))
    rh, lh = run(built[18][0], root)[:2]
    k = kv(lh)
    case("fault handler: handler 0 unregisters", rh["reason"] == "fault" and k.get("reg") == "0" and k.get("unreg") == "0"
         and rh["fault"]["kind"] == "write-protected" and rh["events"] == [], (rh, lh))
    ri, li = run(built[20][0], root)[:2]
    k = kv(li)
    case("fault handler: v8 and NZCV round trip through the frame (handler dirties both)", ri["reason"] == "exit"
         and k.get("v8lo") == "1" and k.get("v8hi") == "1" and k.get("nzcv") == "8" and k.get("frame-pstate") == "8"
         and k.get("frame-v8") == "1" and k.get("store") == "5", (ri, li))
    rj, lj = run(built[21][0], root)[:2]
    case("fault handler: unwritable frame page ends the run as frame-unwritable; misaligned stack_top is refused",
         rj["reason"] == "fault" and rj["fault"]["kind"] == "frame-unwritable" and kv(lj).get("misaligned") == "cc01", (rj, lj))
    case("fault handler: two identical runs give identical run() dicts", ra == run(built[12][0], root)[0]
         and ri == run(built[20][0], root)[0], ra)
    # sections (module 11 fn 11..14)
    def E(c):
        return "%x" % (-c & ((1 << 64) - 1))

    r30, l30 = run(built[30][0], root)[:2]
    k = kv(l30)
    case("section: run exits", r30["reason"] == "exit" and r30["exit_code"] == 7, (r30, l30))
    ok_vals = {"resv": "1", "c0": E(0xCA01), "c-unal": E(0xCA01), "c-big": E(0xCA01), "id": "1", "A": "1", "B-own": "1", "C": "1",
               "zero": "0", "share1": "22", "share2": "33", "share3": "44", "C-sees": "1",
               "m-size0": E(0xCA01), "m-size-unal": E(0xCA01), "m-size-over": E(0xCA01), "m-addr-unal": E(0xCC01),
               "m-off-unal": E(0xCC01), "m-id": E(0xE401), "m-id0": E(0xE401), "m-range": E(0xD401), "m-range-wrap": E(0xD401),
               "h-outside": E(0xDA01), "h-keys": E(0xDA01), "h-image": E(0xDA01), "h-heap": E(0xDA01), "h-straddle": E(0xDA01),
               "h-over-view": E(0xD401), "commit-gap": "0", "h-over-committed": E(0xD401),
               "vm-commit": "d401", "vm-commit-wide": "d401", "vm-commit-B": "d401", "vm-decommit": "d401", "vm-protect": "d401",
               "vm-protect-B": "d401", "vm-release": "d401", "view-intact": "1", "mf-B": "1", "mf-A": "1", "resv2-clear": "1",
               "resv-hint-B": "0", "u-wrong-size": E(0xD401), "u-wrong-addr": E(0xD401), "u-nonview": E(0xD401),
               "free": "0", "map-after-free": E(0xE401), "free-twice": E(0xE401), "free-unknown": E(0xE401), "alive-A": "1",
               "alive-B": "55", "u-A": "0", "u-B-still": "55", "u-C": "0", "u-B": "0", "u-B-twice": E(0xD401),
               "release-R": "0", "B2-lowest": "1", "s2-id": "2"}
    bad = {n: (k.get(n), v) for n, v in ok_vals.items() if k.get(n) != v}
    case("section: every create/map/unmap/free/interplay result as the header says (%d values)" % len(ok_vals), not bad, bad)
    ev = [(e["kind"], e["result"]) for e in r30["events"]]
    want = [("section_create-refused", 0xCA01)] * 3 + [("section_map-refused", c) for c in (
        0xCA01, 0xCA01, 0xCA01, 0xCC01, 0xCC01, 0xE401, 0xE401, 0xD401, 0xD401, 0xDA01, 0xDA01, 0xDA01, 0xDA01, 0xDA01, 0xD401)]
    case("section: refusals recorded in order (create, map errors)", ev[:len(want)] == want, ev[:len(want)])
    tail = ev[len(want):]
    case("section: later refusals are vm_*, map_fixed, section_map/unmap/free refusals with the codes",
         tail[:1] == [("section_map-refused", 0xD401)] and tail[1:8] == [("vm_commit-refused", 0xD401)] * 3
         + [("vm_decommit-refused", 0xD401), ("vm_protect-refused", 0xD401), ("vm_protect-refused", 0xD401), ("vm_release-refused", 0xD401)]
         and ("map_fixed-refused", 0xCC01) in tail and tail.count(("section_unmap-refused", 0xD401)) == 4
         and tail.count(("section_free-refused", 0xE401)) == 2 and ("section_map-refused", 0xE401) in tail, tail)
    r31, l31 = run(built[31][0], root)[:2]
    k = kv(l31)
    case("section: unmapped view faults, the other view still works and writes",
         r31["reason"] == "fault" and r31["fault"]["kind"] == "read-unmapped" and r31["fault"]["addr"] == int(k["A-addr"], 16)
         and k["unmap-A"] == "0" and k["B-works"] == "7" and k["B-writes"] == "9" and k["A-addr"] != k["B-addr"]
         and "after the fault" not in l31, (r31, l31))
    r32, l32 = run(built[32][0], root)[:2]
    k = kv(l32)
    case("section: threads share views (B reads A's write, main reads the thread's write)",
         r32["reason"] == "exit" and r32["threads"] == 3 and k.get("thread-read") == "a5a6" and k.get("thread-write") == "77", (r32, l32))
    r33, l33 = run(built[33][0], root)[:2]
    k = kv(l33)
    case("section: the fault-handler frame may live in a view (frame bytes visible through another view)",
         r33["reason"] == "exit" and k.get("reg") == "0" and k.get("marker") == "abcd" and k.get("kind") == "1"
         and k.get("addr") == "30000000" and k.get("frame-in-view") == "1" and k.get("same-pages") == "1", (r33, l33))
    r34, l34 = run(built[34][0], root)[:2]
    k = kv(l34)
    case("section: table limits (128 sections incl. deferred ones, 512 views, ids never reused)",
         r34["reason"] == "exit" and k.get("sections-ok") == "80" and k.get("last-id") == "80" and k.get("c-full") == E(0xCE01)
         and k.get("free-first-deferred") == "0" and k.get("c-full-deferred") == E(0xCE01) and k.get("unmap-last-view") == "0"
         and k.get("id129") == "81" and k.get("free-last") == "0" and k.get("id130") == "82" and k.get("views-ok") == "200"
         and k.get("views-ascending") == "1" and k.get("v-full") == E(0xCE01) and k.get("unmap-one") == "0" and k.get("v-after") == "1",
         (r34, l34))
    case("section: two identical runs give identical run() dicts", r30 == run(built[30][0], root)[0]
         and r32 == run(built[32][0], root)[0] and r34 == run(built[34][0], root)[0], r30)
    r35 = run(built[35][0], root)[0]
    r36 = run(built[36][0], root)[0]
    case("exit code 0x52000000|fact_id is reason reach-violation with fact_id", r35["reason"] == "reach-violation"
         and r35["fact_id"] == 42 and r35["exit_code"] == 0x5200002A, r35)
    case("other non-zero exit codes stay ordinary exits (no fact_id)", r36["reason"] == "exit" and "fact_id" not in r36
         and r1["reason"] == "exit" and "fact_id" not in r1, r36)
    print("%d cases, %.1f s" % (N[0], time.time() - t0))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
