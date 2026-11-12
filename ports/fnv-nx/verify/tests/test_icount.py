#!/usr/bin/env python3
"""Tests of verify/icount.py: exact executed-instruction counters. Plain script, exit 0 on pass, one line per case.

A tiny A64 program (two address ranges, loops, a function called N times, ldar/stlr/ldaxr/stlxr/dmb/dsb/isb) runs in a
bare Unicorn instance with two fake threads and two marks; the block-hook counters are compared with a reference taken
by a UC_HOOK_CODE single-step run of the same schedule (capstone mnemonics decide the classes).
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import icount  # noqa: E402

from capstone import CS_ARCH_ARM64, CS_MODE_ARM, Cs  # noqa: E402
from unicorn import UC_ARCH_ARM64, UC_HOOK_CODE, UC_MODE_ARM, Uc  # noqa: E402
from unicorn.arm64_const import UC_ARM64_REG_CPACR_EL1  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verify"))
import testcase  # noqa: E402
from testcase import case  # noqa: E402

AS, LD, OBJCOPY, NM = ("aarch64-linux-gnu-" + t for t in ("as", "ld", "objcopy", "nm"))
ARCH = ".arch armv8.4-a+rcpc+lse+lor\n"

PROG = ARCH + """
.section .text,"ax"
.globl _start
_start:
    mov x0, #0x30000
    mov x20, #5
l1: bl funcb
    subs x20, x20, #1
    b.ne l1
p1:
    mov x20, #4
l2: ldar w1, [x0]
    stlr w1, [x0]
    ldarb w1, [x0]
    stlrb w1, [x0]
    ldarh w1, [x0]
    stlrh w1, [x0]
    ldaxr w3, [x0]
    stlxr w4, w3, [x0]
    dmb ish
    dmb ishst
    dsb sy
    isb
    subs x20, x20, #1
    b.ne l2
p2:
    bl funcb
    bl funca2
p3:
    mov x20, #3
l3: bl funca2
    bl funcb
    subs x20, x20, #1
    b.ne l3
end:
    nop
funca2:
    add x1, x1, #1
    add x1, x1, #2
    ret
bench:
    movz x20, #50000
l4: add x1, x1, #1
    subs x20, x20, #1
    b.ne l4
benchend:
    nop
.section .textb,"ax"
.globl funcb
funcb:
    ldar w9, [x0]
    dmb ish
    add x2, x2, #1
    ret
