#!/usr/bin/env python3
"""blockers.py - the blocker table of a discovery boot (emu_guest.py --discover): one row per faulting function, with
the milestone it blocks, the fault, the caller, the link and a first guess at the owning stream.

    tests/s5/blockers.py <emu_stub.log> <link12> [--out blockers.tsv]

Rows come from the log's `discovered` (discovery mode), `stubbed-fault` and `skip-summary` lines, and from the
checks of emu_sanitize.py (--alloc-check: `alloc-check-summary`, --image-check: `image-check-summary`; the function
column is the code that freed the block or made the bad access, back_in the allocating family and function or the
caller). Owner guesses
(the reviewer confirms each before handing it on):
    S2   vtable dispatch: a call through a bad slot ('vslot', 'virtual function defined nowhere', fetch faults)
    R4   platform: a fault in platform/ code (file, BSA, threads, TLS, input, Win32 shims)
    S16  renderer: D3D9 / nx::Renderer / NiDX9
    R2   function bodies: every other fault in a decompiled function ([orig <addr>])
    S5   the emulator, stand-ins and the boot runtime (fnvnx_*, newlib)
  alloc-check / image-check rows: the first frame of the free stack (or the faulting pc) decides: [orig ...] bodies R2,
  platform/ini.cpp and the platform names R4, stand-ins, loader, fixups and newlib S5.
The table holds names and addresses only (no game data), so it may go to the public repository or a message."""
import argparse
import re
import sys

COLS = ["milestone", "count", "fault", "function", "orig", "back_in", "kind", "owner_guess", "link"]
PLATFORM = re.compile(r"^(_ZN12_GLOBAL__N_1|os_|fnvnx_(file|plat|bsa|thread|tls|input)|Get|Set|Create|Read|Write|"
                      r"_Z\d+(Get|Set|Create|Find|Read|Write)\w*A?|w32_|Nt|Rtl)")
RENDER = re.compile(r"(D3D|Direct3D|NiDX9|nx\d?Renderer|_ZN2nx|Shader|Texture|RenderTarget)")


def owner(func, where):
    if re.search(r"vslot|defined nowhere|Invalid memory fetch|non-executable", where):
        return "S2"
    if RENDER.search(func):
        return "S16"
    if "[orig " in func or "[orig " in where:
        return "R2"
    if PLATFORM.search(func):
        return "R4"
    return "S5"


def frame0(stack):
    """the first frame of 'a < b < c' as a function name with its [orig] tag kept"""
    return re.sub(r"\+0x[0-9a-f]+$", "", stack.split(" < ")[0].split(" | ")[0].strip())


def check_rows(log):
    """(milestone, count, fault, function-with-orig, back_in, kind) of the alloc-check and image-check summaries"""
    first, out = {}, []
    lines = [l.rstrip("\n").split("\t") for l in open(log, errors="replace")]
    for f in lines:
        if f[0] == "alloc-check" and len(f) >= 7:
            first.setdefault((f[1], f[2], f[3]), f)
    for f in lines:
        if f[0] == "alloc-check-summary" and len(f) >= 6:
            r = first.get((f[2], f[3], f[4]))
            who = frame0(r[5]) if r else f[3]
            alloc = frame0(r[6]) if r else f[6] if len(f) > 6 else "-"
            out.append(("(alloc-check)", int(f[1]), f[2], who, f"{f[3]} <- {f[4]} allocated in {alloc}", "alloc-check"))
        elif f[0] == "image-check-summary" and len(f) >= 7:
            # count kind section pc-function lr-function first-addr [native=..]
            nat = (" " + f[7]) if len(f) > 7 else ""
            fetch = f[2] in ("fetch-text", "rva-as-address")   # the pc is the bad target: the caller made the call
            who, other = (f[5], f[4]) if fetch else (f[4], f[5])
            out.append(("(image-check)", int(f[1]), f"{f[2]} {f[6]} in {f[3]}{nat}", who,
                        f"{'pc' if fetch else 'lr'} {other}", "image-check"))
    return out


def rows(log, link):
    out = check_rows(log)
    for line in open(log, errors="replace"):
        f = line.rstrip("\n").split("\t")
        if f[0] == "discovered" and len(f) >= 6:
            func, fault, back, before = f[2], f[3], f[4].replace("first back in ", ""), f[5].replace("before ", "")
            out.append((before, int(f[1]), fault, func, back, "discovered"))
        elif f[0] == "stubbed-fault" and len(f) >= 5:
            out.append(("(stub table)", int(f[1]), f[3], f[4], f[2], "stubbed"))
        elif f[0] == "skip-summary" and len(f) >= 5:
            out.append(("winmain", int(f[1]), f[2], f[3], f[4] if len(f) > 4 else "", "skipped-initializer"))
    res = []
    for before, n, fault, func, back, kind in out:
        m = re.search(r"\[orig ([0-9a-f]{8})\]", func)
        name = re.sub(r" \[orig [0-9a-f]{8}\]", "", func).split(" | ")[0]
        res.append({"milestone": before, "count": str(n), "fault": fault, "function": name,
                    "orig": m.group(1) if m else "", "back_in": back, "kind": kind,
                    "owner_guess": owner(func, func + " " + fault), "link": link})
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("log")
    ap.add_argument("link")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    rs = rows(a.log, a.link)
    text = "\t".join(COLS) + "\n" + "".join("\t".join(r[c] for c in COLS) + "\n" for r in rs)
    if a.out:
        open(a.out, "w").write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
