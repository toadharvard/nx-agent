#!/bin/bash
# libdeko3d.a for the native renderer's host module (NX1 HOST=native; the same pins as render/native/env.sh):
# dekotools' dekodef (host tool, meson) then deko3d's library with devkitA64. DEVKITPRO is read, never modified:
# headers and the library go to <prefix>/{include,lib}. Cached: a stamp with the pins skips a rebuild. ~2 min.
#   DEVKITPRO=<dkp> toolchain/deko3d/build.sh <prefix>     -> <prefix>/include/deko3d.h, <prefix>/lib/libdeko3d.a
set -euo pipefail
: "${DEVKITPRO:?set DEVKITPRO}"
P=$(mkdir -p "$1" && cd "$1" && pwd)
DEKO3D=373bfd43d8b2d805fa2eb7d406dbb823032df958      # devkitPro/deko3d
DEKOTOOLS=c15523cea963c04fad77a46f23c3a96d26a86ab9   # devkitPro/dekotools
STAMP="deko3d $DEKO3D dekotools $DEKOTOOLS"
if [ -f "$P/.stamp" ] && [ "$(cat "$P/.stamp")" = "$STAMP" ] && [ -f "$P/lib/libdeko3d.a" ]; then echo "$P"; exit 0; fi
JOBS=${JOBS:-$(nproc)}
pin() {  # dir url commit
	[ -d "$P/src/$1/.git" ] || git clone -q --filter=blob:none "$2" "$P/src/$1"
	git -C "$P/src/$1" fetch -q origin "$3" 2>/dev/null || git -C "$P/src/$1" fetch -q origin
	git -C "$P/src/$1" checkout -q --detach "$3"
}
pin dekotools https://github.com/devkitPro/dekotools "$DEKOTOOLS"
pin deko3d https://github.com/devkitPro/deko3d "$DEKO3D"
rm -rf "$P/build-dekotools"
meson setup "$P/build-dekotools" "$P/src/dekotools" --prefix="$P/tools" >/dev/null
ninja -C "$P/build-dekotools" -j"$JOBS" install >/dev/null
rm -rf "$P/build-dekotools"
export PATH=$P/tools/bin:$DEVKITPRO/tools/bin:$DEVKITPRO/devkitA64/bin:$PATH
make -C "$P/src/deko3d" -j"$JOBS" lib/libdeko3d.a >/dev/null
mkdir -p "$P/include" "$P/lib"
cp -a "$P/src/deko3d/include/." "$P/include/"
cp "$P/src/deko3d/lib/libdeko3d.a" "$P/lib/"
echo "$STAMP" > "$P/.stamp"
echo "$P"
