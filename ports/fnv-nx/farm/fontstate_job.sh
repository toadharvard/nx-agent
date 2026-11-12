#!/usr/bin/env bash
# fontstate_job.sh - farm job: the font state of the reference environment (distribution Wine, fresh 32-bit prefix,
# WINEARCH=win32, wineboot -i under Xvfb), the reference of L5's GDI font code. The reference is Wine 9.0: any other
# `wine --version` ends the job with exit 1 before anything is published.
#
#   bash <checkout>/ports/fnv-nx/farm/fontstate_job.sh <report.md>      (run directly on a farm, not through the runner)
#
# Publishes to the artifacts repository, branch tools/fonts, directory prefix/ (replaced as a whole every run):
#   files.sha256           sha256 and path of every file under Wine's font directories and the prefix's Fonts directory
#   wine-fonts/, pfx-fonts/  the files themselves (.fon, .ttf, ...; Wine's own fonts, LGPL)
#   reg/<n>.reg + reg/INDEX  `wine reg export` of the font keys (INDEX: file, key, rc; a missing key is rc != 0)
#   versions.txt           wine --version, dpkg versions, the kernel, the DPI values
#   fc-list.txt            `fc-list : family file` sorted (the host fonts Wine reaches through fontconfig)
#   fontdump.txt           stdout of src/fontdump.c of the same branch (i686 mingw, run in the prefix)
# src/fontdump.c is required: a branch without it, a compile error or a non-zero exit of the program ends the job with
# exit 1 and nothing is published. The report's last notes give the branch commit (40 hex), the sha256 of fontdump.txt
# and the tree hash of the branch checkout (`verify/cas.py ingest`, .git excluded): what a recipe's external names.
# Needs: root for apt (wine32:i386, xvfb, gcc-mingw-w64-i686, fontconfig), the artifacts repository with push.
# Env: FARM_HOME (~/fnvnx-farm), ARTIFACTS (/home/user/fnv-nx-build). Runtime: about 3 minutes after apt.
set -uo pipefail
REPORT=${1:?usage: fontstate_job.sh <report.md>}; REPORT=$(readlink -f "$REPORT")
FARM_HOME=${FARM_HOME:-$HOME/fnvnx-farm}; ARTIFACTS=${ARTIFACTS:-/home/user/fnv-nx-build}
W=$FARM_HOME/fontstate; rm -rf "$W"; mkdir -p "$W/out/reg"; O=$W/out
NOTES=()
finish() { { echo "# fontstate"; echo; for n in "${NOTES[@]}"; do echo "- $n"; done; } > "$REPORT"; cat "$REPORT"; exit "${1:-0}"; }

need=""; for c in wine Xvfb i686-w64-mingw32-gcc fc-list git; do command -v "$c" > /dev/null || need="$need $c"; done
if [ -n "$need" ]; then
	dpkg --add-architecture i386; apt-get update -qq
	DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wine32:i386 wine gcc-mingw-w64-i686 xvfb fontconfig git \
		> "$W/apt.log" 2>&1 || { NOTES+=("apt-get install failed (log $W/apt.log)"); finish 1; }
fi

export WINEPREFIX=$W/pfx WINEARCH=win32 WINEDEBUG=-all WINEDLLOVERRIDES="mscoree,mshtml=;winemenubuilder.exe=d"
export LC_ALL=C TZ=UTC
DISP=:87; Xvfb "$DISP" -screen 0 1280x720x24 -nolisten tcp -noreset > /dev/null 2>&1 & XPID=$!
trap 'wineserver -k 2> /dev/null; kill $XPID 2> /dev/null' EXIT
export DISPLAY=$DISP; sleep 2
wineboot -i > "$W/wineboot.log" 2>&1; rc=$?; wineserver -w
NOTES+=("wineboot -i rc $rc, prefix $(du -sh "$WINEPREFIX" | cut -f1)")
[ "$rc" = 0 ] || finish 1
WV=$(wine --version 2>&1)
case "$WV" in "wine-9.0 "*|"wine-9.0") ;; *) NOTES+=("the reference is Wine 9.0, this host has: $WV"); finish 1 ;; esac

# 1. font files: every directory named fonts under a wine directory of the distribution, and the prefix's Fonts
# (the directories of the installed wine packages themselves: dpkg -L of every package whose name starts with wine,
# libwine or fonts-wine)
mapfile -t WDIRS < <(dpkg-query -W -f '${Package}:${Architecture}\n' 'wine*' 'libwine*' 'fonts-wine*' 2> /dev/null |
	while read -r p; do dpkg -L "$p" 2> /dev/null; done | while read -r f; do [ -d "$f" ] && echo "$f"; done |
	grep -i '/fonts$' | sort -u)
: > "$O/files.sha256"
for d in "${WDIRS[@]}"; do
	dst=$O/wine-fonts$d; mkdir -p "$dst"; cp -aL "$d/." "$dst/"
	(cd / && find "${d#/}" -type f -o -type l | sort | while read -r f; do sha256sum "$f"; done) >> "$O/files.sha256"
done
PF=$WINEPREFIX/drive_c/windows/Fonts
if [ -d "$PF" ]; then
	mkdir -p "$O/pfx-fonts"; cp -aL "$PF/." "$O/pfx-fonts/"
	(cd "$WINEPREFIX" && find drive_c/windows/Fonts \( -type f -o -type l \) | sort | while read -r f; do
		printf '%s  %s -> %s\n' "$(sha256sum < "$f" | cut -d' ' -f1)" "$f" "$(readlink -f "$f")"; done) >> "$O/files.sha256"
