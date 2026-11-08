#!/usr/bin/env bash
# bootloop_job.sh - farm job `bootloop` (owner order: the boot must reach 10/10 milestones, and the fix -> link ->
# boot loop must be fast): one commit's guest built incrementally on this machine's warm state, then S5's stub boot,
# with the seconds of every stage. Target: under 15 min per iteration on a warm machine.
#
#   tools/farm/bootloop_job.sh <commit> <report.md>          runner job `bootloop` (an integration head, or a
#                                                            stream's head on a try farm: runner.sh WATCH=wip)
#
# One persistent build directory ($FARM_HOME/bootloop/build) for every commit, so the pipeline keeps what did not
# change: prepare is kept while every file prepare.py read is unchanged (traced.py) and otherwise rewrites only the
# changed files (their mtimes), compile rebuilds only the units whose sources or headers changed, the link takes
# its compiles from the link's compile cache (tools/link/objcache.py, $FARM_HOME/linkobj, seeded from linkobj/cache
# by tools/farm/linkcache.sh), the guest is relinked. `make guest` runs only fetch, toolchain, prepare, compile,
# link, guest (and nine, disabled): no census, raw boot, host, package or tests. Then tests/s5/farm_boot.sh
# FARM_BOOTS=stub on that guest with FNVNX_STUB_NULLDEV=1 (S5 a3b368ed: CreateDevice on the null device, so the
# frontier goes past the renderer), EMU_TIMEOUT=${BOOTLOOP_EMU_TIMEOUT:-300} (S5 620572bc: deterministic, about 10x
# faster than an instruction count; the D26 stub boot ran 37-40 s).
# The first iteration on a machine compiles every unit (about an hour); the report says "cold" then.
# Report: the stage table (seconds, cached or ran, from each stage.json), the boot's game milestones (n/10 and the
# last reached), its stop, and the sidecar <report>.json (fnvnx-bootloop/1). Nothing is published but the report
# (the full farm publishes links). Exit 0 when the boot ran; 1 when the guest could not be built (the stage named).
# Env: BOOTLOOP_EMU_TIMEOUT, BOOTLOOP_NULLDEV (default 1), JOBS, FARM_REAL_EXE (lib_job.sh), LINKCACHE=0.
set -euo pipefail
unset FNVNX_STUB_NULLDEV   # set for the stub boot only (below)
# the boots schedule as emu_guest.py does by default (S5 620572bc: threads switch at host calls, deterministic);
# no instruction count or timer slicing of ours, not even one the farm's environment carries (the timer slices
# of 2d1afaeb..6da559aa were nondeterministic). EMU_BUDGET: the raw boot's, tools/pipeline/build.py emu_slicing.
unset BUDGET SLICE_MS EMU_BUDGET EMU_SLICE_MS
COMMIT=${1:?commit}; REPORT=${2:?report.md}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
JOBS=${JOBS:-$(nproc)}
if [ -z "${ARTIFACTS:-}" ]; then
	if [ -d /home/user/fnv-nx-build/.git ]; then ARTIFACTS=/home/user/fnv-nx-build; else ARTIFACTS=$FARM_HOME/artifacts; fi
fi
export ARTIFACTS FARM_HOME JOBS
mkdir -p "$FARM_HOME" "$(dirname "$REPORT")"
REPORT=$(cd "$(dirname "$REPORT")" && pwd)/$(basename "$REPORT")
# shellcheck source=lib_job.sh
. "$HERE/lib_job.sh"
T0=$(date +%s); ST=$T0; STEPS=()
STUB_TRACE=$FARM_HOME/bootloop/stub_trace.tsv; rm -f "$STUB_TRACE"   # the stub boot's function trace (lockstep)
step() { local n; n=$(date +%s); STEPS+=("$1	$((n - ST))"); ST=$n; }   # step <name>: seconds since the last step
F=$FARM_HOME/bootloop; B=$F/build; mkdir -p "$F"
FAIL=""

host_check git python3 make curl jq rsync gcc g++ $LINK_PY meson ninja flex bison
# one worktree path for every head (lib_job.sh job_sources): the link's compile cache hits across heads
export FARM_STABLE_SRC=${FARM_STABLE_SRC:-$FARM_HOME/bootloop-src}
job_sources "$REPO"
# shellcheck source=recon.sh
. "$HERE/recon.sh"
fetch_recon "$ARTIFACTS" "$F/recon" > /dev/null 2>&1 || true
step sources

