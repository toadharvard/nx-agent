#!/usr/bin/env python3
"""test_recjob.py: M1a, the d9rec farm job and its statistics tool (render/tools/rec/farm).
(a) d9rec_stats.py on the streams tests/l6/rec/test_rec.py records from synthetic captures: frames/draws/shader creates against the NXD9
    counts of >= 3 captures, the per-frame create histogram against a hand count over the NXD9 records of one capture, a truncated stream.
(a2) M1b: the WORLD-REACHED verdict: world_verdict() on synthetic per-frame draw/texture lists (menu-only flagged, high-draw phase passes, too short / too few
    textures / interrupted runs flagged) and on the synthetic streams through the CLI (--require-world exit 4 / 0, thresholds as the measured property).
(b) `bash -n` and shellcheck of the job (skipped with a note when shellcheck is not installed).
(c) D9REC_DRY=1 pipeline under Wine (M1b: with the world thresholds lowered to the synthetic captures; one more run with the real thresholds must flag
    scene cell, list FAILED and exit 1; the commit check compares a prefix; the cells.py call does not pipe into head)
    D9REC_DRY=1 pipeline under Wine (nxd9play + synthetic captures as the "game"), profiles handheld and docked: report sections, registry key,
    exactly one adapter mode of the profile's size (checked here on the probe stream independently of the stats tool), DLL removed, nothing pushed.
env: D9REC_TEST_WORK (scratch, default /home/user/work/l6/recjob), D9REC_WORK (test_rec's, default /home/user/work/l6/rec: streams under runs/p1,
captures under caps; if absent test_rec.py is run there), D9REC_HOST (shader host tools).  Exit 0 iff every check passed."""
import collections, json, os, re, shutil, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.dont_write_bytecode = True
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
FARM = os.path.join(ROOT, "render", "tools", "rec", "farm"); JOB = FARM + "/d9rec_job.sh"; STATS = FARM + "/d9rec_stats.py"
sys.path[:0] = [os.path.join(ROOT, "tools", "compare"), os.path.join(ROOT, "render", "tools", "cmp"), FARM]
import nxd9io, d9s1  # noqa: E402
RECW = os.environ.get("D9REC_WORK", "/home/user/work/l6/rec"); W = os.environ.get("D9REC_TEST_WORK", "/home/user/work/l6/recjob")
fails = []; npass = 0
HEAD = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def check(ok, what):
    global npass
    if ok: npass += 1
    else: fails.append(what); print("FAIL", what)


def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


os.makedirs(W, exist_ok=True)
# ---- streams + captures
if not (os.path.isdir(RECW + "/runs/p1") and os.path.isdir(RECW + "/caps")):
    print("running test_rec.py for the streams ..."); r = sh([sys.executable, os.path.join(ROOT, "tests/l6/rec/test_rec.py")], env=dict(os.environ, D9REC_WORK=RECW), timeout=3000)
    check(r.returncode == 0, "test_rec.py: " + r.stdout[-300:])
caps = sorted(f[:-5] for f in os.listdir(RECW + "/caps") if f.endswith(".nxd9") and os.path.isfile(f"{RECW}/runs/p1/{f[:-5]}.d9s"))
check(len(caps) >= 3, f"(a) >= 3 captures with recorded streams ({len(caps)})")

# ---- (a) stats vs NXD9
hand = None
for n in caps[:4]:
    js = f"{W}/{n}.json"; r = sh([sys.executable, "-I", STATS, f"{RECW}/runs/p1/{n}.d9s", "--json", js, "--tsv-dir", f"{W}/{n}_tsv", "--md", f"{W}/{n}.md"])
    check(r.returncode == 0, f"(a) {n}: stats exit {r.returncode} {r.stderr[-200:]}")
    if r.returncode: continue
    R = json.load(open(js)); tags = collections.Counter(); per = collections.defaultdict(collections.Counter); f = 0
    for t, _ in nxd9io.records(f"{RECW}/caps/{n}.nxd9"):
        tags[t] += 1; per[f][t] += 1
        if t == "PRES": f += 1
    want = {"frames": tags["PRES"], "draws": tags["DRAW"] + tags["DRWI"], "VS": tags["VSH "], "PS": tags["PSH "]}
    got = {"frames": R["frames"], "draws": R["totals"].get("draws", 0), "VS": R["creates"]["VS"], "PS": R["creates"]["PS"]}
    check(want == got, f"(a) {n}: NXD9 {want} == stats {got}")
    check(R["decode_error"] is None and R["threads"] == 1 and R["max_depth"] == 0, f"(a) {n}: decode, threads, depth {R['decode_error']} {R['threads']} {R['max_depth']}")
    check(sum(R["called"].values()) == R["records"] and R["bytes"] == os.path.getsize(f"{RECW}/runs/p1/{n}.d9s"), f"(a) {n}: called sums to records, bytes = file size")
    if hand is None and tags["VSH "] + tags["PSH "] > 0: hand = (n, R, per, f)
    print(f"   {n}: frames {got['frames']} draws {got['draws']} VS {got['VS']} PS {got['PS']} records {R['records']}")
