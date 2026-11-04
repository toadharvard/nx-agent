#!/usr/bin/env python3
"""fnv-nx P6n: measure one S6n optimisation commit against its predecessor and append it to optimizations[].

    python3 -I opt_delta.py <commit> [--before <commit>] [--name <text>] [--work <dir>] [--baseline] [--dry-run]

For each of the two commits (default --before: the previous entry's commit in optimizations.json, else <commit>^),
a detached git worktree of this repository is made under <work>/opt/trees/<sha12>, the *current* perf tools are
copied over its render/native/perf (so only the renderer code and shaders differ between the two runs), and the
reduced measurement runs: perf/build.sh (guest + host), gen_stream.py, gpu_cost.py (the commit's shaders), the
ILP32 guest on mix_hi (real + dry, AArch64 instructions of main and render-front), the .nxcap of mix_hi and the
deko3d recording count (render-backend). Snapshots are cached in <work>/opt/<sha>.json.

The entry appended to render/native/perf/optimizations.json (report.json carries the array):
  {commit, name, before_commit, cpu_instr_delta_main, cpu_instr_delta_render, gpu_ms_docked_720p,
   gpu_ms_handheld_720p, before, after}
  gpu_ms_*: [before, after] of the GPU model (mix_hi, efficiency 0.6); deltas are after - before (negative = saved);
  before / after: the snapshots (instructions main / render_front / render_backend, draws, failed draws, GPU ms
  docked/handheld 720p at efficiency 1.0 and 0.6, the per-function top 15).
--baseline: record <commit> alone as the baseline entry (name "baseline", deltas 0).
"""
import json, os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = subprocess.run(["git", "-C", HERE, "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip()
OPT_JSON = os.path.join(HERE, "optimizations.json")


def sh(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        raise SystemExit("failed: %s\n%s%s" % (" ".join(cmd) if isinstance(cmd, list) else cmd, r.stdout[-3000:], r.stderr[-3000:]))
    return r.stdout


def full_sha(c):
    return sh(["git", "-C", REPO, "rev-parse", c + "^{commit}"]).strip()


def measure(sha, work):
    cache = os.path.join(work, "opt", sha + ".json")
    if os.path.exists(cache):
        return json.load(open(cache))
    tree = os.path.join(work, "opt", "trees", sha[:12])
    if not os.path.isdir(tree):
        os.makedirs(os.path.dirname(tree), exist_ok=True)
        sh(["git", "-C", REPO, "worktree", "add", "--detach", "-f", tree, sha])
    perf = os.path.join(tree, "ports", "fnv-nx", "render", "native", "perf")
    shutil.rmtree(perf, ignore_errors=True)
    shutil.copytree(HERE, perf, ignore=shutil.ignore_patterns("report.json", "optimizations.json", "__pycache__"))
    ext = os.path.join(tree, "ports", "fnv-nx", "ext")       # the decomp/libnx symlinks are git-excluded
    if not os.path.exists(ext):
        os.symlink(os.path.realpath(os.path.join(REPO, "ports", "fnv-nx", "ext")), ext)
    w = os.path.join(work, "opt", "w-" + sha[:12])
    os.makedirs(os.path.join(w, "out"), exist_ok=True)
    script = f'''set -euo pipefail
source <("{perf}/../env.sh" --print)
"{perf}/build.sh" "{w}/b" >"{w}/build.log" 2>&1
python3 -I "{perf}/gen_stream.py" "{w}/streams" >/dev/null
python3 -I "{perf}/gpu_cost.py" "{w}/b/n1/shaders" "$UAM" "{w}/out/gpu.json" >/dev/null
"{w}/b/native_drv_cap" "{w}/streams/mix_hi.nxd3d" 0 "{w}/out/mix_hi.nxcap" - 2 >/dev/null
"{perf}/dk/build.sh" "{w}/dk" >/dev/null 2>&1
'''
    sh(["bash", "-c", script])
    env = dict(os.environ)
    env["PATH"] = sh(["bash", "-c", f'source <("{perf}/../env.sh" --print); echo $PATH']).strip()
    code = f'''
import json, sys
sys.path.insert(0, "{perf}"); sys.path.insert(0, "{perf}/dk")
import emu_count, gen_stream, count
k = gen_stream.FRAMES - 1
def seg(r):
    m = {{int(a): b for a, b in r["marks"].items()}}
    return m[4 * k + 2] - m[4 * k + 1], m[4 * k + 3] - m[4 * k + 2]
real = emu_count.count("{w}/b/native_drv.nso", "{w}/streams/mix_hi.nxd3d", "{w}/emu/real", False, 2, 4 * k + 1, "{w}/b/native_drv.elf")
dry = emu_count.count("{w}/b/native_drv.nso", "{w}/streams/mix_hi.nxd3d", "{w}/emu/dry", True, 2)
(rb, rf), (db, df) = seg(real), seg(dry)
dk = count.run("{w}/dk/dkrec.elf", "{w}/out/mix_hi.nxcap", "{w}/b/n1/shaders", True)
g = json.load(open("{w}/out/gpu.json"))
gpu = {{}}
for s in g["scenes"]["mix_hi"]:
    if s["config"] in ("docked_720p", "handheld_720p"):
        gpu["%s_eff_%.1f" % (s["config"], s["efficiency"])] = s["frame_ms"]
prof = [(f, n) for f, n in real.get("profile", {{}}).items() if f != "main"][:15]
print(json.dumps({{"commit": "{sha}", "result": real["result"], "main": rb - db, "render_front": rf - df,
                  "render_backend": dk["record"], "backend_draws": dk["last_frame"]["draws"], "gpu_ms": gpu,
                  "top_front": prof, "top_backend": list((dk.get("profile_last_frame") or {{}}).items())[:10]}}))
'''
    snap = json.loads(sh([sys.executable, "-I", "-c", code], env=env).strip().splitlines()[-1])
    json.dump(snap, open(cache, "w"), indent=1)
    return snap


def entry(sha, name, before, after):
    g = lambda s, c: s["gpu_ms"].get(c + "_eff_0.6")
    return {"commit": sha, "name": name, "before_commit": before["commit"],
            "cpu_instr_delta_main": after["main"] - before["main"],
            "cpu_instr_delta_render": (after["render_front"] + after["render_backend"]) - (before["render_front"] + before["render_backend"]),
            "gpu_ms_docked_720p": [g(before, "docked_720p"), g(after, "docked_720p")],
            "gpu_ms_handheld_720p": [g(before, "handheld_720p"), g(after, "handheld_720p")],
            "before": before, "after": after}


def main():
    a = sys.argv[1:]
    if not a:
        raise SystemExit(__doc__)
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    work = opt("--work", "/home/user/work/p6n")
    sha = full_sha(a[0])
    subj = sh(["git", "-C", REPO, "log", "-1", "--format=%s", sha]).strip()
    entries = json.load(open(OPT_JSON)) if os.path.exists(OPT_JSON) else []
    if "--baseline" in a:
        s = measure(sha, work)
        e = entry(sha, opt("--name", "baseline"), s, s)
    else:
        b = opt("--before") or (entries[-1]["commit"] if entries else sha + "^")
        bsha = full_sha(b)
        e = entry(sha, opt("--name", subj), measure(bsha, work), measure(sha, work))
    print(json.dumps({k: v for k, v in e.items() if k not in ("before", "after")}, indent=1))
    if "--dry-run" not in a:
        entries = [x for x in entries if x["commit"] != sha] + [e]
        json.dump(entries, open(OPT_JSON, "w"), indent=1)
        print("appended to", OPT_JSON)


if __name__ == "__main__":
    main()
