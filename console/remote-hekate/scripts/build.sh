#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Build Remote Hekate in the pinned devkitARM container.
#
# Usage: scripts/build.sh [SRC]   (default: <remote-hekate>/hekate, see fetch-and-apply.sh)
# Env:   DEVKITARM_IMAGE     override the pinned image reference.
#        DEVKITARM_PLATFORM  docker --platform (e.g. linux/amd64); default: host platform.
#
# Output: build/Remote-Hekate.bin (hekate.bin) and build/nyx.bin, plus build/SHA256SUMS.
set -eu

root=$(cd "$(dirname "$0")/.." && pwd)
src=$(cd "${1:-"$root/hekate"}" && pwd)
out="$root/build"
image=${DEVKITARM_IMAGE:-devkitpro/devkitarm@sha256:116afba8df8453961de2936ffab20dd441edf4d682856c1ec8b0e53d7ed0bbf5}
docker=$(command -v docker || true)
platform=${DEVKITARM_PLATFORM:+--platform=$DEVKITARM_PLATFORM}

if [ -z "$docker" ]; then
	echo "build.sh: docker not found in PATH" >&2
	exit 1
fi

if [ ! -f "$src/Makefile" ] || [ ! -f "$src/nyx/nyx_gui/frontend/remote.c" ]; then
	echo "build.sh: $src is not a patched tree; run scripts/fetch-and-apply.sh first" >&2
	exit 1
fi

"$docker" run --rm $platform --user "$(id -u):$(id -g)" -v "$src":/src -w /src "$image" sh -c \
	'make clean >/dev/null && make -j4 && make -C nyx -j4'

mkdir -p "$out"
cp "$src/output/hekate.bin" "$out/Remote-Hekate.bin"
cp "$src/output/nyx.bin" "$out/nyx.bin"

if command -v sha256sum >/dev/null 2>&1; then
	(cd "$out" && sha256sum Remote-Hekate.bin nyx.bin > SHA256SUMS)
else
	(cd "$out" && shasum -a 256 Remote-Hekate.bin nyx.bin > SHA256SUMS)
fi

cat "$out/SHA256SUMS"
