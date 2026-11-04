#!/usr/bin/env bash
# gcc_warn.sh - compile every unit of a prepared tree with the real AArch64 ILP32 flags at -O2 (object discarded) and
# the target-semantics warning set of flags.sh; one log per unit.
#
#   tools/static/gcc_warn.sh <prepared tree> <out dir> [jobs] [units.list]
#
# SHIM=<dir> replaces shim/include (run.sh); NICE (default 19) is the niceness of each compiler.
# Writes <out>/gcc/logs/<unit>.log and <out>/gcc/status.tsv (unit, rc). Logs are reused when newer than the unit
# (FORCE=1 recompiles). -O2 is needed: -Wmaybe-uninitialized, -Waggressive-loop-optimizations, -Warray-bounds,
# -Wdangling-pointer and "control reaches end of non-void function" come from the middle end.
set -euo pipefail
TREE=$(cd "$1" && pwd); OUT=$(mkdir -p "$2/gcc/logs" && cd "$2" && pwd); JOBS=${3:-1}; LIST=${4:-}
HERE=$(cd "$(dirname "$0")/../.." && pwd)
# shellcheck source=flags.sh
. "$HERE/tools/static/flags.sh"
CXX=${CXX:-aarch64-linux-gnu-g++}
SSE2NEON=${SSE2NEON:-$HERE/ext/sse2neon}
OPT=${OPT:--O2}
SHIM=${SHIM:-$HERE/shim/include}
# the build's flags (compile.sh, minus its -fmax-errors), then the warning set; the build's own -Werror=return-type
# stays an error, so a unit the build cannot compile fails here too
FLAGS=("${BUILD_FLAGS[@]}" -c "$OPT" -o /dev/null "${GCC_WARN[@]}" "${GCC_DIAG[@]}"
       -include "$SHIM/fnvnx_compat.h" -I"$SHIM" -I"$SSE2NEON" -I"$TREE/include")
export CXX TREE OUT FLAGS_STR="${FLAGS[*]}" FORCE=${FORCE:-0} NICE=${NICE:-19}
if [ -z "$LIST" ]; then LIST="$OUT/gcc/units.list"; jq -r '.units[].source' "$TREE/build/units.json" | sort -u > "$LIST"; fi

one() {
  local src=$1 log
  log="$OUT/gcc/logs/$(echo "$src" | tr '/ ' '__').log"
  [ -f "$TREE/$src" ] || { echo "missing" > "$log"; return; }
  if [ "$FORCE" != 1 ] && [ -f "$log" ] && [ "$log" -nt "$TREE/$src" ]; then return; fi
  # shellcheck disable=SC2086
  (cd "$TREE" && timeout 900 nice -n "$NICE" $CXX $FLAGS_STR "$src") > "$log.tmp" 2>&1
  echo "rc=$?" >> "$log.tmp"; mv "$log.tmp" "$log"
}
export -f one
xargs -a "$LIST" -P "$JOBS" -I{} bash -c 'one "$@" || true' _ {}

: > "$OUT/gcc/status.tsv"
while IFS= read -r src; do
  log="$OUT/gcc/logs/$(echo "$src" | tr '/ ' '__').log"
  printf '%s\t%s\n' "$src" "$(tail -1 "$log" 2>/dev/null | sed 's/^rc=//')" >> "$OUT/gcc/status.tsv"
done < "$LIST"
echo "gcc: $(awk -F'\t' '$2=="0"' "$OUT/gcc/status.tsv" | wc -l) / $(wc -l < "$LIST") units compiled"