fi
NOTES+=("font directories: ${WDIRS[*]:-none} + prefix Fonts; $(wc -l < "$O/files.sha256") files, $(grep -ci '\.fon' "$O/files.sha256") .fon")

# 2. registry
i=0; : > "$O/reg/INDEX"
while IFS= read -r key; do
	i=$((i + 1)); f=$(printf '%02d.reg' "$i")
	wine reg export "$key" "$(printf 'Z:%s' "$O/reg/$f" | tr / '\\')" /y > "$W/reg$i.log" 2>&1; rc=$?
	[ -f "$O/reg/$f" ] || rc="$rc(no file)"
	printf '%s\t%s\trc=%s\n' "$f" "$key" "$rc" >> "$O/reg/INDEX"
done <<'EOK'
HKLM\Software\Microsoft\Windows NT\CurrentVersion\FontSubstitutes
HKLM\Software\Microsoft\Windows NT\CurrentVersion\Fonts
HKLM\Software\Microsoft\Windows NT\CurrentVersion\FontLink\SystemLink
HKLM\Software\Microsoft\Windows NT\CurrentVersion\FontDPI
HKLM\Software\Microsoft\Windows\CurrentVersion\Fonts
HKCU\Software\Wine\Fonts
HKLM\System\CurrentControlSet\Control\Nls\CodePage
HKLM\System\CurrentControlSet\Hardware Profiles\Current\Software\Fonts
HKCU\Control Panel\Desktop
HKCU\Control Panel\Desktop\WindowMetrics
EOK
wineserver -w
NOTES+=("registry: $(grep -c 'rc=0$' "$O/reg/INDEX") of $i keys exported (reg/INDEX)")

# 3. versions
{
	echo "wine --version: $(wine --version 2>&1)"
	echo "kernel: $(uname -sr)"; . /etc/os-release; echo "os: $PRETTY_NAME"
	dpkg-query -W 'wine*' 'libwine*' 'fonts-wine*' libfreetype6:i386 libfreetype6 fonts-liberation 'fonts-liberation*' \
		fontconfig libfontconfig1 libfontconfig1:i386 fontconfig-config 2>&1 | sort
	echo "--- DPI values in the exported registry files"
	for f in "$O"/reg/*.reg; do
		python3 -I -c 'import sys
t = open(sys.argv[1], "rb").read().decode("utf-16", "replace")
for ln in t.splitlines():
    if "logpixels" in ln.lower() or "fontsmoothing" in ln.lower(): print(sys.argv[1].rsplit("/", 1)[1], ln)' "$f"
	done
} > "$O/versions.txt" 2>&1
fc-list : family file | sort > "$O/fc-list.txt" 2>&1
NOTES+=("$(head -1 "$O/versions.txt"); fc-list $(wc -l < "$O/fc-list.txt") lines")

# 4. L5's dump program; then publish
g=$W/pub; rm -rf "$g"; url=$(git -C "$ARTIFACTS" remote get-url origin)
git init -q "$g"
git -C "$g" fetch -q --depth 1 "$url" refs/heads/tools/fonts 2> "$W/fetch.log" || { NOTES+=("cannot fetch tools/fonts: $(tail -1 "$W/fetch.log")"); finish 1; }
git -C "$g" checkout -q -b w FETCH_HEAD
[ -f "$g/src/fontdump.c" ] || { NOTES+=("tools/fonts has no src/fontdump.c"); finish 1; }
i686-w64-mingw32-gcc -O1 -o "$W/fontdump.exe" "$g/src/fontdump.c" -lgdi32 -luser32 > "$W/fontdump-cc.log" 2>&1 ||
	{ NOTES+=("fontdump.c did not compile: $(head -3 "$W/fontdump-cc.log" | tr '\n' ' ')"); finish 1; }
(cd "$W" && wine fontdump.exe > "$O/fontdump.txt" 2> "$W/fontdump-run.log"); rc=$?; wineserver -w
NOTES+=("fontdump.exe rc $rc, $(wc -l < "$O/fontdump.txt") lines")
[ "$rc" = 0 ] || finish 1
rm -rf "$g/prefix"; cp -a "$O" "$g/prefix"
git -C "$g" add -A prefix
if git -C "$g" diff --cached --quiet; then NOTES+=("tools/fonts prefix/ unchanged, nothing to publish")
elif git -C "$g" -c user.name="fnvnx farm" -c user.email=farm@invalid commit -qm "fonts: reference prefix state ($(wine --version))" &&
		git -C "$g" push -q "$url" HEAD:refs/heads/tools/fonts 2> "$W/push.log"; then
	NOTES+=("published tools/fonts prefix/ at $(git -C "$g" rev-parse --short=12 HEAD), $(du -sh "$g/prefix" | cut -f1)")
else NOTES+=("publishing failed: $(tail -2 "$W/push.log" | tr '\n' ' ')"); finish 1; fi
rm -rf "$W/store"
TREE=$(python3 -I "$(dirname "$(readlink -f "$0")")/../verify/cas.py" ingest "$W/store" "$g" --exclude .git) || { NOTES+=("cas.py ingest failed"); finish 1; }
NOTES+=("commit $(git -C "$g" rev-parse HEAD)")
NOTES+=("fontdump.txt sha256 $(sha256sum < "$g/prefix/fontdump.txt" | cut -c1-64)")
NOTES+=("tree_sha256 $TREE")
finish 0
