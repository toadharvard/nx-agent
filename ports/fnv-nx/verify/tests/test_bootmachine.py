#!/usr/bin/env python3
"""Tests of verify/bootmachine.py + boothost.py (the boot stage machine). Plain script: exit 0 on pass, one line per case.
Runs small ILP32 PIE guests built by verify/recipe.py (root ports/fnv-nx/tests/bootguest, one variant per MODE: crt0.s with the
FNXG header, guest.ld rebased to 0, hostapi.h, guest.c); the runtime part is the test's own
freestanding entry (the real runtime.c needs devkitA64's newlib: sys/iosupport.h). The guest relocates itself, as runtime.c
does. Scratch: tempfile.mkdtemp, removed at exit."""
import atexit
import functools
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(os.path.dirname(VERIFY)))
GUEST_ROOT = "ports/fnv-nx/tests/bootguest"   # the recipe root of the guest images (class test); its files are the fixtures
GUEST_DIR = os.path.join(REPO, GUEST_ROOT)
HOSTABI_DIR = os.path.join(REPO, "ports", "fnv-nx", "hostabi", "include")   # hostapi.h, fnvnx/sockwire.h: their one home
sys.path.insert(0, VERIFY)
import bootlayout  # noqa: E402
import boothost  # noqa: E402
import bootmachine  # noqa: E402
import gate_build  # noqa: E402

TMP = tempfile.mkdtemp(prefix="bm-")
atexit.register(shutil.rmtree, TMP, ignore_errors=True)
GATE_CACHE = os.environ.get("GATE_CACHE") or os.path.join(TMP, "gate-cache")
FAILED, N = [], [0]


def parse_totality(path):
    """the header's TOTALITY TABLE block parsed: (services {fn: (NAME, n or None)}, slots [names]). Rows are `<fn> <NAME> n=<k>`, the
    NOT SERVED row, and the combined sockets row (names, then `n = ... (SOCK_SOCKET 3, CLOSE 1, ...)`); the numbers are cross-checked
    against the `#define FNVNX_PLAT_<NAME> <fn>u` lines; the slots are the uint64 fields of struct FnvnxHostApi."""
    txt = open(path).read()
    block = txt[txt.index("TOTALITY TABLE BEGIN"):txt.index("TOTALITY TABLE END")]
    lines = [re.sub(r"^\s*\*\s?", "", l) for l in block.splitlines()]
    rows = {}
    for l in lines:
        m = re.match(r"\s*(\d+) ([A-Z_0-9]+)\s+n=(\d+)\b", l)
        if m:
            rows[int(m.group(1))] = (m.group(2), int(m.group(3)))
        m = re.match(r"\s*(\d+) ([A-Z_0-9]+)\s+NOT SERVED", l)
        if m:
            rows[int(m.group(1))] = (m.group(2), None)
    body = "\n".join(lines)
    m = re.search(r"16\.\.26 30 31 (.*?)\bn = the count[^(]*\((.*?)\)\s*\n", body, re.S)
    names = re.findall(r"\bSOCK_[A-Z_]+", m.group(1))
    counts = {("SOCK_" + a if not a.startswith("SOCK_") else a): int(b) for a, b in re.findall(r"([A-Z_]+) (\d+)", m.group(2))}
    assert len(names) == 13 and set(counts) == set(names), (names, counts)
    for f, n in zip(list(range(16, 27)) + [30, 31], names):
        rows[f] = (n, counts[n])
    defs = {int(v): n for n, v in re.findall(r"#define FNVNX_PLAT_([A-Z_0-9]+) (\d+)u", txt)}
    assert {f: r[0] for f, r in rows.items()} == defs, "table rows and #define FNVNX_PLAT_* differ"
    st = txt[txt.index("typedef struct FnvnxHostApi {"):txt.index("} FnvnxHostApi;")]
    slots = re.findall(r"uint64_t (\w+);", st)[4:]   # after guest_base, guest_size, heap_base, heap_size
    return rows, slots


def case(name, cond, extra=""):
    N[0] += 1
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:400]))
    if not cond:
        FAILED.append(name)


