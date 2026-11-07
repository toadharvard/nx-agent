#!/usr/bin/env python3
"""scenario.py - S18's scenario bench: scripted, deterministic scenarios run on the original under Wine and on the
guest in the emulator, one report per scenario over the zero targets of D25 (docs/ARCHITECTURE.md).

    scenario.py list     [--file scenarios.json]
    scenario.py compile  <id> [--file F] [--esm FalloutNV.esm] [--allow-unresolved] -o plan.json
    scenario.py validate [--file F] [--esm FalloutNV.esm]       ids against the ESM, keys against the platform keymap
    scenario.py seed     <plan.json> <Fallout_default.ini> <dir> [--width W --height H]
                                    the guest's SD seed: profile inis (tools/compare/ref_ini.py, as the Wine prefix
                                    gets them) and plugins.txt
    scenario.py drive-wine <plan.json> <profile dir> <progress.json>
                                    the Wine side's driver: xdotool on $DISPLAY, wall-clock waits, milestone gates
                                    as wine_s seconds, file gates polled under the profile dir
    scenario.py report   <plan.json> --guest DIR [--wine DIR] [--compare compare.json] [--threads threads.json]
                         [--nx1 nx1.json] [--optional tools/scenario/optional_files.txt]
                         --out report.json [--md report.md]
    scenario.py metric   <report.json>...   [--md OUT]        D25 targets measured (of 12) and the sum of counts

Plans (fnvnx-scenario-plan/1) have four primitive steps, the same on both sides: press <key> [gap], wait <s>,
until (milestone M | file F) [timeout, wine_s], mark <name>. compile expands the macros of scenarios.json:
  console {lines}     grave, each line typed key by key + Return, grave again unless the last line loads a cell or
                      a save (coc/cow/centeroncell/load: the load closes the console itself; wine_ref.sh's rule)
  type {text}         the keys of the text (keys.py; Shift where a US layout needs it)
  save {name}         console "save <name>", then until file Saves/<name>.fos
  load {name}         console "load <name>"
  state {cell}        console: the plan's state lines ({cell} filled in), read back from the scof log
  cells {cells}       per cell: console "coc <cell>", wait cell_wine_s, mark cell:<cell>, state
  quest {title, stages, wait}
                      the quest whose FULL name is <title> in the ESM: "setstage <editor id> <stage>" for its first
                      <stages> stage indices, a wait after each; then "getstage <editor id>" (needs --esm; without
                      it, --allow-unresolved writes a mark "quest-unresolved" in its place)
Numbers given as a name ("timeout": "menu_timeout") come from the file's "defaults".

The report (fnvnx-scenario/1) has one entry per D25 target, each {measured, count, detail}:
   1 faults            guest: a fault that ended the run (1) + faults the stand-in table absorbed (stubbed-fault
                       lines) + initializers skipped after a fault
   2 lockstep          tests/s5/boot_trace.py compare --json: the divergences (first one named)
   3 stand-ins         platform stand-in hits: "[fnvnx] ... not available / unsupported / ignored / no such module /
                       stand-in" lines (distinct), plus trap stubs reached ("defined nowhere")
   4 failed opens      the summary's file faults (distinct call+path) minus tools/scenario/optional_files.txt
   5 draws/states      "[fnvnx] ... skipped draw / unsupported ... state" lines of the guest's device
   6 audio/input       scenario keys the platform keymap cannot map + audio decode failure lines
   7 threads           tools/threads report (races + cycles + long waits) + gate timeouts (hangs on the path)
   8 memory            allocation-failure lines + 1 when the peak heap is over the Switch budget
   9 save/load         tools/scenario/savediff.py: the guest's round-trip differences not in the original's own
  10 script errors     script-error lines of the guest's console log (scof) not in the original's
  11 NRO checklist     the device-readiness checks behind docs/NRO_CHECKLIST.md that do not pass: tests/nx1/run.py
                       --json (fnvnx-nx1/1 "open"), run with this scenario's boot summary and guest
  12 frames > 16.7 ms  tools/perf/frames.py's device estimate when given (--frames-report, calibrated --scale);
                       else frames whose main-thread instructions exceed 16.7 ms at 1.02 GHz and the PERF_budget
                       IPC (emu_scenario.py --frames)
"measured" is false when the input it needs is absent; metric counts the measured ones (S18's METRIC) and sums the
counts. Everything here is our own code; reports carry counts, names and original addresses only.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import keys  # noqa: E402

SCENARIOS = os.path.join(HERE, "scenarios.json")
OPTIONAL = os.path.join(HERE, "optional_files.txt")
KEYMAP = os.path.join(P, "platform", "input", "keymap.cpp")
BUDGET = os.path.join(P, "docs", "PERF_budget.json")
LOADS = re.compile(r"^\s*(coc|cow|centeroncell|centeronworld|load)\b", re.I)
TARGETS = [(1, "faults"), (2, "lockstep"), (3, "stand-ins"), (4, "failed opens"), (5, "draws/states"),
           (6, "audio/input"), (7, "threads"), (8, "memory"), (9, "save/load"), (10, "script errors"),
           (11, "NRO checklist"), (12, "frames > 16.7 ms")]
SWITCH_BUDGET_BYTES = int(os.environ.get("SCENARIO_MEM_BUDGET", 3_285 * 1024 * 1024))   # a retail title's application pool


# ---- compile ---------------------------------------------------------------------------------------------------------
def load_file(path):
    d = json.load(open(path))
    if d.get("format") != "fnvnx-scenarios/1":
        raise SystemExit(f"{path}: not fnvnx-scenarios/1")
    return d


def scenario(d, sid):
    for s in d["scenarios"]:
        if s["id"] == sid:
            return s
    raise SystemExit(f"no scenario {sid!r} (have: {', '.join(s['id'] for s in d['scenarios'])})")


def num(d, v):
    """a number, or the name of one in the file's defaults"""
    if isinstance(v, str):
        if v not in d.get("defaults", {}):
            raise SystemExit(f"unknown default {v!r}")
        return d["defaults"][v]
    return v