.section .data
.space 64
"""

# mnemonic -> classes expected from icount.classify (assembled and decoded by the encoding table)
CLASS_CASES = [
    ("ldar w1,[x0]", "ldar_stlr"), ("ldar x1,[x0]", "ldar_stlr"), ("ldarb w1,[x0]", "ldar_stlr"),
    ("ldarh w1,[x0]", "ldar_stlr"), ("stlr w1,[x0]", "ldar_stlr"), ("stlr x1,[x0]", "ldar_stlr"),
    ("stlrb w1,[x0]", "ldar_stlr"), ("stlrh w1,[x0]", "ldar_stlr"),
    ("ldapr w1,[x0]", "ldar_stlr,ldapr"), ("ldapr x1,[x0]", "ldar_stlr,ldapr"),
    ("ldaprb w1,[x0]", "ldar_stlr,ldapr"), ("ldaprh w1,[x0]", "ldar_stlr,ldapr"),
    ("ldapur w1,[x0]", "ldar_stlr,ldapr"), ("stlur x1,[x0,#8]", "ldar_stlr,ldapr"),
    ("ldapursw x1,[x0,#4]", "ldar_stlr,ldapr"),
    ("ldaxr w1,[x0]", "ldar_stlr,ldaxr_stlxr"), ("ldaxr x1,[x0]", "ldar_stlr,ldaxr_stlxr"),
    ("ldaxrb w1,[x0]", "ldar_stlr,ldaxr_stlxr"), ("stlxr w2,w1,[x0]", "ldar_stlr,ldaxr_stlxr"),
    ("stlxrh w2,w1,[x0]", "ldar_stlr,ldaxr_stlxr"), ("ldaxp x1,x2,[x0]", "ldar_stlr,ldaxr_stlxr"),
    ("stlxp w3,x1,x2,[x0]", "ldar_stlr,ldaxr_stlxr"),
    ("dmb ish", "dmb"), ("dmb sy", "dmb"), ("dmb ishld", "dmb"), ("dmb oshst", "dmb"),
    ("dsb sy", "dsb"), ("dsb ish", "dsb"), ("isb", "isb"),
    # none of the classes
    ("ldr w1,[x0]", ""), ("str x1,[x0]", ""), ("ldxr w1,[x0]", ""), ("stxr w2,w1,[x0]", ""),
    ("ldlar w1,[x0]", ""), ("stllr w1,[x0]", ""), ("casal w1,w2,[x0]", ""), ("swpal w1,w2,[x0]", ""),
    ("ldur w1,[x0,#1]", ""), ("ldp x1,x2,[x0]", ""), ("clrex", ""), ("nop", ""), ("add x1,x2,x3", ""),
    ("ldaddal w1,w2,[x0]", ""),
]


def sh(*cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw).stdout


def build(d):
    s = os.path.join(d, "p.s")
    open(s, "w").write(PROG)
    sh(AS, s, "-o", os.path.join(d, "p.o"))
    elf = os.path.join(d, "p.elf")
    sh(LD, "-N", "--section-start=.text=0x10000", "--section-start=.textb=0x20000", "--section-start=.data=0x30000",
       "-o", elf, os.path.join(d, "p.o"))
    secs = {}
    for name in (".text", ".textb", ".data"):
        b = os.path.join(d, "s.bin")
        sh(OBJCOPY, "-O", "binary", "--only-section=" + name, elf, b)
        secs[name] = open(b, "rb").read()
    syms = {}
    for line in sh(NM, elf).splitlines():
        a, _, n = line.split()
        syms[n] = int(a, 16)
    return elf, secs, syms


def machine(secs):
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    uc.reg_write(UC_ARM64_REG_CPACR_EL1, 3 << 20)
    uc.mem_map(0x10000, 0x30000)
    for addr, name in ((0x10000, ".text"), (0x20000, ".textb"), (0x30000, ".data")):
        uc.mem_write(addr, secs[name])
    return uc


# schedule: (until, thread to run it, mark before it); segments end at block boundaries (after a branch)
def schedule(syms):
    return [(syms["p1"], 1, None), (syms["p2"], 2, None), (syms["p3"], 1, "m1"), (syms["end"], 2, "m2")]


def spec_for(syms):
    return {"ranges": {"A": [[0x10000, 0x11000]], "B": [[0x20000, 0x21000]]},
            "entries": {"funcb": [syms["funcb"]], "funca2": [syms["funca2"]]}}


def drive(uc, syms, ic=None, extra_hook=None):
    pc = syms["_start"]
    for until, tid, mark in schedule(syms):
        if mark:
            (ic.mark(mark) if ic else None)
            if extra_hook:
                extra_hook("mark", mark)
        if ic:
            ic.thread(tid)
        if extra_hook:
            extra_hook("thread", tid)
        uc.emu_start(pc, until)
        pc = until
    if ic:
        ic.stop("test end")
    if extra_hook:
        extra_hook("stop", None)


def run_icount(secs, syms, out):
    uc = machine(secs)
    ic = icount.attach(uc, 0x10000, 0x22000, spec_for(syms), bias=0)
    drive(uc, syms, ic)
    ic.write(out)
    return ic


def reference(secs, syms):
    """per (interval, thread) counters by single-stepping every instruction"""
    uc = machine(secs)
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    spec = spec_for(syms)
    state = {"iv": 0, "tid": 1}
    acc = {}
    names = ["total", "ldar_stlr", "ldapr", "ldaxr_stlxr", "dmb", "dsb", "isb", "A", "B", "funcb", "funca2"]

    def code(uc, addr, size, _):
        c = acc.setdefault((state["iv"], state["tid"]), dict.fromkeys(names, 0))
        mn = next(md.disasm(bytes(uc.mem_read(addr, 4)), addr)).mnemonic
        c["total"] += 1
        if mn.startswith(("ldapr", "ldapur", "stlur")):
            c["ldapr"] += 1
        if mn.startswith(("ldaxr", "ldaxp", "stlxr", "stlxp")):
            c["ldaxr_stlxr"] += 1
        if mn.startswith(("ldar", "stlr")) or mn.startswith(("ldapr", "ldapur", "stlur", "ldaxr", "ldaxp", "stlxr", "stlxp")):
            c["ldar_stlr"] += 1
        if mn in ("dmb", "dsb", "isb"):
            c[mn] += 1
        for r in ("A", "B"):
            if any(lo <= addr < hi for lo, hi in spec["ranges"][r]):
                c[r] += 1

    def ev(kind, v):
        if kind == "mark":
            state["iv"] += 1
        elif kind == "thread":
            state["tid"] = v
    uc.hook_add(UC_HOOK_CODE, code, begin=0x10000, end=0x21fff)
    drive(uc, syms, None, ev)
    return acc


def flat(rep):
    """OUT.json -> {(interval, tid): {class: n}} in the reference's names"""
    out = {}
    for i, iv in enumerate(rep["intervals"]):
        for tid, c in iv["per_thread"].items():
            d = {k: c[k] for k in ("total", "ldar_stlr", "ldapr", "ldaxr_stlxr", "dmb", "dsb", "isb")}
            d.update(c["ranges"])
            d.update(c["entries"])
            out[(i, int(tid))] = d
    return out


