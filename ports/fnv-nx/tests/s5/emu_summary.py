#!/usr/bin/env python3
"""Summarize an emulated game boot (tests/s5/emu_guest.py log): boot milestones, trap hits by category, skipped
initializers by fault, the stop reason and the last symbolized frames.

    python3 -I tests/s5/emu_summary.py <emu log> [<S2 addrmap.tsv>]

With the S2 address map the distinct trapped names are classified by toolchain/game/undef_report.py."""
import collections
import os
import re
import subprocess
import sys

REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../toolchain/game/undef_report.py")


def main():
    log = open(sys.argv[1], errors="replace").read().splitlines()
    miles = [l.strip() for l in log if re.search(r"fnvnx boot: (?!trap|initializer #|import )", l)]
    seen = set()
    print("milestones:")
    for m in miles:
        if m not in seen:
            seen.add(m)
            print("  " + m)
    game = [l.split("\t") for l in log if l.startswith("milestone\t")]   # emu_guest.py --milestones
    if game:
        got = [g[1] for g in game if len(g) > 2 and g[2] == "reached"]
        print(f"game milestones (tests/s5/milestones.txt): {len(got)} of {len(game)} reached"
              + (f", last: {got[-1]}" if got else ""))
        ooo = [g[1] for g in game if len(g) > 2 and g[2] == "out-of-order"]
        if ooo:
            print(f"  hit out of order (not counted): {', '.join(ooo)}")
        for g in game:
            print(f"  {g[1]}: " + (f"{g[3]} {g[4]}" if len(g) > 4 and g[2] == "reached" else
                                   f"hit out of order ({g[-1]})" if len(g) > 2 and g[2] == "out-of-order" else
                                   "not reached"))
    names = [m.group(1) for l in log for m in [re.search(r"trap #\d+: unresolved (\S+)", l)] if m]
    names = list(dict.fromkeys(names))
    after = 0
    for i, l in enumerate(log):
        if "calling WinMain" in l:
            after = sum(1 for x in log[i:] if re.search(r"trap #\d+: unresolved", x))
            break
    print(f"distinct trapped symbols: {len(names)} ({after} first seen after WinMain was called)")
    if len(sys.argv) > 2 and names:
        lst = sys.argv[1] + ".traps.txt"
        open(lst, "w").write("\n".join(names) + "\n")
        rep = subprocess.run([sys.executable, "-I", REPORT, lst, sys.argv[2]], capture_output=True, text=True).stdout
        for l in rep.split("\n\n")[1].splitlines() if "\n\n" in rep else []:
            print("  " + l.strip())
    ptr = [l for l in log if "fnvnx boot: trap: " in l]
    if ptr:
        print(f"image code pointer / IAT traps (first 100 logged): {len(ptr)}")
        for l in ptr[:8]:
            print("  " + l.strip())
    imp = [l.strip() for l in log if re.search(r"fnvnx boot: import \S+: no export", l)]
    if imp:
        print(f"unresolved imports: {len(imp)}: " + ", ".join(x.split("import ")[1].split(":")[0] for x in imp))
    skips = [l for l in log if l.strip().startswith("skipped initializer")]
    kinds = collections.Counter(re.search(r": ([A-Za-z -]+) \(UC_ERR", l).group(1) if re.search(r"\(UC_ERR", l) else "?"
                                for l in skips)
    tot = [l for l in log if "initializers skipped after a fault" in l]
    print(f"skipped initializers: {tot[-1].strip() if tot else len(skips)}; printed {len(skips)}: {dict(kinds)}")
    for i, l in enumerate(log):
        if l.startswith("== trace"):
            print(l)
            for x in log[i + 1:i + 12]:
                if "fnvnx boot:" in x:
                    break
                print(x)
            break
    stop = [l for l in log if l.startswith(("STOPPED", "OK", "FAIL"))]
    print(stop[-1] if stop else log[-1] if log else "(empty log)")


if __name__ == "__main__":
    main()
