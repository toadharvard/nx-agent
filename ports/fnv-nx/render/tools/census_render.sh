#!/usr/bin/env bash
# census_render.sh - the S6 subset of tools/census.sh: -fsyntax-only of the render units only, same flags.
#
#   render/tools/census_render.sh <prepared tree> <out dir> [jobs]
#
# The render units are listed by `render/tools/d3d9_inventory.py units` (units whose source or local headers name
# D3D9/D3DX, plus everything under src/bsshader and the NiDX9 renderer). Writes <out>/units.tsv (unit, errors,
# first error), <out>/logs/ and <out>/summary.txt. With FNVNX_D3D_TRACE=1 every D3D9/D3DX method and function
# declared by the shim is marked deprecated, so each call site the compiler resolves to it is reported;
# those are collected in <out>/calls.tsv (unit, Interface::Method, count).
set -euo pipefail
mkdir -p "$2"
TREE=$(realpath "$1"); OUT=$(realpath "$2"); JOBS=${3:-$(nproc)}
HERE=$(realpath "$(dirname "$0")/../..")
CXX=${CXX:-aarch64-linux-gnu-g++}
SSE2NEON=${SSE2NEON:-$HERE/ext/sse2neon}
export CXX TREE OUT
mkdir -p "$OUT/logs"
FLAGS=(-mabi=ilp32 -std=gnu++17 -fsyntax-only -fms-extensions -fshort-wchar -fpermissive -w
       -fmax-errors=200 -include "$HERE/shim/include/fnvnx_compat.h"
       -I"$HERE/shim/include" -I"$SSE2NEON" -I"$TREE/include")
if [ "${FNVNX_D3D_TRACE:-0}" = 1 ]; then  # -w would silence the deprecation reports; the logs get noisier
  kept=(); for f in "${FLAGS[@]}"; do [ "$f" = -w ] || kept+=("$f"); done
  FLAGS=("${kept[@]}" -DFNVNX_D3D_TRACE -Wdeprecated-declarations)
fi
export FLAGS_STR="${FLAGS[*]}"

python3 -I "$HERE/render/tools/d3d9_inventory.py" units "$TREE" > "$OUT/units.list"
compile_one() {
  local log
  log="$OUT/logs/$(echo "$1" | tr / _).log"
  # shellcheck disable=SC2086
  (cd "$TREE" && timeout 300 $CXX $FLAGS_STR "$1") > "$log" 2>&1 || true
}
export -f compile_one
xargs -a "$OUT/units.list" -P "$JOBS" -I{} bash -c 'compile_one "$@"' _ {}

: > "$OUT/units.tsv"
while read -r src; do
  log="$OUT/logs/$(echo "$src" | tr / _).log"
  n=$(grep -c ': error:' "$log" || true)
  first=$(grep -m1 ': error:' "$log" | sed -E 's/^[^:]+:[0-9]+:[0-9]+: error: //' || true)
  printf '%s\t%s\t%s\n' "$src" "$n" "$first" >> "$OUT/units.tsv"
done < "$OUT/units.list"

if [ "${FNVNX_D3D_TRACE:-0}" = 1 ]; then
  python3 -I "$HERE/render/tools/d3d9_inventory.py" calls "$OUT/logs" > "$OUT/calls.tsv"
fi

{
  total=$(wc -l < "$OUT/units.list"); clean=$(awk -F'\t' '$2==0' "$OUT/units.tsv" | wc -l)
  echo "render units: $total  clean: $clean  failing: $((total - clean))"
  echo "errors total: $(awk -F'\t' '{s+=$2} END {print s}' "$OUT/units.tsv")"
  echo; echo "== error kinds"
  cat "$OUT"/logs/*.log | grep ': error:' | sed -E "s/^[^:]+:[0-9]+:[0-9]+: error: //; s/'[^']*'/'X'/g; s/[0-9]+/N/g" \
    | sort | uniq -c | sort -rn | head -30 || true
  echo; echo "== missing names"
  cat "$OUT"/logs/*.log \
    | grep -oE "error: ('[^']+' (was not declared|does not name a type|has not been declared)|.*has no member named '[^']+')" \
    | grep -oE "'[^']+'" | sort | uniq -c | sort -rn | head -60 || true
  echo; echo "== files of first errors"
  cat "$OUT"/logs/*.log | grep ': error:' | cut -d: -f1 | sort | uniq -c | sort -rn | head -25 || true
} > "$OUT/summary.txt"
head -2 "$OUT/summary.txt"
