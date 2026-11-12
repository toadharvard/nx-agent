#!/usr/bin/env python3
"""Tests of verify/bootgpu.py, Machine.host_ptr and boot.py --gpu/--frames on the REAL renderer library: the published libnxgl4.so of the
fnv-nx-build branch l6-render/nxgl4/13c4573c49d7 (commit and tree_sha256 pinned below, fetched through recipe.fetch_externals like a
recipe external; its sha256 must equal its meta.json and the pin). The guest drives it with the nxgpu v4 calls (INFO, IMAGE_CREATE, IMAGE_READ,
PRESENT, refusals) and every expected value comes from the library's published contract (render/d3d9/backend/NXGL4_HOST.md section 3 and the
ABI headers nxgpu4.h, nx/gpu_abi.h, gl4.h it cites) or from the bytes the guest itself wrote - never from a computation of the code under test.
The host needs the packages of verify/env.json (libegl1, libegl-mesa0 and what they pull); a library that cannot load is a failed test.
Plain script: exit 0 on pass."""
import ctypes
import json
import os
import atexit
import hashlib
import shutil
import tempfile
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

# The published renderer, pinned exactly as a recipe external (verify/RECIPE.md "External inputs"; NXGL4_HOST.md section 1).
NXGL4 = {"name": "nxgl4", "repo": "fnv-nx-build", "ref": "l6-render/nxgl4/13c4573c49d7", "commit": "2f0427f556c6a1d5af80d76a313a386abfc6c03a",
         "tree_sha256": "436df06806ab414a41b426739e18259025a1a4fe7a2dfb412cc75130d8c62b15", "at": "libnxgl4.so"}
BRIDGE = {"name": "bridge", "repo": "fnv-nx-build", "ref": "l6-render/bridge/13c4573c49d7", "commit": "7c98eb37b7c6ac9bc7c74a131fcd04b892352a13",
          "tree_sha256": "30a71e185b6c068a3166716ecd579d9bc0f186d44d750d57f6e38518603d415e", "at": "libfnvnx-d3d9.a"}   # the D3D9 bridge of the same nx-agent commit
NX_AGENT_COMMIT = "13c4573c49d7b5d316ce7aa3df9e6226471c13b7"   # meta.json of the nxgl4 branch and build.json of the bridge branch both record it
NXGL4_SHA256 = "52d324caabe9322a5a46b745f5af1df73f2046c965a4b442f161cbc47b61a7a0"   # NXGL4_HOST.md section 1 table
BAD_CALL = 0xFFFFFFFFFFFFFF02                                                         # gpu_abi.h:191
NXGPU_MAGIC = 0x5550474E                                                              # gpu_abi.h:25 ("NGPU")
FN_INFO, FN_IMAGE_CREATE, FN_IMAGE_READ, FN_PRESENT = 0, 3, 15, 17                   # gpu_abi.h:186-189, nxgpu4.h:261-266


def fetch_renderer(scratch):
    """-> (directory of the pinned nxgl4 branch commit, directory of the pinned bridge branch commit): fetched by exact commit through
    recipe.fetch_externals (tree_sha256 checked there); libnxgl4.so's sha256 must equal the pin and its meta.json, the bridge's build.json must
    name the archive's sha256 and the same nx-agent commit"""
    store, ws = os.path.join(scratch, "cas"), os.path.join(scratch, "ws")
    os.makedirs(ws)
    got = recipe.fetch_externals([NXGL4, BRIDGE], store, os.path.join(scratch, "ext"), ws)
    g, b = got["nxgl4"]["host"], got["bridge"]["host"]
    meta, build = json.load(open(os.path.join(g, "meta.json"))), json.load(open(os.path.join(b, "build.json")))
    sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()  # noqa: E731
    assert sha(os.path.join(g, "libnxgl4.so")) == NXGL4_SHA256 == meta["sha256"], meta
    assert sha(os.path.join(b, "libfnvnx-d3d9.a")) == build["archive_sha256"], build["archive_sha256"]
    assert meta["nx_agent_commit"] == build["nx_agent_commit"] == NX_AGENT_COMMIT
    return g, b


def fetch_nxgl4(scratch):
    """-> path of the pinned libnxgl4.so"""
    return os.path.join(fetch_renderer(scratch)[0], "libnxgl4.so")


# MODE 80 / 81: the renderer guests (tests/bootguest/guest.c; layout of the heap block at +0x60000 and the field offsets are in that source).


def parse(lines):
    return {k: int(v, 16) for k, v in (l.split("=") for l in lines if "=" in l and not l.startswith("hello"))}


def main():
    t0 = time.time()
    d = tb.TMP
    scratch = tempfile.mkdtemp(prefix="nxgl4-")
    atexit.register(shutil.rmtree, scratch, ignore_errors=True)
    lib = fetch_nxgl4(scratch)
    tb.case("setup: the pinned libnxgl4.so fetched, tree_sha256 and sha256 verified", os.path.isfile(lib))
    elf50, elf51 = tb.build(80)[0], tb.build(81)[0]
    root = os.path.join(d, "sd")
    os.makedirs(root)
    frames = os.path.join(d, "frames")

    # --- Machine + Gpu directly (the environment of the library as boot.py sets it)
    os.environ.update(boot.GPU_ENV)
    os.environ["NXGL4_FRAMES"] = frames
    os.makedirs(frames)
    m = bootmachine.Machine(elf50, tb.make_plan(elf50), boothost.Services(root))
    seen = []
    g = bootgpu.Gpu(lib, m, "docked", on_present=seen.append)
    lines = []
    m.on_console(lines.append)
    r = m.run()
    v = parse(lines)
    tb.case("guest exits 0", r["reason"] == "exit" and r["exit_code"] == 0, (r, lines))
    tb.case("INFO: magic, version 4, backend 2 (NXGL4_HOST.md section 3)", v["infoRc"] == 0 and v["magic"] == NXGPU_MAGIC
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
    tb.case("descriptor outside guest memory: BAD_CALL (NXGL4_HOST.md section 3 (d))", v["outside"] == BAD_CALL, v)
    tb.case("descriptor straddling two extents: BAD_CALL (the range is not wholly in one mapping)", v["straddle"] == BAD_CALL, v)
    tb.case("unknown fn 99: BAD_CALL (dispatch, NXGL4_HOST.md section 3)", v["badfn"] == BAD_CALL, v)
    rep = g.report()
    tb.case("report: library sha256 is the pin, presents 4, first call recorded", rep["library_sha256"] == NXGL4_SHA256 and rep["presents"] == 4
            and rep["first_module10_call"]["instructions_total"] >= 0 and rep["first_module10_call"]["virtual_ns"] > 0, rep)
    g.close()

    # --- without a library: module 10 is not registered
    m = bootmachine.Machine(elf50, tb.make_plan(elf50), boothost.Services(root))
    lines = []
    m.on_console(lines.append)
    m.run()
    tb.case("without --gpu: module 10 answers NO_MODULE", parse(lines)["infoRc"] == bootmachine.EXT_NO_MODULE, lines)

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
                and doc["gpu"]["presents"] == 4 and doc["gpu"]["library_sha256"] == NXGL4_SHA256, doc["gpu"])
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
