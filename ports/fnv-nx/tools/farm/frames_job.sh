#!/usr/bin/env bash
# frames_job.sh - per-thread CPU and frame-pacing lane (D25 #12 groundwork: 60 fps at 1.02 GHz). T1's quick_job.sh
# check `frames` and a farm job. tools/perf/FRAMES.md.
#
#   tools/farm/frames_job.sh <commit> <report.md>
#
# 1. tests/s15/test_frames.py: frames.py on synthetic profiles, and linhost --profile on the mtqemu guest's frame
#    loop (the worker must be the critical path).
# 2. the guest of the full job's build (FRAMES_BUILD, default $FARM_HOME/full/build: guest/fnvnx.elf as built, no
#    relink) under tests/s15/mtqemu/boot_game.sh with linhost --profile <file> --profile-hz FRAMES_HZ (default 1000)
#    --frame <the D3D9 device's Present>: FRAMES_SYM, default every symbol nm finds matching FRAMES_MATCH (default
#    'IDirect3DDevice9.*7Present|IDirect3DSwapChain9.*7Present': the null device's, the proxy's), for FRAMES_TIMEOUT
#    seconds (default 300). FRAMES_PROFILES="<profile>..." (+ FRAMES_ELF, FRAMES_LOG): report those instead (S18).
# 3. tools/perf/frames.py (FRAMES_SCALE: the emulated-to-device factor, when calibrated).
# Exit 0 when the report is written; 1 when the profiler's own test failed.
set -uo pipefail
COMMIT=${1:?commit}; REPORT=${2:?report.md}
HERE=$(cd "$(dirname "$0")" && pwd); P=$(cd "$HERE/../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
C12=${COMMIT:0:12}; W=$FARM_HOME/frames/$C12; rm -rf "$W"; mkdir -p "$W" "$(dirname "$REPORT")"
REPORT=$(cd "$(dirname "$REPORT")" && pwd)/$(basename "$REPORT")
log() { echo "$(date -u +%FT%TZ) frames_job: $*"; }
T0=$(date +%s)

TEST_NOTE="not run (FRAMES_TESTS=0)"; TEST_RC=0
if [ "${FRAMES_TESTS:-1}" != 0 ]; then
	python3 -I "$P/tests/s15/test_frames.py" > "$W/test_frames.log" 2>&1; TEST_RC=$?
	TEST_NOTE=$(tail -1 "$W/test_frames.log")
fi
log "test_frames: $TEST_NOTE"

PROFS=(); ELF=${FRAMES_ELF:-}; BLOG=${FRAMES_LOG:-}; GUEST_NOTE=""
if [ -n "${FRAMES_PROFILES:-}" ]; then
	read -r -a PROFS <<< "$FRAMES_PROFILES"; GUEST_NOTE="profiles handed in: ${#PROFS[@]}"
else
	BUILD=${FRAMES_BUILD:-$FARM_HOME/full/build}
	EXE=${FRAMES_EXE:-$FARM_HOME/gamedata/FalloutNV.exe}
	WANT=${FARM_EXE_SHA256:-89e7020487e80f20ddc92ea641c4433b2c6839a82f45a076e414d167d11dd0fe}
	ELF=$BUILD/guest/fnvnx.elf
	if [ ! -f "$ELF" ]; then GUEST_NOTE="not run: no guest at $ELF (the full job's)"; ELF=""
	elif [ ! -f "$EXE" ] || [ -L "$EXE" ] || [ "$(sha256sum "$EXE" | cut -c1-64)" != "$WANT" ]; then
		GUEST_NOTE="not run: no verified player exe"
	elif ! command -v qemu-aarch64 >/dev/null; then GUEST_NOTE="not run: no qemu-aarch64"
	else
		NM=$(command -v aarch64-none-elf-nm || command -v aarch64-linux-gnu-nm || echo nm)
		if [ -n "${FRAMES_SYM:-}" ]; then SYMS=$FRAMES_SYM
		else SYMS=$("$NM" --defined-only "$ELF" | awk '$2 ~ /^[Tt]$/ {print $3}' \
				| grep -E "${FRAMES_MATCH:-IDirect3DDevice9.*7Present|IDirect3DSwapChain9.*7Present}" | sort -u | tr '\n' ' '); fi
		FA=(--profile "$W/profile.txt" --profile-hz "${FRAMES_HZ:-1000}")
		for s in $SYMS; do FA+=(--frame "$s"); done
		LINHOST_ARGS="${FA[*]}" TIMEOUT=${FRAMES_TIMEOUT:-300} EXPECT=${FRAMES_EXPECT:-fnvnx frames: run the whole time} \
			bash "$P/tests/s15/mtqemu/boot_game.sh" "$ELF" "$EXE" "$W/boot" > "$W/boot_game.log" 2>&1
		GUEST_NOTE="guest run: $(grep -E '^boot_game: (REACHED|NOT reached)' "$W/boot_game.log" | tail -1 | cut -c1-160); frame functions: $(echo $SYMS | wc -w)"
		[ -f "$W/profile.txt" ] && PROFS=("$W/profile.txt")
		BLOG=$W/boot/boot.log
	fi
fi
log "guest: $GUEST_NOTE"
RC=3
if [ ${#PROFS[@]} -gt 0 ]; then
	python3 -I "$P/tools/perf/frames.py" "${PROFS[@]}" ${ELF:+--elf "$ELF"} ${BLOG:+--log "$BLOG"} \
		${FRAMES_SCALE:+--scale "$FRAMES_SCALE"} --out "$W/frames.md" > "$W/frames.log" 2>&1; RC=$?
fi
{
	echo "# frames $C12"; echo
	if [ "$TEST_RC" != 0 ]; then echo "- verdict: FAIL (profiler tests)"
	elif [ "$RC" = 0 ]; then echo "- verdict: measured"; else echo "- verdict: no guest data"; fi
	echo "- profiler tests: $TEST_NOTE"; echo "- guest: $GUEST_NOTE"; echo "- $(( $(date +%s) - T0 )) s"
	[ -f "$W/frames.md" ] && tail -n +2 "$W/frames.md"
} > "$REPORT"
[ -f "$W/frames.json" ] && cp "$W/frames.json" "${REPORT%.md}.json"
log "report: $REPORT"
[ "$TEST_RC" = 0 ] || exit 1
exit 0
