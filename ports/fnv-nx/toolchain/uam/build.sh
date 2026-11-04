#!/bin/bash
# uam (devkitPro's GLSL 4.60 -> DKSH shader compiler, a host tool) for libnxrender.a's shader package (the link stage
# with LINK_OVERRIDES=render, render/native/n1/build.sh). Same pin as render/native/env.sh. Cached: a stamp with the
# pin skips a rebuild. ~6 min on 4 cores; needs meson, ninja, flex, bison, python mako and a host C++ compiler.
#   toolchain/uam/build.sh <prefix>        -> <prefix>/bin/uam (prints its path)
set -euo pipefail
P=$(mkdir -p "$1" && cd "$1" && pwd)
UAM=5a5afc2bae8b55409ab36ba45be63fcb73f68993   # devkitPro/uam
if [ -f "$P/.stamp" ] && [ "$(cat "$P/.stamp")" = "uam $UAM" ] && [ -x "$P/bin/uam" ]; then echo "$P/bin/uam"; exit 0; fi
JOBS=${JOBS:-$(nproc)}
[ -d "$P/src/.git" ] || git clone -q --filter=blob:none https://github.com/devkitPro/uam "$P/src"
git -C "$P/src" fetch -q origin "$UAM" 2>/dev/null || git -C "$P/src" fetch -q origin
git -C "$P/src" checkout -q --detach "$UAM"
rm -rf "$P/build"
meson setup "$P/build" "$P/src" --prefix="$P" --buildtype=release >/dev/null
ninja -C "$P/build" -j"$JOBS" install >/dev/null
rm -rf "$P/build"
echo "uam $UAM" > "$P/.stamp"
echo "$P/bin/uam"
