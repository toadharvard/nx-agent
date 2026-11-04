#!/usr/bin/env python3
"""NX1 device-readiness checks: everything that can fail on a real Switch, checked without one.

    python3 -I tests/nx1/run.py [--build <full build dir>] [--sd <SD tree>] [--nro <fnv.nro> --nro-elf <elf>]
                                [--guest-elf <fnvnx.elf>] [--host-elf <host.elf>] [--boot-json <boot_summary.json>]
                                [--ref-bin <dir>] [--json out.json] [--only N[,N]]

METRIC (stream NX1-nro): the number of checks that are not `pass`. A check is `open` when its script does not
exist yet or its input artifact is missing, and `fail` when it ran and found a problem. Missing evidence is never a
pass. `--build` fills the artifact paths from a full build directory (tools/farm/full_job.sh layout:
package/sd, package/nro, guest/fnvnx.elf, package/host/host.elf, boot/).

Checks (docs/NRO.md has the details and the thresholds; each is tests/nx1/check_*.py):
  1 nro       NRO header, MOD0, NACP, icon (tests/nx1/check_nro.py) and the switch-tools round trip
  2 memory    peak heap + image + GPU pools within the title-takeover budget; applet mode refused
  3 svc       every svc / IPC service on the homebrew allowlist; no forbidden instructions
  4 threads   core masks within cores 0-2, priorities in the NPDM range, aligned stacks
  5 fs        SD layout, case-insensitive game paths, writable save/INI directories
  6 gpu       display init/present at 720p and 1080p, shader cache path, nothing desktop-only
  7 platform  HID, audio, suspend/resume, exit to the menu without leaks, crash log on SD
  8 checklist the owner's one-page first-hardware-run checklist
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PY = [sys.executable, "-I"]


class Art:
    """Artifact paths; any may be None."""

    def __init__(self, a):
        b = a.build
        pick = lambda v, *rel: v or (next((p for p in (os.path.join(b, r) for r in rel) if os.path.exists(p)), None)  # noqa: E731
                                     if b else None)
        self.sd = pick(a.sd, "package/sd")
        self.nro = pick(a.nro, "package/nro/fnv.nro", "package/sd/switch/fnv/fnv.nro")
        self.nro_elf = pick(a.nro_elf, "package/nro/fnv.elf")
        self.guest_elf = pick(a.guest_elf, "guest/fnvnx.elf")
        self.host_elf = pick(a.host_elf, "package/host/host.elf", "host/host.elf")
        self.boot_json = pick(a.boot_json, "boot/boot_summary.json")
        self.ref_bin = a.ref_bin or os.environ.get("NX1_REF_BIN")
        self.root = ROOT


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    return r.returncode, r.stdout + r.stderr


def check_nro(art: Art):
    if not art.nro:
        return "open", "no fnv.nro (built by toolchain/nro/build.sh in the package stage)"
    cmd = PY + [os.path.join(HERE, "check_nro.py"), art.nro, "--require-assets"]
    if art.nro_elf:
        cmd += ["--elf", art.nro_elf]
    if art.ref_bin:
        cmd += ["--ref-elf2nro", os.path.join(art.ref_bin, "elf2nro"), "--ref-nacptool", os.path.join(art.ref_bin, "nacptool")]
    rc, out = run(cmd)
    last = out.strip().splitlines()[-1] if out.strip() else ""
    fails = [l for l in out.splitlines() if l.startswith("FAIL")]
    if rc == 0 and not (art.nro_elf and art.ref_bin):
        return "fail", f"{last}; incomplete: the round trip needs --nro-elf and --ref-bin (tests/nx1/ref_tools.sh)"
    return ("pass" if rc == 0 else "fail"), (last if rc == 0 else "; ".join(fails[:5]))


def script(name, *args, need=None):
    """A check script of tests/nx1: pass when it exits 0; its last line is the note."""
    def fn(art):
        missing = [k for k in (need or []) if not getattr(art, k)]
        if missing:
            return "open", "input missing: " + ", ".join(missing)
        argv = PY + [os.path.join(HERE, name)]
        for a in args:
            v = a(art) if callable(a) else a
            argv += v if isinstance(v, list) else [v]
        rc, out = run(argv)
        lines = out.strip().splitlines()
        fails = [l for l in lines if l.startswith("FAIL")]
        return ("pass", lines[-1] if lines else "") if rc == 0 else ("fail", "; ".join(fails[:4]) or (lines[-1] if lines else f"rc {rc}"))
    return fn


def not_yet(what):
    return lambda art: ("open", f"no check script yet: {what}")


CHECKS = [
    (1, "nro", check_nro),
    (2, "memory", script("check_mem.py", lambda a: ["--guest-elf", a.guest_elf], lambda a: ["--boot-json", a.boot_json],
                         need=["guest_elf", "boot_json"])),
    (3, "svc", script("check_svc.py", lambda a: ["--guest-elf", a.guest_elf], lambda a: ["--host-elf", a.host_elf],
                      lambda a: ["--nro-elf", a.nro_elf], need=["guest_elf", "host_elf", "nro_elf"])),
    (4, "threads", script("check_threads.py", lambda a: ["--host-elf", a.host_elf] if a.host_elf else [])),
    (5, "fs", script("check_fs.py", lambda a: ["--sd", a.sd], need=["sd"])),
    (6, "gpu", script("check_gpu.py", lambda a: ["--host-elf", a.host_elf] if a.host_elf else [])),
    (7, "platform", script("check_platform.py")),
    (8, "checklist", script("check_checklist.py")),
]


def main(argv):
    ap = argparse.ArgumentParser()
    for o in ("build", "sd", "nro", "nro-elf", "guest-elf", "host-elf", "boot-json", "ref-bin", "json", "only"):
        ap.add_argument("--" + o)
    a = ap.parse_args(argv)
    art = Art(a)
    only = {int(x) for x in a.only.split(",")} if a.only else None
    rows = []
    for n, name, fn in CHECKS:
        if only and n not in only:
            continue
        status, note = fn(art)
        rows.append({"n": n, "check": name, "status": status, "note": note})
        print(f"{n} {name:9} {status:4}  {note}")
    open_ = sum(r["status"] != "pass" for r in rows)
    print(f"metric: open device-readiness checks = {open_} of {len(rows)}")
    if a.json:
        json.dump({"schema": "fnvnx-nx1/1", "open": open_, "checks": rows}, open(a.json, "w"), indent=1)
    return 0 if open_ == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