def quest_info(esm, title):
    """(editor id, [stage indices]) of the quest whose FULL name is title, from the ESM (None when absent)"""
    sys.path.insert(0, os.path.join(P, "tools", "compare"))
    import esm as E   # noqa: E402  (tools/compare/esm.py: the public TES4 record format)
    import struct
    for pl in E.load_order([esm]):
        for _t, _fid, _fl, data, _g in pl.walk({b"QUST"}):
            full, ed, stages = None, None, []
            for t, v in E.subrecords(data):
                if t == b"EDID":
                    ed = v.rstrip(b"\0").decode("latin-1")
                elif t == b"FULL" and full is None:
                    full = v.rstrip(b"\0").decode("latin-1")
                elif t == b"INDX" and len(v) >= 2:
                    stages.append(struct.unpack_from("<h", v)[0])
            if full and full.lower() == title.lower():
                return ed, stages
    return None


def compile_plan(d, s, esm=None, allow_unresolved=False):
    df = d.get("defaults", {})
    gap = df.get("key_gap", 0.25)
    log = df.get("console_log", "s18_console.txt")
    out, cells, quests = [], [], []

    def press(k, g=None):
        keys.hid(k.split("+")[-1])   # unknown names fail here, not on a farm
        out.append({"op": "press", "key": k, "gap": gap if g is None else g})

    def console(lines):
        lines = [l.format(console_log=log, cell="") if "{cell}" not in l else l for l in lines]
        press("grave", 1.0)
        for l in lines:
            for k in keys.text_keys(l):
                press(k)
            press("Return", 1.0)
        if not (lines and LOADS.match(lines[-1])):
            press("grave", 1.0)

    def state(cell):
        console([l.format(cell=cell) for l in d.get("state", []) if cell or "{cell}" not in l])

    for st in s["steps"]:
        op = st["do"]
        if op == "comment":
            continue
        if op == "press":
            for _ in range(int(st.get("repeat", 1))):
                for k in st["keys"]:
                    press(k, st.get("gap"))
        elif op == "type":
            for k in keys.text_keys(st["text"]):
                press(k)
        elif op == "wait":
            out.append({"op": "wait", "s": num(d, st["s"])})
        elif op == "until":
            u = {"op": "until", "timeout": num(d, st.get("timeout", 600))}
            if "milestone" in st:
                u["milestone"] = st["milestone"]
                u["wine_s"] = num(d, st.get("wine_s", 60))
            else:
                u["file"] = st["file"]
            out.append(u)
        elif op == "mark":
            out.append({"op": "mark", "name": st["name"]})
        elif op == "console":
            console(st["lines"])
            for l in st["lines"]:
                m = re.match(r"^\s*coc\s+(\S+)", l, re.I)
                if m:
                    cells.append(m.group(1))
        elif op == "save":
            console([f"save {st['name']}"])
            out.append({"op": "until", "file": f"Saves/{st['name']}.fos", "timeout": num(d, "save_timeout")})
        elif op == "load":
            console([f"load {st['name']}"])
        elif op == "state":
            state(st.get("cell", ""))
        elif op == "cells":
            for c in st["cells"]:
                console([f"coc {c}"])
                cells.append(c)
                out.append({"op": "wait", "s": num(d, "cell_wine_s")})
                out.append({"op": "mark", "name": f"cell:{c}"})
                state(c)
        elif op == "quest":
            q = quest_info(esm, st["title"]) if esm else None
            if not q:
                if not allow_unresolved:
                    raise SystemExit(f"quest {st['title']!r}: " + ("not in the ESM" if esm else "needs --esm"))
                out.append({"op": "mark", "name": "quest-unresolved"})
                quests.append({"title": st["title"], "edid": None, "stages": []})
                continue
            ed, stages = q
            pick = stages[:int(st.get("stages", 4))]
            quests.append({"title": st["title"], "edid": ed, "stages": pick})
            for k in pick:
                console([f"setstage {ed} {k}"])
                out.append({"op": "wait", "s": st.get("wait", 5)})
                out.append({"op": "mark", "name": f"stage:{k}"})
            console([f"getstage {ed}"])
        else:
            raise SystemExit(f"{s['id']}: unknown step {op!r}")
    out.append({"op": "wait", "s": 5})   # the tail: the last commands' output reaches the log
    return {"schema": "fnvnx-scenario-plan/1", "id": s["id"], "title": s.get("title", ""), "steps": out,
            "roundtrip": s.get("roundtrip", []), "console_log": log, "cells": cells, "quests": quests,
            "plugins": d.get("plugins", ["FalloutNV.esm"])}


