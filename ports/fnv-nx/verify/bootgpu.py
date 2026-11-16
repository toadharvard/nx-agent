#!/usr/bin/env python3
"""The renderer of the boot stage: ext module 10 (the guest's GPU calls) forwarded to the renderer's host library libnxgl4.so
(render/d3d9/backend/gl4.h: desktop GL 4.5 on Mesa llvmpipe, EGL surfaceless). Library: Gpu.

The library is the recipe product of the host-target root render/gl4 (GL_ROOT), taken only from gate_build.ensure in the verdict's store
($GATE_CACHE): there is no prebuilt library, no pin of one and no path argument.

The library exports nxgl4_create(width, height, err, errlen), nxgl4_destroy, nxgl4_ext(fn, args, nargs) and nxgl4_last_error, and
leaves two symbols for the embedder: nxgl4_guest_ptr(addr, size) (guest range -> host pointer; NULL when the range is not wholly inside
one mapping readable and writable for the guest: Machine.host_ptr) and nxgl4_on_present(frame). The library does not ship them, so
this file loads the recipe product of the host-target root tests/bootrt (libnxgl4rt.so, built by recipe.py in the verdict's store, $GATE_CACHE;
no compiler is called here) RTLD_GLOBAL, then the library. bootrt defines the two embedder symbols (forwarding to the function pointers set through
nxgl4_shim_set(ptr, present, exit)) and fnvnx_reach_violation (hostabi/include/fnvnx/reach.h) for every host object. The environment of the library (LIBGL_ALWAYS_SOFTWARE,
GALLIUM_DRIVER, EGL_PLATFORM, NXGL4_FRAMES) is the caller's: boot.py sets it before Gpu() loads the library.
"""
import ctypes
import hashlib
import os
import re
import sys

SIZES = {"handheld": (1280, 720), "docked": (1920, 1080)}   # the swapchain image of the profile (docked 1080p, handheld 720p)
MODULE = 10
PTR_CB = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint64)
PRESENT_CB = ctypes.CFUNCTYPE(None, ctypes.c_uint32)
EXIT_CB = ctypes.CFUNCTYPE(None, ctypes.c_uint32)
BOOTRT_ROOT = "ports/fnv-nx/tests/bootrt"   # the host-target recipe root that defines the embedder symbols (admit.json)
GL_ROOT = "ports/fnv-nx/render/gl4"   # the host-target recipe root of the renderer library libnxgl4.so (admit.json)
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_shim = {}


class GpuError(Exception):
    pass


def product_path(root, gate_cache=None):
    """the path of the linked output of the host-target recipe root ROOT, built by recipe.py in the verdict's own store ($GATE_CACHE, the same cache
    the other recipe products come from); a missing cache variable or a red build is an input error"""
    cache = gate_cache or os.environ.get("GATE_CACHE")
    if not cache:
        raise GpuError("GATE_CACHE is not set: %s is a recipe product, built in the verdict's store" % root)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import gate_build
    res, _out = gate_build.ensure(root, cache, REPO)
    if res.get("status") != "green" or not res.get("elf"):
        raise GpuError("%s is not green: %s" % (root, (res.get("failure") or {}).get("stage", res.get("status"))))
    return res["elf"]["path"]


def bootrt_path(gate_cache=None):
    """the path of the embedder library libnxgl4rt.so (the product of BOOTRT_ROOT)"""
    return product_path(BOOTRT_ROOT, gate_cache)


def gl4_path(gate_cache=None):
    """the path of the renderer library libnxgl4.so (the product of GL_ROOT): the only source of the library"""
    return product_path(GL_ROOT, gate_cache)


def _load_shim(gate_cache=None):
    """load the recipe-built embedder library (bootrt) once per process with RTLD_GLOBAL; typed"""
    if "lib" not in _shim:
        lib = ctypes.CDLL(bootrt_path(gate_cache), mode=ctypes.RTLD_GLOBAL)
        lib.nxgl4_shim_set.argtypes, lib.nxgl4_shim_set.restype = [PTR_CB, PRESENT_CB, EXIT_CB], None
        lib.fnvnx_reach_violation.argtypes, lib.fnvnx_reach_violation.restype = [ctypes.c_uint32], None
        _shim["lib"] = lib
    return _shim["lib"]


def load(gate_cache=None):
    """load the shim and then the recipe-built library (the shim's symbols must exist before it); the library's functions typed"""
    shim = _load_shim(gate_cache)
    path = gl4_path(gate_cache)
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
    return shim, lib, path


class Gpu:
    """one renderer library bound to one machine; at most one live Gpu per process (the library holds one GL context)"""

    def __init__(self, machine, profile="handheld", on_present=None, on_first_call=None, on_exit=None, gate_cache=None):
        if profile not in SIZES:
            raise GpuError("profile must be handheld or docked, got %r" % (profile,))
        self.m, self.presents, self.first_call, self.hook_present, self.hook_first = machine, 0, None, on_present, on_first_call
        shim, self.lib, path = load(gate_cache)
        with open(path, "rb") as f:
            self.sha256 = hashlib.sha256(f.read()).hexdigest()
        # on_exit(code): a host object ended the process through fnvnx_reach_violation with the full 32-bit code; it
        # ends the process itself. None registers no exit callback: the violation then ends the process with status 1 after its log line.
        self._cbs = (PTR_CB(self._guest_ptr), PRESENT_CB(self._present), EXIT_CB(on_exit) if on_exit else EXIT_CB())   # alive as long as the library may call them
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


if __name__ == "__main__":   # bootgpu.py: the load check of the farm job (shim + the recipe-built library load, every export present)
    print("bootgpu: %s loads" % load()[2])
