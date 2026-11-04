#!/usr/bin/env bash
# compile.sh - compile prepared units to AArch64 ILP32 ELF objects for the link step.
#
#   tools/link/compile.sh <prepared tree> <obj dir> [units.list] [jobs]
#   tools/link/compile.sh --print-flags <prepared tree> <work dir>   # the flags, one per line, plugins built in
#                                                                     # <work dir>; compiles nothing
#
# --print-flags is the one source of the game's compile flags: fnvlink's emitted code (vtables.py slot functions,
# bodies.py synthesized bodies) compiles with exactly these (layout.game_flags), so no linked object misses a
# semantics, code-generation or plugin flag (FTOI2, S12 order).
# Same flag set as tools/census.sh, with -c instead of -fsyntax-only (plus $OPT, default -O2). One object per unit:
# <obj dir>/<source with / -> _>.o, its compiler log next to it as .log. Up-to-date objects are kept, so the step
# grows incrementally as more units compile. Writes <obj dir>/objects.tsv: source, object, status (ok/fail).
# -fPIC: a variable a unit defines is addressed through the GOT by its own symbol (as extern ones already are), never
# through a section anchor; the link rebinds it to its image address (an absolute symbol), and an anchor-relative or
# PC-relative access would miss it or tie the guest to its link address (docs/LINK.md, "Split globals"). Not
# -fno-semantic-interposition: it calls a unit's own functions through local aliases, past the by-address binding.
# -march/-mtune as the guest build (toolchain/game/build.sh). No hardening defaults of the distro compiler: Ubuntu's
# gcc enables -fstack-protector-strong and -fstack-clash-protection, and outline atomics call libgcc helpers
# (S15, docs/PERF.md: -4.7 % instructions and -32 % GOT loads in hot units). None of them is a semantics flag.
# -ffile-prefix-map: __FILE__ and other recorded paths do not depend on where the tree lives, so objects built on
# different machines from the same tree are byte-identical (tools/farm/).
# Objects are derived from the decomp: local output only, never committed.
set -euo pipefail
PRINT_FLAGS=0
if [ "${1:-}" = --print-flags ]; then PRINT_FLAGS=1; shift; fi
TREE=$(cd "$1" && pwd); OUT=$(mkdir -p "$2" && cd "$2" && pwd)
LIST=${3:-}; JOBS=${4:-$(nproc)}
HERE=$(cd "$(dirname "$0")/../.." && pwd)
CXX=${CXX:-aarch64-linux-gnu-g++}
SSE2NEON=${SSE2NEON:-$HERE/ext/sse2neon}
OPT=${OPT:--O2}
FLAGS=(-mabi=ilp32 -std=gnu++17 -c $OPT -fms-extensions -fshort-wchar -frounding-math -fsigned-char -fno-strict-aliasing -fwrapv -fno-delete-null-pointer-checks -ffp-contract=off -fPIC -fpermissive -Werror=return-type -Wno-narrowing -fmax-errors=20
       -march=armv8-a+crc+crypto -mtune=cortex-a57 -fno-stack-protector -fno-stack-clash-protection -mno-outline-atomics
       -ffile-prefix-map="$TREE"/= -ffile-prefix-map="$HERE"/=fnv-nx/ -ffile-prefix-map="$SSE2NEON"/=sse2neon/
       -include "$HERE/shim/include/fnvnx_compat.h" -I"$HERE/shim/include" -I"$SSE2NEON" -I"$TREE/include")
# FTOI2 (S12, docs/STATIC_CHECKS.md): the fnvnx_ftoi plugin rewrites every float -> integer conversion into MSVC 2008's
# computation for its target type (__ftol2_sse / fistp / __ftol2: 0x80000000 or INT64_MIN for NaN and out of range,
# where AArch64's fcvtzs saturates). No silent fallback: objects with and without it must never mix.
FTOI_SO=$(CXX=$CXX bash "$HERE/toolchain/ftoi/build.sh" "$OUT/.ftoi") \
  || { echo "compile.sh: the fnvnx_ftoi plugin does not build (install gcc-13-plugin-dev-aarch64-linux-gnu and g++)" >&2; exit 2; }
FLAGS+=(-fplugin="$FTOI_SO")
# S12-ordering (docs/ORDERING.md): the fnvnx_order plugin gives volatile accesses MSVC's /volatile:ms meaning on the
# A57 (acquire loads ldar, release stores stlr; fences where ldar/stlr cannot reach). Same rule: no fallback.
# ORDER_MODE=off (with the compiler-only barriers of fnvnx_compat.h) or tso only for A/B measurements, never for a
# build that runs: ORDER_MODE=volatile is the program's semantics.
ORDER_MODE=${ORDER_MODE:-volatile}
ORDER_SO=$(CXX=$CXX bash "$HERE/toolchain/order/build.sh" "$OUT/.order") \
  || { echo "compile.sh: the fnvnx_order plugin does not build (install gcc-13-plugin-dev-aarch64-linux-gnu and g++)" >&2; exit 2; }
# ORDER_ARGS=rtl: call arguments evaluated right to left like MSVC x86 (ORDER-ARGS); ltr only for A/B measurements.
ORDER_ARGS=${ORDER_ARGS:-rtl}
# ORDER_REFCOUNT=weak: refcount sites of InterlockedIncrement/Decrement relaxed / release (docs/ORDERING.md
# "Refcounts"); full keeps x86 lock semantics everywhere (A/B, or a suspected refcount ordering bug).
ORDER_REFCOUNT=${ORDER_REFCOUNT:-weak}
FLAGS+=(-fplugin="$ORDER_SO" -fplugin-arg-fnvnx_order-mode="$ORDER_MODE" -fplugin-arg-fnvnx_order-args="$ORDER_ARGS"
        -fplugin-arg-fnvnx_order-refcount="$ORDER_REFCOUNT")
if [ "$ORDER_MODE" = off ]; then FLAGS+=(-DFNVNX_COMPILER_BARRIERS=1); fi
# ORDER_KEYS=<file>: the static gate's clang/atomic.keys (fields and variables some unit hands to Interlocked*); their
# plain accesses are ordered in every unit, not only in the units that call Interlocked on them themselves.
if [ -n "${ORDER_KEYS:-}" ]; then FLAGS+=(-fplugin-arg-fnvnx_order-keys="$ORDER_KEYS"); fi
if [ "$PRINT_FLAGS" = 1 ]; then printf '%s\n' "${FLAGS[@]}"; exit 0; fi
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
