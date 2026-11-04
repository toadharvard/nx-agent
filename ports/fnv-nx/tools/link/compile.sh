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
# -U_FORTIFY_SOURCE: Ubuntu's gcc predefines _FORTIFY_SOURCE=3, which turns memcpy/memset/sprintf/strcpy into the
# glibc checking variants __*_chk (114 references in link c0d6c71516f6): they abort where MSVC 2008's CRT wrote past
# an object the exe tolerated, the runtime would have to provide them, and the fuzz harness saw unbound names
# (S1-fuzzB). Unlike the flags above it does change what a program does, towards the exe's behaviour.
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
CC=${CC:-aarch64-linux-gnu-gcc}
SSE2NEON=${SSE2NEON:-$HERE/ext/sse2neon}
OPT=${OPT:--O2}
FLAGS=(-mabi=ilp32 -std=gnu++17 -c $OPT -fms-extensions -fshort-wchar -frounding-math -fsigned-char -fno-strict-aliasing -fwrapv -fno-delete-null-pointer-checks -ffp-contract=off -fPIC -fpermissive -Werror=return-type -Wno-narrowing -fmax-errors=20
       -march=armv8-a+crc+crypto -mtune=cortex-a57 -fno-stack-protector -fno-stack-clash-protection -mno-outline-atomics -U_FORTIFY_SOURCE
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
# ORDER_OBJECT / ORDER_OPERATOR / ORDER_ASSIGN: evaluation order as MSVC 2008 x86, per shape, from S11's recorded
# MSVC runs (tests/s11/order/order.msvc, all five configurations; docs/ORDERING.md "Evaluation order"):
#   ORDER_OBJECT=msvc    a direct member call (member operators too) evaluates its arguments, then the object; a
#                        virtual call the object first; a call through a pointer to member the arguments first
#   ORDER_OPERATOR=rtl   an overloaded <<, >>, [], ->* is a plain call: operands right to left (C++17: left to right)
#   ORDER_ASSIGN=msvc    a built-in compound assignment evaluates its left operand first (C++17: the right one)
# first / cpp17 / cpp17 are GCC's own order, for A/B only.
ORDER_OBJECT=${ORDER_OBJECT:-msvc}
ORDER_OPERATOR=${ORDER_OPERATOR:-rtl}
ORDER_ASSIGN=${ORDER_ASSIGN:-msvc}
FLAGS+=(-fplugin="$ORDER_SO" -fplugin-arg-fnvnx_order-mode="$ORDER_MODE" -fplugin-arg-fnvnx_order-args="$ORDER_ARGS"
        -fplugin-arg-fnvnx_order-refcount="$ORDER_REFCOUNT" -fplugin-arg-fnvnx_order-object="$ORDER_OBJECT"
        -fplugin-arg-fnvnx_order-operator="$ORDER_OPERATOR" -fplugin-arg-fnvnx_order-assign="$ORDER_ASSIGN")
if [ "$ORDER_MODE" = off ]; then FLAGS+=(-DFNVNX_COMPILER_BARRIERS=1); fi
# ORDER_KEYS: the whole program's key list (docs/ORDERING.md "Whole-program keys"): every field and global that some
# unit hands to Interlocked* (directly or through a forwarder) or polls in a wait-only loop. Their plain accesses are
# ordered in every unit, not only in the unit that calls Interlocked on them (ATOMIC-PLAIN-STORE, the writer side of
# POLL-PLAIN). Default: <obj dir>/order.keys, which this script keeps current itself from a syntax-only discovery
# pass over ALL units of units.json (not only [units.list]; cached per unit in <obj dir>/.keys/), so a shard or a
# one-unit compile sees the same list as the full build. The farm computes it once per tree (tools/farm/keys_job.sh)
# and hands it in as ORDER_KEYS=<file>. ORDER_KEYS=unit: in-unit discovery only, for A/B runs and quick local checks,
# never for a build that runs. KEYS_ONLY=1: write the key list and stop.
# --print-flags on a tree without units.json (hand-written test units): the unit is the whole program
if [ "$PRINT_FLAGS" = 1 ] && [ -z "${ORDER_KEYS:-}" ] && [ ! -f "$TREE/build/units.json" ]; then ORDER_KEYS=unit; fi
ORDER_KEYS=${ORDER_KEYS:-$OUT/order.keys}
KEYFLAGS=("${FLAGS[@]}")
if [ "$ORDER_KEYS" != unit ]; then FLAGS+=(-fplugin-arg-fnvnx_order-keys="$ORDER_KEYS"); fi
export CXX TREE OUT FLAGS_STR="${FLAGS[*]}" ORDER_KEYS
if [ -z "$LIST" ] && [ "$PRINT_FLAGS" = 0 ]; then LIST="$OUT/units.list"; jq -r '.units[].source' "$TREE/build/units.json" | sort -u > "$LIST"; fi

# ---- whole-program keys: discover=<unit>.keys under -fsyntax-only (no code generation), merged and sorted
keys_key() { printf '%s' "$1" | sha256sum | cut -c1-24; }   # src/a_b.cpp and src_a/b.cpp must not share a file
keys_one() {
  local src=$1 k dep stale=0 h
  k="$OUT/.keys/$(keys_key "$1").keys"
  h=$(sha256sum < "$TREE/$src" | cut -c1-16)
  # up to date only when the unit's content is the same (a copy with an older mtime is still a change) and no header
  # it includes (its .d from the last discovery) is newer
  if [ -f "$k" ] && [ -f "$k.d" ] && [ "$(cat "$k.src" 2>/dev/null)" = "$h" ]; then
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
  echo "$h" > "$k.src"
}
if [ "$ORDER_KEYS" = "$OUT/order.keys" ]; then
  KEYFLAGS_STR=$(printf '%s\n' "${KEYFLAGS[@]}" | grep -vxF -- -c | tr '\n' ' ')
  # per-unit results are kept while the plugin, the shim headers and the flags stay the same
  stamp=$( (echo "$KEYFLAGS_STR"; cat "$ORDER_SO"; find "$HERE/shim/include" -type f -print0 | LC_ALL=C sort -z | xargs -0 cat) |
          sha256sum | cut -c1-16)
  if [ "$(cat "$OUT/.keys/.stamp" 2>/dev/null)" != "$stamp" ]; then rm -rf "$OUT/.keys"; fi
  mkdir -p "$OUT/.keys"; echo "$stamp" > "$OUT/.keys/.stamp"
  export KEYFLAGS_STR; export -f keys_one keys_key
  jq -r '.units[].source' "$TREE/build/units.json" | sort -u > "$OUT/.keys/units.list"
  xargs -a "$OUT/.keys/units.list" -P "$JOBS" -I{} bash -c 'keys_one "$@"' _ {}
  # forwarders defined in one unit and called in another are resolved over all units (keys_merge.py)
  while read -r src; do echo "$OUT/.keys/$(keys_key "$src").keys"; done < "$OUT/.keys/units.list" > "$OUT/.keys/files.list"
  python3 "$HERE/toolchain/order/keys_merge.py" --list "$OUT/.keys/files.list" > "$OUT/order.keys.tmp"
  # replaced only when it changes: its mtime decides which objects are stale (compile_one)
  if cmp -s "$OUT/order.keys.tmp" "$OUT/order.keys"; then rm "$OUT/order.keys.tmp"; else mv "$OUT/order.keys.tmp" "$OUT/order.keys"; fi
  echo "order keys: $(wc -l < "$OUT/order.keys") fields and globals ($OUT/order.keys)"
elif [ "$ORDER_KEYS" != unit ] && [ ! -f "$ORDER_KEYS" ]; then
  echo "compile.sh: ORDER_KEYS=$ORDER_KEYS does not exist (unset it to compute the list, or use tools/farm/keys_job.sh)" >&2
  exit 2
fi
if [ "${KEYS_ONLY:-0}" = 1 ]; then exit 0; fi
# --print-flags: after the keys, so the flags name the same key list a compile with these settings would use
if [ "$PRINT_FLAGS" = 1 ]; then printf '%s\n' "${FLAGS[@]}"; exit 0; fi

# C units (third_party/zlib-1.2.1/*.c; MSVC compiled them as C: K&R definitions, C names such as `_adler32`) build
# with the C compiler (tools/census.sh does the same): the same flags with C's -std, without the C++-only ones and
# without the plugins (fnvnx_order is built against the C++ front end). The plugins have nothing to do there: the
# zlib sources have no volatile access, no Interlocked call and no floating-point type, and their portable C does not
# depend on argument evaluation order. Every unit gets its units.json /D defines (only zlib has any).
CFLAGS=()
for f in "${FLAGS[@]}"; do
  case $f in -std=gnu++17) CFLAGS+=(-std=gnu99) ;; -fpermissive|-Wno-narrowing|-fplugin=*|-fplugin-arg-*) ;; *) CFLAGS+=("$f") ;; esac
