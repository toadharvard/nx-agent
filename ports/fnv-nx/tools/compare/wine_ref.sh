#!/usr/bin/env bash
# wine_ref.sh - the reference side of S16-compare: the player's own FalloutNV.exe under Wine, headless (Xvfb), with
# software GPU rendering (WineD3D -> OpenGL -> Mesa llvmpipe). Nothing of the game is copied: the prefix holds a
# mirror of symlinks, plus our ini files and console scripts in the mirror's own directories.
#
#   wine_ref.sh setup <game dir> <work dir>              prefix, symlink mirror, ini files (idempotent)
#   wine_ref.sh run   <work dir> <scene id> <out dir>    boot, drive the console, capture frames + scene state
#   wine_ref.sh versions                                 the exact versions of every piece (for the report)
#
# Scene ids and their console scripts: tools/compare/scenes.json. Output per scene: <out>/<scene>/frame_<n>.png,
# <out>/<scene>/console.txt (scof output), <out>/<scene>/run.json (timings, versions, ini hash).
# Needs: wine (32-bit, wine32:i386), xvfb, xdotool, x11-apps (xwd), imagemagick (convert), python3.
# Env: S16_W/S16_H (default 1280x720), S16_BOOT_S (seconds to the main menu, default 150),
#      S16_SETTLE_S (seconds after a scene script before capture, default 60), S16_DISPLAY (default :77).
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
W=${S16_W:-1280}; H=${S16_H:-720}
DISP=${S16_DISPLAY:-:77}

wenv() {  # wenv <work dir>: the environment every wine call of this tool runs in
	export WINEPREFIX=$1/pfx WINEARCH=win32 WINEDEBUG=${WINEDEBUG:--all}
	export WINEDLLOVERRIDES="mscoree,mshtml=;winemenubuilder.exe=d"
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
	for f in "$game"/Data/*; do ln -sfn "$f" "$g/Data/$(basename "$f")"; done
	# no audio device in a container: Wine's null driver; the game's own install key (GOG layout)
	cat >"$work/s16.reg" <<EOF
REGEDIT4

[HKEY_CURRENT_USER\\Software\\Wine\\Drivers]
"Audio"=""

[HKEY_CURRENT_USER\\Software\\Wine\\Direct3D]
"renderer"="gl"
"VideoMemorySize"="1024"
"VideoPciVendorID"=dword:000010de
"VideoPciDeviceID"=dword:000006cd

[HKEY_LOCAL_MACHINE\\Software\\Bethesda Softworks\\FalloutNV]
"Installed Path"="C:\\\\FNV\\\\"
EOF
	wine regedit /S "$work/s16.reg" >/dev/null 2>&1; wineserver -w
	# load order: the base game only (scenes.json "plugins"); the same plugins.txt is given to our guest
	local ad="$work/pfx/drive_c/users/$(id -un)/AppData/Local/FalloutNV"; mkdir -p "$ad"
	python3 -I -c 'import json,sys; print("\r\n".join(json.load(open(sys.argv[1]))["plugins"]), end="\r\n")' \
		"$HERE/scenes.json" >"$ad/plugins.txt"
	python3 -I "$HERE/ref_ini.py" "$game/Fallout_default.ini" "$(docs_dir "$work")" --width "$W" --height "$H"
	echo "setup: $work ($(du -sh "$work/pfx" | cut -f1))"
}

shot() {  # shot <png>: the X root window (the game runs windowed at exactly the screen size)
	xwd -root -silent -display "$DISP" | convert xwd:- -alpha off "png24:$1"
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
	local plan; plan=$(python3 -I "$HERE/scenes.py" chunks "$HERE/scenes.json" "$scene" "$g" "s16_$scene") || return 2
	python3 -I "$HERE/scenes.py" state "$HERE/scenes.json" "$scene" "$g" "s16_$scene"
	python3 -I "$HERE/ref_ini.py" "$g/Fallout_default.ini" "$(docs_dir "$work")" --width "$W" --height "$H" >"$out/ini.txt"
	local frames; frames=$(python3 -I "$HERE/scenes.py" get "$HERE/scenes.json" "$scene" frames)
	local menu; menu=$(python3 -I "$HERE/scenes.py" get "$HERE/scenes.json" "$scene" menu)
	xstart
	local t0; t0=$(date +%s)
	(cd "$g" && exec wine FalloutNV.exe >"$out/wine.log" 2>&1) &
	local boot=${S16_BOOT_S:-150} settle=${S16_SETTLE_S:-60}
	sleep "$boot"
	shot "$out/boot.png"
	if [ "$menu" != 1 ]; then
		console "scof s16_console_$scene.txt"
		local k w
		while read -r k w; do console "bat s16_${scene}_$k"; sleep "$w"; done <<<"$plan"
		sleep "$settle"
	fi
	local i
	for i in $(seq 0 $((frames - 1))); do shot "$out/frame_$i.png"; sleep "${S16_FRAME_GAP_S:-5}"; done
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
	run) shift; cmd_run "$@" ;;
	versions) cmd_versions ;;
	*) sed -n '2,15p' "$0"; exit 2 ;;
esac