# the toolchain: the same directory as full_job.sh's, shared by the commits that build it the same way
TCID=$(cat "$P"/toolchain/sources.sh "$P"/toolchain/gcc/build.sh "$P"/toolchain/devkita64/* "$P"/toolchain/cross/* \
	"$P"/toolchain/vorbis/build.sh "$P"/tools/fetch.sh | sha256sum | cut -c1-12)
TCB=$FARM_HOME/toolchain-$TCID; mkdir -p "$TCB"
# T1: a fresh machine restores the built toolchain of this TCID from the artifacts repository (hostcache.sh) instead
# of building it (gcc ~45 min on 4 cores); the first machine to build it publishes it, in the background
TC_RESTORED=0
if [ ! -f "$TCB/toolchain/stage.json" ] && hostcache fetch toolchain "$TCID" "$TCB"; then TC_RESTORED=1; fi
make -C "$P" fetch toolchain BUILD_DIR="$TCB" EXT="$EXT" JOBS="$JOBS" TESTS=none > "$TCB/make.log" 2>&1 || true
if [ "$TC_RESTORED" = 0 ] && stage_ok "$TCB" toolchain; then
	hostcache_publish_bg toolchain "$TCID" "$TCB" --exclude toolchain/dl --exclude toolchain/src --exclude toolchain/work
fi
art() { python3 -I -c 'import json,sys; r=json.load(open(sys.argv[1])); print(r.get("artifacts",{}).get(sys.argv[2]) or "")' \
	"$TCB/toolchain/stage.json" "$1"; }
stage_ok "$TCB" toolchain || FAIL="the toolchain stage failed (see $TCB/make.log)"
step toolchain

if [ -z "$FAIL" ]; then
	ARGS=(FNVNX_TC="$(art prefix)" DEVKITPRO="$(art devkitpro)" LINK_OVERRIDES="${LINK_OVERRIDES:-render}")
	[ -n "$(art cross_bin)" ] && ARGS+=(CROSS_BIN="$(art cross_bin)")
	if [ -n "$(art vorbis)" ]; then ARGS+=(VORBIS="$(art vorbis)"); else ARGS+=(VORBIS=none); fi
	[ -n "${RENDER_LIB:-}" ] && ARGS+=(RENDER_LIB="$RENDER_LIB")
	job_pristine                          # the link's resolve --msvc-code, as full_job.sh links
	[ -n "$PR" ] && ARGS+=(PRISTINE_OBJS="$PR" MSVC_CODE="$PR")
	job_gamedata                          # the player's exe: the link's --exe, the boot's image
	[ -n "$GAME_EXE" ] && { ARGS+=(BOOT_IMAGE="$GAME_EXE"); export XCGEN_EXE="$GAME_EXE"; }
	# the player's exe for every link tool that reads FNVNX_EXE, as full_job.sh links (the same link outputs)
	if [ -n "$GAME_EXE" ]; then export FNVNX_EXE="$GAME_EXE"; else unset FNVNX_EXE; fi
	export FNVNX_LINK_OBJCACHE=${FNVNX_LINK_OBJCACHE:-$FARM_HOME/linkobj}
	[ "${LINKCACHE:-1}" = 1 ] && { bash "$HERE/linkcache.sh" seed "$FNVNX_LINK_OBJCACHE" >> "$F/linkcache.log" 2>&1 || true; }
	step inputs
	COLD=0; [ -f "$B/compile/objects.tsv" ] || COLD=1
	TB=$(date +%s)
	# shellcheck disable=SC2086
	make -C "$P" guest BUILD_DIR="$B" EXT="$EXT" JOBS="$JOBS" TESTS=none "${ARGS[@]}" ${BOOTLOOP_MAKE_ARGS:-} \
		> "$F/make.log" 2>&1 || true
	step build
	for s in prepare compile link guest; do
		stage_ok "$B" "$s" || { FAIL="the $s stage did not end ok/warn (see $F/make.log)"; break; }
	done
fi

# The incremental link's equivalence check (owner order: the warm link must be byte-identical to a cold one; leq1's
# job, here so it runs on the machine whose cache is in use): once per machine (bootloop/linkeq.done), on the first
# iteration whose link took compiles from the cache, the link and guest are built again with an empty cache at the
# same paths, and guest.nso and addrmap.tsv must have the same sha256. A FAIL keeps the warm outputs as
# link.warm / guest.warm, boots the cold ones (the reference), and fails the job; every later iteration checks again
# until one passes. BOOTLOOP_LINKEQ=0: never. About a cold link (once).
NOTE_LINKEQ=""
if [ -z "$FAIL" ] && [ "${BOOTLOOP_LINKEQ:-1}" = 1 ] && [ ! -f "$F/linkeq.done" ]; then
	hits=$(python3 -I -c 'import json, sys
m = json.load(open(sys.argv[1])).get("metrics", {})
print(int(sum(v for k, v in m.items() if k.startswith("link_objcache_") and k.endswith("_hits"))))' "$B/link/stage.json" 2>/dev/null || echo 0)
	if [ "${hits:-0}" -gt 0 ]; then
		warm=$(cd "$B" && sha256sum guest/guest.nso link/out/addrmap.tsv)
		rm -rf "$B/link.warm" "$B/guest.warm"; mv "$B/link" "$B/link.warm"; mv "$B/guest" "$B/guest.warm"
		EQC=$F/linkeq-cache; rm -rf "$EQC"
		# shellcheck disable=SC2086
		FNVNX_LINK_OBJCACHE=$EQC make -C "$P" guest BUILD_DIR="$B" EXT="$EXT" JOBS="$JOBS" TESTS=none "${ARGS[@]}" \
			${BOOTLOOP_MAKE_ARGS:-} > "$F/linkeq.log" 2>&1 || true
		rm -rf "$EQC"
		cold=$(cd "$B" && sha256sum guest/guest.nso link/out/addrmap.tsv 2>/dev/null || echo "no cold outputs")
		{ echo "commit $COMMIT; warm link: $hits compiles from the cache"; echo "warm:"; echo "$warm"; echo "cold:"; echo "$cold"; } > "$F/linkeq.txt"
		if [ "$warm" = "$cold" ]; then
			NOTE_LINKEQ="PASS: warm == cold (guest.nso, addrmap.tsv sha256; $hits cached compiles)"
			rm -rf "$B/link.warm" "$B/guest.warm"; cp "$F/linkeq.txt" "$F/linkeq.done"
		else
			NOTE_LINKEQ="FAIL: warm != cold (bootloop/linkeq.txt; link.warm and guest.warm kept)"
			FAIL="the incremental link differs from the cold link ($NOTE_LINKEQ)"
		fi
		echo "$NOTE_LINKEQ" >> "$F/linkeq.txt"; log "LINKEQ: $NOTE_LINKEQ"
	else
		NOTE_LINKEQ="not yet: this link took nothing from the cache (cold); the next iteration checks"
	fi
	step "linkeq (once)"
fi

# Lockstep at function granularity (owner: the seams as a list): the stub boot traces the function entries of the
# references' lists, and tests/s5/boot_trace.py compare lists, per reference, the first BOOTLOOP_LOCKSTEP_N (50)
# divergences with both caller chains (the guest trace filtered to that reference's list: --only-list). The exe's
# references (Wine, tools/lockstep/fnvtrace; each fetched once per commit into $F/lockref/<name>-<sha12>):
#   bt1   the newest trace/<link12> of tools/farm/boot_trace_job.sh: list.txt, wine.tsv.xz (the boot)
#   coc   S18's scenario/original trace-coc: list3.txt, wine.tsv.xz (boot -> menu -> coc -> in the cell; complete)
# BOOTLOOP_LOCKSTEP_REFS (default "bt1 coc") picks them; a reference not published yet is said so. The guest's list
# is the union of the image functions of all lists (the API lines, kernel32!..., are the exe's only).
# BOOTLOOP_LOCKSTEP=0: off.
LS_EXTRA=""; LS_NOTE="off"; LS_REFS=()
lockref() {   # lockref <name> <git ref> <list path> <trace path>: $F/lockref/<name>-<sha12> holding list.txt, wine.tsv
	local name=$1 ref=$2 lp=$3 tp=$4 sha d
	sha=$(git -C "$ARTIFACTS" rev-parse -q --verify "$ref^{commit}" 2>/dev/null) || return 1
	d=$F/lockref/$name-${sha:0:12}
	if [ ! -f "$d/.done" ]; then
		rm -rf "$F/lockref/$name-"*; mkdir -p "$d"
		git -C "$ARTIFACTS" show "$sha:$lp" > "$d/list.txt" 2>/dev/null && git -C "$ARTIFACTS" show "$sha:$tp" > "$d/wine.tsv.xz" 2>/dev/null \
			&& xz -df "$d/wine.tsv.xz" && [ -s "$d/list.txt" ] && [ -s "$d/wine.tsv" ] && touch "$d/.done"
	fi
	[ -f "$d/.done" ] && LS_REFS+=("$name|$d|$ref")
}
if [ -z "$FAIL" ] && [ "${BOOTLOOP_LOCKSTEP:-1}" = 1 ]; then
	notes=()
	for r in ${BOOTLOOP_LOCKSTEP_REFS:-bt1 coc}; do
		case "$r" in
			bt1)
				git -C "$ARTIFACTS" fetch -q --depth 1 origin "+refs/heads/trace/*:refs/remotes/origin/trace/*" 2>/dev/null || true
				ref=$(git -C "$ARTIFACTS" for-each-ref --sort=-committerdate --format='%(refname:short)' 'refs/remotes/origin/trace/' | head -1)
				if [ -z "$ref" ]; then notes+=("bt1: no trace/* branch yet (boot_trace_job.sh publishes them)")
				elif ! lockref bt1 "$ref" list.txt wine.tsv.xz; then notes+=("bt1: $ref could not be read"); fi ;;
			coc|newgame)
				git -C "$ARTIFACTS" fetch -q --depth 1 origin "+refs/heads/scenario/original:refs/remotes/origin/scenario/original" 2>/dev/null || true
				if ! git -C "$ARTIFACTS" rev-parse -q --verify refs/remotes/origin/scenario/original > /dev/null; then
					notes+=("$r: no scenario/original branch yet (S18)")
				elif ! lockref "$r" origin/scenario/original "trace-$r/list3.txt" "trace-$r/wine.tsv.xz"; then
					notes+=("$r: scenario/original trace-$r could not be read"); fi ;;
			*) notes+=("$r: unknown reference") ;;
		esac
	done
	if [ ${#LS_REFS[@]} -gt 0 ]; then
		UL=$F/lockref/union.txt
		for x in "${LS_REFS[@]}"; do d=${x#*|}; cat "${d%%|*}/list.txt"; done | python3 -I -c '
import re, sys
seen = set()
for l in sys.stdin:
    f = l.split()
    if f and re.fullmatch(r"[0-9a-fA-F]{1,8}", f[0]) and 0x400000 <= int(f[0], 16) < 0x2100000 and int(f[0], 16) not in seen:
        seen.add(int(f[0], 16)); sys.stdout.write(l if l.endswith("\n") else l + "\n")' > "$UL"
		LS_EXTRA="--trace-fn @$UL --trace-out $STUB_TRACE"
		for x in "${LS_REFS[@]}"; do n=${x%%|*}; d=${x#*|}; notes+=("$n: ${x##*|} ($(grep -c '^[0-9a-fA-F]' "${d%%|*}/list.txt") functions)"); done
		notes+=("the guest traced on $(wc -l < "$UL") functions (the lists' union)")
	fi
	LS_NOTE=$(printf '%s; ' "${notes[@]}"); LS_NOTE=${LS_NOTE%; }
fi
STUB=$F/stub; rm -rf "$STUB"
if [ -z "$FAIL" ]; then
	NULLDEV=${BOOTLOOP_NULLDEV:-1}; mkdir -p "$STUB"; echo "$NULLDEV" > "$STUB/nulldev"
	FNVNX_STUB_NULLDEV=$NULLDEV FARM_LINK_DIR=$B/link FARM_GUEST_DIR=$B/guest FARM_BOOTS=stub \
		EMU_EXTRA="${BOOTLOOP_EMU_EXTRA:-}${LS_EXTRA:+ $LS_EXTRA}" FARM_UNITS="$B/prepare/tree/build/units.json" \
		EMU_TIMEOUT="${BOOTLOOP_EMU_TIMEOUT:-300}" \
		bash "$P/tests/s5/farm_boot.sh" "$COMMIT" "$STUB" ${GAME_EXE:+"$GAME_EXE"} > "$F/stub_boot.log" 2>&1 \
		|| log "farm_boot.sh (stub boot) exited $?"
	[ -f "$STUB/boot_summary_stub.json" ] || FAIL="the stub boot wrote no summary (see $F/stub_boot.log)"
	step "stub boot"
fi
# Determinism (orchestrator: S5's timer slices of 2d1afaeb..6da559aa made boots nondeterministic): once per machine
# and emu_guest.py version (bootloop/determinism-<hash>.done), the same stub boot runs again with the same inputs,
# and tools/farm/boot_determinism.py compares the two: the same summary and function trace when both ended by
# themselves, else the shorter a prefix of the longer. A FAIL fails the job (checked again next iteration).
# BOOTLOOP_DETERMINISM=0: never.
NOTE_DET=""
EMUH=$(sha256sum "$P/tests/s5/emu_guest.py" 2>/dev/null | cut -c1-12)
if [ -z "$FAIL" ] && [ "${BOOTLOOP_DETERMINISM:-1}" = 1 ] && [ -n "$EMUH" ] && [ ! -f "$F/determinism-$EMUH.done" ]; then
	STUB2=$F/stub2; rm -rf "$STUB2"; mkdir -p "$STUB2"; T2=${STUB_TRACE%.tsv}-2.tsv; rm -f "$T2"
	FNVNX_STUB_NULLDEV=$NULLDEV FARM_LINK_DIR=$B/link FARM_GUEST_DIR=$B/guest FARM_BOOTS=stub \
		EMU_EXTRA="${BOOTLOOP_EMU_EXTRA:-}${LS_EXTRA:+ ${LS_EXTRA/$STUB_TRACE/$T2}}" FARM_UNITS="$B/prepare/tree/build/units.json" \
		EMU_TIMEOUT="${BOOTLOOP_EMU_TIMEOUT:-300}" \
		bash "$P/tests/s5/farm_boot.sh" "$COMMIT" "$STUB2" ${GAME_EXE:+"$GAME_EXE"} > "$F/stub_boot2.log" 2>&1 || true
	NOTE_DET=$(python3 -I "$HERE/boot_determinism.py" "$STUB" "$STUB2" ${LS_EXTRA:+--trace-a "$STUB_TRACE" --trace-b "$T2"} 2>&1) || true
	case "$NOTE_DET" in
		PASS:*) echo "$NOTE_DET" > "$F/determinism-$EMUH.done" ;;
		*) FAIL="two identical stub boots disagree: $NOTE_DET" ;;
	esac
	rm -rf "$STUB2" "$T2"
	step "determinism (once)"
elif [ -f "$F/determinism-$EMUH.done" ]; then
	NOTE_DET="passed on this machine for emu_guest.py $EMUH ($(cut -c1-120 "$F/determinism-$EMUH.done"))"
fi
rm -f "$F"/lockstep-*.txt "$F"/lockstep-*.json
if [ -n "$LS_EXTRA" ] && [ -z "$FAIL" ]; then
	if [ -s "$STUB_TRACE" ]; then
		U=$B/prepare/tree/build/units.json; [ -f "$U" ] || U=""
		for x in "${LS_REFS[@]}"; do
			n=${x%%|*}; d=${x#*|}; d=${d%%|*}
			python3 -I "$P/tests/s5/boot_trace.py" compare "$STUB_TRACE" "$d/wine.tsv" ${U:+--units "$U"} --only-list "$d/list.txt" \
				--max-div "${BOOTLOOP_LOCKSTEP_N:-50}" --json "$F/lockstep-$n.json" > "$F/lockstep-$n.txt" 2>&1 \
				|| LS_NOTE="$LS_NOTE; $n: boot_trace.py compare exited $? (see its block)"
		done
	else
		LS_NOTE="$LS_NOTE; the stub boot wrote no function trace"
	fi
	step lockstep
fi
# the linkcache back to the artifacts repository when the link compiled something new (LINKCACHE_PUBLISH=0: not)
if [ -z "$FAIL" ] && [ "${LINKCACHE:-1}" = 1 ] && [ "${LINKCACHE_PUBLISH:-1}" = 1 ] && [ -z "${FARM_WIP_BRANCH:-}" ]; then
	LINKCACHE_NOTE="bootloop $C12" bash "$HERE/linkcache.sh" publish "$FNVNX_LINK_OBJCACHE" >> "$F/linkcache.log" 2>&1 || true
	step "cache publish"
fi

printf '%s\n' "${STEPS[@]}" > "$F/steps.tsv"
python3 -I - "$B" "$STUB" "$F/steps.tsv" "$REPORT" "$COMMIT" "$(( $(date +%s) - T0 ))" "${COLD:-?}" "$FAIL" \
	"${BOOTLOOP_EMU_TIMEOUT:-300}" "${FARM_WIP_BRANCH:-}" "${TB:-0}" "$NOTE_LINKEQ" "$LS_NOTE" "$NOTE_DET" <<'EOF'
import json, os, sys
B, stub, steps, report, commit, total, cold, fail, emu_s, wip, tb, linkeq, ls_note, det = sys.argv[1:15]
tb = int(tb)
total = int(total)
rd = lambda p: json.load(open(p)) if os.path.isfile(p) else None
stages = []
failed_stages = []
for s in ('fetch', 'toolchain', 'prepare', 'compile', 'link', 'guest'):
    sp = os.path.join(B, s, 'stage.json')
    r = rd(sp) or {}
    ran = bool(r) and tb > 0 and os.path.getmtime(sp) >= tb    # a cached stage keeps its earlier record
    m = r.get('metrics') or {}
    extra = ''
    if s == 'compile' and 'compile_built_now' in m:
        extra = '%s units compiled now' % m['compile_built_now']
    if s == 'link':
        hits = sum(v for k, v in m.items() if k.startswith('link_objcache_') and k.endswith('_hits'))
        miss = sum(v for k, v in m.items() if k.startswith('link_objcache_') and k.endswith('_misses'))
        extra = 'compile cache %d hits, %d misses' % (hits, miss) if hits or miss else ''
    if s == 'prepare' and 'prepare_reused' in m:
        extra = 'kept (inputs identical)' if m['prepare_reused'] else 'prepare.py ran'
    if r.get('status') == 'fail':   # the failing stage's own error and log tail (not the job log's apt output)
        tail = list(r.get('log_tail') or [])
        lp = r.get('log') or os.path.join(B, s, 'stage.log')
        if not os.path.isabs(lp):
            lp = os.path.join(os.path.dirname(B), lp) if not os.path.isfile(lp) else lp
        if os.path.isfile(lp):
            tail = [l.rstrip('\n') for l in open(lp, errors='replace').readlines()[-40:]]
        failed_stages.append((s, r.get('error') or r.get('summary') or '', tail))
    stages.append({'stage': s, 'status': r.get('status', 'none'), 'run': 'ran' if ran else ('cached' if r else '-'),
                   'seconds': r.get('seconds') if ran else 0, 'note': extra if ran else ''})
st = [l.rstrip('\n').split('\t') for l in open(steps) if '\t' in l]
bs = rd(os.path.join(stub, 'boot_summary_stub.json')) or {}
ms = bs.get('milestones') or []
reached = sum(1 for m in ms if m.get('reached'))
stop = bs.get('stop') or {}
nd = open(os.path.join(stub, 'nulldev')).read().strip() if os.path.isfile(os.path.join(stub, 'nulldev')) else ''
L = ['# bootloop %s%s' % (commit[:12], (' (%s)' % wip) if wip else ''), '']
if fail:
    L += ['- FAIL: %s' % fail, '']
    for s_, err, tail in failed_stages:
        L += ['- the %s stage: %s' % (s_, err[:300] or 'no error recorded'), '', '```'] + [t[:300] for t in tail] + ['```', '']
# the path this head took (orchestrator: a head that changes only the link must not prepare or compile)
byst = {x['stage']: x for x in stages}
comp_now = None
cr = rd(os.path.join(B, 'compile', 'stage.json')) or {}
if byst.get('compile', {}).get('run') == 'ran':
    comp_now = (cr.get('metrics') or {}).get('compile_built_now')
prep_kept = byst.get('prepare', {}).get('run') == 'cached' or byst.get('prepare', {}).get('note', '').startswith('kept')
if prep_kept and not comp_now:
    path = 'link-only: the tree kept, nothing compiled'
elif prep_kept:
    path = 'incremental: the tree kept, %s units compiled' % comp_now
else:
    path = 'prepare ran%s' % ('' if comp_now is None else ', %s units compiled' % comp_now)
L += ['- path: %s; link %s s, guest %s s' % (path, byst.get('link', {}).get('seconds'), byst.get('guest', {}).get('seconds'))]
L += ['- iteration: %d s (%.1f min; target 15 min)%s' % (total, total / 60, '; cold: the first build on this machine' if cold == '1' else ''),
      '- stub boot: game milestones %d/%d; last reached: %s; null device: %s; EMU_TIMEOUT %s s' % (
          reached, len(ms), bs.get('milestone') or 'none', {'1': 'FNVNX_STUB_NULLDEV=1', '0': 'off'}.get(nd, '-'), emu_s),
      '- incremental link check (warm == cold): %s' % (linkeq or 'passed on this machine before (bootloop/linkeq.done)'
                                                      if os.path.isfile(os.path.join(os.path.dirname(B), 'linkeq.done')) or linkeq
                                                      else 'off'),
      '- determinism (two identical stub boots): %s' % (det or 'off'),
      '- stop: %s' % ' / '.join([str(stop.get('reason') or '-')] + [str(x) for x in (stop.get('messages') or [])[:2]])[:300],
      '', '| step | seconds |', '|---|---:|'] + ['| %s | %s |' % (n, s) for n, s in st] + [
      '', '| stage | status | run | seconds | note |', '|---|---|---|---:|---|'] + [
      '| %s | %s | %s | %s | %s |' % (x['stage'], x['status'], x['run'], '-' if x['seconds'] is None else x['seconds'], x['note'])
      for x in stages]
if ms:
    L += ['', '- game milestones: %s; last reached: %s' % (
        ', '.join('%s %s' % (m.get('name'), 'yes' if m.get('reached') else 'no') for m in ms), bs.get('milestone') or 'none')]
F = os.path.dirname(B)
import glob
L += ['', '## Lockstep: the boot\'s function entries against the exe under Wine', '', '- %s' % ls_note]
lsum = {}
for jp in sorted(glob.glob(os.path.join(F, 'lockstep-*.json'))):
    n = os.path.basename(jp)[len('lockstep-'):-5]
    lsj = rd(jp) or {}
    kinds = {}
    for d in lsj.get('divergences') or []:
        kinds[d['kind'].split()[0]] = kinds.get(d['kind'].split()[0], 0) + 1
    lsum[n] = {'matched': lsj.get('matched'), 'divergences': len(lsj.get('divergences') or []),
               'first': (lsj.get('first') or {}).get('function'), 'by_kind': kinds}
    L += ['', '### against %s' % n, '',
          '- %s calls of the guest, %s of the exe, %s matched in order; %d divergences listed (%s); first: %s' % (
              lsj.get('guest_calls'), lsj.get('wine_calls'), lsj.get('matched'), len(lsj.get('divergences') or []),
              ', '.join('%s %d' % kv for kv in sorted(kinds.items(), key=lambda kv: -kv[1])) or 'none',
              ('%s in %s' % (lsj['first']['kind'], lsj['first']['function'])) if lsj.get('first') else 'none')]
    tp = jp[:-5] + '.txt'
    if os.path.isfile(tp):
        L += ['', '```'] + [l.rstrip('\n')[:400] for l in open(tp, errors='replace')][:200] + ['```']
open(report, 'w').write('\n'.join(L) + '\n')
json.dump({'schema': 'fnvnx-bootloop/1', 'commit': commit, 'seconds': total, 'cold': cold == '1', 'error': fail or None,
           'steps': {n: int(s) for n, s in st}, 'stages': stages, 'nulldev': nd or None,
           'milestones': [[m.get('name'), bool(m.get('reached'))] for m in ms], 'reached': reached, 'of': len(ms),
           'last': bs.get('milestone'), 'linkeq': linkeq or None, 'determinism': det or None, 'path': path,
           'lockstep': dict({'note': ls_note}, refs=lsum)},
          open(report[:-3] + '.json' if report.endswith('.md') else report + '.json', 'w'), indent=1)
EOF
log "bootloop $C12: $(sed -n 3p "$REPORT")"
[ -z "$FAIL" ]
