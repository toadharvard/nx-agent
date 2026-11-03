#!/usr/bin/env bash
# census.sh - compile every decomp unit for AArch64 ILP32 with -fsyntax-only and summarize the errors.
#
#   tools/census.sh <fnv_decomp checkout> <out dir> [jobs]
#
# Writes <out>/logs/<unit>.log, <out>/summary.txt (clean/failed counts, top error kinds, top missing names)
# and <out>/units.tsv (unit, error count, first error). The decomp itself is never copied anywhere.
set -euo pipefail
DECOMP=$(cd "$1" && pwd); OUT=$(mkdir -p "$2" && cd "$2" && pwd); JOBS=${3:-$(nproc)}
HERE=$(cd "$(dirname "$0")/.." && pwd)
CXX=${CXX:-aarch64-linux-gnu-g++}
SSE2NEON=${SSE2NEON:-$HERE/ext/sse2neon}
export CXX DECOMP OUT HERE SSE2NEON
mkdir -p "$OUT/logs"

FLAGS=(-mabi=ilp32 -std=gnu++17 -fsyntax-only -fms-extensions -fshort-wchar -fpermissive -frounding-math -fsigned-char -fno-strict-aliasing -fwrapv -fno-delete-null-pointer-checks -ffp-contract=off -w
       -fmax-errors=200 -include "$HERE/shim/include/fnvnx_compat.h"
       -I"$HERE/shim/include" -I"$SSE2NEON" -I"$DECOMP/include")
export FLAGS_STR="${FLAGS[*]}"

jq -r '.units[].source' "$DECOMP/build/units.json" | sort -u > "$OUT/units.list"

compile_one() {
  local src=$1 log="$OUT/logs/$(echo "$1" | tr / _).log"
  # shellcheck disable=SC2086
  (cd "$DECOMP" && timeout 300 $CXX $FLAGS_STR "$src") > "$log" 2>&1 || true
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

# the summary pipes end in `head`: a closed pipe upstream is expected, not a failure
set +o pipefail

{
  total=$(wc -l < "$OUT/units.list")
  clean=$(awk -F'\t' '$2==0' "$OUT/units.tsv" | wc -l)
  echo "units: $total  clean: $clean  failing: $((total - clean))"
  echo "errors total: $(awk -F'\t' '{s+=$2} END {print s}' "$OUT/units.tsv")"
  echo; echo "== error kinds (normalized, all errors)"
  cat "$OUT"/logs/*.log | grep ': error:' | sed -E "s/^[^:]+:[0-9]+:[0-9]+: error: //; s/'[^']*'/'X'/g; s/[0-9]+/N/g" \
    | sort | uniq -c | sort -rn | head -40
  echo; echo "== missing names (not declared / does not name a type / has no member)"
  cat "$OUT"/logs/*.log | grep -oE "error: ('[^']+' (was not declared|does not name a type|has not been declared)|.*has no member named '[^']+')" \
    | grep -oE "'[^']+'" | tail -n +1 | sort | uniq -c | sort -rn | head -80
  echo; echo "== headers that fail most (file of first error)"
  cat "$OUT"/logs/*.log | grep -m1000000 ': error:' | cut -d: -f1 | sort | uniq -c | sort -rn | head -25
} > "$OUT/summary.txt"
head -3 "$OUT/summary.txt"
