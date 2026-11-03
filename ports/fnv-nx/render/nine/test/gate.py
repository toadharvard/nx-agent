#!/usr/bin/env python3
"""gate.py - host end-to-end gate of the D3D9 proxy path (stream S6c-nine).

    python3 -I render/nine/test/gate.py --guest <guest.nso> --lib <libnine_gate.so> --out <dir> [--elf guest.elf]

Runs the ILP32 test guest (test/nine_test.cpp, built with GUEST_ONLY=1 test/build.sh) under S5's Unicorn harness
(tests/s5/emu_guest.py) and serves its ext_call(NINE_EXT_MODULE, ...) from the real host-side Nine code, built for
x86-64 on softpipe (gate/build.sh). As on the Switch, guest and host share addresses: every guest region is mapped
at the same virtual address in this process (mmap MAP_FIXED_NOREPLACE + uc_mem_map_ptr), and the host library
allocates everything from a low-VA arena that is mapped into Unicorn too, so the host receives guest pointers
directly and the guest can use the pointers the host returns (Lock, LockRect). Address randomization is turned
off for this process (personality ADDR_NO_RANDOMIZE, re-exec) so the brk heap cannot sit on those ranges.

Checks (PASS when all hold): the guest returns 0 and prints "fnvnx nine test: OK"; clear frames match their exact
colour and bar; draw frames match the reference (grey frame, centre rectangle split into red/green/blue/white
quadrants), ignoring 2-pixel bands at the rectangle and quadrant edges. Writes frame_NNN.png, ref_draw.png and, on
mismatch, diff_NNN.png into --out.
"""
import argparse
import ctypes
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
ADDR_NO_RANDOMIZE = 0x0040000
NINE_EXT_MODULE = 9
EXT_NO_MODULE = 0xFFFFFFFFFFFFFF01
CLEAR_FRAMES = 240                       # test/nine_test.cpp
DRAW_FRAMES = 4
W, H = 1280, 720


def ensure_no_aslr():
    libc = ctypes.CDLL(None, use_errno=True)
    cur = libc.personality(0xFFFFFFFF)
    if cur & ADDR_NO_RANDOMIZE:
        return
    if libc.personality(cur | ADDR_NO_RANDOMIZE) < 0:
        raise SystemExit("gate: personality(ADDR_NO_RANDOMIZE) failed")
    os.execv(sys.executable, sys.orig_argv)


def clear_color(f):
    return ((f * 37 + 16) & 255, (f * 91 + 64) & 255, (f * 53 + 128) & 255)


