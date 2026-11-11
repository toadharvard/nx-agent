#!/usr/bin/env bash
# Compiles ff_vs.glsl / ff_fs.glsl to DKSH with uam: build_dksh.sh <outdir>  ->  <outdir>/ff_vs.dksh, ff_fs.dksh
# UAM is the uam binary (default: $FNVNX_SHADERS_DEPS/uam/build/uam, built by render/shaders/build.sh).
set -euo pipefail
OUT=${1:?usage: build_dksh.sh <outdir>}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
UAM=${UAM:-${FNVNX_SHADERS_DEPS:?set UAM or FNVNX_SHADERS_DEPS}/uam/build/uam}
[[ -x $UAM ]] || { echo "build_dksh.sh: uam binary not found: $UAM" >&2; exit 1; }
mkdir -p "$OUT"
"$UAM" -s vert -o "$OUT/ff_vs.dksh" "$HERE/ff_vs.glsl"
"$UAM" -s frag -o "$OUT/ff_fs.dksh" "$HERE/ff_fs.glsl"
echo "build_dksh.sh: $OUT/ff_vs.dksh $OUT/ff_fs.dksh"
