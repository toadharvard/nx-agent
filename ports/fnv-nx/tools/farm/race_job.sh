#!/usr/bin/env bash
# race_job.sh - farm job (D25 #7, runner job `race`): the race-checker guest of one commit, run under qemu with the
# race checker and the wait-graph watchdog on, as one report (tools/threads/README.md).
#
#   tools/farm/race_job.sh <commit> <report.md>          (farm: runner.sh <name> race)
#
# 1. inputs: the prepare farm's tree of <commit> and the RACE=1 objects of <commit> or of the newest ancestor with the
#    same compile inputs (compile_job.sh RACE=1: branches cr/<commit12>/<k>of<N>, FARM_OBJ_SHARDS default 4; lib_job.sh
#    job_inputs with OBJ_PREFIX=cr; waits FARM_TREE_WAIT / FARM_OBJ_WAIT, default 1800 / 3600 s), the pristine MSVC
#    objects and the player's exe, as full_job.sh takes them; the toolchain shared with full_job.sh
#    ($FARM_HOME/toolchain-<id>).
# 2. `make link` in its own build directory $FARM_HOME/race/build (the link stage only: the pipeline's guest stage
#    knows nothing of -fsanitize=thread; tools/threads/variant.sh RACE=1 links the guest with hb.c, the race
#    checker's runtime, and the --wrap of the Win32 sync calls). LINK_OVERRIDES=render as in full_job.sh.
# 3. tools/farm/threads_job.sh with THREADS_BUILD=that directory, THREADS_RACE=1, THREADS_EXE=the verified exe: the
#    checkers' tests, the variant guest under qemu-aarch64 for THREADS_TIMEOUT (default 300) s, tools/threads/report.py.
# Nothing is published but the report (runner.sh): this link is not the port's link (instrumented objects), so it
# never goes to link/<commit12>, link-head or nro/. <report.md> is threads_job.sh's report with the inputs on top;
# its <report>.json sidecar too (fnvnx-threads-job/1).
# Needs: full_job.sh's toolchain and link needs, qemu-user; root with FARM_APT=1 installs what host_check finds missing.
set -euo pipefail
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
t0=$(date +%s)
host_check git python3 make curl jq rsync dpkg-deb gcc g++ llvm-undname qemu-aarch64 $LINK_PY meson ninja flex bison
job_sources "$REPO"
RB=$FARM_HOME/race; LB=$RB/build; mkdir -p "$RB"
fail_report() { printf '# threads (RACE=1) %s\n\n- verdict: not run\n- %s\n' "${COMMIT:0:12}" "$1" >"$REPORT"; echo "$REPORT"; exit 0; }

# 1. toolchain (shared with full_job.sh), tree, RACE objects, pristine objects, exe
TCID=$(cat "$P"/toolchain/sources.sh "$P"/toolchain/gcc/build.sh "$P"/toolchain/devkita64/* "$P"/toolchain/cross/* \
	"$P"/tools/fetch.sh | sha256sum | cut -c1-12)
TCB=$FARM_HOME/toolchain-$TCID; mkdir -p "$TCB"
log "toolchain $TCB"
make -C "$P" fetch toolchain BUILD_DIR="$TCB" EXT="$EXT" JOBS="$JOBS" TESTS=none > "$TCB/make.log" 2>&1 || true
stage_ok "$TCB" toolchain || fail_report "toolchain stage failed ($TCB/make.log)"
art() { python3 -I -c 'import json,sys; r=json.load(open(sys.argv[1])); print(r.get("artifacts",{}).get(sys.argv[2]) or "")' \
	"$TCB/toolchain/stage.json" "$1"; }
ARGS=(FNVNX_TC="$(art prefix)" DEVKITPRO="$(art devkitpro)")
XCB=$(usable_cross "$(art cross_bin)"); [ -n "$XCB" ] && ARGS+=(CROSS_BIN="$XCB")
export OBJ_PREFIX=cr
job_inputs "$RB"
[ -n "$TREE" ] || fail_report "no tree: $NOTE_TREE"
[ -n "$OBJS" ] || fail_report "no RACE=1 objects: $NOTE_OBJS (compile farms: RACE=1 start.sh crc<k> compile <k> 4)"
job_pristine
job_gamedata
if [ -n "$GAME_EXE" ]; then export FNVNX_EXE=$GAME_EXE; else unset FNVNX_EXE; fi
[ -n "$PR" ] && ARGS+=(MSVC_CODE="$PR")
ARGS+=(PREPARED="$TREE" OBJECTS="$OBJS" LINK_OVERRIDES="${LINK_OVERRIDES:-render}")
log "tree: $NOTE_TREE; objects: $NOTE_OBJS; $NOTE_PR; exe: $NOTE_EXE"

# 2. the link stage of the RACE objects
rc=0
# shellcheck disable=SC2086
make -C "$P" link BUILD_DIR="$LB" EXT="$EXT" JOBS="$JOBS" TESTS=none "${ARGS[@]}" ${FARM_MAKE_ARGS:-} \
	> "$RB/make-link-$C12.log" 2>&1 || rc=$?
ls -1t "$RB"/make-link-*.log | tail -n +4 | xargs -r rm -f
stage_ok "$LB" link || fail_report "link stage failed (make exit $rc; $RB/make-link-$C12.log: $(tail -1 "$RB/make-link-$C12.log" | cut -c1-200))"
t1=$(date +%s)

# 3. the threads lane on that build, RACE=1
DEVKITPRO=$(art devkitpro) FNVNX_TC=$(art prefix) THREADS_BUILD=$LB THREADS_RACE=1 THREADS_EXE=${GAME_EXE:-} \
	bash "$P/tools/farm/threads_job.sh" "$COMMIT" "$RB/threads.md" > "$RB/threads_job.log" 2>&1 && trc=0 || trc=$?
{
	head -1 "$RB/threads.md" 2>/dev/null | sed 's/$/ (RACE=1)/' || echo "# threads (RACE=1) ${COMMIT:0:12}"
	echo
	echo "- inputs: tree $NOTE_TREE; objects $NOTE_OBJS; $NOTE_PR"
	echo "- link of the RACE objects: $((t1 - t0)) s (make exit $rc), not published; threads_job.sh exit $trc"
	tail -n +2 "$RB/threads.md" 2>/dev/null || echo "- threads_job.sh wrote no report (log $RB/threads_job.log)"
} >"$REPORT"
[ -f "$RB/threads.json" ] && cp "$RB/threads.json" "${REPORT%.md}.json"
echo "$REPORT"
