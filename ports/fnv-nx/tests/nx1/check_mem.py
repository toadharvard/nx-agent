#!/usr/bin/env python3
"""NX1 check 2: memory. Everything the game process holds must fit what the console gives it, with margin.

    check_mem.py --guest-elf fnvnx.elf --boot-json boot_summary.json [--backend plain|nine|native|all]

The budget. Title takeover gives the application pool (about 3.2 GiB on retail), but the process runs in a
32BitNoReserved address space (D1/D8), whose heap region is 2 GiB, and on the Switch the GPU's memory (deko3d
blocks, Mesa's nouveau buffers) is process heap too. So the hard limit is the heap region:
    host heap (fnvnx_host_heap_size of the backend: GPU pools, Mesa, libnx)  + guest image (link span incl. bss)
  + S10's window backing (0x00fdf000..0x02100000, taken off the guest heap)  + guest heap peak  <=  2 GiB
  npdm      address space 32bit_no_reserved (2 GiB heap region, the figure above);
  refuse    the launcher and the host refuse applet mode with a message, and refuse less application memory than
            FNVNX_MIN_APP_MEMORY, which must cover the heap region the host takes;
  fixed     host heap + image + window backing <= 50 % of the heap region (the rest is the game's);
  peak      the emulated boot's guest heap high-water mark (tests/s5/emu_guest.py peak_heap_bytes) <= 75 % of what
            is left for the guest heap (R0's perf.memory margin: fragmentation, the cells after the first one).
            A peak only counts when the boot reached the first cell: a boot that stopped earlier has not loaded the
            world yet, so the check fails with the milestone it reached;
  emulator  (info) the emulator's guest heap size, which caps what a boot can measure.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "toolchain", "tools"))
import nxelf  # noqa: E402
from check_fs import strip_comments  # noqa: E402
from check_nro import Checks  # noqa: E402

MIB = 1 << 20
HEAP_REGION = {"32bit_no_reserved": 2048 * MIB, "32bit": 1024 * MIB}
WINDOW = 0x02100000 - 0x00fdf000
MILESTONES = ["winmain", "renderer-init", "main-menu", "first-frame", "first-cell"]


def read(rel):
    return strip_comments(open(os.path.join(ROOT, rel), errors="replace").read())


def host_heaps() -> dict[str, int]:
    """fnvnx_host_heap_size per host backend, from the sources that set it."""
    out = {}
    m = re.search(r'fnvnx_host_heap_size\s*=\s*(\d+)ull\s*<<\s*20', read("toolchain/host/host.c"))
    out["plain"] = int(m.group(1)) * MIB if m else None
    m = re.search(r'#define\s+NXGPU_HOST_HEAP_MIB\s+(\d+)', read("render/native/host/nxgpu_host.c"))
    out["native"] = int(m.group(1)) * MIB if m else None
    sh = open(os.path.join(ROOT, "render", "nine", "switch_host.sh"), errors="replace").read()
    m = re.search(r'HOST_HEAP_MIB:-(\d+)', sh) or re.search(r'HOST_HEAP_MIB=(\d+)', sh)
    out["nine"] = int(m.group(1)) * MIB if m else 512 * MIB   # render/nine/README.md: default 512 MiB
    return out


def image_span(path: str) -> int:
    e = nxelf.read(path)
    loads = [p for p in e.phdrs if p.type == nxelf.PT_LOAD]
    lo, hi = min(p.vaddr for p in loads), max(p.vaddr + p.memsz for p in loads)
    return (hi - lo + 0xFFF) & ~0xFFF


def check(guest_elf, boot_json, backend) -> Checks:
    c = Checks()
    npdm = json.load(open(os.path.join(ROOT, "toolchain", "npdm", "fnvnx-app.json")))
    space = npdm["address_space_type"]
    region = HEAP_REGION.get(space)
    c.ok("npdm.space", region == 2048 * MIB, f"address space {space}: heap region {region}")
    region = region or 2048 * MIB
    mins = re.search(r'#define\s+FNVNX_MIN_APP_MEMORY\s+\((\d+)ull\s*<<\s*20\)',
                     read("toolchain/include/fnvnx/sdlayout.h"))
    min_mem = int(mins.group(1)) * MIB if mins else 0
    c.ok("refuse.min_memory", min_mem >= region, f"FNVNX_MIN_APP_MEMORY {min_mem >> 20} MiB < heap region {region >> 20} MiB")
    for rel in ("toolchain/nro/launcher.c", "toolchain/host/host_device.inc"):
        s = read(rel)
        c.ok(f"refuse.applet.{os.path.basename(rel)}", re.search(r'AppletType_Application', s) is not None
             and "title takeover" in s, f"{rel}: no applet-mode refusal with a title-takeover message")
        c.ok(f"refuse.memory.{os.path.basename(rel)}", "FNVNX_MIN_APP_MEMORY" in s, f"{rel}: no memory refusal")

    heaps = host_heaps()
    c.ok("host.heaps", all(heaps.values()), f"could not read every backend's host heap: {heaps}")
    names = [backend] if backend != "all" else [k for k in heaps if heaps[k]]
    host = max(heaps[k] or 0 for k in names)
    img = image_span(guest_elf) if guest_elf else None
    if not c.ok("guest.image", img is not None, "no guest ELF"):
        return c
    fixed = host + img + WINDOW
    c.ok("fixed.share", fixed <= region // 2, f"host {host >> 20} + image {img >> 20} + window {WINDOW >> 20} MiB = "
         f"{fixed >> 20} MiB > half the heap region")
    avail = region - fixed
    print(f"info budget: heap region {region >> 20} MiB; host ({'/'.join(names)}) {host >> 20} MiB, guest image "
          f"{img >> 20} MiB, window {WINDOW >> 20} MiB -> guest heap {avail >> 20} MiB")
    emu = re.search(r'^HEAP_BASE, HEAP_SIZE = (\w+), (\w+)', open(os.path.join(ROOT, "tests", "s5", "emu_guest.py")).read(), re.M)
    if emu:
        print(f"info emulator guest heap {int(emu.group(2), 0) >> 20} MiB (a boot cannot measure more than that)")
    if not c.ok("boot.summary", boot_json and os.path.isfile(boot_json), "no boot summary (tests/s5/emu_guest.py --summary-json)"):
        return c
    b = json.load(open(boot_json))
    peak, ms = b.get("peak_heap_bytes"), b.get("milestone")
    c.ok("boot.peak_measured", isinstance(peak, int), "the boot summary has no peak_heap_bytes")
    if isinstance(peak, int):
        c.ok("boot.peak_margin", peak <= avail * 3 // 4, f"peak {peak >> 20} MiB > 75 % of the guest heap {avail >> 20} MiB")
        print(f"info peak guest heap {peak / MIB:.1f} MiB at milestone {ms}: {100 * peak / avail:.1f} % of the guest heap")
    c.ok("boot.representative", ms == MILESTONES[-1],
         f"the boot stopped at milestone {ms!r}: its peak ({(peak or 0) / MIB:.1f} MiB) is not the game's yet "
         "(needs the first cell)")
    return c


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--guest-elf")
    ap.add_argument("--boot-json")
    ap.add_argument("--backend", default="all", choices=["all", "plain", "nine", "native"])
    a = ap.parse_args(argv)
    c = check(a.guest_elf, a.boot_json, a.backend)
    c.print()
    print(f"memory: {sum(r[1] for r in c.rows)}/{len(c.rows)} pass -> {'PASS' if c.passed else 'FAIL'}")
    return 0 if c.passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
