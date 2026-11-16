#!/usr/bin/env python3
"""bootrt (tests/bootrt, recipe root of target host): the embedder symbols of the boot machine's host objects. The library is the recipe product in GATE_CACHE (gate_build.ensure, the verdict's own store).
fnvnx_reach_violation is a product symbol (platform/win32/platstat.cpp) and is not defined here. Exit 0 on pass."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
sys.path.insert(0, HERE)
import bootgpu  # noqa: E402
import bootlayout  # noqa: E402
import boothost  # noqa: E402
import bootmachine  # noqa: E402
import hostabirec  # noqa: E402
import gate_build  # noqa: E402
from test_bootlayout import ENTRY, make_elf  # noqa: E402
from test_bootmachine import QUANTUM  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(VERIFY)))
TMP = tempfile.mkdtemp(prefix="brt-")
GATE_CACHE = os.environ.get("GATE_CACHE") or os.path.join(TMP, "gate-cache")
FAILED, N = [], [0]

def case(name, cond, extra=""):
    N[0] += 1
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:300]))
    if not cond:
        FAILED.append(name)


def main():
    REACH = hostabirec.record().define("FNVNX_REACH_EXIT")
    res, _ = gate_build.ensure(bootgpu.BOOTRT_ROOT, GATE_CACHE, REPO)
    case("bootrt builds green through recipe.py", res.get("status") == "green" and res.get("elf"), res.get("failure"))
    if res.get("status") != "green":
        print("%d cases, %d failed" % (N[0], len(FAILED)))
        return 1
    so = res["elf"]["path"]
    case("bootgpu finds the same product in GATE_CACHE", bootgpu.bootrt_path(GATE_CACHE) == so)
    nm = subprocess.run(["nm", "-D", "--defined-only", so], capture_output=True, text=True).stdout.split("\n")
    defined = sorted(l.split()[-1] for l in nm if l.strip())
    case("defined symbols: exactly the three", defined == sorted(["nxgl4_guest_ptr", "nxgl4_on_present", "nxgl4_shim_set"]), defined)
    # the machine's side: the run record of a host-object exit, full width, as run() would classify a guest's exit
    elf = make_elf()
    plan = bootlayout.plan(bootlayout.parse_elf(elf), [("keys", 0xf0000, 0x200000), ("exe", 0x400000, 0x1409000)], 4 << 20, entry=ENTRY)
    m = bootmachine.Machine(elf, plan, boothost.Services(tempfile.mkdtemp(prefix="brt-m-")), quantum=QUANTUM)
    r = m.host_exit(REACH | 0xABCDEF)
    case("host_exit reach: reason reach-violation, fact_id, exit_code in full", r["reason"] == "reach-violation" and r["fact_id"] == 0xABCDEF
         and r["exit_code"] == REACH | 0xABCDEF, r)
    r = m.host_exit(0)
    case("host_exit 0 / an ordinary code stays reason exit", r["reason"] == "exit" and r["exit_code"] == 0)
    print("%d cases, %d failed" % (N[0], len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
