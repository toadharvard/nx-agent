#!/usr/bin/env python3
"""Tests of verify/bootgpu.py, Machine.host_ptr and boot.py --gpu/--frames, WITHOUT the real renderer: a fake libnxgl4 (C, built with the
host gcc) implements the same exports. fn 1 = sum the bytes of guest buffer (addr, size) through nxgl4_guest_ptr, 2 = write a pattern into
it, 3 = present (nxgl4_on_present(frame++) after writing a frame file into $NXGL4_FRAMES), 4 = ask for a range (addr, size) and return
an error code when the pointer is NULL, 5 = the swapchain size given to nxgl4_create (w << 16 | h). Plain script: exit 0 on pass."""
import ctypes
import json
import os
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import boot  # noqa: E402
import bootgpu  # noqa: E402
import boothost  # noqa: E402
import bootmachine  # noqa: E402
import test_bootmachine as tb  # noqa: E402
from test_boot import cli  # noqa: E402

FAKE_C = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
void *nxgl4_guest_ptr(uint64_t addr, uint64_t size);
void nxgl4_on_present(uint32_t frame);
static uint32_t frame, W, H;
static char err[64];
const void *nxgl4_create(uint32_t w, uint32_t h, char *e, size_t n) { frame = 0; W = w; H = h; err[0] = 0; return &W; }
void nxgl4_destroy(void) {}
const char *nxgl4_last_error(void) { return err; }
uint64_t nxgl4_ext(uint32_t fn, const uint64_t *a, uint32_t n) {
    if (fn == 5) return (uint64_t)W << 16 | H;
    if (fn == 3) {
        const char *dir = getenv("NXGL4_FRAMES");
        char path[512];
        if (!dir) return 0xE3;
        snprintf(path, sizeof path, "%s/frame_%06u.rgba", dir, frame);
        FILE *f = fopen(path, "wb"); if (!f) return 0xE3; fwrite(&frame, 4, 1, f); fclose(f);
        snprintf(path, sizeof path, "%s/frame_%06u.txt", dir, frame);
        f = fopen(path, "w"); if (!f) return 0xE3; fprintf(f, "%u %u\n", W, H); fclose(f);
        nxgl4_on_present(frame++);
        return 0;
    }
    if (n < 2) return 0xE0;
    uint8_t *p = nxgl4_guest_ptr(a[0], a[1]);
    if (!p) { snprintf(err, sizeof err, "range refused"); return fn == 4 ? 0xE4 : ~0ull; }
    if (fn == 1) { uint64_t s = 0; for (uint64_t i = 0; i < a[1]; i++) s += p[i]; return s; }
    if (fn == 2) { for (uint64_t i = 0; i < a[1]; i++) p[i] = (uint8_t)(i * 7 + 3); return 0; }
    return fn == 4 ? 0 : 0xE0;
}
"""
MODE50 = r"""#if MODE == 50
    uint8_t *buf = (uint8_t *)(uintptr_t)(H->heap_base + 0x60000);
    for (uint32_t i = 0; i < 100; i++) buf[i] = (uint8_t)i;
    uint64_t a2[2] = {H->heap_base + 0x60000, 100}, a3[2] = {H->heap_base + 0x60200, 16};
    num("dims", EC(10, 5, FNVNX_ADDR(a2), 1));
    num("sum", EC(10, 1, FNVNX_ADDR(a2), 2));
    num("pat", EC(10, 2, FNVNX_ADDR(a3), 2));
    uint32_t ps = 0;
    for (uint32_t i = 0; i < 16; i++) ps += buf[0x200 + i];
    num("patsum", ps);
    for (uint32_t i = 0; i < 4; i++) EC(10, 3, FNVNX_ADDR(a2), 1);
    uint64_t A = plat(4, 0, 0x8000000, 0, 2);
    plat(5, A, 0x1000, 0, 2); plat(5, A + 0x1000, 0x1000, 0, 2);
    uint64_t a4[2] = {A + 0xff0, 0x20};
    num("straddle", EC(10, 4, FNVNX_ADDR(a4), 2));
    fnvnx_hcall1(H->exit, 0);
