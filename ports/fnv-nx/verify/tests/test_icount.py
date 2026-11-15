#!/usr/bin/env python3
"""Tests of verify/icount.py: exact executed-instruction counters. Plain script, exit 0 on pass, one line per case.

The fixture is the guest image of the recipe root verify/tests/icount (icfix.s: two address ranges, loops, a function called N times, ldar/stlr/ldaxr/stlxr/dmb/dsb/isb;
icenc.S: one instruction per opcode-class row with its expected class mask as table data), built and linked by verify/recipe.py through gate_build.ensure in GATE_CACHE
like every guest image, and loaded by the boot machine's own loader (bootmachine.Machine). Its program runs with two fake threads and two marks; the block-hook
counters are compared with a reference taken by a UC_HOOK_CODE single-step run of the same schedule (capstone mnemonics decide the classes). Every address, range, entry
and expected class is read from the linked image (the link record elffacts.json of the build, bootfacts.py); no address is typed here, and no assembler, linker
or binutils tool runs in this test.
"""
import json
import os
import struct
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
sys.path.insert(0, HERE)
import bootfacts  # noqa: E402
import boothost  # noqa: E402
import bootmachine  # noqa: E402
import icount  # noqa: E402
import test_bootmachine as tb  # noqa: E402

from capstone import CS_ARCH_ARM64, CS_MODE_ARM, Cs  # noqa: E402
from unicorn import UC_HOOK_CODE  # noqa: E402

FIX_ROOT = "ports/fnv-nx/verify/tests/icount"   # the recipe root of the fixture (admit.json, class test, target guest)
QUANTUM = 10 ** 12   # one slice for the whole program: the counters are compared, the scheduling is not under test
TMPD = tempfile.mkdtemp(prefix="icount-")


class Fix:
    """the fixture image: raw ELF bytes, path, link-time symbols (icfx_ prefix left off), the plan's load base"""

    def __init__(self):
        import gate_build
        res, _out = gate_build.ensure(FIX_ROOT, tb.GATE_CACHE, tb.REPO)
        if res["status"] != "green" or "elf_facts" not in res:
            raise RuntimeError("icount fixture: recipe %s, link record %s: %s" % (res["status"], "elf_facts" in res, res.get("failure")))
        self.path = res["elf"]["path"]
        self.facts = res["elf_facts"]["path"]
        self.rec = bootfacts.load(self.facts)
        self.raw = open(self.path, "rb").read()
        self.secs = bootfacts.sections_by_name(self.rec)
        self.syms = {}
        for n, vs in bootfacts.symbol_values(self.rec).items():
            if n.startswith(("icfx_", "icenc_")):
                if len(vs) != 1:
                    raise RuntimeError("symbol %s is defined %d times" % (n, len(vs)))
                self.syms[n[5:] if n.startswith("icfx_") else n] = vs[0]
        self.plan = tb.make_plan(self.raw)
        self.base = self.plan["image"]["base"]
        self.abs = {n: v + self.base for n, v in self.syms.items()}

    def machine(self):
        """a boot machine with the image loaded by its own loader; nothing is run"""
        return bootmachine.Machine(self.raw, self.plan, boothost.Services(TMPD), quantum=QUANTUM)


# schedule: (until, thread to run it, mark before it); segments end at block boundaries (after a branch)
def schedule(A):
    return [(A["p1"], 1, None), (A["p2"], 2, None), (A["p3"], 1, "m1"), (A["end"], 2, "m2")]


def spec_for(S):
    """SPEC.json of the fixture from its link-time symbols: range A is the code before funcb, B is funcb"""
    return {"ranges": {"A": [[S["start"], S["funcb"]]], "B": [[S["funcb"], S["funcb_end"]]]},
            "entries": {"funcb": [S["funcb"]], "funca2": [S["funca2"]]}}


def drive(uc, A, ic=None, extra_hook=None):
    pc = A["start"]
    for until, tid, mark in schedule(A):
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


def run_icount(fx, out):
    m = fx.machine()
    ic = m.attach_icount(spec_for(fx.syms), fx.base)
    drive(m.uc, fx.abs, ic)
    ic.write(out)
    return ic


