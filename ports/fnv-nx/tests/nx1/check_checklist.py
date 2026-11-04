#!/usr/bin/env python3
"""NX1 check 8: the owner's one-page checklist for the first hardware run (docs/NRO_CHECKLIST.md).

  page     at most 60 lines and 5,000 characters;
  steps    names every file the launcher requires (toolchain/nro/launcher.c's list, via fnvnx/sdlayout.h), the Data
           files it looks for, title takeover (hold R), A to start, Y to clean up, Atmosphere, the private branch
           nro/<commit> the package comes from;
  logs     names every log the owner sends back (launcher, host, crash: FNVNX_SD_*_LOG) and where they are;
  messages the launcher's messages the troubleshooting table quotes exist in launcher.c, so the owner can match
           what the screen says.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
from check_fs import parse_layout  # noqa: E402
from check_nro import Checks  # noqa: E402


def main():
    c = Checks()
    p = os.path.join(ROOT, "docs", "NRO_CHECKLIST.md")
    if not c.ok("exists", os.path.isfile(p), "docs/NRO_CHECKLIST.md missing"):
        c.print()
        return 1
    t = open(p).read()
    lines = t.splitlines()
    c.ok("page.lines", len(lines) <= 60, f"{len(lines)} lines")
    c.ok("page.chars", len(t) <= 5000, f"{len(t)} characters")
    L = parse_layout()
    sd = L["FNVNX_SD_DIR"][len("sdmc:"):]
    c.ok("steps.dir", f"{sd}/" in t, f"{sd}/ not named")
    for k in ("FNVNX_SD_NRO", "FNVNX_SD_EXEFS", "FNVNX_SD_GUEST", "FNVNX_SD_EXE", "FNVNX_SD_DATA"):
        base = os.path.basename(L[k])
        c.ok(f"steps.{base}", base in t, f"{base} not named")
    launcher = open(os.path.join(ROOT, "toolchain", "nro", "launcher.c")).read()
    for f in re.findall(r'"(FalloutNV\.esm|Fallout - \w+\.bsa)"', launcher)[:1]:
        c.ok(f"steps.data.{f}", f in t, f"{f} not named")
    for k, rx in (("takeover", r"hold R"), ("start_a", r"Press \*\*A\*\*"), ("cleanup_y", r"\*\*Y\*\*"),
                  ("atmosphere", r"Atmosph[eè]re"), ("branch", r"nro/<commit>")):
        c.ok(f"steps.{k}", re.search(rx, t) is not None, f"no {k} step")
    for k in ("FNVNX_SD_LAUNCH_LOG", "FNVNX_SD_HOST_LOG", "FNVNX_SD_CRASH_LOG"):
        base = os.path.basename(L[k])
        c.ok(f"logs.{base}", f"`{base}`" in t, f"{base} not in the send-back list")
    for q in ("applet mode", "MISSING", "already exists and is not fnv-nx's"):
        c.ok(f"messages.{q}", q in t and q in launcher, f"'{q}' is quoted by the checklist but not printed by the launcher")
    c.print()
    print(f"checklist: {sum(r[1] for r in c.rows)}/{len(c.rows)} pass -> {'PASS' if c.passed else 'FAIL'}")
    return 0 if c.passed else 1


if __name__ == "__main__":
    sys.exit(main())
