#!/usr/bin/env python3
"""Tests of verify/bootgpu.py, Machine.host_ptr and boot.py --gpu/--frames on the REAL renderer library: libnxgl4.so, the recipe product of the
host-target root render/gl4, built in $GATE_CACHE by gate_build.ensure (bootgpu.gl4_path: the only source of the library). The guest drives it with the nxgpu v4 calls (INFO, IMAGE_CREATE, IMAGE_READ,
PRESENT, refusals) and every expected value comes from the library's published contract (the nxgpu ABI (render/gl4 sources) and the
ABI headers nxgpu4.h, nx/gpu_abi.h, gl4.h it cites) or from the bytes the guest itself wrote - never from a computation of the code under test.
The host needs the packages of verify/env.json (libegl1, libegl-mesa0 and what they pull); a library that cannot load is a failed test.
Plain script: exit 0 on pass."""
import ctypes
import json
import os
import hashlib
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import boot  # noqa: E402
import bootgpu  # noqa: E402
import boothost  # noqa: E402
import bootmachine  # noqa: E402
import recipe  # noqa: E402
import test_bootmachine as tb  # noqa: E402
from test_boot import cli  # noqa: E402

BAD_CALL = 0xFFFFFFFFFFFFFF02                                                         # gpu_abi.h:191
NXGPU_MAGIC = 0x5550474E                                                              # gpu_abi.h:25 ("NGPU")
FN_INFO, FN_IMAGE_CREATE, FN_IMAGE_READ, FN_PRESENT = 0, 3, 15, 17                   # gpu_abi.h:186-189, nxgpu4.h:261-266


# MODE 80 / 81: the renderer guests (tests/bootguest/guest.c; layout of the heap block at +0x60000 and the field offsets are in that source).


def parse(lines):
    return {k: int(v, 16) for k, v in (l.split("=") for l in lines if "=" in l and not l.startswith("hello"))}


