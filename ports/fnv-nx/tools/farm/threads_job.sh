#!/usr/bin/env bash
# threads_job.sh - report lane for D25 #7 (races, deadlocks, hangs): the checkers' own tests, then a run of the real
# guest with them on, as one report. Used by T1's quick_job.sh (check `threads`) and by S18's scenario job (pass
# its run logs). tools/threads/README.md.
#
#   tools/farm/threads_job.sh <commit> <report.md>
#
# 1. tests/s15/test_mtqemu.py: the race checker (hb.c) and the wait-graph watchdog (platform/win32/waitgraph.cpp)
#    on their mtqemu cases (seconds; skips without the cross tools / qemu-user).
# 2. the guest, one of:
#    THREADS_LOGS="<log>..." (+ THREADS_ELF): logs of a run that already had the checkers on (S18's scenario job:
#      its guest from tools/threads/variant.sh); only reported.
#    else the full job's build (THREADS_BUILD, default $FARM_HOME/full/build): tools/threads/variant.sh relinks its
#      guest with the watchdog (RACE=1 too when THREADS_RACE=1 and the build's objects are RACE=1 ones), then
#      tests/s15/mtqemu/boot_game.sh runs it on the player's exe under qemu-aarch64 for THREADS_TIMEOUT seconds
#      (default 300) or until THREADS_EXPECT appears (default: none, the whole time: the checkers flush every 10 s).
# 3. tools/threads/report.py: races per function pair, deadlock cycles, long waits, by address only.
# Env: THREADS_LOGS, THREADS_ELF, THREADS_BUILD, THREADS_EXE (default $FARM_HOME/gamedata/FalloutNV.exe, the full
# job's verified copy), THREADS_RACE, THREADS_LIMIT_MS (default 10000), THREADS_TIMEOUT, THREADS_EXPECT, THREADS_TESTS=0.
# Exit 0 when the report is written (verdict FAIL for a race or a deadlock cycle; long waits are listed, they can be
# a slow load; the lane's metric is races + cycles + long waits, D25 #7's zero target); 1 when a checker test failed
# (the checkers are broken: the guest numbers mean nothing).
set -uo pipefail
COMMIT=${1:?commit}; REPORT=${2:?report.md}
HERE=$(cd "$(dirname "$0")" && pwd); P=$(cd "$HERE/../.." && pwd)
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}
C12=${COMMIT:0:12}; W=$FARM_HOME/threads/$C12; rm -rf "$W"; mkdir -p "$W" "$(dirname "$REPORT")"
REPORT=$(cd "$(dirname "$REPORT")" && pwd)/$(basename "$REPORT")
log() { echo "$(date -u +%FT%TZ) threads_job: $*"; }
T0=$(date +%s)

# ---- 1. the checkers' own tests
TEST_NOTE="not run (THREADS_TESTS=0)"; TEST_RC=0
if [ "${THREADS_TESTS:-1}" != 0 ]; then
	python3 -I "$P/tests/s15/test_mtqemu.py" > "$W/test_mtqemu.log" 2>&1; TEST_RC=$?
	TEST_NOTE=$(tail -1 "$W/test_mtqemu.log")
	grep -q '^skip:' "$W/test_mtqemu.log" && TEST_NOTE="skipped: $(grep -m1 '^skip:' "$W/test_mtqemu.log")"
	log "test_mtqemu: $TEST_NOTE (rc $TEST_RC)"
fi

# ---- 2. the guest
GUEST_NOTE=""; LOGS=(); ELF=${THREADS_ELF:-}
if [ -n "${THREADS_LOGS:-}" ]; then
	read -r -a LOGS <<< "$THREADS_LOGS"
	GUEST_NOTE="logs handed in: ${#LOGS[@]}"
