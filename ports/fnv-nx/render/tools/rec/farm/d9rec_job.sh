#!/usr/bin/env bash
# d9rec_job.sh - farm job (M1a): record the ORIGINAL game's D3D9 call stream under Wine with our recorder (render/tools/rec) in one
# reference-environment profile, for two scenes (menu, first sweep cell), twice each; statistics, determinism check, publish.
#
#   render/tools/rec/farm/d9rec_job.sh <commit> <profile: handheld|docked> <report.md>       runner job `d9rec <profile>`
#
# Profile (QUEUE.md "ENV profile"): handheld = 1280x720@60, docked = 1920x1080@60, ONE adapter mode (Xvfb with -extension RANDR off: Wine then
# exposes only the current mode, at 60 Hz; with RandR on it reports 0 Hz and 16-bit formats too); Wine's video memory = D9REC_VIDEO_MEM_MB
# (HKCU\Software\Wine\Direct3D\VideoMemorySize), the game INI at the profile size (wine_ref.sh ref_ini, windowed at screen size).
# Reuses tools/compare/wine_ref.sh (setup, run, versions), tools/gamedata_fetch.sh, tools/compare/cells.py + scenes.json, d9rec_stats.py, d9cmp.py.
# Output: <report.md> (counts); private artifacts branch l6-render/d9rec/<commit12>/<profile>: raw streams (xz, parts of 95 MB), env answers TSV,
# per-frame TSVs, stats JSON. Game-derived: private repository only, never nx-agent.
# Env: ARTIFACTS (clone of fnv-nx-build with push), FARM_HOME, D9REC_VIDEO_MEM_MB, D9REC_HOLD_SHOTS (shots per scene, default 6) with
# D9REC_HOLD_GAP_S (default 5), D9REC_TIMEOUT_S (per run, default 1200), D9REC_CMP_TIMEOUT_S (default 3000), D9REC_CELL (cell editor id instead of
# the first of cells.py list), D9REC_GAME, D9REC_WORK, D9REC_DISPLAY (:77), D9REC_MIN_FREE_GB (20), D9REC_NO_PUSH=1.
# D9REC_DRY=1: no game; nxd9play on synthetic captures D9REC_DRY_CAPS="<menu.nxd9> <cell.nxd9>" is the "game"; everything else as real, no push.
set -uo pipefail
COMMIT=${1:?commit}; PROFILE=${2:?profile handheld|docked}; REPORT=${3:?report.md}
D9REC_VIDEO_MEM_MB=${D9REC_VIDEO_MEM_MB:-960}        # the ONE place of the number (QUEUE.md: the Switch GPU budget, shared with L5's D5)
case "$PROFILE" in handheld) SW=1280; SH=720 ;; docked) SW=1920; SH=1080 ;; *) echo "profile: handheld|docked" >&2; exit 2 ;; esac
P=$(cd "$(dirname "$0")/../../../.." && pwd)        # ports/fnv-nx
CMP=$P/tools/compare; REC=$P/render/tools/rec; D9=$P/render/d3d9
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}; H12=${COMMIT:0:12}; DRY=${D9REC_DRY:-0}
WK=${D9REC_WORK:-$FARM_HOME/d9rec/$H12/$PROFILE}; PFXW=$WK/wine; DISP=${D9REC_DISPLAY:-:77}; PDISP=${D9REC_PROBE_DISPLAY:-:79}
BRANCH=l6-render/d9rec/$H12/$PROFILE; SHOTS=${D9REC_HOLD_SHOTS:-6}; GAP=${D9REC_HOLD_GAP_S:-5}
mkdir -p "$WK/pub" "$(dirname "$REPORT")"; WK=$(cd "$WK" && pwd)
NOTES=(); XPIDS=(); T0=$(date +%s); SEC=$WK/sections; rm -rf "$SEC"; mkdir -p "$SEC"
log() { echo "$(date -u +%FT%TZ) d9rec_job[$PROFILE]: $*"; }
wpath() { local p=${1//\//\\}; echo "Z:$p"; }
fail() { NOTES+=("FAILED: $*"); log "FAILED: $*"; write_report; exit 1; }
wenv() {
	export WINEPREFIX=$PFXW/pfx WINEARCH=win32 WINEDEBUG=-all LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe LP_NUM_THREADS=${LP_NUM_THREADS:-$(nproc)}
	export WINEDLLOVERRIDES="mscoree,mshtml=;winemenubuilder.exe=d;d3d9=n,b"
}
# shellcheck disable=SC2329  # (the EXIT trap)
cleanup() {
	local p; for p in "${XPIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null; done
	( wenv; wineserver -k 2>/dev/null ) || true
	rm -f "$GDIR/d3d9.dll" 2>/dev/null; [ "$DRY" = 1 ] && rm -rf "$WK/drygame"; return 0
}
GDIR=$PFXW/pfx/drive_c/FNV; trap cleanup EXIT

# ---- setup -----------------------------------------------------------------------------------------------------------------------------
free_gb() { df -Pk "$1" 2>/dev/null | awk 'NR == 2 { printf "%d", $4 / 1048576 }'; }
for d in "$FARM_HOME"/d9rec/*/; do d=${d%/}; [ -d "$d" ] && [ "$(basename "$d")" != "$H12" ] && rm -rf "$d"; done   # earlier heads
[ "$(free_gb "$FARM_HOME")" -ge "${D9REC_MIN_FREE_GB:-20}" ] 2>/dev/null || [ "$DRY" = 1 ] || fail "disk: less than ${D9REC_MIN_FREE_GB:-20} GB free on $FARM_HOME"
need=""; for c in wine wineserver Xvfb xz python3 i686-w64-mingw32-g++ git; do command -v "$c" >/dev/null || need="$need $c"; done
[ "$DRY" = 1 ] || for c in xdotool xwd convert; do command -v "$c" >/dev/null || need="$need $c"; done
if [ -n "$need" ]; then   # (sweep_job.sh's apt block is not callable: L4 note in the README)
	[ "$(id -u)" = 0 ] && [ "${FARM_APT:-1}" = 1 ] || fail "missing:$need"
	dpkg --add-architecture i386; apt-get update -qq
	DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wine32:i386 wine g++-mingw-w64-i686 xvfb xdotool x11-apps imagemagick xz-utils \
		libgl1-mesa-dri:i386 libgl1-mesa-dri >/dev/null || fail "apt-get"
fi
HEADC=$(git -C "$P" rev-parse HEAD 2>/dev/null)   # $COMMIT is a full id or any prefix of one: compare as a prefix, never short against full
case "$HEADC" in "$COMMIT"?*|"$COMMIT") ;; *) NOTES+=("checkout is not $H12 (HEAD ${HEADC:0:12}): the recorder is built from the checkout") ;; esac
# Xvfb with RandR off, found first on PATH: wine_ref.sh's xstart calls plain `Xvfb` (its lines 41-42 have no argument hook; README proposes S16_XVFB_ARGS)
REALX=$(command -v Xvfb); mkdir -p "$WK/shim"; printf '#!/bin/sh\nexec %s "$@" -extension RANDR\n' "$REALX" >"$WK/shim/Xvfb"; chmod +x "$WK/shim/Xvfb"
export PATH=$WK/shim:$PATH
wenv; mkdir -p "$PFXW"
if [ "$DRY" = 1 ]; then
	if [ ! -f "$PFXW/pfx/system.reg" ]; then
		Xvfb "$PDISP" -screen 0 "${SW}x${SH}x24" -nolisten tcp -noreset >/dev/null 2>&1 & XPIDS+=($!); sleep 2
		DISPLAY=$PDISP wineboot -i >"$WK/wineboot.log" 2>&1; wineserver -w; kill "${XPIDS[-1]}" 2>/dev/null; sleep 1
	fi
	mkdir -p "$GDIR" "$WK/drygame"; GAME=$WK/drygame
else
	[ -n "${ARTIFACTS:-}" ] || fail "ARTIFACTS unset"
	GAME=${D9REC_GAME:-${SWEEP_GAME:-$FARM_HOME/game}}
	ARTIFACTS=$ARTIFACTS bash "$P/tools/gamedata_fetch.sh" "$GAME" >"$WK/gamedata.log" 2>&1 || fail "gamedata_fetch.sh (log $WK/gamedata.log)"
	for d in GalaxyWrp.dll binkw32.dll libvorbisfile.dll; do [ -e "$GAME/$d" ] || fail "no-start: the install lacks $d"; done
	S16_W=$SW S16_H=$SH S16_PCI_DEVICE=00000193 bash "$CMP/wine_ref.sh" setup "$GAME" "$PFXW" >"$WK/wine_setup.log" 2>&1 || fail "wine_ref.sh setup (log $WK/wine_setup.log)"
fi
# wine_ref.sh setup writes VideoMemorySize 1024 (its s16.reg); the profile's value goes in after it
wine reg add 'HKCU\Software\Wine\Direct3D' /v VideoMemorySize /t REG_SZ /d "$D9REC_VIDEO_MEM_MB" /f >"$WK/reg.log" 2>&1; wineserver -w
VMEM=$(wine reg query 'HKCU\Software\Wine\Direct3D' /v VideoMemorySize 2>/dev/null | tr -d '\r' | awk '/VideoMemorySize/ { print $NF }')
[ "$VMEM" = "$D9REC_VIDEO_MEM_MB" ] || fail "registry VideoMemorySize is '$VMEM', wanted $D9REC_VIDEO_MEM_MB (log $WK/reg.log)"
# recorder: keyed build of render/tools/rec + gen at this checkout
K=$(cat "$REC"/build.sh "$REC"/*.cpp "$REC"/*.h "$REC"/*.py "$D9"/gen/*.py "$D9"/gen/*.cpp "$D9"/gen/*.h "$D9/spec/d3d9_spec.json" 2>/dev/null | sha256sum | cut -c1-12)
DLL=$WK/rec-$K/d3d9.dll
[ -f "$DLL" ] || bash "$REC/build.sh" "$WK/rec-$K" >"$WK/rec_build.log" 2>&1 || fail "render/tools/rec/build.sh (log $WK/rec_build.log)"
i686-w64-mingw32-gcc -O1 -o "$WK/envprobe.exe" "$P/tests/l6/rec/envprobe.c" -ld3d9 -luser32 -static-libgcc >"$WK/envprobe_build.log" 2>&1 || fail "envprobe build"

xvfb() { Xvfb "$1" -screen 0 "${SW}x${SH}x24" -nolisten tcp -noreset >/dev/null 2>&1 & XPIDS+=($!); sleep 2; }
# ---- the profile check: envprobe under the recorder on the profile's screen; EnumAdapterModes must return exactly one mode WxH@60 --------------
profile_check() {
	mkdir -p "$WK/probe"; cp -f "$DLL" "$WK/envprobe.exe" "$WK/probe/"; rm -f "$WK/probe.d9s"
	xvfb "$PDISP"; local xp=${XPIDS[-1]}
	( cd "$WK/probe" && DISPLAY=$PDISP D9REC_OUT=$(wpath "$WK/probe.d9s") timeout 300 wine envprobe.exe >"$WK/probe.log" 2>&1 ); local rc=$?
	kill "$xp" 2>/dev/null; wineserver -w
	python3 -I "$REC/farm/d9rec_stats.py" "$WK/probe.d9s" --expect-mode "${SW}x${SH}@60" --tsv-dir "$WK/probe_tsv" --md "$SEC/probe_stats.md" >/dev/null 2>"$WK/probe_check.txt"; local sc=$?
	python3 -I "$P/tests/l6/rec/dump_env.py" "$WK/probe.d9s" "$WK/env_probe.tsv" >/dev/null 2>&1
	{ echo "## Environment profile"; echo
	  echo "- profile $PROFILE: screen ${SW}x${SH}@60, one adapter mode; Xvfb RandR off; Wine VideoMemorySize = $VMEM MiB (D9REC_VIDEO_MEM_MB)"
	  echo "- game INI size ${SW}x${SH} (wine_ref.sh ref_ini, windowed at screen size), PCI device 00000193$([ "$DRY" = 1 ] && echo '; DRY RUN: no game')"
	  echo "- $(wine --version 2>/dev/null); $(sed -n '2,5p' < <(bash "$CMP/wine_ref.sh" versions 2>/dev/null) | tr '\n' ';')"
	  echo "- envprobe exit $rc; $(cat "$WK/probe_check.txt")"
	  echo "- envprobe stream: $(grep -m1 '^- records' "$SEC/probe_stats.md"); env answers in env_probe.tsv: $(($(wc -l <"$WK/env_probe.tsv") - 1)) rows"
	  grep -m2 -A0 -E '^- (mode counts|enumerated)' "$SEC/probe_stats.md"; echo; } >"$SEC/0_profile.md"
	[ "$rc" = 0 ] && [ "$sc" = 0 ] || NOTES+=("FAILED: profile check: envprobe exit $rc, single-mode check exit $sc")
}

# ---- scenes ----------------------------------------------------------------------------------------------------------------------------
CELLID=""; CELLKIND=""
if [ "$DRY" != 1 ]; then
	if [ -n "${D9REC_CELL:-}" ]; then CELLID=$D9REC_CELL; CELLKIND=interior
	else   # the whole list goes to a file first: `| head -1` closed the pipe early and cells.py died with BrokenPipeError
		python3 -I "$CMP/cells.py" list "$GAME/Data/FalloutNV.esm" >"$WK/cells.tsv" 2>"$WK/cells.err" || fail "cells.py list (log $WK/cells.err)"
		IFS=$'\t' read -r _ CELLKIND CELLID _ <"$WK/cells.tsv"
	fi
	[ -n "$CELLID" ] || fail "no first cell (cells.py list)"
	python3 -I - "$CMP/scenes.json" "$WK/scenes.json" "$CELLID" "$CELLKIND" "$SHOTS" <<'EOF' || fail "scenes.json"
import json, sys, os
sys.path.insert(0, os.path.dirname(sys.argv[1]))
import cells
base = json.load(open(sys.argv[1])); cid, kind, shots = sys.argv[3], sys.argv[4], int(sys.argv[5])
menu = next(s for s in base['scenes'] if s['id'] == 'menu'); cell = cells.scene(cid, kind); cell['id'] = 'cell'
for s in (menu, cell): s['frames'] = shots
base['scenes'] = [menu, cell]; base['common_post'] = []
json.dump(base, open(sys.argv[2], 'w'))
EOF
fi

run_game() {   # run_game <scene> <out.d9s> <dir>: one recorded run; sets RC (124/137 = timeout, i.e. a hang)
	local sc=$1 out=$2 dir=$3 cap
	rm -f "$out"; mkdir -p "$dir"; wenv
	if [ "$DRY" = 1 ]; then
		read -r -a caps <<<"${D9REC_DRY_CAPS:?D9REC_DRY_CAPS}"; cap=${caps[0]}; [ "$sc" = cell ] && cap=${caps[1]:-${caps[0]}}
		[ -f "$WK/nxd9play.exe" ] || { bash "$CMP/d3d9/build.sh" "$WK/nxd9play-build" >"$WK/nxd9play.log" 2>&1 && cp -f "$WK/nxd9play-build/nxd9play.exe" "$WK/nxd9play.exe"; } || fail "nxd9play build"
		cp -f "$DLL" "$GAME/d3d9.dll"; cp -f "$WK/nxd9play.exe" "$GAME/"; xvfb "$DISP"; local xp=${XPIDS[-1]}
		( cd "$GAME" && DISPLAY=$DISP D9REC_OUT=$(wpath "$out") timeout -k 30 "${D9REC_TIMEOUT_S:-1200}" wine nxd9play.exe "$(wpath "$cap")" -o "$(wpath "$dir")" >"$dir/wine.log" 2>&1 ); RC=$?
		kill "$xp" 2>/dev/null; rm -f "$GAME/d3d9.dll"; wineserver -k 2>/dev/null; return
	fi
	# the job watches the game while it runs: every 5 s the stream size and the number of game processes (watch.tsv), so the report can tell a crash
	# (process gone, stream stopped) from a hang (process alive, stream stopped) from a finished run
	local wpat=FalloutNV.exe wpid; watch "$out" "$wpat" >"$dir/watch.tsv" & wpid=$!
	# wine_ref.sh run: S16_D9CAP is only the name of the proxy DLL it copies beside the exe and enables via WINEDLLOVERRIDES (d3d9=n,b)
	D9REC_OUT=$(wpath "$out") S16_W=$SW S16_H=$SH S16_PCI_DEVICE=00000193 S16_SCENES=$WK/scenes.json S16_D9CAP=$DLL S16_STOP_AFTER_FRAMES=1 \
	S16_FRAME_GAP_S=$GAP S16_DISPLAY=$DISP S16_SETTLE_S=${S16_SETTLE_S:-30} WINEDEBUG=${D9REC_WINEDEBUG:-err+all} \
		timeout -k 30 "${D9REC_TIMEOUT_S:-1200}" bash "$CMP/wine_ref.sh" run "$PFXW" "$sc" "$dir" >"$dir/run.log" 2>&1
	RC=$?; kill "$wpid" 2>/dev/null; wait "$wpid" 2>/dev/null; wineserver -k 2>/dev/null; rm -f "$GDIR/d3d9.dll"
}
watch() {   # watch <stream> <process name pattern>: "seconds<TAB>stream bytes<TAB>matching processes" every 5 s until killed
	local t=$SECONDS; while :; do printf '%s\t%s\t%s\n' $((SECONDS - t)) "$(stat -c %s "$1" 2>/dev/null || echo 0)" "$(pgrep -fc "$2" || true)"; sleep 5; done
}
watch_line() {   # watch_line <watch.tsv>: one report line
	awk -F'\t' '$2 != ps { ls = $1; ps = $2 } $3 > 0 { lp = $1 } { n = $1 } END { if (!NR) print "no watch samples"; else printf "watched %s s: stream last grew at %s s (%s bytes), game process last seen at %s s%s", n, ls, ps, lp + 0, (lp + 0 < n - 10 ? " (GONE before the end: crash or exit)" : "") }' "$1" 2>/dev/null
}

scene() {   # scene <menu|cell>: two recorded runs, stats, env answers, d9cmp
	local sc=$1 s=$WK/$1 n; local -a st sec
	rm -rf "$s"; mkdir -p "$s"; log "scene $sc"
	for n in 1 2; do
		local t1=$SECONDS; run_game "$sc" "$s/run$n.d9s" "$s/out$n"; st[n]=$RC; sec[n]=$((SECONDS - t1))
		log "scene $sc run $n: exit $RC, $(stat -c %s "$s/run$n.d9s" 2>/dev/null || echo 0) bytes, ${sec[n]} s"
	done
	local f="$SEC/$sc.md"
	{ echo "## Scene $sc"; echo
	  for n in 1 2; do
		if [ "${st[n]}" = 124 ] || [ "${st[n]}" = 137 ]; then echo "- run $n: TIMEOUT after ${D9REC_TIMEOUT_S:-1200} s (hang; stream as far as written)"
		elif [ "${st[n]}" != 0 ]; then echo "- run $n: exit ${st[n]} (crash or script failure; last wine.log lines: $(tail -n 3 "$s/out$n/wine.log" "$s/out$n/run.log" 2>/dev/null | tr '\n' '|' | cut -c1-300))"
		else echo "- run $n: exit 0 (a script that kept typing at a dead game also exits 0: the world verdict below decides)"; fi
		echo "- run $n: ${sec[n]} s; $([ -s "$s/out$n/watch.tsv" ] && watch_line "$s/out$n/watch.tsv" || echo 'no watch data')"
		local wl; wl=$(ls "$s/out$n/$sc/wine.log" "$s/out$n/wine.log" 2>/dev/null | head -1)   # wine_ref.sh writes <out>/<scene>/wine.log; the dry run <out>/wine.log
		[ -n "$wl" ] && echo "- run $n: wine.log $(wc -l <"$wl") lines; error/crash lines: $(grep -aiE 'err:|wine:|unhandled|page fault|exception|crash' "$wl" | head -4 | cut -c1-160 | tr '\n' '|')"
		[ -s "$s/run$n.d9s" ] || echo "- run $n: NO STREAM (the game never created a device)"
	  done; echo
	  if [ -s "$s/run1.d9s" ]; then
		local -a req=(); [ "$sc" = cell ] && req=(--require-world)   # the menu scene is not expected to reach a world; the cell scene must
		python3 -I "$REC/farm/d9rec_stats.py" "$s/run1.d9s" --tsv-dir "$s/tsv1" --json "$s/stats1.json" --title "$sc run 1" "${req[@]}" \
			--world-min-draws "${D9REC_WORLD_MIN_DRAWS:-100}" --world-min-frames "${D9REC_WORLD_MIN_FRAMES:-10}" --world-min-textures "${D9REC_WORLD_MIN_TEXTURES:-50}" \
			>"$s/stats1.md" 2>"$s/stats1.err"; local wr=$?
		sed 's/^#/##/' "$s/stats1.md"
		[ "$wr" = 4 ] && NOTES+=("FAILED: scene $sc did not reach a world cell: $(tail -n 1 "$s/stats1.err" | cut -c1-300)")
		[ "$wr" != 0 ] && [ "$wr" != 4 ] && NOTES+=("FAILED: scene $sc: stats exit $wr")
		python3 -I "$P/tests/l6/rec/dump_env.py" "$s/run1.d9s" "$s/env_answers.tsv" >/dev/null 2>&1; echo "- env answers (separate TSV): $(($(wc -l <"$s/env_answers.tsv") - 1)) rows"; echo
	  fi
	  echo "### Determinism: run 1 vs run 2 of scene $sc (d9cmp.py)"; echo
	  if [ -s "$s/run1.d9s" ] && [ -s "$s/run2.d9s" ]; then
		timeout "${D9REC_CMP_TIMEOUT_S:-3000}" python3 -I "$D9/cmp/d9cmp.py" "$s/run1.d9s" "$s/run2.d9s" --json "$s/cmp.json" >"$s/cmp.txt" 2>&1; local cr=$?
		echo "- d9cmp exit $cr ($([ "$cr" = 0 ] && echo EQUAL || { [ "$cr" = 1 ] && echo 'DET MISMATCH'; } || { [ "$cr" = 124 ] && echo TIMEOUT; } || echo incomparable))"
		head -c 1500 "$s/cmp.txt" | sed 's/^/- /'
	  else echo "- not run: a stream is missing"; fi; echo; } >"$f"
	[ -s "$s/run1.d9s" ] || NOTES+=("FAILED: scene $sc: no stream")
}

pack() {   # pack: raw streams as xz (parts of 95 MB above 99 MB, as sweep_job.sh), TSVs, JSON; pushed to $BRANCH unless D9REC_NO_PUSH / DRY
	local pub=$WK/pub sc n; rm -rf "$pub"; mkdir -p "$pub"
	for sc in menu cell; do
		mkdir -p "$pub/$sc"
		for n in 1 2; do
			[ -s "$WK/$sc/run$n.d9s" ] || continue
			xz -T2 -6 -c "$WK/$sc/run$n.d9s" >"$pub/$sc/run$n.d9s.xz"
			if [ "$(stat -c %s "$pub/$sc/run$n.d9s.xz")" -gt 99000000 ]; then   # GitHub refuses > 100 MB: cat run<n>.d9s.xz.part* > run<n>.d9s.xz
				split -b 95m -d -a 2 "$pub/$sc/run$n.d9s.xz" "$pub/$sc/run$n.d9s.xz.part" && rm -f "$pub/$sc/run$n.d9s.xz"
			fi
		done
		cp -f "$WK/$sc/env_answers.tsv" "$WK/$sc/stats1.json" "$WK/$sc/cmp.json" "$WK/$sc/cmp.txt" "$pub/$sc/" 2>/dev/null
		cp -f "$WK/$sc"/tsv1/*.tsv "$pub/$sc/" 2>/dev/null
	done
	cp -f "$WK/probe.d9s" "$WK/env_probe.tsv" "$pub/" 2>/dev/null
	(cd "$pub" && find . -type f -print0 | sort -z | xargs -0 sha256sum >../SHA256SUMS && mv ../SHA256SUMS SHA256SUMS)
	PACKED="$(find "$pub" -type f | wc -l) files, $(du -sh "$pub" | cut -f1)"
	if [ "$DRY" = 1 ] || [ "${D9REC_NO_PUSH:-0}" = 1 ]; then PUSHED="not pushed (dry run or D9REC_NO_PUSH)"; return 0; fi
	local g=$WK/pub.git; rm -rf "$g"; git init -q "$g" && cp -a "$pub"/. "$g"/ && git -C "$g" add -A &&
		git -C "$g" -c user.name="fnvnx farm" -c user.email=farm@invalid commit -qm "d9rec $H12 $PROFILE

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" &&
		git -C "$g" push -q -f "$(git -C "$ARTIFACTS" remote get-url origin)" "HEAD:refs/heads/$BRANCH" 2>"$WK/push.log" &&
		PUSHED="pushed to $BRANCH of the artifacts repository" || PUSHED="PUSH FAILED to $BRANCH (log $WK/push.log)"
	rm -rf "$g"
}

PACKED=""; PUSHED="not run"
write_report() {
	{ echo "# d9rec $H12 $PROFILE"; echo
	  echo "- commit $COMMIT, profile $PROFILE, seconds $(( $(date +%s) - T0 ))$([ "$DRY" = 1 ] && echo ', DRY RUN')"
	  printf -- '- %s\n' "${NOTES[@]:-none}"; echo
	  for f in "$SEC"/0_profile.md "$SEC"/menu.md "$SEC"/cell.md; do [ -f "$f" ] && cat "$f"; done
	  echo "## Artifacts"; echo; echo "- ${PACKED:-not packed}; $PUSHED"; echo "- branch $BRANCH: <scene>/run{1,2}.d9s.xz[.partNN], env_answers.tsv, perframe.tsv, methods.tsv, modes.tsv, stats1.json, cmp.txt/json; env_probe.tsv, probe.d9s"
	} >"$REPORT"
}

profile_check
scene menu; scene cell
pack
write_report
log "done ($(( $(date +%s) - T0 )) s)"
echo "$REPORT"; grep -q '^- FAILED' "$REPORT" && exit 1; exit 0
