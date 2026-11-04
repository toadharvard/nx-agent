#!/usr/bin/env bash
# build.sh - S16-compare's two Windows programs, 32-bit, with mingw-w64 (no Microsoft SDK needed):
#
#   tools/compare/d3d9/build.sh <out dir>
#
#   <out>/nxd9play.exe   the reference renderer: an NXD9 capture through real D3D9 (under Wine: WineD3D on llvmpipe)
#   <out>/d3d9.dll       the capture layer: placed beside FalloutNV.exe (WINEDLLOVERRIDES=d3d9=n,b), it forwards to
#                        Wine's own d3d9 and journals the D3D9 call stream (d9cap.c, tools/compare/README.md)
#
# Needs: gcc-mingw-w64-i686 (i686-w64-mingw32-gcc). Nothing of the game is read or linked.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${1:?out dir}
mkdir -p "$OUT"
CC=${MINGW_CC:-i686-w64-mingw32-gcc}
command -v "$CC" >/dev/null || { echo "build.sh: $CC missing (apt-get install gcc-mingw-w64-i686)" >&2; exit 2; }
CFLAGS="-std=gnu11 -O2 -Wall -Wextra -Wno-unused-parameter -Wno-cast-function-type -Werror"
$CC $CFLAGS -o "$OUT/nxd9play.exe" "$HERE/nxd9play.c" -ld3d9 -lgdi32 -luser32 -static-libgcc
if [ -f "$HERE/d9cap.c" ]; then
	$CC $CFLAGS -shared -o "$OUT/d3d9.dll" "$HERE/d9cap.c" "$HERE/d9cap.def" -static-libgcc -Wl,--enable-stdcall-fixup
fi
echo "build.sh: $(ls "$OUT")"
