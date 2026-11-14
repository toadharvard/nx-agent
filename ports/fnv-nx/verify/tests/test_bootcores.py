#!/usr/bin/env python3
"""Tests of the N-core machine (verify/bootmachine.py rule 3): per-core rounds of `quantum` instructions, priority within a core, blocked
threads freeing a core, determinism, quantum independence for a race-free guest, a thread on each core.

The guests are small AArch64 programs encoded here word by word (no compiler, so the test runs wherever unicorn does): the ELF skeleton is
test_bootlayout.make_elf (crt0 header, the three PT_LOADs), the code sits at the entry 0x1040, globals at 0x12400 (zero data of the RW
segment). They are inputs of the machine, not stand-ins of any product file; the host calls go through the real FnvnxHostApi table and
boothost.Services. N and the core set come from placement.h FNVNX_APP_CORES, read by name (bootmachine.APP_CORES).
Plain script: exit 0 on pass, one line per case."""
import os
import struct
import sys
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(os.path.dirname(VERIFY)))
sys.path.insert(0, VERIFY)
sys.path.insert(0, HERE)
import bootlayout  # noqa: E402
import boothost  # noqa: E402
import bootmachine  # noqa: E402
import test_bootlayout as tbl  # noqa: E402

APP_CORES = bootmachine.APP_CORES
FAILS = []
DATA = 0x12400               # globals: image-relative
GAPI, CTR, CPU, LOGN, LOG, FLAG = DATA, DATA + 0x08, DATA + 0x28, DATA + 0x48, DATA + 0x80, DATA + 0x40
SLOT = {n: 0x30 + 8 * i for i, n in enumerate(bootmachine.SLOTS)}


def case(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(detail)))
    if not ok:
        FAILS.append(name)