check(hand is not None, "(a) a capture with shader creates for the hand count")
if hand:
    n, R, per, nf = hand; rows = [l.split("\t") for l in open(f"{W}/{n}_tsv/perframe.tsv").read().splitlines()[1:]]
    hv = [(per[i]["VSH "], per[i]["PSH "]) for i in range(nf)]; sv = [(int(r[2]), int(r[3])) for r in rows[:nf]]
    check(hv == sv, f"(a) {n}: per-frame (VS, PS) creates by hand {hv} == stats {sv}")
    hh = collections.Counter(a + b for a, b in hv); check({int(k): v for k, v in R["creates"]["window_hist"].items()} == dict(hh), f"(a) {n}: window histogram {R['creates']['window_hist']} vs hand {dict(hh)}")
    check(R["creates"]["last_frame_with_create"] == max(i for i, (a, b) in enumerate(hv) if a + b), f"(a) {n}: last frame with a create")
d = open(f"{RECW}/runs/p1/{caps[0]}.d9s", "rb").read(); open(f"{W}/trunc.d9s", "wb").write(d[:len(d) - 7])   # a killed game: the tail record is partial
r = sh([sys.executable, "-I", STATS, f"{W}/trunc.d9s", "--json", f"{W}/trunc.json"]); T = json.load(open(f"{W}/trunc.json")) if r.returncode == 0 else {}
check(r.returncode == 0 and T.get("decode_error") and T["decode_error"][2] > 0 and T["records"] > 0, f"(a) truncated stream: records before the cut are kept, error reported ({T.get('decode_error')})")
r = sh([sys.executable, "-I", STATS, JOB]); check(r.returncode == 2, f"(a) a non-stream exits 2 ({r.returncode})")

# ---- (a2) world verdict (M1b)
import d9rec_stats as DS  # noqa: E402
menu = [4] * 3000 + [19] * 2000 + [28] * 5 + [26] * 100; tm = [{i % 12} for i in range(len(menu))]          # the measured menu / loading shape: max 28 draws
check(not DS.world_verdict(menu, tm)["reached"], "(a2) menu-only (max 28 draws/frame) is flagged")
world = menu + [150 + i % 40 for i in range(200)] + [26] * 50; tw = tm + [{1000 + (i * 7 + k) % 400 for k in range(30)} for i in range(200)] + [{1}] * 50
v = DS.world_verdict(world, tw); check(v["reached"] and v["best_run_frames"] == 200 and v["first_world_frame"] == len(menu), f"(a2) a 200-frame high-draw phase passes ({v['best_run_frames']}, {v['first_world_frame']})")
check(not DS.world_verdict(menu + [500] * 9 + [4], tm + [set(range(2000, 2200))] * 10)["reached"], "(a2) 9 frames over the draw threshold are too short")
check(not DS.world_verdict(menu + [500] * 40, tm + [{7, 8}] * 40)["reached"], "(a2) high draws with 2 distinct textures (e.g. a particle storm on one menu texture) are flagged")
check(not DS.world_verdict([500, 500, 500, 99, 500, 500, 500] * 3, [set(range(i * 20, i * 20 + 20)) for i in range(21)])["reached"], "(a2) runs interrupted every 3 frames do not add up")
check(DS.world_verdict([], [])["reached"] is False and DS.world_verdict(menu, tm)["draws_pct"]["all"][100] == 28, "(a2) empty stream flagged; percentiles reported (max 28)")
wj = {}
for n in caps[:4]:
    r = sh([sys.executable, "-I", STATS, f"{RECW}/runs/p1/{n}.d9s", "--require-world", "--json", f"{W}/{n}_w.json", "--md", f"{W}/{n}_w.md"]); wj[n] = json.load(open(f"{W}/{n}_w.json"))
    check(r.returncode == 4 and "NOT WORLD-REACHED" in r.stderr and "## 11. World reached" in open(f"{W}/{n}_w.md").read(), f"(a2) {n}: synthetic stream (<= 6 draws) with default thresholds exits 4 and says so ({r.returncode})")
