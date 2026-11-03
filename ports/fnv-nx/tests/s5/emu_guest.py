#!/usr/bin/env python3
"""Run an fnv-nx ILP32 guest NSO under Unicorn with a Python stand-in for the LP64 host.

    python3 -I tests/s5/emu_guest.py build/s5/hello/guest.nso [--expect TEXT]

What it checks without a Switch: the NSO parses and its hashes match (as the host loader checks), the
guest runs at the link address stored in its header (+16) with the size it declares (+20), outside the
reserved original-image range, never writes its text/rodata (pages mapped R-X / R-- like the host maps
them), runs newlib (printf, malloc, nanosleep, clock_gettime) in ILP32, uses
TLS on TPIDR_EL0 and calls host functions through FnvnxHostApi with the AAPCS64 convention. The host side
(libnx, Horizon SVCs) is not exercised here; the hardware test covers it (docs/TOOLCHAIN.md).

Needs: pip install unicorn
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "toolchain", "tools"))
import elf2nso  # noqa: E402

from unicorn import UC_ARCH_ARM64, UC_HOOK_CODE, UC_HOOK_MEM_INVALID, UC_MODE_ARM, UC_PROT_ALL, UC_PROT_EXEC, \
    UC_PROT_READ, UC_PROT_WRITE, Uc, UcError  # noqa: E402
from unicorn.arm64_const import UC_ARM64_REG_CPACR_EL1, UC_ARM64_REG_LR, UC_ARM64_REG_PC, UC_ARM64_REG_SP, \
    UC_ARM64_REG_TPIDR_EL0, UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2  # noqa: E402

PAGE = 0x1000
HOST_PAGE = 0x3F000000          # host API table + stubs (below 4 GiB, as every address in a 32-bit AS)
STACK_TOP, STACK_SIZE = 0x3E000000, 0x100000
HEAP_BASE, HEAP_SIZE = 0x30000000, 0x1000000
RETURN_ADDR = HOST_PAGE + 0xFF0
TICK_FREQ = 19_200_000
SLOTS = ["output_debug_string", "sleep_ns", "exit", "console_write", "console_flush", "main_loop",
         "buttons_down", "system_tick", "system_tick_freq"]


def align(x, a=PAGE):
    return (x + a - 1) & ~(a - 1)


class GuestExit(Exception):
    def __init__(self, code):
        self.code = code


def run(nso_path: str):
    with open(nso_path, "rb") as f:
        info = elf2nso.parse_nso(f.read())
    for s in info["segments"]:
        if not s["hash_ok"]:
            raise SystemExit(f"{nso_path}: {s['name']} hash mismatch")
    text, ro, data = info["segments"]
    magic, _abi, base, size = struct.unpack_from("<IIII", text["bytes"], 8)
    if magic != 0x47584E46:
        raise SystemExit("not an fnv-nx guest (no FNXG magic at +8)")
    if size != align(data["dst"] + data["size"] + data["bss"]):
        raise SystemExit(f"guest header size {size:#x} does not match the NSO")
    if base < 0x200000 or base + size > 0x40000000 or (base < 0x02100000 and base + size > 0x00400000):
        raise SystemExit(f"guest link range {base:#x}..{base + size:#x} not allowed")
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.reg_write(UC_ARM64_REG_CPACR_EL1, 3 << 20)   # FP/SIMD on

    def map_seg(seg, prot, extra=0):
        size = align(seg["size"] + extra)
        uc.mem_map(base + seg["dst"], size, UC_PROT_ALL)
        uc.mem_write(base + seg["dst"], seg["bytes"])
        uc.mem_protect(base + seg["dst"], size, prot)
        return base + seg["dst"] + size

    map_seg(text, UC_PROT_READ | UC_PROT_EXEC)
    map_seg(ro, UC_PROT_READ)
    end = map_seg(data, UC_PROT_READ | UC_PROT_WRITE, data["bss"])
    uc.mem_map(STACK_TOP - STACK_SIZE, STACK_SIZE, UC_PROT_READ | UC_PROT_WRITE)
    uc.mem_map(HEAP_BASE, HEAP_SIZE, UC_PROT_READ | UC_PROT_WRITE)
    uc.mem_map(HOST_PAGE, PAGE, UC_PROT_ALL)

    stubs = {HOST_PAGE + 0x800 + 4 * i: name for i, name in enumerate(SLOTS)}
    api = struct.pack("<IIII", 0x48584E46, 1, 0x78, 0)
    api += struct.pack("<QQQQ", base, end - base, HEAP_BASE, HEAP_SIZE)
    api += b"".join(struct.pack("<Q", a) for a in stubs)
    assert len(api) == 0x78
    uc.mem_write(HOST_PAGE, api)
    uc.mem_write(HOST_PAGE + 0x800, struct.pack("<I", 0xD65F03C0) * len(SLOTS))   # ret (never reached)

    st = {"tick": 1_000_000, "console": bytearray(), "debug": [], "calls": 0}

    def host_call(uc_, name):
        a0, a1 = uc_.reg_read(UC_ARM64_REG_X0), uc_.reg_read(UC_ARM64_REG_X1)
        st["calls"] += 1
        res = 0
        if name == "output_debug_string":
            st["debug"].append(bytes(uc_.mem_read(a0, a1)).decode(errors="replace"))
        elif name == "console_write":
            st["console"] += uc_.mem_read(a0, a1)
            res = a1
        elif name == "sleep_ns":
            st["tick"] += a0 * TICK_FREQ // 1_000_000_000
        elif name == "exit":
            raise GuestExit(a0)
        elif name == "main_loop":
            res = 1
        elif name == "system_tick":
            st["tick"] += 1000
            res = st["tick"]
        elif name == "system_tick_freq":
            res = TICK_FREQ
        for r in (UC_ARM64_REG_X0,):
            uc_.reg_write(r, res)
        uc_.reg_write(UC_ARM64_REG_PC, uc_.reg_read(UC_ARM64_REG_LR))

    def on_code(uc_, addr, size, _):
        if addr in stubs:
            try:
                host_call(uc_, stubs[addr])
            except GuestExit as e:
                st["exit"] = e.code
                uc_.emu_stop()

    def on_bad(uc_, access, addr, size, value, _):
        print(f"  invalid memory access type={access} addr={addr:#x} size={size} pc={uc_.reg_read(UC_ARM64_REG_PC):#x}")
        return False

    uc.hook_add(UC_HOOK_CODE, on_code, begin=HOST_PAGE, end=HOST_PAGE + PAGE - 1)
    uc.hook_add(UC_HOOK_MEM_INVALID, on_bad)
    uc.reg_write(UC_ARM64_REG_SP, STACK_TOP - 0x100)
    uc.reg_write(UC_ARM64_REG_X0, HOST_PAGE)
    uc.reg_write(UC_ARM64_REG_LR, RETURN_ADDR)
    uc.reg_write(UC_ARM64_REG_TPIDR_EL0, 0xDEAD0000)
    try:
        uc.emu_start(base, RETURN_ADDR, count=50_000_000)
    except UcError as e:
        raise SystemExit(f"emulation fault at pc={uc.reg_read(UC_ARM64_REG_PC):#x}: {e}")
    rc = st.get("exit", uc.reg_read(UC_ARM64_REG_X0))
    tp_after = uc.reg_read(UC_ARM64_REG_TPIDR_EL0)
    return base, rc, st, tp_after


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("nso")
    ap.add_argument("--expect", default="fnvnx guest: OK")
    a = ap.parse_args(argv)
    base, rc, st, tp = run(a.nso)
    out = st["console"].decode(errors="replace")
    print(f"== guest at {base:#010x}: rc={rc:#x} host_calls={st['calls']} tpidr_el0 restored={tp == 0xDEAD0000}")
    for line in st["debug"]:
        print(f"   [debug] {line}")
    print("   " + out.rstrip().replace("\n", "\n   "))
    ok = rc == 0 and a.expect in out and tp == 0xDEAD0000
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