def reference(fx):
    """per (interval, thread) counters by single-stepping every instruction"""
    uc = fx.machine().uc
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    spec = spec_for(fx.syms)
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
            if any(lo + fx.base <= addr < hi + fx.base for lo, hi in spec["ranges"][r]):
                c[r] += 1
        for e in ("funcb", "funca2"):
            if addr == fx.abs[e]:
                c[e] += 1

    def ev(kind, v):
        if kind == "mark":
            state["iv"] += 1
        elif kind == "thread":
            state["tid"] = v
    uc.hook_add(UC_HOOK_CODE, code, begin=fx.abs["start"], end=fx.abs["funcb_end"] - 1)
    drive(uc, fx.abs, None, ev)
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


def case(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (" - " + detail if detail and not ok else ""))


def frame_cases():
    """Icount.mark_frame / frames() on a synthetic run (no emulator: the block hook's work is c[0] += n on the current counters)"""
    ic = icount.Icount()
    def run(n):
        ic.cur[0] += n
    ic.thread(1)
    run(100)                               # start .. m1 (a milestone mark inside frame 1)
    ic.mark("m1")
    ic.thread(2, "worker")
    run(30)
    ic.thread(1)
    run(7)
    ic.mark_frame(1, 1000, {"sha256": "aa"})   # frame 1 = everything since the start of the run
    run(11)
    ic.thread(2)
    run(5)
    ic.thread(3)                           # a thread without a name given: t3; it runs in frame 2 only
    run(2)
    ic.mark("m2")                          # a milestone inside frame 2 does not cut it
    ic.thread(1)
    run(1)
    ic.mark_frame(2, 2500)
    ic.thread(1)
    ic.mark_frame(3, 2600)                 # an empty frame: no instruction at all
    run(4)
    ic.stop("limit")
    ic.mark_frame(4, 9999)                 # after the stop: no record
    fr = ic.frames()
    case("frames: n, t_ns, extra and per-thread instructions of frame 1 (start of the run up to its present, a milestone inside it does not cut it)",
         fr[0] == {"n": 1, "t_ns": 1000, "per_tid": {"main": 107, "worker": 30}, "sha256": "aa"}, str(fr[0]))
    case("frames: frame 2 starts after frame 1, threads named as the report names them", fr[1] == {"n": 2, "t_ns": 2500, "per_tid": {"main": 12, "worker": 5, "t3": 2}}, str(fr[1]))
    case("frames: an empty frame is a record with an empty per_tid; nothing after stop", fr[2] == {"n": 3, "t_ns": 2600, "per_tid": {}} and len(fr) == 3, str(fr))
    rep = ic.report()
    case("frames: a frame is not an icount interval (the intervals are the milestones' only), and the frames add up to the totals but what follows the last frame",
         [iv["to"] for iv in rep["intervals"]] == ["m1", "m2", "stop"]
         and sum(sum(f["per_tid"].values()) for f in fr) + 4 == rep["totals"]["all"]["total"], str(rep["intervals"]))
    case("frames: a run without a present has no records", icount.Icount().frames() == [])
    # the record grows linearly and a mark is O(1): 20000 frames of 3 threads, the cost per frame does not grow with the frame count
    import time
    def many(k):
        x = icount.Icount()
        t0 = time.time()
        for i in range(1, k + 1):
            for t in (1, 2, 3):
                x.thread(t)
                x.cur[0] += 1000
            if i % 100 == 0:
                x.mark("m%d" % i)
            x.mark_frame(i, i * 16666667)
        t1 = time.time()
        out = x.frames()
        return t1 - t0, len(json.dumps(x.report()) + json.dumps(out)), out
    a, ba, oa = many(2000)
    b, bb, ob = many(20000)
    case("frames: 20000 frames x 3 threads: every frame holds 3000 instructions, size per frame stays within 5 % of the 2000-frame run (linear), time per frame within 3x",
         all(f["per_tid"] == {"main": 1000, "t2": 1000, "t3": 1000} for f in ob) and len(ob) == 20000
         and abs(bb / 20000 - ba / 2000) < 0.05 * ba / 2000 and b / 20000 < 3 * a / 2000 + 2e-5, "%.3fs/%.3fs %d/%d bytes" % (a, b, ba, bb))
    print("info frames: %.0f bytes per frame in OUT.json (frames + report, a milestone every 100 frames), %.1f us per frame" % (bb / 20000, 1e6 * b / 20000))


