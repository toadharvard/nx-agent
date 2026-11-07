#!/usr/bin/env bash
# mem_job.sh - the memory lane (D25 #8: allocation failures, memory over the Switch budget). T1's quick_job.sh check
# `mem` and a farm job. tools/perf/MEM.md.
#
#   tools/farm/mem_job.sh <commit> <report.md>
#
# 1. tests/s15/test_mem_report.py (mem.py on synthetic logs). The recorder itself is tested by tests/s4's
#    mem_lane_heap_virtual_gpu (heap, virtual, a fake d3d9 backend).
# 2. the guest, one of:
#    MEM_LOGS="<log>..." (+ MEM_ELF): logs of a run with the recorder on (S18's scenarios); only reported;
#    else the full job's build (MEM_BUILD, default $FARM_HOME/full/build): tools/threads/variant.sh MEM=1 WATCHDOG=0
#      relinks its guest with the recorder (mem_autostart.c, mem_wrap.c and the C heap --wraps), then
#      tests/s15/mtqemu/boot_game.sh runs it on the player's exe for MEM_TIMEOUT seconds (default 300) or until
#      MEM_EXPECT; HEAP_MIB (default 1024 here: the guest heap must not be the first limit hit).
# 3. tools/perf/mem.py --budget-gib MEM_BUDGET_GIB (default 3.2).
# Exit 0 when the report is written; 1 when the report's own test failed.
set -uo pipefail
COMMIT=${1:?commit}; REPORT=${2:?report.md}
HERE=$(cd "$(dirname "$0")" && pwd); P=$(cd "$HERE/../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
C12=${COMMIT:0:12}; W=$FARM_HOME/mem/$C12; rm -rf "$W"; mkdir -p "$W" "$(dirname "$REPORT")"
REPORT=$(cd "$(dirname "$REPORT")" && pwd)/$(basename "$REPORT")
log() { echo "$(date -u +%FT%TZ) mem_job: $*"; }
T0=$(date +%s)

TEST_NOTE="not run (MEM_TESTS=0)"; TEST_RC=0
if [ "${MEM_TESTS:-1}" != 0 ]; then
	python3 -I "$P/tests/s15/test_mem_report.py" > "$W/test_mem_report.log" 2>&1; TEST_RC=$?
	TEST_NOTE=$(tail -1 "$W/test_mem_report.log")
fi
log "test_mem_report: $TEST_NOTE"

LOGS=(); ELF=${MEM_ELF:-}; GUEST_NOTE=""
if [ -n "${MEM_LOGS:-}" ]; then
	read -r -a LOGS <<< "$MEM_LOGS"; GUEST_NOTE="logs handed in: ${#LOGS[@]}"
else
	BUILD=${MEM_BUILD:-$FARM_HOME/full/build}
	EXE=${MEM_EXE:-$FARM_HOME/gamedata/FalloutNV.exe}
	WANT=${FARM_EXE_SHA256:-89e7020487e80f20ddc92ea641c4433b2c6839a82f45a076e414d167d11dd0fe}
	if [ ! -f "$BUILD/link/stage.json" ]; then GUEST_NOTE="not run: no pipeline build at $BUILD (the full job's)"
	elif [ ! -f "$EXE" ] || [ -L "$EXE" ] || [ "$(sha256sum "$EXE" | cut -c1-64)" != "$WANT" ]; then
		GUEST_NOTE="not run: no verified player exe"
	elif ! command -v qemu-aarch64 >/dev/null; then GUEST_NOTE="not run: no qemu-aarch64"
	elif ! MEM=1 WATCHDOG=0 bash "$P/tools/threads/variant.sh" "$BUILD" "$W/guest" > "$W/variant.log" 2>&1; then
		GUEST_NOTE="not run: the lane guest does not link ($(tail -1 "$W/variant.log"))"
	else
		ELF=$W/guest/fnvnx.elf
		HEAP_MIB=${HEAP_MIB:-1024} TIMEOUT=${MEM_TIMEOUT:-300} EXPECT=${MEM_EXPECT:-fnvnx mem: run the whole time} \
			bash "$P/tests/s15/mtqemu/boot_game.sh" "$ELF" "$EXE" "$W/boot" > "$W/boot_game.log" 2>&1
		GUEST_NOTE="guest run: $(grep -E '^boot_game: (REACHED|NOT reached)' "$W/boot_game.log" | tail -1 | cut -c1-160)"
		[ -f "$W/boot/boot.log" ] && LOGS=("$W/boot/boot.log")
	fi
fi
log "guest: $GUEST_NOTE"
RC=3
if [ ${#LOGS[@]} -gt 0 ]; then
	python3 -I "$P/tools/perf/mem.py" "${LOGS[@]}" ${ELF:+--elf "$ELF"} --budget-gib "${MEM_BUDGET_GIB:-3.2}" \
		--out "$W/mem.md" > "$W/mem.log" 2>&1; RC=$?
fi
{
	echo "# mem $C12"; echo
	if [ "$TEST_RC" != 0 ]; then echo "- lane: FAIL (report tests)"
	elif [ "$RC" = 0 ]; then echo "- lane: measured"; else echo "- lane: no guest data"; fi
	echo "- report tests: $TEST_NOTE"; echo "- guest: $GUEST_NOTE"; echo "- $(( $(date +%s) - T0 )) s"
	[ -f "$W/mem.md" ] && tail -n +2 "$W/mem.md"
} > "$REPORT"
[ -f "$W/mem.json" ] && cp "$W/mem.json" "${REPORT%.md}.json"
log "report: $REPORT"
[ "$TEST_RC" = 0 ] || exit 1
exit 0
