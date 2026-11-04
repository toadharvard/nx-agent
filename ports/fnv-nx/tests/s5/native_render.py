#!/usr/bin/env python3
"""native_render.py - the emulated boot (tests/s5/emu_guest.py) on the native renderer's engine path.

    python3 -I tests/s5/native_render.py <emu_guest.py arguments> [--native-render OBJ] [--gpu LIBNXGL] [--frames DIR]

--gpu LIBNXGL  forwards the guest's nxgpu calls (ext_call module 10, render/native/front ApiBe) to S6n's GL reference
               backend, as tests/s6n/emu_n0.py does for the renderer self-test. Counts images, shaders, submits and
               presented frames ("gpu" lines at the end).
--frames DIR   with --gpu: the back buffer of presented frames 1, 2, 4, 8, ... (at most 8) as PNG files in DIR.
--native-render OBJ
               the stand-in for a LINK_OVERRIDES=render link on a guest linked without it. OBJ is
               tests/s5/native_render_inject.sh's nxrender_inject.o: libnxrender's sources and
               toolchain/game/native_device.cpp in one ILP32 relocatable object. It is mapped after the guest (its
               undefined names bind to the guest's: newlib, fnvnx_host), and the entry of each engine function that
               render/native/overrides.tsv replaces (the address's native in --addrmap) branches to the replacement,
               as fnvlink's --override binds it in the render link: calls, vtable slots and the data image's code
               pointers all reach it. Every other function the object defines under a name the guest defines too
               replaces the guest's: the object carries the null device (toolchain/game/null_d3d9.cpp) with the
               device-created hook, so the native renderer comes up with the device on a guest linked before it.
               Needs --elf and --addrmap.

Milestones of the native path (milestones.txt names nx:: functions) bind to the injected definitions too. Everything
else is emu_guest.py's: this script only adds to its Emu class and calls its main.
"""
from __future__ import annotations

import ctypes
import os
import re
import struct
import subprocess
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "s6n"))
import emu_guest  # noqa: E402

from unicorn import UC_HOOK_CODE, UC_PROT_ALL  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OVERRIDES = os.path.join(ROOT, "render", "native", "overrides.tsv")
NXGPU_MODULE = 10
FN_INFO, FN_MEM_CREATE, FN_MEM_GPU_ADDR, FN_IMAGE_CREATE, FN_SAMPLER_CREATE, FN_SHADER_CREATE, FN_SUBMIT, \
    FN_WAIT_FRAME, FN_READBACK, FN_SHUTDOWN = range(10)
NULL_CREATE = re.compile(r"^_ZN\d+Null_IDirect3D9(Ex)?\d+CreateDevice(Ex)?E")

# R_AARCH64_P32_* (ILP32) relocations of a GCC -O2 -fPIE object
ABS32, PREL32, ADR_PG, ADD_LO12, LDST8, LDST16, LDST32, LDST64, LDST128, TSTBR14, CONDBR19, JUMP26, CALL26, \
    GOT_PAGE, GOT_LO12 = 1, 3, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 26, 27
LDST_SHIFT = {LDST8: 0, LDST16: 1, LDST32: 2, LDST64: 3, LDST128: 4}


class ObjError(Exception):
    pass


