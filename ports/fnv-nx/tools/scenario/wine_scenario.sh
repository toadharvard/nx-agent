#!/usr/bin/env bash
# wine_scenario.sh - the original side of an S18 scenario: the player's FalloutNV.exe under Wine (the S16 sweep's
# prefix: tools/compare/wine_ref.sh setup - symlink mirror, ref_ini.py inis, Xvfb, WineD3D on llvmpipe), driven by
# the scenario plan (scenario.py drive-wine: xdotool keys, wall-clock waits), optionally traced at function
# boundaries for the lockstep compare (S5-boot's tools/lockstep: fnvtrace.exe loads fnvtrace.dll before the game).
#
#   tools/scenario/wine_scenario.sh <game dir> <work dir> <plan.json> <out dir> [<trace list>]
#
# <out dir> gets: progress.json (steps, marks, gate timeouts), console.txt (the plan's scof log), profile/Saves/
# (the scenario's saves: s18_*), wine.log, and with a trace list wine.tsv. The prefix is reused between scenarios
# (setup is idempotent); the scenario's own saves are removed before each run, so every run starts from no save.
# Everything under <out dir> is game-derived: it goes to the private artifacts repository, never into nx-agent.
# Needs: wine (32-bit), xvfb, xdotool; i686-w64-mingw32-gcc for the tracer. Env: SCENARIO_DISPLAY (default :79),
# SCENARIO_WINE_TAIL_S (seconds after the plan, default 5), FNVTRACE_SNAP / FNVTRACE_DEREF / FNVTRACE_MAX.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); P=$(cd "$HERE/../.." && pwd)
GAME=${1:?game dir}; WORK=${2:?work dir}; PLAN=${3:?plan.json}; OUT=${4:?out dir}; LIST=${5:-}
mkdir -p "$WORK" "$OUT"
WORK=$(cd "$WORK" && pwd); OUT=$(cd "$OUT" && pwd); PLAN=$(cd "$(dirname "$PLAN")" && pwd)/$(basename "$PLAN")
log() { echo "$(date -u +%FT%TZ) wine_scenario: $*"; }
"$P/tools/compare/wine_ref.sh" setup "$GAME" "$WORK" > "$OUT/setup.log" 2>&1 || { log "wine_ref.sh setup failed (see $OUT/setup.log)"; exit 1; }
export WINEPREFIX=$WORK/pfx WINEARCH=win32 WINEDEBUG=${WINEDEBUG:--all}
export WINEDLLOVERRIDES="mscoree,mshtml=;winemenubuilder.exe=d"
export LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe
G=$WORK/pfx/drive_c/FNV
DOCS="$WORK/pfx/drive_c/users/$(id -un)/Documents/My Games/FalloutNV"
mkdir -p "$G/user" "$DOCS/Saves"
LOGREL=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["console_log"].replace("\\","/"))' "$PLAN")
rm -f "$G/$LOGREL" "$DOCS"/Saves/s18_*
EXE=(FalloutNV.exe)
if [ -n "$LIST" ]; then
	if [ -x "$P/tools/lockstep/build.sh" ] && "$P/tools/lockstep/build.sh" "$WORK/tracer" > "$OUT/tracer_build.log" 2>&1; then
		cp -f "$WORK/tracer/fnvtrace.exe" "$WORK/tracer/fnvtrace.dll" "$G/"
		LIST=$(cd "$(dirname "$LIST")" && pwd)/$(basename "$LIST")
		export FNVTRACE_LIST="Z:${LIST//\//\\}" FNVTRACE_OUT="Z:${OUT//\//\\}\\wine.tsv"
		EXE=(fnvtrace.exe FalloutNV.exe)
	else
		log "no tracer (tools/lockstep/build.sh, S5-boot): the run is not traced"
	fi
fi
DISP=${SCENARIO_DISPLAY:-:79}
pkill -f "Xvfb $DISP " 2> /dev/null; sleep 1
Xvfb "$DISP" -screen 0 1280x720x24 -nolisten tcp -noreset > /dev/null 2>&1 &
XPID=$!; export DISPLAY=$DISP; sleep 2
T0=$(date +%s)
(cd "$G" && exec wine "${EXE[@]}" > "$OUT/wine.log" 2>&1) &
WPID=$!
python3 -I "$HERE/scenario.py" drive-wine "$PLAN" "$DOCS" "$OUT/progress.json" > "$OUT/drive.log" 2>&1
sleep "${SCENARIO_WINE_TAIL_S:-5}"
ALIVE=1; kill -0 "$WPID" 2> /dev/null || ALIVE=0
wineserver -k 2> /dev/null; sleep 2; kill "$XPID" 2> /dev/null
cp -f "$G/$LOGREL" "$OUT/console.txt" 2> /dev/null
mkdir -p "$OUT/profile/Saves"; cp -f "$DOCS"/Saves/s18_* "$OUT/profile/Saves/" 2> /dev/null
python3 -I - "$OUT/progress.json" "$ALIVE" "$(( $(date +%s) - T0 ))" <<'EOF'
import json, sys
p, alive, secs = sys.argv[1], sys.argv[2] == "1", int(sys.argv[3])
try:
    r = json.load(open(p))
except (OSError, ValueError):
    r = {"schema": "fnvnx-scenario-wine/1", "steps_done": 0, "complete": False}
r["game_alive_at_end"], r["wall_s"] = alive, secs
json.dump(r, open(p, "w"), indent=1)
EOF
log "$(cat "$OUT/drive.log" | tail -1); game alive at the end: $ALIVE; saves: $(ls "$OUT/profile/Saves" | wc -l); console: $([ -f "$OUT/console.txt" ] && wc -l < "$OUT/console.txt" || echo none) lines"
