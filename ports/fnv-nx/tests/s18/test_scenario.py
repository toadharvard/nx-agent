#!/usr/bin/env python3
"""tests/s18 - the scenario bench (tools/scenario, tools/farm/scenario_job.sh) on synthetic inputs only: a stand-in
emulator class, a stand-in xdotool, made-up saves and a made-up plugin. No game data, no guest, no Wine.

    python3 -I tests/s18/test_scenario.py
"""
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.dirname(os.path.dirname(HERE))
SC = os.path.join(P, "tools", "scenario")
sys.path.insert(0, SC)
import keys          # noqa: E402
import savediff      # noqa: E402
import scenario      # noqa: E402
import emu_scenario  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f": {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def t_keys():
    check("keys: text with shift", keys.text_keys("coc A_b") == ["c", "o", "c", "space", "shift+a", "shift+minus", "b"])
    ev = keys.press_events("shift+minus")
    check("keys: shift held around the key", ev == [(0xE1, 1), (0x2D, 1), (0x2D, 0), (0xE1, 0)], str(ev))
    check("keys: grave is HID 0x35", keys.hid("grave") == 0x35)


def t_compile():
    d = scenario.load_file(scenario.SCENARIOS)
    ids = [s["id"] for s in d["scenarios"]]
    check("compile: five scenarios", ids == ["boot-menu", "newgame", "cells", "saveload", "quest"], str(ids))
    for s in d["scenarios"]:
        plan = scenario.compile_plan(d, s, allow_unresolved=True)
        ops = {st["op"] for st in plan["steps"]}
        check(f"compile {s['id']}: primitive ops only", ops <= {"press", "wait", "until", "mark"}, str(ops))
        check(f"compile {s['id']}: every key maps to a DIK", not scenario.unmapped_keys(plan))
        check(f"compile {s['id']}: first step waits for the menu", plan["steps"][0].get("milestone") == "main-menu")
    sl = scenario.compile_plan(d, scenario.scenario(d, "saveload"), allow_unresolved=True)
    files = [st["file"] for st in sl["steps"] if st["op"] == "until" and "file" in st]
    check("compile saveload: both saves gated on their file", files == ["Saves/s18_rt_a.fos", "Saves/s18_rt_b.fos"], str(files))
    check("compile saveload: round trip named", sl["roundtrip"] == ["s18_rt_a", "s18_rt_b"])
    # console macro: open, type, Return, close - except after a cell load
    mini = {"format": "fnvnx-scenarios/1", "defaults": {"key_gap": 0.1}, "state": [],
            "scenarios": [{"id": "x", "steps": [{"do": "console", "lines": ["tgm"]},
                                                {"do": "console", "lines": ["coc Foo"]}]}]}
    pl = scenario.compile_plan(mini, mini["scenarios"][0])
    ks = [st["key"] for st in pl["steps"] if st["op"] == "press"]
    check("compile: console open/close", ks[:6] == ["grave", "t", "g", "m", "Return", "grave"], str(ks[:6]))
    check("compile: no close after coc", ks[-1] == "Return" and ks[6] == "grave", str(ks[6:]))
    check("compile: coc cells listed", pl["cells"] == ["Foo"])
    try:
        scenario.compile_plan(d, scenario.scenario(d, "quest"))
        check("compile quest without --esm fails", False)
    except SystemExit:
        check("compile quest without --esm fails", True)


