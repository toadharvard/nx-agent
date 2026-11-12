#!/usr/bin/env python3
"""NX1 check 5: the filesystem layout on the SD card.

    check_fs.py [--sd <package SD tree>] [--no-s4]

  layout   toolchain/include/fnvnx/sdlayout.h: one directory sdmc:/switch/<name> (hbmenu lists the NROs of
           /switch/<dir>/), every FNVNX_SD_* path inside it; the fs-relative twins match;
  sources  every "sdmc:/..." string literal in the code that runs on the console (launcher, host, guest runtime,
           platform, startup, loader) is inside FNVNX_SD_DIR or Atmosphere's contents folder; the guest's root
           (os_default_root in platform/guest/os_guest.c and platform_host/os/os_horizon.c) is FNVNX_SD_DIR, the exe
           path of boot.cpp is FNVNX_SD_EXE, path.cpp mounts C:\\FalloutNV at the root itself (so Data/ is
           sdmc:/switch/fnv/Data) and the profile under <root>/user;
  case     os_fs_case_insensitive() is probed at run time, not assumed (the SD's case folding is unverified on
           hardware);
  runtime  tests/s4 (os_posix backend): the game's paths in mixed case resolve, the profile directories (INI, Saves,
           plugins.txt) are writable (cases nx1_sd_layout_game_paths, nx1_profile_writable and the path tests);
  package  (with --sd) fnv.nro, fnv-exefs.nsp, guest.nso at the sdlayout paths, nothing of the player's game in the
           package (no FalloutNV.exe, no Data/), no atmosphere/ folder.
Render code (render/) is checked by check 6 (shader cache paths), not here.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SDLAYOUT = os.path.join(ROOT, "toolchain", "include", "fnvnx", "sdlayout.h")
SHIPPED = ["toolchain/nro", "toolchain/host", "toolchain/guest", "toolchain/game", "toolchain/include", "platform",
           "startup"]
sys.path.insert(0, HERE)
from check_nro import Checks  # noqa: E402


def strip_comments(src: str) -> str:
    """C/C++ comments removed, string literals kept (a // inside "sdmc://x" is not a comment)."""
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c in "\"'":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1])
            i = j + 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("\n" * src.count("\n", i, j))   # line numbers stay right
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def parse_layout(path=SDLAYOUT) -> dict[str, str]:
    """#define NAME "lit" [NAME "lit"]... -> NAME: concatenated string (macros of this file expanded)."""
    defs: dict[str, str] = {}
    text = re.sub(r'\\\n', ' ', strip_comments(open(path).read()))   # join continuation lines
    for m in re.finditer(r'^#define[ \t]+(FNVNX_\w+)[ \t]+(.+)$', text, re.M):
        parts = re.findall(r'"((?:[^"\\]|\\.)*)"|(FNVNX_\w+)', m.group(2))
        if not parts:
            continue
        defs[m.group(1)] = "".join(lit if lit else defs.get(ref, "?" + ref) for lit, ref in parts)
    return defs


def source_files():
    for d in SHIPPED:
        for dp, _, fns in os.walk(os.path.join(ROOT, d)):
            for f in fns:
                if f.endswith((".c", ".cpp", ".h", ".inc", ".hpp")):
                    yield os.path.join(dp, f)