def write_png(path, rgb):
    h, w, _ = rgb.shape
    raw = b"".join(b"\0" + rgb[y].tobytes() for y in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--guest", required=True)
    ap.add_argument("--lib", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--elf", help="guest ELF for symbolized crash traces")
    a = ap.parse_args(argv)
    ensure_no_aslr()
    import numpy as np
    sys.path.insert(0, os.path.join(ROOT, "tests", "s5"))
    import emu_guest
    from unicorn import UC_PROT_READ, UC_PROT_WRITE, Uc

    libc = ctypes.CDLL(None, use_errno=True)
    libc.mmap.restype = ctypes.c_void_p
    libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]
    MAP_PRIVATE, MAP_ANONYMOUS, MAP_FIXED_NOREPLACE = 0x02, 0x20, 0x100000

    def low_va_uc(arch, mode):
        """Unicorn whose mem_map backs every guest mapping with host memory at the same virtual address."""
        uc = Uc(arch, mode)   # Unicorn 2's Uc is a factory (returns the arch class): patch the instance

        def mem_map(address, size, perms=7):
            p = libc.mmap(address, size, 3, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0)
            if p != address:
                raise SystemExit(f"gate: cannot map {address:#x}+{size:#x} at the same host address "
                                 f"(errno {ctypes.get_errno()})")
            uc.mem_map_ptr(address, size, perms, address)
        uc.mem_map = mem_map
        return uc
    emu_guest.Uc = low_va_uc

    lib = ctypes.CDLL(os.path.abspath(a.lib))     # its constructor maps the low-VA arena
    lib.nine_ext.restype = ctypes.c_uint64
    lib.nine_ext.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_uint32]
    for fn in ("gate_arena_base", "gate_arena_size", "gate_arena_peak"):
        getattr(lib, fn).restype = ctypes.c_uint64
    lib.gate_frame_count.restype = ctypes.c_int
    lib.gate_get_frame.restype = ctypes.c_int
    lib.gate_get_frame.argtypes = [ctypes.c_int] + [ctypes.POINTER(ctypes.c_int)] * 4 + [ctypes.POINTER(ctypes.c_void_p)]
    total = CLEAR_FRAMES + DRAW_FRAMES
    keep = [0, 1, 7, 100, CLEAR_FRAMES - 1] + list(range(CLEAR_FRAMES, total))
    karr = (ctypes.c_int * len(keep))(*keep)
    lib.gate_keep_frames(karr, len(keep))

    class GateEmu(emu_guest.Emu):
        def __init__(self, nso, root):
            super().__init__(nso, root)
            base, size = lib.gate_arena_base(), lib.gate_arena_size()
            self.uc.mem_map_ptr(base, size, UC_PROT_READ | UC_PROT_WRITE, base)   # host objects the guest touches
            self.loops = self.ext_calls = 0

        def h_console_write(self, b, n, *_):     # also live, so a native crash still shows how far the guest got
            data = bytes(self.uc.mem_read(b, n))
            for line in data.decode(errors="replace").splitlines():
                print("   | " + line)
            sys.stdout.flush()
            self.console += data
            return n

        def h_main_loop(self, *_):
            self.loops += 1
            return 1 if self.loops <= total else 0

        def h_ext_call(self, module, fn, args, nargs, *_):
            if module != NINE_EXT_MODULE:
                return EXT_NO_MODULE
            self.ext_calls += 1
            return lib.nine_ext(fn & 0xFFFFFFFF, args, nargs & 0xFFFFFFFF)

    os.makedirs(a.out, exist_ok=True)
    root = os.path.join(a.out, "sdmc")
    os.makedirs(root, exist_ok=True)
    emu = GateEmu(a.guest, root)
    emu.budget = 2_000_000_000
    if a.elf:
        emu.load_symbols(a.elf)
    try:
        rc = emu.run()
    except SystemExit as e:                 # the guest console was already printed live (h_console_write)
        print(f"gate: STOPPED: {e}")
        return 1
    out = emu.console.decode(errors="replace")
    print(f"== guest rc={rc:#x} host_calls={emu.calls} ext_calls={emu.ext_calls} frames={lib.gate_frame_count()} "
          f"arena_peak={lib.gate_arena_peak() / 1048576:.1f} MiB")
    ok = rc == 0 and "fnvnx nine test: OK" in out and lib.gate_frame_count() == total

    # reference for the textured draw
    ref = np.full((H, W, 3), 64, np.uint8)
    for (y0, y1, x0, x1), c in [((180, 360, 320, 640), (255, 0, 0)), ((180, 360, 640, 960), (0, 255, 0)),
                                ((360, 540, 320, 640), (0, 0, 255)), ((360, 540, 640, 960), (255, 255, 255))]:
        ref[y0:y1, x0:x1] = c
    band = np.zeros((H, W), bool)
    for x in (320, 640, 960):
        band[178:542, x - 2:x + 2] = True
    for y in (180, 360, 540):
        band[y - 2:y + 2, 318:962] = True
    write_png(os.path.join(a.out, "ref_draw.png"), ref)

    for f in keep:
        w, h, stride, fmt, data = (ctypes.c_int(), ctypes.c_int(), ctypes.c_int(), ctypes.c_int(), ctypes.c_void_p())
        if not lib.gate_get_frame(f, ctypes.byref(w), ctypes.byref(h), ctypes.byref(stride), ctypes.byref(fmt),
                                  ctypes.byref(data)):
            print(f"   frame {f}: not captured")
            ok = False
            continue
        buf = np.ctypeslib.as_array((ctypes.c_uint8 * (stride.value * h.value)).from_address(data.value))
        bgrx = buf.reshape(h.value, stride.value)[:, :w.value * 4].reshape(h.value, w.value, 4)
        rgb = bgrx[:, :, [2, 1, 0]].copy()
        write_png(os.path.join(a.out, f"frame_{f:03d}.png"), rgb)
        if (w.value, h.value, fmt.value) != (W, H, 1):
            print(f"   frame {f}: {w.value}x{h.value} format {fmt.value}, expected {W}x{H} BGRX")
            ok = False
            continue
        if f < CLEAR_FRAMES:
            exp = np.empty_like(rgb)
            exp[:] = clear_color(f)
            exp[330:390, 0:f % 1280] = 255
            mask = np.ones((H, W), bool)
            what = f"clear {clear_color(f)} + bar {f % 1280} px"
        else:
            exp, mask, what = ref, ~band, "textured draw (ref_draw.png)"
        diff = (np.abs(rgb.astype(np.int16) - exp.astype(np.int16)).max(axis=2) > 2) & mask
        bad = int(diff.sum())
        print(f"   frame {f}: {what}: {'ok' if bad == 0 else f'{bad} pixels differ'}")
        if bad:
            ok = False
            d = np.zeros_like(rgb)
            d[diff] = (255, 0, 255)
            write_png(os.path.join(a.out, f"diff_{f:03d}.png"), d)
            ys, xs = np.nonzero(diff)
            print(f"      first at ({xs[0]}, {ys[0]}): got {tuple(rgb[ys[0], xs[0]])} want {tuple(exp[ys[0], xs[0]])}")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
