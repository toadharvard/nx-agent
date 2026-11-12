#!/usr/bin/env python3
"""Tests of verify/bootmachine.py + boothost.py (the boot stage machine). Plain script: exit 0 on pass, one line per case.
Builds small ILP32 PIE guests with aarch64-linux-gnu-gcc (-mabi=ilp32) from the fixtures in tests/guest/ (crt0.s with the
FNXG header, guest.ld rebased to 0 with its two base asserts already removed, hostapi.h); the runtime part is the test's own
freestanding entry (the real runtime.c needs devkitA64's newlib: sys/iosupport.h). The guest relocates itself, as runtime.c
does. Scratch: tempfile.mkdtemp, removed at exit."""
import atexit
import functools
import os
import re
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
#define EC(m, f, p, n) fnvnx_hcall4(H->ext_call, m, f, p, n)
static volatile uint32_t wf, wdone;
static uint64_t waiter(uint64_t arg) { fnvnx_hcall3(H->wait_on_address, FNVNX_ADDR(&wf), 0, ~0ull); wdone++; return arg; }
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
static uint64_t sk(uint32_t fn, uint32_t n, uint64_t a, uint64_t b, uint64_t c, uint64_t d, uint64_t e) {
    uint64_t v[5] = {a, b, c, d, e};
    return fnvnx_hcall4(H->ext_call, 11, fn, FNVNX_ADDR(v), n);
}
#define NEG(e) ((uint64_t)-(int64_t)(e))
static volatile uint32_t sflag2, mainran, tcount;
static uint64_t self_worker(uint64_t self) {   /* pauses and resumes the thread that called THREAD_SELF */
    fnvnx_hcall2(H->sleep_ns, 1000000, 0);
    uint64_t rp = plat(2, self, 1, 0, 2);
    sflag2 = 1;
    fnvnx_hcall2(H->wake_address, FNVNX_ADDR(&sflag2), 1);
    fnvnx_hcall2(H->sleep_ns, 2000000, 0);
    uint32_t ran = mainran;   /* main is woken but paused: it has not run */
    uint64_t rr = plat(2, self, 0, 0, 2);
    tcount = 100 + (uint32_t)rp + 10 * ran + 1000 * (uint32_t)rr;
    return 0;
}
static volatile uint64_t wrec;
static uint64_t rec_worker(uint64_t arg) { wrec = plat(35, 0, 0, 0, 0); return arg; }   /* a record of a thread that then ends */
static uint64_t count_worker(uint64_t arg) { fnvnx_hcall2(H->sleep_ns, 1000000, 0); tcount++; return arg; }
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
    fnvnx_hcall4(H->ext_call, 9, 99, FNVNX_ADDR(a3), 3);
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
    num("mapfixed-over-resv", fnvnx_hcall3(H->map_fixed, A, 0x1000, H->heap_base + 0x20000));   /* a committed page: occupied */
    num("mapfixed-over-resv-free", fnvnx_hcall3(H->map_fixed, A + 0x20000, 0x1000, H->heap_base + 0x20000));   /* reserved, uncommitted */
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
    sk(16, 3, 2, 3, 0, 0, 0);   /* a RAW socket: whether the host allows it is the OS's decision, the header does not say */
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
    num("vm-commit-B", plat(5, B, 0x1000, 0, 2)); num("vm-decommit-B", plat(6, B, 0x1000, 0, 2));
    num("vm-decommit", plat(6, A, 0x1000, 0, 2)); num("vm-protect", plat(8, A, 0x1000, 1, 3));
    num("vm-protect-B", plat(8, B, 0x1000, 1, 3)); num("vm-release", plat(7, R, 0, 0, 1));
    num("view-intact", W32(A) == 0x11 && W32(C + 0x1000) == 0x22);
    num("mf-B", fnvnx_hcall3(H->map_fixed, B, 0x1000, H->heap_base + 0x20000));   /* a host-placed view */
    num("mf-A", fnvnx_hcall3(H->map_fixed, A, 0x1000, H->heap_base + 0x20000));   /* a view inside a reservation */
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
#elif MODE >= 37 && MODE <= 39
    /* UNMAP_FIXED (fn 15) */
    #define UF(a, s, z) plat(FNVNX_PLAT_UNMAP_FIXED, a, s, z, 3)
    #define MF(a, z, s) fnvnx_hcall3(H->map_fixed, a, z, s)
    uint64_t src = H->heap_base + 0x30000, dst = 0x4000000;
    W32(src) = 0x1234;
    num("map", MF(dst, 0x2000, src));
    num("carried", W32(dst));
    W32(dst) = 0xAAAA; W32(dst + 0x1004) = 0xBBBB;   /* written through the mapping */
#if MODE == 37
    num("u-wrong-src", UF(dst, 0x2000, src + 0x1000));
    num("u-wrong-addr", UF(dst + 0x1000, 0x1000, src));
    num("u-wrong-size", UF(dst, 0x1000, src));
    num("u-addr-unal", UF(dst + 4, 0x2000, src));
    num("u-src-unal", UF(dst, 0x2000, src + 4));
    num("u-size0", UF(dst, 0, src)); num("u-size-unal", UF(dst, 0x1800, src));
    num("still-mapped", W32(dst) == 0xAAAA);
    num("ok", UF(dst, 0x2000, src));
    num("src-bytes", W32(src) == 0xAAAA && W32(src + 0x1004) == 0xBBBB);
    W32(src + 8) = 0x77; num("src-writable", W32(src + 8));
    num("twice", UF(dst, 0x2000, src));
    num("map-again", MF(dst, 0x2000, src));
    num("again-bytes", W32(dst) == 0xAAAA && W32(dst + 0x1004) == 0xBBBB && W32(dst + 8) == 0x77);
    num("src-given-again", MF(0x4020000, 0x1000, src));
    num("ok-again", UF(dst, 0x2000, src));
#elif MODE == 38
    num("ok", UF(dst, 0x2000, src));
    num("touch-dst", W32(dst));   /* unmapped: the run must end here */
    say("after the fault\n");
#else
    plat(8, dst, 0x1000, 1, 3);   /* read-only and no access: UNMAP_FIXED needs no restore */
    plat(8, dst + 0x1000, 0x1000, 0, 3);
    num("ok", UF(dst, 0x2000, src));
    num("src-bytes", W32(src) == 0xAAAA && W32(src + 0x1004) == 0xBBBB);
    W32(src + 0x1004) = 5; num("src-rw", W32(src + 0x1004));
#endif
#elif MODE == 40
    /* ext_call and module 11 totality */
    uint64_t v[4] = {1, 2, 3, 4};
    num("mod64", EC(64, 1, FNVNX_ADDR(v), 1));
    num("mod-huge", EC(0xFFFFFFFFFFFFFFFFull, 1, 0, 0));
    num("mod9-unreg", EC(9, 1, FNVNX_ADDR(v), 1));
    num("mod12-fn-huge", EC(12, 0x100000001ull, 0, 0));
    num("fn0", EC(11, 0, FNVNX_ADDR(v), 4));
    num("fn37", EC(11, 37, FNVNX_ADDR(v), 4));
    num("te-n4", EC(11, 36, FNVNX_ADDR(v), 4));
    num("te-null-array", EC(11, 36, 0, 5));
    num("self-n0-null", EC(11, 35, 0, 0));
    num("self-n1-null", EC(11, 35, 0, 1));
    num("sock-null-array-wrongn", EC(11, 17, 0, 2));
    num("sock-null-array-n0", EC(11, 17, 0, 0));
    num("sock-null-array-exact", EC(11, 17, 0, 1));
    num("fn0-null-array", EC(11, 0, 0, 2));
    num("fn-u32max", EC(11, 0xFFFFFFFFull, FNVNX_ADDR(v), 4));
    num("fn-above-u32", EC(11, 0x100000004ull, FNVNX_ADDR(v), 4));
    num("fn10-nargs1", EC(11, 10, FNVNX_ADDR(v), 1));
    num("vm-reserve-n1", EC(11, 4, FNVNX_ADDR(v), 1));
    num("null-array", EC(11, 4, 0, 2));
    num("docked-n0-null", EC(11, 9, 0, 0));
    num("docked-n1-null", EC(11, 9, 0, 1));
    num("docked-n3", EC(11, 9, FNVNX_ADDR(v), 3));
    num("docked-ncap", EC(11, 9, FNVNX_ADDR(v), 0x100000005ull));
    num("boost-extra-args", EC(11, 1, FNVNX_ADDR(v), 4));
    num("sock-wrong-n", EC(11, 16, FNVNX_ADDR(v), 2));
    num("sock-wrong-n0", EC(11, 17, 0, 0));
    num("sock-too-many", EC(11, 31, FNVNX_ADDR(v), 4));
    num("tz-n1", EC(11, 34, FNVNX_ADDR(v), 1));
    num("tz-null", EC(11, 34, 0, 2));
    num("reserve-above-4g", plat(4, 0, 0x100000001ull, 0, 2));
    num("reserve-4g", plat(4, 0, 0x100000000ull, 0, 2));
#elif MODE == 41
    uint64_t v[4] = {1, 2, 3, 4};
    num("m9-fn1", EC(9, 1, FNVNX_ADDR(v), 3));
    num("m9-fn-huge", EC(9, 0x100000001ull, FNVNX_ADDR(v), 1));
    num("m9-nargs0", EC(9, 2, 0, 0));
    num("m9-missing", EC(9, 99, FNVNX_ADDR(v), 1));
    say("after the missing service\n");
#elif MODE == 44
    /* VM_PROTECT: check order, spans, windows; UNMAP_FIXED of protected pages */
