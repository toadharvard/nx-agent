#!/usr/bin/env bash
# compile.sh - compile prepared units to AArch64 ILP32 ELF objects for the link step.
#
#   tools/link/compile.sh <prepared tree> <obj dir> [units.list] [jobs]
#
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
# ORDER_OBJECT=first|last (ORDER-OBJECT: a member call's object before or after its arguments) and
# ORDER_OPERATOR=cpp17|rtl (ORDER-OPERATOR: overloaded <<, >>, [], ->* left to right as C++17, or like any MSVC call);
# docs/ORDERING.md, tests/s12/order/evalorder.cpp. ORDER_OBJECT=last is the build: MSVC's thiscall pushes the arguments
# and loads ecx last, and the decomp (byte-matched against MSVC) has calls that work only that way. ORDER_OPERATOR=rtl:
# MSVC 2008 predates C++17 and compiles an overloaded operator as a plain call (arguments pushed right to left).
# S11's MSVC run of evalorder.cpp confirms both; first / cpp17 are GCC's own order, for A/B.
ORDER_OBJECT=${ORDER_OBJECT:-last}
ORDER_OPERATOR=${ORDER_OPERATOR:-rtl}
FLAGS+=(-fplugin="$ORDER_SO" -fplugin-arg-fnvnx_order-mode="$ORDER_MODE" -fplugin-arg-fnvnx_order-args="$ORDER_ARGS"
        -fplugin-arg-fnvnx_order-refcount="$ORDER_REFCOUNT" -fplugin-arg-fnvnx_order-object="$ORDER_OBJECT"
        -fplugin-arg-fnvnx_order-operator="$ORDER_OPERATOR")
if [ "$ORDER_MODE" = off ]; then FLAGS+=(-DFNVNX_COMPILER_BARRIERS=1); fi
# ORDER_KEYS: the whole program's key list (docs/ORDERING.md "Whole-program keys"): every field and global that some
# unit hands to Interlocked* (directly or through a forwarder) or polls in a wait-only loop. Their plain accesses are
# ordered in every unit, not only in the unit that calls Interlocked on them (ATOMIC-PLAIN-STORE, the writer side of
# POLL-PLAIN). Default: <obj dir>/order.keys, which this script keeps current itself from a syntax-only discovery
# pass over ALL units of units.json (not only [units.list]; cached per unit in <obj dir>/.keys/), so a shard or a
# one-unit compile sees the same list as the full build. The farm computes it once per tree (tools/farm/keys_job.sh)
# and hands it in as ORDER_KEYS=<file>. ORDER_KEYS=unit: in-unit discovery only, for A/B runs and quick local checks,
# never for a build that runs. KEYS_ONLY=1: write the key list and stop.
ORDER_KEYS=${ORDER_KEYS:-$OUT/order.keys}
KEYFLAGS=("${FLAGS[@]}")
if [ "$ORDER_KEYS" != unit ]; then FLAGS+=(-fplugin-arg-fnvnx_order-keys="$ORDER_KEYS"); fi
export CXX TREE OUT FLAGS_STR="${FLAGS[*]}" ORDER_KEYS
if [ -z "$LIST" ]; then LIST="$OUT/units.list"; jq -r '.units[].source' "$TREE/build/units.json" | sort -u > "$LIST"; fi

# ---- whole-program keys: discover=<unit>.keys under -fsyntax-only (no code generation), merged and sorted
keys_one() {
  local src=$1 k dep stale=0
  k="$OUT/.keys/$(echo "$1" | tr / _).keys"
  # up to date only when newer than the unit and than every header it includes (its .d from the last discovery)
  if [ -f "$k" ] && [ -f "$k.d" ] && [ "$k" -nt "$TREE/$src" ]; then
    for dep in $(sed -e 's/\\$//' -e 's/^[^:]*://' "$k.d"); do
      case $dep in /*) ;; *) dep=$TREE/$dep ;; esac
      if [ ! -e "$dep" ] || [ "$dep" -nt "$k" ]; then stale=1; break; fi
    done
    [ $stale = 0 ] && return
  fi
  # shellcheck disable=SC2086
  (cd "$TREE" && timeout 900 $CXX $KEYFLAGS_STR -fsyntax-only -MMD -MF "$k.d.tmp" -MT x \
     -fplugin-arg-fnvnx_order-discover="$k.tmp" "$src") > /dev/null 2>&1 || true   # a unit that does not compile still contributes what it parsed
  if [ -f "$k.tmp" ]; then mv "$k.tmp" "$k"; else : > "$k"; fi
  if [ -f "$k.d.tmp" ]; then mv "$k.d.tmp" "$k.d"; else echo "x: $src" > "$k.d"; fi
}
if [ "$ORDER_KEYS" = "$OUT/order.keys" ]; then
  KEYFLAGS_STR=$(printf '%s\n' "${KEYFLAGS[@]}" | grep -vxF -- -c | tr '\n' ' ')
  # per-unit results are kept while the plugin, the shim headers and the flags stay the same
  stamp=$( (echo "$KEYFLAGS_STR"; cat "$ORDER_SO"; find "$HERE/shim/include" -type f -print0 | LC_ALL=C sort -z | xargs -0 cat) |
          sha256sum | cut -c1-16)
  if [ "$(cat "$OUT/.keys/.stamp" 2>/dev/null)" != "$stamp" ]; then rm -rf "$OUT/.keys"; fi
  mkdir -p "$OUT/.keys"; echo "$stamp" > "$OUT/.keys/.stamp"
  export KEYFLAGS_STR; export -f keys_one
  jq -r '.units[].source' "$TREE/build/units.json" | sort -u > "$OUT/.keys/units.list"
  xargs -a "$OUT/.keys/units.list" -P "$JOBS" -I{} bash -c 'keys_one "$@"' _ {}
  # forwarders defined in one unit and called in another are resolved over all units (keys_merge.py)
  while read -r src; do echo "$OUT/.keys/$(echo "$src" | tr / _).keys"; done < "$OUT/.keys/units.list" > "$OUT/.keys/files.list"
  python3 "$HERE/toolchain/order/keys_merge.py" --list "$OUT/.keys/files.list" > "$OUT/order.keys.tmp"
  # replaced only when it changes: its mtime decides which objects are stale (compile_one)
  if cmp -s "$OUT/order.keys.tmp" "$OUT/order.keys"; then rm "$OUT/order.keys.tmp"; else mv "$OUT/order.keys.tmp" "$OUT/order.keys"; fi
  echo "order keys: $(wc -l < "$OUT/order.keys") fields and globals ($OUT/order.keys)"
elif [ "$ORDER_KEYS" != unit ] && [ ! -f "$ORDER_KEYS" ]; then
  echo "compile.sh: ORDER_KEYS=$ORDER_KEYS does not exist (unset it to compute the list, or use tools/farm/keys_job.sh)" >&2
  exit 2
fi
if [ "${KEYS_ONLY:-0}" = 1 ]; then exit 0; fi

compile_one() {
  local src=$1 base
  base="$OUT/$(echo "$1" | tr / _)"
  if [ -f "$base.o" ] && [ "$base.o" -nt "$TREE/$src" ] && { [ "$ORDER_KEYS" = unit ] || [ "$base.o" -nt "$ORDER_KEYS" ]; }; then
    return
  fi
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