tn = next((n for n in caps[:4] if wj[n]["world"]["distinct_textures_all"] >= 1), None)
if tn:   # the same stream passes when the thresholds are what it measures: a threshold is a stated property, the verdict a function of the stream
    w = wj[tn]["world"]; r = sh([sys.executable, "-I", STATS, f"{RECW}/runs/p1/{tn}.d9s", "--require-world", "--world-min-draws", str(max(1, w["draws_pct"]["all"]["100"])),
                                  "--world-min-frames", "1", "--world-min-textures", "1", "--md", f"{W}/{tn}_w2.md"])
    check(r.returncode == 0 and "verdict: WORLD-REACHED" in open(f"{W}/{tn}_w2.md").read(), f"(a2) {tn}: passes with thresholds at its own maximum ({r.returncode} {r.stderr[-150:]})")
else: check(False, "(a2) a synthetic stream that binds a texture")
jt = open(JOB).read()
check("cells.py\" list" in jt and not re.search(r"cells\.py\"[^\n]*\|\s*head", jt), "(a2) the cells.py call has no `| head` (BrokenPipeError)")
check("--require-world" in jt and "did not reach a world cell" in jt, "(a2) the job requires the world verdict for scene cell")

# ---- (b) syntax, shellcheck
check(sh(["bash", "-n", JOB]).returncode == 0, "(b) bash -n")
sc = shutil.which("shellcheck") or next((p for p in (os.path.join(os.path.dirname(sys.executable), "shellcheck"),) if os.path.isfile(p)), None)
if sc: r = sh([sc, "-x", JOB]); check(r.returncode == 0, "(b) shellcheck: " + r.stdout[-300:]); print("(b) shellcheck clean" if r.returncode == 0 else "(b) shellcheck FAILED")
else: print("(b) shellcheck not installed: skipped")

# ---- (c) dry runs
SECTIONS = ["## Environment profile", "## Scene menu", "## Scene cell", "## 1. Size", "## 2. Shader creation", "## 3. Other Create*", "## 4. Locks, draws, fixed function",
            "## 5. Per frame", "## 6. Failed HRESULTs", "## 7. env-class answers", "## 8. Adapter modes", "## 9. Methods called", "## 10. Spec methods never called",
            "### Determinism: run 1 vs run 2 of scene menu", "### Determinism: run 1 vs run 2 of scene cell", "## Artifacts"]