# ---- validate --------------------------------------------------------------------------------------------------------
def keymap_usages(path=KEYMAP):
    """the HID usages platform/input/keymap.cpp maps to a DIK code"""
    src = open(path).read()
    got = set()
    for lo, hi in re.findall(r"u >= (\d+) && u <= (\d+)", src):
        got |= set(range(int(lo), int(hi) + 1))
    got |= {int(n) for n in re.findall(r"case (\d+): return DIK_", src)}
    return got


def unmapped_keys(plan, usages=None):
    """{key: presses} of the plan's keys the platform keymap has no DIK for"""
    usages = keymap_usages() if usages is None else usages
    bad = {}
    for st in plan["steps"]:
        if st["op"] == "press":
            for u, _down in keys.press_events(st["key"]):
                if u not in usages:
                    bad[st["key"]] = bad.get(st["key"], 0) + 1
                    break
    return bad


def validate(d, esm=None):
    """problems: [str]"""
    probs = []
    usages = keymap_usages()
    edids = None
    if esm:
        sys.path.insert(0, os.path.join(P, "tools", "compare"))
        import esm as E   # noqa: E402
        edids = {e.lower() for e in E.find(E.load_order([esm]), "CELL").values()}
        edids |= {e.lower() for e in E.find(E.load_order([esm]), "WRLD").values()}
    for s in d["scenarios"]:
        try:
            plan = compile_plan(d, s, esm, allow_unresolved=not esm)
        except (SystemExit, KeyError, ValueError) as e:
            probs.append(f"{s['id']}: {e}")
            continue
        for k, n in unmapped_keys(plan, usages).items():
            probs.append(f"{s['id']}: key {k} has no DIK code in platform/input/keymap.cpp ({n} presses)")
        if edids is not None:
            for c in plan["cells"]:
                if c.lower() not in edids:
                    probs.append(f"{s['id']}: cell {c} is not a CELL/WRLD editor id of the ESM")
    return probs