def sh(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(" ".join(cmd) + "\n" + r.stdout + r.stderr)
    return r.stdout


def build(mode):
    """the guest image of MODE -> (ELF bytes, ELF path); importable: test_boot.py, test_bootgpu.py, test_boot_job.py use it. The image is the
    output of verify/recipe.py for the root tests/bootguest, variant m<MODE> (RECIPE.md "Variants"), built through gate_build.ensure in
    GATE_CACHE (the verdict's own store; a standalone run without GATE_CACHE builds cold into the scratch directory). The crt0 header
    words (+16 link address, +20 span, +24 relocatable) are written by the recipe's ldcheck stage."""
    res, out = gate_build.ensure("%s@m%d" % (GUEST_ROOT, mode), GATE_CACHE, REPO)
    if res["status"] != "green":
        raise RuntimeError("guest image of MODE %d: recipe %s: %s" % (mode, res["status"], res.get("failure")))
    path = res["elf"]["path"]
    return open(path, "rb").read(), path


def symbol(elf, name):
    for line in sh(["aarch64-linux-gnu-nm", elf]).splitlines():
        a, _t, n = line.split()
        if n == name:
            return int(a, 16)
    raise KeyError(name)


def make_plan(elf_bytes):
    # exe and mod1 lie inside the host's window (0x400000..0x2100000: map_fixed only zeroes there); mod2 is outside it (map_fixed
    # moves a heap block there); keys stays never-mapped
    rows = [("keys", 0xf0000, 0x200000), ("exe", 0x400000, 0x1409000), ("mod1", 0x1410000, 0x1500000), ("mod2", 0x4000000, 0x4200000)]
    plan = bootlayout.plan(bootlayout.parse_elf(elf_bytes), rows, 4 << 20)
    return plan


def run(elf_bytes, root, watch=None, svc=None, **kw):
    svc = svc or boothost.Services(root)
    m = bootmachine.Machine(elf_bytes, make_plan(elf_bytes), svc, **kw)
    lines, tids = [], []
    m.on_console(lines.append)
    m.on_thread(tids.append)
    if watch:
        watch(m)
    return m.run(), lines, tids, svc


def main():
    t0 = time.time()
    root = os.path.join(TMP, "sd")
    os.makedirs(root)
    open(os.path.join(root, "data.bin"), "wb").write(b"01ABCDEFGH23")
    built = {m: build(m) for m in (0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, *range(30, 42), 44, 45, 46, 47, 48, *range(49, 58), 58, 59)}   # MODE 6 (boot CLI guest) is built by test_boot.py
    elf0, path0 = built[0]
    plan0 = make_plan(elf0)
    base = plan0["image"]["base"]
    hits = {"pc": [], "ret": [], "store": []}
    marker = base + symbol(path0, "marker")
    flag = base + symbol(path0, "flag")

    def watchers(m):
        m.watch_pc(marker, lambda tid, pc: hits["pc"].append((tid, pc)))
        m.watch_ret(marker, lambda tid, x0: hits["ret"].append((tid, x0)))
        m.watch_store(flag, lambda tid, a, s, v: hits["store"].append((tid, a, s, v)))

    r1, lines1, tids1, svc1 = run(elf0, root, watch=watchers)
    text = "\n".join(lines1)
    case("exit code 7, reason exit", r1["reason"] == "exit" and r1["exit_code"] == 7, r1)
    case("console: self-relocated string pointers", lines1[:2] == ["hello from the guest", "second line"], lines1[:3])
    for k, v in (("marker", "2a"), ("map", "0"), ("readback", "68ac"), ("overlap", "f59"), ("keys", "da01"),
                 ("carried", str(hex(0xabcd0001 ^ 0xabcd0002))[2:]), ("zeroed-rest", "0"), ("src-unaligned", "1759"),
                 ("src-given", "d401"), ("src-outside", "d401"), ("src-past-end", "d401"),
                 ("protect", "0"), ("protect-unmapped", "d401"), ("protect-image", "d401"),
                 ("protect-given-src", "d401"), ("protect-heap-none", "0"), ("protect-heap-rw", "0"),
                 ("heap-rw-works", "5"), ("protect-bad-size", "ca01"), ("protect-bad-addr", "cc01"), ("unprotect", "0"), ("tickdiff", "3e8"),
                 ("thread", "10000"), ("woken", "0"), ("flag", "4d"), ("pread", "8"), ("rand-calls-equal", "1")):
        case("guest saw %s=%s" % (k, v), (k + "=" + v) in lines1, text)
    case("pread through fs_* (case-folded path)", "ABCDEFGH" in lines1, text)
    case("two threads, a context switch", r1["threads"] == 2 and r1["switches"] >= 2, r1)
    case("refusals recorded", [e["kind"] for e in r1["events"]] == ["map_fixed-refused"] * 6
                                                                      + ["vm_protect-refused"] * 5, r1["events"])
    case("watch_pc fired once, main thread", hits["pc"] == [(1, marker)], hits["pc"])
    case("watch_ret fired once with x0=42", hits["ret"] == [(1, 42)], hits["ret"])
    case("watch_store saw the worker's store", any(h[0] == 2 and h[3] == 77 for h in hits["store"]), hits["store"])
    r2, lines2, tids2, _ = run(elf0, root)
    case("deterministic: identical run() dicts", r1 == r2, (r1, r2))
    case("deterministic: identical on_thread sequences", tids1 == tids2 and len(tids1) > 3, (tids1, tids2))
    case("virtual time moved by sleep (5 ms)", r1["virtual_ns"] >= 5_000_000, r1["virtual_ns"])

    rf, linesf, _, _ = run(*[built[3][0], root])
    f = rf.get("fault", {})
    case("write to a read-only page ends the run: fault", rf["reason"] == "fault" and rf["exit_code"] is None, rf)
    case("fault record", f.get("kind") == "write-protected" and f.get("addr") == 0x4000000 and f.get("tid") == 1
        and f.get("size") == 4 and set(f) == {"kind", "addr", "size", "pc", "lr", "tid"}, f)
    case("nothing runs after the fault", "after the fault" not in linesf, linesf)

    rf4, lines4 = run(built[4][0], root)[:2]
    case("touching the given-away src block ends the run: fault", rf4["reason"] == "fault"
        and rf4["fault"]["kind"] == "read-unmapped" and rf4["fault"]["addr"] == plan0["regions"]["heap"]["lo"], rf4)
    rg, linesg = run(built[5][0], root)[:2]
    case("protected-none heap page: read faults at that address", rg["reason"] == "fault"
        and rg["fault"]["kind"] == "read-protected" and rg["fault"]["addr"] == plan0["regions"]["heap"]["lo"] + 0x30000
        and "after the fault" not in linesg, rg)
    case("image-page protect refused and recorded", any(e["kind"] == "vm_protect-refused" and e["addr"] == base
        and e["result"] == 0xD401 for e in r1["events"]), r1["events"])
    case("nothing runs after the src fault", "after the fault" not in lines4 and "map=0" in lines4, lines4)
    case("run() carries the whole console text", r1["console"] == "\n".join(lines1) + "\n", r1["console"][:80])

    # instruction bound: one block hook, deterministic stop, reuse of the Icount total
    def limited(limit, with_ic):
        svc = boothost.Services(root)
        m = bootmachine.Machine(elf0, plan0, svc, limit_instructions=limit)
        ic = m.attach_icount(None, base) if with_ic else None
        return m.run(), ic, m.instr

    full, icf, ninstr = limited(10**9, True)
    case("limit not reached: normal exit, run() equals the unbounded run",
         full["reason"] == "exit" and full == r1, full)
    case("instr total equals Icount total", ninstr == icf.report()["totals"]["all"]["total"] > 1000, ninstr)
    lim = ninstr // 2
    la, ica, na = limited(lim, True)
    lb, _, nb = limited(lim, False)
    case("limit: reason limit, same stop point, with and without icount", la["reason"] == lb["reason"] == "limit"
        and la == lb and na == nb >= lim and na - lim < 64 and ica.report()["totals"]["all"]["total"] == na, (la, na, nb))
    try:
        bootmachine.Machine(elf0, plan0, boothost.Services(root), limit_instructions=0)
        ok = False
    except bootmachine.MachineError:
        ok = True
    case("limit_instructions <= 0 is a construction error", ok)

    class Ext9(boothost.Services):
        """module 9 registered: fn 99 is a service it cannot answer; every call is recorded"""
        calls = []

        def ext_9(self, fn, a):
            self.calls.append((fn, list(a)))
            if fn == 99:
                raise bootmachine.ExtMissing(9, fn)
            return 1000 * fn + 10 * len(a) + (a[0] if a else 0)

    re_, _, _, _ = run(built[1][0], root, svc=Ext9(root))
    case("a registered ext module whose handler raises ExtMissing ends the run", re_["reason"] == "ext-module-missing"
        and re_["ext"] == {"module": 9, "fn": 99}, re_)
    rd, _, _, _ = run(built[2][0], root)
    case("no ready thread, no deadline: deadlock with a wait report", rd["reason"] == "deadlock"
        and rd["waits"][0]["tid"] == 1 and rd["waits"][0]["wait"][0] == "addr", rd)
    rl, _, _, _ = run(elf0, root, limit_host_calls=3)
    case("limit_host_calls ends with reason limit", rl["reason"] == "limit" and rl["host_calls"] == 3, rl)

    n_slots = sum(1 for n in bootmachine.SLOTS if callable(getattr(boothost.Services, "h_" + n, None)))
    case("46 slots, all resolved at construction", len(bootmachine.SLOTS) == 46 == n_slots
        and len(bootmachine.Machine(elf0, plan0, boothost.Services(root)).handlers) == 46)

    class NoPad(boothost.Services):
        h_pad_read = None

    try:
        bootmachine.Machine(elf0, plan0, NoPad(root))
        ok, msg = False, "no error"
    except bootmachine.MachineError as e:
        ok, msg = "h_pad_read" in str(e), str(e)
    case("a services object missing one slot raises at construction", ok, msg)

    # platform service (module 11): the VM service and thread activity
    heap_lo, image_hi = plan0["regions"]["heap"]["lo"], plan0["regions"]["image"]["hi"]
    rv, linesv, _, svcv = run(built[7][0], root)
    vals = {l.split("=")[0]: int(l.split("=")[1], 16) for l in linesv if "=" in l and l.split("=")[1].isalnum()}
    A, B, A2 = vals.get("resA", 0), vals.get("resB", 0), vals.get("resA2", 0)
    textv = "\n".join(linesv)
    case("vm: run exits 7", rv["reason"] == "exit" and rv["exit_code"] == 7, (rv, textv))
    def lowest(size, taken):
        """reference: the lowest 64 KiB multiple >= 0x10000 below 4 GiB clear of the plan's regions, free ranges and taken"""
        blocked = [(r["lo"], r["hi"]) for r in plan0["regions"].values()] + [(f["lo"], f["hi"]) for f in plan0["free"]] + taken \
            + [bootmachine.WINDOW]
        c = 0x10000
        while c + size <= 1 << 32:
            if not any(c < b and a < c + size for a, b in blocked):
                return c
            c += 0x10000
    expA = lowest(0x8000000, [])
    expB = lowest(0x10000, [(expA, expA + 0x8000000)])
    case("vm: hintless 128 MiB reserve is the lowest fitting 64 KiB multiple by the rule (plan with two image ranges)",
         A == expA and A % 0x10000 == 0 and A >= 0x10000, (hex(A), hex(expA)))
    case("vm: the second reserve (64 KiB) does not overlap the first and is the lowest fitting address",
         B == expB and not (B < A + 0x8000000 and A < B + 0x10000), (hex(A), hex(B), hex(expB)))
    case("vm: commit, write, decommit, recommit gives zeros", all(vals.get(k) == v for k, v in (
        ("commit", 0), ("rb", 0x33), ("decommit", 0), ("recommit", 0), ("zero", 0))), textv)
    case("vm: recommit of a committed page keeps its content", vals.get("recommit-keeps") == 0x33, textv)
    case("vm: commit outside, past the end of, and of size 0 in a reservation fail",
         vals["commit-outside"] == 0x1759 and vals["commit-past-end"] == 0x1759 and vals["commit-size0"] == 0x1759, textv)
    case("vm: VM_PROTECT on an uncommitted page: nonzero", vals.get("protect-uncommitted") == 0xD401, textv)
    case("vm: VM_PROTECT on a committed page works, read-only reads, rw writes", vals.get("protect-committed") == 0
         and vals.get("ro-read") == 0x33 and vals.get("protect-rw") == 0 and vals.get("rw-write") == 0x44, textv)
    case("vm: VM_PROTECT with an X bit is refused", vals.get("protect-x") == 0xD801, textv)
    case("vm: recommit after decommit is RW and zero whatever the protection was",
         vals.get("recommit-protected") == 0 and vals.get("zero-rw-after-protect") == 0, textv)
    case("vm: decommit of part of an extent zeroes it and it stays accessible (host_vm.inc)",
         vals.get("partial-decommit") == 0 and vals.get("partial-zero-writable") == 0 and vals.get("partial-write") == 0x88, textv)
    case("vm: map_fixed over a committed page is occupied (AlreadyInitialized), over an uncommitted reserved page outside the alias-code region 0xDA01",
         vals.get("mapfixed-over-resv") == 0xF59 and vals.get("mapfixed-over-resv-free") == 0xDA01, textv)
    case("vm: release frees the whole reservation; a non-base address is refused",
         vals.get("release") == 0 and vals.get("release-again") == 0x1759 and vals.get("release-inner") == 0x1759, textv)
    case("vm: reserve after release gives the same address", A2 == A, (hex(A), hex(A2)))
    case("vm: hints: overlap, inside the exe or a module range, above 4 GiB, image, size 0 fail; a hint in a gap or free address is exact (64 KiB rounded)",
         all(vals.get(k) == 0 for k in ("hint-overlap", "hint-exe", "hint-module", "hint-above-4g", "hint-in-image", "hint-size0",
                                        "hint-ok-overlap")) and vals.get("hint-ok") == 0x20000000
         and vals.get("hint-gap") == 0x3000000, textv)
    kinds = [e["kind"] for e in rv["events"]]
    case("vm: every refusal is an event with its result", kinds == [
        "vm_commit-refused"] * 3 + ["vm_protect-refused"] * 2 + ["map_fixed-refused"] * 2 + ["vm_release-refused"] * 2
        + ["vm_reserve-refused"] * 7 + ["thread_activity-refused"] * 3, kinds)
    case("platform: thread_activity: unknown id -> NotFound 0x1359, tid 0 -> BadInput 0x1759, new thread -> BadInput 0x1759, pause holds the worker, resume runs it",
         vals.get("tact-nothread") == 0x1359 and vals.get("tact-tid0") == 0x1759 and vals.get("tact-new") == 0x1759
         and vals.get("tact-pause") == 0 and vals.get("paused-flag") == 0 and vals.get("tact-resume") == 0
         and vals.get("resumed-flag") == 0x4d, textv)
    case("platform: cpu_boost and message are the services'", svcv.boosts == [1] and svcv.messages == [("cap", "body")]
         and vals.get("boost") == 0 and vals.get("message") == 0, (svcv.boosts, svcv.messages))
    rv2 = run(built[7][0], root)[0]
    case("vm: two identical runs give identical run() dicts", rv == rv2, (rv, rv2))
    rf8, lines8 = run(built[8][0], root)[:2]
    case("vm: access to a reserved, uncommitted page: fault record", rf8["reason"] == "fault"
         and rf8["fault"]["kind"] == "read-unmapped" and rf8["fault"]["addr"] == int(lines8[2].split("=")[1], 16) + 0x1000
         and "committed-ok=1" in lines8 and "after the fault" not in lines8, (rf8, lines8))
    rf9, lines9 = run(built[9][0], root)[:2]
    case("vm: write to a VM_PROTECT read-only committed page: fault", rf9["reason"] == "fault"
         and rf9["fault"]["kind"] == "write-protected" and "after the fault" not in lines9, (rf9, lines9))
    rf10, lines10 = run(built[10][0], root)[:2]
    case("vm: access to a wholly decommitted extent: fault", rf10["reason"] == "fault" and rf10["fault"]["kind"] == "read-unmapped"
         and "after the fault" not in lines10, (rf10, lines10))
    rf11 = run(built[11][0], root)[0]
    case("platform: a socket service with its exact nargs (no result in this environment) ends the run as ext-module-missing",
         rf11["reason"] == "ext-module-missing" and rf11["ext"] == {"module": 11, "fn": 16}, rf11)
    def kv(lines):
        return {l.split("=")[0]: l.split("=")[1] for l in lines if "=" in l}

    r49, l49 = run(built[49][0], root)[:2]
    k49 = kv(l49)
    M = bootmachine.U64 + 1
    ne = lambda e: "%x" % (M - e)
    ebadf_fns = [f for f in range(17, 32) if f != 26 and not 27 <= f <= 29]
    want49 = {"sock-afnosupport": ne(97), "sock-af-wide": ne(97), "sock-af-neg": ne(97), "sock-type-4": ne(22), "sock-type-0": ne(22),
              "ebadf-mask": "%x" % sum(1 << f for f in ebadf_fns),
              "poll-big": ne(22), "poll-null": ne(14), "poll-nfds0-t0": "0", "poll-nfds0-null-t7": "0", "poll-wait-us": "1b58",
              "poll-nval": "2", "poll-rev0": "0", "poll-rev1": "20", "poll-rev2": "20", "poll-ev2": "5",
              "poll-neg-t0": "0", "poll-neg-t10": "0", "poll-neg-us": "2710", "poll-neg-rev": "0",
              "tz-0": "0", "tz-off": "0", "tz-dst": "0", "tz-abbr": "435455", "tz-abbr-rest": "0", "tz-max": "0", "tz-1970": "0", "tz-1970-off": "0", "tz-2100-last": "0", "tz-2101": "0", "tz-2101-untouched": "0",
              "tz-u64max": "0", "tz-negative-main": "0",
              "tz-null-out": ne(14)}
    bad49 = {n: (k49.get(n), v) for n, v in want49.items() if k49.get(n) != v}
    case("sockets and TZ_AT (guest): every result the arguments fix with no socket open: errno classes, -EBADF for each handle-first service, "
         "POLL waits its timeout in virtual time and answers POLLNVAL (%d values)" % len(want49), r49["reason"] == "exit" and not bad49
         and "ebadf-bad" not in k49, (bad49, r49))
    r50, l50 = run(built[50][0], root)[:2]
    k50 = kv(l50)
    BI, NF = "1759", "1359"
    want50 = {"self-id": "1", "self-distinct": "1", "self-start": BI, "self-join": BI, "self-prio": "0", "self-resume": "f401",
              "self-free": "0", "self-free-again": NF, "self-use-freed": NF, "pause-by-worker": "64", "act-new": BI, "start": "0",
              "start-again": BI, "start-finished": BI, "act-finished": BI, "count": "65"}
    bad50 = {n: (k50.get(n), v) for n, v in want50.items() if k50.get(n) != v}
    case("THREAD_SELF (guest): a started record (start/join BadInput), distinct ids, free ends only the record, a paused-woken main thread "
         "stays off until resumed; start of a started or finished thread BadInput with state kept, activity of a new or finished thread BadInput",
         r50["reason"] == "exit" and not bad50, (bad50, r50))
    r51 = run(built[51][0], root)[:2]
    case("POLL with nothing to wait for and timeout -1: the run ends as deadlock, never returns", r51[0]["reason"] == "deadlock"
         and "after the poll" not in r51[1], r51[0])
    r52, l52 = run(built[52][0], root)[:2]
    case("the numbers 10, 15, 27..29, 32 and 33, which hostapi.h does not declare, answer NO_SERVICE (guest, any nargs)",
         r52["reason"] == "exit" and kv(l52) == {n: "ffffffffffffff02" for n in ("fn10", "fn15", "fn27", "fn28", "fn29", "fn32", "fn33")}, (r52, l52))
    r53, l53 = run(built[53][0], root)[:2]
    case("TZ_AT at INT64_MAX and INT64_MIN: the fixed UTC zone answers every instant", r53["reason"] == "exit"
         and kv(l53) == {"tz-below": "0", "tz-above": "0"}, (r53, l53))
    r54, l54 = run(built[54][0], root)[:2]
    case("THREAD_ACTIVITY on the calling thread's own THREAD_SELF record: ResultBusy 0xF401 for pause and resume, the run goes on",
         r54["reason"] == "exit" and kv(l54) == {"self-pause": "f401", "self-resume": "f401"} and "after the pause" in l54, (r54, l54))
    r55, l55 = run(built[55][0], root)[:2]
    k55 = kv(l55)
    want55 = {"not-started-pause": "1759", "resume-running": "fa01", "pause": "0", "pause-paused": "fa01", "resume": "0", "resume-again": "fa01",
              "finished": "1759", "me-pause": "f401", "me-resume": "f401", "me-zero": "1759", "rec-finished": "1759", "rec-finished-resume": "1759",
              "no-record": "1359"}
    case("THREAD_ACTIVITY: new / finished thread or record BadInput, resume of a running and pause of a paused thread 0xFA01, own record 0xF401, "
         "unknown id NotFound (%d values)" % len(want55), r55["reason"] == "exit" and k55 == want55, (k55, r55))
    r56, l56 = run(built[56][0], root)[:2]
    k56 = kv(l56)
    want56 = {"h1": "3", "h2": "4", "close1": "0", "h3": "3", "getfl": "2", "setfl": "0", "getfl-nb": "802", "connect-ext": ne(101),
              "sendto-ext": ne(101), "recv-nb": ne(11), "bind-lo": "0", "close3": "0", "closed-getfl": ne(9), "tz-last": "0"}
    case("sockets (guest): handles, flag state, ENETUNREACH, loopback bind "
         "(%d values)" % len(want56), r56["reason"] == "exit" and k56 == want56, ({n: (k56.get(n), v) for n, v in want56.items() if k56.get(n) != v}, r56))
    sw = open(os.path.join(HOSTABI_DIR, "fnvnx", "sockwire.h")).read()
    lst = lambda name: [int(v, 0) for v in re.findall(r"X\(\w+, (\w+)\)", re.search(r"#define " + name + r"\(X\)((?:[^\n]*\\\n)*[^\n]*)", sw).group(1))]
    nums = dict(re.findall(r"#define FNVNX_(WE_\w+|WEAI_\w+) (\d+)", sw))
    eai = dict(re.findall(r"X\(EAI_(\w+), (\d+)\)", sw))
    case("machine's wire constants equal the fixture sockwire.h (errno numbers, AF and type lists, AI mask, EAI numbers, POLLNVAL)",
         tuple(lst("FNVNX_WIRE_AF_LIST")) == bootmachine.WIRE_AF and tuple(lst("FNVNX_WIRE_SOCKTYPE_LIST")) == bootmachine.WIRE_SOCKTYPE
         and functools.reduce(lambda x, y: x | y, lst("FNVNX_WIRE_AI_LIST")) == bootmachine.WIRE_AI_MASK
         and int(nums["WE_INVAL"]) == bootmachine.WE_INVAL and int(nums["WE_FAULT"]) == bootmachine.WE_FAULT
         and int(nums["WE_OVERFLOW"]) == bootmachine.WE_OVERFLOW
         and (int(eai["BADFLAGS"]), int(eai["NONAME"]), int(eai["FAMILY"]), int(eai["SOCKTYPE"])) == (
             bootmachine.WEAI_BADFLAGS, bootmachine.WEAI_NONAME, bootmachine.WEAI_FAMILY, bootmachine.WEAI_SOCKTYPE)
         and "X(EBADF, 9)" in sw and "X(EAFNOSUPPORT, 97)" in sw and int(re.search(r"FNVNX_WPOLLNVAL (0x\w+)", sw).group(1), 16) == bootmachine.WPOLLNVAL,
         (nums, eai))

    class NoMsg(boothost.Services):
        plat_message = None

    try:
        bootmachine.Machine(elf0, plan0, NoMsg(root))
        ok, msg = False, "no error"
    except bootmachine.MachineError as e:
        ok, msg = "message" in str(e), str(e)
    case("a header-defined platform fn nobody implements raises at construction", ok, msg)
    case("every served non-socket fn of module 11 (1..9, 11..14, 34, 35, 36) resolves", sorted(bootmachine.Machine(elf0, plan0, boothost.Services(root)).plat) == [*range(1, 10), *range(11, 15), 34, 35, 36])

    class NoSec(boothost.Services):
        plat_section_map = None

    try:
        bootmachine.Machine(elf0, plan0, NoSec(root))   # services may override a fn; None means "not there": the machine's own serves it
        ok = True
    except bootmachine.MachineError:
        ok = False
    case("section fns are served by the machine itself", ok)
    # sections (module 11 fn 11..14)
    def E(c):
        return "%x" % (-c & ((1 << 64) - 1))

    r30, l30 = run(built[30][0], root)[:2]
    k = kv(l30)
    case("section: run exits", r30["reason"] == "exit" and r30["exit_code"] == 7, (r30, l30))
    ok_vals = {"resv": "1", "c0": E(0xCA01), "c-unal": E(0xCA01), "c-big": E(0xCA01), "id": "1", "A": "1", "B-own": "1", "C": "1",
               "zero": "0", "share1": "22", "share2": "33", "share3": "44", "C-sees": "1",
               "m-size0": E(0xCA01), "m-size-unal": E(0xCA01), "m-size-over": E(0xCA01), "m-addr-unal": E(0xCC01),
               "m-off-unal": E(0xCC01), "m-id": E(0xE401), "m-id0": E(0xE401), "m-range": E(0xD401), "m-range-wrap": E(0xD401),
               "h-outside": E(0xDA01), "h-keys": E(0xDA01), "h-image": E(0xDA01), "h-heap": E(0xDA01), "h-straddle": E(0xDA01),
               "h-over-view": E(0xD401), "commit-gap": "0", "h-over-committed": E(0xD401),
               "vm-commit": "d401", "vm-commit-wide": "d401", "vm-commit-B": "1759", "vm-decommit-B": "d401", "vm-decommit": "d401", "vm-protect": "d401",
               "vm-protect-B": "d401", "vm-release": "d401", "view-intact": "1", "mf-B": "f59", "mf-A": "f59", "resv2-clear": "1",
               "resv-hint-B": "0", "u-wrong-size": E(0xD401), "u-wrong-addr": E(0xD401), "u-nonview": E(0xD401),
               "free": "0", "map-after-free": E(0xE401), "free-twice": E(0xE401), "free-unknown": E(0xE401), "alive-A": "1",
               "alive-B": "55", "u-A": "0", "u-B-still": "55", "u-C": "0", "u-B": "0", "u-B-twice": E(0xD401),
               "release-R": "0", "B2-lowest": "1", "s2-id": "2"}
    bad = {n: (k.get(n), v) for n, v in ok_vals.items() if k.get(n) != v}
    case("section: every create/map/unmap/free/interplay result as the header says (%d values)" % len(ok_vals), not bad, bad)
    ev = [(e["kind"], e["result"]) for e in r30["events"]]
    want = [("section_create-refused", 0xCA01)] * 3 + [("section_map-refused", c) for c in (
        0xCA01, 0xCA01, 0xCA01, 0xCC01, 0xCC01, 0xE401, 0xE401, 0xD401, 0xD401, 0xDA01, 0xDA01, 0xDA01, 0xDA01, 0xDA01, 0xD401)]
    case("section: refusals recorded in order (create, map errors)", ev[:len(want)] == want, ev[:len(want)])
    tail = ev[len(want):]
    case("section: later refusals are vm_*, map_fixed, section_map/unmap/free refusals with the codes",
         tail[:1] == [("section_map-refused", 0xD401)] and tail[1:9] == [("vm_commit-refused", 0xD401)] * 2 + [("vm_commit-refused", 0x1759)]
         + [("vm_decommit-refused", 0xD401)] * 2 + [("vm_protect-refused", 0xD401)] * 2 + [("vm_release-refused", 0xD401)]
         and tail.count(("map_fixed-refused", 0xF59)) == 2 and tail.count(("section_unmap-refused", 0xD401)) == 4
         and tail.count(("section_free-refused", 0xE401)) == 2 and ("section_map-refused", 0xE401) in tail, tail)
    r31, l31 = run(built[31][0], root)[:2]
    k = kv(l31)
    case("section: unmapped view faults, the other view still works and writes",
         r31["reason"] == "fault" and r31["fault"]["kind"] == "read-unmapped" and r31["fault"]["addr"] == int(k["A-addr"], 16)
         and k["unmap-A"] == "0" and k["B-works"] == "7" and k["B-writes"] == "9" and k["A-addr"] != k["B-addr"]
         and "after the fault" not in l31, (r31, l31))
    r32, l32 = run(built[32][0], root)[:2]
    k = kv(l32)
    case("section: threads share views (B reads A's write, main reads the thread's write)",
         r32["reason"] == "exit" and r32["threads"] == 3 and k.get("thread-read") == "a5a6" and k.get("thread-write") == "77", (r32, l32))
    r34, l34 = run(built[34][0], root)[:2]
    k = kv(l34)
    case("section: table limits (128 sections incl. deferred ones, 512 views, ids never reused)",
         r34["reason"] == "exit" and k.get("sections-ok") == "80" and k.get("last-id") == "80" and k.get("c-full") == E(0xCE01)
         and k.get("free-first-deferred") == "0" and k.get("c-full-deferred") == E(0xCE01) and k.get("unmap-last-view") == "0"
         and k.get("id129") == "81" and k.get("free-last") == "0" and k.get("id130") == "82" and k.get("views-ok") == "200"
         and k.get("views-ascending") == "1" and k.get("v-full") == E(0xCE01) and k.get("unmap-one") == "0" and k.get("v-after") == "1",
         (r34, l34))
    case("section: two identical runs give identical run() dicts", r30 == run(built[30][0], root)[0]
         and r32 == run(built[32][0], root)[0] and r34 == run(built[34][0], root)[0], r30)
    r35 = run(built[35][0], root)[0]
    r36 = run(built[36][0], root)[0]
    case("exit code 0x52000000|fact_id is reason reach-violation with fact_id", r35["reason"] == "reach-violation"
         and r35["fact_id"] == 42 and r35["exit_code"] == 0x5200002A, r35)
    case("other non-zero exit codes stay ordinary exits (no fact_id)", r36["reason"] == "exit" and "fact_id" not in r36
         and r1["reason"] == "exit" and "fact_id" not in r1, r36)

    # ---- the header's TOTALITY TABLE (parsed from the fixture) against the machine
    rows, hslots = parse_totality(os.path.join(HOSTABI_DIR, "fnvnx", "hostapi.h"))
    SERVED = list(range(1, 10)) + list(range(11, 15)) + list(range(16, 27)) + [30, 31, 34, 35, 36]
    mach0 = bootmachine.Machine(elf0, plan0, boothost.Services(root))
    case("header: 29 services of module 11 and 46 slots parsed", len(rows) == 29 and sorted(rows) == SERVED
         and len(hslots) == 46, (len(rows), len(hslots)))
    case("slots: the header's struct order is bootmachine.SLOTS, each has an h_<slot> and a handler", hslots == bootmachine.SLOTS
         and all(callable(getattr(boothost.Services, "h_" + n, None)) and n in mach0.handlers for n in hslots), hslots)
    served_decl = {f for f in rows if f in mach0.plat or f in bootmachine.SOCK_SERVICES}
    case("services: every declared service is served by the machine (the sockets by Machine._sock), none extra",
         served_decl == set(rows) and set(mach0.plat) | set(bootmachine.SOCK_SERVICES) == set(rows) and not set(mach0.plat) & set(bootmachine.SOCK_SERVICES),
         (sorted(set(rows) - served_decl), sorted(set(mach0.plat) & set(bootmachine.SOCK_SERVICES))))
    case("services: served = 1..9, 11..26, 30, 31, 34..36 (29 of 29)", sorted(served_decl) == SERVED and not hasattr(bootmachine, "UNSERVED"),
         sorted(served_decl))
    names_ok = all(rows[f][0].lower() == n and rows[f][1] in (None, need) for f, (n, need) in
                   {**bootmachine.PLATFORM_FNS, **bootmachine.SOCK_SERVICES}.items())
    case("services: machine names and least nargs equal the header's rows (every row has its n; fn 10 is served by the console host too)",
         names_ok and all(r[1] is not None for r in rows.values()) and "recorded difference" not in bootmachine.__doc__,
         [(f, rows[f]) for f in rows if rows[f][0].lower() != {**bootmachine.PLATFORM_FNS, **bootmachine.SOCK_SERVICES}[f][0]])
    # mechanical argument rules for every declared service, through Machine.ext_call on the host page
    args_at = mach0.host_page + 0x100
    bad = {}
    for f, (nm, need) in sorted(rows.items()):
        need = need if need is not None else bootmachine.PLATFORM_FNS[f][1]
        if f in bootmachine.SOCK_SERVICES:
            r = [mach0.ext_call(11, f, args_at, k) for k in sorted({0, need - 1, need + 1} - {need, -1})]
            if r != [-22 & bootmachine.U64] * len(r):
                bad[nm] = ("sock wrong nargs", r)
            if mach0.ext_call(11, f, 0, need) != bootmachine.EXT_BAD_NARGS:
                bad[nm + "-null"] = "sock null array with nargs > 0 is not BAD_NARGS"
            mach0.uc.mem_write(args_at, bytes(48))   # all-zero arguments: every service answers, none is the network's
            try:
                if not isinstance(mach0.ext_call(11, f, args_at, need), int):
                    bad[nm] = "sock exact nargs: not a value"
            except bootmachine.ExtMissing as e:
                bad[nm] = ("ExtMissing on zero arguments", e.fn)
            continue
        if need and mach0.ext_call(11, f, args_at, need - 1) != bootmachine.EXT_BAD_NARGS:
            bad[nm] = "nargs below the row"
        if mach0.ext_call(11, f, 0, need + 1) != bootmachine.EXT_BAD_NARGS:
            bad[nm] = "null array with nargs > 0"
    case("services: every declared service answers BAD_NARGS below its n and for a null array, sockets -EINVAL on a wrong nargs, BAD_NARGS on a null array and a value for zero arguments",
         not bad, bad)
    case("services: fn 0, 10, 15, 27..29, 32, 33 and 37.. are NO_SERVICE, unknown module NO_MODULE, fn > UINT32_MAX NO_SERVICE (before the platform table)",
         all(mach0.ext_call(11, f, args_at, 4) == bootmachine.EXT_NO_SERVICE for f in (0, 10, 15, 27, 28, 29, 32, 33, 37, 38, 0xFFFFFFFF, 1 << 32, (1 << 32) + 4))
         and mach0.ext_call(63, 1, args_at, 1) == bootmachine.EXT_NO_MODULE and mach0.ext_call(64, 1 << 33, 0, 0) == bootmachine.EXT_NO_MODULE
         and mach0.ext_call(11, 4, args_at, 1 << 40) == 0, "")
    try:
        bootmachine.Machine(elf0, plan0, boothost.Services(root), profile="tv")
        ok = False
    except bootmachine.MachineError:
        ok = True
    case("profile: anything but handheld / docked is a construction error", ok)

    r40, l40 = run(built[40][0], root)[:2]
    k = kv(l40)
    NOMOD, NOSVC, BADN, EINVAL = "ffffffffffffff01", "ffffffffffffff02", "ffffffffffffff03", "ffffffffffffffea"
    want40 = {"mod64": NOMOD, "mod-huge": NOMOD, "mod9-unreg": NOMOD, "mod12-fn-huge": NOMOD, "fn0": NOSVC, "fn37": NOSVC, "te-n4": BADN, "te-null-array": BADN, "self-n1-null": BADN, "sock-null-array-wrongn": EINVAL,
              "sock-null-array-n0": EINVAL, "sock-null-array-exact": BADN, "fn0-null-array": NOSVC,
              "fn-u32max": NOSVC, "fn-above-u32": NOSVC, "fn10-nargs1": NOSVC, "vm-reserve-n1": BADN, "null-array": BADN,
              "docked-n0-null": "0", "docked-n1-null": BADN, "docked-n3": "0", "docked-ncap": "0", "boost-extra-args": "0",
              "sock-wrong-n": EINVAL, "sock-wrong-n0": EINVAL, "sock-too-many": EINVAL, "tz-n1": BADN, "tz-null": BADN,
              "reserve-above-4g": "0", "reserve-4g": "0"}
    bad = {n: (k.get(n), v) for n, v in want40.items() if k.get(n) != v}
    case("ext_call (guest): NO_MODULE / NO_SERVICE / BAD_NARGS are returned to the guest, nargs above UINT32_MAX counts as UINT32_MAX, "
         "extra arguments ignored, sockets dispatch first: -EINVAL on wrong nargs (null array or not), BAD_NARGS on the exact count with a null array (%d values)" % len(want40), r40["reason"] == "exit" and not bad, (bad, r40))
    # TEXT_ENTRY (36): scenario texts, in order; none left -> cancelled; a text longer than max_len is a scenario error (not truncated)
    r57, l57, _, _ = run(built[57][0], root, text_entries=["Ab\U0001F600", "x"])
    k = kv(l57)
    want57 = {"te-max0": "1", "te-max501": "1", "te-maxhuge": "1", "te-out-null": "1", "te-n-null": "1", "te-bad-untouched": "1",
              "te-entered": "1", "te-n": "4", "te-u0": "41", "te-u1": "62", "te-u2": "d83d", "te-u3": "de00", "te-nul": "0", "te-rest": "7777",
              "te-entered2": "1", "te-n2": "1", "te-2u0": "78", "te-2nul": "0", "te-2rest": "7777",
              "te-cancel": "0", "te-cancel-untouched": "1", "te-cancel-again": "0"}
    bad = {n: (k.get(n), v) for n, v in want57.items() if k.get(n) != v}
    case("TEXT_ENTRY (guest): max_len 0 / 501 / huge, out null, n null -> -FNVNX_HE_BADINPUT without consuming a text; the texts come in order as UTF-16 "
         "with a NUL and *n; none left -> 0 with out and *n untouched", r57["reason"] == "exit" and not bad, (bad, r57["reason"]))
    ev = [(e["kind"], e["result"]) for e in r57["events"] if e["kind"] == "text-entry"]
    case("TEXT_ENTRY: one host event per call with its signed result (5 refused calls -0x1759, then 1, 1, 0, 0)", ev == [("text-entry", -0x1759)] * 5 + [("text-entry", 1)] * 2 + [("text-entry", 0)] * 2, ev)
    r57b = run(built[57][0], root)[0]
    case("TEXT_ENTRY with no scenario: every well-formed call is 0 (cancelled)", r57b["reason"] == "exit"
         and [e["result"] for e in r57b["events"] if e["kind"] == "text-entry"] == [-0x1759] * 5 + [0] * 4, r57b["reason"])
    r58, l58 = run(built[58][0], root, text_entries=["abcd"])[:2]
    case("TEXT_ENTRY: a scenario text longer than max_len ends the run as scenario-error (never truncated, the guest gets no result)",
         r58["reason"] == "scenario-error" and "te-long" not in kv(l58) and "max_len 3" in r58["scenario"] and "4 UTF-16 units" in r58["scenario"], (r58["reason"], r58.get("scenario")))
    r59, l59 = run(built[59][0], root, text_entries=["\U0001F600"])[:2]
    k = kv(l59)
    case("TEXT_ENTRY: a text of exactly max_len units fits", r59["reason"] == "exit" and k == {"te-fit": "1", "te-fit-n": "2", "te-fit-u0": "d83d", "te-fit-u1": "de00", "te-fit-nul": "0"}, (r59["reason"], k))
    for bad_scn in (["\ud800"], "abc", [b"x"]):
        try:
            bootmachine.Machine(elf0, plan0, boothost.Services(root), text_entries=bad_scn)
            ok = False
        except bootmachine.MachineError:
            ok = True
        case("TEXT_ENTRY: a scenario that is not a list of strings of Unicode scalars is a construction error: %r" % (bad_scn,), ok)
    # DISPLAY_DOCKED: the profile
    for prof, want in (("handheld", "0"), ("docked", "1")):
        rp, lp = run(built[40][0], root, profile=prof)[:2]
        k = kv(lp)
        case("DISPLAY_DOCKED is %s for profile %s (any nargs, null array only with nargs 0)" % (want, prof),
             k.get("docked-n0-null") == want and k.get("docked-n3") == want and k.get("docked-ncap") == want
             and k.get("docked-n1-null") == BADN, (prof, k))
        re48 = run(built[48][0], root, profile=prof)[0]
        case("DISPLAY_DOCKED through the exit code, profile %s" % prof, re48["reason"] == "exit" and re48["exit_code"] == int(want), re48)
    svc41 = Ext9(root)
    Ext9.calls = []
    r41, l41 = run(built[41][0], root, svc=svc41)[:2]
    k = kv(l41)
    case("ext_call (guest): a registered module is called with its args; fn > UINT32_MAX is NO_SERVICE without calling it; nargs 0 gives []; "
         "a handler raising ExtMissing ends the run", k.get("m9-fn1") == "407" and k.get("m9-fn-huge") == NOSVC and k.get("m9-nargs0") == "7d0"
         and r41["reason"] == "ext-module-missing" and r41["ext"] == {"module": 9, "fn": 99} and "after the missing service" not in l41
         and Ext9.calls == [(1, [1, 2, 3]), (2, []), (99, [1])], (r41, l41, Ext9.calls))

    # VM_PROTECT order and spans; windows
    r44, l44 = run(built[44][0], root)[:2]
    k = kv(l44)
    want44 = {"p-addr-unal": "cc01", "p-addr-unal-size0": "cc01", "p-size0": "ca01", "p-size-unal": "ca01", "p-wrap": "d401",
              "p-wrap-beats-perm": "d401", "p-wo": "d801", "p-x": "d801", "p-7": "d801", "p-big": "d801", "p-perm-beats-owned": "d801",
              "p-unowned": "d401", "p-still-rw": "3", "p-span": "0", "p-span-ro": "3", "p-none": "0", "p-rw": "0", "p-rw-write": "b",
              "decommit-ro-none": "0", "decommit-wrap": "d401", "decommit-size0": "0", "recommit": "0", "recommit-zero": "0",
              "w-ro": "0", "w-ro-read": "77", "w-none": "0", "w-rw": "0", "w-rw-keeps": "80", "mf": "0"}
    bad = {n: (k.get(n), v) for n, v in want44.items() if k.get(n) != v}
    case("VM_PROTECT: header check order, write-only 2 -> 0xD801, a span of two extents, the host window; DECOMMIT of R / no-access "
         "extents (%d values)" % len(want44), r44["reason"] == "exit" and not bad, (bad, r44))

    r45, l45 = run(built[45][0], root)[:2]
    k = kv(l45)
    want45 = {"size0": "1759", "wrap": "1759", "addr-unal": "1759", "size-unal": "1759", "size0-in-window": "1759", "past-4g": "da01",
              "src0": "1759", "src-unal": "1759", "outside-region": "da01", "keys": "da01", "src-not-heap": "d401",
              "win": "0", "win-zeroed": "0", "win-src-ignored": "0", "win-none-to-rw": "0", "win-rw-again": "9", "win-first": "0",
              "win-last": "0", "win-straddle-end": "f59", "win-straddle-start": "f59", "live": "200", "limit": "559",
              "limit-occupied": "559"}
    bad = {n: (k.get(n), v) for n, v in want45.items() if k.get(n) != v}
    case("map_fixed: size 0 / wrap / misalignment 0x1759, the window zeroes and ignores src, src 0 / misaligned 0x1759, 512 live -> 0x559, "
         "alias region 0xDA01, src not heap 0xD401 (%d values)" % len(want45),
         r45["reason"] == "exit" and not bad, (bad, r45))

    r46, l46 = run(built[46][0], root)[:2]
    k = kv(l46)
    NF, BI, CC, RG = "1359", "1759", "cc01", "d401"
    want46 = {"h1-id": "1", "h-distinct": "1", "start-unknown": NF, "start-zero": NF, "join-unknown": NF, "join-never-started": BI,
              "prio-unknown": NF, "prio-self": "0", "cpu-unknown": NF, "cpu-self": "0", "cpu-negative": "0", "cpu-bits40-62": "0",
              "start": "0", "join": "0", "flag": "4d", "free": "0", "free-twice": NF, "join-freed": NF, "start-freed": NF,
              "prio-freed": NF, "act-freed": NF, "free-unknown": NF, "h3-new-generation": "1", "free-running": "0", "detached-ran": "4f",
              "dir-id": "1", "dir-next-cap0": "0", "dir-next-cut": "3", "dir-cut-bytes": hex(ord("d") * 0x10000 + ord("t") * 0x100 + 0)[2:],
              "dir-next-2": "6", "dir-end": "0", "dir-unknown": "0", "dir-as-thread": NF, "thread-as-dir": "0", "dir-close": "0",
              "dir-next-closed": "0", "dir-close-again": "0", "dir-close-unknown": "0", "dir-notdir": "0", "dir-missing": "0",
              "dirs-until-full": hex(1024 - 3)[2:], "thread-when-full": "0", "close-one": "0", "thread-after-close": "1",
              "wait-unal": CC, "wait-unmapped": RG, "wait-prot-none": RG, "wake-unal": CC, "wake-unmapped": RG, "wake-prot-none": RG,
              "wake-all": "0", "woken-all": "2"}
    bad = {n: (k.get(n), v) for n, v in want46.items() if k.get(n) != v}
    case("ids: thread and directory ids are table handles (unknown / freed -> NotFound, never started -> BadInput, 1024 live together, "
         "a freed id never names anything again); dir_next cap 0 / cut; wait / wake alignment and readability; wake count above "
         "0x7FFFFFFF wakes all (%d values)" % len(want46), r46["reason"] == "exit" and not bad, (bad, r46))
    case("ids: thread_set_cpu with a negative spec changes nothing", run(built[46][0], root)[3].placement.get(1) == {"cpu": 2, "prio": 3},
         "")

    r47, l47 = run(built[47][0], root)[:2]
    k = kv(l47)
    M1 = "ffffffffffffffff"
    want47 = {"a-submit-closed": M1, "a-rate": M1, "a-period0": M1, "a-period-big": M1, "a-open": "0", "a-open-again": "0",
              "a-submit-wrong": M1, "a-submit": "0", "a-close": "0", "a-close-again": "0", "a-submit-after": M1, "a-reopen": "0",
              "a-submit-8192": "0", "pad4": "0", "pad-untouched": "1", "pad0": "0", "pad0-zeroed": "0", "sleep-max": "0"}
    bad = {n: (k.get(n), v) for n, v in want47.items() if k.get(n) != v}
    case("audio_open / audio_submit / audio_close and pad_read index > 3 as the header states; sleep_ns above INT64_MAX sleeps INT64_MAX ns",
         r47["reason"] == "exit" and not bad and (1 << 63) - 1 <= r47["virtual_ns"] <= (1 << 63) - 1 + 10**9, (bad, r47))
    case("new modes: two identical runs give identical run() dicts", r44 == run(built[44][0], root)[0]
         and r45 == run(built[45][0], root)[0] and r46 == run(built[46][0], root)[0] and r40 == run(built[40][0], root)[0], "")
    print("%d cases, %.1f s" % (N[0], time.time() - t0))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