a, b = caps[0], caps[1]
for prof, (w, h) in (("handheld", (1280, 720)), ("docked", (1920, 1080))):
    wk = f"{W}/job_{prof}"; rep = f"{W}/report_{prof}.md"; shutil.rmtree(wk + "/pub", ignore_errors=True)
    env = dict(os.environ, D9REC_DRY="1", D9REC_WORK=wk, FARM_HOME=W + "/farm", D9REC_DRY_CAPS=f"{RECW}/caps/{a}.nxd9 {RECW}/caps/{b}.nxd9", D9REC_VIDEO_MEM_MB="960",
               D9REC_WORLD_MIN_DRAWS="1", D9REC_WORLD_MIN_FRAMES="1", D9REC_WORLD_MIN_TEXTURES="0")   # the synthetic captures are not worlds: thresholds at what they have
    r = sh(["bash", JOB, "a" * 40, prof, rep], env=env, timeout=3000); print(f"(c) {prof}: exit {r.returncode}; " + r.stdout.strip().splitlines()[-1][-100:] if r.stdout.strip() else r.stderr[-300:])
    check(r.returncode == 0 and os.path.isfile(rep), f"(c) {prof}: job exit {r.returncode} {r.stderr[-300:]}")
    if not os.path.isfile(rep): continue
    t = open(rep).read(); check([s for s in SECTIONS if s not in t] == [], f"(c) {prof}: missing sections {[s for s in SECTIONS if s not in t]}")
    check("VideoMemorySize = 960" in t and f"screen {w}x{h}@60" in t, f"(c) {prof}: profile lines (960 MiB, {w}x{h}@60)")
    check(f"single-mode check {w}x{h}@60: PASS" in t, f"(c) {prof}: the job's single-mode check passes")
    check("d9cmp exit" in t and t.count("RESULT:") == 2, f"(c) {prof}: both determinism sections have a d9cmp result")
    check("- FAILED" not in t and "not pushed" in t, f"(c) {prof}: no FAILED note, not pushed")
    check("checkout is not aaaaaaaaaaaa (HEAD " + HEAD[:12] + ")" in t, f"(c) {prof}: a wrong commit is reported with the 12-char HEAD")
    check("## 11. World reached" in t and t.count("verdict: WORLD-REACHED") >= 1, f"(c) {prof}: world verdict section in the report")
    # independent check of the probe stream: GetAdapterModeCount / EnumAdapterModes for X8R8G8B8
    raw = open(f"{wk}/probe.d9s", "rb").read(); spec = json.load(open(ROOT + "/render/d3d9/spec/d3d9_spec.json"))
    _, recs = d9s1.read_stream(raw, spec); modes = set(); cnt = []
    for rc in recs:
        its = {i.label: i for i in rc.items}
        if rc.name == "GetAdapterModeCount" and int.from_bytes(its["in:Format"].data, "little") == 22: cnt.append(int.from_bytes(its["ret"].data, "little"))
        if rc.name == "EnumAdapterModes" and int.from_bytes(its["in:Format"].data, "little") == 22 and "out:pMode" in its and its["out:pMode"].data:
            modes.add(tuple(int.from_bytes(its["out:pMode"].data[k:k + 4], "little") for k in (0, 4, 8)))
    check(cnt == [1] and modes == {(w, h, 60)}, f"(c) {prof}: EnumAdapterModes X8R8G8B8: counts {cnt}, modes {modes} == one {w}x{h}@60")
    pub = f"{wk}/pub"; xs = [f for f in os.listdir(pub + "/menu")] if os.path.isdir(pub + "/menu") else []
    check("run1.d9s.xz" in xs and "env_answers.tsv" in xs and "perframe.tsv" in xs and os.path.isfile(pub + "/SHA256SUMS"), f"(c) {prof}: artifacts packed {sorted(xs)}")
    check(not os.path.exists(f"{wk}/wine/pfx/drive_c/FNV/d3d9.dll") and not os.path.exists(wk + "/drygame"), f"(c) {prof}: recorder DLL and dry game removed")
    # the profile's numbers are what the registry holds
    print(f"   {prof}: {len(t.splitlines())} report lines")
# ---- (c2) the same job with the real thresholds: the synthetic "cell" is menu-like (<= 6 draws): FAILED, exit 1; a SHORT commit id equal to HEAD's prefix and the full id
# raise no "checkout is not" note
wk = f"{W}/job_menuonly"; rep = f"{W}/report_menuonly.md"; shutil.rmtree(wk + "/pub", ignore_errors=True)
env = dict(os.environ, D9REC_DRY="1", D9REC_WORK=wk, FARM_HOME=W + "/farm", D9REC_DRY_CAPS=f"{RECW}/caps/{a}.nxd9 {RECW}/caps/{b}.nxd9", D9REC_VIDEO_MEM_MB="960")
r = sh(["bash", JOB, HEAD[:12], "handheld", rep], env=env, timeout=3000)
t = open(rep).read() if os.path.isfile(rep) else ""
check(r.returncode == 1, f"(c2) menu-only cell run: job exits 1 ({r.returncode})")
check("- FAILED: scene cell did not reach a world cell" in t, "(c2) report: FAILED: scene cell did not reach a world cell")
check("- FAILED: scene menu" not in t and "scene menu did not reach" not in t, "(c2) the menu scene is not required to reach a world")
check("checkout is not" not in t, "(c2) a 12-char commit id equal to HEAD's prefix raises no checkout note")
check("verdict: NOT WORLD-REACHED" in t, "(c2) the report carries the NOT WORLD-REACHED verdict")
print(f"test_recjob: {npass} checks passed, {len(fails)} failed"); sys.exit(1 if fails else 0)