#define MF(a, z, s) fnvnx_hcall3(H->map_fixed, a, z, s)
#define UF(a, s, z) plat(FNVNX_PLAT_UNMAP_FIXED, a, s, z, 3)
    uint64_t R = plat(4, 0, 0x10000, 0, 2);
    plat(5, R, 0x1000, 0, 2); plat(5, R + 0x1000, 0x1000, 0, 2);   /* two extents */
    volatile uint32_t *pa = (volatile uint32_t *)(uintptr_t)R;
    pa[0] = 1; pa[0x400] = 2;
    num("p-addr-unal", plat(8, R + 4, 0x1000, 3, 3));
    num("p-addr-unal-size0", plat(8, R + 4, 0, 3, 3));
    num("p-size0", plat(8, R, 0, 3, 3));
    num("p-size-unal", plat(8, R, 0x800, 3, 3));
    num("p-wrap", plat(8, 0xFFFFFFFFFFFFF000ull, 0x2000, 3, 3));
    num("p-wrap-beats-perm", plat(8, 0xFFFFFFFFFFFFF000ull, 0x2000, 2, 3));
    num("p-wo", plat(8, R, 0x1000, 2, 3));
    num("p-x", plat(8, R, 0x1000, 4, 3));
    num("p-7", plat(8, R, 0x1000, 7, 3));
    num("p-big", plat(8, R, 0x1000, 0x100000003ull, 3));
    num("p-perm-beats-owned", plat(8, 0x3000000, 0x1000, 2, 3));
    num("p-unowned", plat(8, 0x3000000, 0x1000, 3, 3));
    num("p-still-rw", pa[0] + pa[0x400]);
    num("p-span", plat(8, R, 0x2000, 1, 3));
    num("p-span-ro", pa[0] + pa[0x400]);
    num("p-none", plat(8, R + 0x1000, 0x1000, 0, 3));
    num("p-rw", plat(8, R, 0x2000, 3, 3));
    pa[0] = 5; pa[0x400] = 6; num("p-rw-write", pa[0] + pa[0x400]);
    plat(8, R, 0x1000, 1, 3);
    num("decommit-ro-none", plat(6, R, 0x2000, 0, 2));
    num("decommit-wrap", plat(6, 0xFFFFFFFFFFFFF000ull, 0x2000, 0, 2));
    num("decommit-size0", plat(6, R, 0, 0, 2));
    num("recommit", plat(5, R, 0x2000, 0, 2));
    num("recommit-zero", pa[0] | pa[0x400]);
    volatile uint32_t *w = (volatile uint32_t *)0x500000;
    w[0] = 0x77;
    num("w-ro", plat(8, 0x500000, 0x1000, 1, 3)); num("w-ro-read", w[0]);
    num("w-none", plat(8, 0x500000, 0x1000, 0, 3));
    num("w-rw", plat(8, 0x500000, 0x1000, 3, 3)); w[1] = 9; num("w-rw-keeps", w[0] + w[1]);
    uint64_t src = H->heap_base + 0x310000, dst = 0x4000000;
    W32(src) = 0x1234;
    num("mf", MF(dst, 0x2000, src));
    W32(dst) = 0xAAAA; W32(dst + 0x1004) = 0xBBBB;
    plat(8, dst, 0x1000, 1, 3); plat(8, dst + 0x1000, 0x1000, 0, 3);
    num("uf-protected", UF(dst, 0x2000, src));
    num("uf-bytes", W32(src) == 0xAAAA && W32(src + 0x1004) == 0xBBBB);
    W32(src + 8) = 7; num("uf-src-rw", W32(src + 8));
#elif MODE == 45
    /* map_fixed: check order, the window, the 512 limit; UNMAP_FIXED in the window */
#define MF(a, z, s) fnvnx_hcall3(H->map_fixed, a, z, s)
#define UF(a, s, z) plat(FNVNX_PLAT_UNMAP_FIXED, a, s, z, 3)
    volatile uint32_t *w = (volatile uint32_t *)0x500000;
    uint64_t src = H->heap_base + 0x310000;
    num("size0", MF(0x4000000, 0, src));
    num("wrap", MF(0xFFFFFFFFFFFFF000ull, 0x2000, src));
    num("addr-unal", MF(0x4000004, 0x1000, src));
    num("size-unal", MF(0x4000000, 0x800, src));
    num("size0-in-window", MF(0x500000, 0, 0));
    num("past-4g", MF(0xFFFFF000ull, 0x2000, src));
    num("src0", MF(0x4000000, 0x1000, 0));
    num("src-unal", MF(0x4000000, 0x1000, src + 4));
    num("outside-region", MF(0x3000000, 0x1000, src));
    num("keys", MF(0xf0000, 0x1000, src));
    num("src-not-heap", MF(0x4000000, 0x1000, 0x500000));
    w[0] = 0x55; w[0x3FF] = 0x66;
    num("win", MF(0x500000, 0x1000, 0));
    num("win-zeroed", w[0] | w[0x3FF]);
    num("win-src-ignored", MF(0x500000, 0x2000, 0x10001));
    w[0] = 0x55; plat(8, 0x500000, 0x1000, 0, 3);
    num("win-none-to-rw", MF(0x500000, 0x1000, 0));
    w[1] = 9; num("win-rw-again", w[0] + w[1]);
    num("win-first", MF(0x400000, 0x1000, 0));
    num("win-last", MF(0x20FF000, 0x1000, 0));
    num("win-straddle-end", MF(0x20FF000, 0x2000, src));
    num("win-straddle-start", MF(0x3FF000, 0x2000, src));
    uint64_t n = 0;
    for (; n < 512 && MF(0x4000000 + n * 0x1000, 0x1000, H->heap_base + 0x100000 + n * 0x1000) == 0; n++) ;
    num("live", n);
    num("limit", MF(0x4200000, 0x1000, src));
    num("limit-occupied", MF(0x4000000, 0x1000, src));
    num("unmap-one", UF(0x4000000, 0x1000, H->heap_base + 0x100000));
    num("again", MF(0x4000000, 0x1000, H->heap_base + 0x100000));
    w[2] = 3;
    num("uf-win", UF(0x500000, 0x1000, 0)); num("uf-win-zeroed", w[2]);
    num("uf-win-src-unal", UF(0x500000, 0x1000, 4));
    num("uf-win-size-unal", UF(0x500000, 0x800, 0));
    num("uf-wrap", UF(0x1000, 0xFFFFFFFFFFFFF000ull, 0));
    num("uf-win-straddle", UF(0x20FF000, 0x2000, 0));
#elif MODE == 46
    /* thread and directory ids, wait/wake */
#define THR(f, a) fnvnx_hcall1(H->f, a)
    uint64_t h1 = fnvnx_hcall5(H->thread_create, FNVNX_ADDR(worker), 77, 0x10000, 0, ~0ull);
    uint64_t h2 = fnvnx_hcall5(H->thread_create, FNVNX_ADDR(worker), 78, 0x10000, 0, ~0ull);
    num("h1-id", h1 != 0 && (h1 >> 16) != 0); num("h-distinct", h1 != h2);
    num("start-unknown", THR(thread_start, 0x12345)); num("start-zero", THR(thread_start, 0));
    num("join-unknown", THR(thread_join, 0x12345)); num("join-never-started", THR(thread_join, h2));
    num("prio-unknown", fnvnx_hcall2(H->thread_set_priority, 0x12345, 1)); num("prio-self", fnvnx_hcall2(H->thread_set_priority, 0, 3));
    num("cpu-unknown", fnvnx_hcall2(H->thread_set_cpu, 0x12345, 1)); num("cpu-self", fnvnx_hcall2(H->thread_set_cpu, 0, 2));
    num("cpu-negative", fnvnx_hcall2(H->thread_set_cpu, 0, ~0ull));
    num("cpu-bits40-62", fnvnx_hcall2(H->thread_set_cpu, 0, 0x7FFFFF0000000000ull));
    num("start", THR(thread_start, h1)); num("join", THR(thread_join, h1)); num("flag", flag);
    num("free", THR(thread_free, h1)); num("free-twice", THR(thread_free, h1));
    num("join-freed", THR(thread_join, h1)); num("start-freed", THR(thread_start, h1));
    num("prio-freed", fnvnx_hcall2(H->thread_set_priority, h1, 1)); num("act-freed", plat(2, h1, 1, 0, 2));
    num("free-unknown", THR(thread_free, 0x12345));
    uint64_t h3 = fnvnx_hcall5(H->thread_create, FNVNX_ADDR(worker), 79, 0x10000, 0, ~0ull);
    num("h3-new-generation", h3 != h1 && h3 != h2 && (h3 >> 16) != (h1 >> 16));
    THR(thread_start, h3);
    num("free-running", THR(thread_free, h3));
    while (flag != 79) fnvnx_hcall3(H->wait_on_address, FNVNX_ADDR(&flag), 77, ~0ull);
    num("detached-ran", flag);
    uint64_t d = fnvnx_hcall1(H->fs_dir_open, FNVNX_ADDR("sdmc:/"));
    char buf[16];
    num("dir-id", d != 0);
    num("dir-next-cap0", fnvnx_hcall3(H->fs_dir_next, d, FNVNX_ADDR(buf), 0));
    num("dir-next-cut", fnvnx_hcall3(H->fs_dir_next, d, FNVNX_ADDR(buf), 4)); num("dir-cut-bytes", buf[0] * 0x10000 + buf[2] * 0x100 + buf[3]);
    num("dir-next-2", fnvnx_hcall3(H->fs_dir_next, d, FNVNX_ADDR(buf), 16));
    num("dir-end", fnvnx_hcall3(H->fs_dir_next, d, FNVNX_ADDR(buf), 16));
    num("dir-unknown", fnvnx_hcall3(H->fs_dir_next, 0x12345, FNVNX_ADDR(buf), 16));
    num("dir-as-thread", THR(thread_join, d)); num("thread-as-dir", fnvnx_hcall3(H->fs_dir_next, h2, FNVNX_ADDR(buf), 16));
    num("dir-close", THR(fs_dir_close, d)); num("dir-next-closed", fnvnx_hcall3(H->fs_dir_next, d, FNVNX_ADDR(buf), 16));
    num("dir-close-again", THR(fs_dir_close, d)); num("dir-close-unknown", THR(fs_dir_close, 0x12345));
    num("dir-notdir", THR(fs_dir_open, FNVNX_ADDR("sdmc:/data.bin"))); num("dir-missing", THR(fs_dir_open, FNVNX_ADDR("sdmc:/nope")));
    num("wait-unal", fnvnx_hcall3(H->wait_on_address, FNVNX_ADDR(&flag) + 1, 0, 0));
    num("wait-unmapped", fnvnx_hcall3(H->wait_on_address, 0x30000000, 0, 0));
    plat(8, H->heap_base + 0x320000, 0x1000, 0, 3);
    num("wait-prot-none", fnvnx_hcall3(H->wait_on_address, H->heap_base + 0x320000, 0, 0));
    num("wake-unal", fnvnx_hcall2(H->wake_address, FNVNX_ADDR(&flag) + 2, 1));
    num("wake-unmapped", fnvnx_hcall2(H->wake_address, 0x30000000, 1));
    num("wake-prot-none", fnvnx_hcall2(H->wake_address, H->heap_base + 0x320000, 1));
    uint64_t w1 = fnvnx_hcall5(H->thread_create, FNVNX_ADDR(waiter), 1, 0x10000, 0, ~0ull);
    uint64_t w2 = fnvnx_hcall5(H->thread_create, FNVNX_ADDR(waiter), 2, 0x10000, 0, ~0ull);
    THR(thread_start, w1); THR(thread_start, w2);
    fnvnx_hcall1(H->sleep_ns, 1000000);
    num("wake-all", fnvnx_hcall2(H->wake_address, FNVNX_ADDR(&wf), 0x80000000ull));
    THR(thread_join, w1); THR(thread_join, w2);
    num("woken-all", wdone);
    uint64_t cnt = 0, last = 0, x;
    while ((x = THR(fs_dir_open, FNVNX_ADDR("sdmc:/"))) != 0) { cnt++; last = x; }
    num("dirs-until-full", cnt);
    num("thread-when-full", fnvnx_hcall5(H->thread_create, FNVNX_ADDR(worker), 1, 0x10000, 0, ~0ull));
    num("close-one", THR(fs_dir_close, last));
    num("thread-after-close", fnvnx_hcall5(H->thread_create, FNVNX_ADDR(worker), 1, 0x10000, 0, ~0ull) != 0);
