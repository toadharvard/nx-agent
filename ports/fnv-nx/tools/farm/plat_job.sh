#!/usr/bin/env bash
# plat_job.sh - the platform lane (D25 #3 unimplemented platform calls, #4 failed file opens, #6 audio decode and
# input mapping failures). T1's quick_job.sh check `plat` (lane D) and a farm job. tools/perf/PLAT.md.
#
#   tools/farm/plat_job.sh <commit> <report.md>
#
# 1. tests/s15/test_plat_report.py (plat.py on synthetic logs). The recorder itself is tested by tests/s4's
#    plat_lane_opens_stubs_counts.
# 2. the guest, one of:
#    PLAT_LOGS="<log>..." (+ PLAT_ELF): logs of a run with the recorder on (S18's scenarios); only reported;
#    else the full job's build (PLAT_BUILD, default $FARM_HOME/full/build): tools/threads/variant.sh PLAT=1
#      WATCHDOG=0 relinks its guest with tools/perf/plat_autostart.c, then tests/s15/mtqemu/boot_game.sh runs it on
#      the player's exe for PLAT_TIMEOUT seconds (default 300) or until PLAT_EXPECT.
# 3. tools/perf/plat.py: metric = distinct unimplemented entries (+ imports with no export) + failed opens +
#    decode failures + unmapped inputs.
# Exit 0 when the report is written; 1 when the report's own test failed.
set -uo pipefail
COMMIT=${1:?commit}; REPORT=${2:?report.md}
HERE=$(cd "$(dirname "$0")" && pwd); P=$(cd "$HERE/../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
C12=${COMMIT:0:12}; W=$FARM_HOME/plat/$C12; rm -rf "$W"; mkdir -p "$W" "$(dirname "$REPORT")"
REPORT=$(cd "$(dirname "$REPORT")" && pwd)/$(basename "$REPORT")
log() { echo "$(date -u +%FT%TZ) plat_job: $*"; }
T0=$(date +%s)

TEST_NOTE="not run (PLAT_TESTS=0)"; TEST_RC=0
if [ "${PLAT_TESTS:-1}" != 0 ]; then
	python3 -I "$P/tests/s15/test_plat_report.py" > "$W/test_plat_report.log" 2>&1; TEST_RC=$?
	TEST_NOTE=$(tail -1 "$W/test_plat_report.log")
fi
log "test_plat_report: $TEST_NOTE"

LOGS=(); ELF=${PLAT_ELF:-}; GUEST_NOTE=""
if [ -n "${PLAT_LOGS:-}" ]; then
	read -r -a LOGS <<< "$PLAT_LOGS"; GUEST_NOTE="logs handed in: ${#LOGS[@]}"
else
	BUILD=${PLAT_BUILD:-$FARM_HOME/full/build}
	EXE=${PLAT_EXE:-$FARM_HOME/gamedata/FalloutNV.exe}
	WANT=${FARM_EXE_SHA256:-89e7020487e80f20ddc92ea641c4433b2c6839a82f45a076e414d167d11dd0fe}
	if [ ! -f "$BUILD/link/stage.json" ]; then GUEST_NOTE="not run: no pipeline build at $BUILD (the full job's)"
	elif [ ! -f "$EXE" ] || [ -L "$EXE" ] || [ "$(sha256sum "$EXE" | cut -c1-64)" != "$WANT" ]; then
		GUEST_NOTE="not run: no verified player exe"
	elif ! command -v qemu-aarch64 >/dev/null; then GUEST_NOTE="not run: no qemu-aarch64"
	elif ! PLAT=1 WATCHDOG=0 bash "$P/tools/threads/variant.sh" "$BUILD" "$W/guest" > "$W/variant.log" 2>&1; then
		GUEST_NOTE="not run: the lane guest does not link ($(tail -1 "$W/variant.log"))"
	else
		ELF=$W/guest/fnvnx.elf
		TIMEOUT=${PLAT_TIMEOUT:-300} EXPECT=${PLAT_EXPECT:-fnvnx plat: run the whole time} \
			bash "$P/tests/s15/mtqemu/boot_game.sh" "$ELF" "$EXE" "$W/boot" > "$W/boot_game.log" 2>&1
		GUEST_NOTE="guest run: $(grep -E '^boot_game: (REACHED|NOT reached)' "$W/boot_game.log" | tail -1 | cut -c1-160)"
		[ -f "$W/boot/boot.log" ] && LOGS=("$W/boot/boot.log")
	fi
fi
log "guest: $GUEST_NOTE"
RC=3
if [ ${#LOGS[@]} -gt 0 ]; then
	python3 -I "$P/tools/perf/plat.py" "${LOGS[@]}" ${ELF:+--elf "$ELF"} --out "$W/plat.md" > "$W/plat.log" 2>&1; RC=$?
fi
{
	echo "# plat $C12"; echo
	if [ "$TEST_RC" != 0 ]; then echo "- lane: FAIL (report tests)"
	elif [ "$RC" = 0 ]; then echo "- lane: measured"; else echo "- lane: no guest data"; fi
	echo "- report tests: $TEST_NOTE"; echo "- guest: $GUEST_NOTE"; echo "- $(( $(date +%s) - T0 )) s"
	[ -f "$W/plat.md" ] && tail -n +2 "$W/plat.md"
} > "$REPORT"
[ -f "$W/plat.json" ] && cp "$W/plat.json" "${REPORT%.md}.json"
log "report: $REPORT"
[ "$TEST_RC" = 0 ] || exit 1
exit 0
