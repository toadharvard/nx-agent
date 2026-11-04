#!/usr/bin/env python3
"""Self-test of NX1 check 3's decoder (tests/nx1/check_svc.py classify) on hand-assembled encodings."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_svc import APP_SVCS, classify  # noqa: E402

CASES = [  # (encoding, expected kind or None, what)
    (0xD4000001 | 0x27 << 5, "svc", "svc #0x27 OutputDebugString"),
    (0xD4000002, "bad", "hvc #0"), (0xD4000003, "bad", "smc #0"), (0xD4400000, "bad", "hlt #0"),
    (0xD4200000, "brk", "brk #0"), (0xD69F03E0, "bad", "eret"),
    (0xD503207F, "bad", "wfi"), (0xD503205F, None, "wfe"), (0xD503203F, None, "yield"), (0xD503201F, None, "nop"),
    (0xD5033BBF, None, "dmb ish"), (0xD5033F9F, None, "dsb sy"), (0xD5033FDF, None, "isb"),
    (0xD50343DF, "bad", "msr daifset, #3"), (0xD50040BF, "bad", "msr spsel, #0"),
    (0xD50B7420, None, "dc zva, x0"), (0xD50B7E20, None, "dc civac, x0"), (0xD50B7A20, None, "dc cvac, x0"),
    (0xD50B7520, None, "ic ivau, x0"), (0xD5087620, "bad", "dc ivac, x0"), (0xD508751F, "bad", "ic iallu"),
    (0xD508871F, "bad", "tlbi vmalle1"),
    (0xD53BE020, None, "mrs x0, cntpct_el0"), (0xD53BE000, None, "mrs x0, cntfrq_el0"),
    (0xD53BE040, "bad", "mrs x0, cntvct_el0 (traps: CNTKCTL_EL1.EL0VCTEN = 0)"),
    (0xD53B0020, "bad", "mrs x0, ctr_el0"), (0xD5380000, "bad", "mrs x0, midr_el1"),
    (0xD53BD040, None, "mrs x0, tpidr_el0"), (0xD51BD040, None, "msr tpidr_el0, x0"),
    (0xD53BD060, None, "mrs x0, tpidrro_el0"), (0xD51BD060, "bad", "msr tpidrro_el0, x0"),
    (0xD53B4400, None, "mrs x0, fpcr"), (0xD51B4420, None, "msr fpsr, x0"), (0xD53B4200, None, "mrs x0, nzcv"),
    (0xD53B00E0, None, "mrs x0, dczid_el0"), (0xD53B4220, "bad", "mrs x0, daif"),
    (0x8B020020, None, "add x0, x1, x2"), (0x94000000, None, "bl"),
]


def main():
    fails = 0
    for w, want, what in CASES:
        r = classify(w)
        got = r[0] if r else None
        ok = got == want
        fails += not ok
        print(("ok   " if ok else "FAIL ") + f"{w:#010x} {what}: {got}")
    for bad in (0x7F, 0x60, 0x79, 0x4E, 0x3C):
        ok = bad not in APP_SVCS
        fails += not ok
        print(("ok   " if ok else "FAIL ") + f"svc {bad:#x} refused")
    print(f"{fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