#elif MODE == 47
    uint64_t b[8];
    for (int i = 0; i < 8; i++) b[i] = 0xABABABABABABABABull;
    num("a-submit-closed", fnvnx_hcall2(H->audio_submit, FNVNX_ADDR(b), 480));
    num("a-rate", fnvnx_hcall2(H->audio_open, 44100, 480));
    num("a-period0", fnvnx_hcall2(H->audio_open, 48000, 0)); num("a-period-big", fnvnx_hcall2(H->audio_open, 48000, 8193));
    num("a-open", fnvnx_hcall2(H->audio_open, 48000, 480));
    num("a-open-again", fnvnx_hcall2(H->audio_open, 44100, 1));
    num("a-submit-wrong", fnvnx_hcall2(H->audio_submit, FNVNX_ADDR(b), 481)); num("a-submit", fnvnx_hcall2(H->audio_submit, FNVNX_ADDR(b), 480));
    num("a-close", fnvnx_hcall0(H->audio_close)); num("a-close-again", fnvnx_hcall0(H->audio_close));
    num("a-submit-after", fnvnx_hcall2(H->audio_submit, FNVNX_ADDR(b), 480));
    num("a-reopen", fnvnx_hcall2(H->audio_open, 48000, 8192)); num("a-submit-8192", fnvnx_hcall2(H->audio_submit, FNVNX_ADDR(b), 8192));
    num("pad4", fnvnx_hcall2(H->pad_read, 4, FNVNX_ADDR(b)));
    num("pad-untouched", b[0] == 0xABABABABABABABABull && b[3] == 0xABABABABABABABABull);
    num("pad0", fnvnx_hcall2(H->pad_read, 0, FNVNX_ADDR(b))); num("pad0-zeroed", b[0] | b[1] | b[2] | b[3]);
    num("sleep-max", fnvnx_hcall1(H->sleep_ns, ~0ull));
#elif MODE == 48
    uint64_t v[1] = {0};
    fnvnx_hcall1(H->exit, plat(9, 0, 0, 0, 0) + v[0]);   /* DISPLAY_DOCKED: the exit code is the answer */
#elif MODE == 49
    /* sockets 16..33 with no network (every result the arguments alone fix), TZ_AT, the tz slot */
    num("sock-afnosupport", sk(16, 3, 1, 1, 0, 0, 0));
    num("sock-af-wide", sk(16, 3, 0x100000002ull, 1, 0, 0, 0));
    num("sock-af-neg", sk(16, 3, ~0ull, 1, 0, 0, 0));
    num("sock-type-4", sk(16, 3, 2, 4, 0, 0, 0));
    num("sock-type-0", sk(16, 3, 10, 0, 0, 0, 0));
    static const uint8_t SN[34] = {0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 3, 1, 2, 2, 2, 2, 5, 5, 5, 5, 3, 2, 2, 2, 1, 2, 4, 3};
    uint64_t mask = 0, hs[4] = {0, 3, 0x7FFFFFFFull, 1ull << 32};
    for (uint32_t fn = 17; fn <= 31; fn++) {
        if (fn == 26 || (fn >= 27 && fn <= 29)) continue;   /* 26 POLL has no handle first; 27..29 are not services */
        for (uint32_t i = 0; i < 4; i++) {
            uint64_t r = sk(fn, SN[fn], hs[i], 0, 0, 0, 0);
            if (r == NEG(9)) mask |= 1ull << fn; else { mask |= 1ull << 40; num("ebadf-bad", fn); }
        }
    }
    num("ebadf-mask", mask);
    FnvnxWirePollfd pf[3] = {{-1, 1, 0xFFFF}, {5, 1, 0xFFFF}, {7, 5, 0xFFFF}};
    num("poll-big", sk(26, 3, FNVNX_ADDR(pf), 0x1000001, 0, 0, 0));
    num("poll-null", sk(26, 3, 0, 1, 0, 0, 0));
    uint64_t t0 = fnvnx_hcall0(H->wall_time_us);
    num("poll-nfds0-t0", sk(26, 3, 0, 0, 0, 0, 0));
    num("poll-nfds0-null-t7", sk(26, 3, 0, 0, 7, 0, 0));
    num("poll-wait-us", fnvnx_hcall0(H->wall_time_us) - t0);
    num("poll-nval", sk(26, 3, FNVNX_ADDR(pf), 3, 0xFFFFFFFFull - 1000, 0, 0));
    num("poll-rev0", pf[0].revents); num("poll-rev1", pf[1].revents); num("poll-rev2", pf[2].revents);
    num("poll-ev2", pf[2].events);
    FnvnxWirePollfd pn[2] = {{-1, 1, 0xFFFF}, {-5, 4, 0xFFFF}};
    t0 = fnvnx_hcall0(H->wall_time_us);
    num("poll-neg-t0", sk(26, 3, FNVNX_ADDR(pn), 2, 0, 0, 0));
    num("poll-neg-t10", sk(26, 3, FNVNX_ADDR(pn), 2, 10, 0, 0));
    num("poll-neg-us", fnvnx_hcall0(H->wall_time_us) - t0);
    num("poll-neg-rev", pn[0].revents | pn[1].revents);
    uint32_t zw[4] = {0xAAAAAAAA, 0xAAAAAAAA, 0xAAAAAAAA, 0xAAAAAAAA};
    num("tz-0", plat(34, 0, FNVNX_ADDR(zw), 0, 2));
    num("tz-off", zw[0]); num("tz-dst", zw[1]);
    num("tz-abbr", zw[2]);   /* "UTC\0" little endian */
    num("tz-abbr-rest", zw[3]);
    num("tz-max", plat(34, 0x7FFFFFFFFFFFFFFFull, FNVNX_ADDR(zw), 0, 2));
    zw[0] = 0xAAAAAAAA;
    num("tz-1970", plat(34, 0, FNVNX_ADDR(zw), 0, 2)); num("tz-1970-off", zw[0]);
    num("tz-2100-last", plat(34, 4133980799ull, FNVNX_ADDR(zw), 0, 2));
    zw[0] = 0xAAAAAAAA;
    num("tz-2101", plat(34, 4133980800ull, FNVNX_ADDR(zw), 0, 2)); num("tz-2101-untouched", zw[0]);   /* the label is historic: the offset 0 is written */
    num("tz-u64max", plat(34, ~0ull, FNVNX_ADDR(zw), 0, 2));
    num("tz-negative-main", plat(34, 0x8000000000000000ull, FNVNX_ADDR(zw), 0, 2));
    num("tz-null-out", plat(34, 5, 0, 0, 2));
    num("tz-slot", fnvnx_hcall0(H->tz_offset_minutes));
