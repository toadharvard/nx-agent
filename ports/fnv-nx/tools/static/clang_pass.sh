#!/usr/bin/env bash
# clang_pass.sh - run fnvcheck over every unit of a prepared tree in one clang configuration.
#
#   tools/static/clang_pass.sh <prepared tree> <out dir> <gnu|msvc|atomic> [jobs] [units.list]
#
# gnu:    AST checks + record layouts + clang's flow-sensitive warnings (GCC/Itanium ILP32 proxy of the real build)
# msvc:   record layouts only (MSVC x86-32 layout engine)
# atomic: plain stores to the Interlocked operands listed in $ATOMIC_KEYS (gnu configuration)
# Writes <out>/clang/<cfg>/rows/<unit>.tsv (fnvcheck rows) and .err (clang diagnostics), status.tsv (unit, rc), and
# keeps <out>/clang/<cfg>/seen.keys so a record defined in a shared header is printed once. Results newer than the
# unit are reused (FORCE=1 redoes them).
set -euo pipefail
TREE=$(cd "$1" && pwd); OUT=$(mkdir -p "$2" && cd "$2" && pwd); CFG=$3; JOBS=${4:-1}; LIST=${5:-}
HERE=$(cd "$(dirname "$0")/../.." && pwd)
# shellcheck source=clang_env.sh
. "$HERE/tools/static/clang_env.sh"
clang_env_setup "$OUT" "$HERE"
bash "$HERE/tools/static/build.sh" "$OUT" >/dev/null
D="$OUT/clang/$CFG"; mkdir -p "$D/rows"; touch "$D/seen.keys"
case $CFG in
  gnu)  ARGS=("${CLANG_GNU[@]}" "${CLANG_WARN[@]}"); TOOL=(--layout) ;;
  msvc) ARGS=("${CLANG_MSVC[@]}"); TOOL=(--layout --no-ast) ;;
  atomic) ARGS=("${CLANG_GNU[@]}"); TOOL=(--only-atomic --atomic-keys="${ATOMIC_KEYS:?ATOMIC_KEYS=<keys file>}") ;;
  *) echo "config: gnu, msvc or atomic" >&2; exit 2 ;;
esac
ARGS+=(-resource-dir "$($CLANG -print-resource-dir)" -I"$TREE/include")
TOOL+=("${CLANG_EXCLUDE[@]}")
export TREE D BIN="$OUT/bin/fnvcheck" ARGS_STR="${ARGS[*]}" TOOL_STR="${TOOL[*]}" FORCE=${FORCE:-0}
if [ -z "$LIST" ]; then LIST="$D/units.list"; jq -r '.units[].source' "$TREE/build/units.json" | sort -u > "$LIST"; fi

one() {
  local src=$1 base
  base="$D/rows/$(echo "$src" | tr '/ ' '__')"
  [ -f "$TREE/$src" ] || return 0
  if [ "$FORCE" != 1 ] && [ -f "$base.tsv" ] && [ "$base.tsv" -nt "$TREE/$src" ]; then return 0; fi
  local rc=0
  # shellcheck disable=SC2086
  (cd "$TREE" && timeout 900 nice -n "${NICE:-19}" "$BIN" --root="$TREE" $TOOL_STR --seen="$D/seen.keys" "$src" -- $ARGS_STR) \
    > "$base.tsv.tmp" 2> "$base.err" || rc=$?
  { flock 9; awk -F'\t' '$1=="L"{print $2}' "$base.tsv.tmp" >> "$D/seen.keys"; } 9>"$D/seen.lock"
  echo "$rc" > "$base.rc"; mv "$base.tsv.tmp" "$base.tsv"
}
export -f one
xargs -a "$LIST" -P "$JOBS" -I{} bash -c 'one "$@" || true' _ {}

: > "$D/status.tsv"
while IFS= read -r src; do
  base="$D/rows/$(echo "$src" | tr '/ ' '__')"
  printf '%s\t%s\n' "$src" "$(cat "$base.rc" 2>/dev/null || echo missing)" >> "$D/status.tsv"
done < "$LIST"
echo "clang $CFG: $(awk -F'\t' '$2=="0"' "$D/status.tsv" | wc -l) clean, $(awk -F'\t' '$2=="1"' "$D/status.tsv" | wc -l) with errors, $(wc -l < "$LIST") units"
