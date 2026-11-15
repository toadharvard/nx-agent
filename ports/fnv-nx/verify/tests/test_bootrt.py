#!/usr/bin/env python3
"""bootrt (tests/bootrt, recipe root of target host): the embedder symbols of the boot machine's host objects and the proven-unreachable
assertion fnvnx_reach_violation of hostabi/include/fnvnx/reach.h. The library is the recipe product in GATE_CACHE (gate_build.ensure, the verdict's own store). A violation
ends the process, so each case runs in a child python: it loads the library through bootgpu, registers an exit callback that writes the code it
receives to a file, and calls the violation. Exit 0 on pass."""
import json
import os
import re
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
import gate_build  # noqa: E402
from test_bootlayout import make_elf  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(VERIFY)))
RT_H = os.path.join(REPO, "ports", "fnv-nx", "hostabi", "include", "fnvnx", "reach.h")
TMP = tempfile.mkdtemp(prefix="brt-")
GATE_CACHE = os.environ.get("GATE_CACHE") or os.path.join(TMP, "gate-cache")
FAILED, N = [], [0]

CHILD = r"""
import ctypes, json, os, sys
sys.path.insert(0, %(verify)r)
import bootgpu
lib = bootgpu._load_shim(%(cache)r)
mode, out = sys.argv[1], sys.argv[2]
def ex(code):
    open(out, "w").write(json.dumps({"code": code}))
    os._exit(2)
cbs = (bootgpu.PTR_CB(lambda a, s: 0), bootgpu.PRESENT_CB(lambda f: None), bootgpu.EXIT_CB(ex) if mode == "cb" else bootgpu.EXIT_CB())
lib.nxgl4_shim_set(*cbs)
lib.fnvnx_reach_violation(0xABCDEF)
os._exit(77)   # never reached: noreturn
"""


def case(name, cond, extra=""):
    N[0] += 1
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:300]))
    if not cond:
        FAILED.append(name)


def define(name):
    m = re.search(r"#define\s+%s\s+(0x[0-9A-Fa-f]+)u?\b" % name, open(RT_H).read())
    return int(m.group(1), 16)


def main():
    REACH = define("FNVNX_REACH_EXIT")
    case("machine constant equals reach.h (top byte)", bootmachine.REACH_VIOLATION == REACH >> 24)
    res, _ = gate_build.ensure(bootgpu.BOOTRT_ROOT, GATE_CACHE, REPO)
    case("bootrt builds green through recipe.py", res.get("status") == "green" and res.get("elf"), res.get("failure"))
    if res.get("status") != "green":
        print("%d cases, %d failed" % (N[0], len(FAILED)))
        return 1
    so = res["elf"]["path"]
    case("bootgpu finds the same product in GATE_CACHE", bootgpu.bootrt_path(GATE_CACHE) == so)
    nm = subprocess.run(["nm", "-D", "--defined-only", so], capture_output=True, text=True).stdout.split("\n")
    defined = sorted(l.split()[-1] for l in nm if l.strip())
    case("defined symbols: exactly the four", defined == sorted(["fnvnx_reach_violation", "nxgl4_guest_ptr", "nxgl4_on_present", "nxgl4_shim_set"]), defined)
    child = os.path.join(TMP, "child.py")
    open(child, "w").write(CHILD % {"verify": VERIFY, "cache": GATE_CACHE})

    def run(mode):
        out = os.path.join(TMP, "code.json")
        if os.path.exists(out):
            os.remove(out)
        r = subprocess.run([sys.executable, "-s", child, mode, out], capture_output=True, text=True)
        code = json.load(open(out))["code"] if os.path.exists(out) else None
        return r.returncode, r.stderr, code

    rc, err, code = run("cb")
    case("reach: one log line, the exit callback gets 0x52000000 | fact_id (full 32 bits)", err == "fnvnx-reach fact=0xabcdef\n" and code == REACH | 0xABCDEF
         and rc == 2, (rc, err, code))
    rc, err, code = run("nocb")
    case("reach with no exit callback: the log line, then status 1", err == "fnvnx-reach fact=0xabcdef\n" and rc == 1 and code is None, (rc, err, code))

    # the machine's side: the run record of a host-object exit, full width, as run() would classify a guest's exit
    elf = make_elf()
    plan = bootlayout.plan(bootlayout.parse_elf(elf), [("keys", 0xf0000, 0x200000), ("exe", 0x400000, 0x1409000)], 4 << 20)
    m = bootmachine.Machine(elf, plan, boothost.Services(tempfile.mkdtemp(prefix="brt-m-")), app_cores=bootmachine.FNVNX_APP_CORES)
    r = m.host_exit(REACH | 0xABCDEF)
    case("host_exit reach: reason reach-violation, fact_id, exit_code in full", r["reason"] == "reach-violation" and r["fact_id"] == 0xABCDEF
         and r["exit_code"] == REACH | 0xABCDEF, r)
    r = m.host_exit(0)
    case("host_exit 0 / an ordinary code stays reason exit", r["reason"] == "exit" and r["exit_code"] == 0)
    print("%d cases, %d failed" % (N[0], len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
