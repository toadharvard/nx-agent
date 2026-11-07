#!/usr/bin/env bash
# wine_ref.sh - the reference side of S16-compare: the player's own FalloutNV.exe under Wine, headless (Xvfb), with
# software GPU rendering (WineD3D -> OpenGL -> Mesa llvmpipe). Nothing of the game is copied: the prefix holds a
# mirror of symlinks, plus our ini files and console scripts in the mirror's own directories.
#
#   wine_ref.sh setup <game dir> <work dir>              prefix, symlink mirror, ini files (idempotent)
#   wine_ref.sh run   <work dir> <scene id> <out dir>    boot, drive the console, capture frames + scene state
#   wine_ref.sh versions                                 the exact versions of every piece (for the report)
#   wine_ref.sh mirror <game dir> <dir>                  only the Data/ mirror (base load order, no .nam/DLC)
#
# Scene ids and their console scripts: tools/compare/scenes.json. Output per scene: <out>/<scene>/frame_<n>.png,
# <out>/<scene>/console.txt (scof output), <out>/<scene>/run.json (timings, versions, ini hash).
# Needs: wine (32-bit, wine32:i386), xvfb, xdotool, x11-apps (xwd), imagemagick (convert), python3.
# Env: S16_SCENES (scene list, default scenes.json), S16_D9CAP (d3d9.dll of d9cap), S16_STOP_AFTER_FRAMES,
#      S16_PCI_DEVICE (WineD3D's PCI device id, setup; default 000006cd, 00000193 = GeForce 8800 for SM3),
#      S16_W/S16_H (default 1280x720), S16_BOOT_S (seconds to the main menu, default 150),
#      S16_SETTLE_S (seconds after a scene script before capture, default 60), S16_DISPLAY (default :77).
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
SCN=${S16_SCENES:-$HERE/scenes.json}
# the adapter description WineD3D reports for the pinned PCI id: FalloutPrefs.ini's sD3DDevice must equal it, or the
# game resets its INIs, starts FalloutNVLauncher.exe and exits (seen with 00000193 and the GTX 470 name)
case "${S16_PCI_DEVICE:-000006cd}" in
	000006cd) D3DDEV='"NVIDIA GeForce GTX 470"' ;;
	00000193) D3DDEV='"NVIDIA GeForce 8800 GTS"' ;;   # as Wine 9.0 reports it (GetAdapterIdentifier, checked)
	*) D3DDEV=${S16_D3D_DEVICE:?S16_D3D_DEVICE: the adapter description for S16_PCI_DEVICE=$S16_PCI_DEVICE} ;;
esac   # the scene list (the sweep passes its own: cells.py scene)
W=${S16_W:-1280}; H=${S16_H:-720}
DISP=${S16_DISPLAY:-:77}

wenv() {  # wenv <work dir>: the environment every wine call of this tool runs in
	export WINEPREFIX=$1/pfx WINEARCH=win32 WINEDEBUG=${WINEDEBUG:--all}
	# S16_D9CAP=<d3d9.dll>: d9cap beside the exe journals the D3D9 stream (S16_CAPTURE, S16_CAPTURE_FRAMES, S16_CAPTURE_MAX_MB)
	export WINEDLLOVERRIDES="mscoree,mshtml=;winemenubuilder.exe=d${S16_D9CAP:+;d3d9=n,b}"
	export LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe MESA_GL_VERSION_OVERRIDE=${MESA_GL_VERSION_OVERRIDE:-}
	[ -z "$MESA_GL_VERSION_OVERRIDE" ] && unset MESA_GL_VERSION_OVERRIDE
	export LP_NUM_THREADS=${LP_NUM_THREADS:-$(nproc)}
}

xstart() {  # a private X server for the run
	pkill -f "Xvfb $DISP " 2>/dev/null; sleep 1
	Xvfb "$DISP" -screen 0 "${W}x${H}x24" -nolisten tcp -noreset >/dev/null 2>&1 &
	XPID=$!; export DISPLAY=$DISP; sleep 2
}

game_dir() { echo "$1/pfx/drive_c/FNV"; }
docs_dir() { echo "$1/pfx/drive_c/users/$(id -un)/Documents/My Games/FalloutNV"; }

