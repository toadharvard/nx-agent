#!/usr/bin/env python3
"""UNMAP_RANGE (module 11, fn 37) in the boot machine, against hostapi.h. A Machine is built on the synthetic ELF of test_bootlayout and no
guest code runs: the service is called through ext_call, as the guest's host call reaches it. Exit 0 on pass."""
import os
import re
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
sys.path.insert(0, HERE)
import bootlayout  # noqa: E402
import boothost  # noqa: E402
import bootmachine  # noqa: E402
from test_bootlayout import ENTRY, make_elf  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(VERIFY)))
HDR = os.path.join(REPO, "ports", "fnv-nx", "hostabi", "include", "fnvnx", "hostapi.h")
FAILED = []
N = [0]
P = 0x1000


def case(name, cond, extra=""):
    N[0] += 1
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:300]))
    if not cond:
        FAILED.append(name)


def define(src, name):
    m = re.search(r"#define\s+%s\s+(0x[0-9A-Fa-f]+|\d+)(?:ull|u)?\b" % name, src)
    return int(m.group(1), 0) if m else None


def main():
    src = open(HDR).read()
    FN, BAD, RNG = (define(src, n) for n in ("FNVNX_PLAT_UNMAP_RANGE", "FNVNX_HE_BADINPUT", "FNVNX_HE_RANGE"))
    NOSVC = define(src, "FNVNX_EXT_NO_SERVICE")
    case("header: UNMAP_RANGE is 37, BADINPUT 0x1759, RANGE 0xD401", (FN, BAD, RNG) == (37, 0x1759, 0xD401), (FN, BAD, RNG))
    elf = make_elf()
    rows = [("keys", 0xf0000, 0x200000), ("exe", 0x400000, 0x1409000), ("mod1", 0x1410000, 0x1500000), ("mod2", 0x4000000, 0x4200000)]
    plan = bootlayout.plan(bootlayout.parse_elf(elf), rows, 4 << 20, entry=ENTRY)
    m = bootmachine.Machine(elf, plan, boothost.Services(tempfile.mkdtemp(prefix="bu-")), quantum=20000)
    ARGS = 0x1400000   # inside the window, scratch for the argument array
    m.map_fixed(ARGS & ~0xFFF, P, 0)

    def call(fn, *a):
        m.uc.mem_write(ARGS, struct.pack("<%dQ" % len(a), *a) if a else b"")
        return m.ext_call(11, fn, ARGS, len(a))

    def rd(addr, n):
        return bytes(m.uc.mem_read(addr, n))

    LO, HI = bootmachine.WINDOW
    case("window is as the header lists it", (LO, HI) == (0x400000, 0x2100000), (LO, HI))
    A = 0x800000
    case("fn 37 is served", 37 in m.plat and FN in m.plat)
    case("size 0 -> BADINPUT", call(FN, A, 0) == BAD)
    case("addr+size wraps -> BADINPUT", call(FN, (1 << 64) - P, 2 * P) == BAD)
    case("addr unaligned -> BADINPUT", call(FN, A + 1, P) == BAD)
    case("size unaligned -> BADINPUT", call(FN, A, P + 1) == BAD)
    case("BADINPUT is checked before RANGE (unaligned and outside)", call(FN, 0x10001, P) == BAD)
    case("never mapped page of the window -> RANGE", call(FN, A, P) == RNG)
    case("outside the window -> RANGE", call(FN, 0x3000000, P) == RNG and call(FN, 0x100000, P) == RNG)
    case("fewer arguments than the row -> BAD_NARGS, not served as a call", call(FN, A) != 0)
    # map, dirty, protect, unmap, map: zeroed and default protection
    case("map_fixed in the window", m.map_fixed(A, 4 * P, 0) == 0)
    m.uc.mem_write(A, b"\xAA" * (4 * P))
    case("protect read-only (VM_PROTECT)", call(8, A, 4 * P, 1) == 0, hex(call(8, A, 4 * P, 1)))
    case("partial overlap (2 mapped + 1 never mapped page) -> RANGE, nothing changes",
         call(FN, A + 2 * P, 3 * P) == RNG and rd(A, 4 * P) == b"\xAA" * (4 * P) and m.win_live >= {A + i * P for i in range(4)})
    case("unmap of the mapped range -> 0", call(FN, A, 4 * P) == 0)
    case("second unmap of it -> RANGE (not mapped any more)", call(FN, A, 4 * P) == RNG)
    case("pages are free again", not (m.win_live & {A + i * P for i in range(4)}))
    case("memory is zero after unmap", rd(A, 4 * P) == bytes(4 * P))
    case("map again: fresh zeroed memory, default protection (writable)", m.map_fixed(A, 4 * P, 0) == 0 and rd(A, 4 * P) == bytes(4 * P)
         and m.uc.mem_write(A, b"\x55") is None)
    case("a sub-range unmaps alone", call(FN, A + P, P) == 0 and (A in m.win_live) and (A + P not in m.win_live)
         and (A + 2 * P in m.win_live))
    # window edges
    for name, ed in (("lower edge 0x400000", LO), ("upper edge", HI - P), ("0xFDF000 boundary", 0xFDF000 - P), ("past 0xFDF000", 0xFDF000)):
        ok = m.map_fixed(ed, P, 0) == 0 and call(FN, ed, P) == 0 and rd(ed, P) == bytes(P)
        case("edge: " + name, ok)
    case("range crossing the upper edge -> RANGE", call(FN, HI - P, 2 * P) == RNG)
    case("range crossing the lower edge -> RANGE", call(FN, LO - P, 2 * P) == RNG)
    case("a long range over the whole mapped run", m.map_fixed(0x1000000, 64 * P, 0) == 0 and call(FN, 0x1000000, 64 * P) == 0)
    case("fn 39 -> NO_SERVICE (38 is THREAD_CPU)", call(39, 0, 0) == NOSVC and NOSVC is not None, (NOSVC,))
    print("%d cases, %d failed" % (N[0], len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
