#!/bin/bash
# fnv-nx native renderer (S6n): rebuild the build environment from source. Idempotent (each step leaves a stamp).
#
#   render/native/env.sh [step ...]        steps: apt sources toolchain libnx dekotools deko3d uam all (default)
#   source <(render/native/env.sh --print) exports DEVKITPRO, PATH, UAM for the other scripts
#
# Layout (S6N_WORK, default /home/user/work/s6n; nothing here goes into git):
#   dl/                     binutils/gcc/newlib tarballs        ext/   buildscripts, devkita64-rules, deko3d, dekotools, uam
#   build/                  build trees (deleted after install)  opt/fnvnx-a64  the toolchain (docs/TOOLCHAIN.md §3)
#   opt/dkp                 DEVKITPRO tree: devkitA64 -> fnvnx-a64, libnx (+ deko3d in libnx/)   opt/dekotools, opt/uam (host tools)
# Pins: deko3d 373bfd4, uam 5a5afc2, dekotools master (c15523c at the time of writing), libnx = ext/libnx (tools/fetch.sh).
# Costs (4 cores, JOBS=2): toolchain ~90 min, libnx 3 min, deko3d 1 min, uam 6 min, dekotools 1 min.
set -euo pipefail
W=${S6N_WORK:-/home/user/work/s6n}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
JOBS=${JOBS:-2}
DKP=$W/opt/dkp
if [ "${1:-}" = "--print" ]; then
	echo "export DEVKITPRO=$DKP"
	echo "export PATH=$DKP/tools/bin:$W/opt/fnvnx-a64/bin:$W/opt/dekotools/bin:\$PATH"
	echo "export UAM=$W/opt/uam/bin/uam"
	exit 0
fi
mkdir -p "$W"/{dl,ext,build,opt}
stamp() { [ -f "$W/.stamp-$1" ]; }
done_() { touch "$W/.stamp-$1"; echo "== $1 done"; }

step_apt() {
	stamp apt && return
	apt-get update -qq
	DEBIAN_FRONTEND=noninteractive apt-get install -y -qq libgmp-dev libmpfr-dev libmpc-dev texinfo bison flex \
		libegl1 libegl-mesa0 libegl-dev libgl-dev libopengl-dev libopengl0 meson ninja-build python3-pil python3-numpy
	done_ apt
}
step_sources() {
	stamp sources && return
	cd "$W/dl"
	for u in https://ftp.gnu.org/gnu/binutils/binutils-2.45.1.tar.xz https://ftp.gnu.org/gnu/gcc/gcc-15.2.0/gcc-15.2.0.tar.xz \
	         https://sourceware.org/pub/newlib/newlib-4.6.0.20260123.tar.gz; do
		[ -f "$(basename "$u")" ] || curl -sSfLO "$u"
	done
	cd "$W/ext"
	[ -d buildscripts ] || git clone -q https://github.com/devkitPro/buildscripts
	[ -d devkita64-rules ] || git clone -q https://github.com/devkitPro/devkita64-rules
	[ -d deko3d ] || { git clone -q https://github.com/devkitPro/deko3d && git -C deko3d checkout -q 373bfd4; }
	[ -d dekotools ] || git clone -q https://github.com/devkitPro/dekotools
	[ -d uam ] || { git clone -q https://github.com/devkitPro/uam && git -C uam checkout -q 5a5afc2; }
	done_ sources
}
step_toolchain() {   # docs/TOOLCHAIN.md §3: fnvnx-a64 = devkitA64 sources + ILP32 multilibs
	stamp toolchain && return
	JOBS=$JOBS "$ROOT/toolchain/gcc/build.sh" "$W/dl" "$W/build/tc" "$W/opt/fnvnx-a64" "$W/ext/buildscripts"
	rm -rf "$W/build/tc"
	done_ toolchain
}
step_libnx() {
	stamp libnx && return
	[ -d "$ROOT/ext/libnx" ] || { echo "ext/libnx missing: run tools/fetch.sh"; exit 1; }
	rm -rf "$W/build/libnx"; cp -a "$ROOT/ext/libnx" "$W/build/libnx"   # builds in-tree; keep ext/ clean
	JOBS=$JOBS "$ROOT/toolchain/devkita64/setup.sh" "$W/opt/fnvnx-a64" "$W/build/libnx" "$W/ext/devkita64-rules" "$DKP"
	rm -rf "$W/build/libnx"
	done_ libnx
}
step_dekotools() {
	stamp dekotools && return
	meson setup "$W/build/dekotools" "$W/ext/dekotools" --prefix="$W/opt/dekotools"
	ninja -j"$JOBS" -C "$W/build/dekotools" install
	rm -rf "$W/build/dekotools"
	done_ dekotools
}
step_deko3d() {      # libdeko3d.a for the LP64 host module; needs dekodef from dekotools
	stamp deko3d && return
	export DEVKITPRO=$DKP PATH=$DKP/tools/bin:$W/opt/fnvnx-a64/bin:$W/opt/dekotools/bin:$PATH
	make -C "$W/ext/deko3d" -j"$JOBS" lib/libdeko3d.a
	cp -a "$W/ext/deko3d/include/." "$DKP/libnx/include/"     # where upstream's 'make install' puts it
	cp "$W/ext/deko3d/lib/libdeko3d.a" "$DKP/libnx/lib/"
	done_ deko3d
}
step_uam() {         # host shader compiler (GLSL 4.60 -> DKSH)
	stamp uam && return
	meson setup "$W/build/uam" "$W/ext/uam" --prefix="$W/opt/uam"
	ninja -j"$JOBS" -C "$W/build/uam" install
	rm -rf "$W/build/uam"
	done_ uam
}

steps=("$@"); [ ${#steps[@]} -eq 0 ] && steps=(all)
for s in "${steps[@]}"; do
	case $s in
	all) step_apt; step_sources; step_toolchain; step_libnx; step_dekotools; step_deko3d; step_uam ;;
	apt|sources|toolchain|libnx|dekotools|deko3d|uam) "step_$s" ;;
	*) echo "unknown step $s"; exit 2 ;;
	esac
done
