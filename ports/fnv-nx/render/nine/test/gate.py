#!/usr/bin/env python3
"""gate.py - host end-to-end gate of the D3D9 proxy path (stream S6c-nine).

    python3 -I render/nine/test/gate.py --guest <guest.nso> --lib <libnine_gate.so> --out <dir> [--elf guest.elf]
    python3 -I render/nine/test/gate.py --game --guest <game guest.nso> --elf <fnvnx.elf> --lib ... --out <dir>

Runs the ILP32 test guest (test/nine_test.cpp, built with GUEST_ONLY=1 test/build.sh) under S5's Unicorn harness
(tests/s5/emu_guest.py) and serves its ext_call(NINE_EXT_MODULE, ...) from the real host-side Nine code, built for
x86-64 on softpipe (gate/build.sh). As on the Switch, guest and host share addresses: every guest region is mapped
at the same virtual address in this process (mmap MAP_FIXED_NOREPLACE + uc_mem_map_ptr), and the host library
allocates everything from a low-VA arena that is mapped into Unicorn too, so the host receives guest pointers
directly and the guest can use the pointers the host returns (Lock, LockRect). Address randomization is turned
off for this process (personality ADDR_NO_RANDOMIZE, re-exec) so the brk heap cannot sit on those ranges.

Checks (PASS when all hold): the guest returns 0 and prints "fnvnx nine test: OK" (its D3DX checks and the
lock-after-draw / Reset readbacks all passed); clear frames match their exact colour and bar; draw frames match the
reference (grey frame, centre rectangle split into red/green/blue/white quadrants; the last two, after the texture
was rewritten following two draws and after Reset, rotated: green/blue/white/red), ignoring 2-pixel bands at the
rectangle and quadrant edges; at least 3 heap-statistics lines reached the host (render/nine/heaplog.c). Writes
frame_NNN.png, ref_draw.png, ref_draw_rotated.png and, on mismatch, diff_NNN.png into --out. NINE_MANAGED_SYSMEM=1
runs Nine with its system-memory copy of MANAGED resources (without patch 0007's path) for comparison.

--game: the guest is the game image (game/build.sh). It boots as far as it goes (without FalloutNV.exe the image
is empty and static init stops, J-0021), then the gate calls into it the way the engine does: LoadLibraryA("d3d9.dll"),
GetProcAddress("Direct3DCreate9") (must be the proxy), Direct3DCreate9 -> Nine, GetAdapterCount, GetAdapterIdentifier,
Release, plus the IAT lookup of d3d9!D3DPERF_SetOptions. Run through gate/gate_launcher (FNVNX_GATE_LAUNCHER set),
the original-image window is host memory at the same address like every other guest region and the gate checks
it both ways (host write -> emulator read, emulator write -> host read); under the system python3 (a non-PIE
executable mapped at 0x00200000..0x01dxxxxx) the window can only be emulator-only memory, which is noted.

    gate/gate_launcher -I render/nine/test/gate.py --game ...     (gate/build.sh builds the launcher)
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
IMAGE_BASE, IMAGE_END = 0x00400000, 0x02100000   # FalloutNV.exe image window (TOOLCHAIN.md §5)


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


def elf_symbols(elf):
    import subprocess
    nm = os.environ.get("NM", "aarch64-linux-gnu-nm")
    out = subprocess.run([nm, "--defined-only", elf], capture_output=True, text=True, check=True).stdout
    syms = {}
    for line in out.splitlines():
        f = line.split()
        if len(f) == 3:
            syms.setdefault(f[2], int(f[0], 16))
    return syms


def map_object_of(map_path, section):
    """Input object that provided the output section `section` (GNU ld map: section line, then address line)."""
    try:
        lines = open(map_path, errors="replace").read().splitlines()
    except OSError:
        return None
    for i, l in enumerate(lines):
        if l.strip().split(" ")[0] == section:
            for cand in (l, lines[i + 1] if i + 1 < len(lines) else ""):
                parts = cand.split()
                if parts and parts[-1].endswith(".o"):
                    return parts[-1]
    return None


def window_check(emu, unshared):
    """The original-image window must be the same memory in the host process and in the emulator (as on the
    Switch, where host code and guest share one address space): host write -> emulator read and back."""
    win = [(b, n) for b, n in getattr(emu, "mapped_fixed", []) if b < IMAGE_END and b + n > IMAGE_BASE]
    if not win:
        return ("image window mapped by the boot (map_fixed)", False, "not mapped")
    base, size = win[0]
    if any(u[0] == base for u in unshared):
        return ("image window shared with the host process", False,
                f"{base:#x}+{size:#x} emulator-only (run gate.py through gate/gate_launcher)")
    uc = emu.uc
    addr = base + size - 64                       # last bytes of the first window mapping: restored afterwards
    saved = bytes(uc.mem_read(addr, 32))
    ctypes.memmove(addr, b"fnvnx gate: host -> guest\0\0\0\0\0\0", 32)
    ok1 = bytes(uc.mem_read(addr, 32)).startswith(b"fnvnx gate: host -> guest")
    uc.mem_write(addr, b"fnvnx gate: guest -> host\0\0\0\0\0\0")
    ok2 = ctypes.string_at(addr, 32).startswith(b"fnvnx gate: guest -> host")
    uc.mem_write(addr, saved)
    return ("image window shared with the host process", ok1 and ok2,
            f"{base:#x}+{size:#x}, host write seen by the emulator {ok1}, emulator write seen by the host {ok2}")


def game_probe(emu, a, lib, emu_guest, unshared):
    """Boot the game image as far as it goes (trace mode without FalloutNV.exe: static init stops, J-0021), then call
    into the guest the way the engine does: LoadLibraryA("d3d9.dll") -> GetProcAddress("Direct3DCreate9") -> call
    it -> IDirect3D9::GetAdapterCount / GetAdapterIdentifier / Release through the proxy vtable, served by Nine."""
    from unicorn.arm64_const import (UC_ARM64_REG_LR, UC_ARM64_REG_PC, UC_ARM64_REG_SP, UC_ARM64_REG_X0,
                                     UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3)
    syms = elf_symbols(a.elf)
    if getattr(emu, "relocatable", False):         # S5's relocatable guest: link base 0x30000000, run elsewhere
        lo, hi, delta = emu.link_base, emu.link_base + emu.size, emu.base - emu.link_base
        syms = {k: v + delta if lo <= v < hi else v for k, v in syms.items()}
        print(f"   relocatable guest: link base {lo:#x}, running at {emu.base:#x}")
    need = ["LoadLibraryA", "GetProcAddress", "fnvnx_win32_lookup_export", "Direct3DCreate9", "D3DPERF_SetOptions",
            "fnvnx_d3d9_register"]
    missing = [n for n in need if n not in syms]
    if missing:
        print(f"gate: FAIL: symbols missing from the game image: {missing}")
        return 1
    mp = os.path.splitext(a.elf)[0] + ".map"
    for n in ("Direct3DCreate9", "fnvnx_d3d9_register"):
        if n in syms:
            print(f"   {n} = {syms[n]:#x} from {map_object_of(mp, '.text.' + n)}")
    try:
        rc = emu.run()
        print(f"== game guest returned {rc:#x}")
    except SystemExit as e:
        print(f"== game boot stopped (expected without the exe): {e}")
    print(f"   host calls {emu.calls}, ext calls during boot {emu.ext_calls}")

    uc = emu.uc
    scratch = emu_guest.HOST_PAGE + 0x400           # unused part of the host page
    sp = emu.stack_for(emu_guest.MAX_THREADS)        # a stack slot no guest thread uses
    buf = sp - 0x8000

    def put(addr, s):
        uc.mem_write(addr, s.encode() + b"\0")
        return addr

    def call(fn, *args):
        regs = (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3)
        for r, v in zip(regs, args):
            uc.reg_write(r, v)
        uc.reg_write(UC_ARM64_REG_SP, sp)
        uc.reg_write(UC_ARM64_REG_LR, emu_guest.RETURN_ADDR)
        emu.cur = emu.threads[1]
        emu.cur.state = "running"
        try:
            uc.emu_start(fn, emu_guest.RETURN_ADDR, count=200_000_000)
        except emu_guest.UcError as e:
            raise SystemExit(f"probe call {fn:#x} faulted: {e} at pc {uc.reg_read(UC_ARM64_REG_PC):#x}")
        if uc.reg_read(UC_ARM64_REG_PC) != emu_guest.RETURN_ADDR:
            raise SystemExit(f"probe call {fn:#x} did not return (pc {uc.reg_read(UC_ARM64_REG_PC):#x})")
        return uc.reg_read(UC_ARM64_REG_X0)

    def rd32(addr):
        return struct.unpack("<I", uc.mem_read(addr, 4))[0]

    checks = [window_check(emu, unshared)]
    if not os.environ.get("FNVNX_GATE_LAUNCHER") and not checks[0][1]:
        print(f"   note: {checks[0][0]}: {checks[0][2]} (not required without the launcher)")
        checks = []
    heap0 = lib.gate_heaplog_lines()
    checks.append(("heap statistics posted during boot (HEAPLOG_S build: nine_register.cpp starts heaplog.c)",
                   heap0 >= 1 or not a.heaplog, f"{heap0} line(s)"))
    try:
        dll = put(scratch, "d3d9.dll")
        name = put(scratch + 0x20, "Direct3DCreate9")
        perf = put(scratch + 0x40, "D3DPERF_SetOptions")
        h = call(syms["LoadLibraryA"], dll) & 0xFFFFFFFF
        checks.append(("LoadLibraryA(\"d3d9.dll\") != NULL", h != 0, f"{h:#x}"))
        p = call(syms["GetProcAddress"], h, name) & 0xFFFFFFFF
        checks.append(("GetProcAddress(d3d9, \"Direct3DCreate9\") == proxy Direct3DCreate9", p == syms["Direct3DCreate9"],
                       f"{p:#x}"))
        q = call(syms["fnvnx_win32_lookup_export"], dll, perf) & 0xFFFFFFFF
        checks.append(("IAT lookup d3d9!D3DPERF_SetOptions resolves (S3 binding path)", q == syms["D3DPERF_SetOptions"],
                       f"{q:#x}"))
        ext0 = emu.ext_calls
        d3d = call(p, 32) & 0xFFFFFFFF
        checks.append(("Direct3DCreate9(D3D_SDK_VERSION) through ext_call -> Nine", d3d != 0 and emu.ext_calls > ext0,
                       f"{d3d:#x}, {emu.ext_calls - ext0} ext call(s)"))
        if d3d:
            vt = rd32(d3d)
            n = call(rd32(vt + 4 * 4), d3d) & 0xFFFFFFFF                     # IDirect3D9::GetAdapterCount
            checks.append(("IDirect3D9::GetAdapterCount() == 1", n == 1, str(n)))
            hr = call(rd32(vt + 5 * 4), d3d, 0, 0, buf) & 0xFFFFFFFF         # GetAdapterIdentifier(0, 0, &id)
            desc = bytes(uc.mem_read(buf + 512, 512)).split(b"\0")[0].decode(errors="replace")
            checks.append(("IDirect3D9::GetAdapterIdentifier", hr == 0 and "Nine" in desc, f"hr={hr:#x} '{desc}'"))
            r = call(rd32(vt + 2 * 4), d3d) & 0xFFFFFFFF                     # Release
            checks.append(("IDirect3D9::Release() == 0", r == 0, str(r)))
    except SystemExit as e:
        checks.append(("probe", False, str(e)))
    ok = all(c[1] for c in checks)
    for what, good, detail in checks:
        print(f"   {'ok  ' if good else 'FAIL'} {what}: {detail}")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--guest", required=True)
    ap.add_argument("--lib", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--elf", help="guest ELF for symbolized crash traces (required with --game)")
    ap.add_argument("--game", action="store_true", help="the guest is the game image (render/nine/game/build.sh): "
                    "boot it as far as it goes, then probe d3d9.dll -> Direct3DCreate9 -> Nine")
    ap.add_argument("--heaplog", action="store_true", help="--game: the image was built with HEAPLOG_S (expect heap "
                    "statistics lines from the boot)")
    a = ap.parse_args(argv)
    if a.game and not a.elf:
        ap.error("--game needs --elf (symbol addresses)")
    ensure_no_aslr()
    import numpy as np
    sys.path.insert(0, os.path.join(ROOT, "tests", "s5"))
    import emu_guest
    from unicorn import UC_PROT_READ, UC_PROT_WRITE, Uc

    libc = ctypes.CDLL(None, use_errno=True)
    libc.mmap.restype = ctypes.c_void_p
    libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]
    MAP_PRIVATE, MAP_ANONYMOUS, MAP_FIXED_NOREPLACE = 0x02, 0x20, 0x100000

    unshared = []

    def low_va_uc(arch, mode):
        """Unicorn whose mem_map backs every guest mapping with host memory at the same virtual address."""
        uc = Uc(arch, mode)   # Unicorn 2's Uc is a factory (returns the arch class): patch the instance

        def mem_map(address, size, perms=7):
            p = libc.mmap(address, size, 3, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0)
            if p == address:
                uc.mem_map_ptr(address, size, perms, address)
                return
            if address < IMAGE_END and address + size > IMAGE_BASE:
                # The original-image window (S3's data image, map_fixed) collides with this (non-PIE) python's own
                # image: emulator-only memory there. Host code must not dereference image addresses in this mode.
                print(f"gate: note: {address:#x}+{size:#x} (original-image window) is emulator-only memory "
                      f"(the host process occupies it)")
                type(uc).mem_map(uc, address, size, perms)
                unshared.append((address, size))
                return
            raise SystemExit(f"gate: cannot map {address:#x}+{size:#x} at the same host address "
                             f"(errno {ctypes.get_errno()})")
        uc.mem_map = mem_map
        return uc
    emu_guest.Uc = low_va_uc

    lib = ctypes.CDLL(os.path.abspath(a.lib))     # its constructor maps the low-VA arena
    lib.nine_ext.restype = ctypes.c_uint64
    lib.nine_ext.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_uint32]
    for fn in ("gate_arena_base", "gate_arena_size", "gate_arena_peak"):
        getattr(lib, fn).restype = ctypes.c_uint64
    lib.gate_frame_count.restype = ctypes.c_int
    lib.gate_heaplog_lines.restype = ctypes.c_uint
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
    if a.game:
        return game_probe(emu, a, lib, emu_guest, unshared)
    try:
        rc = emu.run()
    except SystemExit as e:                 # the guest console was already printed live (h_console_write)
        print(f"gate: STOPPED: {e}")
        return 1
    out = emu.console.decode(errors="replace")
    print(f"== guest rc={rc:#x} host_calls={emu.calls} ext_calls={emu.ext_calls} frames={lib.gate_frame_count()} "
          f"arena_peak={lib.gate_arena_peak() / 1048576:.1f} MiB")
    ok = rc == 0 and "fnvnx nine test: OK" in out and lib.gate_frame_count() == total

    heap_lines = lib.gate_heaplog_lines()
    print(f"   heap statistics lines (render/nine/heaplog.c -> host): {heap_lines}")
    ok = ok and heap_lines >= 3

    # reference for the textured draw: quadrants red, green / blue, white; the last two draw frames (after the
    # lock-after-draw rewrite, and after Reset) rotated: green, blue / white, red
    quads = [(180, 360, 320, 640), (180, 360, 640, 960), (360, 540, 320, 640), (360, 540, 640, 960)]
    cols = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]
    ref = np.full((H, W, 3), 64, np.uint8)
    ref_rot = ref.copy()
    for i, (y0, y1, x0, x1) in enumerate(quads):
        ref[y0:y1, x0:x1] = cols[i]
        ref_rot[y0:y1, x0:x1] = cols[(i + 1) % 4]
    band = np.zeros((H, W), bool)
    for x in (320, 640, 960):
        band[178:542, x - 2:x + 2] = True
    for y in (180, 360, 540):
        band[y - 2:y + 2, 318:962] = True
    write_png(os.path.join(a.out, "ref_draw.png"), ref)
    write_png(os.path.join(a.out, "ref_draw_rotated.png"), ref_rot)

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
        elif f < CLEAR_FRAMES + 2:
            exp, mask, what = ref, ~band, "textured draw (ref_draw.png)"
        else:
            exp, mask, what = ref_rot, ~band, ("relocked texture" if f == CLEAR_FRAMES + 2 else
                                               "after Reset") + " (ref_draw_rotated.png)"
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