def check(sd: str | None, run_s4: bool) -> Checks:
    c = Checks()
    L = parse_layout()
    sd_dir = L.get("FNVNX_SD_DIR", "")
    c.ok("layout.dir", re.fullmatch(r"sdmc:/switch/[A-Za-z0-9_-]+", sd_dir) is not None,
         f"FNVNX_SD_DIR {sd_dir!r} is not sdmc:/switch/<name>: hbmenu would not list fnv.nro")
    inside = [k for k, v in L.items() if k.startswith("FNVNX_SD_") and k not in ("FNVNX_SD_DIR", "FNVNX_SD_PORT_NAMES")]
    bad = [k for k in inside if not L[k].startswith(sd_dir + "/")]
    c.ok("layout.inside", inside and not bad, f"outside {sd_dir}: {bad}")
    c.ok("layout.nro_name", L.get("FNVNX_SD_NRO") == sd_dir + "/fnv.nro", f"FNVNX_SD_NRO {L.get('FNVNX_SD_NRO')}")
    c.ok("layout.data", L.get("FNVNX_SD_DATA") == sd_dir + "/Data", "Data/ must sit next to FalloutNV.exe in the root")
    for fs_, sdk in (("FNVNX_FS_CRASH_LOG", "FNVNX_SD_CRASH_LOG"), ("FNVNX_FS_MARKER", "FNVNX_SD_MARKER")):
        c.ok(f"layout.{fs_.lower()}", "sdmc:" + L.get(fs_, "?") == L.get(sdk), f"{fs_} {L.get(fs_)} != {L.get(sdk)}")
    c.ok("layout.override_fmt", "sdmc:" + L.get("FNVNX_FS_OVERRIDE_FMT", "?") == L.get("FNVNX_ATMOSPHERE_OVERRIDE_FMT"),
         "the two override formats differ")

    # sources
    allowed = (sd_dir + "/", "sdmc:/atmosphere/contents/")
    stray = []
    for p in source_files():
        txt = strip_comments(open(p, errors="replace").read())
        for m in re.finditer(r'"(sdmc:/[^"]*)"', txt):
            lit = m.group(1)
            if lit in (sd_dir, "sdmc:/", "sdmc:/switch", "sdmc:/atmosphere") or lit.startswith(allowed) \
                    or p == SDLAYOUT:
                continue
            stray.append(f"{os.path.relpath(p, ROOT)}: {lit}")
    c.ok("src.sdmc_literals", not stray, f"{len(stray)} paths outside {sd_dir}: " + "; ".join(stray[:6]))
    for rel in ("platform/guest/os_guest.c", "platform_host/os/os_horizon.c"):
        txt = strip_comments(open(os.path.join(ROOT, rel)).read())
        m = re.search(r'os_default_root\s*\(\s*void\s*\)\s*\{\s*return\s+([^;]+);', txt)
        val = m.group(1).strip() if m else None
        good = val in ("FNVNX_SD_DIR", f'"{sd_dir}"')
        c.ok(f"src.root.{os.path.basename(rel)}", good, f"os_default_root returns {val}, not FNVNX_SD_DIR")
    boot = strip_comments(open(os.path.join(ROOT, "toolchain", "game", "boot.cpp")).read())
    m = re.search(r'k_exe\s*=\s*([^;]+);', boot)
    # either the fixed FNVNX_SD_EXE or S5-data's os_game_dir() (FNVNX_SD_DIR unless fnvnx.ini sets game_dir=)
    via_game_dir = re.search(r'os_game_dir\(\)[^;]*FalloutNV\.exe', boot) is not None
    c.ok("src.boot_exe", via_game_dir or m is not None and m.group(1).strip() in ("FNVNX_SD_EXE", f'"{L.get("FNVNX_SD_EXE")}"'),
         f"boot.cpp k_exe = {m.group(1) if m else None}")
    og_all = strip_comments(open(os.path.join(ROOT, "platform", "guest", "os_guest.c")).read())
    gd = re.search(r'os_game_dir\s*\(\s*void\s*\)\s*\{(.*?)\n\}', og_all, re.S)
    if gd:
        c.ok("src.game_dir_default", "FNVNX_SD_DIR" in gd.group(1) or f'"{sd_dir}"' in gd.group(1) or "os_default_root" in gd.group(1),
             "os_game_dir() does not default to FNVNX_SD_DIR")
    pc = strip_comments(open(os.path.join(ROOT, "platform", "win32", "path.cpp")).read())
    c.ok("src.game_mount_root", re.search(r'fnvnx_path_mount\(\s*GAME_DIR\s*,\s*g_root\s*\)', pc) is not None,
         "path.cpp does not mount C:\\FalloutNV at the root (Data/ would not be sdmc:/switch/fnv/Data)")
    c.ok("src.user_mount", re.search(r'"%s/user"', pc) is not None, "path.cpp: no <root>/user profile mount")
    c.ok("src.drive_mount", re.search(r'"%s/drive_%c"', pc) is not None, "path.cpp: other drives not under <root>/drive_<x>")
    names = L.get("FNVNX_SD_PORT_NAMES", "").split()
    own = {os.path.basename(v) for k, v in L.items() if k.startswith("FNVNX_SD_") and k not in ("FNVNX_SD_DIR", "FNVNX_SD_EXE",
                                                                                                 "FNVNX_SD_DATA", "FNVNX_SD_PORT_NAMES")}
    c.ok("layout.port_names", own <= set(names) and {"user", "drive_*"} <= set(names),
         f"FNVNX_SD_PORT_NAMES lacks {sorted(own - set(names))}")
    c.ok("layout.port_names_not_game", not {"FalloutNV.exe", "Data"} & set(names), "a game file is listed as the port's")
    og = strip_comments(open(os.path.join(ROOT, "platform", "guest", "os_guest.c")).read())
    m = re.search(r'int\s+os_fs_case_insensitive\s*\(\s*void\s*\)\s*\{(.*?)\n\}', og, re.S)
    c.ok("case.probed", m is not None and "os_stat" in m.group(1),
         "os_fs_case_insensitive() is a constant: case folding of the SD is assumed, not probed")

    if run_s4:
        r = subprocess.run(["make", "-s", "-C", os.path.join(ROOT, "tests", "s4")], capture_output=True, text=True, errors="replace",
                           timeout=900)
        out = r.stdout + r.stderr
        want = ["nx1_sd_layout_game_paths", "nx1_profile_writable", "path_case_insensitive_resolution",
                "path_mapping_rules"]
        for t in want:
            c.ok(f"s4.{t}", re.search(rf"^{t}\s+ok$", out, re.M) is not None, f"tests/s4 {t} did not pass (make rc {r.returncode})")
    if sd:
        base = os.path.join(sd, *sd_dir[len("sdmc:/"):].split("/"))
        for k in ("FNVNX_SD_NRO", "FNVNX_SD_EXEFS", "FNVNX_SD_GUEST"):
            rel = L[k][len(sd_dir) + 1:]
            c.ok(f"pkg.{rel}", os.path.isfile(os.path.join(base, rel)), f"{L[k]} not in the package")
        c.ok("pkg.no_game_files", not os.path.exists(os.path.join(base, "FalloutNV.exe"))
             and not os.path.exists(os.path.join(base, "Data")), "the package holds game files: they are the player's")
        c.ok("pkg.no_atmosphere", not os.path.exists(os.path.join(sd, "atmosphere")),
             "the package installs an override directly; the launcher does that, for the title it runs under")
    return c


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sd")
    ap.add_argument("--no-s4", action="store_true")
    a = ap.parse_args(argv)
    c = check(a.sd, not a.no_s4)
    c.print()
    print(f"fs: {sum(r[1] for r in c.rows)}/{len(c.rows)} pass -> {'PASS' if c.passed else 'FAIL'}")
    return 0 if c.passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
