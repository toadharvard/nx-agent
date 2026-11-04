#!/usr/bin/env bash
# compile.sh - compile prepared units to AArch64 ILP32 ELF objects for the link step.
#
#   tools/link/compile.sh <prepared tree> <obj dir> [units.list] [jobs]
#
# Same flag set as tools/census.sh, with -c instead of -fsyntax-only (plus $OPT, default -O2). One object per unit:
# <obj dir>/<source with / -> _>.o, its compiler log next to it as .log. Up-to-date objects are kept, so the step
# grows incrementally as more units compile. Writes <obj dir>/objects.tsv: source, object, status (ok/fail).
# -fPIC -fno-semantic-interposition: a variable a unit defines is addressed through the GOT by its own symbol (as
# extern ones already are), never through a section anchor; the link rebinds it to its image address (an absolute
# symbol), and an anchor-relative or PC-relative access would miss it or tie the guest to its link address
# (docs/LINK.md, "Split globals"). Functions still inline (no semantic interposition).
# Objects are derived from the decomp: local output only, never committed.
set -euo pipefail
TREE=$(cd "$1" && pwd); OUT=$(mkdir -p "$2" && cd "$2" && pwd)
LIST=${3:-}; JOBS=${4:-$(nproc)}
HERE=$(cd "$(dirname "$0")/../.." && pwd)
CXX=${CXX:-aarch64-linux-gnu-g++}
SSE2NEON=${SSE2NEON:-$HERE/ext/sse2neon}
OPT=${OPT:--O2}
FLAGS=(-mabi=ilp32 -std=gnu++17 -c $OPT -fms-extensions -fshort-wchar -frounding-math -fsigned-char -fno-strict-aliasing -fwrapv -fno-delete-null-pointer-checks -ffp-contract=off -fPIC -fno-semantic-interposition -fpermissive -Werror=return-type -Wno-narrowing -fmax-errors=20
       -include "$HERE/shim/include/fnvnx_compat.h" -I"$HERE/shim/include" -I"$SSE2NEON" -I"$TREE/include")
export CXX TREE OUT FLAGS_STR="${FLAGS[*]}"
if [ -z "$LIST" ]; then LIST="$OUT/units.list"; jq -r '.units[].source' "$TREE/build/units.json" | sort -u > "$LIST"; fi

compile_one() {
  local src=$1 base
  base="$OUT/$(echo "$1" | tr / _)"
  if [ -f "$base.o" ] && [ "$base.o" -nt "$TREE/$src" ]; then return; fi
  # shellcheck disable=SC2086
  if (cd "$TREE" && timeout 900 $CXX $FLAGS_STR "$src" -o "$base.o.tmp") > "$base.log" 2>&1; then
    mv "$base.o.tmp" "$base.o"
  else
    rm -f "$base.o.tmp" "$base.o"
  fi
}
export -f compile_one
xargs -a "$LIST" -P "$JOBS" -I{} bash -c 'compile_one "$@"' _ {}

: > "$OUT/objects.tsv"
while read -r src; do
  base="$OUT/$(echo "$src" | tr / _)"
  if [ -f "$base.o" ]; then st=ok; else st=fail; fi
  printf '%s\t%s\t%s\n' "$src" "$base.o" "$st" >> "$OUT/objects.tsv"
done < "$LIST"
echo "objects: $(grep -c $'\tok$' "$OUT/objects.tsv" || true) ok, $(grep -c $'\tfail$' "$OUT/objects.tsv" || true) failed"
