#!/usr/bin/env python3
"""The renderer of the boot stage: ext module 10 (the guest's GPU calls) forwarded to the renderer's host library libnxgl4.so
(render/d3d9/backend/gl4.h: desktop GL 4.5 on Mesa llvmpipe, EGL surfaceless). Library: Gpu.

The library exports nxgl4_create(width, height, err, errlen), nxgl4_destroy, nxgl4_ext(fn, args, nargs) and nxgl4_last_error, and
leaves two symbols for the embedder: nxgl4_guest_ptr(addr, size) (guest range -> host pointer; NULL when the range is not wholly inside
one mapping readable and writable for the guest: Machine.host_ptr) and nxgl4_on_present(frame). The library does not ship them, so
this file builds a tiny C shim once per process (the two symbols forwarding to function pointers set through nxgl4_shim_set), loads
it RTLD_GLOBAL, then loads the library. The shim needs the host gcc. The environment of the library (LIBGL_ALWAYS_SOFTWARE,
GALLIUM_DRIVER, EGL_PLATFORM, NXGL4_FRAMES) is the caller's: boot.py sets it before Gpu() loads the library.
"""
import atexit
import ctypes
import hashlib
import os
import shutil
import subprocess
import tempfile

SHIM_C = r"""
#include <stdint.h>
static void *(*g_ptr)(uint64_t, uint64_t);
static void (*g_present)(uint32_t);
void nxgl4_shim_set(void *(*ptr)(uint64_t, uint64_t), void (*present)(uint32_t)) { g_ptr = ptr; g_present = present; }
void *nxgl4_guest_ptr(uint64_t addr, uint64_t size) { return g_ptr ? g_ptr(addr, size) : (void *)0; }
void nxgl4_on_present(uint32_t frame) { if (g_present) g_present(frame); }
"""
SIZES = {"handheld": (1280, 720), "docked": (1920, 1080)}   # the swapchain image of the profile (docked 1080p, handheld 720p)
MODULE = 10
PTR_CB = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint64)
PRESENT_CB = ctypes.CFUNCTYPE(None, ctypes.c_uint32)
_shim = {}


class GpuError(Exception):
    pass


def _load_shim():
    """build (once per process, into a temp dir) and load the callback shim with RTLD_GLOBAL"""
    if "lib" not in _shim:
        d = tempfile.mkdtemp(prefix="nxgl4-shim-")
        atexit.register(shutil.rmtree, d, ignore_errors=True)
        src, so = os.path.join(d, "shim.c"), os.path.join(d, "libnxgl4shim.so")
        with open(src, "w") as f:
            f.write(SHIM_C)
        r = subprocess.run(["gcc", "-O2", "-fPIC", "-shared", src, "-o", so], capture_output=True, text=True)
        if r.returncode:
            raise GpuError("cannot build the callback shim with gcc: %s" % r.stderr.strip())
        lib = ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
        lib.nxgl4_shim_set.argtypes, lib.nxgl4_shim_set.restype = [PTR_CB, PRESENT_CB], None
        _shim["lib"] = lib
    return _shim["lib"]


def load(path):
    """load the shim and then the library at `path` (the shim's symbols must exist before it); the library's functions typed"""
    shim = _load_shim()
    try:
        lib = ctypes.CDLL(os.path.abspath(path))
    except OSError as e:
        raise GpuError("cannot load %s: %s" % (path, e))
    lib.nxgl4_create.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t]
    lib.nxgl4_create.restype = ctypes.c_void_p
    lib.nxgl4_destroy.argtypes, lib.nxgl4_destroy.restype = [], None
    lib.nxgl4_ext.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint64), ctypes.c_uint32]
    lib.nxgl4_ext.restype = ctypes.c_uint64
    lib.nxgl4_last_error.argtypes, lib.nxgl4_last_error.restype = [], ctypes.c_char_p
    return shim, lib


class Gpu:
    """one renderer library bound to one machine; at most one live Gpu per process (the library holds one GL context)"""

    def __init__(self, path, machine, profile="handheld", on_present=None, on_first_call=None):
        if profile not in SIZES:
            raise GpuError("profile must be handheld or docked, got %r" % (profile,))
        with open(path, "rb") as f:
            self.sha256 = hashlib.sha256(f.read()).hexdigest()
        self.m, self.presents, self.first_call, self.hook_present, self.hook_first = machine, 0, None, on_present, on_first_call
        shim, self.lib = load(path)
        self._cbs = (PTR_CB(self._guest_ptr), PRESENT_CB(self._present))   # alive as long as the library may call them
        shim.nxgl4_shim_set(*self._cbs)
        lib = self.lib
        err = ctypes.create_string_buffer(256)
        if not lib.nxgl4_create(*SIZES[profile], err, len(err)):
            raise GpuError("nxgl4_create failed: %s" % err.value.decode(errors="replace"))
        self.live = True
        machine.ext[MODULE] = self.ext_10

    def _guest_ptr(self, addr, size):
        return self.m.host_ptr(addr, size)

    def _present(self, frame):
        self.presents += 1
        if self.hook_present:
            self.hook_present(frame)

    def ext_10(self, fn, words):
        """module 10 service: the nargs argument words (already read from guest memory by Machine.ext_call) go to nxgl4_ext"""
        if self.first_call is None:
            self.first_call = {"instructions_total": self.m.instr, "virtual_ns": self.m.clock.now_ns()}
            if self.hook_first:
                self.hook_first()
        arr = (ctypes.c_uint64 * max(len(words), 1))(*words)
        return self.lib.nxgl4_ext(fn, arr, len(words))

    def last_error(self):
        e = self.lib.nxgl4_last_error()
        return e.decode(errors="replace") if e else None

    def close(self):
        if self.live:
            self.live = False
            self.lib.nxgl4_destroy()

    def report(self):
        return {"library_sha256": self.sha256, "presents": self.presents, "first_module10_call": self.first_call,
                "last_error": self.last_error() or None}


if __name__ == "__main__":   # bootgpu.py LIB: the load check of the farm job (shim + library load, every export present)
    import sys
    load(sys.argv[1])
    print("bootgpu: %s loads" % sys.argv[1])
