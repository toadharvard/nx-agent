#!/usr/bin/env python3
"""Tests of verify/bootmachine.py + boothost.py (the boot stage machine). Plain script: exit 0 on pass, one line per case.
Builds small ILP32 PIE guests with aarch64-linux-gnu-gcc (-mabi=ilp32) from the real toolchain/guest/crt0.s (FNXG header)
and the real toolchain/guest/guest.ld rebased to 0 (its two base asserts removed); the runtime part is the test's own
freestanding entry (the real runtime.c needs devkitA64's newlib: sys/iosupport.h). The guest relocates itself, as runtime.c
does. Scratch: tempfile.mkdtemp, removed at exit."""
import atexit
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
TC = os.path.join(VERIFY, "..", "toolchain")
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
          "-I" + os.path.join(TC, "include")]
LDFLAGS = ["-m", "aarch64elf32", "-pie", "--no-dynamic-linker", "-z", "text", "-z", "now", "-z", "pack-relative-relocs",
           "--gc-sections", "-z", "max-page-size=0x1000", "--defsym=FNVNX_GUEST_BASE=0",
           "--require-defined=__fnvnx_guest_start"]

GUEST = r'''
#include <stdint.h>
#include "fnvnx/hostapi.h"
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
uint64_t __fnvnx_guest_entry(uint64_t api) {
    relocate((uint32_t)(uintptr_t)__fnvnx_guest_start);
    fnvnx_host = (const FnvnxHostApi *)(uintptr_t)api;
    say(MSG[0]); say(MSG[1]);
#if MODE == 1
    uint64_t a3[3] = {0x500000, 0x1000, 1};   /* a 3-argument call to module 9: not registered */
    fnvnx_hcall4(H->ext_call, 9, 1, FNVNX_ADDR(a3), 3);
#elif MODE == 2
    fnvnx_hcall3(H->wait_on_address, FNVNX_ADDR(&flag), 0, ~0ull);   /* nobody wakes it, no deadline */
#else
    num("marker", marker(41));
    num("map", fnvnx_hcall3(H->map_fixed, 0x400000, 0x10000, 0));
    volatile uint32_t *p = (volatile uint32_t *)0x400000;
    p[0] = 0x1234; p[0x3FFF] = 0x5678;
    num("readback", p[0] + p[0x3FFF]);
    num("overlap", fnvnx_hcall3(H->map_fixed, 0x400000, 0x1000, 0));
    num("keys", fnvnx_hcall3(H->map_fixed, 0xf0000, 0x1000, 0));
    uint64_t a[3] = {0x400000, 0x10000, 1};
    num("protect", fnvnx_hcall4(H->ext_call, 11, 8, FNVNX_ADDR(a), 3));
    uint64_t u[3] = {0x500000, 0x1000, 1};
    num("protect-unmapped", fnvnx_hcall4(H->ext_call, 11, 8, FNVNX_ADDR(u), 3));
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
    """crt0.s + test C -> PIE ELF at base 0; the crt0 header's +20 size and +24 flags are filled as elf2nso does"""
    src, ld = os.path.join(TMP, "g.c"), os.path.join(TMP, "guest.ld")
    if not os.path.exists(ld):
        txt = open(os.path.join(TC, "guest", "guest.ld")).read()
        txt = re.sub(r"\tASSERT\( __fnvnx_guest_base >= 0x00200000.*?\)\n", "", txt, flags=re.S)
        txt = re.sub(r"\tASSERT\( __end__ <= 0x00400000.*?\)\n", "", txt, flags=re.S)
        open(ld, "w").write(txt)
        open(src, "w").write(GUEST)
        sh([CC] + CFLAGS[:3] + ["-c", os.path.join(TC, "guest", "crt0.s"), "-o", os.path.join(TMP, "crt0.o")])
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
    rows = [("keys", 0xf0000, 0x200000), ("exe", 0x400000, 0x2100000)]
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
    built = {m: build(m) for m in (0, 1, 2, 3)}
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
                 ("protect", "0"), ("protect-unmapped", "d401"), ("unprotect", "0"), ("tickdiff", "3e8"),
                 ("thread", "2"), ("woken", "0"), ("flag", "4d"), ("pread", "8"), ("rand-calls-equal", "1")):
        case("guest saw %s=%s" % (k, v), (k + "=" + v) in lines1, text)
    case("pread through fs_* (case-folded path)", "ABCDEFGH" in lines1, text)
    case("two threads, a context switch", r1["threads"] == 2 and r1["switches"] >= 2, r1)
    case("refusals recorded", [e["kind"] for e in r1["events"]] == ["map_fixed-refused", "map_fixed-refused",
                                                                      "vm_protect-refused"], r1["events"])
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
    print("%d cases, %.1f s" % (N[0], time.time() - t0))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