results = []




def main():
    with tempfile.TemporaryDirectory() as d:
        elf, secs, syms = build(d)
        out1, out2 = os.path.join(d, "o1.json"), os.path.join(d, "o2.json")
        run_icount(secs, syms, out1)
        run_icount(secs, syms, out2)
        rep = json.load(open(out1))
        ref = reference(secs, syms)
        got = flat(rep)

        # (1) total
        bad = {k: (got.get(k, {}).get("total"), ref[k]["total"]) for k in ref if got.get(k, {}).get("total") != ref[k]["total"]}
        case("1 block-based total == single-step reference (per interval and thread)",
             not bad and {k for k, v in got.items() if v["total"]} == set(ref) and sum(v["total"] for v in got.values()) > 50, str(bad))

        # (2) opcode classes: reference per interval/thread, analytic totals, and the encoding table by assembling
        cls = ("ldar_stlr", "ldapr", "ldaxr_stlxr", "dmb", "dsb", "isb")
        bad = {(k, c): (got[k][c], ref[k][c]) for k in ref for c in cls if got[k][c] != ref[k][c]}
        tot = rep["totals"]["all"]
        want = {"ldar_stlr": 4 * 8 + 9, "ldapr": 0, "ldaxr_stlxr": 4 * 2, "dmb": 4 * 2 + 9, "dsb": 4, "isb": 4}
        bad2 = {c: (tot[c], want[c]) for c in cls if tot[c] != want[c]}
        words = assemble_cases(d)
        bad3 = [(m, icount.classify(w), e) for (m, e), w in zip(CLASS_CASES, words)
                if ",".join(icount.classify(w)) != e]
        case("2 opcode class counts == reference == analytic; encoding masks vs assembled mnemonics",
             not bad and not bad2 and not bad3, f"{bad} {bad2} {bad3}")

        # (3) ranges and entries
        calls_b, calls_a2 = 5 + 1 + 3, 1 + 3
        bad = {k: (got[k][r], ref[k][r]) for k in ref for r in ("A", "B") if got[k][r] != ref[k][r]}
        entries_ok = tot["entries"] == {"funca2": calls_a2, "funcb": calls_b}
        b_ok = tot["ranges"]["B"] == 4 * calls_b and tot["ranges"]["A"] + tot["ranges"]["B"] == tot["total"]
        case("3 range class and entry counts == analytic / reference", not bad and entries_ok and b_ok,
             f"{bad} {tot['entries']} {tot['ranges']}")

        # (4) sums
        ok = True
        keys = cls + ("total",)
        for c in keys:
            ok &= sum(v[c] for v in got.values()) == tot[c]
            for t, th in rep["totals"].items():
                if t != "all":
                    ok &= sum(v[c] for (i, tid), v in got.items() if str(tid) == t) == th[c]
        ok &= [iv["from"] + ">" + iv["to"] for iv in rep["intervals"]] == ["start>m1", "m1>m2", "m2>stop"]
        ok &= rep["threads"] == {"1": "main", "2": "t2"} and rep["format"] == "fnvnx-icount/1"
        ok &= all(iv["out_of_order"] is False for iv in rep["intervals"])
        case("4 per-thread and per-interval sums add up to the totals", ok)

        # (5) determinism
        case("5 two runs give byte-identical OUT.json", open(out1, "rb").read() == open(out2, "rb").read())

        # (6) spec subcommand
        case("6 spec resolves a section and symbols, errors on a missing one", spec_cases(d, elf, syms))

        bench(secs, syms)
    return 0 if all(results) else 1