def link_object(data, place, resolve):
    """Lay out the SHF_ALLOC sections of an ELF32 AArch64 relocatable object at `place`, apply its RELA relocations
    (undefined names through resolve(name) -> address or None for a weak one) and return (image bytes, {defined
    global/weak function or object name: address}, {defined function name: address}). A GOT for GOT_PAGE/GOT_LO12
    follows the sections."""
    if data[:5] != b"\x7fELF\x01" or struct.unpack_from("<H", data, 0x12)[0] != 183:
        raise ObjError("not an ELF32 AArch64 (ILP32) object")
    shoff, = struct.unpack_from("<I", data, 0x20)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 0x2E)
    sh = [struct.unpack_from("<IIIIIIIIII", data, shoff + i * shentsize) for i in range(shnum)]

    def shname(i):
        return data[sh[shstrndx][4] + sh[i][0]:].split(b"\0")[0].decode()
    layout, blob = {}, bytearray()
    for i, h in enumerate(sh):
        if h[2] & 2 and h[1] in (1, 8, 14, 15) and h[5] and shname(i) != ".eh_frame":
            al = max(h[8], 1)
            off = (len(blob) + al - 1) & ~(al - 1)
            blob += b"\0" * (off - len(blob)) + (data[h[4]:h[4] + h[5]] if h[1] != 8 else b"\0" * h[5])
            layout[i] = place + off
            if shname(i).startswith((".init_array", ".preinit_array", ".ctors")):
                raise ObjError(f"{shname(i)}: static constructors are not run by the loader")
    symtab = next(i for i, h in enumerate(sh) if h[1] == 2)
    strs = sh[sh[symtab][6]][4]
    syms = []
    for k in range(sh[symtab][5] // 16):
        name_off, value, _, info, _, shndx = struct.unpack_from("<IIIBBH", data, sh[symtab][4] + 16 * k)
        syms.append((data[strs + name_off:].split(b"\0")[0].decode(), value, info, shndx))
    got = {}
    nrel = sum(h[5] // 12 for h in sh if h[1] == 4 and h[7] in layout)
    got_base = place + ((len(blob) + 15) & ~15)
    blob += b"\0" * (got_base - place - len(blob))
    missing = []

    def addr_of(k):
        name, value, info, shndx = syms[k]
        if shndx in layout:
            return layout[shndx] + value
        if shndx == 0xFFF1:   # SHN_ABS
            return value
        a = resolve(name)
        if a is None:
            if info >> 4 == 2:   # weak undefined
                return 0
            missing.append(name)
            return 0
        return a

    def got_slot(k):
        if k not in got:
            got[k] = got_base + 4 * len(got)
        return got[k]
    fixes = []
    for h in sh:
        if h[1] != 4 or h[7] not in layout:
            continue
        for r in range(h[5] // 12):
            off, info, add = struct.unpack_from("<IIi", data, h[4] + 12 * r)
            fixes.append((info & 0xFF, info >> 8, add, layout[h[7]] + off))
    for typ, k, add, P in fixes:   # GOT slots first, so the GOT size is known before it is written
        if typ in (GOT_PAGE, GOT_LO12):
            got_slot(k)
    blob += b"\0" * (4 * len(got))
    for k, slot in got.items():
        struct.pack_into("<I", blob, slot - place, addr_of(k) & 0xFFFFFFFF)
    for typ, k, add, P in fixes:
        at = P - place
        S = addr_of(k) + add
        if typ == ABS32:
            struct.pack_into("<I", blob, at, S & 0xFFFFFFFF)
            continue
        if typ == PREL32:
            struct.pack_into("<i", blob, at, ((S - P + (1 << 31)) & 0xFFFFFFFF) - (1 << 31))
            continue
        (w,) = struct.unpack_from("<I", blob, at)
        if typ in (JUMP26, CALL26):
            d = (S - P) >> 2
            if not -(1 << 25) <= d < (1 << 25):
                raise ObjError(f"branch to {S:#x} from {P:#x} out of range")
            w = (w & 0xFC000000) | (d & 0x3FFFFFF)
        elif typ == CONDBR19:
            w = (w & 0xFF00001F) | ((((S - P) >> 2) & 0x7FFFF) << 5)
        elif typ == TSTBR14:
            w = (w & 0xFFF8001F) | ((((S - P) >> 2) & 0x3FFF) << 5)
        elif typ in (ADR_PG, GOT_PAGE):
            T = got[k] if typ == GOT_PAGE else S
            pg = ((T & ~0xFFF) - (P & ~0xFFF)) >> 12
            w = (w & 0x9F00001F) | ((pg & 3) << 29) | (((pg >> 2) & 0x7FFFF) << 5)
        elif typ == ADD_LO12:
            w = (w & 0xFFC003FF) | ((S & 0xFFF) << 10)
        elif typ in LDST_SHIFT:
            w = (w & 0xFFC003FF) | (((S & 0xFFF) >> LDST_SHIFT[typ]) << 10)
        elif typ == GOT_LO12:
            w = (w & 0xFFC003FF) | (((got[k] & 0xFFF) >> 2) << 10)
        else:
            raise ObjError(f"relocation type {typ} at {P:#x} not supported")
        struct.pack_into("<I", blob, at, w)
    if missing:
        raise ObjError("undefined in the guest: " + ", ".join(sorted(set(missing))))
    defined, funcs = {}, {}
    for name, value, info, shndx in syms:
        if shndx in layout and info >> 4 in (1, 2) and name:
            defined[name] = layout[shndx] + value
            if info & 0xF == 2:
                funcs[name] = layout[shndx] + value
    return bytes(blob), defined, funcs, nrel


def i32(demangled):
    """newlib's and glibc's ILP32 spellings of [u]int32_t made equal (long long stays itself)"""
    return re.sub(r"\blong\b", "int", demangled.replace("long long", "LONGLONG")).replace("LONGLONG", "long long")


def demangle(names):
    if not names:
        return []
    out = subprocess.run(["aarch64-linux-gnu-c++filt"], input="\n".join(names) + "\n", capture_output=True,
                         text=True).stdout.splitlines()
    return out if len(out) == len(names) else list(names)


def png(path, w, h, rgba):
    raw = b"".join(b"\0" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


class NativeRenderEmu(emu_guest.Emu):
    nr_opts = {}

    # -- guest symbols at their run-time addresses (functions and data), from nm --------------------------------
    def nr_guest_syms(self, elf):
        out = subprocess.run(["aarch64-linux-gnu-nm", "--defined-only", elf], capture_output=True, text=True).stdout
        g = {}
        for line in out.splitlines():
            f = line.split()
            if len(f) == 3:
                a = int(f[0], 16)
                g.setdefault(f[2], a - self.link_base + self.base if self.relocatable and f[1] not in "aA" else a)
        return g

    def nr_install(self):
        o = self.nr_opts
        self.nr = {"redirected": 0, "missing": [], "device_hook": None, "replaced": 0}
        elf, addrmap = o.get("elf"), o.get("addrmap")
        if not elf or not addrmap:
            raise SystemExit("--native-render needs --elf and --addrmap")
        guest = self.nr_guest_syms(elf)
        place = getattr(self, "inject_place", (self.base + self.size + 0x1FFFF) & ~0xFFFF)
        data = open(o["native_render"], "rb").read()
        try:
            blob, defined, funcs, nrel = link_object(data, place, guest.get)
        except ObjError as e:
            raise SystemExit(f"--native-render {o['native_render']}: {e}")
        size = (len(blob) + 0xFFFF) & ~0xFFFF
        self.uc.mem_map(place, size, UC_PROT_ALL)
        self.uc.mem_write(place, blob)
        self.inject_place = place + size
        self.nr_funcs = funcs
        # symbols for traces and milestones: the injected functions at their run-time addresses (code_syms holds
        # link addresses, code_addr converts them back)
        conv = (lambda a: a - self.base + self.link_base) if self.relocatable else (lambda a: a)
        for n, a in funcs.items():
            self.code_syms.setdefault(n, conv(a))
        pairs = sorted(list(zip(self.sym_addrs, self.sym_names)) + [(a, [n]) for n, a in funcs.items()])
        self.sym_addrs, self.sym_names = [p[0] for p in pairs], [p[1] for p in pairs]
        # the override rows: address -> the guest's native for it (addrmap native_symbol, else __fnv_<addr>)
        native = {}
        for line in open(addrmap, errors="replace"):
            f = line.rstrip("\n").split("\t")
            if len(f) >= 4 and not f[0].startswith("#") and f[1] == "F":
                native[int(f[0], 16)] = [s for s in (f[3], f[2]) if s]
        rows = []
        for line in open(o.get("overrides", OVERRIDES)):
            f = line.rstrip("\n").split("\t")
            if len(f) < 2 or line.startswith("#") or "N1-pending" in (f[2] if len(f) > 2 else ""):
                continue
            if not re.fullmatch(r"[0-9a-fA-F]+", f[0]):
                self.nr["missing"].append(f"{f[0]} (slot rows need the image vftables: not handled)")
                continue
            rows.append((int(f[0], 16), f[1]))
        # names match demangled, with the two spellings of [u]int32_t equal: newlib's ILP32 [u]int32_t is [unsigned]
        # long (the table's names, the render link's), glibc's is [unsigned] int (this object, Ubuntu's cross g++)
        loose = {}
        for n, d in zip(defined, demangle(list(defined))):
            loose.setdefault(i32(d), n)
        row_dm = demangle([ours for _, ours in rows])
        for (addr, ours), dm in zip(rows, row_dm):
            name = ours if ours in defined else loose.get(i32(dm))
            at = next((guest[s] for s in native.get(addr, []) + [f"__fnv_{addr:08x}"] if s in guest), None)
            if name is None or at is None:
                self.nr["missing"].append(f"{addr:08x} {ours}: " + ("not in the object" if name is None else
                                                                   "no native in the guest"))
                continue
            self.nr_branch(at, defined[name])
            self.nr["redirected"] += 1
        # every other function the object defines that the guest defines too is replaced by the object's: the null
        # device as it is now (the device-created hook into native_device.cpp, the adapter's mode list)
        replaced = [n for n in funcs if n in guest and guest[n] != funcs[n]]
        for n in replaced:
            self.nr_branch(guest[n], funcs[n])
        self.nr["replaced"] = len(replaced)
        print(f"   native-render: {os.path.basename(o['native_render'])} at {place:#x} ({len(blob)} bytes, {nrel} "
              f"relocations); {self.nr['redirected']} of {len(rows)} overrides.tsv rows redirected; {len(replaced)} "
              f"guest functions replaced by the object's of the same name")
        for m in self.nr["missing"]:
            print(f"   native-render: not redirected: {m}")
        self.nr["device_hook"] = ("object's null device" if "fnvnx_d3d9_device_created" in funcs and any(
            NULL_CREATE.search(n) for n in replaced) else "guest's null device" if "fnvnx_d3d9_device_created" in guest
            else "none (the guest's null device has no hook call)")
        print(f"   native-render: device hook: {self.nr['device_hook']}")
        self.nr_milestones()

    def nr_branch(self, at, to):
        d = (to - at) >> 2
        if not -(1 << 25) <= d < (1 << 25):
            raise SystemExit(f"--native-render: {to:#x} out of branch range of {at:#x}")
        self.uc.mem_write(at, struct.pack("<I", 0x14000000 | (d & 0x3FFFFFF)))

    def nr_milestones(self):
        path = self.nr_opts.get("milestones") or emu_guest.MILESTONES
        if path == "none" or not hasattr(self, "on_mile_call"):
            return
        for name, kind, target in emu_guest.parse_milestones(path):
            if kind == "call" and target.startswith("re:"):
                rx = re.compile(target[3:])
                for n, a in self.nr_funcs.items():
                    if rx.search(n):
                        self.uc.hook_add(UC_HOOK_CODE, self.on_mile_call, user_data=name, begin=a, end=a)

    # -- --gpu: nxgpu -> GL ------------------------------------------------------------------------------------------
    def nr_attach_gpu(self):
        import emu_n0
        n0 = emu_n0.N0Emu
        for m in ("attach_gl", "call", "host_copy"):
            setattr(self, m, n0.__dict__[m].__get__(self))
        self.nr_gpu_call = n0.__dict__["h_ext_call"].__get__(self)
        self.attach_gl(os.path.abspath(self.nr_opts["gpu"]))
        self.gpu = {"calls": 0, "images": 0, "shaders": 0, "submits": 0, "presents": 0, "failed": 0, "frames": []}
        self.gpu_back = None
        print(f"   gpu: nxgpu (ext_call {NXGPU_MODULE}) -> {os.path.basename(self.nr_opts['gpu'])}")

    def h_ext_call(self, module, fn, args, nargs, *rest):
        if module != NXGPU_MODULE or not getattr(self, "gpu", None):
            return super().h_ext_call(module, fn, args, nargs, *rest)
        g = self.gpu
        g["calls"] += 1
        desc = None
        if fn == FN_IMAGE_CREATE and nargs:
            a0 = struct.unpack("<Q", bytes(self.uc.mem_read(args, 8)))[0]
            desc = struct.unpack_from("<HHBBB", bytes(self.uc.mem_read(a0, 8)))
        present = False
        if fn == FN_SUBMIT and nargs:
            a0 = struct.unpack("<Q", bytes(self.uc.mem_read(args, 8)))[0]
            d = bytes(self.uc.mem_read(a0, 32))
            present = bool(struct.unpack_from("<I", d, 24)[0]) if len(d) >= 28 else False
        r = self.nr_gpu_call(module, fn, args, nargs, *rest)
        if fn == FN_IMAGE_CREATE:
            g["images"] += 1 if r else 0
            g["failed"] += 0 if r else 1
            if r and desc and desc[4] == 1 and self.gpu_back is None:   # the first colour target: the back buffer
                self.gpu_back = (r, desc[0], desc[1])
        elif fn == FN_SHADER_CREATE:
            g["shaders"] += 1 if r else 0
            g["failed"] += 0 if r else 1
        elif fn == FN_SUBMIT:
            g["submits"] += 1
            g["failed"] += 1 if r else 0
            if present:           # Renderer::Do_EndFrame submits with present = 1: one frame on the screen
                g["presents"] += 1
                n = g["presents"]
                if n & (n - 1) == 0 and len(g["frames"]) < 8 and self.nr_opts.get("frames"):
                    self.nr_save_frame(n)
        return r

    def nr_save_frame(self, n):
        if not self.gpu_back:
            return
        img, w, h = self.gpu_back
        size = w * h * 4
        hb = ctypes.create_string_buffer(size)
        desc = ctypes.create_string_buffer(struct.pack("<6IQII", img, 0, 0, w, h, 0, ctypes.addressof(hb), size, 0), 40)
        if self.call(FN_READBACK, ctypes.addressof(desc)) != 0:
            return
        os.makedirs(self.nr_opts["frames"], exist_ok=True)
        path = os.path.join(self.nr_opts["frames"], f"frame_{n:04d}.png")
        raw = hb.raw
        nonzero = sum(1 for i in range(0, size, 4 * 97) if raw[i:i + 3] != b"\0\0\0")
        png(path, w, h, raw)
        self.gpu["frames"].append((n, path, nonzero))

    def nr_lines(self):
        out = []
        nr = getattr(self, "nr", None)
        if nr is not None:
            out.append(f"native-render\tredirected {nr['redirected']}\tnot redirected {len(nr['missing'])}\t"
                       f"replaced {nr['replaced']}\tdevice hook {nr['device_hook']}")
        g = getattr(self, "gpu", None)
        if g is not None:
            out.append(f"gpu\tcalls {g['calls']}\timages {g['images']}\tshaders {g['shaders']}\tsubmits {g['submits']}"
                       f"\tframes {g['presents']}\tfailed {g['failed']}")
            for n, path, nz in g["frames"]:
                out.append(f"gpu-frame\t{n}\t{path}\tnon-black samples {nz}")
        return out

    # -- hooks into emu_guest.main: install before the first instruction, report with the milestones -------------
    def run(self):
        if self.nr_opts.get("gpu"):
            self.nr_attach_gpu()
        if self.nr_opts.get("native_render"):
            self.nr_install()
        if self.nr_opts.get("trace"):
            self.nr_install_trace(self.nr_opts["trace"])
        return super().run()

    # -- --trace RE: entry (x0..x3) and return (x0) of the guest functions whose symbol matches ---------------------
    def nr_install_trace(self, patterns):
        rxs = [re.compile(p) for p in patterns]
        hits = sorted({(self.code_addr(n), n) for n in self.code_syms if any(r.search(n) for r in rxs)})
        self.nr_trace_pending, self.nr_trace_lr, self.nr_trace_log = {}, set(), []
        for a, n in hits[:400]:
            if a is not None:
                self.uc.hook_add(UC_HOOK_CODE, self.nr_on_trace, user_data=n, begin=a, end=a)
        print(f"   trace: {len(hits)} functions" + (" (first 400 hooked)" if len(hits) > 400 else ""))

    def nr_on_trace(self, uc, addr, size, name):
        from unicorn.arm64_const import UC_ARM64_REG_LR, UC_ARM64_REG_SP, UC_ARM64_REG_X0, UC_ARM64_REG_X1, \
            UC_ARM64_REG_X2, UC_ARM64_REG_X3
        lr, sp = uc.reg_read(UC_ARM64_REG_LR), uc.reg_read(UC_ARM64_REG_SP)
        args = [uc.reg_read(r) & 0xFFFFFFFF for r in (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
                                                       UC_ARM64_REG_X3)]
        depth = len(self.nr_trace_pending)
        line = f"trace\t{'  ' * depth}> {name}({', '.join(f'{v:#x}' for v in args)}) from {self.sym(lr)}"
        if len(self.nr_trace_log) < 3000:
            self.nr_trace_log.append(line)
            print("   " + line)
        self.nr_trace_pending[(lr, sp)] = (name, depth)
        if lr not in self.nr_trace_lr:
            self.nr_trace_lr.add(lr)
            uc.hook_add(UC_HOOK_CODE, self.nr_on_trace_ret, begin=lr, end=lr)

    def nr_on_trace_ret(self, uc, addr, size, _):
        from unicorn.arm64_const import UC_ARM64_REG_SP, UC_ARM64_REG_X0
        k = (addr, uc.reg_read(UC_ARM64_REG_SP))
        if k in self.nr_trace_pending:
            name, depth = self.nr_trace_pending.pop(k)
            if len(self.nr_trace_log) < 3000:
                line = f"trace\t{'  ' * depth}< {name} = {uc.reg_read(UC_ARM64_REG_X0) & 0xFFFFFFFF:#x}"
                self.nr_trace_log.append(line)
                print("   " + line)

    def milestone_lines(self):
        return super().milestone_lines() + self.nr_lines()


def main(argv):
    own, rest, i = {}, [], 0
    flags = {"--native-render": "native_render", "--gpu": "gpu", "--frames": "frames", "--overrides": "overrides"}
    own["trace"] = []
    while i < len(argv):
        if argv[i] == "--trace" and i + 1 < len(argv):
            own["trace"].append(argv[i + 1])
            i += 2
            continue
        if argv[i] in flags and i + 1 < len(argv):
            own[flags[argv[i]]] = argv[i + 1]
            i += 2
            continue
        if argv[i] in ("--elf", "--addrmap", "--milestones") and i + 1 < len(argv):
            own[argv[i][2:]] = argv[i + 1]
        rest.append(argv[i])
        i += 1
    NativeRenderEmu.nr_opts = own
    emu_guest.Emu = NativeRenderEmu
    return emu_guest.main(rest)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
