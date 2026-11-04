#!/usr/bin/env python3
"""Run the N0 Switch guest (ILP32, the same scene code as the console build) under Unicorn, with S5's emulated host
(tests/s5/emu_guest.py) plus an ext_call module 10 that forwards every nxgpu call to the GL reference backend.

    python3 tests/s6n/emu_n0.py <guest.nso> <libnxgl.so> <out dir> [results file under <out dir>]

The guest writes its readbacks to sdmc:/fnvnx/n0/out/ (= <out dir>/fnvnx/n0/out/); compare them with the goldens:
    python3 tests/s6n/scenes.py --compare <out dir>/fnvnx/n0/out
What this shows: the ILP32 build of the front end, translator and scenes produces the same command streams and
images as the LP64 host build (struct layouts across the boundary, the ext_call path, the guest runtime).
Guest addresses are translated by copying: memory blocks are re-read from guest memory at every submit.
"""
import ctypes, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "s5"))
import emu_guest  # noqa: E402

NXGPU_EXT_MODULE = 10
FN_INFO, FN_MEM_CREATE, FN_MEM_GPU_ADDR, FN_IMAGE_CREATE, FN_SAMPLER_CREATE, FN_SHADER_CREATE, FN_SUBMIT, \
    FN_WAIT_FRAME, FN_READBACK, FN_SHUTDOWN = range(10)


class N0Emu(emu_guest.Emu):
    def attach_gl(self, lib):
        self.gl = ctypes.CDLL(lib)
        self.gl.nxgl_create.restype = ctypes.c_void_p
        self.gl.nxgl_create.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t]
        self.gl.nxgpu_dispatch.restype = ctypes.c_uint64
        self.gl.nxgpu_dispatch.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
        err = ctypes.create_string_buffer(256)
        self.api = self.gl.nxgl_create(0, 0, err, 256)
        if not self.api:
            raise SystemExit("nxgl_create: " + err.value.decode())
        self.blocks = []          # (guest addr, size, host buffer)
        self.keep = []            # host buffers that must outlive the call (image data is copied by GL at once)
        self.ext_calls = 0

    def call(self, fn, *args):
        a = (ctypes.c_uint64 * max(1, len(args)))(*args)
        return self.gl.nxgpu_dispatch(self.api, fn, a, len(args))

    def host_copy(self, addr, size):
        buf = ctypes.create_string_buffer(bytes(self.uc.mem_read(addr, size)) if size else b"", max(size, 1))
        return buf

    def h_ext_call(self, module, fn, args, nargs, *_):
        if module != NXGPU_EXT_MODULE:
            return 0xFFFFFFFFFFFFFF01
        self.ext_calls += 1
        a = struct.unpack("<%dQ" % nargs, bytes(self.uc.mem_read(args, 8 * nargs))) if nargs else ()
        if fn == FN_INFO:
            out = ctypes.create_string_buffer(32)
            r = self.call(fn, ctypes.addressof(out))
            self.uc.mem_write(a[0], out.raw)
            return r
        if fn == FN_MEM_CREATE:
            storage, size, kind = struct.unpack("<QII", bytes(self.uc.mem_read(a[0], 16)))
            hb = self.host_copy(storage, size)
            desc = ctypes.create_string_buffer(struct.pack("<QII", ctypes.addressof(hb), size, kind), 16)
            r = self.call(fn, ctypes.addressof(desc))
            if r:
                self.blocks.append((storage, size, hb))
            return r
        if fn == FN_IMAGE_CREATE:
            d = bytearray(self.uc.mem_read(a[0], 32))
            data, dsize = struct.unpack_from("<QI", d, 16)
            hb = self.host_copy(data, dsize) if data else None
            struct.pack_into("<Q", d, 16, ctypes.addressof(hb) if hb else 0)
            desc = ctypes.create_string_buffer(bytes(d), 32)
            return self.call(fn, ctypes.addressof(desc))
        if fn == FN_SAMPLER_CREATE:
            desc = self.host_copy(a[0], 16)
            return self.call(fn, ctypes.addressof(desc))
        if fn == FN_SHADER_CREATE:
            d = bytearray(self.uc.mem_read(a[0], 32))
            stage, dsize, dksh, glsl, gsize = struct.unpack_from("<IIQQI", d, 0)
            hd = self.host_copy(dksh, dsize) if dksh and dsize else None
            hg = self.host_copy(glsl, gsize)
            struct.pack_into("<QQ", d, 8, ctypes.addressof(hd) if hd else 0, ctypes.addressof(hg))
            desc = ctypes.create_string_buffer(bytes(d), 32)
            return self.call(fn, ctypes.addressof(desc))
        if fn == FN_SUBMIT:
            d = bytearray(self.uc.mem_read(a[0], 32))
            stream, words, frame, stats = struct.unpack_from("<QIIQ", d, 0)
            for (g, size, hb) in self.blocks:          # caller-backed memory: what the guest wrote this frame
                ctypes.memmove(hb, bytes(self.uc.mem_read(g, size)), size)
            hs = self.host_copy(stream, words * 4)
            st = ctypes.create_string_buffer(32)
            struct.pack_into("<QIIQ", d, 0, ctypes.addressof(hs), words, frame, ctypes.addressof(st) if stats else 0)
            desc = ctypes.create_string_buffer(bytes(d), 32)
            r = self.call(fn, ctypes.addressof(desc))
            if stats:
                self.uc.mem_write(stats, st.raw)
            return r
        if fn == FN_READBACK:
            d = bytearray(self.uc.mem_read(a[0], 40))   # NxReadback v3: dst at 24, dst_size at 32
            dst, dsize = struct.unpack_from("<QI", d, 24)
            hb = ctypes.create_string_buffer(max(dsize, 1))
            struct.pack_into("<Q", d, 24, ctypes.addressof(hb))
            desc = ctypes.create_string_buffer(bytes(d), 40)
            r = self.call(fn, ctypes.addressof(desc))
            if r == 0:
                self.uc.mem_write(dst, hb.raw[:dsize])
            return r
        if fn in (FN_MEM_GPU_ADDR, FN_WAIT_FRAME):
            return self.call(fn, a[0])
        if fn == FN_SHUTDOWN:
            return self.call(fn)
        return 0xFFFFFFFFFFFFFF02


def main():
    if len(sys.argv) not in (4, 5):
        raise SystemExit(__doc__)
    nso, lib, out = sys.argv[1:4]
    results = sys.argv[4] if len(sys.argv) == 5 else os.path.join("fnvnx", "n0", "out", "results.txt")
    os.makedirs(out, exist_ok=True)
    emu = N0Emu(nso, os.path.abspath(out))
    emu.budget = 4_000_000_000
    emu.attach_gl(os.path.abspath(lib))
    rc = emu.run()
    res = os.path.join(out, results)
    if os.path.exists(res):
        sys.stdout.write(open(res).read())
    print("guest rc=%s, host calls %d, nxgpu ext calls %d" % (rc, emu.calls, emu.ext_calls))
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
