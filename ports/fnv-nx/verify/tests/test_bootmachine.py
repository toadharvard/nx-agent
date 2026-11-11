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
    built = {m: build(m) for m in (0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11)}   # MODE 6 (boot CLI guest) is built by test_boot.py
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
    case("every header fn 1..8 of module 11 resolves", sorted(bootmachine.Machine(elf0, plan0, boothost.Services(root)).plat) == list(range(1, 9)))
    print("%d cases, %.1f s" % (N[0], time.time() - t0))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
