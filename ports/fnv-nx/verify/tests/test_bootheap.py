#!/usr/bin/env python3
"""The heap contract in bootmachine.Machine.__init__: heap hi > lo, page-aligned, hi <= 2^32, disjoint from image, stacks and host_page.
The plan is the standard one of bootlayout.plan, edited one condition at a time. Exit 0 on pass."""
import copy
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
sys.path.insert(0, HERE)
import bootlayout  # noqa: E402
import boothost  # noqa: E402
import bootmachine  # noqa: E402
from test_bootlayout import make_elf  # noqa: E402

QUANTUM = 20000   # instructions per slice (bootmachine.py rule 3): a declared parameter of these runs

FAILED, N = [], [0]


def case(name, cond, extra=""):
    N[0] += 1
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:300]))
    if not cond:
        FAILED.append(name)


def main():
    elf = make_elf()
    rows = [("keys", 0xf0000, 0x200000), ("exe", 0x400000, 0x1409000)]
    std = bootlayout.plan(bootlayout.parse_elf(elf), rows, 4 << 20)
    root = tempfile.mkdtemp(prefix="bh-")

    def build(edit):
        plan = copy.deepcopy(std)
        edit(plan["regions"])
        try:
            bootmachine.Machine(elf, plan, boothost.Services(root), quantum=QUANTUM)
            return None
        except bootmachine.MachineError as e:
            return str(e)

    case("the standard plan passes", build(lambda r: None) is None, build(lambda r: None))
    H = std["regions"]["heap"]

    def setheap(lo, hi):
        def f(r):
            r["heap"] = {"lo": lo, "hi": hi}
        return f

    for name, lo, hi, word in (
            ("empty (hi == lo)", H["lo"], H["lo"], "empty"),
            ("hi < lo", H["lo"], H["lo"] - 0x1000, "empty"),
            ("lo not page-aligned", H["lo"] + 1, H["hi"], "aligned"),
            ("hi not page-aligned", H["lo"], H["hi"] + 1, "aligned"),
            ("hi above 2^32", H["lo"], (1 << 32) + 0x1000, "4 GiB"),
    ):
        e = build(setheap(lo, hi))
        case("violated: " + name, e is not None and word in e, e)
    case("hi == 2^32 exactly passes the bound", "4 GiB" not in (build(setheap(H["lo"], 1 << 32)) or ""))
    for n in ("image", "stacks", "host_page"):
        lo, hi = std["regions"][n]["lo"], std["regions"][n]["hi"]
        e = build(setheap(lo, hi))
        case("violated: heap overlaps " + n, e is not None and n in e, e)
        e = build(setheap(lo - 0x1000, lo + 0x1000))
        case("violated: heap straddles the start of " + n, e is not None and n in e, e)
    print("%d cases, %d failed" % (N[0], len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
