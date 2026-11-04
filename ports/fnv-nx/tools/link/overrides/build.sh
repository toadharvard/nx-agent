#!/usr/bin/env bash
# build.sh - compile the native replacements listed in overrides.tsv and write the table `fnvlink.py resolve
# --override` reads (address, native symbol, object, MSVC signature).
#
#   tools/link/overrides/build.sh <out dir>        # -> <out dir>/<source>.o, <out dir>/override.tsv
#
# Same target as the game objects (AArch64 ILP32, -fPIC: compile.sh). CC defaults to aarch64-linux-gnu-gcc.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$(mkdir -p "$1" && cd "$1" && pwd)
CC=${CC:-aarch64-linux-gnu-gcc}
: > "$OUT/override.tsv"
while IFS=$'\t' read -r addr sym src sig; do
  case "$addr" in ''|'#'*) continue ;; esac
  obj="$OUT/${src%.*}.o"
  $CC -mabi=ilp32 -O2 -fPIC -U_FORTIFY_SOURCE -std=gnu11 -Wall -ffp-contract=off -frounding-math -c "$HERE/$src" -o "$obj"
  printf '%s\t%s\t%s\t%s\n' "$addr" "$sym" "$obj" "$sig" >> "$OUT/override.tsv"
done < "$HERE/overrides.tsv"
echo "overrides: $(grep -c . "$OUT/override.tsv" || true) -> $OUT/override.tsv"
