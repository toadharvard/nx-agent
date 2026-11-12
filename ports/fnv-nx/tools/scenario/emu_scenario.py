#!/usr/bin/env python3
"""emu_scenario.py - the guest side of an S18 scenario: tests/s5/emu_guest.py with scripted, deterministic input.

    emu_scenario.py --scenario PLAN.json [--scenario-out DIR] [--frames] <emu_guest.py arguments...>

PLAN.json is `scenario.py compile` output (fnvnx-scenario-plan/1): primitive steps press / wait / until / mark. They
run on the emulator's virtual time (emu_guest's tick: it advances with the guest's sleeps, waits and timers), so a
run is the same every time for the same guest. Keys go to the guest as FNVNX_EV_KEY events through the host API's
input_poll (HID usages, tools/scenario/keys.py), exactly where the console host feeds the keyboard; the guest's
platform maps them to DirectInput codes (platform/guest/platform_glue.cpp).

  press  key down, key up after half the gap, the next step after the gap (seconds)
  wait   the next step after s seconds
  until  milestone M (emu_guest's milestone table) or file F (a path under the SD card, matched case-insensitively
         at any depth: "Saves/s18_rt_a.fos" finds the profile's save) - or the step's timeout, which is recorded as a
         gate timeout (a hang on the scenario path) and the plan goes on
  mark   a named checkpoint (virtual time, host calls)
After the last step the run ends: "STOPPED: scenario complete" (the CPU is stopped in the host call and the
scheduler's next pick raises SystemExit, which emu_guest reports with the full summary). The steps are checked at every input_poll, pad_read, sleep_ns, thread_yield and
wall_time_us call, so a game that never polls input still runs its wait/until timeouts.

The boot summary (--summary-json) gets a "scenario" object (fnvnx-scenario-guest/1): the steps reached, the marks,
gate timeouts, the keys sent and the unmapped ones, the guest's "[fnvnx]" console lines with counts, the SD root,
and with --frames the instructions between frames (Present calls) of the main thread, for D25 #12.
--scenario-out DIR: a seed tree DIR/seed (profile inis, plugins.txt: scenario.py seed) is copied onto the SD root
first (--root, or DIR/sd without one), and the scenario's own files (s18_*: saves, the scof log) are copied to
DIR/kept when the summary is written - farm_boot.sh deletes its root after the boot. The summary's root is DIR/kept.
SCENARIO_PROGRESS_S (default 600; 0 off): tools/perf/emu_progress.py's hang detector, when that file exists.
SCENARIO_CALLWATCH=<config>: tools/perf/callwatch.py on the game's script-error entry points (private config).

Our own code; it reads emu_guest's state only through the names emu_guest defines.
"""
from __future__ import annotations

import collections
import json
import os
import re
import shutil
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.dirname(os.path.dirname(HERE))
S5 = os.environ.get("SCENARIO_EMU_DIR", os.path.join(P, "tests", "s5"))
sys.path.insert(0, HERE)
sys.path.insert(0, S5)
import keys  # noqa: E402

FNVNX_EV_KEY = 1
EV = struct.Struct("<IIiiiI")   # FnvnxInputEvent (toolchain/include/fnvnx/hostapi.h)
FRAME_RE = r"^_ZN\d+(Null_I|Px)Direct3D(Device|SwapChain)9(Ex)?\d+Present(Ex)?E"   # milestones.txt first-frame
FNVNX_LINE = re.compile(r"\[fnvnx\][^\n]*")