def main():
    t0 = time.time()
    d = tb.TMP
    lib = bootgpu.gl4_path()
    lib_sha = hashlib.sha256(open(lib, "rb").read()).hexdigest()
    tb.case("setup: libnxgl4.so is the green product of render/gl4 in GATE_CACHE", os.path.isfile(lib))
    elfg = tb.build()[0]   # one image; the renderer guests are its modes 80 and 81
    elf50 = elf51 = elfg   # modes 80 and 81 of it: the file `mode` of the card, written before each run
    root = os.path.join(d, "sd")
    os.makedirs(root)
    frames = os.path.join(d, "frames")

    # --- Machine + Gpu directly (the environment of the library as boot.py sets it)
    os.environ.update(boot.GPU_ENV)
    os.environ["NXGL4_FRAMES"] = frames
    os.makedirs(frames)
    tb.write_mode(root, 80)
    m = bootmachine.Machine(elf50, tb.make_plan(elf50), boothost.Services(root), quantum=tb.QUANTUM)
    seen = []
    g = bootgpu.Gpu(m, "docked", on_present=seen.append)
    lines = []
    m.on_console(lines.append)
    r = m.run()
    v = parse(lines)
    tb.case("guest exits 0", r["reason"] == "exit" and r["exit_code"] == 0, (r, lines))
    tb.case("INFO: magic, version 4, backend 2 (nxgpu ABI)", v["infoRc"] == 0 and v["magic"] == NXGPU_MAGIC
            and v["version"] == 4 and v["backend"] == 2, v)
    tb.case("INFO: docked profile gives the swapchain 1920x1080 (NxInfo words 4, 5)", v["dims"] == 1920 << 16 | 1080, v)
    tb.case("IMAGE_CREATE reads the guest descriptor and pixels and returns an id", v["created"] == 1, v)
    tb.case("IMAGE_READ writes the uploaded pixels back into the guest buffer: all 256 bytes equal", v["readRc"] == 0 and v["readSame"] == 256, v)
    tb.case("PRESENT x4 returns 0 and calls nxgl4_on_present with NxPresent.frame 0..3", v["presentRc"] == 0
            and g.presents == 4 and seen == [0, 1, 2, 3], (v["presentRc"], g.presents, seen))
    rgba = ["frame_%06u.%s" % (i, e) for i in range(4) for e in ("rgba", "txt")]
    tb.case("frame files: frame_%06u.rgba is the whole swapchain (w*h*4 bytes), .txt is 'w h\\n'", sorted(os.listdir(frames)) == rgba
            and open(os.path.join(frames, "frame_000002.txt")).read() == "1920 1080\n"
            and os.path.getsize(os.path.join(frames, "frame_000002.rgba")) == 1920 * 1080 * 4, os.listdir(frames))
    tb.case("descriptor outside guest memory: BAD_CALL (nxgpu ABI, bad descriptor)", v["outside"] == BAD_CALL, v)
    tb.case("descriptor straddling two extents: BAD_CALL (the range is not wholly in one mapping)", v["straddle"] == BAD_CALL, v)
    tb.case("unknown fn 99: BAD_CALL (dispatch, nxgpu ABI)", v["badfn"] == BAD_CALL, v)
    rep = g.report()
    tb.case("report: library sha256 is the product's, presents 4, first call recorded", rep["library_sha256"] == lib_sha and rep["presents"] == 4
            and rep["first_module10_call"]["instructions_total"] >= 0 and rep["first_module10_call"]["virtual_ns"] > 0, rep)
    g.close()

    # --- without a library: module 10 is not registered
    tb.write_mode(root, 80)
    m = bootmachine.Machine(elf50, tb.make_plan(elf50), boothost.Services(root), quantum=tb.QUANTUM)
    lines = []
    m.on_console(lines.append)
    m.run()
    tb.case("without --gpu: module 10 answers NO_MODULE", parse(lines)["infoRc"] == bootmachine.EXT_NO_MODULE, lines)

    # --- host_ptr
    tb.write_mode(root, 81)
    m = bootmachine.Machine(elf51, tb.make_plan(elf51), boothost.Services(root), quantum=tb.QUANTUM)
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

    tb.write_mode(root, 80)

    def args(out, fr, *extra):
        return ["run", "--elf", elf, "--facts", tb.facts_path(), "--reserved", w("res.tsv", "exe\t400000\t2100000\nmod2\t4000000\t4200000\n"), "--keys", "f0000:200000",
                "--heap-size", "0x100000", "--root", root, "--quantum", str(tb.QUANTUM), "--milestones", table, "--manifest", w("m.tsv", "1000\tmarker\n"),
                "--out", os.path.join(d, out), "--gpu", "--frames", os.path.join(d, fr), *extra]

    outs = []
    for k in range(2):
        rc, o, e = cli(args("g%d.json" % k, "fr%d" % k))
        outs.append((rc, o, e))
    doc = json.load(open(os.path.join(d, "g0.json")))
    ok = outs[0][0] == 0 and doc["run"]["reason"] == "exit"
    tb.case("CLI --gpu run exits 0", ok, outs[0])
    if ok:
        tb.case("CLI: gpu record in OUT.json", set(doc["gpu"]) == {"library_sha256", "presents", "first_module10_call", "last_error"}
                and doc["gpu"]["presents"] == 4 and doc["gpu"]["library_sha256"] == lib_sha, doc["gpu"])
        st = {x["name"]: x["reached"] for x in doc["milestones"]}
        tb.case("CLI: host events gpu-first-call and present reached", st == {"first": True, "pres": True}, st)
        names = [iv["to"] for iv in doc["icount"]["intervals"]]
        tb.case("CLI: a frame is not an icount interval (no frame-<n> mark)", [n for n in names if n.startswith("frame")] == [], names)
        fr = doc.get("frames", [])
        tb.case("CLI: OUT.json frames = one record per present: n 1..4, virtual t_ns not decreasing, main-thread instructions, a sha256 key",
                [f["n"] for f in fr] == [1, 2, 3, 4] and all(set(f) == {"n", "t_ns", "per_tid", "sha256"} for f in fr)
                and [f["t_ns"] for f in fr] == sorted(f["t_ns"] for f in fr) and all(f["per_tid"].get("main", 0) > 0 for f in fr)
                and sum(sum(f["per_tid"].values()) for f in fr) <= doc["icount"]["totals"]["all"]["total"], fr)
        keys = {f["sha256"] for f in fr}
        tb.case("CLI: the frames directory holds exactly the distinct frames, <key>.rgba + <key>.txt, key = sha256(.txt + .rgba)",
                sorted(os.listdir(os.path.join(d, "fr0"))) == sorted(k + e for k in keys for e in (".rgba", ".txt"))
                and all(hashlib.sha256(open(os.path.join(d, "fr0", k + ".txt"), "rb").read() + open(os.path.join(d, "fr0", k + ".rgba"), "rb").read()).hexdigest() == k
                        for k in keys), sorted(os.listdir(os.path.join(d, "fr0"))))
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
