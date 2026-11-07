#!/usr/bin/env bash
# wine_trace.sh - the reference side of the lockstep trace: the player's FalloutNV.exe under Wine (the S16 sweep's
# prefix, tools/compare/wine_ref.sh setup: symlink mirror, ini files, Xvfb, WineD3D on llvmpipe) started through
# fnvtrace.exe, which loads fnvtrace.dll before the game's first instruction (tools/lockstep/fnvtrace.c).
#
#   tools/lockstep/wine_trace.sh <game dir> <work dir> <function list> <out.tsv> [seconds]
#
# <function list>: tests/s5/boot_trace.py fnlist output. The game runs for [seconds] (default 120; WINE_TRACE_S)
# with no input, then the prefix is killed. FNVTRACE_SNAP / FNVTRACE_DEREF / FNVTRACE_MAX pass through. Nothing of
# the game is copied: the two tracer files go beside the mirror's symlinks. Needs wine (32-bit), xvfb,
# i686-w64-mingw32-gcc (tools/lockstep/build.sh).
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
P=$(cd "$HERE/../.." && pwd)
GAME=${1:?game dir}; WORK=${2:?work dir}; LIST=${3:?function list}; OUT=${4:?out.tsv}; SECS=${5:-${WINE_TRACE_S:-120}}
mkdir -p "$WORK" "$(dirname "$OUT")"
WORK=$(cd "$WORK" && pwd); LIST=$(cd "$(dirname "$LIST")" && pwd)/$(basename "$LIST")
OUT=$(cd "$(dirname "$OUT")" && pwd)/$(basename "$OUT")
log() { echo "$(date -u +%FT%TZ) wine_trace: $*"; }
"$P/tools/compare/wine_ref.sh" setup "$GAME" "$WORK" > "$WORK/setup.log" 2>&1 || { log "wine_ref.sh setup failed (see $WORK/setup.log)"; exit 1; }
"$HERE/build.sh" "$WORK/tracer" > "$WORK/build.log" 2>&1 || { log "tracer build failed (see $WORK/build.log)"; exit 1; }
G=$WORK/pfx/drive_c/FNV
cp -f "$WORK/tracer/fnvtrace.exe" "$WORK/tracer/fnvtrace.dll" "$G/"
export WINEPREFIX=$WORK/pfx WINEARCH=win32 WINEDEBUG=${WINEDEBUG:--all}
export WINEDLLOVERRIDES="mscoree,mshtml=;winemenubuilder.exe=d"
export LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe
export FNVTRACE_LIST="Z:${LIST//\//\\}" FNVTRACE_OUT="Z:${OUT//\//\\}"
DISP=${WINE_TRACE_DISPLAY:-:78}
Xvfb "$DISP" -screen 0 1280x720x24 -nolisten tcp -noreset > /dev/null 2>&1 &
XPID=$!; export DISPLAY=$DISP; sleep 2
log "tracing $(grep -vc '^#' "$LIST") functions for ${SECS}s -> $OUT"
T0=$(date +%s)
(cd "$G" && exec wine fnvtrace.exe FalloutNV.exe > "$WORK/wine.log" 2>&1) &
WPID=$!
while kill -0 "$WPID" 2> /dev/null && [ $(( $(date +%s) - T0 )) -lt "$SECS" ]; do sleep 2; done
RC=running
if kill -0 "$WPID" 2> /dev/null; then wineserver -k 2> /dev/null; else wait "$WPID"; RC=$?; fi
sleep 2; kill "$XPID" 2> /dev/null
log "game ended: $RC after $(( $(date +%s) - T0 ))s; $(grep -c '^call' "$OUT" 2> /dev/null || echo 0) calls logged"
grep -m3 'fnvtrace' "$WORK/wine.log" || true
