#!/usr/bin/env python3
"""emu_progress.py - the hang detector for long emulator runs (tests/s5/emu_guest.py), the twin of linhost's
--progress-timeout (tests/s15/mtqemu/linhost.c): a run that makes no progress for S seconds of wall time writes a stack
per thread and ends, instead of burning the farm's hour.

    emu_progress.py --progress-timeout S <emu_guest.py arguments...>
    # or, from another wrapper (tools/scenario/emu_scenario.py):  emu_progress.install(emu_guest, S)

Progress: a milestone reached (emu.miles grows), a function seen for the first time at a scheduling point (the
function of the running thread's pc at every Emu.pick, every input_poll / pad_read / sleep_ns / thread_yield /
wall_time_us host call), or a new line on the guest's console. On a hang: "== hang: no progress for S s (last: <what>
<t> s ago)", one "   thread <tid> <state> pc <sym> frames <sym> ..." line per thread (each thread's saved Unicorn
context is restored only to read it), then SystemExit("hang: no progress for S s"), which emu_guest reports as its
STOPPED reason with the full summary (the fault classifier tools/report/faults.py reads it as kind hang).

Our own code; it reads emu_guest's state only through the names emu_guest defines (pick, threads, miles, uc, sym,
console, cur).
"""
from __future__ import annotations

import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.dirname(os.path.dirname(HERE))
S5 = os.environ.get("SCENARIO_EMU_DIR", os.path.join(P, "tests", "s5"))

PC, FP = None, None   # Unicorn register ids, set by install


class Watch:
    def __init__(self, timeout_s, clock=time.monotonic):
        self.timeout, self.clock = float(timeout_s), clock
        self.last, self.what = clock(), "start"
        self.funcs, self.miles, self.console = set(), 0, 0
        self.fired = False

    def note(self, what):
        self.last, self.what = self.clock(), what

    def observe(self, emu):
        """one look at the emulator; True when the run is to end as a hang"""
        miles = len(getattr(emu, "miles", {}) or {})
        if miles > self.miles:
            self.miles = miles
            self.note("milestone")
        con = len(getattr(emu, "console", b"") or b"")
        if con > self.console:
            self.console = con
            self.note("console")
        try:
            pc = emu.uc.reg_read(PC)
            f = func_of(emu, pc)
        except Exception:
            f = None
        if f is not None and f not in self.funcs:
            self.funcs.add(f)
            self.note("new function")
        return not self.fired and self.clock() - self.last >= self.timeout


def func_of(emu, pc):
    """the function a pc is in: its symbol without the offset (emu.sym: '0x.. name [orig ..]+0x..')"""
    s = emu.sym(pc) if hasattr(emu, "sym") else "%#x" % pc
    head, _, _ = s.rpartition("+")
    return head or s


def frames(emu, fp, n=16):
    out = []
    for _ in range(n):
        if not fp or fp & 7:
            break
        try:
            nfp, ret = struct.unpack("<QQ", emu.uc.mem_read(fp, 16))
        except Exception:
            break
        out.append(emu.sym(ret) if hasattr(emu, "sym") else "%#x" % ret)
        if nfp <= fp:
            break
        fp = nfp
    return out


def dump(emu, w):
    """the hang lines: every thread's pc and frame chain"""
    lines = ["== hang: no progress for %g s (last: %s %.1f s ago)" % (w.timeout, w.what, w.clock() - w.last)]
    uc = emu.uc
    try:
        here = uc.context_save()
    except Exception:
        here = None
    cur = getattr(getattr(emu, "cur", None), "tid", None)
    for tid, t in sorted((getattr(emu, "threads", {}) or {}).items()):
        try:
            if tid != cur and getattr(t, "ctx", None) is not None:
                uc.context_restore(t.ctx)
            elif here is not None:
                uc.context_restore(here)
            pc, fp = uc.reg_read(PC), uc.reg_read(FP)
            lines.append("   thread %s %s pc %s frames %s" % (tid, getattr(t, "state", "?"),
                                                            emu.sym(pc) if hasattr(emu, "sym") else "%#x" % pc,
                                                            " | ".join(frames(emu, fp)) or "-"))
        except Exception as e:
            lines.append("   thread %s: unreadable (%s)" % (tid, e))
    if here is not None:
        try:
            uc.context_restore(here)
        except Exception:
            pass
    return lines


def install(eg, timeout_s, clock=time.monotonic):
    """patch emu_guest.Emu: checks at every scheduling point and the polling host calls"""
    global PC, FP
    try:
        from unicorn.arm64_const import UC_ARM64_REG_PC, UC_ARM64_REG_X29
        PC, FP = UC_ARM64_REG_PC, UC_ARM64_REG_X29
    except ImportError:   # tests with a stand-in emulator
        PC, FP = "pc", "x29"
    Emu = eg.Emu
    w = Watch(timeout_s, clock)

    def check(emu):
        if w.observe(emu):
            w.fired = True
            for line in dump(emu, w):
                print(line)
            raise SystemExit("hang: no progress for %g s (last: %s)" % (w.timeout, w.what))

    def wrap(name):
        orig = getattr(Emu, name, None)
        if orig is None:
            return

        def f(self, *a, **k):
            check(self)
            return orig(self, *a, **k)
        setattr(Emu, name, f)

    for n in ("pick", "h_input_poll", "h_pad_read", "h_sleep_ns", "h_thread_yield", "h_wall_time_us"):
        wrap(n)
    return w


def split_args(argv):
    ours, rest, i = {}, [], 0
    while i < len(argv):
        if argv[i] == "--progress-timeout" and i + 1 < len(argv):
            ours["timeout"] = float(argv[i + 1])
            i += 2
            continue
        if argv[i].startswith("--progress-timeout="):
            ours["timeout"] = float(argv[i].split("=", 1)[1])
        else:
            rest.append(argv[i])
        i += 1
    return ours, rest


def main(argv):
    ours, rest = split_args(argv)
    if "timeout" not in ours:
        print(__doc__)
        return 2
    sys.path.insert(0, S5)
    import emu_guest as eg   # noqa: E402
    install(eg, ours["timeout"])
    return eg.main(rest)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
