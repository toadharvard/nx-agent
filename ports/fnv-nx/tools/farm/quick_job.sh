#!/usr/bin/env bash
# quick_job.sh - farm job (T1-fastbench): verify one integration commit on one warm machine, running only what its
# diff can affect, each check on its warm cache, with one report of every check's verdict and time.
#
#   tools/farm/quick_job.sh <commit> <report.md>          (runner job `quick`; the warm farms: runner.sh qk<k> quick)
#
# 1. plan   tools/farm/quick_plan.py: the files changed since the last verified head ($FARM_HOME/quick/verified, or
#           QUICK_BASE) -> the checks (build, equiv, fuzz, audit, replay, programs); no verified head -> all of them.
# 2. lanes, in parallel (4 CPUs):
#    A  build: full_job.sh (prepare, compile and link incremental in its persistent build directory; guest; emulated
#       boot; publishes link/<c12>), then fuzz_job.sh 1 of 1 (verdict cache: only changed function keys; FUZZ_MAX_S,
#       default 600) and audit_job.sh 1 of 1 (row cache: only changed keys; AUDIT_BUDGET, default 300)
#    B  equiv: equiv_job.sh 1 of 1 (object cache: only units whose source, headers, flags or compiler changed)
#    D  after lane A's build, beside its fuzz and audit: threads_job.sh (D25 #7: the race checker's and the watchdog's tests, then the guest just built relinked
#       with the watchdog and run under qemu-aarch64 for THREADS_TIMEOUT, default 180 s here; tools/threads/README.md)
#       and frames_job.sh (per-thread CPU and frame pacing: the guest under linhost --profile for FRAMES_TIMEOUT,
#       default 180 s here; tools/perf/FRAMES.md) and mem_job.sh (the memory lane: the guest relinked with the
#       recorder for MEM_TIMEOUT, default 180 s here; the peak against 3.2 GiB; tools/perf/MEM.md)
#       and scenario_job.sh (S18, D25's targets on scripted scenarios: QUICK_SCENARIOS, default "boot-menu saveload",
#       on this build's link and guest, EMU_TIMEOUT 900 s each; the original's runs come from its reference cache)
#    C  replay: sweep_job.sh 1 of 1 with SWEEP_CAPTURE=replay on the capture store's cover scenes (quick_plan.py
#       cover: the fewest scenes that hold every program; QUICK_CELLS, default 12), programs too when the plan says
#       so (SWEEP_PROGRAMS=1); the full replay of all 1,155 scenes stays on the sweep farms (36 shards, replay only)
# 3. report: one table (check, ran or why not, exit, seconds, headline of its report), the plan, the
#    sub-reports' heads; the wall-clock total is T1's metric. A run where every selected check exited 0 records the
#    commit as the next base ($FARM_HOME/quick/verified). A plan that says "full" (a key every cached result depends
#    on changed: toolchain, pins) is reported as such: the sharded farms do that run.
# Env: QUICK_BASE, QUICK_CHECKS (a comma list that replaces the plan), QUICK_CELLS, FUZZ_MAX_S, AUDIT_BUDGET,
# QUICK_SKIP_LANES (e.g. "C"), QUICK_JOBS (a directory of stand-in sub-jobs, for tests). Everything the sub-jobs need (README.md); they run as on their own farms.
set -uo pipefail
COMMIT=${1:?commit}; REPORT=${2:?report.md}
HERE=$(cd "$(dirname "$0")" && pwd); P=$(cd "$HERE/../.." && pwd); REPO=$(cd "$P/../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}; export FARM_HOME
J=${QUICK_JOBS:-$HERE}   # the sub-jobs (tests: stand-ins)
C12=${COMMIT:0:12}; Q=$FARM_HOME/quick; R=$Q/$C12; rm -rf "$R"; mkdir -p "$R" "$(dirname "$REPORT")"
REPORT=$(cd "$(dirname "$REPORT")" && pwd)/$(basename "$REPORT")
T0=$(date +%s)
# the warm farms keep their caches local: three machines must not write one 1-of-1 branch (they are seeded from the
# sharded farms' branches; their own results stay on the machine). Reports still go to reports/<farm>.
export FUZZ_PUBLISH=${FUZZ_PUBLISH:-0} AUDIT_PUBLISH=${AUDIT_PUBLISH:-0} EQUIV_CACHE_PUBLISH=${EQUIV_CACHE_PUBLISH:-0}
# lane A builds in full_job's persistent build directory (incremental): it does not wait for the prepare/compile
# farms' tree and objects (full_job's default is 300 s each, a third of the budget idle); one look, then local. V1's
# link audits keep running, with a smaller budget (its slow audits are skipped and marked so).
export FARM_TREE_WAIT=${FARM_TREE_WAIT:-0} FARM_OBJ_WAIT=${FARM_OBJ_WAIT:-0} V1_BUDGET=${V1_BUDGET:-600}
log() { echo "$(date -u +%FT%TZ) quick_job: $*"; }

# ---- 1. plan --------------------------------------------------------------------------------------------------------
BASE=${QUICK_BASE:-$(cat "$Q/verified" 2>/dev/null || true)}
git -C "$REPO" cat-file -e "$COMMIT^{commit}" 2>/dev/null || git -C "$REPO" fetch -q origin "$COMMIT" 2>/dev/null || true
if [ -n "$BASE" ] && git -C "$REPO" cat-file -e "$BASE^{commit}" 2>/dev/null && [ "$BASE" != "$COMMIT" ]; then
	python3 -I "$HERE/quick_plan.py" plan "$REPO" "$BASE" "$COMMIT" --out "$R/plan.json" || BASE=""
fi
if [ -z "$BASE" ] || [ ! -s "$R/plan.json" ]; then
	printf '{"files": null, "checks": {"build": true, "equiv": true, "fuzz": true, "audit": true, "replay": true, "programs": false, "threads": true, "frames": true, "mem": true, "scenario": true}, "why": {"all": ["no verified head"]}, "full": [], "base": null}\n' >"$R/plan.json"
fi
if [ -n "${QUICK_CHECKS:-}" ]; then
	python3 -I - "$R/plan.json" "$QUICK_CHECKS" <<'EOF'
import json, sys
p = json.load(open(sys.argv[1])); want = set(sys.argv[2].split(','))
p['checks'] = {c: c in want for c in p['checks']}; p.setdefault('why', {})['QUICK_CHECKS'] = [sys.argv[2]]
json.dump(p, open(sys.argv[1], 'w'), indent=1)
EOF
fi
on() { python3 -I -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1]))["checks"].get(sys.argv[2]) else 1)' "$R/plan.json" "$1"; }
log "plan: base ${BASE:0:12} -> $C12: $(python3 -I -c 'import json,sys; p=json.load(open(sys.argv[1])); print(",".join(c for c,v in p["checks"].items() if v) or "nothing")' "$R/plan.json")"