mirror_data() {  # mirror_data <game dir> <prefix game dir>: Data/ as symlinks, the base game's load order only
	local game=$1 g=$2
	# Data: only the load order of scenes.json. A .nam file makes the game load its add-on even when plugins.txt
	# leaves it out (S5-data: "Caravan Pack items added to inventory"), so no .nam, no master outside "plugins",
	# and no BSA named after such a master ("DeadMoney - Main.bsa"). Update.bsa and "Fallout - *.bsa" are base game.
	local keep; keep=$(python3 -I -c 'import json,sys; print("\n".join(p.lower() for p in json.load(open(sys.argv[1]))["plugins"]))' "$SCN")
	find "$g/Data" -maxdepth 1 -type l -delete
	for f in "$game"/Data/*; do
		local b l stem; b=$(basename "$f"); l=$(echo "$b" | tr 'A-Z' 'a-z')
		case "$l" in
			*.nam) continue ;;
			*.esm|*.esp) grep -qx "$l" <<<"$keep" || continue ;;
			*.bsa)
				stem=${l%% - *}; stem=${stem%.bsa}
				case "$stem" in fallout|update) ;; *) grep -qx "$stem.esm" <<<"$keep" || grep -qx "$stem.esp" <<<"$keep" || continue ;; esac ;;
		esac
		ln -sfn "$f" "$g/Data/$b"
	done
}

cmd_setup() {
	local game=${1:?game dir} work=${2:?work dir}
	game=$(cd "$game" && pwd); mkdir -p "$work"; work=$(cd "$work" && pwd)
	[ -f "$game/FalloutNV.exe" ] || { echo "no FalloutNV.exe in $game" >&2; return 2; }
	wenv "$work"
	if [ ! -f "$work/pfx/system.reg" ]; then
		xstart; wineboot -i >"$work/wineboot.log" 2>&1; wineserver -w; kill "$XPID" 2>/dev/null
	fi
	# the mirror: every top-level file and every Data entry a symlink; our own files are real files beside them
	local g; g=$(game_dir "$work"); mkdir -p "$g/Data"
	for f in "$game"/*; do [ "$(basename "$f")" = Data ] || ln -sfn "$f" "$g/$(basename "$f")"; done
	mirror_data "$game" "$g"
	# no audio device in a container: Wine's null driver; the game's own install key (GOG layout)
	cat >"$work/s16.reg" <<EOF
REGEDIT4

[HKEY_CURRENT_USER\\Software\\Wine\\Drivers]
"Audio"=""

[HKEY_CURRENT_USER\\Software\\Wine\\Direct3D]
"renderer"="gl"
"VideoMemorySize"="1024"
"VideoPciVendorID"=dword:000010de
"VideoPciDeviceID"=dword:${S16_PCI_DEVICE:-000006cd}

[HKEY_LOCAL_MACHINE\\Software\\Bethesda Softworks\\FalloutNV]
"Installed Path"="C:\\\\FNV\\\\"
EOF
	wine regedit /S "$work/s16.reg" >/dev/null 2>&1; wineserver -w
	# load order: the base game only (scenes.json "plugins"); the same plugins.txt is given to our guest
	local ad="$work/pfx/drive_c/users/$(id -un)/AppData/Local/FalloutNV"; mkdir -p "$ad"
	python3 -I -c 'import json,sys; print("\r\n".join(json.load(open(sys.argv[1]))["plugins"]), end="\r\n")' \
		"$SCN" >"$ad/plugins.txt"
	python3 -I "$HERE/ref_ini.py" "$game/Fallout_default.ini" "$(docs_dir "$work")" --width "$W" --height "$H" --d3d-device "$D3DDEV"
	echo "setup: $work ($(du -sh "$work/pfx" | cut -f1))"
}

shot() {  # shot <png>: the X root window (the game runs windowed at exactly the screen size)
	xwd -root -silent -display "$DISP" | convert xwd:- -alpha off "png24:$1"
}

gfocus() {  # the game's window gets the keyboard focus again (a cell load can leave it elsewhere; no window manager)
	local w; w=$(xdotool search --onlyvisible --name 'Fallout' 2>/dev/null | tail -1)
	[ -n "$w" ] && xdotool windowfocus --sync "$w" 2>/dev/null   # (no click: a mouse move turns the camera)
	return 0
}

console() {  # console <text>: open the console, type one line, close it
	xdotool key --delay 200 grave; sleep 1
	xdotool type --delay 60 "$1"; sleep 0.5
	xdotool key Return; sleep 1
	xdotool key --delay 200 grave; sleep 1
}

cmd_run() {
	local work=${1:?work dir} scene=${2:?scene id} out=${3:?out dir}
	work=$(cd "$work" && pwd); mkdir -p "$out/$scene"; out=$(cd "$out/$scene" && pwd)
	wenv "$work"
	local g; g=$(game_dir "$work")
	# the scene's console script becomes .txt files in the game directory, run with `bat`, one per wait:<s> chunk
	rm -f "$g"/s16_"$scene"_*.txt "$g"/s16_console_*.txt
	local plan; plan=$(python3 -I "$HERE/scenes.py" chunks "$SCN" "$scene" "$g" "s16_$scene") || return 2
	python3 -I "$HERE/scenes.py" state "$SCN" "$scene" "$g" "s16_$scene"
	python3 -I "$HERE/ref_ini.py" "$g/Fallout_default.ini" "$(docs_dir "$work")" --width "$W" --height "$H" --d3d-device "$D3DDEV" >"$out/ini.txt"
	local frames; frames=$(python3 -I "$HERE/scenes.py" get "$SCN" "$scene" frames)
	local menu; menu=$(python3 -I "$HERE/scenes.py" get "$SCN" "$scene" menu)
	if [ -n "${S16_D9CAP:-}" ]; then cp -f "$S16_D9CAP" "$g/d3d9.dll"; else rm -f "$g/d3d9.dll"; fi
	xstart
	local t0; t0=$(date +%s)
	(cd "$g" && exec wine FalloutNV.exe >"$out/wine.log" 2>&1) &
	local boot=${S16_BOOT_S:-150} settle=${S16_SETTLE_S:-60}
	sleep "$boot"
	shot "$out/boot.png"
	if [ "$menu" != 1 ]; then
		# one open / type / close per chunk, except that a chunk which loads a cell (coc, cow) is not closed: the load
		# closes the console itself, and a closing keypress after it would reopen it over the frames (S16_DEBUG=1 shots).
		local k w dbg=0 c
		gfocus; xdotool key --delay 200 grave; sleep 1
		xdotool type --delay 60 "scof s16_console_$scene.txt"; sleep 0.5; xdotool key Return; sleep 1
		xdotool key --delay 200 grave; sleep 1
		while read -r k w; do
			gfocus; xdotool key --delay 200 grave; sleep 1
			xdotool type --delay 60 "bat s16_${scene}_$k"; sleep 0.5; xdotool key Return; sleep 1
			c=0; grep -qiE '^\s*(coc|cow|centeroncell|centeronworld)\b' "$g/s16_${scene}_$k.txt" 2>/dev/null && c=1
			[ "$c" = 0 ] && { xdotool key --delay 200 grave; sleep 1; }
			sleep "$w"
			[ "${S16_DEBUG:-0}" = 1 ] && shot "$out/dbg_$((dbg++))_after_chunk_$k.png"
		done <<<"$plan"
		sleep "$settle"
	fi
	local i
	for i in $(seq 0 $((frames - 1))); do shot "$out/frame_$i.png"; sleep "${S16_FRAME_GAP_S:-5}"; done
	# S16_STOP_AFTER_FRAMES=1 (S5-data): end the game right after the shots, so a d9cap journal ends on the scene
	if [ "${S16_STOP_AFTER_FRAMES:-0}" = 1 ]; then wineserver -k 2>/dev/null; sleep 2; menu=1; fi
	if [ "$menu" != 1 ]; then
		console "bat s16_${scene}_state"; sleep 10
		shot "$out/after_state.png"
	fi
	cp -f "$g/s16_console_$scene.txt" "$out/console.txt" 2>/dev/null
	wineserver -k 2>/dev/null; sleep 2; kill "$XPID" 2>/dev/null
	python3 -I - "$out/run.json" "$scene" "$t0" "$boot" "$settle" "$frames" "$W" "$H" <<'EOF'
import json, sys, time
p, scene, t0, boot, settle, frames, w, h = sys.argv[1:]
json.dump({'scene': scene, 'started': int(t0), 'seconds': int(time.time()) - int(t0), 'boot_s': int(boot),
           'settle_s': int(settle), 'frames': int(frames), 'size': [int(w), int(h)]}, open(p, 'w'), indent=1)
EOF
	echo "run: $scene -> $out"
}

cmd_versions() {
	echo "wine: $(wine --version 2>/dev/null)"
	echo "mesa: $(dpkg-query -W -f='${Version}' libgl1-mesa-dri:i386 2>/dev/null) (i386 llvmpipe)"
	echo "xvfb: $(dpkg-query -W -f='${Version}' xvfb 2>/dev/null)"
	echo "xdotool: $(dpkg-query -W -f='${Version}' xdotool 2>/dev/null)"
	echo "llvm: $(dpkg-query -W -f='${Package} ${Version}\n' 'libllvm*:i386' 2>/dev/null | head -1)"
	echo "os: $(. /etc/os-release && echo "$PRETTY_NAME")"
}

case "${1:-}" in
	setup) shift; cmd_setup "$@" ;;
	mirror) shift; mkdir -p "$2/Data"; mirror_data "$@" ;;   # tests: the Data mirror alone
	run) shift; cmd_run "$@" ;;
	versions) cmd_versions ;;
	*) sed -n '2,18p' "$0"; exit 2 ;;
esac
