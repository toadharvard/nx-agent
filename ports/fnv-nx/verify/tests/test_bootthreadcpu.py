#!/usr/bin/env python3
"""Thread placement and THREAD_CPU (module 11, fn 38) in the boot machine, against hostapi.h and placement.h: thread_create's cpu is
FNVNX_CPU_SPEC(ideal, mask) with ideal in mask within the application cores (else BADINPUT, nothing created, id 0); thread_set_cpu takes a spec
or a plain core, KEEP / mask 0 keep, a negative spec changes nothing; the main thread starts at FNVNX_DEFAULT_CORE. Every number is read by name
from the headers. The machine is built on the synthetic ELF of test_bootlayout; the calling thread is set the way run() sets main. Exit 0 on pass."""
import json
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
from test_bootlayout import make_elf  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(VERIFY)))
INC = os.path.join(REPO, "ports", "fnv-nx", "hostabi", "include", "fnvnx")
FAILED, N = [], [0]


def case(name, cond, extra=""):
    N[0] += 1
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:300]))
    if not cond:
        FAILED.append(name)


def define(name):
    for h in ("hostapi.h", "placement.h"):
        m = re.search(r"#define\s+%s\s+(0x[0-9A-Fa-f]+|\d+)(?:ull|u)?\b" % name, open(os.path.join(INC, h)).read())
        if m:
            return int(m.group(1), 0)
    return None


