#!/usr/bin/env bash
# quick_job.sh - farm job (T1-fastbench): verify one integration commit on one warm machine, running only what its
# diff can affect, each check on its warm cache, with one report of every check's verdict and time.
#
#   tools/farm/quick_job.sh <commit> <report.md>          (runner job `quick`; the warm farms: runner.sh qk<k> quick)
#
# 1. plan   tools/farm/quick_plan.py: the files changed since the last verified head ($FARM_HOME/quick/verified, or
#           QUICK_BASE) -> the checks (build, equiv, replay, programs); no verified head -> all of them.
#           The base is the last head this farm finished ($FARM_HOME/quick/last); a check that is not a pass in
#           $FARM_HOME/quick/status.json (failed, or never ran here) is planned again whatever the diff.
# 2. lanes, in parallel (4 CPUs):
#    A  build: full_job.sh (prepare, compile and link incremental in its persistent build directory; guest; emulated
#       boot; publishes link/<c12>)
#    B  equiv: equiv_job.sh 1 of 1 (object cache: only units whose source, headers, flags or compiler changed)
#    D  after lane A's build, beside it: threads_job.sh (D25 #7: the race checker's and the watchdog's tests, then the guest just built relinked
#       with the watchdog and run under qemu-aarch64 for THREADS_TIMEOUT, default 180 s here; tools/threads/README.md)
#       and frames_job.sh (per-thread CPU and frame pacing: the guest under linhost --profile for FRAMES_TIMEOUT,
#       default 180 s here; tools/perf/FRAMES.md) and mem_job.sh (the memory lane: the guest relinked with the
#       recorder for MEM_TIMEOUT, default 180 s here; the peak against 3.2 GiB; tools/perf/MEM.md)
#       and plat_job.sh (R4's platform lane, PLAT_TIMEOUT default 180 s here; tools/perf/PLAT.md)
#       and scenario_job.sh (S18, D25's targets on scripted scenarios: QUICK_SCENARIOS, default "boot-menu saveload",
#       on this build's link and guest, EMU_TIMEOUT 900 s each, linhost 180 s without progress and 600 s in all;
#       the original's runs come from its reference cache)
#       and boottrace_job.sh (T1: the guest's function-boundary trace against the cached Wine reference trace of the
#       same list, S5-boot's tests/s5/boot_trace.py; gate: the first divergence does not move earlier than at the
#       verified head). A failed build skips lane D (its checks would run an older guest).
#    C  replay: sweep_job.sh 1 of 1 with SWEEP_CAPTURE=replay on the capture store's cover scenes (quick_plan.py
#       cover: the fewest scenes that hold every program; QUICK_CELLS, default 12), programs too when the plan says
#       so (SWEEP_PROGRAMS=1); the full replay of all 1,155 scenes stays on the sweep farms (36 shards, replay only)
# 3. report: one table (check, ran or why not, exit, seconds, headline of its report), the plan, the
#    sub-reports' heads; the wall-clock total is T1's metric. A run where every selected check exited 0 records the
#    commit as the next base ($FARM_HOME/quick/verified). A plan that says "full" (a key every cached result depends
#    on changed: toolchain, pins) is reported as such: the sharded farms do that run.
# Env: QUICK_BASE, QUICK_CHECKS (a comma list that replaces the plan), QUICK_CELLS, QUICK_COLD_LOCAL,
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
export LINKCACHE_PUBLISH=${LINKCACHE_PUBLISH:-0} EQUIV_CACHE_PUBLISH=${EQUIV_CACHE_PUBLISH:-0}
# lane A builds in full_job's persistent build directory (incremental): it does not wait for the prepare/compile
# farms' tree and objects (full_job's default is 300 s each, a third of the budget idle); one look, then local. V1's
# link audits keep running, with a smaller budget (its slow audits are skipped and marked so).
export FARM_TREE_WAIT=${FARM_TREE_WAIT:-0} FARM_OBJ_WAIT=${FARM_OBJ_WAIT:-0} V1_BUDGET=${V1_BUDGET:-600}
log() { echo "$(date -u +%FT%TZ) quick_job: $*"; }

# ---- 1. plan --------------------------------------------------------------------------------------------------------
# the base is the last head this farm finished, whatever its verdict ($Q/last); every check whose last result here is
# not a pass ($Q/status.json: per check, updated by the checks that ran) is planned again whatever the diff, so one
# failing check neither hides itself nor sends every later head back to "every check" (qk2: a cold run with a failure)
BASE=${QUICK_BASE:-$(cat "$Q/last" 2>/dev/null || cat "$Q/verified" 2>/dev/null || true)}
git -C "$REPO" cat-file -e "$COMMIT^{commit}" 2>/dev/null || git -C "$REPO" fetch -q origin "$COMMIT" 2>/dev/null || true
if [ -n "$BASE" ] && git -C "$REPO" cat-file -e "$BASE^{commit}" 2>/dev/null && [ "$BASE" != "$COMMIT" ]; then
	python3 -I "$HERE/quick_plan.py" plan "$REPO" "$BASE" "$COMMIT" --out "$R/plan.json" || BASE=""