#elif MODE == 50
    /* THREAD_SELF and the thread_start / thread_join / thread_activity edge cases */
    uint64_t s1 = plat(35, 0, 0, 0, 0), s2 = plat(35, 0, 0, 0, 0);
    num("self-id", s1 != 0); num("self-distinct", s1 != s2);
    num("self-start", fnvnx_hcall1(H->thread_start, s1));
    num("self-join", fnvnx_hcall1(H->thread_join, s1));
    num("self-prio", fnvnx_hcall3(H->thread_set_priority, s1, 5, 0));
    num("self-resume", plat(2, s1, 0, 0, 2));
    num("self-free", fnvnx_hcall1(H->thread_free, s2));
    num("self-free-again", fnvnx_hcall1(H->thread_free, s2));
    num("self-use-freed", plat(2, s2, 0, 0, 2));
    uint64_t w = fnvnx_hcall5(H->thread_create, FNVNX_ADDR(self_worker), s1, 0x10000, 0, ~0ull);
    fnvnx_hcall1(H->thread_start, w);
    while (sflag2 == 0) fnvnx_hcall3(H->wait_on_address, FNVNX_ADDR(&sflag2), 0, ~0ull);
    mainran = 1;
    fnvnx_hcall1(H->thread_join, w);
    num("pause-by-worker", tcount);   /* 100 + pause result 0 + 10 * main-ran-while-paused 0 + 1000 * resume result 0 */
    uint64_t c = fnvnx_hcall5(H->thread_create, FNVNX_ADDR(count_worker), 0, 0x10000, 0, ~0ull);
    num("act-new", plat(2, c, 1, 0, 2));
    num("start", fnvnx_hcall1(H->thread_start, c));
    num("start-again", fnvnx_hcall1(H->thread_start, c));
    fnvnx_hcall1(H->thread_join, c);
    num("start-finished", fnvnx_hcall1(H->thread_start, c));
    num("act-finished", plat(2, c, 1, 0, 2));
    num("count", tcount);   /* the entry ran once: 100 + 1 */
#elif MODE == 51
    FnvnxWirePollfd pf = {-1, 1, 0};
    sk(26, 3, FNVNX_ADDR(&pf), 1, ~0ull, 0, 0);   /* nothing can become ready and the timeout is forever */
    say("after the poll\n");
#elif MODE == 52
    /* the numbers that hostapi.h no longer declares (SHUTDOWN, GETSOCKNAME, GETPEERNAME, GAI_OPEN, GAI_FETCH) answer NO_SERVICE for any nargs */
    num("fn27", plat(27, 0, 0, 0, 2)); num("fn28", plat(28, 0, 0, 0, 2)); num("fn29", plat(29, 0, 0, 0, 0));
    num("fn32", plat(32, 0, 0, 0, 4)); num("fn33", plat(33, 0, 0, 0, 3));
#elif MODE == 53
    uint32_t zo[4];
    num("tz-below", plat(34, 0x7FFFFFFFFFFFFFFFull, FNVNX_ADDR(zo), 0, 2));
    num("tz-above", plat(34, 1ull << 63, FNVNX_ADDR(zo), 0, 2));
#elif MODE == 54
    uint64_t me = plat(35, 0, 0, 0, 0);
    num("self-pause", plat(2, me, 1, 0, 2));   /* the kernel: a thread is not paused through its own handle */
    num("self-resume", plat(2, me, 0, 0, 2));
    say("after the pause\n");
#elif MODE == 55
    uint64_t me = plat(35, 0, 0, 0, 0);
    uint64_t w = fnvnx_hcall5(H->thread_create, FNVNX_ADDR(rec_worker), 0, 0x10000, 0, ~0ull);
    uint64_t c = fnvnx_hcall5(H->thread_create, FNVNX_ADDR(count_worker), 0, 0x10000, 0, ~0ull);
    num("not-started-pause", plat(2, c, 1, 0, 2));
    fnvnx_hcall1(H->thread_start, c);
    num("resume-running", plat(2, c, 0, 0, 2));       /* not paused */
    num("pause", plat(2, c, 1, 0, 2));
    num("pause-paused", plat(2, c, 1, 0, 2));         /* already paused */
    num("resume", plat(2, c, 0, 0, 2));
    num("resume-again", plat(2, c, 0, 0, 2));
    fnvnx_hcall1(H->thread_join, c);
    num("finished", plat(2, c, 1, 0, 2));
    num("me-pause", plat(2, me, 1, 0, 2));
    num("me-resume", plat(2, me, 0, 0, 2));
    num("me-zero", plat(2, 0, 1, 0, 2));
    fnvnx_hcall1(H->thread_start, w);
    fnvnx_hcall1(H->thread_join, w);
    num("rec-finished", plat(2, wrec, 1, 0, 2));      /* the record of a thread that has ended */
    num("rec-finished-resume", plat(2, wrec, 0, 0, 2));
    num("no-record", plat(2, 0x7777, 1, 0, 2));
#elif MODE == 56
    static FnvnxWireSockaddr to, lo;
    static FnvnxWireAddrinfo recs[3];
    static char buf[4];
    uint32_t ec = 0x77;
    uint64_t h1 = sk(16, 3, 2, 1, 0, 0, 0), h2 = sk(16, 3, 2, 2, 17, 0, 0);
    num("h1", h1); num("h2", h2);
    num("close1", sk(17, 1, h1, 0, 0, 0, 0));
    uint64_t h3 = sk(16, 3, 2, 1, 6, 0, 0);
    num("h3", h3);                                    /* the lowest free number again */
    num("getfl", sk(30, 1, h2, 0, 0, 0, 0));
    num("setfl", sk(31, 2, h2, 0x800, 0, 0, 0));
    num("getfl-nb", sk(30, 1, h2, 0, 0, 0, 0));
    to.family = 2; to.port = 80; to.addr[0] = 8; to.addr[1] = 8; to.addr[2] = 8; to.addr[3] = 8;
    num("connect-ext", sk(21, 2, h3, FNVNX_ADDR(&to), 0, 0, 0));
    num("sendto-ext", sk(22, 5, h2, FNVNX_ADDR(buf), 1, 0, FNVNX_ADDR(&to)));
    num("recv-nb", sk(23, 5, h2, FNVNX_ADDR(buf), 1, 0, 0));
    lo.family = 2; lo.port = 8080; lo.addr[0] = 127; lo.addr[3] = 1;
    num("bind-lo", sk(18, 2, h3, FNVNX_ADDR(&lo), 0, 0, 0));
    num("close3", sk(17, 1, h3, 0, 0, 0, 0));
    num("closed-getfl", sk(30, 1, h3, 0, 0, 0, 0));
    num("tz-last", plat(34, 4133980799ull, FNVNX_ADDR(recs), 0, 2));
#elif MODE == 35
    fnvnx_hcall1(H->exit, 0x5200002A);   /* fnvnx_reach_violation(42) */
#elif MODE == 36
    fnvnx_hcall1(H->exit, 0x5100002A);   /* an ordinary non-zero code */
#elif MODE == 57
    /* TEXT_ENTRY (36): argument checks, entered, entered with init, cancelled (scenario texts: "Ab" + U+1F600, "x", then none) */
    static uint16_t ou[8]; static uint32_t nn;
    for (int i = 0; i < 8; i++) ou[i] = 0x7777;
    nn = 0x55555555;
    uint64_t B = NEG(0x1759);
    num("te-max0", sk(36, 5, 0, 0, 0, FNVNX_ADDR(ou), FNVNX_ADDR(&nn)) == B);
    num("te-max501", sk(36, 5, 501, 0, 0, FNVNX_ADDR(ou), FNVNX_ADDR(&nn)) == B);
    num("te-maxhuge", sk(36, 5, 0x100000001ull, 0, 0, FNVNX_ADDR(ou), FNVNX_ADDR(&nn)) == B);
    num("te-out-null", sk(36, 5, 8, 0, 0, 0, FNVNX_ADDR(&nn)) == B);
    num("te-n-null", sk(36, 5, 8, 0, 0, FNVNX_ADDR(ou), 0) == B);
    num("te-bad-untouched", ou[0] == 0x7777 && nn == 0x55555555);
    static const uint16_t in[2] = {'q', 'r'};
    num("te-entered", sk(36, 5, 8, FNVNX_ADDR(in), 2, FNVNX_ADDR(ou), FNVNX_ADDR(&nn)));
    num("te-n", nn);
    num("te-u0", ou[0]); num("te-u1", ou[1]); num("te-u2", ou[2]); num("te-u3", ou[3]); num("te-nul", ou[4]); num("te-rest", ou[5]);
    for (int i = 0; i < 8; i++) ou[i] = 0x7777;
    nn = 0x55555555;
    num("te-entered2", sk(36, 5, 500, 0, 0, FNVNX_ADDR(ou), FNVNX_ADDR(&nn)));
    num("te-n2", nn); num("te-2u0", ou[0]); num("te-2nul", ou[1]); num("te-2rest", ou[2]);
    for (int i = 0; i < 8; i++) ou[i] = 0x7777;
    nn = 0x55555555;
    num("te-cancel", sk(36, 5, 8, 0, 0, FNVNX_ADDR(ou), FNVNX_ADDR(&nn)));
    num("te-cancel-untouched", ou[0] == 0x7777 && nn == 0x55555555);
    num("te-cancel-again", sk(36, 5, 8, 0, 0, FNVNX_ADDR(ou), FNVNX_ADDR(&nn)));
#elif MODE == 58
    /* TEXT_ENTRY: one call, max_len 3; the scenario text is longer (4 units): a scenario error */
    static uint16_t ou[8]; static uint32_t nn;
    num("te-long", sk(36, 5, 3, 0, 0, FNVNX_ADDR(ou), FNVNX_ADDR(&nn)));
    num("te-after-long", 1);
#elif MODE == 59
    /* TEXT_ENTRY: a text of exactly max_len units fits (2 units: one scalar above U+FFFF), out has room for max_len + 1 */
    static uint16_t ou[3] = {0x7777, 0x7777, 0x7777}; static uint32_t nn;
    num("te-fit", sk(36, 5, 2, 0, 0, FNVNX_ADDR(ou), FNVNX_ADDR(&nn)));
    num("te-fit-n", nn); num("te-fit-u0", ou[0]); num("te-fit-u1", ou[1]); num("te-fit-nul", ou[2]);
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
    num("map", fnvnx_hcall3(H->map_fixed, 0x4000000, 0x10000, H->heap_base));
    volatile uint32_t *p = (volatile uint32_t *)0x4000000;
    num("carried", p[0] ^ p[0x3FFF]);
    num("zeroed-rest", p[1] | p[0x3FFE]);
#if MODE == 4
    num("touch-src", hp[0]);   /* the given-away block: the run must end here */
    say("after the fault\n");