class Driver:
    """the plan's state machine on a clock (ns) and two probes: milestone reached?, file present?"""

    def __init__(self, plan):
        self.steps = plan["steps"]
        self.k, self.t_step, self.started = 0, None, None
        self.queue = []           # (due ns, hid usage, down)
        self.marks, self.timeouts, self.sent, self.unmapped = [], [], 0, collections.Counter()
        self.done = False
        self.log = []             # (ns, step index, what)

    @staticmethod
    def ns(s):
        return int(float(s) * 1_000_000_000)

    def advance(self, now, reached, has_file, calls=0):
        """run every step that is due at now; returns True when the plan has ended"""
        if self.started is None:
            self.started = self.t_step = now
        while not self.done:
            if self.k >= len(self.steps):
                self.done = True
                self.log.append((now, self.k, "end"))
                break
            s = self.steps[self.k]
            op = s["op"]
            if now < self.t_step:
                break                          # the previous step's gap is still running
            if op == "press":
                gap = self.ns(s.get("gap", 0.25))
                try:
                    evs = keys.press_events(s["key"])
                except KeyError:
                    self.unmapped[s["key"]] += 1
                    evs = []
                downs = [e for e in evs if e[1]]
                ups = [e for e in evs if not e[1]]
                for usage, d in downs:
                    self.queue.append((now, usage, d))
                for usage, d in ups:
                    self.queue.append((now + gap // 2, usage, d))
                self.sent += 1
                self.k, self.t_step = self.k + 1, now + gap
                self.log.append((now, self.k - 1, "press " + s["key"]))
                if gap:
                    break
                continue
            if op == "wait":
                due = self.t_step + self.ns(s.get("s", 0))
                if now < due:
                    break
                self.k, self.t_step = self.k + 1, due
                continue
            if op == "until":
                ok = (reached(s["milestone"]) if "milestone" in s else has_file(s["file"]))
                if ok:
                    self.log.append((now, self.k, "until ok " + (s.get("milestone") or s.get("file"))))
                elif now >= self.t_step + self.ns(s.get("timeout", 600)):
                    self.timeouts.append({"step": self.k, "what": s.get("milestone") or s.get("file"),
                                          "seconds": s.get("timeout", 600)})
                    self.log.append((now, self.k, "until TIMEOUT " + (s.get("milestone") or s.get("file"))))
                else:
                    break
                self.k, self.t_step = self.k + 1, now
                continue
            if op == "mark":
                self.marks.append({"name": s["name"], "step": self.k, "t_s": round((now - self.started) / 1e9, 3),
                                   "host_calls": calls})
                self.k += 1
                continue
            self.log.append((now, self.k, "unknown op " + op))   # compile never writes one: skip it
            self.k += 1
        return self.done

    def take_events(self, now, cap):
        """the queued key events that are due, at most cap"""
        due = [e for e in self.queue if e[0] <= now][:cap]
        for e in due:
            self.queue.remove(e)
        return due


def find_file(root, rel):
    """rel ('Saves/x.fos') under root at any depth, every component case-insensitive"""
    want = [p.lower() for p in rel.replace("\\", "/").split("/") if p]
    for d, _dirs, files in os.walk(root, followlinks=False):
        parts = [p.lower() for p in os.path.relpath(d, root).replace("\\", "/").split("/") if p != "."]
        for f in files:
            if (parts + [f.lower()])[-len(want):] == want:
                return os.path.join(d, f)
    return None


def install(eg, plan, out_dir, frames, game_dir=None):
    """patch emu_guest.Emu: the input feed, the step checks, the summary"""
    Emu = eg.Emu
    drv = Driver(plan)
    state = {"emu": None, "frames": [], "icount": 0, "last_frame": None, "main_tid": None}

    def tick(emu):
        state["emu"] = emu
        root = getattr(emu, "root", None) or "."
        ended = drv.done or drv.advance(emu.now_ns(), lambda m: m in getattr(emu, "miles", {}),
                                        lambda f: find_file(root, f) is not None, getattr(emu, "calls", 0))
        if ended and not drv.queue and not getattr(emu, "_s18_done", False):
            # inside a Unicorn hook: stop the CPU; the scheduler's next pick ends the run (below)
            emu._s18_done = True
            try:
                emu.uc.emu_stop()
            except Exception:
                pass

    def wrap(name, before=True):
        orig = getattr(Emu, name, None)
        if orig is None:
            return

        def f(self, *a, **k):
            tick(self)
            return orig(self, *a, **k)
        setattr(Emu, name, f)

    for n in ("h_pad_read", "h_sleep_ns", "h_thread_yield", "h_wall_time_us"):
        wrap(n)
    orig_pick = Emu.pick

    def pick(self, *a, **k):
        tick(self)
        if getattr(self, "_s18_done", False):
            raise SystemExit("scenario complete")
        return orig_pick(self, *a, **k)
    Emu.pick = pick
    orig_poll = Emu.h_input_poll

    def h_input_poll(self, buf=0, cap=0, *a):
        tick(self)
        evs = drv.take_events(self.now_ns(), min(int(cap), 64)) if buf and cap else []
        for i, (_t, usage, down) in enumerate(evs):
            self.uc.mem_write(buf + i * EV.size, EV.pack(FNVNX_EV_KEY, usage, down, 0, 0, 0))
        if evs:
            return len(evs)
        return orig_poll(self, buf, cap, *a)
    Emu.h_input_poll = h_input_poll

    if frames:
        orig_mile = Emu.install_milestones

        def install_milestones(self, path):
            orig_mile(self, path)
            self._s18_install_frames()
        Emu.install_milestones = install_milestones

        def _s18_install_frames(self):
            from unicorn import UC_HOOK_BLOCK, UC_HOOK_CODE
            rx = re.compile(FRAME_RE)
            addrs = sorted({self.code_addr(n) for n in getattr(self, "code_syms", {}) if rx.search(n)} - {None})

            def on_block(uc, addr, size, _):
                if self.cur is not None and self.cur.tid == state["main_tid"]:
                    state["icount"] += size // 4

            def on_present(uc, addr, size, _):
                tid = self.cur.tid if self.cur else None
                if state["main_tid"] is None:
                    state["main_tid"] = tid
                    self.uc.hook_add(UC_HOOK_BLOCK, on_block)   # counts from the first frame on
                elif tid == state["main_tid"]:
                    state["frames"].append(state["icount"])
                    state["icount"] = 0
            for a in addrs:
                self.uc.hook_add(UC_HOOK_CODE, on_present, begin=a, end=a)
            print(f"   scenario frames: {len(addrs)} Present entries hooked")
        Emu._s18_install_frames = _s18_install_frames

    def keep(emu):
        """copy the scenario's own files (s18_* saves and logs) out of the SD root, which farm_boot.sh deletes"""
        root = getattr(emu, "root", None)
        if not out_dir or not root or not os.path.isdir(root):
            return root
        dst = os.path.join(out_dir, "kept")
        for d, _dirs, files in os.walk(root):
            for f in files:
                if f.lower().startswith("s18_"):
                    rel = os.path.relpath(os.path.join(d, f), root)
                    os.makedirs(os.path.dirname(os.path.join(dst, rel)), exist_ok=True)
                    shutil.copy2(os.path.join(d, f), os.path.join(dst, rel))
        # the game's own directory (--game, mounted at sdmc:/switch/fnv): the console's scof file lands at its top
        if game_dir and os.path.isdir(game_dir):
            for f in os.listdir(game_dir):
                if f.lower().startswith("s18_") and os.path.isfile(os.path.join(game_dir, f)):
                    os.makedirs(os.path.join(dst, "switch", "fnv"), exist_ok=True)
                    shutil.copy2(os.path.join(game_dir, f), os.path.join(dst, "switch", "fnv", f))
        os.makedirs(dst, exist_ok=True)
        return dst

    orig_summary = Emu.summary

    def summary(self, stop, rc=None):
        s = orig_summary(self, stop, rc)
        text = self.console.decode(errors="replace")
        lines = collections.Counter(m.group(0).strip()[:300] for m in FNVNX_LINE.finditer(text))
        s["scenario"] = {
            "schema": "fnvnx-scenario-guest/1", "id": plan.get("id"), "steps": len(drv.steps),
            "steps_done": drv.k, "complete": drv.done, "marks": drv.marks, "gate_timeouts": drv.timeouts,
            "keys_sent": drv.sent, "keys_unmapped": dict(drv.unmapped),
            "virtual_s": round(self.now_ns() / 1e9, 3), "root": keep(self),
            "fnvnx_lines": [{"line": l, "n": n} for l, n in lines.most_common(500)],
            "frames": {"measured": frames, "instructions": state["frames"][:20000]} if frames else {"measured": False},
            "log": [[round(t / 1e9, 3), k, w] for t, k, w in drv.log[-400:]],
        }
        return s
    Emu.summary = summary
    return drv


def split_args(argv):
    """(our options, emu_guest's argv)"""
    ours, rest, i = {"frames": False}, [], 0
    while i < len(argv):
        a = argv[i]
        if a in ("--scenario", "--scenario-out"):
            ours[a[2:].replace("-", "_")] = argv[i + 1]
            i += 2
            continue
        if a.startswith(("--scenario=", "--scenario-out=")):
            k, v = a[2:].split("=", 1)
            ours[k.replace("-", "_")] = v
        elif a == "--frames":
            ours["frames"] = True
        else:
            rest.append(a)
        i += 1
    return ours, rest


def main(argv):
    ours, rest = split_args(argv)
    if "scenario" not in ours:
        print(__doc__)
        return 2
    plan = json.load(open(ours["scenario"]))
    out = ours.get("scenario_out")
    if out:
        root = next((rest[i + 1] for i, a in enumerate(rest[:-1]) if a == "--root"), None) or \
            next((a.split("=", 1)[1] for a in rest if a.startswith("--root=")), None)
        if root is None:
            root = os.path.join(out, "sd")
            rest = ["--root", root] + rest
        os.makedirs(root, exist_ok=True)
        seed = os.path.join(out, "seed")
        if os.path.isdir(seed):
            shutil.copytree(seed, root, dirs_exist_ok=True)
    game = next((rest[i + 1] for i, a in enumerate(rest[:-1]) if a == "--game"), None)
    if game and os.path.isdir(game):   # an earlier run's files at the game's top level must not count for this one
        for f in os.listdir(game):
            if f.lower().startswith("s18_") and os.path.isfile(os.path.join(game, f)):
                os.remove(os.path.join(game, f))
    import emu_guest as eg   # noqa: E402  (tests/s5, or SCENARIO_EMU_DIR)
    install(eg, plan, out, ours["frames"], game)
    # R4's hang detector (tools/perf/emu_progress.py): no milestone, new function or console line for
    # SCENARIO_PROGRESS_S wall seconds ends the run as "hang: ..." with a stack per thread (0 turns it off)
    prog = float(os.environ.get("SCENARIO_PROGRESS_S", 600))
    if prog > 0 and os.path.isfile(os.path.join(P, "tools", "perf", "emu_progress.py")):
        sys.path.insert(0, os.path.join(P, "tools", "perf"))
        import emu_progress   # noqa: E402
        emu_progress.install(eg, prog)
    # R4's call watcher (tools/perf/callwatch.py, D25 #10): the game's script / console error entry points from a
    # private config (game knowledge: never in this repository), logged as "emu: call <kind> caller ... form 0x..."
    cw = os.environ.get("SCENARIO_CALLWATCH", "")
    if cw and os.path.isfile(cw) and os.path.isfile(os.path.join(P, "tools", "perf", "callwatch.py")):
        sys.path.insert(0, os.path.join(P, "tools", "perf"))
        import callwatch   # noqa: E402
        callwatch.install(eg, cw)
    return eg.main(rest)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