# ---- seed (the guest's SD card before the run) -------------------------------------------------------------------------
def seed(plan, default_ini, out, w=1280, h=720):
    prof = os.path.join(out, "switch", "fnv", "user", "Documents", "My Games", "FalloutNV")
    os.makedirs(prof, exist_ok=True)
    subprocess.run([sys.executable, "-I", os.path.join(P, "tools", "compare", "ref_ini.py"), default_ini, prof,
                    "--width", str(w), "--height", str(h)], check=True, stdout=subprocess.DEVNULL)
    ad = os.path.join(out, "switch", "fnv", "user", "AppData", "Local", "FalloutNV")
    os.makedirs(ad, exist_ok=True)
    open(os.path.join(ad, "plugins.txt"), "w", newline="").write("\r\n".join(plan["plugins"]) + "\r\n")
    return prof


# ---- the Wine driver -------------------------------------------------------------------------------------------------
def find_file(root, rel):
    sys.path.insert(0, HERE)
    from emu_scenario import find_file as ff   # noqa: E402
    return ff(root, rel)


def drive_wine(plan, profile, progress, xdo="xdotool", sleep=time.sleep, now=time.time):
    """run the plan against the game on $DISPLAY: returns the progress record (also written to progress)"""
    t0 = now()
    rec = {"schema": "fnvnx-scenario-wine/1", "id": plan["id"], "steps": len(plan["steps"]), "steps_done": 0,
           "marks": [], "gate_timeouts": [], "keys_sent": 0, "complete": False}

    def save():
        with open(progress, "w") as f:
            json.dump(rec, f, indent=1)

    def focus():
        r = subprocess.run([xdo, "search", "--onlyvisible", "--name", "Fallout"], capture_output=True, text=True)
        w = r.stdout.split()
        if w:
            subprocess.run([xdo, "windowfocus", "--sync", w[-1]], capture_output=True)

    for k, st in enumerate(plan["steps"]):
        op = st["op"]
        if op == "press":
            if st["key"] == "grave":
                focus()
            subprocess.run([xdo, "key", "--delay", "60", st["key"]], capture_output=True)
            rec["keys_sent"] += 1
            sleep(st.get("gap", 0.25))
        elif op == "wait":
            sleep(st["s"])
        elif op == "until":
            if "milestone" in st:
                sleep(st.get("wine_s", 60))   # the original has no milestone hooks: a fixed wait
            else:
                t = now()
                while find_file(profile, st["file"]) is None:
                    if now() - t > st.get("timeout", 600):
                        rec["gate_timeouts"].append({"step": k, "what": st["file"], "seconds": st.get("timeout")})
                        break
                    sleep(2)
        elif op == "mark":
            rec["marks"].append({"name": st["name"], "step": k, "t_s": round(now() - t0, 1)})
        rec["steps_done"] = k + 1
        if op == "mark":
            save()
    rec["complete"] = True
    rec["seconds"] = round(now() - t0, 1)
    save()
    return rec


# ---- report ----------------------------------------------------------------------------------------------------------
STANDIN_RE = re.compile(r"not available|unsupported|ignored|no such module|stand-?in|not implemented|unimplemented",
                        re.I)