#elif MODE == 51
    num("mf", fnvnx_hcall3(H->map_fixed, 0x4000000, 0x2000, H->heap_base + 0x20000));
    uint64_t A = plat(4, 0, 0x8000000, 0, 2);
    num("A", A);
    plat(5, A, 0x1000, 0, 2); plat(5, A + 0x1000, 0x1000, 0, 2); plat(5, A + 0x10000, 0x1000, 0, 2);
    plat(8, A + 0x10000, 0x1000, 1, 3);
    fnvnx_hcall1(H->exit, 0);
#elif MODE == 6"""
EXPECT = {"dims": 0x500 << 16 | 0x2D0, "sum": 4950, "patsum": sum((i * 7 + 3) & 255 for i in range(16)), "straddle": 0xE4}


def parse(lines):
    return {k: int(v, 16) for k, v in (l.split("=") for l in lines if "=" in l and not l.startswith("hello"))}


def main():
    t0 = time.time()
    d = tb.TMP
    c = os.path.join(d, "fake.c")
    open(c, "w").write(FAKE_C)
    lib = os.path.join(d, "libnxgl4.so")
    subprocess.run(["gcc", "-O2", "-fPIC", "-shared", c, "-o", lib], check=True)
    elf50, elf51 = tb.build(50, MODE50)[0], tb.build(51, MODE50)[0]
    root = os.path.join(d, "sd")
    os.makedirs(root)
    frames = os.path.join(d, "frames")

    # --- Machine + Gpu directly
    os.environ["NXGL4_FRAMES"] = frames
    os.makedirs(frames)
    m = bootmachine.Machine(elf50, tb.make_plan(elf50), boothost.Services(root))
    seen = []
    g = bootgpu.Gpu(lib, m, "docked", on_present=seen.append)
    lines = []
    m.on_console(lines.append)
    r = m.run()
    v = parse(lines)
    tb.case("guest exits 0", r["reason"] == "exit" and r["exit_code"] == 0, r)
    tb.case("docked profile: library got 1920x1080", v["dims"] == 1920 << 16 | 1080, v)
    tb.case("fn 1: sum of the guest buffer through nxgl4_guest_ptr", v["sum"] == 4950, v)
    tb.case("fn 2: the library's pattern is visible to the guest", v["patsum"] == EXPECT["patsum"], v)
    tb.case("presents counted, callback saw 0..3", g.presents == 4 and seen == [0, 1, 2, 3], (g.presents, seen))
    tb.case("frame files written", sorted(os.listdir(frames)) == ["frame_%06u.%s" % (i, e) for i in range(4) for e in ("rgba", "txt")]
            and open(os.path.join(frames, "frame_000002.txt")).read() == "1920 1080\n", os.listdir(frames))
    tb.case("straddling range refused (NULL -> error code)", v["straddle"] == 0xE4, v)
    rep = g.report()
    tb.case("report: last_error from the library, first call recorded", rep["last_error"] == "range refused" and rep["presents"] == 4
            and rep["first_module10_call"]["instructions_total"] >= 0 and rep["first_module10_call"]["virtual_ns"] > 0, rep)
    g.close()

    # --- without a library: module 10 is not registered
    m = bootmachine.Machine(elf50, tb.make_plan(elf50), boothost.Services(root))
    lines = []
    m.on_console(lines.append)
    m.run()
    tb.case("without --gpu: module 10 answers NO_MODULE", parse(lines)["dims"] == bootmachine.EXT_NO_MODULE, lines)

    # --- host_ptr
    m = bootmachine.Machine(elf51, tb.make_plan(elf51), boothost.Services(root))
    lines = []
    m.on_console(lines.append)
    r = m.run()
    v = parse(lines)
    A, hp = v["A"], m.host_ptr
    m.uc.mem_write(m.heap[0] + 0x30000, b"xyz")
    p = hp(m.heap[0] + 0x30000, 0x100)
    tb.case("host_ptr: heap range is a real host pointer onto the guest bytes", v["mf"] == 0 and p and ctypes.string_at(p, 3) == b"xyz", (v, p))
    ctypes.memmove(p, b"abc", 3)
    tb.case("host_ptr: writes through it reach the guest", bytes(m.uc.mem_read(m.heap[0] + 0x30000, 3)) == b"abc")
    tb.case("host_ptr: map_fixed range", hp(0x4000000, 0x2000) is not None and hp(0x4001ff0, 0x10) is not None)
    tb.case("host_ptr: committed VM extent", hp(A, 0x1000) is not None and hp(A + 0x1000, 0x1000) is not None)
    tb.case("host_ptr: range crossing two extents is None", hp(A + 0xff0, 0x20) is None and hp(A, 0x2000) is None)
    tb.case("host_ptr: read-only page is None", hp(A + 0x10000, 0x10) is None)
    tb.case("host_ptr: uncommitted/unmapped is None", hp(A + 0x8000, 0x10) is None and hp(0x30000000, 4) is None)
    tb.case("host_ptr: heap block given away by map_fixed is None, a range over it too",
            hp(m.heap[0] + 0x20000, 0x10) is None and hp(m.heap[0] + 0x1f000, 0x4000) is None)
    tb.case("host_ptr: the host page is None", hp(m.host_page, 8) is None)
    tb.case("host_ptr: an empty range inside a mapping, and exactly at its end, gets a pointer",
            hp(m.heap[0], 0) == hp(m.heap[0], 8) and hp(m.heap[0] + 0x1000, 0) == hp(m.heap[0], 8) + 0x1000)
    tb.case("host_ptr: an empty range outside every mapping is None", hp(0x000F8000, 0) is None)
    tb.case("host_ptr: the host window and a stack are readable and writable",
            hp(bootmachine.WINDOW[0], 16) is not None and hp(m.stacks_top - 0x1000, 16) is not None)

    # --- the CLI
    w = lambda n, t: (open(os.path.join(d, n), "w").write(t), os.path.join(d, n))[1]  # noqa: E731
    elf = os.path.join(d, "h50.elf")
    open(elf, "wb").write(elf50)
    table = w("t.txt", "first any host gpu-first-call\npres any host present\n")

    def args(out, fr, *extra):
        return ["run", "--elf", elf, "--reserved", w("res.tsv", "exe\t400000\t2100000\nmod2\t4000000\t4200000\n"), "--keys", "f0000:200000",
                "--heap-size", "0x100000", "--root", root, "--milestones", table, "--manifest", w("m.tsv", "1000\tmarker\n"),
                "--out", os.path.join(d, out), "--gpu", lib, "--frames", os.path.join(d, fr), *extra]

    outs = []
    for k in range(2):
        rc, o, e = cli(args("g%d.json" % k, "fr%d" % k))
        outs.append((rc, o, e))
    doc = json.load(open(os.path.join(d, "g0.json")))
    ok = outs[0][0] == 0 and doc["run"]["reason"] == "exit"
    tb.case("CLI --gpu run exits 0", ok, outs[0])
    if ok:
        tb.case("CLI: gpu record in OUT.json", set(doc["gpu"]) == {"library_sha256", "presents", "first_module10_call", "last_error"}
                and doc["gpu"]["presents"] == 4 and doc["gpu"]["last_error"] == "range refused"
                and len(doc["gpu"]["library_sha256"]) == 64, doc["gpu"])
        st = {x["name"]: x["reached"] for x in doc["milestones"]}
        tb.case("CLI: host events gpu-first-call and present reached", st == {"first": True, "pres": True}, st)
        names = [iv["to"] for iv in doc["icount"]["intervals"]]
        tb.case("CLI: icount marks frame-1..3 only", [n for n in names if n.startswith("frame")] == ["frame-1", "frame-2", "frame-3"], names)
        tb.case("CLI: frame files in --frames", len(os.listdir(os.path.join(d, "fr0"))) == 8)
        tb.case("CLI: two runs give byte-identical OUT.json", open(os.path.join(d, "g0.json"), "rb").read()
                == open(os.path.join(d, "g1.json"), "rb").read())
    try:
        rc, o, e = cli(["run", "--frames", "x"])
    except SystemExit as x:
        rc = x.code
    tb.case("--frames without --gpu is a usage error", rc == 2, rc)
    print("%d cases, %.1f s" % (tb.N[0], time.time() - t0))
    return 1 if tb.FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