def main():
    D, APP, APP3, KEEP, FN, BAD, NOTF = (define(n) for n in ("FNVNX_DEFAULT_CORE", "FNVNX_APP_CORES", "FNVNX_APP_CORES_CORE3", "FNVNX_CPU_KEEP",
                                                             "FNVNX_PLAT_THREAD_CPU", "FNVNX_HE_BADINPUT", "FNVNX_HE_NOTFOUND"))
    case("headers: default core, app cores, KEEP, THREAD_CPU by name", None not in (D, APP, APP3, KEEP, FN) and FN == 38, (D, APP, APP3, KEEP, FN))
    case("machine constants equal the headers", (bootmachine.FNVNX_DEFAULT_CORE, bootmachine.FNVNX_APP_CORES, bootmachine.FNVNX_APP_CORES_CORE3,
                                                 bootmachine.FNVNX_CPU_KEEP) == (D, APP, APP3, KEEP))
    case("BADINPUT 0x1759 and NOTFOUND by name", BAD == bootmachine.LIBNX_BAD_INPUT and (NOTF or bootmachine.FNVNX_HE_NOTFOUND) == bootmachine.FNVNX_HE_NOTFOUND)

    def SPEC(ideal, mask):
        return (1 << 63) | (mask << 8) | (ideal & 0xFF)   # FNVNX_CPU_SPEC

    elf = make_elf()
    rows = [("keys", 0xf0000, 0x200000), ("exe", 0x400000, 0x1409000)]
    plan = bootlayout.plan(bootlayout.parse_elf(elf), rows, 4 << 20)

    def machine(app):
        svc = boothost.Services(tempfile.mkdtemp(prefix="tc-"))
        m = bootmachine.Machine(elf, plan, svc, app_cores=app)
        m.threads[1] = bootmachine.Thread(1)   # as run() starts main
        m.order, m.cur = [1], m.threads[1]
        return m, svc

    ARGS = 0x400000

    def thread_cpu(m):
        m.uc.mem_write(ARGS, b"")
        return m.ext_call(11, FN, ARGS, 0)

    def create(svc, cpu):
        return svc.h_thread_create(0x1000, 0, 0x10000, 0, cpu)

    for tag, app in (("app cores 0-2", APP), ("core 3 build", APP3)):
        m, svc = machine(app)
        case("[%s] main starts at the default core, mask 1 << default; THREAD_CPU answers it" % tag,
             thread_cpu(m) == D and svc._placed(0) == {"ideal": D, "mask": 1 << D}, svc._placed(0))
        for c in range(4):
            before = len(m.threads)
            h = create(svc, SPEC(c, 1 << c))
            ok = c < 3 or app == APP3
            case("[%s] thread_create on core %d %s" % (tag, c, "is placed" if ok else "is refused (not an app core): id 0, nothing created"),
                 (h != 0 and len(m.threads) == before + 1) if ok else (h == 0 and len(m.threads) == before), (h, len(m.threads)))
            if ok:
                t = m.ids.get("thread", h)
                saved, m.cur = m.cur, t
                case("[%s] THREAD_CPU of that thread is %d" % (tag, c), thread_cpu(m) == c and svc.placement[t.tid]["mask"] == 1 << c)
                m.cur = saved
        before = len(m.threads)
        h = create(svc, SPEC(1, 0x7 if app else 0))
        t = m.ids.get("thread", h)
        case("[%s] a multi-core mask keeps the ideal core: ideal 1, mask 7" % tag, h != 0 and svc.placement[t.tid] == {"prio": 0, "ideal": 1, "mask": 7})
        refused = {"-1 (the old default form)": ~0 & (2**64 - 1), "plain core 1": 1, "role form 0x101": 0x101, "ideal not in mask": SPEC(0, 0x6),
                   "mask 0": SPEC(0, 0), "KEEP as ideal": SPEC(KEEP, 0x7), "mask outside the app cores": SPEC(0, 0x7 | 1 << 4),
                   "ideal 32": SPEC(32, 1 << 31), "bits 40..62 set": SPEC(0, 1) | (1 << 41), "ideal 5 in mask 0x20": SPEC(5, 0x20)}
        n0 = len(m.threads)
        for name, cpu in refused.items():
            h = create(svc, cpu)
            case("[%s] refused: %s -> id 0, nothing created" % (tag, name), h == 0 and len(m.threads) == n0, (hex(cpu), h))
        # thread_set_cpu on main (id 0 = the caller)
        p = lambda: dict(svc._placed(0))
        case("[%s] set_cpu unknown id -> NOTFOUND" % tag, svc.h_thread_set_cpu(0x12345, SPEC(0, 1)) == bootmachine.FNVNX_HE_NOTFOUND)
        case("[%s] set_cpu plain core 2 -> that core only" % tag, svc.h_thread_set_cpu(0, 2) == 0 and p()["ideal"] == 2 and p()["mask"] == 4
             and thread_cpu(m) == 2)
        keep = p()
        case("[%s] set_cpu negative spec -> 0, nothing changed" % tag, svc.h_thread_set_cpu(0, ~0 & (2**64 - 1)) == 0 and p() == keep
             and svc.h_thread_set_cpu(0, 0x7FFFFF0000000000) == 0 and p() == keep)
        case("[%s] set_cpu KEEP ideal with a new mask: the ideal stays and joins the mask" % tag,
             svc.h_thread_set_cpu(0, SPEC(KEEP, 0x3)) == 0 and p() == {"ideal": 2, "mask": 0x7})
        case("[%s] set_cpu mask 0 keeps the mask, new ideal joins it" % tag, svc.h_thread_set_cpu(0, SPEC(1, 0)) == 0 and p() == {"ideal": 1, "mask": 0x7}
             and thread_cpu(m) == 1)
        case("[%s] set_cpu KEEP and mask 0 keep everything" % tag, svc.h_thread_set_cpu(0, SPEC(KEEP, 0)) == 0 and p() == {"ideal": 1, "mask": 0x7})
        keep = p()
        case("[%s] set_cpu plain core 5 (not an app core) -> BADINPUT, unchanged" % tag, svc.h_thread_set_cpu(0, 5) == BAD and p() == keep)
        case("[%s] set_cpu plain core 64 -> BADINPUT, unchanged" % tag, svc.h_thread_set_cpu(0, 64) == BAD and p() == keep)
        case("[%s] set_cpu mask outside the app cores -> BADINPUT, unchanged" % tag, svc.h_thread_set_cpu(0, SPEC(KEEP, 1 << 4)) == BAD
             and p() == keep)
        r3 = svc.h_thread_set_cpu(0, 3)
        case("[%s] set_cpu plain core 3 %s" % (tag, "is accepted" if app == APP3 else "-> BADINPUT, unchanged"),
             (r3 == 0 and p() == {"ideal": 3, "mask": 8}) if app == APP3 else (r3 == BAD and p() == keep), (r3, p()))
        case("[%s] set_cpu ideal 3 joining the mask %s" % (tag, "is accepted" if app == APP3 else "-> BADINPUT"),
             (svc.h_thread_set_cpu(0, SPEC(3, 0x3)) == 0 and p() == {"ideal": 3, "mask": 0xB}) if app == APP3
             else svc.h_thread_set_cpu(0, SPEC(3, 0x3)) == BAD)
        case("[%s] fn 39 -> NO_SERVICE (header bound)" % tag, thread_cpu(m) is not None and m.ext_call(11, 39, ARGS, 0) == 0xFFFFFFFFFFFFFF02)

    # app_cores_of: the recipe's own flags
    d = tempfile.mkdtemp(prefix="ac-")

    def root(flags):
        json.dump({"flags": flags}, open(os.path.join(d, "build.json"), "w"))
        return bootmachine.app_cores_of(d)

    case("app_cores_of: no define -> 0-2", root({"c": ["-O2"], "cxx": [], "asm": []}) == APP)
    case("app_cores_of: -DFNVNX_CORE3_DEFAULT=1 -> with core 3", root({"c": ["-DFNVNX_CORE3_DEFAULT=1"], "cxx": [], "asm": []}) == APP3)
    case("app_cores_of: -DFNVNX_CORE3_DEFAULT (bare) -> with core 3", root({"c": ["-DFNVNX_CORE3_DEFAULT"], "cxx": [], "asm": []}) == APP3)
    case("app_cores_of: -DFNVNX_CORE3_DEFAULT=0 -> 0-2", root({"c": ["-DFNVNX_CORE3_DEFAULT=0"], "cxx": [], "asm": []}) == APP)
    case("app_cores_of: the define in the cxx flags counts too", root({"c": [], "cxx": ["-DFNVNX_CORE3_DEFAULT=1"], "asm": []}) == APP3)
    json.dump({"flags": {"c": [], "cxx": [], "asm": []}, "variants": {"v": {"flags": {"c": ["-DFNVNX_CORE3_DEFAULT=1"], "cxx": [], "asm": []}}}},
              open(os.path.join(d, "build.json"), "w"))
    case("app_cores_of: a named variant's own flags decide", bootmachine.app_cores_of(d, "v") == APP3 and bootmachine.app_cores_of(d) == APP)
    case("the bootguest root builds without the core 3 opt-in", bootmachine.app_cores_of(os.path.join(REPO, "ports", "fnv-nx", "tests", "bootguest")) == APP)
    print("%d cases, %d failed" % (N[0], len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