class Asm:
    """just enough AArch64: the words of the instructions used below, labels resolved at the end"""
    def __init__(self):
        self.w, self.lab, self.fix = [], {}, []

    def pc(self):
        return 0x1040 + 4 * len(self.w)

    def label(self, n):
        self.lab[n] = self.pc()

    def emit(self, w):
        self.w.append(w & 0xFFFFFFFF)

    def ref(self, kind, n, w):
        self.fix.append((len(self.w), kind, n))
        self.emit(w)

    def movz(self, rd, v, sh=0):
        self.emit(0xD2800000 | sh // 16 << 21 | v << 5 | rd)

    def movk(self, rd, v, sh):
        self.emit(0xF2800000 | sh // 16 << 21 | v << 5 | rd)

    def movn(self, rd, v):
        self.emit(0x92800000 | v << 5 | rd)

    def imm(self, rd, v):
        """rd = the 64-bit constant v (non-negative, or -1..-16)"""
        if v < 0:
            self.movn(rd, -v - 1)
            return
        self.movz(rd, v & 0xFFFF)
        for sh in (16, 32, 48):
            if v >> sh & 0xFFFF:
                self.movk(rd, v >> sh & 0xFFFF, sh)

    def mov(self, rd, rm):
        self.emit(0xAA0003E0 | rm << 16 | rd)

    def addi(self, rd, rn, v):
        self.emit(0x91000000 | v << 10 | rn << 5 | rd)

    def subsi(self, rd, rn, v):
        self.emit(0xF1000000 | v << 10 | rn << 5 | rd)

    def ldr(self, rt, rn, off=0):
        self.emit(0xF9400000 | off // 8 << 10 | rn << 5 | rt)

    def str(self, rt, rn, off=0):
        self.emit(0xF9000000 | off // 8 << 10 | rn << 5 | rt)

    def add_lsl3(self, rd, rn, rm):
        self.emit(0x8B000000 | rm << 16 | 3 << 10 | rn << 5 | rd)

    def adr_abs(self, rd, va):
        """rd = the guest address of the image-relative va (position independent: adr)"""
        off = va - self.pc()
        self.emit(0x10000000 | (off & 3) << 29 | (off >> 2 & 0x7FFFF) << 5 | rd)

    def adr(self, rd, n):
        self.ref("adr", n, 0x10000000 | rd)

    def blr(self, rn):
        self.emit(0xD63F0000 | rn << 5)

    def ret(self):
        self.emit(0xD65F03C0)

    def cbnz(self, rt, n):
        self.ref("cbnz", n, 0xB5000000 | rt)

    def b(self, n):
        self.ref("b", n, 0x14000000)

    def bytes(self):
        for i, kind, n in self.fix:
            pc, tgt = 0x1040 + 4 * i, self.lab[n]
            off = tgt - pc
            if kind == "adr":
                self.w[i] |= (off & 3) << 29 | (off >> 2 & 0x7FFFF) << 5
            elif kind == "cbnz":
                self.w[i] |= (off >> 2 & 0x7FFFF) << 5
            else:
                self.w[i] |= off >> 2 & 0x3FFFFFF
        return struct.pack("<%dI" % len(self.w), *self.w)

    # -- the guest's conventions: x19 = the api table
    def hcall(self, name, *args):
        """x0..x4 = args (ints), then the slot; the result is x0"""
        for i, a in enumerate(args):
            self.imm(i, a)
        self.ldr(16, 19, SLOT[name])
        self.blr(16)

    def load_api(self):
        self.adr_abs(9, GAPI)
        self.ldr(19, 9)


def spin(a, idx, k):
    """counter[idx] += 1, k times (5 instructions per iteration)"""
    a.imm(9, k)
    a.adr_abs(10, CTR + 8 * idx)
    a.label("spin%d_%d" % (idx, k))
    a.ldr(11, 10)
    a.addi(11, 11, 1)
    a.str(11, 10)
    a.subsi(9, 9, 1)
    a.cbnz(9, "spin%d_%d" % (idx, k))


def logit(a, v):
    """log[logn++] = v"""
    a.adr_abs(10, LOGN)
    a.ldr(11, 10)
    a.addi(12, 11, 1)
    a.str(12, 10)
    a.adr_abs(12, LOG)
    a.add_lsl3(12, 12, 11)
    a.imm(13, v)
    a.str(13, 12)


def thread_cpu(a, idx):
    """cpu[idx] = FNVNX_PLAT_THREAD_CPU() (ext_call module 11, fn 38, no args)"""
    a.hcall("ext_call", 11, 38, 0, 0)
    a.adr_abs(10, CPU + 8 * idx)
    a.str(0, 10)


def cpu_spec(ideal):
    return 1 << 63 | (1 << ideal) << 8 | ideal


def guest(builder, workers):
    """one image: the entry code from builder(a) (x19 = the api table is set up), then the worker functions workers = [(name, fn(a))]"""
    a = Asm()
    a.mov(19, 0)
    a.adr_abs(9, GAPI)
    a.str(19, 9)
    builder(a)
    for name, fn in workers:
        a.label(name)
        a.mov(27, 30)        # the host calls (blr) overwrite x30; x27 is callee-saved
        a.load_api()
        fn(a)
        a.mov(30, 27)
        a.ret()
    code = a.bytes()
    img = bytearray(tbl.make_elf())
    assert 0x40 + len(code) < 0xFC0
    struct.pack_into("<I", img, 52 + 32 + 16, 0x1000)   # the RX segment's filesz: the whole text page
    img[0x1040:0x1040 + len(code)] = code
    return bytes(img)


def create(a, fn, idx_arg, prio, ideal, save):
    """h = thread_create(fn, idx_arg, 0x10000, prio, spec); the id goes to register `save`"""
    a.adr(0, fn)
    a.imm(1, idx_arg)
    a.imm(2, 0x10000)
    a.imm(3, prio)
    a.imm(4, cpu_spec(ideal))
    a.ldr(16, 19, SLOT["thread_create"])
    a.blr(16)
    a.mov(save, 0)


def call_h(a, name, reg):
    a.mov(0, reg)
    a.ldr(16, 19, SLOT[name])
    a.blr(16)


def make_plan(raw):
    rows = [("keys", 0xf0000, 0x200000), ("exe", 0x400000, 0x1409000)]
    return bootlayout.plan(bootlayout.parse_elf(raw), rows, 4 << 20)


def run(raw, quantum, root, limit=None, svc=None):
    svc = svc or boothost.Services(root)
    m = bootmachine.Machine(raw, make_plan(raw), svc, quantum=quantum, limit_instructions=limit)
    tids = []
    m.on_thread(tids.append)
    r = m.run()
    rd = lambda va, n: struct.unpack("<%dQ" % n, bytes(m.uc.mem_read(m.base + va, 8 * n)))   # noqa: E731
    return m, r, tids, rd


def rle(xs):
    out = []
    for x in xs:
        if not out or out[-1] != x:
            out.append(x)
    return out


# ---------------------------------------------------------------------------------------------------------- the guests
K = 1000   # 5000 instructions of spinning per thread


def g_cores():
    """main on core 0 and one thread on each other app core, each spinning; main joins them and exits 0"""
    others = [c for c in range(32) if APP_CORES >> c & 1][1:]

    def entry(a):
        for i, c in enumerate(others):
            create(a, "w%d" % (i + 1), i + 1, 0, c, 20 + i)
        for i in range(len(others)):
            call_h(a, "thread_start", 20 + i)
        thread_cpu(a, 0)
        spin(a, 0, K)
        for i in range(len(others)):
            call_h(a, "thread_join", 20 + i)
        a.hcall("exit", 0)
    return guest(entry, [("w%d" % (i + 1), lambda a, i=i: (spin(a, i + 1, K), thread_cpu(a, i + 1))) for i in range(len(others))])


def g_prio():
    """main (core 0, prio 0) starts T_lo (tid 2, prio -5) and then T_hi (tid 3, prio +5), both on core 0, and spins; each worker logs its tid"""
    def entry(a):
        create(a, "lo", 2, -5, 0, 20)
        create(a, "hi", 3, 5, 0, 21)
        call_h(a, "thread_start", 20)
        call_h(a, "thread_start", 21)
        spin(a, 0, 3 * K)
        call_h(a, "thread_join", 21)
        call_h(a, "thread_join", 20)
        a.hcall("exit", 0)
    return guest(entry, [("lo", lambda a: (spin(a, 1, K // 5), logit(a, 2))), ("hi", lambda a: (spin(a, 2, K // 5), logit(a, 3)))])


def g_block():
    """T2 and T3 on core 1, equal priority. T2 waits on FLAG (blocked, no timeout); T3 spins, sets FLAG, wakes it and logs 3; T2 then logs 2.
    Main (core 0) spins meanwhile, joins both and exits"""
    def t2(a):
        a.adr_abs(0, FLAG)
        a.imm(1, 0)
        a.imm(2, -1)
        a.ldr(16, 19, SLOT["wait_on_address"])
        a.blr(16)
        logit(a, 2)

    def t3(a):
        spin(a, 2, K)
        a.imm(9, 1)
        a.adr_abs(10, FLAG)
        a.str(9, 10)
        a.adr_abs(0, FLAG)
        a.imm(1, 1)
        a.ldr(16, 19, SLOT["wake_address"])
        a.blr(16)
        logit(a, 3)

    def entry(a):
        create(a, "t2", 1, 0, 1, 20)
        create(a, "t3", 2, 0, 1, 21)
        call_h(a, "thread_start", 20)
        call_h(a, "thread_start", 21)
        spin(a, 0, 4 * K)
        call_h(a, "thread_join", 20)
        call_h(a, "thread_join", 21)
        a.hcall("exit", 0)
    return guest(entry, [("t2", t2), ("t3", t3)])


def main():
    tmp = tempfile.mkdtemp(prefix="bcores-")
    try:
        root = os.path.join(tmp, "sd")
        os.makedirs(root)
        N = bin(APP_CORES).count("1")
        cores = [c for c in range(32) if APP_CORES >> c & 1]
        case("the declared machine has 3 cores 0..2 (placement.h FNVNX_APP_CORES)", N == 3 and cores == [0, 1, 2], (hex(APP_CORES), cores))
        # quantum is required and positive
        raw = g_cores()
        for bad in (0, -1, 1.5, None, True):
            try:
                bootmachine.Machine(raw, make_plan(raw), boothost.Services(root), quantum=bad)
                case("quantum %r refused" % (bad,), False)
            except bootmachine.MachineError:
                case("quantum %r refused" % (bad,), True)
        try:
            bootmachine.Machine(raw, make_plan(raw), boothost.Services(root))
            case("quantum has no default", False)
        except TypeError:
            case("quantum has no default", True)

        # 1. all cores progress in one round; a thread on each core; THREAD_CPU = ideal
        Q = 100
        m, r, tids, rd = run(raw, Q, root)
        ctr, cpu = rd(CTR, 3), rd(CPU, 3)
        case("run: exit 0, every thread finished its %d iterations" % K, r["reason"] == "exit" and r["exit_code"] == 0 and ctr == (K, K, K), (r["reason"], ctr))
        case("the slices begin 1,2,3,1,2,3: every core with a ready thread runs once per round, in core order 0..2", tids[:6] == [1, 2, 3, 1, 2, 3], tids[:9])
        case("a thread on each core 0..2: THREAD_CPU of main, T2, T3 = 0, 1, 2", cpu == (0, 1, 2), cpu)
        c = r["cores"]
        case("per-core numbers: each core ran slices and >= one quantum of instructions", sorted(c) == ["0", "1", "2"]
             and all(c[k]["slices"] > 1 and c[k]["instructions"] >= 5 * K for k in c), c)
        case("the cores' instructions add up to the machine's count", sum(c[k]["instructions"] for k in c) == m.instr, (c, m.instr))
        case("a slice ends at the first block boundary past Q: a core's instructions <= slices * (Q + 16), >= its thread's loop (5 * K)",
             all(5 * K <= c[k]["instructions"] <= c[k]["slices"] * (Q + 16) for k in c), c)
        case("a core running one thread has no switch; core 0 (main blocks in the join, resumes) is the only one that may", c["1"]["switches"] == 0 and c["2"]["switches"] == 0, c)
        # determinism
        m2, r2, tids2, rd2 = run(raw, Q, root)
        case("a rerun gives the same slices, counts and results", tids2 == tids and r2["cores"] == c and rd2(CTR, 3) == ctr and r2["host_calls"] == r["host_calls"]
             and m2.instr == m.instr and r2["virtual_ns"] == r["virtual_ns"], (len(tids), len(tids2)))
        # quantum independence for a race-free guest
        outs = set()
        for q in (7, 100, 5000):
            mq, rq, tq, rdq = run(raw, q, root)
            outs.add((rq["reason"], rq["exit_code"], rdq(CTR, 3), rdq(CPU, 3), mq.instr))
        case("another quantum (7, 100, 5000): the same results, the same instruction total (race-free guest)", len(outs) == 1, outs)
        # limit: stops deterministically in the middle of the run, every core has run
        ml, rl, tl, _ = run(raw, Q, root, limit=3 * Q + 50)
        cl = rl["cores"]
        case("a limit of 3 quanta + 50: stops after round 1 started, all 3 cores have run their first slice", rl["reason"] == "limit"
             and tl[:3] == [1, 2, 3], (rl["reason"], tl, cl))

        # 2. priority within a core
        mp, rp, tp, rdp = run(g_prio(), 50, root)
        log = rdp(LOG, 2)
        case("priority: the +5 thread (tid 3) ends before the -5 thread (tid 2) although it was started later", rp["reason"] == "exit" and log == (3, 2), (rp["reason"], log))
        case("priority: after main's first slice the +5 thread runs before main continues; main before the -5 thread: 1,3,1,2,1", rle(tp) == [1, 3, 1, 2, 1], rle(tp))
        case("priority: one core ran all three threads (core 1 and 2 had none)", rp["cores"]["1"]["slices"] == 0 and rp["cores"]["2"]["slices"] == 0
             and rp["cores"]["0"]["slices"] == len(tp), rp["cores"])

        # 3. a blocked thread frees its core; the other thread of the core runs
        mb, rb, tb, rdb = run(g_block(), 50, root)
        logb = rdb(LOG, 2)
        case("block: T2 waits, T3 (same core) runs and ends, then T2: log 3, 2", rb["reason"] == "exit" and logb == (3, 2), (rb["reason"], logb))
        seq = rle([t for t in tb if t != 1])
        case("block: on core 1 the order is T2 (blocks), T3 (runs while T2 waits), T2 (woken)", seq == [2, 3, 2], rle(tb))
        case("block: core 1 switched twice (2 to 3, 3 to 2); core 0 ran main alone meanwhile, core 2 idle", rb["cores"]["1"]["switches"] == 2
             and rb["cores"]["1"]["pairs"] == {"2>3": 1, "3>2": 1} and rb["cores"]["2"]["slices"] == 0, rb["cores"])
        case("block: the total switches is the sum over the cores", rb["switches"] == sum(v["switches"] for v in rb["cores"].values()), rb)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\ntest_bootcores: %d failed" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