#endif
    p[0] = 0x1234; p[0x3FFF] = 0x5678;
    num("readback", p[0] + p[0x3FFF]);
    num("overlap", fnvnx_hcall3(H->map_fixed, 0x4000000, 0x1000, H->heap_base + 0x20000));
    num("keys", fnvnx_hcall3(H->map_fixed, 0xf0000, 0x1000, H->heap_base + 0x20000));
    num("src-unaligned", fnvnx_hcall3(H->map_fixed, 0x4010000, 0x1000, H->heap_base + 0x20001));
    num("src-given", fnvnx_hcall3(H->map_fixed, 0x4010000, 0x1000, H->heap_base));
    num("src-outside", fnvnx_hcall3(H->map_fixed, 0x4010000, 0x1000, 0x10000));
    num("src-past-end", fnvnx_hcall3(H->map_fixed, 0x4010000, 0x2000, H->heap_base + H->heap_size - 0x1000));
    uint64_t a[3] = {0x4000000, 0x10000, 1};
    num("protect", fnvnx_hcall4(H->ext_call, 11, 8, FNVNX_ADDR(a), 3));
    uint64_t u[3] = {0x3000000, 0x1000, 1};
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
    uint64_t w[3] = {0x4000000, 0x10000, 3};
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


def parse_totality(path):
    """the header's TOTALITY TABLE block parsed: (services {fn: (NAME, n or None)}, slots [names]). Rows are `<fn> <NAME> n=<k>`, the
    NOT SERVED row, and the combined sockets row (names, then `n = ... (SOCK_SOCKET 3, CLOSE 1, ...)`); the numbers are cross-checked
    against the `#define FNVNX_PLAT_<NAME> <fn>u` lines; the slots are the uint64 fields of struct FnvnxHostApi."""
    txt = open(path).read()
    block = txt[txt.index("TOTALITY TABLE BEGIN"):txt.index("TOTALITY TABLE END")]
    lines = [re.sub(r"^\s*\*\s?", "", l) for l in block.splitlines()]
    rows = {}
    for l in lines:
        m = re.match(r"\s*(\d+) ([A-Z_0-9]+)\s+n=(\d+)\b", l)
        if m:
            rows[int(m.group(1))] = (m.group(2), int(m.group(3)))
        m = re.match(r"\s*(\d+) ([A-Z_0-9]+)\s+NOT SERVED", l)
        if m:
            rows[int(m.group(1))] = (m.group(2), None)
    body = "\n".join(lines)
    m = re.search(r"16\.\.26 30 31 (.*?)\bn = the count[^(]*\((.*?)\)\s*\n", body, re.S)
    names = re.findall(r"\bSOCK_[A-Z_]+", m.group(1))
    counts = {("SOCK_" + a if not a.startswith("SOCK_") else a): int(b) for a, b in re.findall(r"([A-Z_]+) (\d+)", m.group(2))}
    assert len(names) == 13 and set(counts) == set(names), (names, counts)
    for f, n in zip(list(range(16, 27)) + [30, 31], names):
        rows[f] = (n, counts[n])
    defs = {int(v): n for n, v in re.findall(r"#define FNVNX_PLAT_([A-Z_0-9]+) (\d+)u", txt) if n not in ("SOCK_FIRST", "SOCK_LAST")}
    assert {f: r[0] for f, r in rows.items()} == defs, "table rows and #define FNVNX_PLAT_* differ"
    st = txt[txt.index("typedef struct FnvnxHostApi {"):txt.index("} FnvnxHostApi;")]
    slots = re.findall(r"uint64_t (\w+);", st)[4:]   # after guest_base, guest_size, heap_base, heap_size
    return rows, slots


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


def build(mode, more=""):
    """crt0.s + test C (GUEST with -DMODE) -> (ELF bytes, ELF path); importable: test_boot.py uses it. `more`: C text of further
    `#if MODE == n ... #elif` branches of another test file, placed before the MODE 6 branch (the guest source is this argument's
    function; the module's GUEST is never changed)"""
    tag = "x" if more else ""
    src, ld = os.path.join(TMP, "g%s.c" % tag), os.path.join(TMP, "guest.ld")
    if not os.path.exists(ld):
        shutil.copy(os.path.join(GUEST_DIR, "guest.ld"), ld)
        sh([CC] + CFLAGS[:3] + ["-c", os.path.join(GUEST_DIR, "crt0.s"), "-o", os.path.join(TMP, "crt0.o")])
    if not os.path.exists(src):
        text = GUEST
        if more:
            assert text.count("#if MODE == 6") == 1
            text = text.replace("#if MODE == 6", more, 1)
        open(src, "w").write(text)
    # crt0 header: the +20 size and +24 flags are filled as elf2nso does
    obj, elf = os.path.join(TMP, "g%s%d.o" % (tag, mode)), os.path.join(TMP, "g%s%d.elf" % (tag, mode))
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
    # exe and mod1 lie inside the host's window (0x400000..0x2100000: map_fixed only zeroes there); mod2 is outside it (map_fixed
    # moves a heap block there); keys stays never-mapped
    rows = [("keys", 0xf0000, 0x200000), ("exe", 0x400000, 0x1409000), ("mod1", 0x1410000, 0x1500000), ("mod2", 0x4000000, 0x4200000)]
    plan = bootlayout.plan(bootlayout.parse_elf(elf_bytes), rows, 4 << 20)
    return plan


