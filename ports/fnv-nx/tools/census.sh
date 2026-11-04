#!/usr/bin/env bash
# census.sh - compile every decomp unit for AArch64 ILP32 with -fsyntax-only and summarize the errors.
#
#   tools/census.sh <fnv_decomp checkout> <out dir> [jobs] [units.list]
#
# Writes <out>/logs/<unit>.log, <out>/summary.txt (clean/failed counts, top error kinds, top missing names)
# and <out>/units.tsv (unit, error count, first error). units.list (one source per line, as in units.json)
# restricts the census to those units; default: every unit. The decomp itself is never copied anywhere.
set -euo pipefail
DECOMP=$(cd "$1" && pwd); OUT=$(mkdir -p "$2" && cd "$2" && pwd); JOBS=${3:-$(nproc)}; LIST=${4:-}
HERE=$(cd "$(dirname "$0")/.." && pwd)
CXX=${CXX:-aarch64-linux-gnu-g++}
CC=${CC:-aarch64-linux-gnu-gcc}
SSE2NEON=${SSE2NEON:-$HERE/ext/sse2neon}
export CXX CC DECOMP OUT HERE SSE2NEON
mkdir -p "$OUT/logs"

FLAGS=(-mabi=ilp32 -std=gnu++17 -fsyntax-only -fms-extensions -fshort-wchar -fpermissive -frounding-math -fsigned-char -fno-strict-aliasing -fwrapv -fno-delete-null-pointer-checks -fcheck-new -ffp-contract=off -U_FORTIFY_SOURCE
       -Werror=return-type -Wno-narrowing -fmax-errors=200 -include "$HERE/shim/include/fnvnx_compat.h"
       -I"$HERE/shim/include" -I"$SSE2NEON" -I"$DECOMP/include")
export FLAGS_STR="${FLAGS[*]}"
# C units (third_party/zlib-1.2.1/*.c: MSVC compiled them as C; K&R definitions, C names `_adler32`) build with the C
# compiler: the same flags with C's -std and without the C++-only ones. Every unit gets its units.json /D defines
# (only the zlib units have any: memcpy=fnv_zlib_memcpy, memset=fnv_zlib_memset).
CFLAGS=()
for f in "${FLAGS[@]}"; do
  case $f in -std=gnu++17) CFLAGS+=(-std=gnu99) ;; -fpermissive|-Wno-narrowing) ;; *) CFLAGS+=("$f") ;; esac
done
export CFLAGS_STR="${CFLAGS[*]}"

if [ -n "$LIST" ]; then sort -u "$LIST" > "$OUT/units.list.tmp"; mv "$OUT/units.list.tmp" "$OUT/units.list"
else jq -r '.units[].source' "$DECOMP/build/units.json" | sort -u > "$OUT/units.list"; fi

jq -r '.units[] | [.source, ([.flags[]? | select(startswith("/D")) | "-D" + .[2:]] | join(" "))] | @tsv' \
  "$DECOMP/build/units.json" | awk -F'\t' '$2 != ""' > "$OUT/defines.tsv"
compile_one() {
  local src=$1 log="$OUT/logs/$(echo "$1" | tr / _).log" defs
  defs=$(awk -F'\t' -v s="$1" '$1 == s {print $2; exit}' "$OUT/defines.tsv")
  # shellcheck disable=SC2086
  case $src in
    *.c) (cd "$DECOMP" && timeout 300 $CC $CFLAGS_STR $defs "$src") > "$log" 2>&1 || true ;;
    *)   (cd "$DECOMP" && timeout 300 $CXX $FLAGS_STR $defs "$src") > "$log" 2>&1 || true ;;
  esac
}
export -f compile_one
xargs -a "$OUT/units.list" -P "$JOBS" -I{} bash -c 'compile_one "$@"' _ {}

: > "$OUT/units.tsv"
while read -r src; do
  log="$OUT/logs/$(echo "$src" | tr / _).log"
  n=$(grep -cE ': (fatal )?error:' "$log" || true)       # fatal: e.g. a source missing from the tree
  first=$(grep -m1 -E ': (fatal )?error:' "$log" | sed -E 's/^[^:]+:([0-9]+:[0-9]+:)? (fatal )?error: //' || true)
  printf '%s\t%s\t%s\n' "$src" "$n" "$first" >> "$OUT/units.tsv"
done < "$OUT/units.list"

# the summary pipes end in `head`: a closed pipe upstream is expected, not a failure
set +o pipefail

{
  total=$(wc -l < "$OUT/units.list")
  clean=$(awk -F'\t' '$2==0' "$OUT/units.tsv" | wc -l)
  echo "units: $total  clean: $clean  failing: $((total - clean))"
  echo "errors total: $(awk -F'\t' '{s+=$2} END {print s}' "$OUT/units.tsv")"
  # (line 3: farm reports quote lines 1-2) NATIVE1: units the native renderer replaces left the prepared tree's units.json (render/native/drop_units.txt)
  if [ -f "$DECOMP/prepare_stats.tsv" ]; then
    echo "native units dropped (NATIVE1): $(awk -F'\t' '$1=="NATIVE1" {print $3+0}' "$DECOMP/prepare_stats.tsv" | head -1)"
  fi
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
