#!/usr/bin/env python3
"""Present and software-cursor tests of the REAL renderer library: libnxgl4.so, the recipe product of the host root render/gl4, built by
recipe.py in the verdict's store ($GATE_CACHE) and loaded through verify/bootgpu.py (the shared GL model of the boot machine; no double of a
renderer file, no replaced function). Rule under test: render/nxgpu/nxgpu4.h P2 (the Wine 9.0 software cursor, cs.c:649-700): before the
present blit the cursor texels whose alpha is not 0 replace the texels of the source image at (cx, cy), clipped to the image; texels with alpha 0
leave it; the image keeps the cursor afterwards (a later image_read, i.e. GetBackBuffer, sees it); the present then copies the image.
Every expected value is computed in this file from that rule on the bytes the test wrote itself, never from the library.
Plain script: exit 0 on pass. Not part of the verdict until L4's harness admits it."""
import ctypes
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import bootgpu  # noqa: E402

ABI = os.path.join(os.path.dirname(os.path.dirname(HERE)), "render", "nxgpu")
BAD_CALL = 0xFFFFFFFFFFFFFF02
W = H = 8   # the source image: RGBA8, 8 x 8


def names(path, macro):
    """the entry names of an X-macro list (`#define macro(X) X(a) X(b) ...`), in order: how the header numbers the ops and functions"""
    text = open(path).read()
    m = re.search(r"#define %s\(X\)((?:[^\n]*\\\n)*[^\n]*)" % macro, text)
    body = re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)
    return re.findall(r"\bX\(\s*(\w+)", body)


def numbers():
    abi, abi4 = os.path.join(ABI, "nx", "gpu_abi.h"), os.path.join(ABI, "nxgpu4.h")
    ops = names(abi, "NXGPU_OP_LIST_V3") + names(abi4, "NXGPU_OP_LIST_V4")
    fns = ["INFO"] + names(abi, "NXGPU_FN_LIST_V3") + ["SHUTDOWN"] + names(abi4, "NXGPU_FN_LIST_V4")
    return {n: i for i, n in enumerate(ops)}, {n: i for i, n in enumerate(fns)}


class Present(ctypes.Structure):
    _fields_ = [("image", ctypes.c_uint32), ("sx", ctypes.c_int32), ("sy", ctypes.c_int32), ("sw", ctypes.c_int32), ("sh", ctypes.c_int32),
                ("dx", ctypes.c_int32), ("dy", ctypes.c_int32), ("dw", ctypes.c_int32), ("dh", ctypes.c_int32), ("interval", ctypes.c_uint32),
                ("gamma", ctypes.c_uint64), ("frame", ctypes.c_uint32), ("pad", ctypes.c_uint32), ("cursor", ctypes.c_uint64),
                ("cx", ctypes.c_int32), ("cy", ctypes.c_int32), ("cw", ctypes.c_uint32), ("ch", ctypes.c_uint32)]


class Read(ctypes.Structure):
    _fields_ = [("image", ctypes.c_uint32), ("level", ctypes.c_uint16), ("layer", ctypes.c_uint16), ("x", ctypes.c_uint32), ("y", ctypes.c_uint32),
                ("z", ctypes.c_uint32), ("w", ctypes.c_uint32), ("h", ctypes.c_uint32), ("d", ctypes.c_uint32), ("dst", ctypes.c_uint64),
                ("dst_size", ctypes.c_uint32), ("rowPitch", ctypes.c_uint32)]


class Submit(ctypes.Structure):
    _fields_ = [("stream", ctypes.c_uint64), ("words", ctypes.c_uint32), ("frame", ctypes.c_uint32), ("stats", ctypes.c_uint64),
                ("present", ctypes.c_uint32), ("pad", ctypes.c_uint32)]


class Mem(ctypes.Structure):
    _fields_ = [("storage", ctypes.c_uint64), ("size", ctypes.c_uint32), ("kind", ctypes.c_uint32)]


class Desc(ctypes.Structure):
    _fields_ = [("w", ctypes.c_uint16), ("h", ctypes.c_uint16), ("d", ctypes.c_uint16), ("type", ctypes.c_uint8), ("format", ctypes.c_uint8),
                ("mips", ctypes.c_uint8), ("samples", ctypes.c_uint8), ("flags", ctypes.c_uint8), ("pad0", ctypes.c_uint8),
                ("swizzle", ctypes.c_uint16), ("pad1", ctypes.c_uint16)]


def lib_path():
    cache = os.environ.get("GATE_CACHE")
    assert cache, "GATE_CACHE is not set: libnxgl4.so is the recipe product of render/gl4 in the verdict's store"
    sys.path.insert(0, os.path.dirname(HERE))
    import gate_build
    repo = bootgpu.REPO
    res, _ = gate_build.ensure("ports/fnv-nx/render/gl4", cache, repo)
    assert res.get("status") == "green" and res.get("elf"), "render/gl4 is not green: %s" % (res.get("failure") or res.get("status"))
    return res["elf"]["path"]


def expect(base, cur, cx, cy, cw, ch):
    """the rule: base = W*H RGBA texels (list of 4-tuples), cur = cw*ch BGRA texels (B, G, R, A); returns the image after the stamp"""
    out = list(base)
    for j in range(ch):
        for i in range(cw):
            x, y = cx + i, cy + j
            b, g, r, a = cur[j * cw + i]
            if 0 <= x < W and 0 <= y < H and a != 0:
                out[y * W + x] = (r, g, b, a)
    return out


