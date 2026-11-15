#!/usr/bin/env bash
# sources.sh - the pinned sources of the fnvnx-a64 toolchain and its DEVKITPRO tree (docs/TOOLCHAIN.md §3):
# the binutils / gcc / newlib tarballs toolchain/gcc/build.sh takes, and the devkitPro checkouts it and
# toolchain/devkita64/setup.sh take (buildscripts: the patches; devkita64-rules: base_rules / base_tools).
#
#   toolchain/sources.sh <tarball dir> <checkout dir>     -> <tarball dir>/*.tar.*, <checkout dir>/{buildscripts,devkita64-rules}
#
# Tarballs come from the GNU / sourceware mirrors (devkitPro's own hosts are not reachable from the cloud
# containers) and are checked against their SHA-256; a file already there with the right hash is kept. The
# checkouts are cloned once and moved to the pinned commit only when they are elsewhere.
set -euo pipefail
DL=$(mkdir -p "${1:?tarball dir}" && cd "$1" && pwd); CO=$(mkdir -p "${2:?checkout dir}" && cd "$2" && pwd)

tarball() {   # file sha256 url...
	local f=$1 sum=$2 u; shift 2
	if [ -f "$DL/$f" ] && echo "$sum  $DL/$f" | sha256sum -c --quiet - 2>/dev/null; then echo "$f ok"; return; fi
	for u in "$@"; do
		if curl -fsSL --retry 3 -o "$DL/$f.part" "$u" && echo "$sum  $DL/$f.part" | sha256sum -c --quiet - 2>/dev/null; then
			mv "$DL/$f.part" "$DL/$f"; echo "$f fetched"; return
		fi
		rm -f "$DL/$f.part"
	done
	echo "cannot fetch $f with SHA-256 $sum" >&2; return 1
}
tarball binutils-2.45.1.tar.xz 5fe101e6fe9d18fdec95962d81ed670fdee5f37e3f48f0bef87bddf862513aa5 \
	https://ftp.gnu.org/gnu/binutils/binutils-2.45.1.tar.xz https://mirrors.kernel.org/gnu/binutils/binutils-2.45.1.tar.xz
tarball gcc-15.2.0.tar.xz 438fd996826b0c82485a29da03a72d71d6e3541a83ec702df4271f6fe025d24e \
	https://ftp.gnu.org/gnu/gcc/gcc-15.2.0/gcc-15.2.0.tar.xz https://mirrors.kernel.org/gnu/gcc/gcc-15.2.0/gcc-15.2.0.tar.xz
# the game compiler's own tarball: its pin lives in toolchain/msvcmodel/build.sh (SHA256=), read here, not retyped
tarball gcc-13.3.0.tar.xz "$(sed -n 's/^SHA256=//p' "$(dirname "$0")/msvcmodel/build.sh")" \
	https://ftp.gnu.org/gnu/gcc/gcc-13.3.0/gcc-13.3.0.tar.xz https://mirrors.kernel.org/gnu/gcc/gcc-13.3.0/gcc-13.3.0.tar.xz
tarball newlib-4.6.0.20260123.tar.gz 6ff27e3bf022666f43f7802255be680eeff722ac181b1725d21e2e8318604ee3 \
	https://sourceware.org/pub/newlib/newlib-4.6.0.20260123.tar.gz

checkout() {  # name url commit
	local d=$CO/$1
	if [ ! -d "$d/.git" ]; then git clone --quiet --filter=blob:none "$2" "$d"; fi
	if [ "$(git -C "$d" rev-parse HEAD)" != "$3" ]; then
		git -C "$d" fetch --quiet origin "$3" 2>/dev/null || git -C "$d" fetch --quiet origin
		git -C "$d" checkout --quiet --detach "$3"
	fi
	echo "$1 @ $(git -C "$d" rev-parse --short HEAD)"
}
checkout buildscripts https://github.com/devkitPro/buildscripts 5740da9beb433ff9f5b724fafc1127f3a5ddc944
checkout devkita64-rules https://github.com/devkitPro/devkita64-rules b037940200a5a9a87888743fb83d7a87e348c8e7
