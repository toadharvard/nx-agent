#!/usr/bin/env python3
"""test_s5_milestone_store.py - V1 (D14) against S5's boot milestones (J-0135/J-0147): "first-cell is reached when the
player's TESObjectREFR::parentCell (+0x40) gets a non-zero word" (tests/s5/milestones.txt `store *0x011dea3c+0x40`).

Two halves:
  L1  the offset under ILP32: the decomp's own TESObjectREFR declaration (include/TESObjectREFR.h, read in place from
      the pinned checkout, never copied) compiled with aarch64-linux-gnu-g++ -mabi=ilp32: offsetof(m_pOwningCell) and
      the bases it rests on (TESForm 0x18, TESChildCell +0x18, PlacedObjectInfo +0x20). Skipped without the decomp
      checkout or the cross compiler.
  W*  the store watch of tests/s5/emu_guest.py under Unicorn, with the store shapes AArch64 code produces for a 32-bit
      field (synthetic code and addresses; the object at 0x20000000, the pointer at 0x01100010):
        W1 str w, [x, #0x40]                       the plain store (S5's own test)
        W2 str x, [x, #0x3c]                       a 64-bit store whose upper word is parentCell (store merging)
        W3 stp w, w, [x, #0x3c]                    a pair store, parentCell second
        W4 stp x, x, [x, #0x38]                    a pair of 64-bit words, parentCell in the second's low word
        W5 stur q, [x, #0x38]                      a 128-bit store (struct copy), parentCell in its third word
        W6 str x, [A-4]                            the player pointer itself stored as the upper word of a 64-bit store
        W7 the pointer moves P1 -> P2, then a store to P1+0x40 (P1 freed and reused): must not count
        W9-W10 the field set by stlr, by ldaxr/stlxr (S12: volatiles, Interlocked*; armv8.0, no LSE): must count
        W13 the pointer stored by stlr: must count
        W8 a non-zero store to +0x44 (the next field): must not count
  python3 -I tests/v1/test_s5_milestone_store.py   -> one line per case, exit 1 when any shows a defect
"""
from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.dirname(os.path.dirname(HERE))
S5 = os.path.join(PORT, "tests", "s5")
DECOMP = os.environ.get("FNVNX_DECOMP", "/home/user/ext/fnv_decomp")
CXX = os.environ.get("V1_CXX", "aarch64-linux-gnu-g++")
AS = os.environ.get("V1_AS", "aarch64-linux-gnu-as")
OBJCOPY = os.environ.get("V1_OBJCOPY", "aarch64-linux-gnu-objcopy")

A, OFF, OBJ, OBJ2 = 0x01100010, 0x40, 0x20000000, 0x20000800
CELL = 0x2000_0C00
fails = 0


def say(case, ok, text):
    global fails
    print(f"{case}: {'ok' if ok else 'DEFECT'} - {text}")
    fails += 0 if ok else 1


def layout(work):
    hdr = os.path.join(DECOMP, "include", "TESObjectREFR.h")
    if not os.path.isfile(hdr) or not shutil.which(CXX):
        print("L1: skip (no decomp checkout or no cross g++)")
        return
    src = os.path.join(work, "probe.cpp")
    with open(src, "w") as f:
        f.write('#include "TESObjectREFR.h"\nextern "C" {\n'
                "int v1_cell = __builtin_offsetof(TESObjectREFR, m_pOwningCell);\n"
                "int v1_child = __builtin_offsetof(TESObjectREFR, tESChildCellComponent);\n"
                "int v1_data = __builtin_offsetof(TESObjectREFR, data);\n"
                "int v1_form = sizeof(TESForm);\n}\n")
    out = os.path.join(work, "probe.s")
    r = subprocess.run([CXX, "-mabi=ilp32", "-fpermissive", "-w", "-I", os.path.join(DECOMP, "include"), "-S",
                        "-o", out, src], capture_output=True, text=True)
    if r.returncode:
        say("L1", False, "the probe does not compile: " + r.stderr.strip().splitlines()[-1][:160])
        return
    got = dict(re.findall(r"^(v1_\w+):\n\s+\.word\s+(\d+)", open(out).read(), re.M))
    got = {k: int(v) for k, v in got.items()}
    want = {"v1_cell": 0x40, "v1_child": 0x18, "v1_data": 0x20, "v1_form": 0x18}
    say("L1", got == want, "ILP32 offsets " + ", ".join(f"{k[3:]}=0x{got.get(k, -1):x}" for k in want)
        + " (x86 MSVC: cell 0x40, child 0x18, data 0x20, TESForm 0x18)")


def assemble(work, text):
    s, o, b = (os.path.join(work, n) for n in ("c.s", "c.o", "c.bin"))
    with open(s, "w") as f:
        f.write(".text\n" + text + "\nnop\n")
    subprocess.run([AS, "-o", o, s], check=True)
    subprocess.run([OBJCOPY, "-O", "binary", "-j", ".text", o, b], check=True)
    return open(b, "rb").read()