def main():
    ops, fns = numbers()
    shim, lib = bootgpu.load(lib_path())
    keep = {}
    ptr = bootgpu.PTR_CB(lambda addr, size: addr)   # guest addresses are host addresses here: every range is a ctypes buffer of this test
    pres = bootgpu.PRESENT_CB(lambda frame: None)
    ex = bootgpu.EXIT_CB()
    shim.nxgl4_shim_set(ptr, pres, ex)
    err = ctypes.create_string_buffer(256)
    assert lib.nxgl4_create(1280, 720, err, len(err)), err.value
    fails = []

    def call(fn, *a):
        arr = (ctypes.c_uint64 * len(a))(*a)
        return lib.nxgl4_ext(fns[fn], arr, len(a))

    def case(name, ok, detail=""):
        print("%s: %s %s" % ("PASS" if ok else "FAIL", name, detail))
        if not ok:
            fails.append(name)

    def addr(obj):
        return ctypes.addressof(obj)

    frame = [0]

    def submit(words):
        buf = (ctypes.c_uint32 * len(words))(*words)
        keep["stream"] = buf
        s = Submit(addr(buf), len(words), frame[0])
        frame[0] += 1
        return call("SUBMIT", addr(s))

    def upload(img, pixels):
        data = (ctypes.c_uint8 * (W * H * 4))(*[c for p in pixels for c in p])
        keep["data"] = data
        m = Mem(addr(data), W * H * 4, 0)
        mid = call("MEM_CREATE", addr(m))
        assert mid and mid != BAD_CALL, "mem_create"
        rc = submit([ops["ImageUpdate"] | 12 << 16, img, 0, 0, 0, 0, W, H, 1, mid, 0, W * H * 4, W * 4, ops["End"]])
        assert rc == 0, "submit %x" % rc
        call("MEM_DESTROY", mid)

    def read(img, x=0, y=0, w=W, h=H):
        out = (ctypes.c_uint8 * (w * h * 4))()
        r = Read(img, 0, 0, x, y, 0, w, h, 1, addr(out), w * h * 4, w * 4)
        rc = call("IMAGE_READ", addr(r))
        assert rc == 0, "image_read %x" % rc
        return [tuple(out[4 * k:4 * k + 4]) for k in range(w * h)]

    NxFmt_RGBA8 = 1
    desc = Desc(W, H, 1, 0, NxFmt_RGBA8, 1, 1, 1, 0, 0, 0)   # flags 1: render target
    img = call("IMAGE_CREATE4", addr(desc))
    assert img and img != BAD_CALL, "image_create4"
    base = [(10 + x, 20 + y, 30 + x + y, 255) for y in range(H) for x in range(W)]

    def present(cur, cx, cy, cw, ch, with_cursor=True):
        upload(img, base)
        curbuf = (ctypes.c_uint8 * (cw * ch * 4))(*[c for p in cur for c in p]) if cur else None
        keep["cur"] = curbuf
        p = Present(img, 0, 0, W, H, 0, 0, W, H, 0, 0, frame[0], 0, addr(curbuf) if with_cursor and curbuf else 0, cx, cy, cw, ch)
        return call("PRESENT", addr(p))

    # a 3 x 3 cursor, B, G, R, A: alpha 0 on the border, alpha != 0 (including 1) inside; the colours are distinct so a swap shows
    cur = [(1, 2, 3, 0) if (i % 3 in (0, 2) or j in (0, 2)) else (200, 100, 50, 77) for j in range(3) for i in range(3)]
    cur[4] = (200, 100, 50, 1)
    # the positions: inside; each edge overlapping by one column / row; the corners; wholly outside on each side
    for name, cx, cy in [("inside", 2, 2), ("left edge", -1, 3), ("top edge", 3, -1), ("right edge", W - 2, 3), ("bottom edge", 3, H - 2),
                         ("top-left corner", -2, -2), ("bottom-right corner", W - 1, H - 1), ("wholly left", -3, 2), ("wholly above", 2, -3),
                         ("wholly right", W, 2), ("wholly below", 2, H)]:
        rc = present(cur, cx, cy, 3, 3)
        got = read(img)
        want = expect(base, cur, cx, cy, 3, 3)
        case("%s: present ok, the image holds the stamped texels (alpha 0 kept, alpha != 0 replaced, clipped)" % name, rc == 0 and got == want,
             "" if got == want else "rc=%x" % rc)
    # the cursor is in the source image: image_read of the back buffer (GetBackBuffer) sees it, with no cursor given the image is untouched
    rc = present(cur, 2, 2, 3, 3)
    case("GetBackBuffer reads the cursor after the present", rc == 0 and read(img, 3, 3, 1, 1) == [(50, 100, 200, 1)])
    rc = present(cur, 2, 2, 3, 3, with_cursor=False)
    case("no cursor: the image is exactly what was written", rc == 0 and read(img) == base)
    # the swapchain image (image 0) holds the 1:1 present of the stamped image
    rc = present(cur, 2, 2, 3, 3)
    case("the swapchain image holds the stamped image", rc == 0 and read(0, 0, 0, W, H) == expect(base, cur, 2, 2, 3, 3))
    case("a cursor of size 0 is BAD_CALL", present(cur, 0, 0, 0, 3) == BAD_CALL)
    case("a cursor wider than 4096 is BAD_CALL", present(cur, 0, 0, 4097, 1) == BAD_CALL)
    lib.nxgl4_destroy()
    print("test_gl4_cursor: %s" % ("FAIL %d" % len(fails) if fails else "PASS"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
