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
set -Eeuo pipefail
# a failure under set -e always names its line and command, in functions and subshells too (-E) (full1: an exit 1
# with no message cost a build)
trap 'rc_=$?; echo "compile.sh: line $LINENO: exit $rc_: $BASH_COMMAND" >&2' ERR
PRINT_FLAGS=0
if [ "${1:-}" = --print-flags ]; then PRINT_FLAGS=1; shift; fi
TREE=$(cd "$1" && pwd); OUT=$(mkdir -p "$2" && cd "$2" && pwd)
LIST=${3:-}; JOBS=${4:-$(nproc)}
HERE=$(cd "$(dirname "$0")/../.." && pwd)
CXX=${CXX:-aarch64-linux-gnu-g++}
CC=${CC:-aarch64-linux-gnu-gcc}
SSE2NEON=${SSE2NEON:-$HERE/ext/sse2neon}
OPT=${OPT:--O2}
# -fcheck-new (S1-fuzzA): MSVC 2008 tests the result of every new-expression for null before it constructs, the
# standard placement form `new (p) T(x)` and a throwing `new T` included (x86: `test eax, eax; je`); GCC drops the
# test for both, so an append into an hkArray whose m_data is still null constructed at address 0. A semantics flag.
# -fno-sized-deallocation (W41): MSVC 2008 never emits a sized delete (the exe's delete sites call 0x00401030, the unsized
# one); GCC under gnu++17 would bind every delete-expression to the sized `operator delete(void*, size_t)` (0x00aa1460).
# -fno-lifetime-dse (EQ 588863, 00d979f0): decomp code calls a destructor explicitly and reuses the object (ResetFields'
# `this->SpawnedBase::~SpawnedBase()`); MSVC never elides the destructor's vptr store, GCC drops it as dead.
FLAGS=(-mabi=ilp32 -std=gnu++17 -c $OPT -fms-extensions -fshort-wchar -frounding-math -fsigned-char -fno-strict-aliasing -fwrapv -fno-delete-null-pointer-checks -fcheck-new -fno-sized-deallocation -fno-lifetime-dse -ffp-contract=off -fPIC -fpermissive -Werror=return-type -Wno-narrowing -fmax-errors=20
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
# AB_FLAGS: extra flags appended after all of the above, so they win (e.g. -fstrict-aliasing against the parity set).
# For A/B measurements only (tools/perf/unit_ab.sh, docs/PERF.md 11.8), never for a build that runs; unset = no
# change.
if [ -n "${AB_FLAGS:-}" ]; then read -r -a _ab <<< "$AB_FLAGS"; FLAGS+=("${_ab[@]}"); fi
# RACE=1 (D25 #7, tools/threads/README.md): the race-checker build. -fsanitize=thread only inserts __tsan_* calls
# (hb.c is their runtime, linked by tools/pipeline/guest_link.py RACE=1); the code is otherwise the same build. Its
# own object directory: objects with and without it never mix (the flag is in the cache key like every flag).
if [ "${RACE:-0}" = 1 ]; then FLAGS+=(-fsanitize=thread -Wno-tsan); fi
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
  # stderr under --print-flags: stdout is the flag list there (layout.game_flags parses it)
  if [ "$PRINT_FLAGS" = 1 ]; then echo "order keys: $(wc -l < "$OUT/order.keys") fields and globals ($OUT/order.keys)" >&2
  else echo "order keys: $(wc -l < "$OUT/order.keys") fields and globals ($OUT/order.keys)"; fi
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
# One compile.sh at a time per object directory: a second run with other flags would delete the first one's objects
# here (the stamp above) while it works (try1of1, D26: 2,499 units "failed (no error line)", 1,716 logs of 0 bytes,
# i.e. compiles that had succeeded and then lost their object). COMPILE_LOCK_WAIT seconds (default 7200) to wait.
exec 7>"$OUT/.compile.lock"
flock -w "${COMPILE_LOCK_WAIT:-7200}" 7 || { echo "compile.sh: $OUT is locked by another compile.sh" >&2; exit 2; }
if [ "$(cat "$OUT/.objstamp" 2>/dev/null)" != "$objstamp" ]; then
  # Every object here is about to go, so every unit that had one is compiled again, not only the given list: the
  # pipeline passes only the units it thinks stale, and its key does not see the plugins' bytes or the shim headers
  # (try1of1, D26: it asked for 493, this wiped the other 2,485 objects and left them unbuilt: "failed (no error line)").
  if [ -n "$LIST" ] && [ "$LIST" != "$OUT/units.list" ]; then
    jq -r '.units[].source' "$TREE/build/units.json" | sort -u | while read -r s_; do
      # if/fi, never `[ ] && echo`: under pipefail a last unit without an object ends the loop with status 1, and
      # set -e then stops compile.sh with no message (full1 on c46cd30231d9: "exit 1" right after the order keys)
      if [ -f "$OUT/$(echo "$s_" | tr / _).o" ]; then echo "$s_"; fi
    done > "$OUT/.wiped.list"
    sort -u "$LIST" "$OUT/.wiped.list" > "$OUT/.rebuild.list"
    echo "compile.sh: flags, plugins or shim headers changed: every object of $OUT is rebuilt ($(wc -l < "$OUT/.wiped.list") had one, $(wc -l < "$LIST") were asked for)" >&2
    LIST=$OUT/.rebuild.list
  else
    echo "compile.sh: flags, plugins or shim headers changed: every object of $OUT is rebuilt" >&2
  fi
  rm -f "$OUT"/*.o "$OUT"/*.ok; echo "$objstamp" > "$OUT/.objstamp"
fi

compile_one() {
  local src=$1 base
  base="$OUT/$(echo "$1" | tr / _)"
  if [ -f "$base.o" ] && [ "$base.o" -nt "$TREE/$src" ] && { [ "$ORDER_KEYS" = unit ] || [ "$base.o" -nt "$ORDER_KEYS" ]; }; then
    return
  fi
  # shellcheck disable=SC2086
  local cc=$CXX flags=$FLAGS_STR defs
  case $src in *.c) cc=$CC; flags=$CFLAGS_STR ;; esac
  # T1: FNVNX_UNIT_OBJCACHE=<dir>: through the content-addressed cache (tools/link/objcache.py cc), shared by the
  # farms (tools/farm/linkcache.sh, branch unitobj/cache): a unit with the same compiler, command line, source and
  # headers comes back as the object the first farm compiled; the paths are part of the key, so it is byte-exact
  [ -n "${FNVNX_UNIT_OBJCACHE:-}" ] && cc="python3 -I $OBJCACHE_PY cc $cc"
  defs=$(awk -F'\t' -v s="$src" '$1 == s {print $2; exit}' "$OUT/defines.tsv")
  # shellcheck disable=SC2086
  local rc=0
  rm -f "$base.ok"
  (cd "$TREE" && timeout 900 $cc $flags $defs "$src" -o "$base.o.tmp") > "$base.log" 2>&1 || rc=$?
  if [ $rc = 0 ] && [ -s "$base.o.tmp" ]; then
    mv "$base.o.tmp" "$base.o" && : > "$base.ok"      # .ok: this run compiled it (the end check below)
  else
    rm -f "$base.o.tmp" "$base.o"
    # never a failure without a reason: what the compiler said, else how it ended
    case $rc in
      0)   why="exit 0 but no object (an empty or missing $base.o.tmp)" ;;
      124) why="timeout: killed after 900 s" ;;
      12[5-7]) why="timeout/exec failed (exit $rc): the compiler could not run" ;;
      *)   if [ $rc -gt 128 ]; then why="killed by signal $((rc - 128)) (exit $rc)"; else why="exit $rc"; fi ;;
    esac
    echo "compile.sh: $src: $why" >> "$base.log"
  fi
  return 0      # xargs stops at a child's exit 255 or a signal: one unit must never end the others' compiles
}
export -f compile_one
export OBJCACHE_PY=$HERE/tools/link/objcache.py FNVNX_UNIT_OBJCACHE=${FNVNX_UNIT_OBJCACHE:-}
xargs -a "$LIST" -P "$JOBS" -I{} bash -c 'compile_one "$@"' _ {}

: > "$OUT/objects.tsv"
while read -r src; do
  base="$OUT/$(echo "$src" | tr / _)"
  if [ -f "$base.o" ]; then st=ok; else st=fail
    # a failed unit always says why (compile_one writes it; these are the cases it cannot see)
    if [ -f "$base.ok" ]; then echo "compile.sh: $src: compiled in this run, then its object disappeared" >> "$base.log"
    elif [ ! -f "$base.log" ]; then echo "compile.sh: $src: never compiled (the run ended before it)" > "$base.log"
    elif ! grep -q . "$base.log"; then echo "compile.sh: $src: no object and an empty log" >> "$base.log"; fi
  fi
  printf '%s\t%s\t%s\n' "$src" "$base.o" "$st" >> "$OUT/objects.tsv"
done < "$LIST"
echo "objects: $(grep -c $'\tok$' "$OUT/objects.tsv" || true) ok, $(grep -c $'\tfail$' "$OUT/objects.tsv" || true) failed"