DRAW_RE = re.compile(r"skipped draw|unsupported (render|sampler|texture|stage) state|draw skipped", re.I)
AUDIO_RE = re.compile(r"(audio|sound|vorbis|mp3|wav|xwm|dshow|dsound)[^\n]*(fail|error|cannot|unsupported)", re.I)
ALLOC_RE = re.compile(r"(alloc\w*|out of memory|heap)[^\n]*(fail|exhaust|null)|out of memory", re.I)
TRAP_RE = re.compile(r"defined nowhere: (\S+)")
SCRIPT_RE = re.compile(r"^(SCRIPTS?:|.*\bscript\b.*\b(error|failed|invalid|missing|not found)\b|.*Error in script|"
                       r".*Script command .* not found)", re.I)


def read(path):
    try:
        return open(path, errors="replace").read()
    except OSError:
        return None


def jload(path):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return None


def console_lines(text):
    return [l.strip() for l in (text or "").splitlines() if l.strip()]


def first(paths):
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


def guest_inputs(gdir, plan):
    """the guest run's summary, its emulator log and its scof console text"""
    summ = jload(first([os.path.join(gdir, "boot_summary_stub.json")] +
                       sorted(glob.glob(os.path.join(gdir, "boot_summary_*.json")))) or "")
    log = read(first([os.path.join(gdir, "emu_stub.log")] + sorted(glob.glob(os.path.join(gdir, "emu_*.log")))) or "")
    root = ((summ or {}).get("scenario") or {}).get("root") or os.path.join(gdir, "sd")
    con = None
    if root and os.path.isdir(root):
        f = find_file(root, plan["console_log"])
        con = read(f) if f else None
    return summ, log, con, root


def roundtrip(plan, root, savediff):
    """savediff.py's comparison of the plan's round-trip saves under root (None when the plan has none)"""
    rt = plan.get("roundtrip") or []
    if len(rt) != 2:
        return None
    a, b = (find_file(root, f"Saves/{n}.fos") if root and os.path.isdir(root) else None for n in rt)
    if not a or not b:
        return {"present": [bool(a), bool(b)]}
    sys.path.insert(0, HERE)
    import savediff as SD   # noqa: E402
    return SD.compare(a, b)


