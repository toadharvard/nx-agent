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
# frontier goes past the renderer), EMU_TIMEOUT=${BOOTLOOP_EMU_TIMEOUT:-480}.
# The first iteration on a machine compiles every unit (about an hour); the report says "cold" then.
# Report: the stage table (seconds, cached or ran, from each stage.json), the boot's game milestones (n/10 and the
# last reached), its stop, and the sidecar <report>.json (fnvnx-bootloop/1). Nothing is published but the report
# (the full farm publishes links). Exit 0 when the boot ran; 1 when the guest could not be built (the stage named).
# Env: BOOTLOOP_EMU_TIMEOUT, BOOTLOOP_NULLDEV (default 1), JOBS, FARM_REAL_EXE (lib_job.sh), LINKCACHE=0.
set -euo pipefail
unset FNVNX_STUB_NULLDEV   # set for the stub boot only (below)
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
step() { local n; n=$(date +%s); STEPS+=("$1	$((n - ST))"); ST=$n; }   # step <name>: seconds since the last step
F=$FARM_HOME/bootloop; B=$F/build; mkdir -p "$F"
FAIL=""

host_check git python3 make curl jq rsync gcc g++ py:unicorn py:elftools py:mako meson ninja flex bison
job_sources "$REPO"
# shellcheck source=recon.sh
. "$HERE/recon.sh"
fetch_recon "$ARTIFACTS" "$F/recon" > /dev/null 2>&1 || true
step sources

# the toolchain: the same directory as full_job.sh's, shared by the commits that build it the same way
TCID=$(cat "$P"/toolchain/sources.sh "$P"/toolchain/gcc/build.sh "$P"/toolchain/devkita64/* "$P"/toolchain/cross/* \
	"$P"/toolchain/vorbis/build.sh "$P"/tools/fetch.sh | sha256sum | cut -c1-12)
TCB=$FARM_HOME/toolchain-$TCID; mkdir -p "$TCB"
make -C "$P" fetch toolchain BUILD_DIR="$TCB" EXT="$EXT" JOBS="$JOBS" TESTS=none > "$TCB/make.log" 2>&1 || true
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

STUB=$F/stub; rm -rf "$STUB"
if [ -z "$FAIL" ]; then
	NULLDEV=${BOOTLOOP_NULLDEV:-1}; mkdir -p "$STUB"; echo "$NULLDEV" > "$STUB/nulldev"
	FNVNX_STUB_NULLDEV=$NULLDEV FARM_LINK_DIR=$B/link FARM_GUEST_DIR=$B/guest FARM_BOOTS=stub \
		EMU_EXTRA="${BOOTLOOP_EMU_EXTRA:-}" FARM_UNITS="$B/prepare/tree/build/units.json" \
		EMU_TIMEOUT="${BOOTLOOP_EMU_TIMEOUT:-480}" \
		bash "$P/tests/s5/farm_boot.sh" "$COMMIT" "$STUB" ${GAME_EXE:+"$GAME_EXE"} > "$F/stub_boot.log" 2>&1 \
		|| log "farm_boot.sh (stub boot) exited $?"
	[ -f "$STUB/boot_summary_stub.json" ] || FAIL="the stub boot wrote no summary (see $F/stub_boot.log)"
	step "stub boot"
fi
# the linkcache back to the artifacts repository when the link compiled something new (LINKCACHE_PUBLISH=0: not)
if [ -z "$FAIL" ] && [ "${LINKCACHE:-1}" = 1 ] && [ "${LINKCACHE_PUBLISH:-1}" = 1 ] && [ -z "${FARM_WIP_BRANCH:-}" ]; then
	LINKCACHE_NOTE="bootloop $C12" bash "$HERE/linkcache.sh" publish "$FNVNX_LINK_OBJCACHE" >> "$F/linkcache.log" 2>&1 || true
	step "cache publish"
fi

printf '%s\n' "${STEPS[@]}" > "$F/steps.tsv"
python3 -I - "$B" "$STUB" "$F/steps.tsv" "$REPORT" "$COMMIT" "$(( $(date +%s) - T0 ))" "${COLD:-?}" "$FAIL" \
	"${BOOTLOOP_EMU_TIMEOUT:-480}" "${FARM_WIP_BRANCH:-}" "${TB:-0}" "$NOTE_LINKEQ" <<'EOF'
import json, os, sys
B, stub, steps, report, commit, total, cold, fail, emu_s, wip, tb, linkeq = sys.argv[1:13]
tb = int(tb)
total = int(total)
rd = lambda p: json.load(open(p)) if os.path.isfile(p) else None
stages = []
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
L += ['- iteration: %d s (%.1f min; target 15 min)%s' % (total, total / 60, '; cold: the first build on this machine' if cold == '1' else ''),
      '- stub boot: game milestones %d/%d; last reached: %s; null device: %s; EMU_TIMEOUT %s s' % (
          reached, len(ms), bs.get('milestone') or 'none', {'1': 'FNVNX_STUB_NULLDEV=1', '0': 'off'}.get(nd, '-'), emu_s),
      '- incremental link check (warm == cold): %s' % (linkeq or 'passed on this machine before (bootloop/linkeq.done)'
                                                      if os.path.isfile(os.path.join(os.path.dirname(B), 'linkeq.done')) or linkeq
                                                      else 'off'),
      '- stop: %s' % ' / '.join([str(stop.get('reason') or '-')] + [str(x) for x in (stop.get('messages') or [])[:2]])[:300],
      '', '| step | seconds |', '|---|---:|'] + ['| %s | %s |' % (n, s) for n, s in st] + [
      '', '| stage | status | run | seconds | note |', '|---|---|---|---:|---|'] + [
      '| %s | %s | %s | %s | %s |' % (x['stage'], x['status'], x['run'], '-' if x['seconds'] is None else x['seconds'], x['note'])
      for x in stages]
if ms:
    L += ['', '- game milestones: %s; last reached: %s' % (
        ', '.join('%s %s' % (m.get('name'), 'yes' if m.get('reached') else 'no') for m in ms), bs.get('milestone') or 'none')]
open(report, 'w').write('\n'.join(L) + '\n')
json.dump({'schema': 'fnvnx-bootloop/1', 'commit': commit, 'seconds': total, 'cold': cold == '1', 'error': fail or None,
           'steps': {n: int(s) for n, s in st}, 'stages': stages, 'nulldev': nd or None,
           'milestones': [[m.get('name'), bool(m.get('reached'))] for m in ms], 'reached': reached, 'of': len(ms),
           'last': bs.get('milestone'), 'linkeq': linkeq or None},
          open(report[:-3] + '.json' if report.endswith('.md') else report + '.json', 'w'), indent=1)
EOF
log "bootloop $C12: $(sed -n 3p "$REPORT")"
[ -z "$FAIL" ]