def make_esm(path, quests):
    """a minimal TES4 plugin: header + one QUST group (EDID, FULL, INDX...)"""
    def sub(t, b):
        return t + struct.pack("<H", len(b)) + b
    def rec(t, fid, body, compressed=False):
        flags = 0
        if compressed:
            body = struct.pack("<I", len(body)) + zlib.compress(body)
            flags = 0x40000
        return t + struct.pack("<III", len(body), flags, fid) + b"\0" * 8 + body
    hdr = rec(b"TES4", 0, sub(b"HEDR", struct.pack("<fII", 1.34, len(quests), 0x800)))
    recs = b""
    for k, (ed, full, stages) in enumerate(quests):
        body = sub(b"EDID", ed.encode() + b"\0") + sub(b"FULL", full.encode() + b"\0")
        for st in stages:
            body += sub(b"INDX", struct.pack("<h", st)) + sub(b"CNAM", b"log\0")
        recs += rec(b"QUST", 0x100 + k, body, compressed=(k == 1))
    grp = b"GRUP" + struct.pack("<I4si", 24 + len(recs), b"QUST", 0) + b"\0" * 8 + recs
    open(path, "wb").write(hdr + grp)


def t_quest(tmp):
    esm = os.path.join(tmp, "Test.esm")
    make_esm(esm, [("TQ00", "Some Other Quest", [5]), ("TQ01", "Ain't That a Kick in the Head", [0, 10, 20, 30, 40, 50, 60])])
    q = scenario.quest_info(esm, "ain't that a kick in the head")
    check("quest: resolved from a compressed record", q == ("TQ01", [0, 10, 20, 30, 40, 50, 60]), str(q))
    d = scenario.load_file(scenario.SCENARIOS)
    plan = scenario.compile_plan(d, scenario.scenario(d, "quest"), esm)
    marks = [st["name"] for st in plan["steps"] if st["op"] == "mark"]
    check("quest: six stages set", [m for m in marks if m.startswith("stage:")] ==
          [f"stage:{k}" for k in (0, 10, 20, 30, 40, 50)], str(marks))
    check("quest: plan records the quest", plan["quests"][0]["edid"] == "TQ01")


