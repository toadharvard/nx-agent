#!/usr/bin/env python3
"""Harness of the test root tests/guest_os (L4 item 11 step B): build the root with the one build (gate_build.ensure), run its ELF on
the boot machine (boot.py run, renderer attached), and check
  - the run reason is exit with code 0 (main of tests/guest_os/main.c returns 0: locks, threads, condvars, clock held);
  - g_gpu_ret, read from the ELF symbol in the final guest memory (boot.py --dump), equals the answer of the model itself:
    bootgpu.Gpu.ext_10(7, g_gpu_args) and ext_10(0, []) called directly on the same library.
No skip and no fallback: while ports/fnv-nx/rt/guest or the platform root does not exist, the build fails and this prints
"root missing" and exits 1. Env: GATE_CACHE (stage cache, else a temp dir), NXGL4_LIB (path of libnxgl4.so, required).
"""
import json
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(os.path.dirname(VERIFY)))
sys.path.insert(0, VERIFY)
import boot  # noqa: E402
import bootgpu  # noqa: E402
import gate_build  # noqa: E402

ROOT = "ports/fnv-nx/tests/guest_os"
TABLE = "mainret any ret 0x1000\n"          # the table must name a milestone; main's return is the one that exists
MANIFEST = "1000\tmain\n"
ARGS = [0x1122334455667788, 0, 0xFFFFFFFFFFFFFFFF, 42, 0xDEADBEEF]   # g_gpu_args of main.c


def fail(msg):
    print("FAIL %s" % msg)
    sys.exit(1)


def main():
    lib = os.environ.get("NXGL4_LIB")
    if not lib:
        fail("NXGL4_LIB (the renderer library) is not set")
    cache = os.environ.get("GATE_CACHE") or tempfile.mkdtemp(prefix="guest-os-")
    try:
        res, out = gate_build.ensure(ROOT, cache, REPO)
    except Exception as e:            # RecipeError: an unadmitted root, a missing input root (rt/guest, platform), a red unit
        fail("root missing or red: %s: %s" % (type(e).__name__, e))
    elf = res["elf"]["path"] if isinstance(res.get("elf"), dict) else res.get("elf")
    if not elf:
        fail("the root built no ELF")
    d = tempfile.mkdtemp(prefix="guest-os-run-")
    w = lambda n, t: (open(os.path.join(d, n), "w").write(t), os.path.join(d, n))[1]  # noqa: E731
    reserved = w("res.tsv", "exe\t400000\t2100000\nmod2\t4000000\t4200000\n")
    o = os.path.join(d, "out.json")
    rc = boot.main(["run", "--elf", elf, "--reserved", reserved, "--keys", "f0000:200000", "--heap-size", "0x100000",
                    "--root", os.path.join(d, "sd"), "--milestones", w("t.txt", TABLE), "--manifest", w("m.tsv", MANIFEST),
                    "--gpu", lib, "--dump", "g_gpu_ret:16", "--out", o])
    doc = json.load(open(o))
    run = doc["run"]
    if not (run["reason"] == "exit" and run["exit_code"] == 0):
        fail("guest_os exit: reason=%s code=%s (code N = check N of main.c)" % (run["reason"], run.get("exit_code")))
    got = struct.unpack("<2Q", bytes.fromhex(doc["dump"]["g_gpu_ret"]))
    # the model's own answer: a second run of the same two calls on the same library (the library is the reference)
    m = _model()
    want = (m.ext_10(7, ARGS), m.ext_10(0, []))
    if tuple(got) != tuple(want):
        fail("gpu: guest got %s, the model answers %s" % ([hex(x) for x in got], [hex(x) for x in want]))
    print("ok   guest_os exit 0; gpu words equal the model's: %s" % [hex(x) for x in got])
    return 0


def _model():
    """the renderer library called directly through bootgpu.load (the same nxgl4_ext the boot machine forwards module 10 to); the
    calls here pass words only, no guest pointer, so nothing needs a machine"""
    import ctypes
    shim, lib = bootgpu.load(os.environ["NXGL4_LIB"])
    shim.nxgl4_shim_set(bootgpu.PTR_CB(lambda a, s: None), bootgpu.PRESENT_CB(lambda f: None))
    err = ctypes.create_string_buffer(256)
    if not lib.nxgl4_create(*bootgpu.SIZES["handheld"], err, len(err)):
        fail("nxgl4_create: %s" % err.value.decode(errors="replace"))

    class Direct:
        @staticmethod
        def ext_10(fn, words):
            arr = (ctypes.c_uint64 * max(len(words), 1))(*words)
            return lib.nxgl4_ext(fn, arr, len(words))
    return Direct


if __name__ == "__main__":
    sys.exit(main())
