#!/usr/bin/env python3
"""NX1 check 6: the native renderer's device integration (owner decision: the native deko3d path is the target;
Gallium Nine is not checked). The render machine owns render/native; this checks how it meets the console.

    check_gpu.py [--host-elf host.elf] [--guest-elf fnvnx.elf] [--build <full build dir>]

  init     nxgpu_init (render/native/host/nxgpu.c): dkDeviceCreate with a debug callback; every object it creates
           (memory blocks, queue, swapchain, command buffers, the render thread) is checked before use, so a failed
           init returns NULL (logged "deko3d init FAILED" by nxgpu_host.c) instead of faulting later;
  window   the swapchain is on nwindowGetDefault() (the application's layer) with 1280x720 images, which VI scales
           to the TV when docked, or the size follows appletGetOperationMode;
  present  dkQueueAcquireImage / dkQueuePresentImage, an acquired image always goes back;
  pools    the memory blocks nxgpu_init creates (framebuffers, command memory, code, staging, image, readback,
           descriptors) fit the host heap nxgpu_host.c asks for (NXGPU_HOST_HEAP_MIB) with 64 MiB left for libnx,
           newlib and the guest file while it loads; the render thread on an application core;
  shaders  shader code comes from the guest through shader_create into the code pool (no on-SD cache file to
           check); the code pool keeps DK_SHADER_CODE_UNUSABLE_SIZE free;
  paths    every sdmc: path of the native host inside FNVNX_SD_DIR;
  console  nothing desktop-only (GLX/WGL/X11/SDL/EGL-X11, /dev/dri, DISPLAY);
  package  (with --host-elf) the packaged host is the native one (dkDeviceCreate) and built without libnx's text
           console (FNVNX_HOST_NO_CONSOLE: the console and deko3d would both take the default window);
  path     the guest-to-host render path: the guest's front (render/native/front/front.cpp nx::GpuBackend) refuses a
           host whose NXGPU_MAGIC/NXGPU_VERSION differ; the ext module id is the host's only user of it; (with
           --guest-elf) the packaged guest links the native front (LINK_OVERRIDES=render, libnxrender.a); (with
           --build) the guest's libnxrender.a and the host were built against the same gpu_abi.h (the link stage's
           render_abi, from the archive's .abi sidecar, equals the host stage's gpu_abi).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
from check_fs import parse_layout, strip_comments  # noqa: E402
from check_nro import Checks  # noqa: E402

NATIVE = "render/native/host"
DESKTOP = [r'\bglX\w*', r'\bwgl[A-Z]\w*', r'#include\s*<X11/', r'EGL_PLATFORM_X11', r'getenv\(\s*"DISPLAY"', r'"/dev/dri',
           r'#include\s*[<"]SDL', r'\bxcb_\w+']
MIB = 1 << 20


def body(text, fn):
    m = re.search(r'\b' + re.escape(fn) + r'\s*\([^;{]*\)\s*\{', text)
    if not m:
        return ""
    i, depth = m.end(), 1
    while i < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[m.end():i]


def cexpr(expr: str, defs: dict[str, int]) -> int | None:
    e = re.sub(r'(\d+)u\b', r'\1', expr)
    e = re.sub(r'\b[A-Z_][A-Z0-9_]*\b', lambda m: str(defs.get(m.group(0), "None")), e)
    if "None" in e or re.search(r'[^0-9xa-fA-F+\-*/()<>\s&~|]', e):
        return None
    try:
        return int(eval(e, {"__builtins__": {}}))   # noqa: S307 - digits and operators only (checked above)
    except Exception:
        return None


def stage_rec(build, name):
    try:
        return json.load(open(os.path.join(build, name, "stage.json")))
    except (OSError, ValueError):
        return {}


def check(host_elf, guest_elf=None, build=None) -> Checks:
    c = Checks()
    sd = parse_layout()["FNVNX_SD_DIR"]
    files = {}
    for f in sorted(os.listdir(os.path.join(ROOT, NATIVE))):
        if f.endswith((".c", ".h")):
            files[f"{NATIVE}/{f}"] = strip_comments(open(os.path.join(ROOT, NATIVE, f), errors="replace").read())
    gpu, host = files.get(f"{NATIVE}/nxgpu.c", ""), files.get(f"{NATIVE}/nxgpu_host.c", "")
    allsrc = "\n".join(files.values())
    init = body(gpu, "nxgpu_init")
    c.ok("init.present", bool(init), "no nxgpu_init in render/native/host/nxgpu.c")
    c.ok("init.device", "dkDeviceCreate" in init and "cbDebug" in init and re.search(r'if\s*\(\s*!g\.dev\s*\)', init),
         "dkDeviceCreate without a debug callback or a NULL check")
    # every created object is checked: g.X = <create>(...) needs a later `!g.X` (or an R_FAILED for threads)
    created = re.findall(r'\b(g\.\w+(?:\[i\])?)\s*=\s*(dk\w+Create|host_block)\s*\(', init)
    created += [(m, "host_block") for m in re.findall(r'\bDkMemBlock\s+(\w+)\s*=\s*host_block\(', init)]
    unchecked = sorted({v for v, _ in created if not re.search(r'!\s*' + re.escape(v.replace("[i]", "")) + r'\b', init)})
    c.ok("init.checked", not unchecked, f"nxgpu_init uses unchecked results: {', '.join(unchecked)} (a failed create "
         "faults later instead of 'deko3d init FAILED')")
    c.ok("init.thread", re.search(r'R_FAILED\(threadCreate\(&g\.thread', init) is not None, "render thread creation unchecked")
    c.ok("init.failure_logged", "init FAILED" in host, "nxgpu_host.c does not log a failed init")
    c.ok("window.default", re.search(r'dkSwapchainMakerDefaults\([^;]*nwindowGetDefault\(\)', init) is not None,
         "the swapchain is not on nwindowGetDefault()")
    w = re.search(r'#define\s+NXGPU_WIDTH\s+(\d+)', host)
    h = re.search(r'#define\s+NXGPU_HEIGHT\s+(\d+)', host)
    size = (int(w.group(1)), int(h.group(1))) if w and h else None
    docked = "appletGetOperationMode" in allsrc
    c.ok("window.size", size == (1280, 720) or docked, f"output {size}: neither 1280x720 (VI scales it docked) nor "
         "chosen from the operation mode")
    print(f"info native output {size}; docked: {'operation mode' if docked else 'VI scales the 720p images'}")
    c.ok("present.calls", "dkQueueAcquireImage" in gpu and "dkQueuePresentImage" in gpu, "no acquire/present pair")

    defs = {k: int(v.replace("u", ""), 0) for k, v in re.findall(r'#define\s+(\w+)\s+\(?(\d+u?)\)?\s', gpu)}
    for k, v in re.findall(r'#define\s+(\w+)\s+\((\d+u?\s*<<\s*\d+)\)', gpu):
        defs[k] = cexpr(v, defs) or 0
    fb = cexpr("1280 * 720 * 4 * 3", {}) if size == (1280, 720) else (size[0] * size[1] * 4 * 3 if size else 0)
    pool, parts = fb, [f"framebuffers {fb / MIB:.1f}"]
    for m in re.finditer(r'host_block\(([^,]+),', init):
        expr = m.group(1).strip()
        if "fbSize" in expr:
            continue
        v = cexpr(expr, defs)
        if v is None and expr == "descSize":
            v = 64 << 10
        pool += v or 0
        parts.append(f"{expr} {(v or 0) / MIB:.1f}")
    heap = re.search(r'#define\s+NXGPU_HOST_HEAP_MIB\s+(\d+)', host)
    heap = int(heap.group(1)) * MIB if heap else 0
    c.ok("pools.fit", heap and pool + 64 * MIB <= heap, f"GPU pools {pool / MIB:.0f} MiB + 64 MiB > host heap {heap >> 20} MiB")
    print(f"info native pools {pool / MIB:.0f} MiB of host heap {heap >> 20} MiB: " + ", ".join(parts))
    c.ok("pools.thread_core", re.search(r'threadCreate\(&g\.thread,[^;]*,\s*0x2[0-9A-Fa-f]\s*,\s*[012]\s*\)', init) is not None,
         "render thread not on an application core with a game priority")
    c.ok("shaders.code_pool", "DK_SHADER_CODE_UNUSABLE_SIZE" in gpu, "the code pool ignores DK_SHADER_CODE_UNUSABLE_SIZE")
    stray = [f"{p}: {m}" for p, s in files.items() for m in re.findall(r'"(sdmc:/[^"]*)"', s) if not m.startswith(sd + "/")]
    c.ok("paths.inside", not stray, "; ".join(stray[:4]))
    bad = [f"{p}: {m.group(0)}" for p, s in files.items() for rx in DESKTOP for m in re.finditer(rx, s)]
    c.ok("console.only", not bad, "; ".join(bad[:4]))
    # the guest-to-host path
    front = strip_comments(open(os.path.join(ROOT, "render", "native", "front", "front.cpp"), errors="replace").read())
    gb = body(front, "GpuBackend")
    c.ok("path.handshake", "NXGPU_MAGIC" in gb and "NXGPU_VERSION" in gb,
         "the guest front does not refuse a host with another nxgpu ABI (magic/version)")
    abi = open(os.path.join(ROOT, "render", "native", "include", "nx", "gpu_abi.h"), errors="replace").read()
    mod = re.search(r'#define\s+NXGPU_EXT_MODULE\s+(\d+)u', abi)
    hostapi = open(os.path.join(ROOT, "toolchain", "include", "fnvnx", "hostapi.h"), errors="replace").read()
    others = {int(v) for v in re.findall(r'#define\s+FNVNX_EXT_\w+\s+(\d+)u', hostapi)} | {9}   # 9: Nine's module
    c.ok("path.module_id", mod is not None and int(mod.group(1)) not in others,
         f"NXGPU_EXT_MODULE {mod.group(1) if mod else None} collides with another ext module {sorted(others)}")
    nm = shutil.which("aarch64-linux-gnu-nm") or shutil.which("nm")
    if guest_elf:
        gsyms = subprocess.run([nm, guest_elf], capture_output=True, text=True, errors="replace").stdout if nm else ""
        c.ok("path.guest_front", "2nx10GpuBackend" in gsyms, "the packaged guest links no native front (nx::GpuBackend): "
             "the game never calls the renderer; the full build needs LINK_OVERRIDES=render (libnxrender.a)")
    else:
        c.ok("path.guest_front", False, "no guest ELF: whether the guest links the native renderer is unknown")
    if build:
        link, host_r = stage_rec(build, "link"), stage_rec(build, "host")
        ra, ha = link.get("render_abi"), host_r.get("gpu_abi")
        c.ok("path.abi", ra and ha and ra == ha, f"guest libnxrender.a ABI {ra or 'none (no LINK_OVERRIDES=render)'} "
             f"vs host gpu_abi.h {ha or 'none (not the native host)'}")
    else:
        c.ok("path.abi", False, "no build directory: the guest's and the host's nxgpu ABI cannot be compared")
    if host_elf:
        syms = subprocess.run([nm, host_elf], capture_output=True, text=True, errors="replace").stdout if nm else ""
        native = "dkDeviceCreate" in syms
        c.ok("package.native", native, "the packaged host is not the native renderer's (HOST=plain draws nothing; the "
             "full build has to link render/native/host with host.c)")
        if native:
            c.ok("package.no_console", re.search(r'\bconsoleInit\b', syms) is None,
                 "the native host links libnx's console: build host.c with -DFNVNX_HOST_NO_CONSOLE")
    else:
        c.ok("package.native", False, "no host ELF: the packaged backend is unknown")
    return c


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--host-elf")
    ap.add_argument("--guest-elf")
    ap.add_argument("--build")
    a = ap.parse_args(argv)
    c = check(a.host_elf, a.guest_elf, a.build)
    c.print()
    print(f"gpu: {sum(r[1] for r in c.rows)}/{len(c.rows)} pass -> {'PASS' if c.passed else 'FAIL'}")
    return 0 if c.passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