def report(plan, gdir, wdir=None, compare=None, threads=None, nx1=None, optional=OPTIONAL, budget=BUDGET,
           frames_report=None):
    summ, log, con, root = guest_inputs(gdir, plan)
    sc = (summ or {}).get("scenario") or {}
    stop = ((summ or {}).get("stop") or {}).get("reason", "")
    ran = summ is not None
    T = {}

    def put(n, measured, count, **detail):
        T[n] = {"target": dict(TARGETS)[n], "measured": bool(measured), "count": int(count) if measured else None,
                "detail": detail}

    # 1 faults
    stubbed = len(re.findall(r"^stubbed-fault", log or "", re.M)) or (summ or {}).get("stubbed_faults", 0)
    fault_end = bool(re.search(r"fault|invalid memory|out of instruction budget|host stand-in error|deadlock", stop, re.I))
    skipped = (summ or {}).get("initializers_skipped", 0) or 0
    put(1, ran, int(fault_end) + int(stubbed) + int(skipped), stop=stop[:300], stubbed_faults=stubbed,
        initializers_skipped=skipped)
    # 2 lockstep
    cj = jload(compare) if compare else None
    if cj is not None:
        divs = cj.get("divergences") or []
        put(2, True, len(divs), first=cj.get("first"), end=cj.get("end"),
            guest_calls=cj.get("guest_calls"), wine_calls=cj.get("wine_calls"))
    else:
        put(2, False, 0, why="no boot_trace.py compare --json for this run")
    # 3 stand-ins
    fl = [x["line"] for x in sc.get("fnvnx_lines", [])] or [l for l in console_lines(log) if "[fnvnx]" in l]
    stand = sorted({re.sub(r"0x[0-9a-f]+|\d+", "#", l) for l in fl if STANDIN_RE.search(l)})
    traps = sorted(set(TRAP_RE.findall(log or "")))
    put(3, ran, len(stand) + len(traps), platform=stand[:50], traps=traps[:50])
    # 4 failed opens
    opt = [re.compile(re.split(r"\s+#", l.strip())[0], re.I) for l in (read(optional) or "").splitlines()
           if l.strip() and not l.lstrip().startswith("#")]
    ff = [f for f in (summ or {}).get("file_faults", []) if not any(r.search(f.get("path", "")) for r in opt)]
    put(4, ran, len({(f.get("call"), f.get("path", "").lower()) for f in ff}),
        faults=[f"{f.get('call')} {f.get('path')} errno {f.get('errno')}" for f in ff[:50]])
    # 5 draws / states
    dr = sorted({l for l in fl if DRAW_RE.search(l)})
    put(5, ran, len(dr), lines=dr[:30])
    # 6 audio / input
    um = unmapped_keys(plan)
    au = sorted({l for l in fl if AUDIO_RE.search(l)})
    put(6, ran, len(um) + len(au), unmapped_keys=um, audio=au[:30])
    # 7 threads
    tj = jload(threads) if threads else None
    hangs = len(sc.get("gate_timeouts", []))
    if tj is not None:
        tc = {k: int(tj.get(k, 0) or 0) for k in ("races", "cycles", "long_waits")}   # fnvnx-threads/1
        n = sum(tc.values())
        put(7, True, n + hangs, threads=tc, gate_timeouts=sc.get("gate_timeouts", []))
    else:
        put(7, ran, hangs, gate_timeouts=sc.get("gate_timeouts", []),
            why="no threads report: gate timeouts only" if ran else "no guest run")
    # 8 memory
    peak = (summ or {}).get("peak_heap_bytes")
    al = sorted({l for l in fl if ALLOC_RE.search(l)})
    put(8, ran and peak is not None, len(al) + int((peak or 0) > SWITCH_BUDGET_BYTES), peak_heap_bytes=peak,
        budget_bytes=SWITCH_BUDGET_BYTES, lines=al[:30])
    # 9 save/load round trip
    if plan.get("roundtrip"):
        g = roundtrip(plan, root, None) if ran else None
        wroot = (wdir and os.path.join(wdir, "profile")) or None
        w = roundtrip(plan, wroot, None) if wroot and os.path.isdir(wroot) else None
        if g and "diffs" in g:
            allow = set(w["diff_keys"]) if w and "diff_keys" in w else set()
            extra = [k for k in g["diff_keys"] if k not in allow]
            put(9, True, len(extra), guest=g["summary"], wine=(w or {}).get("summary"), extra=extra[:40])
        elif ran and sc.get("steps_done", 0) >= len(plan["steps"]):
            put(9, True, 1, why="the scenario completed but a round-trip save is missing", present=(g or {}).get("present"))
        else:
            put(9, False, 0, why="the guest did not reach the round-trip saves", present=(g or {}).get("present"),
                wine=(w or {}).get("summary"))
    else:
        put(9, False, 0, why="no round trip in this scenario")
    # 10 script errors
    wcon = None
    if wdir:
        wcon = read(os.path.join(wdir, "console.txt"))
    if con is not None:
        ge = {l for l in console_lines(con) if SCRIPT_RE.search(l)}
        we = {l for l in console_lines(wcon) if SCRIPT_RE.search(l)} if wcon is not None else set()
        own = {l for l in ge if re.search(r"not found", l, re.I) and re.search(r"command", l, re.I)}
        extra = sorted(ge - we - own)
        put(10, True, len(extra), guest=len(ge), wine=len(we) if wcon is not None else None,
            scenario_command_errors=sorted(own)[:20], extra=extra[:40])
    else:
        put(10, False, 0, why="no guest console log (scof) - the guest did not reach the console")
    # 11 NRO checklist: NX1's device-readiness checks (missing evidence counts as open there, never as a pass)
    nj = jload(nx1) if nx1 else None
    if nj is not None and nj.get("schema") == "fnvnx-nx1/1":
        rows = nj.get("checks", [])
        put(11, True, int(nj.get("open", 0)), checks=len(rows),
            open=[f"{r.get('n')} {r.get('check')}: {r.get('status')}" for r in rows if r.get("status") != "pass"])
    else:
        put(11, False, 0, why="no tests/nx1/run.py --json for this run")
    # 12 frames
    fr = sc.get("frames") or {}
    fj = jload(frames_report) if frames_report else None
    if fj and (fj.get("device_ms") or {}).get("over_budget") is not None:   # tools/perf/frames.py, calibrated
        put(12, True, fj["device_ms"]["over_budget"], frames=fj.get("frames"), source="frames.py device estimate")
    elif fr.get("measured") and fr.get("instructions"):
        ipc = 0.8
        bj = jload(budget)
        for a in (bj or {}).get("assumptions", []):
            if a.get("id") == "ipc":
                ipc = float(a["value"])
        limit = int(16.7e-3 * 1.02e9 * ipc)
        ins = fr["instructions"]
        over = sum(1 for n in ins if n > limit)
        put(12, True, over, frames=len(ins), limit_instructions=limit, ipc=ipc, max=max(ins),
            median=sorted(ins)[len(ins) // 2], source="main-thread instructions per frame")
    else:
        put(12, False, 0, why="no frames measured (emu_scenario.py --frames, after renderer-init)")

    wprog = jload(os.path.join(wdir, "progress.json")) if wdir else None
    rep = {"schema": "fnvnx-scenario/1", "id": plan["id"], "title": plan.get("title"),
           "guest": {"ran": ran, "stop": stop[:300], "steps": len(plan["steps"]), "steps_done": sc.get("steps_done"),
                     "complete": sc.get("complete"), "marks": [m["name"] for m in sc.get("marks", [])],
                     "milestone": (summ or {}).get("milestone"), "virtual_s": sc.get("virtual_s")},
           "wine": {"ran": wprog is not None, "steps_done": (wprog or {}).get("steps_done"),
                    "complete": (wprog or {}).get("complete"), "marks": [m["name"] for m in (wprog or {}).get("marks", [])],
                    "seconds": (wprog or {}).get("seconds")} if wdir else None,
           "targets": {str(n): T[n] for n, _ in TARGETS}}
    rep["measured"] = sum(1 for t in T.values() if t["measured"])
    rep["sum"] = sum(t["count"] for t in T.values() if t["measured"])
    return rep


def report_md(r):
    o = [f"# scenario {r['id']}: {r.get('title') or ''}", ""]
    g = r["guest"]
    o.append(f"- guest: {'ran' if g['ran'] else 'did not run'}; steps {g.get('steps_done')}/{g['steps']}; "
             f"marks: {', '.join(g.get('marks') or []) or 'none'}; milestone {g.get('milestone')}; stop: {g.get('stop')}")
    if r.get("wine"):
        w = r["wine"]
        o.append(f"- original (Wine): {'ran' if w['ran'] else 'did not run'}; steps {w.get('steps_done')}; "
                 f"marks: {', '.join(w.get('marks') or []) or 'none'}; {w.get('seconds')} s")
    o.append(f"- D25 targets measured: {r['measured']}/12; sum of counts: {r['sum']}")
    o += ["", "| # | target | measured | count | detail |", "|---:|---|---|---:|---|"]
    for n, name in TARGETS:
        t = r["targets"][str(n)]
        # scalars only: lists can hold game text (console lines, paths); they stay in the private report.json
        det = json.dumps({k: v for k, v in t["detail"].items() if isinstance(v, (int, float, str, bool)) or v is None},
                         separators=(",", ":"))
        o.append(f"| {n} | {name} | {'yes' if t['measured'] else 'no'} | "
                 f"{t['count'] if t['measured'] else '-'} | {det[:220].replace('|', '/')} |")
    return "\n".join(o) + "\n"


def metric(reports):
    """per target: measured on every scenario report that ran, and the summed count"""
    out = {}
    for n, name in TARGETS:
        ts = [r["targets"][str(n)] for r in reports]
        m = [t for t in ts if t["measured"]]
        out[n] = {"target": name, "measured_in": len(m), "of": len(ts), "count": sum(t["count"] for t in m)}
    measured = sum(1 for v in out.values() if v["measured_in"])
    return {"schema": "fnvnx-scenario-metric/1", "scenarios": [r["id"] for r in reports], "targets": out,
            "measured": measured, "sum": sum(v["count"] for v in out.values())}


def metric_md(m):
    o = [f"- S18 metric: {m['measured']}/12 D25 targets measured on scenarios; sum of counts {m['sum']} "
         f"({len(m['scenarios'])} scenarios: {', '.join(m['scenarios'])})", "",
         "| # | target | measured in | count |", "|---:|---|---:|---:|"]
    for n, v in m["targets"].items():
        o.append(f"| {n} | {v['target']} | {v['measured_in']}/{v['of']} | {v['count']} |")
    return "\n".join(o) + "\n"


# ---- CLI -------------------------------------------------------------------------------------------------------------
def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("list"); a.add_argument("--file", default=SCENARIOS)
    a = sub.add_parser("compile"); a.add_argument("id"); a.add_argument("--file", default=SCENARIOS)
    a.add_argument("--esm"); a.add_argument("--allow-unresolved", action="store_true"); a.add_argument("-o", required=True)
    a = sub.add_parser("validate"); a.add_argument("--file", default=SCENARIOS); a.add_argument("--esm")
    a = sub.add_parser("seed"); a.add_argument("plan"); a.add_argument("default_ini"); a.add_argument("dir")
    a.add_argument("--width", type=int, default=1280); a.add_argument("--height", type=int, default=720)
    a = sub.add_parser("drive-wine"); a.add_argument("plan"); a.add_argument("profile"); a.add_argument("progress")
    a = sub.add_parser("report"); a.add_argument("plan"); a.add_argument("--guest", required=True)
    a.add_argument("--wine"); a.add_argument("--compare"); a.add_argument("--threads"); a.add_argument("--nx1")
    a.add_argument("--optional", default=OPTIONAL); a.add_argument("--out", required=True); a.add_argument("--md")
    a.add_argument("--frames-report", help="tools/perf/frames.py's fnvnx-frames/1 (with --scale) for target 12")
    a = sub.add_parser("metric"); a.add_argument("reports", nargs="+"); a.add_argument("--md"); a.add_argument("--out")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        for s in load_file(a.file)["scenarios"]:
            print(f"{s['id']}\t{s.get('title', '')}")
        return 0
    if a.cmd == "compile":
        d = load_file(a.file)
        plan = compile_plan(d, scenario(d, a.id), a.esm, a.allow_unresolved)
        json.dump(plan, open(a.o, "w"), indent=1)
        print(f"{a.id}: {len(plan['steps'])} steps, {sum(1 for s in plan['steps'] if s['op'] == 'press')} key presses")
        return 0
    if a.cmd == "validate":
        probs = validate(load_file(a.file), a.esm)
        for p in probs:
            print(p)
        print(f"validate: {len(probs)} problems" + ("" if a.esm else " (no --esm: cell and quest ids not checked)"))
        return 1 if probs else 0
    if a.cmd == "seed":
        print(seed(json.load(open(a.plan)), a.default_ini, a.dir, a.width, a.height))
        return 0
    if a.cmd == "drive-wine":
        r = drive_wine(json.load(open(a.plan)), a.profile, a.progress)
        print(f"drive-wine {r['id']}: {r['steps_done']}/{r['steps']} steps, {r['keys_sent']} keys, "
              f"{len(r['gate_timeouts'])} gate timeouts")
        return 0
    if a.cmd == "report":
        r = report(json.load(open(a.plan)), a.guest, a.wine, a.compare, a.threads, a.nx1, a.optional,
                   frames_report=a.frames_report)
        json.dump(r, open(a.out, "w"), indent=1)
        if a.md:
            open(a.md, "w").write(report_md(r))
        print(f"{r['id']}: {r['measured']}/12 measured, sum {r['sum']}")
        return 0
    if a.cmd == "metric":
        m = metric([json.load(open(p)) for p in a.reports])
        if a.out:
            json.dump(m, open(a.out, "w"), indent=1)
        txt = metric_md(m)
        if a.md:
            open(a.md, "w").write(txt)
        print(txt, end="")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
