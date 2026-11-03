#!/bin/bash
# Compile the native renderer's GLSL to DKSH with uam:  UAM=<uam binary> shaders/build.sh <out dir>
# File name decides the stage: *_vsh.glsl vertex, *_fsh.glsl fragment.
set -euo pipefail
: "${UAM:?set UAM to the uam binary}"
HERE=$(cd "$(dirname "$0")" && pwd); OUT=$(mkdir -p "$1" && cd "$1" && pwd)
for src in "$HERE"/*_vsh.glsl "$HERE"/*_fsh.glsl; do
	name=$(basename "$src" .glsl)
	case "$name" in *_vsh) stage=vert ;; *) stage=frag ;; esac
	"$UAM" -s "$stage" -o "$OUT/$name.dksh" "$src"
done