# ---- 2. lanes -------------------------------------------------------------------------------------------------------
run() {   # run <check> <cmd...>: time, exit status, report path, in $R/<check>.{rc,s,md,log}
	local c=$1 t=$(date +%s) rc; shift
	log "start $c"
	"$@" "$R/$c.md" >"$R/$c.log" 2>&1; rc=$?
	echo "$rc" >"$R/$c.rc"; echo $(( $(date +%s) - t )) >"$R/$c.s"
	log "done $c rc=$rc ($(cat "$R/$c.s") s)"
	return $rc
}
skip() { echo "$2" >"$R/$1.skip"; }
lane_a() {
	if on build; then run build bash "$J/full_job.sh" "$COMMIT"; else skip build "not affected"; fi
	lane_d &   # the emulator lanes need this build; they run beside fuzz and audit
	if on fuzz; then
		FUZZ_MAX_S=${FUZZ_MAX_S:-600} FUZZ_REPORT_S=${FUZZ_REPORT_S:-120} run fuzz bash "$J/fuzz_job.sh" "$COMMIT" 1 1
	else skip fuzz "not affected"; fi
	if on audit; then AUDIT_BUDGET=${AUDIT_BUDGET:-300} run audit bash "$J/audit_job.sh" "$COMMIT" 1 1
	else skip audit "not affected"; fi
	wait
}
lane_d() {   # D: the emulator lanes on lane A's build, one after another (each runs a multi-threaded qemu)
	if on threads; then THREADS_TIMEOUT=${THREADS_TIMEOUT:-180} run threads bash "$J/threads_job.sh" "$COMMIT"
	else skip threads "not affected"; fi
	if on frames; then FRAMES_TIMEOUT=${FRAMES_TIMEOUT:-180} run frames bash "$J/frames_job.sh" "$COMMIT"
	else skip frames "not affected"; fi
	if on mem; then MEM_TIMEOUT=${MEM_TIMEOUT:-180} run mem bash "$J/mem_job.sh" "$COMMIT"
	else skip mem "not affected"; fi
	if on scenario; then
		local fb=$FARM_HOME/full/build ld="" gd=""
		[ -d "$fb/link/out" ] && ld=$fb/link
		[ -f "$fb/guest/guest.nso" ] && gd=$fb/guest
		SCENARIOS=${QUICK_SCENARIOS:-boot-menu saveload} EMU_TIMEOUT=${SCENARIO_EMU_TIMEOUT:-900} SCENARIO_PUBLISH=0 \
			SCENARIO_LINK_DIR=$ld SCENARIO_GUEST_DIR=$gd run scenario bash "$J/scenario_job.sh" "$COMMIT"
	else skip scenario "not affected"; fi
}
lane_b() { if on equiv; then run equiv bash "$J/equiv_job.sh" "$COMMIT" 1 1; else skip equiv "not affected"; fi; }
lane_c() {
	if ! on replay && ! on programs; then skip replay "not affected"; return; fi
	local store; store=$(ls -1dt "$FARM_HOME"/capture/*/ 2>/dev/null | head -1)
	local cells=() progs=0
	on programs && progs=1
	if on replay && [ -n "$store" ] && python3 -I "$HERE/quick_plan.py" cover "$store" --max "${QUICK_CELLS:-12}" --out "$R/cells.tsv" 2>"$R/cover.txt" && [ -s "$R/cells.tsv" ]; then
		cells=(SWEEP_CELL_LIST="$R/cells.tsv")
	elif on replay; then
		# no store on this machine yet: the first sweep fetches it; until then only the programs part, if planned
		echo "no capture store on this machine (the first sweep run fetches capture/<key>/*)" >"$R/cover.txt"
		[ "$progs" = 1 ] || { skip replay "no capture store here yet"; return; }
		cells=(SWEEP_CELLS=0)
	else cells=(SWEEP_CELLS=0); fi
	export SWEEP_CAPTURE=replay SWEEP_PROGRAMS=$progs "${cells[@]}"    # lane C runs in its own subshell
	run replay bash "$J/sweep_job.sh" "$COMMIT" 1 1
}
case ",${QUICK_SKIP_LANES:-}," in *,A,*) ;; *) lane_a & ;; esac
case ",${QUICK_SKIP_LANES:-}," in *,B,*) ;; *) lane_b & ;; esac
case ",${QUICK_SKIP_LANES:-}," in *,C,*) ;; *) lane_c & ;; esac
wait
T1=$(date +%s)

# ---- 3. report ------------------------------------------------------------------------------------------------------
python3 -I - "$R" "$REPORT" "$COMMIT" "${BASE:-}" "$((T1 - T0))" <<'EOF'
import json, os, sys
R, out, commit, base, secs = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
plan = json.load(open(os.path.join(R, 'plan.json')))
def rd(n):
    p = os.path.join(R, n)
    return open(p).read().strip() if os.path.isfile(p) else None
def headline(md):
    if not md:
        return ''
    for l in md.splitlines()[1:]:
        l = l.strip().lstrip('-* ').strip()
        if l and not l.startswith('|') and not l.startswith('#'):
            return l[:160].replace('|', '/')
    return ''
rows, ok = [], True
for c in ('build', 'equiv', 'fuzz', 'audit', 'replay', 'threads', 'frames', 'mem', 'scenario'):
    sk, rc = rd(c + '.skip'), rd(c + '.rc')
    if sk is not None:
        rows.append((c, 'skipped: ' + sk, '-', '-', ''))
    elif rc is None:
        rows.append((c, 'not run (lane skipped)', '-', '-', ''))
    else:
        ok &= rc == '0'
        rows.append((c, 'ran', rc, rd(c + '.s') or '?', headline(rd(c + '.md'))))
full = plan.get('full') or []
verdict = 'FULL RUN NEEDED' if full else ('PASS (every selected check exited 0)' if ok else 'FAIL')
o = open(out, 'w')
o.write('# quick %s\n\n' % commit[:12])
o.write('- verdict: %s\n- wall clock: %d s (%.1f min) on one machine\n' % (verdict, secs, secs / 60))
o.write('- base: %s; %s changed files\n' % (base[:12] or 'none (no verified head: every check)', plan.get('files')))
o.write('- checks: %s\n' % (', '.join(c for c, v in plan['checks'].items() if v) or 'none'))
for f in full:
    o.write('- full run needed: %s\n' % f)
cv = rd('cover.txt')
if cv:
    o.write('- replay scenes: %s\n' % cv.splitlines()[-1])
o.write('\n| check | run | exit | seconds | headline |\n|---|---|---:|---:|---|\n')
for r in rows:
    o.write('| %s | %s | %s | %s | %s |\n' % r)
o.write('\n## why\n\n')
for c, ps in sorted(plan.get('why', {}).items()):
    o.write('- %s: %s\n' % (c, ', '.join(ps[:8]) + (' ...' if len(ps) > 8 else '')))
for c in ('build', 'equiv', 'fuzz', 'audit', 'replay', 'threads', 'frames', 'mem', 'scenario'):
    md = rd(c + '.md')
    if md:
        o.write('\n## %s\n\n' % c + '\n'.join(md.splitlines()[:25]) + '\n')
o.close()
json.dump({'schema': 'fnvnx-quick/1', 'commit': commit, 'base': base or None, 'seconds': secs, 'verdict': verdict,
           'checks': {r[0]: {'run': r[1], 'rc': r[2], 'seconds': r[3]} for r in rows}},
          open(out[:-3] + '.json' if out.endswith('.md') else out + '.json', 'w'), indent=1)
sys.exit(0 if ok and not full else 4)
EOF
st=$?
if [ "$st" = 0 ]; then echo "$COMMIT" >"$Q/verified"; log "verified $C12 ($((T1 - T0)) s)"; fi
echo "$REPORT"
exit 0