done
export CC CFLAGS_STR="${CFLAGS[*]}"
jq -r '.units[] | [.source, ([.flags[]? | select(startswith("/D")) | "-D" + .[2:]] | join(" "))] | @tsv' \
  "$TREE/build/units.json" | awk -F'\t' '$2 != ""' > "$OUT/defines.tsv"
# Objects are kept only while the flags (the order options included), the plugins and the shim headers stay the
# same: a change of any of them rebuilds every object of <obj dir> (V1's C4: an ORDER_OBJECT change kept old objects).
objstamp=$( (echo "$FLAGS_STR"; cat "$ORDER_SO" "$FTOI_SO"; find "$HERE/shim/include" -type f -print0 | LC_ALL=C sort -z |
            xargs -0 cat) | sha256sum | cut -c1-16)
if [ "$(cat "$OUT/.objstamp" 2>/dev/null)" != "$objstamp" ]; then rm -f "$OUT"/*.o; echo "$objstamp" > "$OUT/.objstamp"; fi

compile_one() {
  local src=$1 base
  base="$OUT/$(echo "$1" | tr / _)"
  if [ -f "$base.o" ] && [ "$base.o" -nt "$TREE/$src" ] && { [ "$ORDER_KEYS" = unit ] || [ "$base.o" -nt "$ORDER_KEYS" ]; }; then
    return
  fi
  # shellcheck disable=SC2086
  local cc=$CXX flags=$FLAGS_STR defs
  case $src in *.c) cc=$CC; flags=$CFLAGS_STR ;; esac
  defs=$(awk -F'\t' -v s="$src" '$1 == s {print $2; exit}' "$OUT/defines.tsv")
  # shellcheck disable=SC2086
  if (cd "$TREE" && timeout 900 $cc $flags $defs "$src" -o "$base.o.tmp") > "$base.log" 2>&1; then
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
