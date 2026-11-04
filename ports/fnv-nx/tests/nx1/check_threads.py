#!/usr/bin/env python3
"""NX1 check 4: threads, cores, priorities and stacks are valid for the application on Horizon.

    check_threads.py [--host-elf <host.elf>]

  npdm       toolchain/npdm/fnvnx-app.json: cores 0..3 allowed by the kernel flags, default core 0, the main
             thread's priority inside the thread priority range 0x1C..0x3B, main stack a multiple of 4 KiB;
  placement  tests/nx1/threads_harness.c (placement.h, natively): every thread_create cpu argument, Win32 priority
             and thread_set_cpu spec the guest can pass lands on an ideal core inside its mask, the mask inside the
             application cores 0-2 (0-3 only with the core 3 opt-in, off by default), priority 0x1C..0x3B;
  sites      every literal threadCreate(...) in console code (launcher, host, startup, render hosts): stack a
             multiple of 4 KiB, priority 0x1C..0x3B, core -2 or 0..2;
  guest      the host tells the guest 3 CPUs (h_cpu_count), so the win32 layer's `cpu % os_cpu_count()` stays on
             cores 0-2; the guest's thread stacks are rounded to pages by the host (h_thread_create);
  elf        (with --host-elf) the linked host keeps its symbols (crash.log addresses are symbolized with it).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
from check_fs import strip_comments  # noqa: E402
from check_nro import Checks  # noqa: E402

SITES = ["toolchain/nro", "toolchain/host", "startup/host", "render/nine/host", "render/native/host", "loader/host"]
PRIO_LO, PRIO_HI = 0x1C, 0x3B


def num(s: str):
    s = s.strip()
    try:
        return int(s, 0)
    except ValueError:
        return None


def check(host_elf: str | None) -> Checks:
    c = Checks()
    j = json.load(open(os.path.join(ROOT, "toolchain", "npdm", "fnvnx-app.json")))
    kf = next(k["value"] for k in j["kernel_capabilities"] if k["type"] == "kernel_flags")
    lo_p, hi_p = kf["lowest_thread_priority"], kf["highest_thread_priority"]   # npdmtool's names: lowest = most urgent
    c.ok("npdm.prio_range", (lo_p, hi_p) == (PRIO_LO, PRIO_HI), f"thread priorities {lo_p:#x}..{hi_p:#x}, want 0x1C..0x3B")
    c.ok("npdm.cores", kf["lowest_cpu_id"] == 0 and kf["highest_cpu_id"] in (2, 3),
         f"cores {kf['lowest_cpu_id']}..{kf['highest_cpu_id']}")
    c.ok("npdm.default_core", j["default_cpu_id"] == 0, f"default_cpu_id {j['default_cpu_id']}")
    mp = j["main_thread_priority"]
    c.ok("npdm.main_prio", PRIO_LO <= mp <= PRIO_HI, f"main thread priority {mp:#x}")
    ms = int(j["main_thread_stack_size"], 0)
    c.ok("npdm.main_stack", ms % 0x1000 == 0 and ms >= 0x40000, f"main stack {ms:#x}")

    cc = shutil.which("gcc") or shutil.which("cc")
    if c.ok("placement.compiler", cc is not None, "no native C compiler"):
        with tempfile.TemporaryDirectory() as t:
            exe = os.path.join(t, "h")
            r = subprocess.run([cc, "-O1", "-Wall", "-Werror", "-I" + os.path.join(ROOT, "toolchain", "host"),
                                "-I" + os.path.join(ROOT, "toolchain", "include"),
                                os.path.join(HERE, "threads_harness.c"), "-o", exe], capture_output=True, text=True, errors="replace")
            if c.ok("placement.build", r.returncode == 0, r.stderr.strip()[:200]):
                r = subprocess.run([exe], capture_output=True, text=True, errors="replace")
                c.ok("placement.exhaustive", r.returncode == 0,
                     "; ".join(l for l in r.stdout.splitlines() if l.startswith("FAIL"))[:300])

    found = 0
    for d in SITES:
        for dp, _, fns in os.walk(os.path.join(ROOT, d)):
            for f in fns:
                if not f.endswith((".c", ".inc", ".cpp")):
                    continue
                p = os.path.join(dp, f)
                txt = strip_comments(open(p, errors="replace").read())
                for m in re.finditer(r'\bthreadCreate\s*\(', txt):
                    # the argument list up to the matching parenthesis
                    i, depth = m.end(), 1
                    while i < len(txt) and depth:
                        depth += {"(": 1, ")": -1}.get(txt[i], 0)
                        i += 1
                    args, depth, cur = [], 0, ""
                    for ch in txt[m.end():i - 1]:
                        if ch == "," and depth == 0:
                            args.append(cur)
                            cur = ""
                            continue
                        depth += {"(": 1, ")": -1}.get(ch, 0)
                        cur += ch
                    args.append(cur)
                    if len(args) != 7:
                        continue
                    found += 1
                    where = f"{os.path.relpath(p, ROOT)}:{txt[:m.start()].count(chr(10)) + 1}"
                    st, pr, core = num(args[4].split("?")[-1].split(":")[-1]), num(args[5]), num(args[6])
                    if st is not None:
                        c.ok(f"site.stack {where}", st % 0x1000 == 0, f"stack {st:#x} not a multiple of 4 KiB")
                    if pr is not None:
                        c.ok(f"site.prio {where}", PRIO_LO <= pr <= PRIO_HI, f"priority {pr:#x}")
                    if core is not None:
                        c.ok(f"site.core {where}", core in (-2, 0, 1, 2), f"core {core}: not an application core")
    c.ok("site.found", found >= 3, f"only {found} threadCreate sites parsed")
    v2 = strip_comments(open(os.path.join(ROOT, "toolchain", "host", "host_v2.inc")).read())
    m = re.search(r'h_cpu_count\s*\(\s*void\s*\)\s*\{\s*return\s+(\w+)\s*;', v2)
    c.ok("guest.cpu_count", m is not None and num(m.group(1)) == 3, f"h_cpu_count returns {m.group(1) if m else '?'}")
    m = re.search(r'h_thread_create\s*\(.*?\{(.*?)\n\}', v2, re.S)
    c.ok("guest.stack_rounded", m is not None and "0xFFF" in m.group(1), "h_thread_create does not round the stack")
    if host_elf:
        nm = shutil.which("aarch64-linux-gnu-nm") or shutil.which("nm")
        r = subprocess.run([nm, host_elf], capture_output=True, text=True, errors="replace") if nm else None
        syms = r.stdout if r and r.returncode == 0 else ""
        c.ok("elf.symbols", "threadCreate" in syms, "no symbols in the host ELF")
    return c


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--host-elf")
    a = ap.parse_args(argv)
    c = check(a.host_elf)
    c.print()
    print(f"threads: {sum(r[1] for r in c.rows)}/{len(c.rows)} pass -> {'PASS' if c.passed else 'FAIL'}")
    return 0 if c.passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