def assemble_cases(d):
    s = os.path.join(d, "c.s")
    open(s, "w").write(ARCH + "".join(f"{m}\n" for m, _ in CLASS_CASES))
    sh(AS, s, "-o", os.path.join(d, "c.o"))
    sh(OBJCOPY, "-O", "binary", "--only-section=.text", os.path.join(d, "c.o"), os.path.join(d, "c.bin"))
    b = open(os.path.join(d, "c.bin"), "rb").read()
    assert len(b) == 4 * len(CLASS_CASES)
    return [int.from_bytes(b[i:i + 4], "little") for i in range(0, len(b), 4)]


def spec_cases(d, elf, syms):
    ic = os.path.join(VERIFY, "icount.py")
    nf = os.path.join(d, "names.txt")
    open(nf, "w").write("funcb\nfunca2\n")
    out = os.path.join(d, "spec.json")
    base = [sys.executable, "-I", ic, "spec", "--elf", elf]
    r = subprocess.run(base + ["--range", "B=section:.textb", "--range", "F=symrange:funca2,bench",
                               "--entries", "fn=symfile:" + nf, "-o", out], capture_output=True, text=True)
    if r.returncode != 0:
        return False
    sp = json.load(open(out))
    ok = sp["ranges"]["B"] == [[0x20000, 0x20000 + len(secs_b(d, elf))]]
    ok &= sp["ranges"]["F"] == [[syms["funca2"], syms["bench"]]]
    ok &= sp["entries"]["fn"] == sorted([syms["funcb"], syms["funca2"]])
    open(nf, "w").write("funcb\nnope\n")
    r1 = subprocess.run(base + ["--entries", "fn=symfile:" + nf, "-o", out + "1"], capture_output=True, text=True)
    r2 = subprocess.run(base + ["--range", "X=section:.nosuch", "-o", out + "2"], capture_output=True, text=True)
    r3 = subprocess.run(base + ["--range", "X=symrange:funca2,nope", "-o", out + "3"], capture_output=True, text=True)
    return ok and r1.returncode == 1 and r2.returncode == 1 and r3.returncode == 1 and not os.path.exists(out + "1")


def secs_b(d, elf):
    b = os.path.join(d, "sb.bin")
    sh(OBJCOPY, "-O", "binary", "--only-section=.textb", elf, b)
    return open(b, "rb").read()


def bench(secs, syms):
    def timed(with_ic):
        uc = machine(secs)
        uc.reg_write(__import__("unicorn.arm64_const", fromlist=["x"]).UC_ARM64_REG_X0, 0x30000)
        if with_ic:
            icount.attach(uc, 0x10000, 0x22000, spec_for(syms))
        t = time.perf_counter()
        uc.emu_start(syms["bench"], syms["benchend"])
        return time.perf_counter() - t
    a, b = min(timed(False) for _ in range(3)), min(timed(True) for _ in range(3))
    print(f"info slowdown on the 3-instruction loop (50000 blocks): {b / a:.1f}x ({a * 1e3:.1f} ms -> {b * 1e3:.1f} ms)")


if __name__ == "__main__":
    sys.exit(main())
