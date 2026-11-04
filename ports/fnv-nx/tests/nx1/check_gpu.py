#!/usr/bin/env python3
"""NX1 check 6: the GPU's device integration (the render machine owns the renderer; this checks how it meets the
console).

    check_gpu.py [--host-elf host.elf]

  backend  (with --host-elf) the packaged host has a GPU backend: Gallium Nine (nx_nine_shader_cache_init, nwindow
           present) or the native deko3d renderer (dkDeviceCreate). The plain host draws nothing (D3D9=null);
  display  each backend presents through libnx's default window (nwindowGetDefault: the application layer vi:u
           gives every application) with 1280x720 buffers, which VI scales to the TV when docked, or picks
           1920x1080 docked from appletGetOperationMode; Nine advertises both modes to the game;
  present  a present call (nwindowQueueBuffer / dkQueuePresentImage) and a dequeue that handles failure;
  paths    the shader cache and the logs inside FNVNX_SD_DIR (fnvnx/sdlayout.h);
  console  nothing desktop-only in the render hosts or the Nine guest side: no GLX/WGL/X11/SDL, no /dev/dri, no
           DISPLAY lookups.
"""
from __future__ import annotations

import argparse
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

BACKENDS = {"nine": "render/nine/host", "native": "render/native/host"}
DESKTOP = [r'\bglX\w*', r'\bwgl[A-Z]\w*', r'#include\s*<X11/', r'EGL_PLATFORM_X11', r'getenv\(\s*"DISPLAY"', r'"/dev/dri',
           r'#include\s*[<"]SDL', r'\bxcb_\w+']


def tree(rel):
    out = {}
    for dp, _, fns in os.walk(os.path.join(ROOT, rel)):
        for f in fns:
            if f.endswith((".c", ".h", ".cpp", ".inc")):
                p = os.path.join(dp, f)
                out[os.path.relpath(p, ROOT)] = strip_comments(open(p, errors="replace").read())
    return out


def check(host_elf) -> Checks:
    c = Checks()
    sd = parse_layout()["FNVNX_SD_DIR"]
    for name, rel in BACKENDS.items():
        files = tree(rel)
        allsrc = "\n".join(files.values())
        c.ok(f"{name}.window", "nwindowGetDefault" in allsrc, f"{rel}: no nwindowGetDefault (the application's layer)")
        c.ok(f"{name}.present", any(k in allsrc for k in ("nwindowQueueBuffer", "dkQueuePresentImage")), f"{rel}: no present")
        c.ok(f"{name}.size_720p", re.search(r'\b1280\b', allsrc) and re.search(r'\b720\b', allsrc),
             f"{rel}: no 1280x720 output size")
        docked = "appletGetOperationMode" in allsrc or ("1920" in allsrc and "nwindowGetDimensions" in allsrc)
        print(f"info {name}: docked output {'chosen from the operation mode / window' if docked else 'VI scales the 720p buffers'}")
        stray = [f"{p}: {m}" for p, s in files.items() for m in re.findall(r'"(sdmc:/[^"]*)"', s) if not m.startswith(sd + "/")]
        c.ok(f"{name}.paths", not stray, "; ".join(stray[:4]))
        bad = [f"{p}: {m.group(0)}" for p, s in files.items() for rx in DESKTOP for m in re.finditer(rx, s)]
        c.ok(f"{name}.console_only", not bad, "; ".join(bad[:4]))
    nine = "\n".join(tree("render/nine/host").values())
    c.ok("nine.modes", re.search(r'\{\s*1280\s*,\s*720\s*,', nine) and re.search(r'\{\s*1920\s*,\s*1080\s*,', nine),
         "Nine does not offer both 1280x720 and 1920x1080 to the game")
    c.ok("nine.dequeue_fails_soft", re.search(r'R_FAILED\(nwindowDequeueBuffer', nine) is not None,
         "a failed nwindowDequeueBuffer is not handled")
    m = re.search(r'#define\s+NX_NINE_SHADER_CACHE_PATH\s+"([^"]+)"', nine)
    c.ok("nine.shader_cache", m is not None and m.group(1).startswith(sd + "/"),
         f"shader cache {m.group(1) if m else None} outside {sd}")
    guest = "\n".join(s for p, s in tree("render/nine").items() if "/host/" not in p)
    bad = [m.group(0) for rx in DESKTOP for m in re.finditer(rx, guest)]
    c.ok("nine.guest_console_only", not bad, f"desktop-only code in render/nine guest side: {bad[:4]}")
    if host_elf:
        nm = shutil.which("aarch64-linux-gnu-nm") or shutil.which("nm")
        syms = subprocess.run([nm, host_elf], capture_output=True, text=True).stdout if nm else ""
        be = "native" if "dkDeviceCreate" in syms else "nine" if "nx_nine_shader_cache_init" in syms else None
        c.ok("package.backend", be is not None, "the packaged host has no GPU backend (HOST=plain, D3D9=null): the game "
             "draws nothing on the console; the full build needs NINE=1 or the native renderer")
        print(f"info package backend: {be}")
    else:
        c.ok("package.backend", False, "no host ELF: the packaged backend is unknown")
    return c


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--host-elf")
    a = ap.parse_args(argv)
    c = check(a.host_elf)
    c.print()
    print(f"gpu: {sum(r[1] for r in c.rows)}/{len(c.rows)} pass -> {'PASS' if c.passed else 'FAIL'}")
    return 0 if c.passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