def run(elf_bytes, root, watch=None, svc=None, **kw):
    svc = svc or boothost.Services(root)
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
    built = {m: build(m) for m in (0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, *range(12, 22), *range(30, 42), 44, 45, 46, 47, 48, *range(49, 57), 57, 58, 59)}   # MODE 6 (boot CLI guest) is built by test_boot.py
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
    for k, v in (("marker", "2a"), ("map", "0"), ("readback", "68ac"), ("overlap", "f59"), ("keys", "da01"),
                 ("carried", str(hex(0xabcd0001 ^ 0xabcd0002))[2:]), ("zeroed-rest", "0"), ("src-unaligned", "1759"),
                 ("src-given", "d401"), ("src-outside", "d401"), ("src-past-end", "d401"),
                 ("protect", "0"), ("protect-unmapped", "d401"), ("protect-image", "d401"),
                 ("protect-given-src", "d401"), ("protect-heap-none", "0"), ("protect-heap-rw", "0"),
                 ("heap-rw-works", "5"), ("protect-bad-size", "ca01"), ("protect-bad-addr", "cc01"), ("unprotect", "0"), ("tickdiff", "3e8"),
                 ("thread", "10000"), ("woken", "0"), ("flag", "4d"), ("pread", "8"), ("rand-calls-equal", "1")):
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
    case("fault record", f.get("kind") == "write-protected" and f.get("addr") == 0x4000000 and f.get("tid") == 1
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

    class Ext9(boothost.Services):
        """module 9 registered: fn 99 is a service it cannot answer; every call is recorded"""
        calls = []

        def ext_9(self, fn, a):
            self.calls.append((fn, list(a)))
            if fn == 99:
                raise bootmachine.ExtMissing(9, fn)
            return 1000 * fn + 10 * len(a) + (a[0] if a else 0)

    re_, _, _, _ = run(built[1][0], root, svc=Ext9(root))
    case("a registered ext module whose handler raises ExtMissing ends the run", re_["reason"] == "ext-module-missing"
        and re_["ext"] == {"module": 9, "fn": 99}, re_)
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
        blocked = [(r["lo"], r["hi"]) for r in plan0["regions"].values()] + [(f["lo"], f["hi"]) for f in plan0["free"]] + taken \
            + [bootmachine.WINDOW]
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
         vals["commit-outside"] == 0x1759 and vals["commit-past-end"] == 0x1759 and vals["commit-size0"] == 0x1759, textv)
    case("vm: VM_PROTECT on an uncommitted page: nonzero", vals.get("protect-uncommitted") == 0xD401, textv)
    case("vm: VM_PROTECT on a committed page works, read-only reads, rw writes", vals.get("protect-committed") == 0
         and vals.get("ro-read") == 0x33 and vals.get("protect-rw") == 0 and vals.get("rw-write") == 0x44, textv)
    case("vm: VM_PROTECT with an X bit is refused", vals.get("protect-x") == 0xD801, textv)
    case("vm: recommit after decommit is RW and zero whatever the protection was",
         vals.get("recommit-protected") == 0 and vals.get("zero-rw-after-protect") == 0, textv)
    case("vm: decommit of part of an extent zeroes it and it stays accessible (host_vm.inc)",
         vals.get("partial-decommit") == 0 and vals.get("partial-zero-writable") == 0 and vals.get("partial-write") == 0x88, textv)
    case("vm: map_fixed over a committed page is occupied (AlreadyInitialized), over an uncommitted reserved page outside the alias-code region 0xDA01",
         vals.get("mapfixed-over-resv") == 0xF59 and vals.get("mapfixed-over-resv-free") == 0xDA01, textv)
    case("vm: release frees the whole reservation; a non-base address is refused",
         vals.get("release") == 0 and vals.get("release-again") == 0x1759 and vals.get("release-inner") == 0x1759, textv)
    case("vm: reserve after release gives the same address", A2 == A, (hex(A), hex(A2)))
    case("vm: hints: overlap, inside the exe or a module range, above 4 GiB, image, size 0 fail; a hint in a gap or free address is exact (64 KiB rounded)",
         all(vals.get(k) == 0 for k in ("hint-overlap", "hint-exe", "hint-module", "hint-above-4g", "hint-in-image", "hint-size0",
                                        "hint-ok-overlap")) and vals.get("hint-ok") == 0x20000000
         and vals.get("hint-gap") == 0x3000000, textv)
    kinds = [e["kind"] for e in rv["events"]]
    case("vm: every refusal is an event with its result", kinds == [
        "vm_commit-refused"] * 3 + ["vm_protect-refused"] * 2 + ["map_fixed-refused"] * 2 + ["vm_release-refused"] * 2
        + ["vm_reserve-refused"] * 7 + ["thread_activity-refused"] * 3, kinds)
    case("platform: thread_activity: unknown id -> NotFound 0x1359, tid 0 -> BadInput 0x1759, new thread -> BadInput 0x1759, pause holds the worker, resume runs it",
         vals.get("tact-nothread") == 0x1359 and vals.get("tact-tid0") == 0x1759 and vals.get("tact-new") == 0x1759
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
    case("platform: a socket service with its exact nargs (no result in this environment) ends the run as ext-module-missing",
         rf11["reason"] == "ext-module-missing" and rf11["ext"] == {"module": 11, "fn": 16}, rf11)
    def kv(lines):
        return {l.split("=")[0]: l.split("=")[1] for l in lines if "=" in l}

    r49, l49 = run(built[49][0], root)[:2]
    k49 = kv(l49)
    M = bootmachine.U64 + 1
    ne = lambda e: "%x" % (M - e)
    ebadf_fns = [f for f in range(17, 32) if f != 26 and not 27 <= f <= 29]
    want49 = {"sock-afnosupport": ne(97), "sock-af-wide": ne(97), "sock-af-neg": ne(97), "sock-type-4": ne(22), "sock-type-0": ne(22),
              "ebadf-mask": "%x" % sum(1 << f for f in ebadf_fns),
              "poll-big": ne(22), "poll-null": ne(14), "poll-nfds0-t0": "0", "poll-nfds0-null-t7": "0", "poll-wait-us": "1b58",
              "poll-nval": "2", "poll-rev0": "0", "poll-rev1": "20", "poll-rev2": "20", "poll-ev2": "5",
              "poll-neg-t0": "0", "poll-neg-t10": "0", "poll-neg-us": "2710", "poll-neg-rev": "0",
              "tz-0": "0", "tz-off": "0", "tz-dst": "0", "tz-abbr": "435455", "tz-abbr-rest": "0", "tz-max": "0", "tz-1970": "0", "tz-1970-off": "0", "tz-2100-last": "0", "tz-2101": "0", "tz-2101-untouched": "0",
              "tz-u64max": "0", "tz-negative-main": "0",
              "tz-null-out": ne(14), "tz-slot": "0"}
    bad49 = {n: (k49.get(n), v) for n, v in want49.items() if k49.get(n) != v}
    case("sockets and TZ_AT (guest): every result the arguments fix with no socket open: errno classes, -EBADF for each handle-first service, "
         "POLL waits its timeout in virtual time and answers POLLNVAL (%d values)" % len(want49), r49["reason"] == "exit" and not bad49
         and "ebadf-bad" not in k49, (bad49, r49))
    r50, l50 = run(built[50][0], root)[:2]
    k50 = kv(l50)
    BI, NF = "1759", "1359"
    want50 = {"self-id": "1", "self-distinct": "1", "self-start": BI, "self-join": BI, "self-prio": "0", "self-resume": "f401",
              "self-free": "0", "self-free-again": NF, "self-use-freed": NF, "pause-by-worker": "64", "act-new": BI, "start": "0",
              "start-again": BI, "start-finished": BI, "act-finished": BI, "count": "65"}
    bad50 = {n: (k50.get(n), v) for n, v in want50.items() if k50.get(n) != v}
    case("THREAD_SELF (guest): a started record (start/join BadInput), distinct ids, free ends only the record, a paused-woken main thread "
         "stays off until resumed; start of a started or finished thread BadInput with state kept, activity of a new or finished thread BadInput",
         r50["reason"] == "exit" and not bad50, (bad50, r50))
    r51 = run(built[51][0], root)[:2]
    case("POLL with nothing to wait for and timeout -1: the run ends as deadlock, never returns", r51[0]["reason"] == "deadlock"
         and "after the poll" not in r51[1], r51[0])
    r52, l52 = run(built[52][0], root)[:2]
    case("the numbers 27, 28, 29, 32 and 33, which hostapi.h no longer declares, answer NO_SERVICE (guest, any nargs)",
         r52["reason"] == "exit" and kv(l52) == {n: "ffffffffffffff02" for n in ("fn27", "fn28", "fn29", "fn32", "fn33")}, (r52, l52))
    r53, l53 = run(built[53][0], root)[:2]
    case("TZ_AT at INT64_MAX and INT64_MIN: the fixed UTC zone answers every instant", r53["reason"] == "exit"
         and kv(l53) == {"tz-below": "0", "tz-above": "0"}, (r53, l53))
    r54, l54 = run(built[54][0], root)[:2]
    case("THREAD_ACTIVITY on the calling thread's own THREAD_SELF record: ResultBusy 0xF401 for pause and resume, the run goes on",
         r54["reason"] == "exit" and kv(l54) == {"self-pause": "f401", "self-resume": "f401"} and "after the pause" in l54, (r54, l54))
    r55, l55 = run(built[55][0], root)[:2]
    k55 = kv(l55)
    want55 = {"not-started-pause": "1759", "resume-running": "fa01", "pause": "0", "pause-paused": "fa01", "resume": "0", "resume-again": "fa01",
              "finished": "1759", "me-pause": "f401", "me-resume": "f401", "me-zero": "1759", "rec-finished": "1759", "rec-finished-resume": "1759",
              "no-record": "1359"}
    case("THREAD_ACTIVITY: new / finished thread or record BadInput, resume of a running and pause of a paused thread 0xFA01, own record 0xF401, "
         "unknown id NotFound (%d values)" % len(want55), r55["reason"] == "exit" and k55 == want55, (k55, r55))
    r56, l56 = run(built[56][0], root)[:2]
    k56 = kv(l56)
    want56 = {"h1": "3", "h2": "4", "close1": "0", "h3": "3", "getfl": "2", "setfl": "0", "getfl-nb": "802", "connect-ext": ne(101),
              "sendto-ext": ne(101), "recv-nb": ne(11), "bind-lo": "0", "close3": "0", "closed-getfl": ne(9), "tz-last": "0"}
    case("sockets (guest): handles, flag state, ENETUNREACH, loopback bind "
         "(%d values)" % len(want56), r56["reason"] == "exit" and k56 == want56, ({n: (k56.get(n), v) for n, v in want56.items() if k56.get(n) != v}, r56))
    sw = open(os.path.join(GUEST_DIR, "fnvnx", "sockwire.h")).read()
    lst = lambda name: [int(v, 0) for v in re.findall(r"X\(\w+, (\w+)\)", re.search(r"#define " + name + r"\(X\)((?:[^\n]*\\\n)*[^\n]*)", sw).group(1))]
    nums = dict(re.findall(r"#define FNVNX_(WE_\w+|WEAI_\w+) (\d+)", sw))
    eai = dict(re.findall(r"X\(EAI_(\w+), (\d+)\)", sw))
    case("machine's wire constants equal the fixture sockwire.h (errno numbers, AF and type lists, AI mask, EAI numbers, POLLNVAL)",
         tuple(lst("FNVNX_WIRE_AF_LIST")) == bootmachine.WIRE_AF and tuple(lst("FNVNX_WIRE_SOCKTYPE_LIST")) == bootmachine.WIRE_SOCKTYPE
         and functools.reduce(lambda x, y: x | y, lst("FNVNX_WIRE_AI_LIST")) == bootmachine.WIRE_AI_MASK
         and int(nums["WE_INVAL"]) == bootmachine.WE_INVAL and int(nums["WE_FAULT"]) == bootmachine.WE_FAULT
         and int(nums["WE_OVERFLOW"]) == bootmachine.WE_OVERFLOW
         and (int(eai["BADFLAGS"]), int(eai["NONAME"]), int(eai["FAMILY"]), int(eai["SOCKTYPE"])) == (
             bootmachine.WEAI_BADFLAGS, bootmachine.WEAI_NONAME, bootmachine.WEAI_FAMILY, bootmachine.WEAI_SOCKTYPE)
         and "X(EBADF, 9)" in sw and "X(EAFNOSUPPORT, 97)" in sw and int(re.search(r"FNVNX_WPOLLNVAL (0x\w+)", sw).group(1), 16) == bootmachine.WPOLLNVAL,
         (nums, eai))

    class NoMsg(boothost.Services):
        plat_message = None

    try:
        bootmachine.Machine(elf0, plan0, NoMsg(root))
        ok, msg = False, "no error"
    except bootmachine.MachineError as e:
        ok, msg = "message" in str(e), str(e)
    case("a header-defined platform fn nobody implements raises at construction", ok, msg)
    case("every served non-socket fn of module 11 (1..15, 34, 35, 36) resolves", sorted(bootmachine.Machine(elf0, plan0, boothost.Services(root)).plat) == [*range(1, 16), 34, 35, 36])

    class NoSec(boothost.Services):
        plat_section_map = None

    try:
        bootmachine.Machine(elf0, plan0, NoSec(root))   # services may override a fn; None means "not there": the machine's own serves it
        ok = True
    except bootmachine.MachineError:
        ok = False
    case("section fns are served by the machine itself", ok)
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
               "vm-commit": "d401", "vm-commit-wide": "d401", "vm-commit-B": "1759", "vm-decommit-B": "d401", "vm-decommit": "d401", "vm-protect": "d401",
               "vm-protect-B": "d401", "vm-release": "d401", "view-intact": "1", "mf-B": "f59", "mf-A": "f59", "resv2-clear": "1",
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
         tail[:1] == [("section_map-refused", 0xD401)] and tail[1:9] == [("vm_commit-refused", 0xD401)] * 2 + [("vm_commit-refused", 0x1759)]
         + [("vm_decommit-refused", 0xD401)] * 2 + [("vm_protect-refused", 0xD401)] * 2 + [("vm_release-refused", 0xD401)]
         and tail.count(("map_fixed-refused", 0xF59)) == 2 and tail.count(("section_unmap-refused", 0xD401)) == 4
         and tail.count(("section_free-refused", 0xE401)) == 2 and ("section_map-refused", 0xE401) in tail, tail)
    # UNMAP_FIXED (module 11 fn 15)
    r37, l37 = run(built[37][0], root)[:2]
    k = kv(l37)
    want37 = {"map": "0", "carried": "1234", "u-wrong-src": "d401", "u-wrong-addr": "d401", "u-wrong-size": "d401", "u-addr-unal": "cc01",
              "u-src-unal": "cc01", "u-size0": "ca01", "u-size-unal": "ca01", "still-mapped": "1", "ok": "0", "src-bytes": "1",
              "src-writable": "77", "twice": "d401", "map-again": "0", "again-bytes": "1", "src-given-again": "d401", "ok-again": "0"}
    bad = {n: (k.get(n), v) for n, v in want37.items() if k.get(n) != v}
    case("unmap_fixed: exact (addr, size, src) pairs undo the map_fixed, src readable again with the written bytes, "
         "errors 0xD401 / 0xCC01 / 0xCA01, twice refused, map again works", r37["reason"] == "exit" and not bad, (bad, r37, l37))
    ev37 = [(e["kind"], e["result"]) for e in r37["events"]]
    case("unmap_fixed: refusals are events with their codes", ev37 == [("unmap_fixed-refused", c) for c in (
        0xD401, 0xD401, 0xD401, 0xCC01, 0xCC01, 0xCA01, 0xCA01)] + [("unmap_fixed-refused", 0xD401), ("map_fixed-refused", 0xD401)], ev37)
    r38, l38 = run(built[38][0], root)[:2]
    case("unmap_fixed: the unmapped addr faults", r38["reason"] == "fault" and r38["fault"]["kind"] == "read-unmapped"
         and r38["fault"]["addr"] == 0x4000000 and "ok=0" in l38 and "after the fault" not in l38, (r38, l38))
    r39, l39 = run(built[39][0], root)[:2]
    k = kv(l39)
    case("unmap_fixed: pages that are read-only / no access need no restore: succeeds, the bytes and RW come back",
         r39["reason"] == "exit" and k.get("ok") == "0" and k.get("src-bytes") == "1" and k.get("src-rw") == "5", (r39, l39))
    case("unmap_fixed: two identical runs give identical run() dicts", r37 == run(built[37][0], root)[0])
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

    # ---- the header's TOTALITY TABLE (parsed from the fixture) against the machine
    rows, hslots = parse_totality(os.path.join(GUEST_DIR, "hostapi.h"))
    SERVED = list(range(1, 27)) + [30, 31, 34, 35, 36]
    mach0 = bootmachine.Machine(elf0, plan0, boothost.Services(root))
    case("header: 31 services of module 11 and 50 slots parsed", len(rows) == 31 and sorted(rows) == SERVED
         and len(hslots) == 50, (len(rows), len(hslots)))
    case("slots: the header's struct order is bootmachine.SLOTS, each has an h_<slot> and a handler", hslots == bootmachine.SLOTS
         and all(callable(getattr(boothost.Services, "h_" + n, None)) and n in mach0.handlers for n in hslots), hslots)
    served_decl = {f for f in rows if f in mach0.plat or f in bootmachine.SOCK_SERVICES}
    case("services: every declared service is served by the machine (the sockets by Machine._sock), none extra",
         served_decl == set(rows) and set(mach0.plat) | set(bootmachine.SOCK_SERVICES) == set(rows) and not set(mach0.plat) & set(bootmachine.SOCK_SERVICES),
         (sorted(set(rows) - served_decl), sorted(set(mach0.plat) & set(bootmachine.SOCK_SERVICES))))
    case("services: served = 1..26, 30, 31, 34..36 (31 of 31)", sorted(served_decl) == SERVED and not hasattr(bootmachine, "UNSERVED"),
         sorted(served_decl))
    names_ok = all(rows[f][0].lower() == n and rows[f][1] in (None, need) for f, (n, need) in
                   {**bootmachine.PLATFORM_FNS, **bootmachine.SOCK_SERVICES}.items())
    case("services: machine names and least nargs equal the header's rows (every row has its n; fn 10 is served by the console host too)",
         names_ok and all(r[1] is not None for r in rows.values()) and "recorded difference" not in bootmachine.__doc__,
         [(f, rows[f]) for f in rows if rows[f][0].lower() != {**bootmachine.PLATFORM_FNS, **bootmachine.SOCK_SERVICES}[f][0]])
    # mechanical argument rules for every declared service, through Machine.ext_call on the host page
    args_at = mach0.host_page + 0x100
    bad = {}
    for f, (nm, need) in sorted(rows.items()):
        need = need if need is not None else bootmachine.PLATFORM_FNS[f][1]
        if f in bootmachine.SOCK_SERVICES:
            r = [mach0.ext_call(11, f, args_at, k) for k in sorted({0, need - 1, need + 1} - {need, -1})]
            if r != [-22 & bootmachine.U64] * len(r):
                bad[nm] = ("sock wrong nargs", r)
            if mach0.ext_call(11, f, 0, need) != bootmachine.EXT_BAD_NARGS:
                bad[nm + "-null"] = "sock null array with nargs > 0 is not BAD_NARGS"
            mach0.uc.mem_write(args_at, bytes(48))   # all-zero arguments: every service answers, none is the network's
            try:
                if not isinstance(mach0.ext_call(11, f, args_at, need), int):
                    bad[nm] = "sock exact nargs: not a value"
            except bootmachine.ExtMissing as e:
                bad[nm] = ("ExtMissing on zero arguments", e.fn)
            continue
        if need and mach0.ext_call(11, f, args_at, need - 1) != bootmachine.EXT_BAD_NARGS:
            bad[nm] = "nargs below the row"
        if mach0.ext_call(11, f, 0, need + 1) != bootmachine.EXT_BAD_NARGS:
            bad[nm] = "null array with nargs > 0"
    case("services: every declared service answers BAD_NARGS below its n and for a null array, sockets -EINVAL on a wrong nargs, BAD_NARGS on a null array and a value for zero arguments",
         not bad, bad)
    case("services: fn 0, 27..29, 32, 33 and 37.. are NO_SERVICE, unknown module NO_MODULE, fn > UINT32_MAX NO_SERVICE (before the platform table)",
         all(mach0.ext_call(11, f, args_at, 4) == bootmachine.EXT_NO_SERVICE for f in (0, 27, 28, 29, 32, 33, 37, 38, 0xFFFFFFFF, 1 << 32, (1 << 32) + 4))
         and mach0.ext_call(63, 1, args_at, 1) == bootmachine.EXT_NO_MODULE and mach0.ext_call(64, 1 << 33, 0, 0) == bootmachine.EXT_NO_MODULE
         and mach0.ext_call(11, 4, args_at, 1 << 40) == 0, "")
    try:
        bootmachine.Machine(elf0, plan0, boothost.Services(root), profile="tv")
        ok = False
    except bootmachine.MachineError:
        ok = True
    case("profile: anything but handheld / docked is a construction error", ok)

    r40, l40 = run(built[40][0], root)[:2]
    k = kv(l40)
    NOMOD, NOSVC, BADN, EINVAL = "ffffffffffffff01", "ffffffffffffff02", "ffffffffffffff03", "ffffffffffffffea"
    want40 = {"mod64": NOMOD, "mod-huge": NOMOD, "mod9-unreg": NOMOD, "mod12-fn-huge": NOMOD, "fn0": NOSVC, "fn37": NOSVC, "te-n4": BADN, "te-null-array": BADN, "self-n1-null": BADN, "sock-null-array-wrongn": EINVAL,
              "sock-null-array-n0": EINVAL, "sock-null-array-exact": BADN, "fn0-null-array": NOSVC,
              "fn-u32max": NOSVC, "fn-above-u32": NOSVC, "fn10-nargs1": BADN, "vm-reserve-n1": BADN, "null-array": BADN,
              "docked-n0-null": "0", "docked-n1-null": BADN, "docked-n3": "0", "docked-ncap": "0", "boost-extra-args": "0",
              "sock-wrong-n": EINVAL, "sock-wrong-n0": EINVAL, "sock-too-many": EINVAL, "tz-n1": BADN, "tz-null": BADN,
              "reserve-above-4g": "0", "reserve-4g": "0"}
    bad = {n: (k.get(n), v) for n, v in want40.items() if k.get(n) != v}
    case("ext_call (guest): NO_MODULE / NO_SERVICE / BAD_NARGS are returned to the guest, nargs above UINT32_MAX counts as UINT32_MAX, "
         "extra arguments ignored, sockets dispatch first: -EINVAL on wrong nargs (null array or not), BAD_NARGS on the exact count with a null array (%d values)" % len(want40), r40["reason"] == "exit" and not bad, (bad, r40))
    # TEXT_ENTRY (36): scenario texts, in order; none left -> cancelled; a text longer than max_len is a scenario error (not truncated)
    r57, l57, _, _ = run(built[57][0], root, text_entries=["Ab\U0001F600", "x"])
    k = kv(l57)
    want57 = {"te-max0": "1", "te-max501": "1", "te-maxhuge": "1", "te-out-null": "1", "te-n-null": "1", "te-bad-untouched": "1",
              "te-entered": "1", "te-n": "4", "te-u0": "41", "te-u1": "62", "te-u2": "d83d", "te-u3": "de00", "te-nul": "0", "te-rest": "7777",
              "te-entered2": "1", "te-n2": "1", "te-2u0": "78", "te-2nul": "0", "te-2rest": "7777",
              "te-cancel": "0", "te-cancel-untouched": "1", "te-cancel-again": "0"}
    bad = {n: (k.get(n), v) for n, v in want57.items() if k.get(n) != v}
    case("TEXT_ENTRY (guest): max_len 0 / 501 / huge, out null, n null -> -FNVNX_HE_BADINPUT without consuming a text; the texts come in order as UTF-16 "
         "with a NUL and *n; none left -> 0 with out and *n untouched", r57["reason"] == "exit" and not bad, (bad, r57["reason"]))
    ev = [(e["kind"], e["result"]) for e in r57["events"] if e["kind"] == "text-entry"]
    case("TEXT_ENTRY: one host event per call with its signed result (5 refused calls -0x1759, then 1, 1, 0, 0)", ev == [("text-entry", -0x1759)] * 5 + [("text-entry", 1)] * 2 + [("text-entry", 0)] * 2, ev)
    r57b = run(built[57][0], root)[0]
    case("TEXT_ENTRY with no scenario: every well-formed call is 0 (cancelled)", r57b["reason"] == "exit"
         and [e["result"] for e in r57b["events"] if e["kind"] == "text-entry"] == [-0x1759] * 5 + [0] * 4, r57b["reason"])
    r58, l58 = run(built[58][0], root, text_entries=["abcd"])[:2]
    case("TEXT_ENTRY: a scenario text longer than max_len ends the run as scenario-error (never truncated, the guest gets no result)",
         r58["reason"] == "scenario-error" and "te-long" not in kv(l58) and "max_len 3" in r58["scenario"] and "4 UTF-16 units" in r58["scenario"], (r58["reason"], r58.get("scenario")))
    r59, l59 = run(built[59][0], root, text_entries=["\U0001F600"])[:2]
    k = kv(l59)
    case("TEXT_ENTRY: a text of exactly max_len units fits", r59["reason"] == "exit" and k == {"te-fit": "1", "te-fit-n": "2", "te-fit-u0": "d83d", "te-fit-u1": "de00", "te-fit-nul": "0"}, (r59["reason"], k))
    for bad_scn in (["\ud800"], "abc", [b"x"]):
        try:
            bootmachine.Machine(elf0, plan0, boothost.Services(root), text_entries=bad_scn)
            ok = False
        except bootmachine.MachineError:
            ok = True
        case("TEXT_ENTRY: a scenario that is not a list of strings of Unicode scalars is a construction error: %r" % (bad_scn,), ok)
    # DISPLAY_DOCKED: the profile
    for prof, want in (("handheld", "0"), ("docked", "1")):
        rp, lp = run(built[40][0], root, profile=prof)[:2]
        k = kv(lp)
        case("DISPLAY_DOCKED is %s for profile %s (any nargs, null array only with nargs 0)" % (want, prof),
             k.get("docked-n0-null") == want and k.get("docked-n3") == want and k.get("docked-ncap") == want
             and k.get("docked-n1-null") == BADN, (prof, k))
        re48 = run(built[48][0], root, profile=prof)[0]
        case("DISPLAY_DOCKED through the exit code, profile %s" % prof, re48["reason"] == "exit" and re48["exit_code"] == int(want), re48)
    svc41 = Ext9(root)
    Ext9.calls = []
    r41, l41 = run(built[41][0], root, svc=svc41)[:2]
    k = kv(l41)
    case("ext_call (guest): a registered module is called with its args; fn > UINT32_MAX is NO_SERVICE without calling it; nargs 0 gives []; "
         "a handler raising ExtMissing ends the run", k.get("m9-fn1") == "407" and k.get("m9-fn-huge") == NOSVC and k.get("m9-nargs0") == "7d0"
         and r41["reason"] == "ext-module-missing" and r41["ext"] == {"module": 9, "fn": 99} and "after the missing service" not in l41
         and Ext9.calls == [(1, [1, 2, 3]), (2, []), (99, [1])], (r41, l41, Ext9.calls))

    # VM_PROTECT order and spans; windows; UNMAP_FIXED of protected pages
    r44, l44 = run(built[44][0], root)[:2]
    k = kv(l44)
    want44 = {"p-addr-unal": "cc01", "p-addr-unal-size0": "cc01", "p-size0": "ca01", "p-size-unal": "ca01", "p-wrap": "d401",
              "p-wrap-beats-perm": "d401", "p-wo": "d801", "p-x": "d801", "p-7": "d801", "p-big": "d801", "p-perm-beats-owned": "d801",
              "p-unowned": "d401", "p-still-rw": "3", "p-span": "0", "p-span-ro": "3", "p-none": "0", "p-rw": "0", "p-rw-write": "b",
              "decommit-ro-none": "0", "decommit-wrap": "d401", "decommit-size0": "0", "recommit": "0", "recommit-zero": "0",
              "w-ro": "0", "w-ro-read": "77", "w-none": "0", "w-rw": "0", "w-rw-keeps": "80", "mf": "0",
              "uf-protected": "0", "uf-bytes": "1", "uf-src-rw": "7"}
    bad = {n: (k.get(n), v) for n, v in want44.items() if k.get(n) != v}
    case("VM_PROTECT: header check order, write-only 2 -> 0xD801, a span of two extents, the host window; DECOMMIT of R / no-access "
         "extents; UNMAP_FIXED of R / no-access pages (%d values)" % len(want44), r44["reason"] == "exit" and not bad, (bad, r44))

    r45, l45 = run(built[45][0], root)[:2]
    k = kv(l45)
    want45 = {"size0": "1759", "wrap": "1759", "addr-unal": "1759", "size-unal": "1759", "size0-in-window": "1759", "past-4g": "da01",
              "src0": "1759", "src-unal": "1759", "outside-region": "da01", "keys": "da01", "src-not-heap": "d401",
              "win": "0", "win-zeroed": "0", "win-src-ignored": "0", "win-none-to-rw": "0", "win-rw-again": "9", "win-first": "0",
              "win-last": "0", "win-straddle-end": "f59", "win-straddle-start": "f59", "live": "200", "limit": "559",
              "limit-occupied": "559", "unmap-one": "0", "again": "0", "uf-win": "0", "uf-win-zeroed": "0", "uf-win-src-unal": "cc01",
              "uf-win-size-unal": "ca01", "uf-wrap": "d401", "uf-win-straddle": "d401"}
    bad = {n: (k.get(n), v) for n, v in want45.items() if k.get(n) != v}
    case("map_fixed: size 0 / wrap / misalignment 0x1759, the window zeroes and ignores src, src 0 / misaligned 0x1759, 512 live -> 0x559, "
         "alias region 0xDA01, src not heap 0xD401; UNMAP_FIXED: ADDR, SIZE, wrap, window (%d values)" % len(want45),
         r45["reason"] == "exit" and not bad, (bad, r45))

    r46, l46 = run(built[46][0], root)[:2]
    k = kv(l46)
    NF, BI, CC, RG = "1359", "1759", "cc01", "d401"
    want46 = {"h1-id": "1", "h-distinct": "1", "start-unknown": NF, "start-zero": NF, "join-unknown": NF, "join-never-started": BI,
              "prio-unknown": NF, "prio-self": "0", "cpu-unknown": NF, "cpu-self": "0", "cpu-negative": "0", "cpu-bits40-62": "0",
              "start": "0", "join": "0", "flag": "4d", "free": "0", "free-twice": NF, "join-freed": NF, "start-freed": NF,
              "prio-freed": NF, "act-freed": NF, "free-unknown": NF, "h3-new-generation": "1", "free-running": "0", "detached-ran": "4f",
              "dir-id": "1", "dir-next-cap0": "0", "dir-next-cut": "3", "dir-cut-bytes": hex(ord("d") * 0x10000 + ord("t") * 0x100 + 0)[2:],
              "dir-next-2": "6", "dir-end": "0", "dir-unknown": "0", "dir-as-thread": NF, "thread-as-dir": "0", "dir-close": "0",
              "dir-next-closed": "0", "dir-close-again": "0", "dir-close-unknown": "0", "dir-notdir": "0", "dir-missing": "0",
              "dirs-until-full": hex(1024 - 3)[2:], "thread-when-full": "0", "close-one": "0", "thread-after-close": "1",
              "wait-unal": CC, "wait-unmapped": RG, "wait-prot-none": RG, "wake-unal": CC, "wake-unmapped": RG, "wake-prot-none": RG,
              "wake-all": "0", "woken-all": "2"}
    bad = {n: (k.get(n), v) for n, v in want46.items() if k.get(n) != v}
    case("ids: thread and directory ids are table handles (unknown / freed -> NotFound, never started -> BadInput, 1024 live together, "
         "a freed id never names anything again); dir_next cap 0 / cut; wait / wake alignment and readability; wake count above "
         "0x7FFFFFFF wakes all (%d values)" % len(want46), r46["reason"] == "exit" and not bad, (bad, r46))
    case("ids: thread_set_cpu with a negative spec changes nothing", run(built[46][0], root)[3].placement.get(1) == {"cpu": 2, "prio": 3},
         "")

    r47, l47 = run(built[47][0], root)[:2]
    k = kv(l47)
    M1 = "ffffffffffffffff"
    want47 = {"a-submit-closed": M1, "a-rate": M1, "a-period0": M1, "a-period-big": M1, "a-open": "0", "a-open-again": "0",
              "a-submit-wrong": M1, "a-submit": "0", "a-close": "0", "a-close-again": "0", "a-submit-after": M1, "a-reopen": "0",
              "a-submit-8192": "0", "pad4": "0", "pad-untouched": "1", "pad0": "0", "pad0-zeroed": "0", "sleep-max": "0"}
    bad = {n: (k.get(n), v) for n, v in want47.items() if k.get(n) != v}
    case("audio_open / audio_submit / audio_close and pad_read index > 3 as the header states; sleep_ns above INT64_MAX sleeps INT64_MAX ns",
         r47["reason"] == "exit" and not bad and (1 << 63) - 1 <= r47["virtual_ns"] <= (1 << 63) - 1 + 10**9, (bad, r47))
    case("new modes: two identical runs give identical run() dicts", r44 == run(built[44][0], root)[0]
         and r45 == run(built[45][0], root)[0] and r46 == run(built[46][0], root)[0] and r40 == run(built[40][0], root)[0], "")
    print("%d cases, %.1f s" % (N[0], time.time() - t0))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