def run_case(work, asm, regs, vregs=None):
    sys.path.insert(0, S5)
    import emu_guest
    from unicorn import UC_ARCH_ARM64, UC_MODE_ARM, Uc
    from unicorn import arm64_const as C
    e = emu_guest.Emu.__new__(emu_guest.Emu)
    e.uc = uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    e.calls, e.cur, e.code_syms, e.miles = 0, None, {}, {}
    e.sym = lambda a: f"0x{a:x} 0x{a:x}"
    uc.mem_map(0x1000, 0x1000)
    uc.mem_map(0x01100000, 0x1000)
    uc.mem_map(0x20000000, 0x1000)
    uc.reg_write(C.UC_ARM64_REG_CPACR_EL1, 3 << 20)   # FP/SIMD on (q-register stores)
    code = assemble(work, asm)
    uc.mem_write(0x1000, code)
    m = os.path.join(work, "m.txt")
    with open(m, "w") as f:
        f.write(f"first-cell store *0x{A:08x}+0x{OFF:x}\n")
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        e.install_milestones(m)
        for r, v in regs.items():
            uc.reg_write(getattr(C, f"UC_ARM64_REG_{r.upper()}"), v)
        for r, v in (vregs or {}).items():
            uc.reg_write(getattr(C, f"UC_ARM64_REG_{r.upper()}"), v)
        uc.emu_start(0x1000, 0x1000 + len(code) - 4)
    return "first-cell" in e.miles


def watch(work):
    pre = "str w1, [x0]\n"   # the player pointer: x0 = A, x1 = the object
    base = {"x0": A, "x1": OBJ, "x2": OBJ}
    cases = [
        ("W1", "str w3, [x2, #0x40]", {"x3": CELL}, None, True),
        ("W2", "str x3, [x2, #0x3c]", {"x3": CELL << 32}, None, True),
        ("W3", "stp w4, w3, [x2, #0x3c]", {"x3": CELL, "x4": 0}, None, True),
        ("W4", "stp x4, x3, [x2, #0x38]", {"x3": CELL, "x4": 0}, None, True),
        ("W5", "stur q0, [x2, #0x38]", {}, {"q0": CELL << 64}, True),
    ]
    for case, asm, regs, vregs, want in cases:
        try:
            got = run_case(work, pre + asm, {**base, **regs}, vregs)
        except Exception as ex:  # noqa: BLE001 - a crash of the harness is a finding too
            say(case, False, f"{asm}: the watch raised {type(ex).__name__}: {ex}")
            continue
        say(case, got == want, f"{asm.strip()}: first-cell {'reached' if got else 'NOT reached'}"
            + ("" if got == want else " although parentCell became non-zero"))
    # W6: the pointer at A arrives as the upper word of a 64-bit store at A-4
    got = run_case(work, "str x1, [x0, #-4]\nstr w3, [x2, #0x40]", {"x0": A, "x1": OBJ << 32, "x2": OBJ, "x3": CELL})
    say("W6", got, "str x, [A-4] (pointer in the upper word), then str w, [P+0x40]: first-cell "
        + ("reached" if got else "NOT reached: the pointer store is not seen, the object is never watched"))
    # W7: P1 -> P2; a store to the old P1+0x40 must not count
    got = run_case(work, "str w1, [x0]\nstr w5, [x0]\nstr w3, [x2, #0x40]",
                   {"x0": A, "x1": OBJ, "x5": OBJ2, "x2": OBJ, "x3": CELL})
    say("W7", not got, "pointer P1 then P2, a store to P1+0x40: first-cell "
        + ("reached on the stale object" if got else "not reached"))
    # W9-W10: ordered and exclusive stores. S12's plugin compiles volatiles to stlr and Interlocked* to ldaxr/stlxr
    # (the guest is -march=armv8-a -mno-outline-atomics: no LSE swp/cas on the A57); Unicorn must report them as
    # writes, or a field set that way is never seen.
    atomics = [
        ("W9", "stlr w3, [x4]", {"x3": CELL, "x4": OBJ + OFF}),
        ("W10", "1: ldaxr w5, [x4]\nstlxr w6, w3, [x4]\ncbnz w6, 1b", {"x3": CELL, "x4": OBJ + OFF}),
    ]
    for case, asm, regs in atomics:
        try:
            got = run_case(work, pre + asm, {**base, **regs})
        except Exception as ex:  # noqa: BLE001
            say(case, False, f"{asm}: the watch raised {type(ex).__name__}: {ex}")
            continue
        say(case, got, asm.replace("\n", "; ") + ": first-cell "
            + ("reached" if got else "NOT reached: the store is not reported to the write hook"))
    # W13: the pointer itself stored with stlr (a volatile global under S12's plugin)
    got = run_case(work, "stlr w1, [x0]\nstr w3, [x2, #0x40]", {"x0": A, "x1": OBJ, "x2": OBJ, "x3": CELL})
    say("W13", got, "stlr pointer, then str w, [P+0x40]: first-cell " + ("reached" if got else "NOT reached"))
    # W8: the next field
    got = run_case(work, pre + "str w3, [x2, #0x44]", {**base, "x3": CELL})
    say("W8", not got, "str w, [P+0x44]: first-cell " + ("reached on the wrong field" if got else "not reached"))


def main():
    work = tempfile.mkdtemp(prefix="v1-s5mile-")   # raises when it cannot: never a relative or empty path
    try:
        layout(work)
        if not shutil.which(AS):
            print("W*: skip (no aarch64 assembler)")
        else:
            watch(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