def t_driver():
    plan = {"steps": [{"op": "press", "key": "grave", "gap": 1.0}, {"op": "wait", "s": 2},
                      {"op": "until", "milestone": "main-menu", "timeout": 10}, {"op": "mark", "name": "menu"},
                      {"op": "until", "file": "Saves/x.fos", "timeout": 5}, {"op": "mark", "name": "end"}]}
    d = emu_scenario.Driver(plan)
    S = 1_000_000_000
    miles, files = set(), set()
    adv = lambda t: d.advance(t, lambda m: m in miles, lambda f: f in files)
    adv(0)
    check("driver: key down queued at once", d.take_events(0, 64) == [(0, 0x35, 1)])
    check("driver: key up after half the gap", d.take_events(S // 2, 64) == [(S // 2, 0x35, 0)])
    adv(2 * S)
    check("driver: wait holds", d.k == 1, str(d.k))
    adv(3 * S)
    check("driver: until waits for the milestone", d.k == 2)
    miles.add("main-menu")
    adv(4 * S)
    check("driver: mark after the milestone", [m["name"] for m in d.marks] == ["menu"] and d.k == 4)
    adv(10 * S)
    check("driver: file gate times out", d.timeouts and d.timeouts[0]["what"] == "Saves/x.fos", str(d.timeouts))
    check("driver: plan ends", d.done and [m["name"] for m in d.marks] == ["menu", "end"])


def t_emu_wrapper(tmp):
    """install() on a stand-in emu_guest module: events reach guest memory, the run ends through pick"""
    class Mem:
        def __init__(self):
            self.w = {}
        def mem_write(self, a, b):
            self.w[a] = bytes(b)
        def emu_stop(self):
            self.stopped = True

    class Emu:
        def __init__(self):
            self.uc, self.t, self.miles, self.calls, self.console = Mem(), 0, {"main-menu": {}}, 0, b"[fnvnx] ShellExecuteA(open, x) not available\n"
            self.root = os.path.join(tmp, "sd")
        def now_ns(self):
            return self.t
        def h_input_poll(self, *_):
            return 0
        def h_pad_read(self, *_):
            return 0
        def h_sleep_ns(self, *_):
            return 0
        def h_thread_yield(self, *_):
            return 0
        def h_wall_time_us(self, *_):
            return 0
        def pick(self):
            return None
        def install_milestones(self, path):
            pass
        def summary(self, stop, rc=None):
            return {"stop": {"reason": stop}}
    os.makedirs(os.path.join(tmp, "sd", "switch", "fnv", "user"), exist_ok=True)
    open(os.path.join(tmp, "sd", "switch", "fnv", "user", "s18_console.txt"), "w").write("hello\n")
    eg = types.SimpleNamespace(Emu=Emu)
    plan = {"id": "t", "steps": [{"op": "until", "milestone": "main-menu", "timeout": 5},
                                 {"op": "press", "key": "a", "gap": 0.0}, {"op": "mark", "name": "m"}]}
    out = os.path.join(tmp, "out")
    emu_scenario.install(eg, plan, out, frames=False)
    e = Emu()
    n = e.h_input_poll(0x1000, 64)
    check("wrapper: two key events written", n == 2 and len(e.uc.w) == 2, f"{n} {e.uc.w}")
    ev = struct.unpack("<IIiiiI", e.uc.w[0x1000])
    check("wrapper: FNVNX_EV_KEY a down", ev[:3] == (1, 0x04, 1), str(ev))
    try:
        e.pick()
        check("wrapper: pick ends the run after the plan", False)
    except SystemExit as x:
        check("wrapper: pick ends the run after the plan", "scenario complete" in str(x))
    s = e.summary("STOPPED: scenario complete")
    sc = s.get("scenario", {})
    check("wrapper: summary has the scenario", sc.get("complete") and sc.get("marks")[0]["name"] == "m", str(sc)[:200])
    check("wrapper: kept files copied out of the root",
          os.path.isfile(os.path.join(out, "kept", "switch", "fnv", "user", "s18_console.txt")))
    check("wrapper: [fnvnx] lines counted", sc["fnvnx_lines"][0]["n"] == 1)
    ours, rest = emu_scenario.split_args(["x.nso", "--scenario", "p.json", "--frames", "--elf", "e"])
    check("wrapper: own args split off", ours == {"frames": True, "scenario": "p.json"} and rest == ["x.nso", "--elf", "e"])


def fos(body, w=32, h=16, name="Courier", playtime="000.01.02"):
    def bs(s):
        return struct.pack("<H", len(s)) + b"|" + s.encode()
    hdr = b"|".join([struct.pack("<I", 0x30), struct.pack("<I", w), struct.pack("<I", h), struct.pack("<I", 7),
                     bs(name), bs("title"), struct.pack("<I", 1), bs("Goodsprings"), bs(playtime)]) + b"|"
    k = sum(playtime.encode())   # each save has its own picture
    shot = bytes((i * 7 + k) & 0xFF for i in range(w * h * 3))
    return savediff.MAGIC + struct.pack("<I", len(hdr)) + hdr + shot + body


def t_savediff(tmp):
    import random
    rnd = random.Random(1)
    body = bytes(rnd.randrange(256) for _ in range(60000))
    a, b, c, d = (os.path.join(tmp, f"{n}.fos") for n in "abcd")
    open(a, "wb").write(fos(body))
    open(b, "wb").write(fos(body, playtime="000.05.09"))
    r = savediff.compare(a, b)
    check("savediff: header/screenshot ignored", r["diffs"] == 0 and r["identical_body"], r["summary"])
    check("savediff: screenshot recognised", r["header"][0]["screenshot_bytes"] == 32 * 16 * 3, str(r["header"][0]))
    body2 = body[:30000] + b"\x01\x02\x03\x04" + body[30004:]
    open(c, "wb").write(fos(body2))
    r2 = savediff.compare(a, c)
    check("savediff: one changed word is one region", r2["diffs"] == 1 and r2["diff_bytes"] < 9000, r2["summary"])
    body3 = body[:30000] + b"\x09\x09\x09\x09" + body[30004:]
    open(d, "wb").write(fos(body3, name="Other"))
    r3 = savediff.compare(b, d)
    check("savediff: the same place keys the same in another pair", r3["diff_keys"] == r2["diff_keys"],
          f"{r3['diff_keys']} vs {r2['diff_keys']}")
    ins = body[:20000] + b"INSERTED" * 10 + body[20000:]
    open(d, "wb").write(fos(ins))
    r4 = savediff.compare(a, d)
    check("savediff: an insertion stays local", r4["diffs"] == 1 and r4["diff_bytes"] < 9000, r4["summary"])


def t_report(tmp):
    d = scenario.load_file(scenario.SCENARIOS)
    plan = scenario.compile_plan(d, scenario.scenario(d, "saveload"), allow_unresolved=True)
    g = os.path.join(tmp, "rep", "emu")
    kept = os.path.join(g, "kept", "switch", "fnv", "user")
    os.makedirs(os.path.join(kept, "Documents", "My Games", "FalloutNV", "Saves"))
    import random
    body = bytes(random.Random(2).randrange(256) for _ in range(20000))
    sv = os.path.join(kept, "Documents", "My Games", "FalloutNV", "Saves")
    open(os.path.join(sv, "s18_rt_a.fos"), "wb").write(fos(body))
    open(os.path.join(sv, "s18_rt_b.fos"), "wb").write(fos(body[:5000] + b"\xff" * 8 + body[5008:]))
    open(os.path.join(kept, "s18_console.txt"), "w").write("GetPos: X >> 1.0\nSCRIPTS: Script 'Foo' error on line 3\n"
                                                          "Script command \"showtagskillsmenu\" not found.\n")
    summ = {"stop": {"reason": "STOPPED: scenario complete"}, "stubbed_faults": 2, "initializers_skipped": 1,
            "peak_heap_bytes": 500 << 20, "milestone": "first-cell",
            "file_faults": [{"call": "open", "path": "switch/fnv/Custom.ini", "errno": 2},
                            {"call": "open", "path": "switch/fnv/Data/Missing.bsa", "errno": 2}],
            "scenario": {"steps_done": len(plan["steps"]), "complete": True, "marks": [{"name": "in-cell"}],
                         "gate_timeouts": [{"step": 3, "what": "Saves/x", "seconds": 5}],
                         "root": os.path.join(g, "kept"),
                         "fnvnx_lines": [{"line": "[fnvnx] ShellExecuteA(open, x) not available", "n": 1},
                                         {"line": "[fnvnx] d3d9: skipped draw (fvf 0x142)", "n": 3},
                                         {"line": "[fnvnx] audio: vorbis decode failed", "n": 1}],
                         "frames": {"measured": True, "instructions": [1_000_000, 20_000_000, 3_000_000]}}}
    json.dump(summ, open(os.path.join(g, "boot_summary_stub.json"), "w"))
    open(os.path.join(g, "emu_stub.log"), "w").write("stubbed-fault\tx\nstubbed-fault\ty\ntrap: defined nowhere: _ZN3Foo3barEv\n")
    w = os.path.join(tmp, "rep", "wine")
    os.makedirs(os.path.join(w, "profile", "Saves"))
    open(os.path.join(w, "profile", "Saves", "s18_rt_a.fos"), "wb").write(fos(body))
    open(os.path.join(w, "profile", "Saves", "s18_rt_b.fos"), "wb").write(fos(body))
    open(os.path.join(w, "console.txt"), "w").write("GetPos: X >> 1.0\n")
    json.dump({"steps_done": 10, "complete": True, "marks": [{"name": "in-cell"}], "seconds": 300},
              open(os.path.join(w, "progress.json"), "w"))
    cmp_ = os.path.join(tmp, "rep", "compare.json")
    json.dump({"first": {"kind": "return", "function": "FUN_1"}, "divergences": [{}, {}], "end": "divergence"}, open(cmp_, "w"))
    thr = os.path.join(tmp, "rep", "threads.json")
    json.dump({"schema": "fnvnx-threads/1", "races": 1, "cycles": 0, "long_waits": 2}, open(thr, "w"))
    nro = os.path.join(tmp, "rep", "nro.md")
    open(nro, "w").write("- [x] done\n- [ ] open one\n- [ ] open two\n")
    r = scenario.report(plan, g, w, cmp_, thr, nro)
    T = r["targets"]
    want = {"1": 3, "2": 2, "3": 2, "4": 1, "5": 1, "6": 1, "7": 4, "8": 0, "9": 1, "10": 1, "11": 2, "12": 1}
    got = {k: T[k]["count"] for k in want}
    check("report: every target measured", r["measured"] == 12, str({k: T[k]["measured"] for k in T}))
    check("report: counts", got == want, f"{got} != {want}")
    check("report: scenario's own unknown command is not a game error", T["10"]["detail"]["scenario_command_errors"])
    md = scenario.report_md(r)
    check("report md: no game text", "SCRIPTS" not in md and "Missing.bsa" not in md and "12/12" in md, md[:400])
    rp = os.path.join(tmp, "rep", "r.json")
    json.dump(r, open(rp, "w"))
    empty = scenario.report(plan, os.path.join(tmp, "nothing"), nro=nro)
    m = scenario.metric([r, empty])
    check("metric: targets measured in any scenario", m["measured"] == 12 and m["targets"][1]["measured_in"] == 1, str(m["measured"]))
    check("metric: sum over the scenarios", m["sum"] == sum(want.values()) + 2, str(m["sum"]))
    check("report: a run that did not happen measures only the checklist", empty["measured"] == 1, str(empty["measured"]))


def t_drive_wine(tmp):
    log = os.path.join(tmp, "xdo.log")
    xdo = os.path.join(tmp, "xdotool")
    open(xdo, "w").write(f"#!/bin/sh\necho \"$@\" >> {log}\n")
    os.chmod(xdo, 0o755)
    prof = os.path.join(tmp, "prof")
    os.makedirs(os.path.join(prof, "Saves"))
    plan = {"id": "w", "steps": [{"op": "until", "milestone": "main-menu", "wine_s": 3},
                                 {"op": "press", "key": "grave", "gap": 1}, {"op": "press", "key": "shift+a"},
                                 {"op": "until", "file": "Saves/s.fos", "timeout": 4}, {"op": "mark", "name": "x"}]}
    clock = [0.0]
    slept = []
    def sleep(s):
        slept.append(s)
        clock[0] += s
        if clock[0] > 5:
            open(os.path.join(prof, "Saves", "s.fos"), "w").write("x")
    r = scenario.drive_wine(plan, prof, os.path.join(tmp, "progress.json"), xdo=xdo, sleep=sleep, now=lambda: clock[0])
    calls = open(log).read().splitlines()
    check("drive-wine: milestone gate is a fixed wait", slept[0] == 3)
    check("drive-wine: keys sent with xdotool", [c for c in calls if c.startswith("key")] ==
          ["key --delay 60 grave", "key --delay 60 shift+a"], str(calls))
    check("drive-wine: focus before the console key", calls[0].startswith("search"))
    check("drive-wine: file gate polled until the save appears", not r["gate_timeouts"] and r["complete"], str(r))


def t_job_syntax():
    for f in ("tools/farm/scenario_job.sh", "tools/scenario/wine_scenario.sh"):
        rc = subprocess.run(["bash", "-n", os.path.join(P, f)]).returncode
        check(f"bash -n {f}", rc == 0)
    fb = open(os.path.join(P, "tests", "s5", "farm_boot.sh")).read()
    check("farm_boot.sh takes EMU_PY", "${EMU_PY:-$HERE/emu_guest.py}" in fb)


def main():
    tmp = tempfile.mkdtemp(prefix="s18-")
    try:
        t_keys()
        t_compile()
        t_quest(tmp)
        t_driver()
        t_emu_wrapper(tmp)
        t_savediff(tmp)
        t_report(tmp)
        t_drive_wine(tmp)
        t_job_syntax()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"tests/s18: {len(FAILS)} failed" + (": " + ", ".join(FAILS) if FAILS else ""))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