fi
if [ -z "$BASE" ] || [ ! -s "$R/plan.json" ]; then
	printf '{"files": null, "checks": {"build": true, "equiv": true, "replay": true, "programs": false, "threads": true, "frames": true, "mem": true, "scenario": true, "boottrace": true, "plat": true}, "why": {"all": ["no verified head"]}, "full": [], "base": null}\n' >"$R/plan.json"
fi
if [ -f "$Q/status.json" ] && [ -n "$BASE" ]; then
	python3 -I - "$R/plan.json" "$Q/status.json" <<'PY'
import json, sys
p, st = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
for c in p['checks']:
    if c == 'programs':          # only when the translator changed (replay carries the sweep's state)
        continue
    if st.get(c) != 'pass' and not p['checks'][c]:
        p['checks'][c] = True
        p.setdefault('why', {}).setdefault(c, []).append('(not a pass here before: %s)' % (st.get(c) or 'never ran'))
if any(p['checks'].get(c) for c in ('threads', 'frames', 'mem', 'plat', 'scenario', 'boottrace')):
    p['checks']['build'] = True   # their inputs: this head's link / build directory
json.dump(p, open(sys.argv[1], 'w'), indent=1)
PY
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
# the head's worktree once, before the lanes: every sub-job of this head uses $FARM_HOME/src-<c12> (worktree.sh locks
# the add as well; qk2 on ed380ec7 lost build, fuzz and audit to two jobs adding it at the same moment)
bash "$HERE/worktree.sh" "$REPO" "$FARM_HOME/src-$C12" "$COMMIT" >/dev/null 2>&1 || log "worktree src-$C12 failed (the jobs try again)"
run() {   # run <check> <cmd...>: time, exit status, report path, in $R/<check>.{rc,s,md,log}
	local c=$1 t=$(date +%s) rc; shift
	log "start $c"
	"$@" "$R/$c.md" >"$R/$c.log" 2>&1; rc=$?
	echo "$rc" >"$R/$c.rc"; echo $(( $(date +%s) - t )) >"$R/$c.s"
	log "done $c rc=$rc ($(cat "$R/$c.s") s)"
	return $rc
}
skip() { echo "$2" >"$R/$1.skip"; }
build_verdict() {   # full_job exits 0 once its report is written: the build passes only when its stages did
	# (qk1 on 78435e5a: prepare failed, the check said 0, fuzz then took a stale link). prepare, compile, link and
	# guest must be ok/warn in the build directory and the report's provenance must not FAIL; else build.rc = 2.
	[ "$(cat "$R/build.rc" 2>/dev/null)" = 0 ] || return 0
	local why
	why=$(python3 -I - "$FARM_HOME/full/build" "$R/build.md" <<'PY'
import json, os, sys
b, md = sys.argv[1], sys.argv[2]
bad = []
for st in ('prepare', 'compile', 'link', 'guest'):
    try:
        r = json.load(open(os.path.join(b, st, 'stage.json')))
    except (OSError, ValueError):
        bad.append('%s: no record' % st); continue
    if r.get('status') not in ('ok', 'warn'):
        bad.append('%s: %s' % (st, r.get('status')))
if os.path.isfile(md) and any(l.startswith('- provenance: FAIL') for l in open(md, errors='replace')):
    bad.append('provenance FAIL')
print('; '.join(bad))
PY
)
	if [ -n "$why" ]; then echo 2 >"$R/build.rc"; echo "the build failed: $why" >"$R/build.why"; log "build: $why"; fi
}
lane_a() {
	if on build; then run build bash "$J/full_job.sh" "$COMMIT"; build_verdict; else skip build "not affected"; fi
	if [ -f "$R/build.rc" ] && [ "$(cat "$R/build.rc")" != 0 ]; then   # lane D would run an older build's guest
		for c in threads frames mem plat scenario boottrace; do skip $c "the build failed"; done
	else lane_d & fi   # the emulator lanes need this build; they run beside it
	wait
}
lane_d() {   # D: the emulator lanes on lane A's build, one after another (each runs a multi-threaded qemu)
	if on threads; then THREADS_TIMEOUT=${THREADS_TIMEOUT:-180} run threads bash "$J/threads_job.sh" "$COMMIT"
	else skip threads "not affected"; fi
	if on frames; then FRAMES_TIMEOUT=${FRAMES_TIMEOUT:-180} run frames bash "$J/frames_job.sh" "$COMMIT"
	else skip frames "not affected"; fi
	if on mem; then MEM_TIMEOUT=${MEM_TIMEOUT:-180} run mem bash "$J/mem_job.sh" "$COMMIT"
	else skip mem "not affected"; fi
	if on plat; then PLAT_TIMEOUT=${PLAT_TIMEOUT:-180} run plat bash "$J/plat_job.sh" "$COMMIT"
	else skip plat "not affected"; fi
	if on scenario; then
		local fb=$FARM_HOME/full/build ld="" gd=""
		[ -d "$fb/link/out" ] && ld=$fb/link
		[ -f "$fb/guest/guest.nso" ] && gd=$fb/guest
		# linhost (D25 #12): 180 s without progress ends a scenario here (the farm default 600 s: on aec30808 both
		# scenarios stopped early and waited it out, 3,057 s for the check), 600 s in all
		SCENARIO_PROGRESS_S=${SCENARIO_PROGRESS_S:-180} SCENARIO_LINHOST_S=${SCENARIO_LINHOST_S:-600} \
		SCENARIOS=${QUICK_SCENARIOS:-boot-menu saveload} EMU_TIMEOUT=${SCENARIO_EMU_TIMEOUT:-900} SCENARIO_PUBLISH=0 \
			SCENARIO_LINK_DIR=$ld SCENARIO_GUEST_DIR=$gd run scenario bash "$J/scenario_job.sh" "$COMMIT"
	else skip scenario "not affected"; fi
	if on boottrace; then BOOTTRACE_BUILD=$FARM_HOME/full/build run boottrace bash "$J/boottrace_job.sh" "$COMMIT"
	else skip boottrace "not affected"; fi
}
eq_cache_after() {   # one complete set of the eq farms' cache shards (equiv/cache/kofN for k = 1..N, each "equiv object
	# cache <c12> shard k/N") is of a head at or after $1: the cold run is over, a run here seeds from a warm cache.
	# Any N: eqa's 1of1 (the eq farm since 06:40) counts, and the retired eq1-eq6's 1of6..6of6 do not hold it back
	local A=${ARTIFACTS:-}; [ -n "$A" ] || { if [ -d /home/user/fnv-nx-build/.git ]; then A=/home/user/fnv-nx-build; else A=$FARM_HOME/artifacts; fi; }
	local refs r c n k bad
	refs=$(git -C "$A" ls-remote origin 'refs/heads/equiv/cache/*' 2>/dev/null | awk '{print $2}')
	[ -n "$refs" ] || return 1
	for n in $(printf '%s\n' $refs | sed -n 's|.*/[0-9]*of\([0-9]*\)$|\1|p' | sort -un); do
		bad=0
		for k in $(seq 1 "$n"); do
			r=refs/heads/equiv/cache/${k}of$n
			printf '%s\n' $refs | grep -qx "$r" || { bad=1; break; }
			git -C "$A" fetch -q --depth 1 --filter=blob:none origin "+$r:refs/quick-eqc/${r#refs/heads/}" 2>/dev/null || { bad=1; break; }
			c=$(git -C "$A" log -1 --format=%s "refs/quick-eqc/${r#refs/heads/}" | sed -n 's/^equiv object cache \([0-9a-f]\{12\}\) .*/\1/p')
			[ -n "$c" ] && git -C "$REPO" merge-base --is-ancestor "$1" "$c" 2>/dev/null || { bad=1; break; }
		done
		[ $bad = 0 ] && return 0
	done
	return 1
}
lane_b() {
	if ! on equiv; then skip equiv "not affected"; return; fi
	# planned again after a cold skip (status: "skipped: cold cache ... at <c12>"): still cold here until the eq farms
	# have published the cache of a head at or after it
	local carried; carried=$(python3 -I -c 'import json, os, re, sys
s = json.load(open(sys.argv[1])).get("equiv", "") if os.path.isfile(sys.argv[1]) else ""
m = re.match(r"skipped: cold cache.* at ([0-9a-f]{12})$", s)
print(m.group(1) if m else "")' "$Q/status.json" 2>/dev/null)
	if [ -n "$carried" ] && [ "${QUICK_COLD_LOCAL:-0}" != 1 ] && ! eq_cache_after "$carried"; then
		skip equiv "cold cache (since $carried): the eq farms have not published a warm cache yet"; return
	fi
	# a cold cache (quick_plan COLD: the MSVC shim headers): the whole check, hours here; the eq farms' sharded run
	# verifies the head and publishes the cache the next run seeds from (QUICK_COLD_LOCAL=1: run it here anyway)
	local cold; cold=$(python3 -I -c 'import json, sys; print(len(json.load(open(sys.argv[1])).get("cold", {}).get("equiv", [])))' "$R/plan.json" 2>/dev/null || echo 0)
	if [ "${cold:-0}" != 0 ] && [ "${QUICK_COLD_LOCAL:-0}" != 1 ]; then
		skip equiv "cold cache ($cold files every key reads): left to the eq farms' sharded run"; return
	fi
	run equiv bash "$J/equiv_job.sh" "$COMMIT" 1 1
}
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
rows, ok, failed = [], True, []
for c in ('build', 'equiv', 'replay', 'threads', 'frames', 'mem', 'plat', 'scenario', 'boottrace'):
    sk, rc = rd(c + '.skip'), rd(c + '.rc')
    if sk is not None:
        rows.append((c, 'skipped: ' + sk, '-', '-', ''))
    elif rc is None:
        rows.append((c, 'not run (lane skipped)', '-', '-', ''))
    else:
        ok &= rc == '0'
        hl = rd(c + '.why') or headline(rd(c + '.md'))
        if rc != '0':        # a failed check never reads as its counts (qk1 89dcf1c0's replay: rc 1, "programs checked 0")
            failed.append(c)
            hl = 'FAILED (exit %s): %s' % (rc, hl.replace('FAIL: ', '', 1) if hl.startswith('FAIL: ') else hl)
        rows.append((c, 'ran', rc, rd(c + '.s') or '?', hl))
full = plan.get('full') or []
# T1: a failed check is a FAIL whatever else the plan says (qk1 89dcf1c0: build rc 2, the verdict read "FULL RUN
# NEEDED"); a full run needed is named beside it
verdict = ('FAIL (%s)' % ', '.join(failed) + ('; a full run is needed too' if full else '')) if not ok else \
    ('FULL RUN NEEDED' if full else 'PASS (every selected check exited 0)')
o = open(out, 'w')
o.write('# quick %s\n\n' % commit[:12])
o.write('- verdict: %s\n- wall clock: %d s (%.1f min) on one machine\n' % (verdict, secs, secs / 60))
o.write('- base: %s; %s changed files\n' % (base[:12] or 'none (no verified head: every check)', plan.get('files')))
o.write('- checks: %s\n' % (', '.join(c for c, v in plan['checks'].items() if v) or 'none'))
for f in full:
    o.write('- full run needed: %s\n' % f)
for c, fs in sorted((plan.get('cold') or {}).items()):
    o.write('- %s: cold cache, left to its sharded farms (%s)\n' % (c, '; '.join(fs[:3]) + (' ...' if len(fs) > 3 else '')))
cv = rd('cover.txt')
if cv:
    o.write('- replay scenes: %s\n' % cv.splitlines()[-1])
o.write('\n| check | run | exit | seconds | headline |\n|---|---|---:|---:|---|\n')
for r in rows:
    o.write('| %s | %s | %s | %s | %s |\n' % r)
o.write('\n## why\n\n')
for c, ps in sorted(plan.get('why', {}).items()):
    o.write('- %s: %s\n' % (c, ', '.join(ps[:8]) + (' ...' if len(ps) > 8 else '')))
for c in ('build', 'equiv', 'replay', 'threads', 'frames', 'mem', 'plat', 'scenario', 'boottrace'):
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
# the next base and each check's standing (a check that did not run keeps the one it had)
echo "$COMMIT" >"$Q/last"
python3 -I - "${REPORT%.md}.json" "$Q/status.json" <<'PY'
import json, os, sys
r = json.load(open(sys.argv[1]))
st = json.load(open(sys.argv[2])) if os.path.isfile(sys.argv[2]) else {}
for c, v in r['checks'].items():
    if v['run'] == 'ran':
        st[c] = 'pass' if v['rc'] == '0' else 'fail rc %s at %s' % (v['rc'], r['commit'][:12])
    elif not v['run'].startswith('skipped: not affected'):
        st[c] = '%s at %s' % (v['run'], r['commit'][:12])      # the build failed, no store yet, lane skipped
json.dump(st, open(sys.argv[2], 'w'), indent=1)
PY
if [ "$st" = 0 ]; then echo "$COMMIT" >"$Q/verified"
	# the boot trace's first divergence becomes the bar for the next head (boottrace_job.sh: "not earlier")
	cand=$FARM_HOME/boottrace/$C12/boottrace.candidate; [ -f "$cand" ] && cp "$cand" "$Q/boottrace.base"
	log "verified $C12 ($((T1 - T0)) s)"; fi
echo "$REPORT"
exit 0
