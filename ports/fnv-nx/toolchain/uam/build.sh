#!/bin/bash
# uam, the host shader compiler of the native renderer (GLSL -> DKSH; render/native/shaders/build.py), at the render
# machine's pin (render/native/env.sh step_uam). LINK_OVERRIDES=render needs it to build libnxrender.a on farms that
# have no render machine environment. meson + ninja + bison + flex + the system python3's mako, a host C/C++
# compiler; about 6 min on 4 cores.
# Cached: a stamp with the pin skips a rebuild.
#   toolchain/uam/build.sh <prefix>     -> <prefix>/bin/uam (printed)
set -euo pipefail
P=$(mkdir -p "$1" && cd "$1" && pwd)
UAM=5a5afc2bae8b55409ab36ba45be63fcb73f68993   # devkitPro/uam
STAMP="uam $UAM"
if [ -f "$P/.stamp" ] && [ "$(cat "$P/.stamp")" = "$STAMP" ] && [ -x "$P/bin/uam" ]; then echo "$P/bin/uam"; exit 0; fi
JOBS=${JOBS:-$(nproc)}
[ -d "$P/src/uam/.git" ] || git clone -q --filter=blob:none https://github.com/devkitPro/uam "$P/src/uam"
git -C "$P/src/uam" fetch -q origin "$UAM" 2>/dev/null || git -C "$P/src/uam" fetch -q origin
git -C "$P/src/uam" checkout -q --detach "$UAM"
rm -rf "$P/build"
meson setup "$P/build" "$P/src/uam" --prefix="$P" --buildtype=release >/dev/null
ninja -C "$P/build" -j"$JOBS" install >/dev/null
rm -rf "$P/build"
[ -x "$P/bin/uam" ] || { echo "uam build: no $P/bin/uam" >&2; exit 1; }
echo "$STAMP" > "$P/.stamp"
echo "$P/bin/uam"