else
	BUILD=${THREADS_BUILD:-$FARM_HOME/full/build}
	EXE=${THREADS_EXE:-$FARM_HOME/gamedata/FalloutNV.exe}
	WANT=${FARM_EXE_SHA256:-89e7020487e80f20ddc92ea641c4433b2c6839a82f45a076e414d167d11dd0fe}
	if [ ! -f "$BUILD/link/stage.json" ]; then
		GUEST_NOTE="not run: no pipeline build at $BUILD (the full job's)"
	elif [ ! -f "$EXE" ] || [ -L "$EXE" ] || [ "$(sha256sum "$EXE" | cut -c1-64)" != "$WANT" ]; then
		GUEST_NOTE="not run: no verified player exe (THREADS_EXE / the full job's gamedata)"
	elif ! command -v qemu-aarch64 >/dev/null; then
		GUEST_NOTE="not run: no qemu-aarch64"
	elif ! RACE=${THREADS_RACE:-0} WG_LIMIT_MS=${THREADS_LIMIT_MS:-10000} \
			bash "$P/tools/threads/variant.sh" "$BUILD" "$W/guest" > "$W/variant.log" 2>&1; then
		GUEST_NOTE="not run: the lane guest does not link ($(tail -1 "$W/variant.log"))"
	else
		ELF=$W/guest/fnvnx.elf
		TIMEOUT=${THREADS_TIMEOUT:-300} EXPECT=${THREADS_EXPECT:-fnvnx threads: run the whole time} \
			bash "$P/tests/s15/mtqemu/boot_game.sh" "$ELF" "$EXE" "$W/boot" > "$W/boot_game.log" 2>&1
		GUEST_NOTE="guest run: $(grep -E '^boot_game: (REACHED|NOT reached)' "$W/boot_game.log" | tail -1 | cut -c1-160)"
		[ -f "$W/boot/boot.log" ] && LOGS=("$W/boot/boot.log")
	fi
fi
log "guest: $GUEST_NOTE"

# ---- 3. report
RJ_RC=3
if [ ${#LOGS[@]} -gt 0 ]; then
	python3 -I "$P/tools/threads/report.py" "${LOGS[@]}" ${ELF:+--elf "$ELF"} --out "$W/threads.md" > "$W/report.log" 2>&1
	RJ_RC=$?
fi
python3 -I - "$REPORT" "$W" "$COMMIT" "$TEST_RC" "$TEST_NOTE" "$GUEST_NOTE" "$RJ_RC" "$(( $(date +%s) - T0 ))" <<'EOF'
import json, os, sys
out, w, commit, trc, tnote, gnote, rrc, secs = sys.argv[1:9]
j = {}
p = os.path.join(w, 'threads.json')
if rrc in ('0', '3') and os.path.isfile(p):
    j = json.load(open(p))
md = os.path.join(w, 'threads.md')
body = open(md).read().split('\n', 1)[1] if os.path.isfile(md) else ''
metric = (j.get('races', 0) + j.get('cycles', 0) + j.get('long_waits', 0)) if j else None
bad = trc != '0' or (j and (j.get('cycles', 0) or j.get('races', 0)))
verdict = 'FAIL (checker tests)' if trc != '0' else ('no guest data' if not j else ('FAIL' if bad else 'PASS'))
o = open(out, 'w')
o.write('# threads %s\n\n' % commit[:12])
o.write('- verdict: %s; metric (races + cycles + long waits) %s\n' % (verdict, metric if metric is not None else 'n/a'))
o.write('- checker tests: %s\n- guest: %s\n- %d s\n' % (tnote, gnote, int(secs)))
if body:
    o.write(body)
o.close()
json.dump({'schema': 'fnvnx-threads-job/1', 'commit': commit, 'verdict': verdict, 'metric': metric,
           'tests_rc': int(trc), 'guest': gnote, 'report': j or None},
          open(out[:-3] + '.json' if out.endswith('.md') else out + '.json', 'w'), indent=1)
EOF
log "report: $REPORT"
[ "$TEST_RC" = 0 ] || exit 1
exit 0