def main():
    frame_cases()
    fx = Fix()
    with tempfile.TemporaryDirectory() as d:
        out1, out2 = os.path.join(d, "o1.json"), os.path.join(d, "o2.json")
        run_icount(fx, out1)
        run_icount(fx, out2)
        rep = json.load(open(out1))
        ref = reference(fx)
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
        rows = encoding_rows(fx)
        bad3 = [(hex(a), icount.classify(w), icount.OPCODE_NAMES) for a, w, mask in rows
                if sum(icount.CLASS_BITS[n] for n in icount.classify(w)) != mask]
        if len(rows) < 30:
            bad3.append(("table", len(rows)))
        case("2 opcode class counts == reference == analytic; encoding masks vs assembled mnemonics",
             not bad and not bad2 and not bad3, f"{bad} {bad2} {bad3}")

        # (3) ranges and entries
        calls_b, calls_a2 = 5 + 1 + 3, 1 + 3   # the call counts of the program text in icfix.s
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
        case("6 spec resolves a section and symbols, errors on a missing one", spec_cases(d, fx))

        bench(fx)
    return 0 if all(results) else 1


def encoding_rows(fx):
    """[(instruction address, instruction word, expected class mask)] of the image's table icenc_table: {offset from icenc_begin, mask} per row"""
    t = bootfacts.symbol_bytes(fx.rec, fx.raw, "icenc_table")
    if len(t) % 8 or len(t) != fx.syms["icenc_table_end"] - fx.syms["icenc_table"]:
        raise RuntimeError("icenc_table is %d bytes" % len(t))
    uc = fx.machine().uc
    out = []
    for i in range(0, len(t), 8):
        off, mask = struct.unpack_from("<II", t, i)
        a = fx.abs["icenc_begin"] + off
        out.append((a, int.from_bytes(bytes(uc.mem_read(a, 4)), "little"), mask))
    if fx.abs["icenc_begin"] + 4 * len(out) != fx.abs["icenc_end"]:
        raise RuntimeError("icenc rows are not the whole of icenc_begin..icenc_end")
    return out


def spec_cases(d, fx):
    ic = os.path.join(VERIFY, "icount.py")
    S = fx.syms
    nf = os.path.join(d, "names.txt")
    open(nf, "w").write("icfx_funcb\nicfx_funca2\n")
    out = os.path.join(d, "spec.json")
    base = [sys.executable, "-I", ic, "spec", "--facts", fx.facts]
    r = subprocess.run(base + ["--range", "T=section:.text", "--range", "F=symrange:icfx_funca2,icfx_bench",
                               "--entries", "fn=symfile:" + nf, "-o", out], capture_output=True, text=True)
    if r.returncode != 0:
        return False
    sp = json.load(open(out))
    ok = sp["ranges"]["T"] == [[a, a + n] for a, n in fx.secs[".text"]]
    ok &= sp["ranges"]["F"] == [[S["funca2"], S["bench"]]]
    ok &= sp["entries"]["fn"] == sorted([S["funcb"], S["funca2"]])
    open(nf, "w").write("icfx_funcb\nnope\n")
    r1 = subprocess.run(base + ["--entries", "fn=symfile:" + nf, "-o", out + "1"], capture_output=True, text=True)
    r2 = subprocess.run(base + ["--range", "X=section:.nosuch", "-o", out + "2"], capture_output=True, text=True)
    r3 = subprocess.run(base + ["--range", "X=symrange:icfx_funca2,nope", "-o", out + "3"], capture_output=True, text=True)
    return ok and r1.returncode == 1 and r2.returncode == 1 and r3.returncode == 1 and not os.path.exists(out + "1")


def bench(fx):
    def timed(with_ic):
        m = fx.machine()
        if with_ic:
            m.attach_icount(spec_for(fx.syms), fx.base)
        t = time.perf_counter()
        m.uc.emu_start(fx.abs["bench"], fx.abs["benchend"])
        return time.perf_counter() - t
    a, b = min(timed(False) for _ in range(3)), min(timed(True) for _ in range(3))
    print(f"info slowdown on the 3-instruction loop (50000 blocks): {b / a:.1f}x ({a * 1e3:.1f} ms -> {b * 1e3:.1f} ms)")


if __name__ == "__main__":
    sys.exit(main())
